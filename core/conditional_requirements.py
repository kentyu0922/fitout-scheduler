"""FIX-02 / FIX-03 / FIX-04 — CONDITIONAL REQUIREMENTS & EXPOSURE (V6.4.2).

UNKNOWN IS NOT ZERO. UNKNOWN IS NOT YES. UNKNOWN IS NOT A DEFAULT NUMBER.

When an occupancy/IAQ requirement or an authority requirement is UNKNOWN, the
governed activities:

  * stay VISIBLE in the WBS (never deleted),
  * carry an UNRESOLVED duration and ``committed_to_baseline = False``,
  * contribute ``0`` to the COMMITTED PROGRAMME — which is an explicit
    "not committed" state, NOT a 0-day planning assumption,
  * and re-enter the picture through CONDITIONAL EXPOSURE.

Conditional exposure is computed by RE-SOLVING the CPM for every scenario
combination. Durations are never added up (35d + 15d = 50d is forbidden): the
chains overlap in the network, so only a re-solve gives a true finish envelope.
"""
from __future__ import annotations

import copy
import itertools
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.authority_decision import UNKNOWN, TRUE
from core.occupancy_readiness import occupancy_scenario_options

__all__ = [
    "build_conditional_registry",
    "apply_committed_baseline",
    "apply_scenario",
    "scenario_combinations",
    "evaluate_conditional_exposure",
    "conditional_exposure_block",
    "programme_certainty_block",
    "MAX_SCENARIOS",
]

MAX_SCENARIOS = 16

_IAQ_DRIVER = "OCCUPANCY_IAQ"
_AUTHORITY_DRIVER = "AUTHORITY_ACCEPTANCE_CHAIN"


def _tid(t: Dict[str, Any]) -> Any:
    return t.get("id")


def _template_duration(t: Dict[str, Any]) -> Any:
    """The duration the WBS template / profile intended, NOT the committed value.

    ``apply_occupancy_profile()`` zeroes the IAQ tasks *before* the registry is
    built (that zero is the explicit "not committed" state). Reading
    ``duration_days`` here would therefore record every IAQ template as 0d and
    make an UNKNOWN requirement indistinguishable from a 0-day plan — the exact
    failure this module exists to prevent. The pre-zeroing value is kept on the
    task as ``template_duration_days``; use it whenever it is present.
    """
    if t.get("template_duration_days") is not None:
        return t["template_duration_days"]
    return t.get("duration_days", t.get("duration", 0))


def _task_by_id(tasks: List[Dict[str, Any]], tid: Any) -> Optional[Dict[str, Any]]:
    for t in tasks:
        if t.get("id") == tid:
            return t
    return None


def build_conditional_registry(tasks: List[Dict[str, Any]],
                                occupancy_resolved: Dict[str, Any],
                                authority_mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Collect every UNKNOWN requirement that can move the finish date.

    Returns drivers (each with status / basis / source / confidence /
    committed_to_baseline / potential_schedule_impact / decision_required and a
    set of resolved scenario options) plus the flat conditional task list.
    """
    drivers: List[Dict[str, Any]] = []
    conditional_tasks: List[Dict[str, Any]] = []

    # ---- occupancy / IAQ driver -------------------------------------------
    iaq_unknown = (occupancy_resolved.get("status") == "UNKNOWN"
                   or occupancy_resolved.get("profile") == "UNKNOWN")
    iaq_options = occupancy_scenario_options(tasks)
    iaq_task_ids: List[Any] = []
    seen = set()
    for opt in iaq_options:
        for tid in opt["durations"]:
            if tid not in seen:
                seen.add(tid)
                iaq_task_ids.append(tid)
    iaq_template = {}
    for tid in iaq_task_ids:
        t = _task_by_id(tasks, tid)
        if t is not None:
            iaq_template[tid] = _template_duration(t)
    drivers.append({
        "driver_id": _IAQ_DRIVER,
        "label": "IAQ / Flush-out Requirement",
        "status": "UNKNOWN" if iaq_unknown else "CONFIRMED",
        "basis": occupancy_resolved.get("basis"),
        "source": "config/occupancy_readiness_profiles.json",
        "confidence": occupancy_resolved.get("confidence"),
        "baseline_commitment": "UNRESOLVED" if iaq_unknown else "COMMITTED",
        "committed_to_baseline": not iaq_unknown,
        "potential_schedule_impact": True,
        "decision_required": iaq_unknown,
        "task_ids": list(iaq_task_ids),
        "template_durations": iaq_template,
        "options": ([opt for opt in iaq_options] if iaq_unknown else [
            dict(option_id="CONFIRMED", label="confirmed input",
                 flushout_days=occupancy_resolved.get("flushout_duration"),
                 testing_days=None,
                 basis=occupancy_resolved.get("basis"),
                 confidence=occupancy_resolved.get("confidence"),
                 durations={})]),
        "confirmed_selection": None if iaq_unknown else "CONFIRMED",
    })
    if iaq_unknown:
        for tid in iaq_task_ids:
            t = _task_by_id(tasks, tid)
            if t is None:
                continue
            conditional_tasks.append({
                "task_id": tid, "name": str(t.get("name", "")),
                "driver_id": _IAQ_DRIVER,
                "template_duration_days": iaq_template.get(tid),
                "committed_duration_days": 0,
                "duration_status": "UNRESOLVED",
                "committed_to_baseline": False,
            })

    # ---- authority acceptance chain driver --------------------------------
    reqs = authority_mapping.get("requirements", {})
    unknown_ids = list(authority_mapping.get("conditional_task_ids", []))
    unknown_reqs = [k for k, v in reqs.items() if v["status"] == UNKNOWN]
    if unknown_ids:
        auth_template = {}
        for tid in unknown_ids:
            t = _task_by_id(tasks, tid)
            if t is not None:
                auth_template[tid] = _template_duration(t)
        drivers.append({
            "driver_id": _AUTHORITY_DRIVER,
            "label": "Fire / Completion Authority Acceptance Chain",
            "status": "UNKNOWN",
            "basis": ("Authority applicability not confirmed for: "
                      + ", ".join(sorted(unknown_reqs))),
            "source": "config/city_permit.json (no fire applicability rule present)",
            "confidence": "low",
            "baseline_commitment": "UNRESOLVED",
            "committed_to_baseline": False,
            "potential_schedule_impact": True,
            "decision_required": True,
            "task_ids": list(unknown_ids),
            "template_durations": auth_template,
            # SEM-02: the two options are SCHEDULE EXPOSURE BOUNDS, not two real
            # statutory execution paths. They must never be presented as
            # "all-none" vs "all-required" legal alternatives.
            "envelope_type": "BOUNDING_ENVELOPE_ONLY",
            "legal_path_confirmed": False,
            "options": [
                {"option_id": "NOT_REQUIRED", "label": "AUTHORITY_MIN_EXPOSURE",
                 "basis": "none of the unconfirmed authority obligations apply — "
                          "schedule exposure lower bound only",
                 "confidence": "scenario", "durations": {tid: 0 for tid in unknown_ids}},
                {"option_id": "REQUIRED", "label": "AUTHORITY_MAX_EXPOSURE",
                 "basis": "all unconfirmed authority obligations apply at their "
                          "statutory processing durations — schedule exposure "
                          "upper bound only",
                 "confidence": "scenario", "durations": dict(auth_template)},
            ],
            "confirmed_selection": None,
            "requirement_trace": [
                {"requirement_name": k,
                 "status": reqs[k]["status"],
                 "basis": reqs[k]["basis"],
                 "source": reqs[k]["source"],
                 "confidence": reqs[k]["confidence"],
                 "committed_to_baseline": reqs[k]["committed_to_baseline"],
                 "potential_schedule_impact": reqs[k]["potential_schedule_impact"],
                 "decision_required": reqs[k]["decision_required"],
                 "disposition": reqs[k]["disposition"],
                 "tasks": reqs[k]["tasks"]}
                for k in sorted(unknown_reqs)
            ],
            "envelope_note": (
                "AUTHORITY_MIN_EXPOSURE / AUTHORITY_MAX_EXPOSURE are schedule "
                "exposure bounds only (envelope_type=BOUNDING_ENVELOPE_ONLY, "
                "legal_path_confirmed=false) — they are NOT statutory execution "
                "paths. The governed activities form a serial chain, so the finish "
                "envelope over every independent TRUE/FALSE combination is bounded "
                "by [min exposure, max exposure]. Fire acceptance and filing are "
                "kept independent; no mutual exclusivity is hard-coded."),
        })
        for tid in unknown_ids:
            t = _task_by_id(tasks, tid)
            if t is None:
                continue
            conditional_tasks.append({
                "task_id": tid, "name": str(t.get("name", "")),
                "driver_id": _AUTHORITY_DRIVER,
                "template_duration_days": auth_template.get(tid),
                "committed_duration_days": 0,
                "duration_status": "UNRESOLVED",
                "committed_to_baseline": False,
            })

    return {
        "principle": ("UNKNOWN is not committed to the baseline and is not deleted: "
                      "it becomes a conditional placeholder whose exposure is "
                      "re-solved as a scenario, never added up."),
        "drivers": drivers,
        "conditional_tasks": conditional_tasks,
        "unresolved_driver_ids": [d["driver_id"] for d in drivers if d["status"] == "UNKNOWN"],
    }


def apply_committed_baseline(tasks: List[Dict[str, Any]],
                             registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Zero the conditional activities for the committed programme.

    The zero is an explicit NOT-COMMITTED state: every touched task keeps its
    template duration, gains ``duration_status='UNRESOLVED'`` and
    ``committed_to_baseline=False``, so no reader can mistake it for a 0-day plan.
    """
    for item in registry["conditional_tasks"]:
        t = _task_by_id(tasks, item["task_id"])
        if t is None:
            continue
        t["duration_days"] = 0
        t["duration"] = 0
        t["duration_status"] = "UNRESOLVED"
        t["committed_to_baseline"] = False
        t["unresolved_placeholder"] = True
        t["template_duration_days"] = item["template_duration_days"]
        t.setdefault("conditional_requirement", {})
        t["conditional_requirement"].update(dict(
            driver_id=item["driver_id"], status="UNKNOWN",
            baseline_commitment="UNRESOLVED", committed_to_baseline=False,
            potential_schedule_impact=True, decision_required=True,
            template_duration_days=item["template_duration_days"]))
    return tasks


def _option_size(o: Dict[str, Any]) -> int:
    return sum(int(v) for v in (o.get("durations") or {}).values())


def scenario_combinations(registry: Dict[str, Any]) -> Dict[str, Any]:
    """Cartesian product of driver options, with explicit truncation handling.

    P1-02: truncation is never silent. Returns:
        combinations : list of {driver_id: option_id} selections to evaluate
        total        : full number of combinations
        evaluated    : how many are actually evaluated
        truncated    : True when total > MAX_SCENARIOS
        truncation_reason : human-readable explanation
        all_min / all_max : the minimum-exposure and maximum-exposure corners

    The ALL-MIN and ALL-MAX corners (by total conditional duration) are ALWAYS
    evaluated regardless of the limit, so the worst-case envelope can never be
    lost to truncation. Because CPM finish is monotone non-decreasing in task
    duration, all-max is the latest finish and all-min the earliest.
    """
    drivers = [d for d in registry["drivers"] if d.get("options")]
    if not drivers:
        return {"driver_ids": [], "combinations": [], "total": 0, "evaluated": 0,
                "truncated": False, "truncation_reason": None, "all_min": {}, "all_max": {}}

    driver_ids = [d["driver_id"] for d in drivers]
    all_min = {d["driver_id"]: min(d["options"], key=_option_size)["option_id"]
               for d in drivers}
    all_max = {d["driver_id"]: max(d["options"], key=_option_size)["option_id"]
               for d in drivers}

    option_lists = [[o["option_id"] for o in d["options"]] for d in drivers]
    total = 1
    for ol in option_lists:
        total *= len(ol)

    def _as_selection(combo) -> Dict[str, str]:
        return {driver_ids[i]: opt for i, opt in enumerate(combo)}

    if total <= MAX_SCENARIOS:
        combos = [_as_selection(c) for c in itertools.product(*option_lists)]
        return {"driver_ids": driver_ids, "combinations": combos, "total": total,
                "evaluated": len(combos), "truncated": False, "truncation_reason": None,
                "all_min": all_min, "all_max": all_max}

    # Truncated: corners first, then a deterministic representative sample.
    combos = [dict(all_min), dict(all_max)]
    seen = {tuple(sorted(all_min.items())), tuple(sorted(all_max.items()))}
    for combo in itertools.product(*option_lists):
        if len(combos) >= MAX_SCENARIOS:
            break
        sel = _as_selection(combo)
        key = tuple(sorted(sel.items()))
        if key in seen:
            continue
        seen.add(key)
        combos.append(sel)
    return {
        "driver_ids": driver_ids, "combinations": combos, "total": total,
        "evaluated": len(combos), "truncated": True,
        "truncation_reason": (
            f"{total} scenario combinations exceed MAX_SCENARIOS={MAX_SCENARIOS}; "
            "evaluated the ALL-MIN and ALL-MAX corners plus a deterministic "
            "representative subset. Envelope corners are always evaluated, so the "
            "worst-case finish cannot be lost to truncation."),
        "all_min": all_min, "all_max": all_max,
    }


def apply_scenario(tasks: List[Dict[str, Any]],
                   registry: Dict[str, Any],
                   selection: Dict[str, str]) -> List[Dict[str, Any]]:
    """Deep-copy the task list and write the selected option's durations."""
    out = copy.deepcopy(tasks)
    by_driver = {d["driver_id"]: d for d in registry["drivers"]}
    for driver_id, option_id in selection.items():
        driver = by_driver.get(driver_id)
        if not driver:
            continue
        opt = next((o for o in driver["options"] if o["option_id"] == option_id), None)
        if not opt:
            continue
        for tid, days in (opt.get("durations") or {}).items():
            t = _task_by_id(out, tid)
            if t is None:
                continue
            t["duration_days"] = int(days)
            t["duration"] = int(days)
    return out


def evaluate_conditional_exposure(tasks: List[Dict[str, Any]],
                                   registry: Dict[str, Any],
                                   solve_fn: Callable[[List[Dict[str, Any]]], str],
                                   committed_finish: str,
                                   target_date: Optional[str] = None) -> Dict[str, Any]:
    """Re-solve the CPM for every scenario and derive the finish envelope.

    ``solve_fn`` must accept a task list and return the project finish date as
    an ISO string. Durations are never summed.
    """
    scenarios = []
    combo_meta = scenario_combinations(registry)
    for selection in combo_meta["combinations"]:
        variant = apply_scenario(tasks, registry, selection)
        finish = solve_fn(variant)
        label = " + ".join("%s=%s" % (k, v) for k, v in sorted(selection.items()))
        scenarios.append({
            "scenario_id": "S%d" % (len(scenarios) + 1),
            "selection": dict(selection),
            "label": label,
            "finish_date": finish,
            "meets_target": (finish <= target_date) if target_date else None,
        })

    finishes = [s["finish_date"] for s in scenarios]
    earliest = min([committed_finish] + finishes) if finishes else committed_finish
    latest = max([committed_finish] + finishes) if finishes else committed_finish

    driver_impacts = []
    for d in registry["drivers"]:
        if d["status"] != "UNKNOWN":
            driver_impacts.append(dict(driver_id=d["driver_id"], label=d["label"],
                                       status=d["status"], min_impact_days=0,
                                       max_impact_days=0, committed=True,
                                       basis=d["basis"], confidence=d["confidence"],
                                       options=[]))
            continue
        # Isolated impact: hold every OTHER driver at its smallest option and
        # move only this driver to its largest. Taking the max over all
        # scenarios would attribute another driver's duration to this one —
        # the exact "35 + 15 = 50" mistake this module exists to prevent.
        min_pick, max_pick = {}, {}
        for other in registry["drivers"]:
            if other["status"] != "UNKNOWN":
                continue
            opts = other["options"]
            def _size(o):
                return sum(int(v) for v in (o.get("durations") or {}).values())
            min_pick[other["driver_id"]] = min(opts, key=_size)["option_id"]
            max_pick[other["driver_id"]] = max(opts, key=_size)["option_id"]
        isolated = dict(min_pick)
        isolated[d["driver_id"]] = max_pick[d["driver_id"]]
        match = next((s for s in scenarios if s["selection"] == isolated), None)
        if match is None:
            finished = solve_fn(apply_scenario(tasks, registry, isolated))
        else:
            finished = match["finish_date"]
        driver_impacts.append(dict(
            driver_id=d["driver_id"], label=d["label"], status=d["status"],
            committed=False, basis=d["basis"], confidence=d["confidence"],
            min_impact_days=0,
            max_impact_days=_days_between(committed_finish, finished),
            isolated_selection=isolated,
            options=[{"option_id": o["option_id"], "label": o.get("label"),
                      "flushout_days": o.get("flushout_days"),
                      "testing_days": o.get("testing_days"),
                      "basis": o.get("basis"), "confidence": o.get("confidence")}
                     for o in d["options"]]))

    modelled = [s["finish_date"] for s in scenarios if s["finish_date"]]
    return {
        "committed_finish_date": committed_finish,
        "scenarios": scenarios,
        # P1-02: explicit truncation accounting, never silent.
        "scenario_count_total": combo_meta["total"],
        "scenario_count_evaluated": combo_meta["evaluated"],
        "scenarios_truncated": combo_meta["truncated"],
        "truncation_reason": combo_meta["truncation_reason"],
        "all_min_corner": combo_meta["all_min"],
        "all_max_corner": combo_meta["all_max"],
        "conditional_finish_range": {"earliest": earliest, "latest": latest},
        # The committed programme excludes every UNRESOLVED requirement, so it is
        # the optimistic corner by construction. The earliest *modelled* scenario
        # is reported alongside it so nobody reads the committed date as a forecast.
        "earliest_modelled_scenario_finish": min(modelled) if modelled else None,
        "committed_is_optimistic_vs_all_scenarios": bool(modelled) and committed_finish < min(modelled),
        "drivers": driver_impacts,
        "re_solved": True,
        "summation_used": False,
        "note": ("Exposure is derived by re-solving the CPM for each scenario; "
                 "conditional durations are never added together."),
    }


def _start_margin_working_days(reference: str, required_start: str) -> int:
    """Signed working-day margin from the planning reference to the required start.

    Positive when the required start is at/after the reference (slack available),
    negative when it is before (deficit). Uses the standard 5-day workweek with
    the configured holiday bitmap; informational only — the authoritative
    reachability judgement is the calendar-date comparison ``required >= ref``.
    """
    import datetime
    from core.holidays import build_holiday_bitmap, load_holiday_pairs, is_workday
    try:
        ref = datetime.date.fromisoformat(str(reference)[:10])
        req = datetime.date.fromisoformat(str(required_start)[:10])
    except Exception:
        return 0
    if req == ref:
        return 0
    sign = 1 if req > ref else -1
    bitmap = build_holiday_bitmap(load_holiday_pairs())
    cnt = 0
    cur = ref
    while cur != req:
        cur += datetime.timedelta(days=sign)
        if is_workday(cur, bitmap):
            cnt += sign
    return cnt


def evaluate_backward_conditional_exposure(tasks: List[Dict[str, Any]],
                                           registry: Dict[str, Any],
                                           backward_solve_fn: Callable[[List[Dict[str, Any]]], str],
                                           planning_reference_date: str,
                                           target_date: str) -> Dict[str, Any]:
    """P0-02 — backward-mode conditional target feasibility.

    Each scenario is cloned, its conditions applied, then solved BACKWARD
    independently from the SAME planning target date. Reachability is judged by
    comparing the scenario's required start date against the planning reference
    date. Forward-solving from the optimistic baseline latest start is forbidden
    here because it structurally biases any scenario with added duration to miss
    the target (a false-negative feasibility defect).

    Per-scenario output: scenario_finish_target / scenario_required_start_date /
    planning_reference_date / start_margin_working_days / meets_target.
    """
    combo_meta = scenario_combinations(registry)
    scenarios: List[Dict[str, Any]] = []
    for selection in combo_meta["combinations"]:
        variant = apply_scenario(tasks, registry, selection)
        required_start = backward_solve_fn(variant)
        label = " + ".join("%s=%s" % (k, v) for k, v in sorted(selection.items()))
        scenarios.append({
            "scenario_id": "S%d" % (len(scenarios) + 1),
            "selection": dict(selection),
            "label": label,
            "scenario_finish_target": target_date,
            "scenario_required_start_date": required_start,
            "planning_reference_date": planning_reference_date,
            "start_margin_working_days": _start_margin_working_days(
                planning_reference_date, required_start),
            "meets_target": (required_start >= planning_reference_date),
        })

    required_starts = [s["scenario_required_start_date"] for s in scenarios]
    return {
        "mode": "BACKWARD",
        "planning_reference_date": planning_reference_date,
        "target_date": target_date,
        "scenarios": scenarios,
        "scenario_count_total": combo_meta["total"],
        "scenario_count_evaluated": combo_meta["evaluated"],
        "scenarios_truncated": combo_meta["truncated"],
        "truncation_reason": combo_meta["truncation_reason"],
        "all_min_corner": combo_meta["all_min"],
        "all_max_corner": combo_meta["all_max"],
        "conditional_required_start_range": {
            "earliest": min(required_starts) if required_starts else None,
            "latest": max(required_starts) if required_starts else None,
        },
        "re_solved": True,
        "summation_used": False,
        "note": ("Backward reachability is judged by solving each scenario backward "
                 "from the planning target and comparing its required start date to "
                 "the planning reference date — never by forward-solving from the "
                 "optimistic baseline latest start."),
    }


def _days_between(a: str, b: str) -> int:
    import datetime
    try:
        da = datetime.date.fromisoformat(str(a)[:10])
        db = datetime.date.fromisoformat(str(b)[:10])
        return max(0, (db - da).days)
    except Exception:
        return 0


def conditional_exposure_block(exposure: Dict[str, Any]) -> Dict[str, Any]:
    """Serializable summary of a forward or backward conditional exposure."""
    block: Dict[str, Any] = {
        "re_solved": exposure.get("re_solved", True),
        "summation_used": exposure.get("summation_used", False),
        "scenarios": exposure.get("scenarios", []),
        "scenario_count_total": exposure.get("scenario_count_total"),
        "scenario_count_evaluated": exposure.get("scenario_count_evaluated"),
        "scenarios_truncated": exposure.get("scenarios_truncated", False),
        "truncation_reason": exposure.get("truncation_reason"),
        "all_min_corner": exposure.get("all_min_corner"),
        "all_max_corner": exposure.get("all_max_corner"),
        "note": exposure.get("note", ""),
    }
    if exposure.get("mode") == "BACKWARD":
        block["mode"] = "BACKWARD"
        block["planning_reference_date"] = exposure.get("planning_reference_date")
        block["target_date"] = exposure.get("target_date")
        block["conditional_required_start_range"] = exposure.get("conditional_required_start_range")
    else:
        block["committed_finish_date"] = exposure.get("committed_finish_date")
        block["conditional_finish_range"] = exposure.get("conditional_finish_range")
        block["drivers"] = exposure.get("drivers", [])
    return block


def programme_certainty_block(committed_finish: str,
                               target_date: Optional[str],
                               target_status: Optional[str],
                               registry: Dict[str, Any],
                               exposure: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """FIX-05 / SEM-01 — the block an executive must read.

    SEM-01: ``known_scope_base_finish`` is the canonical name for the finish date
    of the currently committed / known scope (excluding unresolved conditional
    requirements). It is NOT a forecast; ``committed_forecast_finish`` is kept as
    a DEPRECATED alias only.
    """
    unresolved = [{"driver_id": d["driver_id"], "label": d["label"], "status": d["status"],
                   "baseline_commitment": d.get("baseline_commitment"),
                   "basis": d["basis"], "confidence": d["confidence"],
                   "committed_to_baseline": d["committed_to_baseline"],
                   "potential_schedule_impact": d.get("potential_schedule_impact"),
                   "decision_required": d["decision_required"]}
                  for d in registry["drivers"] if d["status"] == "UNKNOWN"]

    # SEM-01: the conditional forecast envelope. In FORWARD mode the scenarios
    # are re-solved forward and produce a genuine earliest/latest FINISH envelope.
    # In BACKWARD mode every scenario is solved backward from the same planning
    # target, so the finish is target-bounded and there is NO finish variance to
    # envelope — the conditional exposure is a REQUIRED START range instead.
    rng_finish = (exposure or {}).get("conditional_finish_range")
    rng_start = (exposure or {}).get("conditional_required_start_range")
    envelope = None
    envelope_note = None
    if rng_finish:
        envelope = {"earliest_finish": rng_finish.get("earliest"),
                    "latest_finish": rng_finish.get("latest")}
    elif rng_start:
        envelope = None
        envelope_note = (
            "Backward solve: the finish is target-bounded, so there is no finish "
            "envelope. The conditional exposure is a required-start range "
            "(see conditional_required_start_range), never a finish forecast.")

    block = {
        "known_scope_base_finish": committed_finish,
        "known_scope_base_finish_label": "确定范围基准完工日（未计未确认条件项）",
        "known_scope_base_finish_caveat": (
            "确定范围基准完工日仅覆盖当前已承诺/已知范围的完工日，排除未确认条件项。"
            "它不是完工日预测；必须与 conditional_forecast_envelope 一起解读。"),
        "conditional_forecast_envelope": envelope,
        "conditional_forecast_envelope_note": envelope_note,
        "target_date": target_date,
        "target_status": target_status,
        "unresolved_schedule_drivers": unresolved,
        "conditional_finish_range": rng_finish,
        "conditional_required_start_range": rng_start,
        "required_decisions": [
            "Confirm the IAQ / flush-out requirement (occupancy profile or explicit days + basis)"
            if d["driver_id"] == _IAQ_DRIVER else
            "Confirm the fire / completion authority applicability"
            for d in unresolved
        ],
        "conditional_task_count": len(registry.get("conditional_tasks", [])),
    }
    # SEM-01 back-compat: deprecated alias only.
    block["committed_forecast_finish"] = committed_finish
    block["committed_forecast_caveat"] = (
        "The committed forecast EXCLUDES every unresolved requirement. It is the "
        "optimistic corner by construction and is NOT a forecast of the finish "
        "date; read it together with the conditional finish range.")
    block["committed_forecast_is_deprecated_alias"] = True
    return block
