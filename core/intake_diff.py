"""Compare two project intakes and, when solved, their control dates."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

COMPARE_FIELDS = (
    "project_name", "city", "area_sqm", "sector", "delivery", "bidding",
    "start_date", "target_date", "cleanroom_class", "nc_rating", "esd_level",
    "power_kva", "mocap_camera_count", "mocap_stage_area_sqm", "specialist_area_sqm",
    "statutory_tender_required",
)


def unwrap_intake(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    proj = dict(raw.get("project") or {})
    if not proj:
        proj = {k: v for k, v in raw.items()
                if k not in {"project", "field_evidence", "schema_version", "status",
                             "blockers", "warnings", "module_plan", "authority_plan", "assumptions"}}
    else:
        for k, v in raw.items():
            if k not in {"project", "field_evidence", "schema_version", "status",
                         "blockers", "warnings", "module_plan", "authority_plan", "assumptions"}:
                proj.setdefault(k, v)
    from core.project_input import ALIASES
    out = {}
    for k, v in proj.items():
        out[ALIASES.get(k, k)] = v
    return out


def _norm(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _area(intake: dict) -> Any:
    return intake.get("area_sqm", intake.get("area"))


def _addons(intake: dict) -> Dict[str, str]:
    return {k: v for k, v in intake.items() if k.startswith("addon_") and v not in (None, "unknown")}


def compare_intake_snapshots(base_intake: dict, target_intake: dict,
                             base_milestones: Optional[dict] = None,
                             target_milestones: Optional[dict] = None) -> dict:
    """Structured delta for live constraint changes."""
    base_intake = unwrap_intake(base_intake)
    target_intake = unwrap_intake(target_intake)
    fields = []
    for key in COMPARE_FIELDS:
        a = _norm(_area(base_intake) if key == "area_sqm" else base_intake.get(key))
        b = _norm(_area(target_intake) if key == "area_sqm" else target_intake.get(key))
        if a != b:
            fields.append({"field": key, "base": a, "target": b})
    base_add, tgt_add = _addons(base_intake), _addons(target_intake)
    added, removed, changed = [], [], []
    for k in sorted(set(base_add) | set(tgt_add)):
        if k not in base_add:
            added.append({k: tgt_add[k]})
        elif k not in tgt_add:
            removed.append({k: base_add[k]})
        elif base_add[k] != tgt_add[k]:
            changed.append({k: {"base": base_add[k], "target": tgt_add[k]}})
    procurement = None
    if base_intake.get("bidding") != target_intake.get("bidding"):
        procurement = {"base": base_intake.get("bidding"), "target": target_intake.get("bidding")}
    delivery = None
    if base_intake.get("delivery") != target_intake.get("delivery"):
        delivery = {"base": base_intake.get("delivery"), "target": target_intake.get("delivery")}
    milestones = []
    if base_milestones and target_milestones:
        for key, label in (("permit", "施工许可证"), ("wall_concealed", "隔墙隐蔽验收"),
                           ("ceiling_concealed", "天花隐蔽验收"), ("completion", "竣工/搬迁完成"),
                           ("permit_lead_calendar_days", "开工至许可日历天"),
                           ("concealed_gap_calendar_days", "墙/天花隐蔽间隔日历天")):
            a, b = base_milestones.get(key), target_milestones.get(key)
            shift = None
            if isinstance(a, str) and isinstance(b, str):
                try:
                    shift = (date.fromisoformat(b) - date.fromisoformat(a)).days
                except ValueError:
                    shift = None
            elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
                shift = int(b) - int(a)
            milestones.append({"milestone": label, "base": a, "target": b, "shift_days": shift})
    deficit = []
    for snap, tag in ((target_milestones, "target"), (base_milestones, "base")):
        if snap and snap.get("schedule_deficit"):
            deficit.append({tag: snap["schedule_deficit"]})
    return dict(
        added_packages=added, removed_packages=removed, package_state_changes=changed,
        procurement_switch=procurement, delivery_switch=delivery,
        field_changes=fields, milestones=milestones, deficit_alerts=deficit,
        critical_path_note="Dates are from independently solved CPM networks; statutory windows are not compressed.",
    )


def format_ascii_table(delta: dict) -> str:
    lines = [
        "==============================================================",
        "[INTAKE DIFF] PMP Scheduler Engine v4.0.0",
        "--------------------------------------------------------------",
    ]
    if delta.get("delivery_switch"):
        d = delta["delivery_switch"]
        lines.append(f"  Delivery:     {d['base']}  →  {d['target']}")
    if delta.get("procurement_switch"):
        d = delta["procurement_switch"]
        lines.append(f"  Bidding:      {d['base']}  →  {d['target']}")
    for row in delta.get("field_changes") or []:
        if row["field"] in {"delivery", "bidding"}:
            continue
        lines.append(f"  {row['field']:<18} {row['base']}  →  {row['target']}")
    if delta.get("added_packages"):
        lines.append("  Added:        " + ", ".join(str(x) for x in delta["added_packages"]))
    if delta.get("removed_packages"):
        lines.append("  Removed:      " + ", ".join(str(x) for x in delta["removed_packages"]))
    if delta.get("milestones"):
        lines.append("  ---- control dates ----")
        lines.append(f"  {'Milestone':<22} {'Base':<14} {'Target':<14} Shift")
        for m in delta["milestones"]:
            shift = m.get("shift_days")
            shift_s = f"{shift:+d} d" if shift is not None else "—"
            lines.append(f"  {m['milestone']:<22} {str(m.get('base') or '—'):<14} {str(m.get('target') or '—'):<14} {shift_s}")
    if delta.get("deficit_alerts"):
        lines.append("  [SCHEDULE DEFICIT / 负时差警告] " + str(delta["deficit_alerts"]))
    lines.append("==============================================================")
    return "\n".join(lines)


def markdown_card(delta: dict) -> str:
    rows = ["| 项 | 基准 | 对比 | 差值 |", "|---|---|---|---|"]
    if delta.get("delivery_switch"):
        d = delta["delivery_switch"]
        rows.append(f"| 发包模式 | {d['base']} | {d['target']} | |")
    if delta.get("procurement_switch"):
        d = delta["procurement_switch"]
        rows.append(f"| 招标方式 | {d['base']} | {d['target']} | |")
    for m in delta.get("milestones") or []:
        shift = m.get("shift_days")
        rows.append(f"| {m['milestone']} | {m.get('base') or '—'} | {m.get('target') or '—'} | {'' if shift is None else str(shift) + ' 天'} |")
    return "\n".join(rows)
