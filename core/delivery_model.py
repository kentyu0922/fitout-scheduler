"""DBB serial vs D&B fast-track topology. Statutory windows are not shortened."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.module_graph import add_pred
from core._common import parse_predecessor


def _find(tasks: List[Dict[str, Any]], *needles: str) -> Optional[Dict[str, Any]]:
    for needle in needles:
        hit = next((t for t in tasks if needle in str(t.get("name") or "")), None)
        if hit:
            return hit
    return None


def _by_role(tasks: List[Dict[str, Any]], role: str) -> Optional[Dict[str, Any]]:
    return next((t for t in tasks if t.get("permit_role") == role), None)


def _replace_pred(task: Dict[str, Any], old_id: int, new_id: int, rel: str = "FS", lag: int = 0) -> None:
    parts = []
    found = False
    for pid, r, l in parse_predecessor(str(task.get("predecessors") or "")):
        if pid == old_id:
            parts.append(str(new_id) + rel + (f"{lag:+d}" if lag else ""))
            found = True
        else:
            parts.append(str(pid) + r + (f"{l:+d}" if l else ""))
    if not found:
        parts.append(str(new_id) + rel + (f"{lag:+d}" if lag else ""))
    task["predecessors"] = ",".join(dict.fromkeys(parts))


def _drop_preds_matching(task: Dict[str, Any], tasks: List[Dict[str, Any]], *needles: str) -> None:
    byid = {t["id"]: t for t in tasks}
    parts = []
    for pid, r, l in parse_predecessor(str(task.get("predecessors") or "")):
        name = str((byid.get(pid) or {}).get("name") or "")
        if any(n in name for n in needles):
            continue
        parts.append(str(pid) + r + (f"{l:+d}" if l else ""))
    task["predecessors"] = ",".join(dict.fromkeys(parts))


def apply_delivery_model(tasks: List[Dict[str, Any]], facts: Dict[str, Any]) -> Dict[str, Any]:
    delivery = str(facts.get("delivery") or "DBB")
    permit = _by_role(tasks, "result") or _find(tasks, "正式取得施工许可证", "施工许可取得")
    permit_work = _by_role(tasks, "submit") or _find(
        tasks, "施工许可行政审批", "施工许可证申报", "施工许可申报")
    permit_prep = _by_role(tasks, "prepare") or _find(tasks, "施工许可资料")
    sd = _find(tasks, "初步设计SD", "初步设计 SD")
    concept = _find(tasks, "Test Fit", "概念方案深化")
    plan = _find(tasks, "平面图确认")
    brief = _find(tasks, "行业需求", "(Brief)", "任务书")
    contract = _find(tasks, "D&B 总承包合同签订完成", "施工总包合同签订完成", "总承包合同签订完成")
    if contract is None:
        contract = next((t for t in tasks if t.get("procurement_step") == "contract"
                         and t.get("module_id") in {"procurement_gc", "procurement_db"}), None)
    takeover = _find(tasks, "场地移交与动工准备", "场地移交与进场准备")
    setting = next((t for t in tasks if t.get("activity_type") == "setting_out"), None)
    demolition = next((t for t in tasks if t.get("activity_type") == "demolition"), None)
    changes = []

    from core.milestones import drawing_review_task
    review = drawing_review_task(tasks)
    from core.milestones import drawing_rework_task
    rework = drawing_rework_task(tasks)
    cert = next((t for t in tasks if (
        "施工图审查合格" in str(t.get("name") or "") or "图审合格证" in str(t.get("name") or "")
    ) and "修改" not in str(t.get("name") or "")), None)
    drawings = cert
    design_contract = next((t for t in tasks if t.get("module_id") == "procurement_design"
                            and t.get("procurement_step") == "contract"), None)
    cd = next((t for t in tasks if "全专业施工图" in str(t.get("name") or "")
               and int(t.get("duration_days") or 0) > 0 and not t.get("summary")
               and "审查" not in str(t.get("name") or "")), None)
    waste = next((t for t in tasks if t.get("activity_type") == "waste_removal"), None)
    if waste is None:
        waste = _find(tasks, "建筑垃圾清运", "垃圾清运")

    if delivery == "DBB":
        if concept and brief:
            _drop_preds_matching(concept, tasks, "合同", "定标", "中标")
            add_pred(concept, brief)
            changes.append("DBB Test Fit starts from employer brief (design hire in parallel)")
        if cd and design_contract:
            add_pred(cd, design_contract, "FS", 2)
            changes.append("DBB construction drawings wait for appointed designer")
        if cert and rework:
            add_pred(cert, rework)
            changes.append("Drawing certificate follows revision and one re-review")
        elif cert and review:
            add_pred(cert, review)
        if cd:
            _drop_preds_matching(cd, tasks, "效果图", "材料选型", "公共与专项")
        ldi = _find(tasks, "本地化翻图", "LDI")
        if ldi and cd:
            _drop_preds_matching(ldi, tasks, "接口审查", "行业")
            _drop_preds_matching(ldi, tasks, "全专业施工图")
            add_pred(ldi, cd, "FF")
            changes.append("LDI localization finishes with construction drawings (FF)")
        if review and cd:
            _drop_preds_matching(review, tasks, "翻图", "LDI")
            add_pred(review, cd)
            changes.append("Statutory drawing review starts from IFC, parallel with LDI stamping")
        if takeover and permit:
            add_pred(takeover, permit)
            takeover["delivery_gate"] = "permit_before_site"
            changes.append("DBB site takeover remains behind 施工许可证")
        for node in (permit_prep, permit_work):
            if node and contract:
                add_pred(node, contract)
            if node and drawings:
                add_pred(node, drawings)
        if permit_prep or permit_work:
            changes.append("DBB permit waits for GC contract and drawing review")
    else:
        if takeover and permit:
            add_pred(takeover, permit)
            takeover["delivery_gate"] = "permit_before_site"
            takeover["input_notes"] = str(takeover.get("input_notes") or "") + (
                "\n所有模式：场地移交、拆除与清运均在取得施工许可证之后（施工图完成→图审→合格书→许可→现场）。"
            )
            changes.append("D&B site takeover remains behind 施工许可证")
        if setting and permit:
            add_pred(setting, permit)
            setting["delivery_gate"] = "permit_before_setting_out"
            changes.append("D&B setting-out still behind 施工许可证")
        if demolition and permit:
            add_pred(demolition, permit)
            demolition["fasttrack_overlap"] = None
            changes.append("D&B demolition waits for construction permit")
        if concept and brief:
            if contract:
                _drop_preds_matching(concept, tasks, "合同", "定标", "中标")
            _replace_pred(concept, brief["id"], brief["id"])
            concept["delivery_gate"] = "employer_concept_during_db_tender"
            changes.append("D&B Test Fit runs from employer brief, parallel with tender")
        issue = next((t for t in tasks if t.get("procurement_step") == "issue"
                      and t.get("module_id") in {"procurement_gc", "procurement_db"}), None)
        award = next((t for t in tasks if t.get("procurement_step") == "award"
                      and t.get("module_id") in {"procurement_gc", "procurement_db"}), None)
        if issue and concept:
            import math
            area = float(facts.get("area_sqm") or 3000)
            release = dict(id=max(t["id"] for t in tasks)+1,
                name="业主Test-fit招标版编制与发布", activity_uid="pmp.db.employer_testfit",
                outline_level=3, duration_days=max(3,min(5,math.ceil(3+2*(area-800)/9200))),
                calendar="5D", activity_class="DESIGN", milestone=False,
                predecessors=concept.get("predecessors", ""), specialist_phase="设计与审批",
                duration_basis="规划假设：需求齐备后3–5工作日出招标版功能布局；方案深化与签字继续并行。不完整（依据待补）")
            release["duration"]=release["duration_days"]
            tasks.append(release)
            _drop_preds_matching(issue, tasks, "平面图确认", "Test Fit", "概念方案深化")
            add_pred(issue, release)
            issue["soft_dependencies"] = [plan["id"]] if plan else []
        if sd and award:
            import math
            area = float(facts.get("area_sqm") or 3000)
            days = max(3, min(5, math.ceil(3 + 2 * (area - 800) / 9200)))
            sd.update(name="初步设计确认/深化（投标技术方案基础）", duration_days=days,
                      duration=days, predecessors=f'{award["id"]}FS', calendar="5D",
                      activity_class="DESIGN", duration_unit="working_days", ignore_holidays=False,
                      duration_basis="规划假设：投标方案成熟；确认深化3–5工作日，不完整（依据待补）")
            sd.pop("elapsed_window", None)
        if cd and award:
            # Award authorises design mobilisation; confirmation and contract run in parallel.
            cd["predecessors"] = f'{award["id"]}SS'
            cd["duration_basis"] += "；中标后开展，合同与设计确认并行，送审前须完成。规划假设。"
        if review and cd:
            _drop_preds_matching(review, tasks, "初步设计", "翻图", "LDI")
            add_pred(review, cd)
            for gate in (contract, sd):
                if gate: add_pred(review, gate)
        changes.append("DB award releases drawings; concept releases RFP; contract and design confirmation gate review")
        if cert and rework:
            add_pred(cert, rework)
        elif cert and review:
            add_pred(cert, review)
        for node in (permit_prep, permit_work):
            if not node:
                continue
            _drop_preds_matching(node, tasks, "初步设计SD", "物业装修图纸")
            if cert:
                add_pred(node, cert)
            elif rework:
                add_pred(node, rework)
            elif review:
                add_pred(node, review)
            if contract:
                add_pred(node, contract)
            node["design_release_gate"] = "PERMIT_AFTER_DRAWING_CERTIFICATE"
            node["input_notes"] = str(node.get("input_notes") or "") + (
                "\nD&B 亦须施工图完成并取得图审合格书后申报施工许可；"
                "审图5工作日、发证2工作日（沪建规范〔2025〕12号）按工作日日历，不压缩。"
            )
        if permit_prep or permit_work:
            changes.append("D&B permit filing from drawing certificate + contract")

    # Hard FS for every delivery: 施工图完成 → 图审 → 修改复审 → 合格书 → 许可 → 现场（含拆除/清运/移交）
    if review and cd:
        add_pred(review, cd)
        changes.append("Statutory drawing review waits for construction drawings")
    if rework and review:
        add_pred(rework, review)
    if cert and rework:
        add_pred(cert, rework)
        changes.append("Certificate after one revision/re-review")
    elif cert and review:
        add_pred(cert, review)
    for node in (permit_prep, permit_work):
        if node and cert:
            add_pred(node, cert)
        elif node and rework:
            add_pred(node, rework)
    for site in (takeover, setting, demolition, waste):
        if site and permit:
            add_pred(site, permit)
            site["delivery_gate"] = "permit_before_site"
    if permit:
        changes.append("Site work including demolition waits for construction permit")

    for t in tasks:
        if t.get("procurement_step"):
            _drop_preds_matching(t, tasks, "效果图", "材料选型", "渲染")
        if "效果图" in str(t.get("name") or "") and int(t.get("duration_days") or 0) > 4:
            t["duration_days"] = t["duration"] = 4
            t["calendar"] = "7D"
            t["duration_basis"] = "〔惯〕效果图不同步闸施工许可；规划占位4日历日。"
    if tasks:
        tasks[0]["delivery_model"] = dict(delivery=delivery, changes=changes)
    return dict(delivery=delivery, changes=changes)
