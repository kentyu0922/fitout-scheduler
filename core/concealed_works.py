"""Partition-wall vs above-ceiling concealed inspection sequence."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.duration_derivation import concealed_gap_working_days, statutory_days
from core.module_graph import add_pred
from core._common import parse_predecessor


def _find(tasks: List[Dict[str, Any]], needle: str) -> Optional[Dict[str, Any]]:
    return next((t for t in tasks if needle in str(t.get("name") or "")), None)


def _ensure(tasks, *, after, seq, **node):
    existing = _find(tasks, node["name"][:10]) if node.get("name") else None
    if existing is not None and existing.get("activity_uid") == node.get("activity_uid"):
        existing.update({k: v for k, v in node.items() if k != "id"})
        return existing, seq
    hit = next((t for t in tasks if t.get("activity_uid") == node.get("activity_uid")), None)
    if hit:
        hit.update({k: v for k, v in node.items() if k != "id"})
        return hit, seq
    node = dict(id=seq, predecessors="", **node)
    seq += 1
    tasks.insert(tasks.index(after), node)
    return node, seq


def apply_concealed_works(tasks: List[Dict[str, Any]], facts: Dict[str, Any]) -> Dict[str, Any]:
    wall = _find(tasks, "隔墙及主管管线隐蔽工程验收")
    ceiling = _find(tasks, "天花隐蔽工程验收")
    if not wall or not ceiling:
        return dict(applied=False, reason="inspection gates missing")
    sprinkler = next((t for t in tasks if t.get("activity_type") == "sprinkler"), None)
    hvac = next((t for t in tasks if t.get("activity_type") == "hvac_duct"), None)
    fcu = next((t for t in tasks if t.get("activity_type") == "fcu"), None)
    containment = next((t for t in tasks if t.get("activity_type") == "containment"), None)
    boarding = next((t for t in tasks if t.get("activity_type") == "gypsum_ceiling"), None)
    seq = max(t["id"] for t in tasks) + 1
    phase = wall.get("phase") or "实体施工"

    strength_days = statutory_days("sprinkler_strength", facts)["duration_days"]
    hydro_days = statutory_days("hydrostatic_test", facts)["duration_days"]
    leak_days = statutory_days("duct_leakage", facts)["duration_days"]
    elec_days = statutory_days("electrical_conduit", facts)["duration_days"]

    strength, seq = _ensure(
        tasks, after=ceiling, seq=seq,
        name="喷淋系统强度试验（1.4MPa 稳压30分钟）",
        outline_level=4, duration_days=strength_days, duration=strength_days, calendar="7D",
        activity_class="TESTING_COMMISSIONING", activity_uid="pmp.concealed.sprinkler_strength",
        phase=phase, duration_authority="timed_test_once", statutory_locked=True,
        duration_basis="GB 50261-2017第6.2.1条：试验压≥1.4MPa，稳压30min；不随面积延长。",
    )
    hydro, seq = _ensure(
        tasks, after=ceiling, seq=seq,
        name="喷淋系统24小时严密性试验（工作压保压）",
        outline_level=4, duration_days=hydro_days, duration=hydro_days,
        calendar="7D", work_weekend=True, ignore_holidays=True, elapsed_window=True,
        duration_unit="calendar_days", duration_authority="timed_test_once", statutory_locked=True,
        activity_class="TESTING_COMMISSIONING", activity_uid="pmp.concealed.sprinkler_hydrostatic",
        phase=phase, duration_basis="GB 50261-2017第6.2.2条：工作压稳压24h无渗漏；日历时钟，不随面积延长。",
    )
    leak, seq = _ensure(
        tasks, after=ceiling, seq=seq,
        name="风管严密性检测、冷凝保温及桥架抗震支吊架检查",
        outline_level=4, duration_days=leak_days, duration=leak_days, calendar="7D",
        activity_class="TESTING_COMMISSIONING", activity_uid="pmp.concealed.duct_leakage",
        phase=phase, duration_authority="timed_test_once", statutory_locked=True,
        duration_basis="GB 50243-2016第4.2.1条及附录C.1；时钟检测，不随面积延长。",
    )
    elec, seq = _ensure(
        tasks, after=ceiling, seq=seq,
        name="电气导管隐蔽前检查",
        outline_level=4, duration_days=elec_days, duration=elec_days, calendar="7D",
        activity_class="TESTING_COMMISSIONING", activity_uid="pmp.concealed.electrical_conduit",
        phase=phase, duration_authority="timed_test_once", statutory_locked=True,
        duration_basis="GB 50303-2015第3.3.11条：电气导管隐蔽前应检查合格；不随面积延长。",
    )

    # Tests run in parallel inside the wall→ceiling working-day window (not serial stack).
    if sprinkler:
        add_pred(strength, sprinkler)
        add_pred(hydro, sprinkler)
    else:
        add_pred(strength, wall)
        add_pred(hydro, wall)
    add_pred(hydro, strength)  # 强度试验完成后进入24h严密性
    for pred in (hvac, fcu, containment, wall):
        if pred:
            add_pred(leak, pred)
    add_pred(elec, wall)

    gap = concealed_gap_working_days(facts)
    lag = int(gap["lag_working_days"])
    parts = []
    seen = set()
    for pid, rel, existing_lag in parse_predecessor(str(ceiling.get("predecessors") or "")):
        if pid == wall["id"]:
            continue
        parts.append(str(pid) + rel + (f"{existing_lag:+d}" if existing_lag else ""))
        seen.add(pid)
    parts.append(f"{wall['id']}FS+{lag}")
    for extra in (hydro, leak, elec, strength):
        if extra and extra["id"] not in seen:
            parts.append(str(extra["id"]) + "FS")
    ceiling["predecessors"] = ",".join(dict.fromkeys(parts))
    ceiling["concealed_gap_working_days"] = lag
    ceiling["concealed_gap_band"] = {k: gap[k] for k in ("min_working_days", "max_working_days", "area_sqm") if k in gap}
    ceiling["calendar"] = facts.get("calendar_profile") or ceiling.get("calendar") or "7D"
    ceiling["input_notes"] = str(ceiling.get("input_notes") or "") + (
        f"\n天花隐蔽验收不得早于隔墙隐蔽验收后 {lag} 个项目工作日"
        f"（面积分档 {gap['min_working_days']}–{gap['max_working_days']} 工作日，调研#15），"
        "且须完成喷淋强度试验（GB 50261-2017第6.2.1条）、24小时严密性试验（第6.2.2条）、"
        "风管严密性检测（GB 50243-2016第4.2.1条）及电气导管隐蔽检查（GB 50303-2015第3.3.11条）。"
        "上述检测与间隔并行，不在隔墙验收后串行叠加。"
    )
    if boarding:
        add_pred(boarding, ceiling)
        boarding["input_notes"] = str(boarding.get("input_notes") or "") + "\n天花封板仅在天花隐蔽验收签认之后开始（GB 50210-2018第7.1.4条）。"
    wall["inspection_gate"] = True
    wall["input_notes"] = str(wall.get("input_notes") or "") + "\n隔墙封板前：龙骨、管线及水管试压、填充（GB 50210-2018第8.1.4条）。"
    ceiling["inspection_gate"] = True
    return dict(applied=True, wall_id=wall["id"], ceiling_id=ceiling["id"],
                hydrostatic_id=hydro["id"], strength_id=strength["id"],
                duct_id=leak["id"], electrical_id=elec["id"],
                lag_working_days=lag, band=gap)
