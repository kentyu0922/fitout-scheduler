# -*- coding: utf-8 -*-
"""Traceable Hangzhou-style constraint statuses. Not wired into the solver.

Validation of night windows, partial demolition, simplified filing and
completion releases. Field presence is not confirmation. Passing validation
is not a committed programme.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

APPLICABILITY = {
    True: "REQUIRED",
    False: "NOT_APPLICABLE",
    "TRUE": "REQUIRED",
    "REQUIRED": "REQUIRED",
    "YES": "REQUIRED",
    "FALSE": "NOT_APPLICABLE",
    "NOT_APPLICABLE": "NOT_APPLICABLE",
    "NO": "NOT_APPLICABLE",
    "UNKNOWN": "UNKNOWN",
    None: "UNKNOWN",
}

BLOCKING_STATUSES = {
    "INVALID_INPUT",
    "NIGHT_OUTPUT_UNQUANTIFIED",
    "CLOCK_UNQUANTIFIED",
    "WEEKLY_CALENDAR_UNQUANTIFIED",
    "PARTIAL_DEMOLITION_UNQUANTIFIED",
    "REQUIRED_UNQUANTIFIED",
    "UNKNOWN_AND_LOAD_BEARING",
    "CONFLICTING_INPUT",
}

FULL_STRIP_EPS = 1e-9


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _confirmation(spec: Dict[str, Any]) -> str:
    raw = _text(spec.get("confirmation_status"))
    if not raw:
        return "DECLARED"
    key = raw.upper().replace(" ", "_")
    if key in {"VERIFIED", "REVIEWED", "AUDITED", "DECLARED", "SOURCE_DECLARED", "PM_ESTIMATE", "UNVERIFIED"}:
        return key
    return "DECLARED"


def _finite_number(name: str, value: Any, *, allow_none: bool = False) -> Tuple[Optional[float], Optional[str]]:
    if value is None:
        if allow_none:
            return None, None
        return None, f"{name} missing"
    if isinstance(value, bool):
        return None, f"{name} must not be boolean"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{name} is not a number"
    if not math.isfinite(number):
        return None, f"{name} must be finite"
    return number, None


def _parse_hhmm(value: Any) -> Tuple[Optional[int], Optional[str]]:
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, "clock must be HH:MM string"
    match = TIME_RE.fullmatch(value.strip())
    if not match:
        return None, f"invalid clock {value!r}; expected HH:MM"
    return int(match.group(1)) * 60 + int(match.group(2)), None


def _window_minutes(start_min: int, finish_min: int) -> int:
    if finish_min == start_min:
        return 24 * 60
    if finish_min > start_min:
        return finish_min - start_min
    return 24 * 60 - start_min + finish_min


def _has_verification_evidence(spec: Dict[str, Any], *, require_process: bool = False) -> bool:
    source = _text(spec.get("source"))
    evidence = _text(spec.get("evidence"))
    if not source:
        return False
    if not evidence or evidence.upper() == "NONE":
        return False
    if require_process:
        instrument = _text(spec.get("instrument"))
        authority = _text(spec.get("authority"))
        if not (instrument and authority):
            return False
    return True


def _checked_outcome(
    spec: Dict[str, Any],
    checked: List[str],
    *,
    require_process: bool = False,
) -> Tuple[str, str, str, str, List[str]]:
    """Echo declared confirmation; VERIFIED only with evidence this function can cite."""
    confirmation = _confirmation(spec)
    checked = list(checked)
    if confirmation == "VERIFIED" and _has_verification_evidence(spec, require_process=require_process):
        return (
            "VERIFIED",
            confirmation,
            "VERIFIED",
            f"Checked: {', '.join(checked)}. Independent verification evidence is present.",
            checked,
        )
    return (
        "VALID_UNVERIFIED",
        confirmation,
        "NOT_VERIFIED",
        (
            f"Checked: {', '.join(checked)}. "
            "This function did not independently verify facts. "
            f"Declared confirmation_status={confirmation} is echoed; "
            "REVIEWED/AUDITED/source presence are not VERIFIED."
        ),
        checked,
    )


def _gate(id_: str, status: str, reason: str, confirmation: str, **extra: Any) -> Dict[str, Any]:
    blocking = status in BLOCKING_STATUSES
    return dict(
        id=id_,
        status=status,
        blocking=blocking,
        input_invalid=status in {"INVALID_INPUT", "CONFLICTING_INPUT"},
        validation_ok=status not in {"INVALID_INPUT", "CONFLICTING_INPUT"},
        confirmation_status=confirmation,
        source=extra.pop("source", None),
        reason=reason,
        **extra,
    )


def evaluate_night_window(spec: Dict[str, Any]) -> Dict[str, Any]:
    confirmation = _confirmation(spec)
    source = spec.get("source")
    errors: List[str] = []
    nights, nights_err = _finite_number("nights_per_week", spec.get("nights_per_week"), allow_none=True)
    if nights_err:
        errors.append(nights_err)
    elif nights is not None:
        if not float(nights).is_integer() or nights < 1 or nights > 7:
            errors.append("nights_per_week must be an integer 1–7")
            nights = None
        else:
            nights = int(nights)

    lt8 = spec.get("each_night_shorter_than_8h")
    if lt8 is not None and not isinstance(lt8, bool):
        errors.append("each_night_shorter_than_8h must be boolean or omitted")
        lt8 = None
    daytime = spec.get("daytime_work_allowed")
    if daytime is not None and not isinstance(daytime, bool):
        errors.append("daytime_work_allowed must be boolean or omitted")
        daytime = None

    start = _text(spec.get("permitted_start"))
    finish = _text(spec.get("permitted_finish"))
    start_min, start_err = _parse_hhmm(start)
    finish_min, finish_err = _parse_hhmm(finish)
    if start_err:
        errors.append(start_err)
    if finish_err:
        errors.append(finish_err)

    hours, hours_err = _finite_number("effective_hours", spec.get("effective_hours"), allow_none=True)
    if hours_err:
        errors.append(hours_err)
    elif hours is not None and hours <= 0:
        errors.append("effective_hours must be > 0")
        hours = None

    window_hours = None
    overnight = False
    if start_min is not None and finish_min is not None and not start_err and not finish_err:
        window_min = _window_minutes(start_min, finish_min)
        window_hours = window_min / 60.0
        overnight = finish_min <= start_min
        if lt8 is True and window_hours >= 8:
            errors.append("declared window is not shorter than 8h")
        if hours is not None and hours - window_hours > 1e-9:
            errors.append("effective_hours exceed permitted window length")

    extra = dict(
        nights_per_week=nights,
        each_night_shorter_than_8h=lt8,
        daytime_work_allowed=daytime,
        permitted_start=start,
        permitted_finish=finish,
        effective_hours=hours,
        window_hours=window_hours,
        overnight_window=overnight,
        source=source,
        furniture_install_rate="separate_pm_estimate_not_this_gate",
        other_trades_night_output="unknown_not_copied_from_furniture",
        single_night_window_valid=False,
        weekly_nights_status="MISSING" if nights is None else "QUANTIFIED",
    )

    if errors:
        return _gate(
            "night_window", "INVALID_INPUT", "; ".join(errors), confirmation, **extra)

    clock_present = start_min is not None and finish_min is not None and hours is not None
    extra["single_night_window_valid"] = bool(clock_present)
    if not clock_present:
        reason = (
            "Weekly nights and <8h / no-daytime limits may be declared; "
            "permitted clock times and effective hours are not. "
            "Furniture 50 each/team-night is a separate PM estimate and is not "
            "this gate or a rate for demolition/MEP."
        )
        return _gate("night_window", "NIGHT_OUTPUT_UNQUANTIFIED", reason, confirmation, **extra)

    if nights is None:
        reason = (
            "Single-night window passes clock/hours checks "
            f"(window_hours={window_hours}, overnight={overnight}). "
            "nights_per_week is missing; weekly working days cannot be inferred "
            "from one night's duration. Date inputs are not ready."
        )
        return _gate("night_window", "WEEKLY_CALENDAR_UNQUANTIFIED", reason, confirmation, **extra)

    status, confirmation, verification_status, checked_reason, checked = _checked_outcome(
        spec,
        ["HH:MM clocks", "overnight wrap", "effective_hours <= window", "nights_per_week 1–7"],
    )
    extra["validation_checked"] = checked
    extra["verification_status"] = verification_status
    extra["date_inputs_ready"] = True
    return _gate("night_window", status, checked_reason, confirmation, **extra)


def evaluate_partial_demolition(spec: Dict[str, Any], *, area_sqm: Any) -> Dict[str, Any]:
    confirmation = _confirmation(spec)
    source = spec.get("source")
    mode = spec.get("mode")
    errors: List[str] = []
    construction, area_err = _finite_number("construction_area_sqm", area_sqm, allow_none=True)
    if area_err:
        errors.append(area_err)
    elif construction is not None and construction <= 0:
        errors.append("construction_area_sqm must be > 0")
        construction = None

    fraction, frac_err = _finite_number("demolition_fraction", spec.get("demolition_fraction"), allow_none=True)
    if frac_err:
        errors.append(frac_err)
    elif fraction is not None and not (0.0 <= fraction <= 1.0):
        errors.append("demolition_fraction must be in [0, 1]")

    demo_area, demo_err = _finite_number("demolition_area_sqm", spec.get("demolition_area_sqm"), allow_none=True)
    if demo_err:
        errors.append(demo_err)
    elif demo_area is not None and demo_area < 0:
        errors.append("demolition_area_sqm must be >= 0")
    elif construction is not None and demo_area is not None and demo_area - construction > 1e-9:
        errors.append("demolition_area_sqm exceeds construction scope")

    if fraction is not None and demo_area is not None and construction:
        expected = fraction * construction
        if abs(expected - demo_area) > max(0.5, 0.01 * construction):
            errors.append("demolition_fraction and demolition_area_sqm disagree")

    equivalent_fraction = fraction
    if equivalent_fraction is None and demo_area is not None and construction:
        equivalent_fraction = demo_area / construction

    if mode not in {None, "partial", "none", "full_strip"}:
        errors.append(f"unknown demolition mode {mode!r}")

    if mode == "none" and (
        (equivalent_fraction is not None and equivalent_fraction > 0)
        or (demo_area is not None and demo_area > 0)
    ):
        errors.append("mode=none conflicts with positive demolition quantity")
    if mode == "partial" and equivalent_fraction is not None and equivalent_fraction >= 1 - FULL_STRIP_EPS:
        errors.append("mode=partial conflicts with full-strip quantity (fraction=1 or area=construction scope)")
    if mode == "full_strip" and equivalent_fraction is not None and equivalent_fraction < 1 - FULL_STRIP_EPS:
        errors.append("mode=full_strip conflicts with fraction/area below construction scope")

    extra = dict(
        mode=mode,
        demolition_fraction=fraction,
        demolition_area_sqm=demo_area,
        equivalent_fraction=equivalent_fraction,
        construction_area_sqm=construction,
        full_strip_upper_bound_sqm=construction if mode == "partial" else None,
        full_strip_upper_bound_auto_selected=False,
        source=source,
    )
    if errors:
        range_error = any("conflict" not in e for e in errors)
        status = "INVALID_INPUT" if range_error else "CONFLICTING_INPUT"
        return _gate("partial_demolition", status, "; ".join(errors), confirmation, **extra)

    if mode == "none" and (fraction in (None, 0) and demo_area in (None, 0)):
        return _gate("partial_demolition", "NOT_APPLICABLE", "No demolition declared.", confirmation, **extra)

    if (mode == "partial" or mode is None) and fraction is None and demo_area is None:
        reason = (
            f"Construction scope {construction}㎡ is not demolition quantity. "
            "Partial demolition is declared; fraction/area still missing."
        )
        return _gate("partial_demolition", "PARTIAL_DEMOLITION_UNQUANTIFIED", reason, confirmation, **extra)

    status, confirmation, verification_status, checked_reason, checked = _checked_outcome(
        spec, ["mode vs equivalent fraction/area", "range 0–1 / area ≤ construction"])
    extra["validation_checked"] = checked
    extra["verification_status"] = verification_status
    return _gate("partial_demolition", status, checked_reason, confirmation, **extra)


def evaluate_simplified_filing(spec: Dict[str, Any]) -> Dict[str, Any]:
    confirmation = _confirmation(spec)
    source = spec.get("source")
    required = spec.get("required")
    if required is True:
        applicability = "REQUIRED"
    elif required is False:
        applicability = "NOT_APPLICABLE"
    elif required in (None, "UNKNOWN"):
        applicability = "UNKNOWN"
    else:
        return _gate(
            "simplified_construction_filing",
            "INVALID_INPUT",
            f"invalid required flag {required!r}",
            confirmation,
            required=required,
            source=source,
        )

    instrument = _text(spec.get("instrument"))
    authority = _text(spec.get("authority"))
    duration, dur_err = _finite_number("duration_days", spec.get("duration_days"), allow_none=True)
    duration_basis = _text(spec.get("duration_basis"))
    evidence = _text(spec.get("evidence")) or "NONE"

    process_identification = "MISSING"
    if instrument and authority:
        process_identification = "INSTRUMENT_AND_AUTHORITY"
    elif instrument or authority:
        process_identification = "NAME_ONLY"

    extra = dict(
        required=required,
        applicability=applicability,
        authority=authority,
        instrument=instrument,
        duration_days=duration,
        duration_basis=duration_basis,
        process_identification=process_identification,
        evidence_status=evidence,
        construction_permit_folded_separately=True,
        source=source,
    )

    if dur_err:
        extra["duration_days"] = spec.get("duration_days")
        return _gate(
            "simplified_construction_filing", "INVALID_INPUT", dur_err, confirmation, **extra)

    if duration is not None and duration < 0:
        return _gate(
            "simplified_construction_filing", "INVALID_INPUT",
            "duration_days must be >= 0", confirmation, **extra)

    if duration == 0 and not duration_basis:
        return _gate(
            "simplified_construction_filing", "INVALID_INPUT",
            "zero duration requires explicit duration_basis; missing must not coerce to 0d",
            confirmation, **extra)

    if applicability == "NOT_APPLICABLE":
        return _gate(
            "simplified_construction_filing", "NOT_APPLICABLE",
            "Explicitly not required.", confirmation, **extra)

    if applicability == "UNKNOWN":
        return _gate(
            "simplified_construction_filing", "UNKNOWN_AND_LOAD_BEARING",
            "Filing applicability is not decided.", confirmation, **extra)

    duration_quantified = duration is not None and (duration > 0 or bool(duration_basis))
    extra["duration_status"] = "QUANTIFIED" if duration_quantified else "UNQUANTIFIED"
    extra["process_status"] = process_identification

    if not duration_quantified:
        reason = (
            "Simplified filing is required. A filename or label alone does not "
            "quantify duration; receiving office, duration and evidence remain separate."
        )
        return _gate(
            "simplified_construction_filing", "REQUIRED_UNQUANTIFIED", reason, confirmation, **extra)

    status, confirmation, verification_status, checked_reason, checked = _checked_outcome(
        spec,
        ["required flag", "duration_days range", "zero-duration basis"],
        require_process=True,
    )
    extra["validation_checked"] = checked
    extra["verification_status"] = verification_status
    return _gate("simplified_construction_filing", status, checked_reason, confirmation, **extra)


def _path_applicability(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    if isinstance(raw, str):
        key: Any = raw.strip().upper().replace(" ", "_")
    else:
        key = raw
    if key in APPLICABILITY:
        return APPLICABILITY[key], None
    return None, f"invalid applicability {raw!r}; use UNKNOWN, REQUIRED, NOT_APPLICABLE"


def evaluate_completion_releases(spec: Dict[str, Any]) -> Dict[str, Any]:
    confirmation = _confirmation(spec)
    source = spec.get("source")
    paths = []
    blocking = False
    invalid = False
    for key in ("owner_landlord_pc", "fire_acceptance_or_filing", "joint_completion", "iaq"):
        item = dict(spec.get(key) or {})
        applicability, app_err = _path_applicability(item.get("applicability"))
        duration, dur_err = _finite_number("duration_days", item.get("duration_days"), allow_none=True)
        completed = item.get("completed")
        if completed is not None and not isinstance(completed, bool):
            dur_err = (dur_err + "; " if dur_err else "") + "completed must be boolean"
        evidence = _text(item.get("evidence")) or "NONE"
        duration_basis = _text(item.get("duration_basis"))

        if app_err:
            status = "INVALID_INPUT"
            reason = app_err
        elif dur_err:
            status = "INVALID_INPUT"
            reason = dur_err
        elif duration is not None and duration < 0:
            status = "INVALID_INPUT"
            reason = "duration_days must be >= 0"
        elif duration == 0 and not duration_basis:
            status = "INVALID_INPUT"
            reason = "zero duration requires explicit duration_basis"
        elif applicability == "UNKNOWN":
            status = "UNKNOWN_AND_LOAD_BEARING"
            reason = "Applicability pending review; not auto REQUIRED or NOT_APPLICABLE."
        elif applicability == "NOT_APPLICABLE":
            status = "NOT_APPLICABLE"
            reason = "Explicitly not applicable."
        elif applicability == "REQUIRED" and duration is None:
            status = "REQUIRED_UNQUANTIFIED"
            reason = "Required but duration not quantified; completed/duration/evidence are separate."
        elif applicability == "REQUIRED":
            status, path_conf, verification_status, checked_reason, checked = _checked_outcome(
                item if item.get("confirmation_status") or item.get("source") or item.get("evidence") else spec,
                ["applicability enum", "duration_days range"],
            )
            reason = checked_reason
            path_verification = verification_status
            path_checked = checked
        else:
            status = "INVALID_INPUT"
            reason = "unhandled applicability"

        row_blocking = status in BLOCKING_STATUSES
        row = dict(
            id=key,
            applicability=applicability if not app_err else item.get("applicability"),
            duration_days=duration if not dur_err else item.get("duration_days"),
            completed=completed,
            evidence_status=evidence,
            duration_basis=duration_basis,
            status=status,
            blocking=row_blocking,
            input_invalid=status == "INVALID_INPUT",
            confirmation_status=_confirmation(item) if item.get("confirmation_status") else confirmation,
            verification_status=path_verification if status in {"VALID_UNVERIFIED", "VERIFIED"} else "NOT_VERIFIED",
            validation_checked=path_checked if status in {"VALID_UNVERIFIED", "VERIFIED"} else None,
            reason=reason,
        )
        paths.append(row)
        blocking = blocking or row_blocking
        invalid = invalid or status == "INVALID_INPUT"

    overall_required = spec.get("completion_cannot_be_omitted")
    if invalid:
        package_status = "INVALID_INPUT"
    elif blocking:
        package_status = (
            "REQUIRED_UNQUANTIFIED"
            if overall_required and any(p["status"] == "REQUIRED_UNQUANTIFIED" for p in paths)
            else "UNKNOWN_AND_LOAD_BEARING"
        )
    else:
        load_bearing = [p for p in paths if p["status"] != "NOT_APPLICABLE"]
        if load_bearing and all(p["status"] == "VERIFIED" for p in load_bearing):
            package_status = "VERIFIED"
        else:
            package_status = "VALID_UNVERIFIED"

    extra = dict(
        completion_cannot_be_omitted=overall_required,
        paths=paths,
        source=source,
        statutory_applicability_adjudicated=False,
        verification_status="NOT_VERIFIED" if package_status != "VERIFIED" else "VERIFIED",
    )
    reason = (
        "Owner/landlord PC, fire, joint completion and IAQ are separate. "
        "UNKNOWN means pending review, not all-required or all-NA. "
        "Package status does not promote child VALID_UNVERIFIED paths to VERIFIED. "
        "This register does not decide statutory applicability."
    )
    return _gate("completion_releases", package_status, reason, confirmation, **extra)


def evaluate_project_constraints(project: Dict[str, Any]) -> Dict[str, Any]:
    gates_in = project.get("gates") or {}
    night = evaluate_night_window(gates_in.get("night_window") or {})
    demo = evaluate_partial_demolition(
        gates_in.get("partial_demolition") or {},
        area_sqm=project.get("area_sqm"),
    )
    filing = evaluate_simplified_filing(gates_in.get("simplified_filing") or {})
    completion = evaluate_completion_releases(gates_in.get("completion_releases") or {})
    rows = [night, demo, filing, completion]
    blocking = [row["id"] for row in rows if row.get("blocking")]
    invalid = [row["id"] for row in rows if row.get("input_invalid")]
    return dict(
        project_id=project.get("project_id"),
        committed_schedule_allowed=False,
        committed_schedule_decided_here=False,
        date_inputs_ready=not blocking,
        cpm_feasible=None,
        validation_ok=not invalid,
        blocking_gates=blocking,
        invalid_gates=invalid,
        seven_day_eight_hour_dayshift_is_not_this_project=True,
        applied=False,
        engine_patch=False,
        commercial_release=False,
        solver_run=False,
        product_approved=False,
        gates=rows,
        furniture_install_rate=dict(
            status="PM_ESTIMATE",
            rate_value=50,
            rate_unit="each_per_team_night",
            source="project_pm_statement_2026-09-16",
            not_copied_to=["demolition", "mep", "night_window_gate"],
        ),
        note=(
            "Gate validation is not a committed 520㎡ programme. "
            "This register never authorizes committed_schedule_allowed; "
            "date_inputs_ready, cpm_feasible and product_approved are independent. "
            "Do not re-solve dates from this register."
        ),
    )


HANGZHOU_520_INPUT = dict(
    project_id="HZ520",
    city="杭州",
    area_sqm=520,
    delivery="DB",
    bidding="invite",
    floors=1,
    target_date="2027-04-30",
    construction_scope="full_520_not_full_strip",
    gates=dict(
        night_window=dict(
            nights_per_week=7,
            each_night_shorter_than_8h=True,
            daytime_work_allowed=False,
            permitted_start=None,
            permitted_finish=None,
            effective_hours=None,
            confirmation_status="DECLARED",
            source="project_pm_statement_2026-09-16",
        ),
        partial_demolition=dict(
            mode="partial",
            demolition_fraction=None,
            demolition_area_sqm=None,
            confirmation_status="DECLARED",
            source="project_pm_statement_2026-09-16",
        ),
        simplified_filing=dict(
            required=True,
            authority=None,
            instrument=None,
            duration_days=None,
            confirmation_status="DECLARED",
            source="project_pm_statement_2026-09-16",
        ),
        completion_releases=dict(
            completion_cannot_be_omitted=True,
            confirmation_status="DECLARED",
            owner_landlord_pc=dict(applicability="UNKNOWN"),
            fire_acceptance_or_filing=dict(applicability="UNKNOWN"),
            joint_completion=dict(applicability="UNKNOWN"),
            iaq=dict(applicability="UNKNOWN"),
            source="project_pm_statement_2026-09-16",
        ),
    ),
)
