"""P0-01 / P0-02 — target-date semantics and backward feasibility.

Contract (REAL-CASE HARDENING):

1. The user's ``--target_date`` is preserved verbatim. Backward solving may only
   consume a *planning* target derived from an explicit management buffer
   (default 0). No hidden subtraction is allowed anywhere else.
2. After a backward solve, if the required start date is earlier than the
   planning reference date, the run is ``TARGET_INFEASIBLE``: the solved
   schedule is a DIAGNOSTIC RECOVERY BASELINE and must be marked
   NOT EXECUTABLE AS BASELINE. It must never be presented as an executable
   programme.

Status vocabulary
-----------------
TARGET_INFEASIBLE      required start < planning reference date. Diagnostic only.
CONDITIONALLY_FEASIBLE dates work, but unconfirmed inputs are load-bearing
                       (e.g. site condition UNKNOWN, cost UNKNOWN).
FEASIBLE               dates work and no blocking unconfirmed input is known.

This module performs pure date arithmetic and status classification only; it
never mutates task data or the user's target date.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "TARGET_INFEASIBLE",
    "CONDITIONALLY_FEASIBLE",
    "FEASIBLE",
    "DIAGNOSTIC_LABEL",
    "resolve_planning_target",
    "evaluate_target_feasibility",
    "format_target_summary",
    "format_infeasible_block",
]

TARGET_INFEASIBLE = "TARGET_INFEASIBLE"
CONDITIONALLY_FEASIBLE = "CONDITIONALLY_FEASIBLE"
FEASIBLE = "FEASIBLE"
TARGET_MET = "TARGET_MET"
TARGET_UNVERIFIABLE = "TARGET_UNVERIFIABLE"
TARGET_STATUS_DEPENDS_ON_IAQ_REQUIREMENT = "TARGET_STATUS_DEPENDS_ON_IAQ_REQUIREMENT"
TARGET_STATUS_DEPENDS_ON_AUTHORITY_CONFIRMATION = "TARGET_STATUS_DEPENDS_ON_AUTHORITY_CONFIRMATION"

# P1-04: three independent semantic dimensions, never collapsed into one status.
BASELINE_EXECUTABLE = "EXECUTABLE"
BASELINE_NOT_EXECUTABLE = "NOT_EXECUTABLE"

DIAGNOSTIC_LABEL = "DIAGNOSTIC RECOVERY BASELINE - NOT EXECUTABLE AS BASELINE"


def _to_date(value: Any) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    return _dt.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def resolve_planning_target(user_target_date: str,
                            management_buffer_days: int = 0) -> Dict[str, Any]:
    """Split the user target into user / management buffer / planning target.

    Pure arithmetic: ``planning = user - buffer``. With the default buffer of 0
    the user date is returned verbatim — no workday alignment, no hidden shift.
    Workday alignment of the move anchor remains the solver's own documented
    behaviour and is not a buffer.
    """
    if management_buffer_days is None or isinstance(management_buffer_days, bool):
        raise ValueError("management_buffer_days must be a non-negative integer")
    if int(management_buffer_days) != management_buffer_days or management_buffer_days < 0:
        raise ValueError("management_buffer_days must be a non-negative integer")
    user_dt = _to_date(user_target_date)
    planning_dt = user_dt - _dt.timedelta(days=int(management_buffer_days))
    return {
        "user_target_date": user_dt.isoformat(),
        "management_buffer_days": int(management_buffer_days),
        "planning_target_date": planning_dt.isoformat(),
        "rule": "planning_target = user_target - management_buffer (explicit, default 0; no hidden shift)",
    }


def evaluate_target_feasibility(required_start_date: Any,
                                 planning_reference_date: Any,
                                 unconfirmed_inputs: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Classify a backward solve against the planning reference date.

    ``start_gap_days`` follows the spec convention: planning reference minus
    required start (positive when the required start is in the past).
    ``recovery_required_days`` equals the positive gap on TARGET_INFEASIBLE and
    is 0 otherwise.
    """
    required = _to_date(required_start_date)
    reference = _to_date(planning_reference_date)
    gap = (reference - required).days
    inputs = [str(x) for x in (unconfirmed_inputs or [])]

    if required < reference:
        status = TARGET_INFEASIBLE
        recovery = gap
    elif inputs:
        status = CONDITIONALLY_FEASIBLE
        recovery = 0
    else:
        status = FEASIBLE
        recovery = 0

    return {
        "status": status,
        "required_start_date": required.isoformat(),
        "planning_reference_date": reference.isoformat(),
        "start_gap_days": gap,
        "recovery_required_days": recovery,
        "unconfirmed_inputs": inputs,
        "executable_as_baseline": status != TARGET_INFEASIBLE,
        "diagnostic_label": DIAGNOSTIC_LABEL if status == TARGET_INFEASIBLE else None,
    }


def classify_target_from_scenarios(scenarios: Sequence[Dict[str, Any]],
                                    target_date: Optional[str],
                                    unconfirmed_inputs: Optional[Sequence[str]] = None,
                                    committed_finish: Optional[str] = None,
                                    truncated: bool = False) -> Dict[str, Any]:
    """FIX-02 D / FIX-03 E — target status when UNKNOWNs can move the finish.

    A definite TARGET_MET or TARGET_INFEASIBLE is only emitted when every
    relevant scenario agrees. When the outcome flips between scenarios the
    status is CONDITIONALLY_FEASIBLE and ``depends_on`` names the drivers that
    flip it.

    P1-02: ``truncated=True`` means the scenario set is a representative subset
    (not a complete enumeration). Because the ALL-MIN / ALL-MAX corners are
    ALWAYS evaluated and the CPM finish is monotone non-decreasing in task
    duration, a definite verdict derived from the corners is envelope-proven.
    The returned ``enumeration_complete`` flag records the distinction.
    """
    inputs = [str(x) for x in (unconfirmed_inputs or [])]
    if not scenarios or not target_date:
        return {"status": None, "depends_on": [], "scenario_count": len(scenarios or []),
                "unconfirmed_inputs": inputs, "enumeration_complete": not truncated}

    scored = [s for s in scenarios if s.get("meets_target") is not None]
    if not scored:
        return {"status": None, "depends_on": [], "scenario_count": len(scenarios),
                "unconfirmed_inputs": inputs, "enumeration_complete": not truncated}

    met = [s for s in scored if s["meets_target"]]
    if len(met) == len(scored):
        status = TARGET_MET
    elif not met:
        status = TARGET_INFEASIBLE
    else:
        status = CONDITIONALLY_FEASIBLE

    # Which driver flips the verdict?
    depends: List[str] = []
    driver_ids = sorted({k for s in scored for k in s.get("selection", {})})
    for drv in driver_ids:
        seen_values = {}
        for s in scored:
            key = tuple(sorted((k, v) for k, v in s["selection"].items() if k != drv))
            seen_values.setdefault((key, s["selection"].get(drv)), s["meets_target"])
        groups: Dict[Any, set] = {}
        for (key, val), res in seen_values.items():
            groups.setdefault(key, set()).add(res)
        if any(len(v) > 1 for v in groups.values()):
            depends.append(drv)

    detail = None
    if status == CONDITIONALLY_FEASIBLE:
        if "OCCUPANCY_IAQ" in depends and "AUTHORITY_ACCEPTANCE_CHAIN" in depends:
            detail = "%s / %s" % (TARGET_STATUS_DEPENDS_ON_IAQ_REQUIREMENT,
                                  TARGET_STATUS_DEPENDS_ON_AUTHORITY_CONFIRMATION)
        elif "OCCUPANCY_IAQ" in depends:
            detail = TARGET_STATUS_DEPENDS_ON_IAQ_REQUIREMENT
        elif "AUTHORITY_ACCEPTANCE_CHAIN" in depends:
            detail = TARGET_STATUS_DEPENDS_ON_AUTHORITY_CONFIRMATION

    return {
        "status": status,
        "detail": detail,
        "depends_on": depends,
        "scenario_count": len(scored),
        "scenarios_meeting_target": len(met),
        "committed_finish": committed_finish,
        "unconfirmed_inputs": inputs,
        "enumeration_complete": not truncated,
        "enumeration_mode": "PARTIAL_ENUMERATION" if truncated else "COMPLETE_ENUMERATION",
        "envelope_proof": bool(truncated),
        "rule": ("a definite verdict requires agreement across all relevant "
                 "UNKNOWN states; otherwise the status is conditional"),
    }


def compose_target_dimensions(date_feasibility_status: Optional[str],
                               conditional_scenario_status: Optional[str],
                               executable_as_baseline: bool) -> Dict[str, Any]:
    """P1-04 — expose the three independent dimensions without collapsing them.

    * date_feasibility_status     : can the required start date be achieved
                                    relative to the planning reference date?
    * conditional_scenario_status : how many unresolved-condition scenarios
                                    meet the target?
    * baseline_execution_status   : is the generated baseline executable from
                                    the planning date?

    ``status`` remains as a DEPRECATED summary field for back-compat; the three
    fields are the source of truth.
    """
    return {
        "date_feasibility_status": date_feasibility_status,
        "conditional_scenario_status": conditional_scenario_status,
        "baseline_execution_status": (BASELINE_EXECUTABLE if executable_as_baseline
                                      else BASELINE_NOT_EXECUTABLE),
    }


def format_target_summary(semantics: Dict[str, Any]) -> str:
    """One-line-per-field block for logs and reports (P0-01 disclosure)."""
    return (
        f"User Target Date:       {semantics['user_target_date']}\n"
        f"Management Buffer:      {semantics['management_buffer_days']} days\n"
        f"Planning Target Date:    {semantics['planning_target_date']}"
    )


def format_infeasible_block(feasibility: Dict[str, Any]) -> str:
    """Prominent multi-line warning for a TARGET_INFEASIBLE run (P0-02)."""
    return (
        "TARGET_INFEASIBLE: the required start date is earlier than the planning reference date.\n"
        f"  Required Start:            {feasibility['required_start_date']}\n"
        f"  Planning Reference Date:   {feasibility['planning_reference_date']}\n"
        f"  Start Gap:                 {feasibility['start_gap_days']} days\n"
        f"  Recovery Required:         {feasibility['recovery_required_days']} days\n"
        f"  Status:                    {feasibility['status']}\n"
        f"  This run is a {DIAGNOSTIC_LABEL}.\n"
        "  It shows what SHOULD have happened; it is not an executable programme."
    )
