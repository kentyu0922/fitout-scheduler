# -*- coding: utf-8 -*-
"""V6.5.0.1 — the ONE canonical scenario metrics provider.

Why this module exists
----------------------
The V6.5.0 Scenario Challenge package reported Scenario A physical construction as
BOTH **155** and **145** working days, from the same solved schedule:

* **155** — measured over the **Phase 6 WBS SUMMARY row** (2027-02-22 → 2027-10-16).
  Phase 6 is a summary task, so its span swallows ``Stage 4: 空气治理与家具进场
  (Post-Completion & IAQ)`` — furniture installation 09-25 → 10-16 and the SOP-4
  flush-out node on 10-16. Post-completion IAQ and furniture are **not** physical
  construction.
* **145** — measured over the engine's **own declared** site-construction span,
  anchored ``site_construction_start.start → physical_completion.finish``
  (2027-02-22 → 2027-09-24). This anchor pair is emitted by the engine itself in
  ``schedule.phase_semantics.site_construction``.

Scenario B1/B2 both terminate at ``[M] ⭐️ 物理竣工 / 硬装施工完成 (Physical
Completion)``, so comparing A-over-Phase-6 against B-over-physical-completion was
never like-for-like. The 155 figure was an apples-to-oranges measurement, and the
"41.3% saving" derived from it was therefore overstated.

This module is the **single source of truth**. Report generators must NOT
recompute these values; they read from here.

Canonical definition (frozen for V6.5.0.1)
------------------------------------------
``physical_construction`` = ``site_construction_start.start`` → ``physical_completion.finish``
where the two anchors are resolved from the schedule's own ``phase_semantics`` block
(Scenario A) or from the scenario's declared physical-construction window (B1/B2).

Every duration carries an explicit calendar basis:
``duration_value`` / ``duration_unit`` / ``calendar_id`` / ``calendar_basis``.
Never compare durations from different calendars silently.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional, Sequence

from core import holidays as HOL

DEFINITION_VERSION = "1.0.0"
MODULE = "core.scenario_metrics"

# --- calendar identities ---------------------------------------------------
CALENDAR_STANDARD_5D = "STANDARD_5D"        # Mon–Fri, statutory holidays off
CALENDAR_SITE_7D = "SITE_7D"                # 7 days/week, statutory holidays off
CALENDAR_ELAPSED = "ELAPSED_CALENDAR"       # raw calendar, no exclusions

UNIT_WORKING_DAYS = "WORKING_DAYS"
UNIT_CALENDAR_DAYS = "CALENDAR_DAYS"

BASIS_STANDARD_5D = "mon_fri_excluding_statutory_holidays"
BASIS_SITE_7D = "all_days_excluding_statutory_holidays"
BASIS_ELAPSED = "inclusive_of_both_endpoints"

# The canonical basis for the reported physical-construction working-day metric.
# STANDARD_5D is retained because it is the basis the frozen A/B1/B2 figures
# (145 / 91 / 83) were produced on; V6.5.0.1 declares it instead of changing it.
DEFAULT_WORKING_DAY_CALENDAR = CALENDAR_STANDARD_5D

CANONICAL_DEFINITION = (
    "physical_construction = site_construction_start.start -> physical_completion.finish; "
    "working days counted on STANDARD_5D (Mon-Fri, statutory holidays excluded); "
    "elapsed calendar days counted inclusive of both endpoints."
)


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------
def _d(value: Any) -> Optional[_dt.date]:
    if value is None:
        return None
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def elapsed_calendar_days(start: Any, finish: Any) -> Optional[int]:
    """Elapsed calendar days, INCLUSIVE of both endpoints.

    A milestone that starts and finishes on the same day is 1 calendar day —
    consistent with the B1/B2 figures (136 / 126) that must not move.
    """
    s, f = _d(start), _d(finish)
    if not s or not f or f < s:
        return None
    return (f - s).days + 1


def working_days(start: Any, finish: Any,
                 calendar_id: str = DEFAULT_WORKING_DAY_CALENDAR) -> Optional[int]:
    """Working days between two dates (inclusive), on an explicit calendar."""
    s, f = _d(start), _d(finish)
    if not s or not f or f < s:
        return None
    pairs = []
    for raw_start, raw_finish in HOL.load_holiday_pairs():
        hs, hf = _d(raw_start), _d(raw_finish)
        if hs and hf:
            pairs.append((hs, hf))
    if calendar_id == CALENDAR_STANDARD_5D:
        bitmap = HOL.build_holiday_bitmap(HOL.load_holiday_pairs())
        n, cur = 0, s
        while cur <= f:
            if HOL.is_workday(cur, bitmap):
                n += 1
            cur += _dt.timedelta(days=1)
        return n
    if calendar_id == CALENDAR_SITE_7D:
        n, cur = 0, s
        while cur <= f:
            if not any(a <= cur <= b for a, b in pairs):
                n += 1
            cur += _dt.timedelta(days=1)
        return n
    if calendar_id == CALENDAR_ELAPSED:
        return (f - s).days + 1
    raise ValueError(f"unknown calendar_id {calendar_id!r}")


def duration_metric(value: Optional[int], unit: str, calendar_id: str,
                    calendar_basis: str, **extra: Any) -> Dict[str, Any]:
    """A duration that always states its own calendar basis."""
    out = dict(duration_value=value, duration_unit=unit,
               calendar_id=calendar_id, calendar_basis=calendar_basis)
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# span resolution
# ---------------------------------------------------------------------------
def _find_task(tasks: Sequence[Dict[str, Any]], anchor: str) -> Optional[Dict[str, Any]]:
    for t in tasks:
        if str(t.get("name") or "").strip() == anchor.strip():
            return t
    for t in tasks:
        if anchor and anchor.strip() and anchor.strip() in str(t.get("name") or ""):
            return t
    return None


def resolve_physical_construction_span(schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the canonical physical-construction span for one schedule.

    Priority 1 — the engine's own declared anchors (``phase_semantics``).
    Priority 2 — an explicitly declared physical-construction window.
    Never the Phase-N summary row.
    """
    tasks = schedule.get("tasks") or []
    ps = schedule.get("phase_semantics") or {}
    sc = (ps.get("site_construction") or {}) if isinstance(ps, dict) else {}

    if sc.get("start") and sc.get("finish"):
        anchors = sc.get("anchors") or {}
        anchor_rows = {}
        for key in ("site_construction_start", "physical_completion"):
            nm = anchors.get(key)
            t = _find_task(tasks, nm) if nm else None
            if t:
                anchor_rows[key] = dict(id=t.get("id"), name=t.get("name"),
                                        start=str(t.get("start"))[:10],
                                        finish=str(t.get("finish"))[:10])
        # The declared anchors are authoritative for the START; the finish must be
        # the physical-completion milestone, never a later post-completion node.
        start = sc["start"]
        finish = (anchor_rows.get("physical_completion") or {}).get("finish") or sc["finish"]
        return dict(start=str(start)[:10], finish=str(finish)[:10],
                    source="phase_semantics.site_construction",
                    definition=sc.get("definition"),
                    anchors=anchor_rows,
                    engine_declared_calendar_days=sc.get("calendar_days"))

    # Scenario B variants carry the window explicitly
    if schedule.get("physical_construction_start") and schedule.get("physical_construction_finish"):
        return dict(start=str(schedule["physical_construction_start"])[:10],
                    finish=str(schedule["physical_construction_finish"])[:10],
                    source="declared_physical_construction_window",
                    definition="declared physical_construction_start -> physical_construction_finish",
                    anchors={},
                    engine_declared_calendar_days=None,
                    physical_completion_task_id=schedule.get("physical_completion_task_id"))

    raise ValueError("cannot resolve a physical-construction span: no phase_semantics "
                     "and no declared physical construction window")


def resolve_overall_programme(schedule: Dict[str, Any]) -> Dict[str, Any]:
    tasks = schedule.get("tasks") or []
    start = schedule.get("start_date")
    finish = schedule.get("finish_date")
    if not start or not finish:
        starts = [str(t.get("start"))[:10] for t in tasks if t.get("start")]
        finishes = [str(t.get("finish"))[:10] for t in tasks if t.get("finish")]
        start = start or (min(starts) if starts else None)
        finish = finish or (max(finishes) if finishes else None)
    return dict(start=(str(start)[:10] if start else None),
                finish=(str(finish)[:10] if finish else None))


# ---------------------------------------------------------------------------
# the provider
# ---------------------------------------------------------------------------
def canonical_scenario_metrics(scenario_id: str, schedule: Dict[str, Any],
                               optimizer: Optional[Dict[str, Any]] = None,
                               working_day_calendar: str = DEFAULT_WORKING_DAY_CALENDAR,
                               ) -> Dict[str, Any]:
    """Build the canonical metrics block for ONE scenario. Single source of truth."""
    span = resolve_physical_construction_span(schedule)
    prog = resolve_overall_programme(schedule)

    pc_cal = elapsed_calendar_days(span["start"], span["finish"])
    pc_wd = working_days(span["start"], span["finish"], working_day_calendar)
    prog_cal = elapsed_calendar_days(prog["start"], prog["finish"])

    critical_path_length = None
    driving_chain_length = None
    if optimizer:
        cp = optimizer.get("critical_path") or {}
        critical_path_length = cp.get("critical_task_count")
        driving_chain_length = cp.get("driving_chain_length")

    known_scope_finish = schedule.get("known_scope_finish") or prog["finish"]

    block = dict(
        scenario=scenario_id,
        definition_version=DEFINITION_VERSION,
        canonical_definition=CANONICAL_DEFINITION,
        module=MODULE,

        physical_construction=dict(
            start=span["start"],
            finish=span["finish"],
            span_source=span["source"],
            span_definition=span.get("definition"),
            anchors=span.get("anchors") or {},
            elapsed_calendar_days=duration_metric(
                pc_cal, UNIT_CALENDAR_DAYS, CALENDAR_ELAPSED, BASIS_ELAPSED),
            working_days=duration_metric(
                pc_wd, UNIT_WORKING_DAYS, working_day_calendar,
                BASIS_STANDARD_5D if working_day_calendar == CALENDAR_STANDARD_5D
                else BASIS_SITE_7D),
            calendar_basis=working_day_calendar,
        ),

        overall_programme=dict(
            start=prog["start"], finish=prog["finish"],
            elapsed_calendar_days=duration_metric(
                prog_cal, UNIT_CALENDAR_DAYS, CALENDAR_ELAPSED, BASIS_ELAPSED),
        ),

        known_scope_finish=known_scope_finish,

        critical_path=dict(
            critical_path_length=critical_path_length,
            driving_chain_length=driving_chain_length,
            critical_path_length_definition="leaves with total float == 0",
            driving_chain_length_definition="forward-driving chain nodes from start to latest finish",
        ),

        # flat convenience mirrors (exactly the field names the spec enumerates)
        physical_construction_start=span["start"],
        physical_construction_finish=span["finish"],
        physical_construction_elapsed_calendar_days=pc_cal,
        physical_construction_working_days=pc_wd,
        physical_construction_calendar_basis=working_day_calendar,
        overall_programme_start=prog["start"],
        overall_programme_elapsed_calendar_days=prog_cal,
        critical_path_length=critical_path_length,
        driving_chain_length=driving_chain_length,
    )
    return block


def as_flat_row(block: Dict[str, Any]) -> Dict[str, Any]:
    """One row per scenario for CSVs. Consumes the canonical block only."""
    pc = block["physical_construction"]
    return dict(
        scenario=block["scenario"],
        physical_construction_start=pc["start"],
        physical_construction_finish=pc["finish"],
        physical_construction_working_days=pc["working_days"]["duration_value"],
        physical_construction_calendar_id=pc["working_days"]["calendar_id"],
        physical_construction_elapsed_calendar_days=pc["elapsed_calendar_days"]["duration_value"],
        physical_construction_calendar_basis=pc["calendar_basis"],
        overall_programme_start=block["overall_programme"]["start"],
        overall_programme_elapsed_calendar_days=block["overall_programme"]["elapsed_calendar_days"]["duration_value"],
        known_scope_finish=block["known_scope_finish"],
        critical_path_length=block["critical_path_length"],
        driving_chain_length=block["driving_chain_length"],
    )


def assert_single_source(blocks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Consistency gate: every scenario must publish exactly one working-day value,
    and every published mirror must agree with the nested metric."""
    problems: List[str] = []
    values: Dict[str, Optional[int]] = {}
    for b in blocks:
        sid = b["scenario"]
        pc = b["physical_construction"]
        wd_nested = pc["working_days"]["duration_value"]
        wd_flat = b.get("physical_construction_working_days")
        cal_nested = pc["elapsed_calendar_days"]["duration_value"]
        cal_flat = b.get("physical_construction_elapsed_calendar_days")
        values[sid] = wd_nested
        if wd_nested != wd_flat:
            problems.append(f"{sid}: working-day mirror disagrees ({wd_nested} vs {wd_flat})")
        if cal_nested != cal_flat:
            problems.append(f"{sid}: calendar-day mirror disagrees ({cal_nested} vs {cal_flat})")
        if not pc["working_days"].get("calendar_id"):
            problems.append(f"{sid}: working days carry no calendar_id")
        if pc["start"] is None or pc["finish"] is None:
            problems.append(f"{sid}: physical construction span unresolved")
    return dict(pass_=not problems, problems=problems,
                single_working_day_value_per_scenario=values,
                rule="one calculated schedule -> one authoritative duration metric")
