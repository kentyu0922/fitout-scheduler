"""Equipment delivery as intake fields. Default is pending contract, never a invented week-count."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

LEAD_ITEMS = (
    # field, addon, label, default on critical path
    ("lead_ups_days", "A04", "UPS交货", True),
    ("lead_crac_days", "A04", "精密空调交货", True),
    ("lead_gas_suppression_days", "A04", "气体灭火钢瓶组交货", True),
    ("lead_ahu_days", "A13", "净化空调机组交货", True),
    ("lead_ffu_days", "A13", "FFU交货", True),
    ("lead_hepa_days", "A13", "高效过滤器交货", True),
    ("lead_fume_hood_days", "A12", "排风柜交货", True),
    ("lead_vault_door_days", "A11", "金库门交货", True),
    ("lead_upw_days", "A16", "纯化设备交货", True),
    ("lead_air_spring_days", "A17", "空气弹簧隔振台交货", True),
    ("lead_specialist_equipment_days", "A22", "专业设备交货", True),
    ("lead_mocap_camera_days", "A06", "动捕相机交货", False),
)

FIELDS = {row[0] for row in LEAD_ITEMS}
DEFAULT_LABEL = "待合同回填"


def lead_days(facts: Dict[str, Any], field: str) -> Dict[str, Any]:
    raw = facts.get(field)
    if raw in (None, "", DEFAULT_LABEL):
        return dict(days=None, status="pending_contract", label=DEFAULT_LABEL,
                    basis="交货周数不完整，待采购合同回填；不得把行业传闻周数当作合同交期。")
    return dict(days=int(raw), status="provided", label=f"{int(raw)}日历日",
                basis="用户/合同提供的交货日历日")


def install_anchor(addon: str, tasks: List[Dict[str, Any]]):
    mapping = {
        "A04": [("datacenter", 4), ("datacenter", 2)],
        "A11": [("vault_security", 1)],
        "A12": [("lab_hvac", 1)],
        "A13": [("cleanroom_iso", 1)],
        "A16": None,
        "A17": [("anti_vibration", 1)],
        "A22": None,
        "A06": [("mocap", 4)],
    }
    spec = mapping.get(addon)
    if spec:
        for pack, step in spec:
            hit = next((t for t in tasks if t.get("module_id") == pack and t.get("module_step") == step), None)
            if hit:
                return hit
    if addon == "A16":
        return next((t for t in tasks if t.get("lifecycle_step") == "works" and t.get("module_id") == "A16"), None)
    if addon == "A22":
        return next((t for t in tasks if t.get("lifecycle_step") == "placement" and t.get("module_id") == "A22"), None)
    return None


def apply_equipment_leads(tasks: List[Dict[str, Any]], facts: Dict[str, Any], selected) -> Dict[str, Any]:
    from core.module_graph import add_pred
    selected = set(selected or [])
    design = next((t for t in tasks if "施工图全套确认" in t.get("name", "") or "全专业施工图深化出图" in t.get("name", "")), None)
    contract = next((t for t in tasks if t.get("procurement_step") == "contract"
                     and t.get("module_id") in {"procurement_gc", "procurement_db"}), None)
    seq = max(t["id"] for t in tasks) + 1 if tasks else 1
    rows = []
    for field, addon, label, critical in LEAD_ITEMS:
        if addon not in selected:
            continue
        info = lead_days(facts, field)
        target = install_anchor(addon, tasks)
        existing = next((t for t in tasks if t.get("equipment_lead_field") == field), None)
        if existing is None and field == "lead_mocap_camera_days":
            existing = next((t for t in tasks if t.get("module_id") == "module_procurement"
                             and "动捕相机" in t.get("name", "")), None)
        days = info["days"]
        if days is None:
            # 不编造周数：0天占位 + 明确「交货期未填，不完整」。未填时不把到货当作已发生。
            duration, milestone = 0, True
        else:
            duration, milestone = int(days), False
        node = existing or dict(id=seq, outline_level=3, calendar="7D",
                                module_id="module_procurement", activity_uid="pmp.lead." + field)
        if existing is None:
            seq += 1
            tasks.append(node)
        node.update(
            name=(f"专项设备：{label}（交货期未填，不完整）" if days is None
                  else f"专项设备：{label}（{info['label']}）"),
            client_task_name=label,
            duration_days=duration, duration=duration, milestone=milestone,
            equipment_lead_field=field, lead_status=info["status"],
            duration_basis=("交货期未填，不完整；计划未计入供货等待，关键路径偏乐观，待合同回填。"
                            if days is None else info["basis"]),
            duration_authority="pending_contract" if days is None else "contract_lead",
            calendar="7D", elapsed_window=True, ignore_holidays=True,
            duration_unit="calendar_days", activity_class="LOGISTICS_DELIVERY",
            critical_equipment=critical,
            incomplete_lead=days is None,
            input_notes=("交货期未填，不完整。未把行业传闻周数当作合同交期。" if days is None else "〔合同〕用户提供交货日历日。")
            + (" 安装不得早于到货节点；未填时该节点为0天警告占位。" if critical else " 是否关键路径待合同确认。"),
        )
        award = next((t for t in tasks if t.get("procurement_step") == "award"
                      and t.get("module_id") in {"procurement_gc", "procurement_db"}), None)
        if facts.get("delivery") == "DB" and award:
            node["predecessors"] = ""
            preds = [award]
            node["input_notes"] += " 中标后技术规格已冻结并获下单授权的规划假设；变更风险由项目确认。"
        else:
            preds = [p for p in (design, contract) if p]
        for p in preds:
            add_pred(node, p)
        if field == "lead_mocap_camera_days":
            receipt = next((t for t in tasks if t.get("activity_uid", "").startswith("pmp.integration.receipt.")
                            and "动捕相机" in t.get("name", "")), None)
            if receipt:
                receipt["name"] = "动捕相机到货开箱与技术文件验收" + ("（交货期未填，不完整）" if days is None else "")
                add_pred(receipt, node)
        if target:
            add_pred(target, node)
        rows.append(dict(field=field, addon=addon, status=info["status"], days=days, critical=critical,
                         incomplete=days is None))
    warnings = [r for r in rows if r.get("incomplete")]
    if warnings and tasks:
        note = "交货期未填，不完整：" + "、".join(r["field"] for r in warnings)
        tasks[0]["equipment_lead_warnings"] = [dict(field=r["field"], message="交货期未填，不完整") for r in warnings]
        tasks[0]["input_notes"] = str(tasks[0].get("input_notes") or "") + "\n" + note
        log.warning(note)
    return dict(applied=len(rows), items=rows, warnings=warnings)
