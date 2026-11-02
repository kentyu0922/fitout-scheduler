"""Explainable pre-construction and specialist durations from existing config.

Typical-plate b0 lives in config/preconstruction_duration.json. Area scaling uses
the L2 occupancy law (plate_ref_m2, area_exponent). Quantity × productivity ÷ crews
is recorded as a diagnostic; statutory windows are never compressed.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
CFG_PATH = ROOT / "config" / "preconstruction_duration.json"


_CACHE: Dict[str, Any] = {}


def _load(path: Path) -> Dict[str, Any]:
    key = str(path)
    hit = _CACHE.get(key)
    if hit is None:
        hit = json.loads(path.read_text(encoding="utf-8"))
        _CACHE[key] = hit
    return hit


def load_policy() -> Dict[str, Any]:
    return _load(CFG_PATH)


def _grade_params(area_facts: Dict[str, Any]) -> Dict[str, Any]:
    qb = _load(ROOT / "config" / "quantity_benchmarks.json")
    grade = str(area_facts.get("grade") or qb.get("default_grade") or "A").upper()
    if grade not in qb.get("grades", {}):
        grade = qb.get("default_grade") or "A"
    layout = str(area_facts.get("layout") or area_facts.get("office_type") or qb.get("default_layout") or "standard")
    params = dict(qb["grades"][grade]["params"])
    mult = float((qb.get("layouts") or {}).get(layout, {}).get("partition_density_multiplier") or 1.0)
    params["partition_density_m_per_m2"] = float(params["partition_density_m_per_m2"]) * mult
    params["grade"] = grade
    params["layout"] = layout
    return params


def specialist_room_area(facts: Dict[str, Any], room: str = "mocap") -> float:
    rooms = _load(ROOT / "config" / "specialist_rooms.json")
    default = float((rooms.get("default_sqm") or {}).get(room) or 300)
    field = {"mocap": "acoustic_room_area_sqm", "datacenter": "server_room_area_sqm"}.get(room)
    for key in (field, "mocap_stage_area_sqm", "specialist_area_sqm"):
        if key and facts.get(key):
            return max(1.0, float(facts[key]))
    return default


DESIGN_ROLES = {"concept_design", "detailed_drawings", "sd_package", "employer_requirements"}


def statutory_tender_required(facts: Dict[str, Any]) -> bool:
    """依法必须招标（招标投标法第24条适用范围），不是邀请/公开标签本身。"""
    if facts.get("statutory_tender_required") is True:
        return True
    if facts.get("statutory_tender_required") is False:
        return False
    return facts.get("procurement_regime") == "formal_tender"


def concealed_gap_working_days(facts: Dict[str, Any], policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    policy = policy or load_policy()
    area = max(1.0, float(facts.get("area_sqm") or facts.get("area") or 3000))
    bands = list((policy.get("concealed_gap") or {}).get("bands") or [])
    chosen = None
    for band in bands:
        cap = band.get("max_area_sqm")
        if cap is None or area <= float(cap):
            chosen = band
            break
    if chosen is None:
        chosen = {"min_working_days": 12, "max_working_days": 18, "typical_working_days": 14}
    lag = int(chosen.get("typical_working_days") or chosen.get("min_working_days") or 12)
    lo, hi = int(chosen["min_working_days"]), int(chosen["max_working_days"])
    lag = max(lo, min(hi, lag))
    return dict(lag_working_days=lag, min_working_days=lo, max_working_days=hi,
                area_sqm=area, band=chosen, unit="working_days",
                basis=(policy.get("concealed_gap") or {}).get("basis"))


def camera_count(facts: Dict[str, Any]) -> int:
    if facts.get("mocap_camera_count"):
        return max(24, min(48, int(facts["mocap_camera_count"])))
    area = specialist_room_area(facts, "mocap")
    # 24 cameras at the default 300㎡ stage; 48 at 2× default. Clamp to the optical array band.
    scaled = 24.0 * math.sqrt(area / 300.0)
    return max(24, min(48, int(round(scaled))))


def _crews(area_sqm: float) -> int:
    cap = _load(ROOT / "config" / "trade_capacity.json")
    wf = cap.get("workfront") or {}
    zone = float(wf.get("recommended_zone_area_sqm") or 2000)
    lo = int(wf.get("min_workfronts") or 1)
    hi = int(wf.get("max_workfronts") or 8)
    n = int(math.ceil(max(1.0, float(area_sqm)) / zone))
    return max(lo, min(hi, n))


def _qrc_days(quantity: float, activity_type: str, crews: int, min_days: int, factor: float) -> Optional[Dict[str, Any]]:
    try:
        from core.productivity import load_productivity_rates, duration_parts
        rates = load_productivity_rates()
        entry = (rates.get("activity_types") or {}).get(activity_type)
        if not entry:
            return None
        table = entry.get("rates_per_worker_day") or {}
        rate = float(table.get("typical") or entry.get("rate_per_worker_day") or 0)
        if rate <= 0:
            return None
        base_crew = int(entry.get("default_crew_size") or 4)
        # Diminishing returns: extra workfronts grow as sqrt, matching L2 exponent < 1.
        crew = max(2, int(round(base_crew * math.sqrt(max(1, crews)))))
        _raw, rounded = duration_parts(quantity, rate, crew, factor)
        days = max(min_days, rounded)
        return dict(days=days, rate=rate, crew=crew, quantity=quantity,
                    factor=factor, formula="ceil(qty / (rate × crew) × factor)")
    except Exception:
        return None


def scale_role(role: str, facts: Dict[str, Any], policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    policy = policy or load_policy()
    spec = policy["roles"][role]
    from core.l2_master_duration import occupancy_days, load_l2_duration_policy
    l2 = load_l2_duration_policy()
    area = max(1.0, float(facts.get("area_sqm") or facts.get("area") or 3000))
    if spec.get("quantity") == "specialist_room":
        area = specialist_room_area(facts, spec.get("room") or "mocap")
    elif spec.get("quantity") == "camera_count":
        # Occupancy still uses room area; camera count is the Q/R/C quantity.
        area = specialist_room_area(facts, spec.get("room") or "mocap")
    sector = str(facts.get("sector") or "office")
    delivery = str(facts.get("delivery") or "DBB")
    factor = 1.0
    if spec.get("family") == "design" or role in DESIGN_ROLES:
        factor *= float((policy.get("delivery_design_factor") or {}).get(delivery) or 1.0)
        # 设计随面积适度变长，但不叠行业复杂度（否则 10000㎡ 许可前置越出调研#9 区间）。
    else:
        factor = float((policy.get("sector_complexity") or {}).get(sector) or 1.0)
    if facts.get("cleanroom_class") or str(facts.get("cleanroom_class") or "").upper() in {"ISO7", "ISO8", "CLASS1000", "1000"}:
        factor *= 1.06
    if facts.get("power_kva") and float(facts["power_kva"]) >= 800:
        factor *= 1.04
    entry = {
        "b0_days": float(spec["b0_days"]) * factor,
        "b_min": int(spec["b_min"]),
        "scale_mode": spec.get("scale_mode") or "plate",
        "role": "ONCE" if spec.get("scale_mode") == "once" else "SLICE",
        "package_id": role,
        "overlap_group": role,
    }
    if spec.get("family") == "design" or role in DESIGN_ROLES:
        # 仅设计可随面积适度变长（调研#8/#9）；指数低于施工 L2 0.4，避免许可前置随面积越出区间。
        entry["area_exponent"] = float(policy.get("design_area_exponent") or 0.32)
        if spec.get("b_max") is not None:
            entry["b_max"] = int(spec["b_max"])
    elif spec.get("b_max") is not None:
        entry["b_max"] = int(spec["b_max"])
    occ = occupancy_days(entry, area_sqm=area, policy=l2)
    days = int(occ["duration_days"])
    if role == "detailed_drawings":
        anchors = spec.get("area_duration_anchors", [])
        if anchors:
            import bisect
            i = bisect.bisect_left([a[0] for a in anchors], area)
            if i == 0:
                days = anchors[0][1]
            elif i == len(anchors):
                days = math.ceil(anchors[-1][1] * (area / anchors[-1][0]) ** 0.32)
            else:
                a, b = anchors[i-1], anchors[i]
                days = math.ceil(a[1] + (b[1]-a[1]) * (area-a[0]) / (b[0]-a[0]))
            days = math.ceil(days * factor)
            occ = dict(occ, duration_days=days, method="area_duration_anchors")
    grade = _grade_params(facts)
    qty = area
    if spec.get("quantity") == "partition_and_ceiling":
        qty = area * float(grade["net_ceiling_ratio"]) + area * float(grade["partition_density_m_per_m2"]) * float(grade["partition_height_m"])
    elif spec.get("quantity") == "partition_area":
        qty = area * float(grade["partition_density_m_per_m2"]) * float(grade["partition_height_m"])
    elif spec.get("quantity") == "camera_count":
        qty = float(camera_count(facts))
    crews = _crews(area)
    qrc = None
    prod_type = spec.get("productivity_activity")
    if prod_type:
        qrc = _qrc_days(qty, prod_type, crews, int(spec["b_min"]), factor)
        if qrc and spec.get("crew_diminishing"):
            # Blend occupancy (diminishing crews) with Q/R/C diagnostic; occupancy governs.
            days = max(int(spec["b_min"]), days)
    if statutory_tender_required(facts) and role in {"gc_prepare_invite", "db_rfp"}:
        floor = int(policy["statutory"]["formal_tender_prepare_days"]["days"])
        days = max(days, floor)
        locked = True
        lock_reason = "statutory_tender_window"  # 招标投标法第24条
    else:
        locked = False
        lock_reason = None
    return dict(
        role=role, duration_days=days, area_sqm=area, quantity=qty, crews=crews,
        factor=round(factor, 4), occupancy=occ, qrc=qrc,
        b0_days=spec["b0_days"], b_min=spec["b_min"],
        statutory_locked=locked, lock_reason=lock_reason,
        inputs=dict(sector=sector, delivery=delivery, bidding=facts.get("bidding"),
                    city=facts.get("city"), grade=grade.get("grade"),
                    cleanroom_class=facts.get("cleanroom_class"),
                    nc_rating=facts.get("nc_rating"), esd_level=facts.get("esd_level"),
                    power_kva=facts.get("power_kva"),
                    mocap_cameras=camera_count(facts) if spec.get("quantity") == "camera_count" else None),
        basis="L2 occupancy D=max(b_min, ceil(b0×(area/plate_ref)^exponent)) × sector/delivery factors; Q/R/C diagnostic from productivity_rates × quantity_benchmarks; statutory windows locked.",
    )


def _shanghai_city(facts: Dict[str, Any]) -> bool:
    return str(facts.get("city") or "").strip().lower().removesuffix("市") in {"上海", "shanghai"}


def statutory_days(name: str, facts: Dict[str, Any], city_permit: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    policy = load_policy()
    if name == "drawing_review":
        if facts.get("authority_review_days") is not None and facts.get("authority_days_basis"):
            days = int(facts["authority_review_days"])
            source = "user authority_review_days"
        elif _shanghai_city(facts):
            spec = policy["statutory"]["shanghai_drawing_review_workdays"]
            days = int(spec["days"])
            source = spec["basis"]
        else:
            days = int((city_permit or {}).get("review_days_min") or 10)
            source = "city_permit.review_days_min or 住建部13号令第12条中型及以下10工作日"
        return dict(duration_days=days, statutory_locked=True, lock_reason="drawing_review_window",
                    source=source, unit="working_days")
    if name == "permit_filing":
        if facts.get("authority_permit_days") is not None and facts.get("authority_days_basis"):
            days = int(facts["authority_permit_days"])
            source = "user authority_permit_days"
        elif _shanghai_city(facts):
            spec = policy["statutory"]["shanghai_permit_issue_workdays"]
            days = int(spec["days"])
            source = spec["basis"]
        else:
            days = int((city_permit or {}).get("permit_days_min") or 7)
            source = "city_permit.permit_days_min or 住建部18号令7日内发证"
        return dict(duration_days=days, statutory_locked=True, lock_reason="construction_permit_window",
                    source=source, unit="working_days")
    if name == "hydrostatic_test":
        spec = policy["statutory"]["hydrostatic_test_days"]
        return dict(duration_days=int(spec["days"]), statutory_locked=True, lock_reason="timed_pressure_test",
                    source=spec.get("basis") or "preconstruction_duration.statutory.hydrostatic_test_days", unit=spec["unit"])
    if name == "sprinkler_strength":
        spec = policy["statutory"]["sprinkler_strength_test_days"]
        return dict(duration_days=int(spec["days"]), statutory_locked=True, lock_reason="timed_pressure_test",
                    source=spec.get("basis"), unit=spec["unit"])
    if name == "formal_tender":
        spec = policy["statutory"]["formal_tender_prepare_days"]
        return dict(duration_days=int(spec["days"]), statutory_locked=True, lock_reason="statutory_tender_window",
                    source=spec.get("basis"), unit=spec["unit"])
    if name == "duct_leakage":
        spec = policy["statutory"]["duct_leakage_check_days"]
        return dict(duration_days=int(spec["days"]), statutory_locked=True, lock_reason="timed_inspection",
                    source=spec.get("basis"), unit=spec["unit"])
    if name == "electrical_conduit":
        spec = policy["statutory"]["electrical_conduit_check_days"]
        return dict(duration_days=int(spec["days"]), statutory_locked=True, lock_reason="timed_inspection",
                    source=spec.get("basis"), unit=spec["unit"])
    if name == "fire_design_review_special":
        spec = policy["statutory"]["fire_design_review_special_workdays"]
        return dict(duration_days=int(spec["days"]), statutory_locked=True, lock_reason="fire_design_review_window",
                    source=spec.get("basis"), unit=spec["unit"])
    raise KeyError(name)


def procurement_step_days(key: str, facts: Dict[str, Any], *, design: bool, public: bool, formal: bool) -> Dict[str, Any]:
    policy = load_policy()
    area = float(facts.get("area_sqm") or facts.get("area") or 3000)
    tender = (statutory_tender_required(facts) or formal) and not design
    if key == "prepare" and tender:
        return {**statutory_days("formal_tender", facts), "step": key}
    if key == "publicity":
        spec = policy["statutory"]["publicity_days"]
        days = int(spec["days"]) if tender else max(1, int(spec["days"]))
        return dict(step=key, duration_days=days, statutory_locked=bool(tender),
                    lock_reason="publicity_window" if tender else None)
    mapping = {
        "scope": "design_rfp_prepare" if design else "gc_prepare_invite",
        "boq": "boq_prep",
        "access": "design_rfp_prepare" if design else "gc_prepare_invite",
        "prepare": "db_rfp" if facts.get("delivery") == "DB" else "gc_prepare_invite",
        "clarify": None,
        "technical1": None,
        "commercial1": None,
        "interview1": None,
        "evaluation": None,
        "approval": None,
        "contract": None,
        "clarification_close": None,
    }
    role = mapping.get(key)
    if role:
        scaled = scale_role(role, facts, policy)
        days = scaled["duration_days"]
        if key in {"scope", "access"}:
            days = max(2, min(days, int(math.ceil(days * 0.25))))
        elif key == "boq" and design:
            days = max(2, min(days, int(math.ceil(days * 0.35))))
        elif key == "boq" and not design:
            # Employer-requirements / D&B comparable-quote pack, not a full DBB BOQ.
            frac = 0.40 if facts.get("delivery") == "DB" else 0.55
            days = max(3, min(days, int(math.ceil(days * frac))))
        elif key == "prepare":
            days = max(int(policy["roles"][role]["b_min"]), days)
        return dict(step=key, duration_days=days, statutory_locked=False, derivation=scaled)
    # 招采治理节拍不随面积变长（法定窗口除外）。
    plate = {"clarify": 2, "technical1": 2, "commercial1": 2, "interview1": 1,
             "evaluation": 2, "approval": 2, "contract": 3, "clarification_close": 1}.get(key, 2)
    return dict(step=key, duration_days=max(1, int(plate)), statutory_locked=False,
                derivation=dict(b0_days=plate, law="fixed_governance_beat"))


def apply_preconstruction_durations(tasks: List[Dict[str, Any]], facts: Dict[str, Any],
                                    city_permit: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    policy = load_policy()
    audit: List[Dict[str, Any]] = []
    city_permit = city_permit or {}

    def apply_task(task, scaled, extra=None):
        if task.get("statutory_locked") or task.get("elapsed_window"):
            if task.get("duration_authority") in {"timed_test_once", "statutory_window"}:
                return
        days = int(scaled["duration_days"])
        task["duration_days"] = days
        task["duration"] = days
        task["duration_derivation"] = scaled
        if extra:
            task["duration_derivation"].update(extra)
        audit.append(dict(id=task.get("id"), name=task.get("name"), **{k: scaled[k] for k in
                     ("role", "duration_days", "area_sqm", "quantity", "crews", "factor", "basis") if k in scaled}))

    for role, spec in policy["roles"].items():
        needles = spec.get("match_any") or []
        if not needles:
            continue
        scaled = scale_role(role, facts, policy)
        for task in tasks:
            if task.get("milestone") or int(task.get("outline_level") or 3) < 3:
                continue
            name = str(task.get("name") or "")
            if any(n in name for n in needles):
                apply_task(task, scaled, extra=dict(matched_role=role))
                if role in DESIGN_ROLES or spec.get("family") == "design":
                    # 设计天数随面积变；日历按〔惯〕赶图连续日出图，避免5天制把许可前置拉出调研区间。
                    task["activity_class"] = "DESIGN"
                    task["calendar"] = "7D"
                    task["ignore_holidays"] = True
                    task["elapsed_window"] = True
                    task["duration_unit"] = "calendar_days"
                    task["duration_basis"] = (str(task.get("duration_basis") or "")
                        + " 〔惯〕出图工期随面积连续放大（见 references/design_duration_practice.md）；按连续日历日排以便与法定工作日窗口区分。")

    review = statutory_days("drawing_review", facts, city_permit)
    permit = statutory_days("permit_filing", facts, city_permit)
    for task in tasks:
        name = str(task.get("name") or "")
        if any(k in name for k in ("第三方施工图审查", "第三方图审机构审查", "施工图送审与审查")) and int(task.get("duration_days") or 0) > 0:
            task["duration_days"] = task["duration"] = review["duration_days"]
            task["duration_authority"] = "statutory_window"
            task["statutory_locked"] = True
            task["activity_class"] = "AUTHORITY_APPROVAL"
            task["calendar"] = "5D"
            task["ignore_holidays"] = False
            task["duration_unit"] = "working_days"
            task.pop("elapsed_window", None)
            task["duration_derivation"] = review
            audit.append(dict(id=task.get("id"), name=name, role="drawing_review", **review))
        if (("施工许可证申报" in name or "施工许可行政审批" in name or "施工许可申报与办理" in name)
                and int(task.get("duration_days") or 0) > 0):
            task["duration_days"] = task["duration"] = permit["duration_days"]
            task["duration_authority"] = "statutory_window"
            task["statutory_locked"] = True
            task["activity_class"] = "AUTHORITY_APPROVAL"
            task["calendar"] = "5D"
            task["ignore_holidays"] = False
            task["duration_unit"] = "working_days"
            task.pop("elapsed_window", None)
            task["duration_derivation"] = permit
            audit.append(dict(id=task.get("id"), name=name, role="permit_filing", **permit))
        if task.get("procurement_step"):
            design = "设计服务" in name
            public = facts.get("bidding") == "public"
            formal = statutory_tender_required(facts) or facts.get("procurement_regime") == "formal_tender"
            step = procurement_step_days(task["procurement_step"], facts, design=design, public=public, formal=formal)
            if step.get("statutory_locked"):
                task["statutory_locked"] = True
                task["duration_authority"] = "statutory_window"
            if not (task.get("elapsed_window") and task["procurement_step"] in {"prepare", "publicity"} and (public or formal)):
                if task["procurement_step"] not in {"issue", "quote1", "quote2", "quote3", "award"}:
                    task["duration_days"] = task["duration"] = int(step["duration_days"])
            task["duration_derivation"] = step
            audit.append(dict(id=task.get("id"), name=name, role="procurement." + task["procurement_step"],
                              duration_days=task.get("duration_days"), **{k: step[k] for k in ("statutory_locked", "lock_reason") if k in step}))
    if tasks:
        tasks[0]["duration_derivation_audit"] = audit
    return dict(applied=len(audit), rows=audit, policy_version=policy.get("version"))
