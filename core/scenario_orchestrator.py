# -*- coding: utf-8 -*-
"""V6.6.0 — generic scenario orchestrator.

Generates, for ANY (area, floors, zones) input:

* **Scenario A**  — monolithic single-workfront baseline (`sectional=off`, `workfronts=1`).
* **Scenario B1** — rolling spatial flow (zone → zone → next floor) retaining the
  *calculated* mobilisation duration from the template.
* **Scenario B2** — the same rolling spatial flow on a PM-reviewed *essential*
  mobilisation duration.

It replaces the V6.5.0.1 ``scripts/run_scenario_challenge.py`` static fixture, which
only knew how to reproduce one hardcoded 3,000 ㎡ / 3-zone case. Scenario durations
remain an OUTPUT; nothing here tunes a duration to reach a preferred answer.

Every metric is read from :mod:`core.scenario_metrics` — the single canonical provider.
No scenario re-derives its own working-day count.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Optional, Sequence

from core.scenario_metrics import canonical_scenario_metrics, as_flat_row, DEFAULT_WORKING_DAY_CALENDAR

DEFAULT_ESSENTIAL_MOBILISATION_DAYS = 4
MOBILISATION_NAME_HINTS = ("场地保护与临时设施", "site setup", "mobilisation", "mobilization")


def _find_mobilisation_tasks(tasks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for t in tasks:
        if t.get("activity_type") == "site_setup":
            out.append(t)
            continue
        nm = str(t.get("name") or "").lower()
        if any(h.lower() in nm for h in MOBILISATION_NAME_HINTS):
            out.append(t)
    return out


def essential_mobilisation(tasks: List[Dict[str, Any]],
                           days: int = DEFAULT_ESSENTIAL_MOBILISATION_DAYS,
                           log: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """PM-reviewed essential mobilisation (perimeter hoarding + temporary power only).

    Returns the list of modified tasks. The template mobilisation is *replaced*, never
    silently deleted — the change is recorded on the task.
    """
    changed = []
    for t in _find_mobilisation_tasks(tasks):
        before = t.get("duration_days", t.get("duration"))
        if before is None:
            continue
        t["template_duration_days"] = before
        t["duration_days"] = int(days)
        t["duration"] = int(days)
        t["mobilisation_basis"] = ("ESSENTIAL_MOBILISATION: perimeter hoarding + temporary power; "
                                   "secondary welfare setup proceeds in parallel.")
        t["mobilisation_reduced_from"] = before
        changed.append(t)
        if log is not None:
            log.append(f"  · mobilisation {before}d → {days}d on task {t.get('id')} {t.get('name')}")
    return changed


def _solve(tasks: List[Dict[str, Any]], start_date: str) -> Dict[str, Any]:
    from core.solver_engine import solve_schedule
    return solve_schedule(tasks, start_date)


def _metrics(name: str, solved: Dict[str, Any], optimizer: Optional[Dict[str, Any]] = None,
             working_day_calendar: str = DEFAULT_WORKING_DAY_CALENDAR) -> Dict[str, Any]:
    from core.phase_semantics import phase_semantics_block
    sched = dict(tasks=solved.get("tasks") or [],
                 start_date=solved.get("start_date") or solved.get("start"),
                 finish_date=solved.get("finish_date"),
                 phase_semantics=phase_semantics_block(solved.get("tasks") or []))
    return canonical_scenario_metrics(name, sched, optimizer,
                                      working_day_calendar=working_day_calendar)


def run_scenario_a(template_tasks: Sequence[Dict[str, Any]], area_sqm: float,
                   start_date: str, **ctx) -> Dict[str, Any]:
    """Monolithic baseline: one workfront, strict FS, no spatial bypass."""
    from core.fitout_engine import prepare_fitout
    prepared = prepare_fitout(copy.deepcopy(list(template_tasks)), area_sqm,
                              workfronts=1, sectional="off", **ctx)
    solved = _solve(prepared["tasks"], start_date)
    from core.optimizer import analyze_schedule
    opt = analyze_schedule(solved["tasks"], start_date=start_date, verify=True)
    return dict(scenario="scenario_a", label="Monolithic single-workfront baseline",
                mode="monolithic", solved=solved, optimizer=opt,
                metrics=_metrics("scenario_a", solved, opt),
                task_count=len(solved["tasks"]))


def run_scenario_b(template_tasks: Sequence[Dict[str, Any]], area_sqm: float,
                   start_date: str, variant: str = "b1", floors: int = 1,
                   floor_breakdown=None, zones_per_floor: Optional[int] = None,
                   essential_mob_days: int = DEFAULT_ESSENTIAL_MOBILISATION_DAYS,
                   **ctx) -> Dict[str, Any]:
    """Rolling spatial flow. ``variant='b1'`` keeps calculated mob; ``'b2'`` uses essential mob."""
    from core.fitout_engine import prepare_spatial_fitout
    from core.zone_model import apportion

    src = copy.deepcopy(list(template_tasks))
    notes: List[str] = []

    manual_zones = None
    zone_mode = "auto"
    if zones_per_floor:
        zone_mode = "manual"
        floor_specs = (floor_breakdown if floor_breakdown
                       else [dict(floor_id=f"F{i + 1:02d}", area_sqm=p)
                             for i, p in enumerate(apportion(float(area_sqm),
                                                             [1.0] * max(1, int(floors))))])
        manual_zones = []
        for f in floor_specs:
            parts = apportion(float(f["area_sqm"]), [1.0] * int(zones_per_floor))
            for i, part in enumerate(parts):
                manual_zones.append(dict(floor_id=f["floor_id"], zone_id=f"Z{i + 1}", area_sqm=part))

    prepared = prepare_spatial_fitout(src, area_sqm, zone_mode=zone_mode,
                                      manual_zones=manual_zones,
                                      floors_count=floors, floor_breakdown=floor_breakdown,
                                      **ctx)
    tasks = prepared["tasks"]
    if variant == "b2":
        essential_mobilisation(tasks, days=essential_mob_days, log=notes)
    solved = _solve(tasks, start_date)
    from core.optimizer import analyze_schedule
    opt = analyze_schedule(solved["tasks"], start_date=start_date, verify=True)
    name = f"scenario_{variant}"
    return dict(scenario=name,
                label=("Rolling spatial flow + calculated mobilisation" if variant == "b1"
                       else "Rolling spatial flow + essential mobilisation"),
                mode="rolling_spatial", solved=solved, optimizer=opt,
                metrics=_metrics(name, solved, opt),
                plan=prepared.get("plan"), task_count=len(solved["tasks"]),
                mobilisation_notes=notes)


def compare(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Tabulate the scenarios. Differences are CALCULATED, never presented as achieved."""
    table = []
    base = next((r for r in rows if r["scenario"] == "scenario_a"), None)
    base_wd = base["metrics"]["physical_construction_working_days"] if base else None
    for r in rows:
        flat = as_flat_row(r["metrics"])
        wd = flat["physical_construction_working_days"]
        diff_pct = (round((base_wd - wd) / base_wd * 100, 2)
                    if (base_wd and wd is not None) else None)
        table.append(dict(flat,
                          label=r["label"], mode=r["mode"],
                          critical_path_length=flat["critical_path_length"],
                          driving_chain_length=flat["driving_chain_length"],
                          calculated_difference_vs_a_pct=diff_pct))
    return dict(
        rows=table,
        baseline=dict(scenario="scenario_a", working_days=base_wd),
        labelling=("CALCULATED SCENARIO DIFFERENCE / POTENTIAL PROGRAMME IMPROVEMENT — subject to "
                   "validation of zoning, MEP sectional release, vertical logistics and "
                   "contractor methodology."),
        rule="Scenario duration is an OUTPUT. No scenario is tuned to reach a preferred answer.",
    )


def run_all(template_tasks: Sequence[Dict[str, Any]], area_sqm: float, start_date: str,
            floors: int = 1, floor_breakdown=None, zones_per_floor: Optional[int] = None,
            **ctx) -> Dict[str, Any]:
    """Convenience entry: Scenario A + B1 + B2 + comparison, for any parameters."""
    a = run_scenario_a(template_tasks, area_sqm, start_date, **ctx)
    b1 = run_scenario_b(template_tasks, area_sqm, start_date, variant="b1", floors=floors,
                        floor_breakdown=floor_breakdown, zones_per_floor=zones_per_floor, **ctx)
    b2 = run_scenario_b(template_tasks, area_sqm, start_date, variant="b2", floors=floors,
                        floor_breakdown=floor_breakdown, zones_per_floor=zones_per_floor, **ctx)
    return dict(area_sqm=area_sqm, floors=floors,
                zones_per_floor=zones_per_floor,
                scenarios={r["scenario"]: r for r in (a, b1, b2)},
                comparison=compare([a, b1, b2]))


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text[:10] if len(text) >= 10 else text


def programme_snapshot(solved: Optional[Dict[str, Any]],
                       optimizer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Finish date + driving chain from an already-solved programme. No mutation."""
    solved = solved or {}
    cp = (optimizer or {}).get("critical_path") or {}
    longest = cp.get("longest_critical_activities") or []
    chain = []
    for x in longest[:6]:
        chain.append(dict(name=x.get("name"), duration_days=x.get("duration_days"),
                          float_days=x.get("float_days"), start=_iso(x.get("start")),
                          finish=_iso(x.get("finish"))))
    return dict(
        finish=_iso(solved.get("finish_date") or solved.get("finish")),
        start=_iso(solved.get("start_date") or solved.get("start")),
        critical_task_count=cp.get("critical_task_count"),
        driving_chain_length=cp.get("driving_chain_length"),
        driving_chain=chain,
        source_fields=["solved.finish_date", "optimizer.critical_path.longest_critical_activities"],
    )


def clone_mobilisation_resolve(tasks: Sequence[Dict[str, Any]], start_date: str,
                               days: int = DEFAULT_ESSENTIAL_MOBILISATION_DAYS) -> Dict[str, Any]:
    """B2-style essential mobilisation on a COPY of the current graph. Baseline untouched."""
    clone = copy.deepcopy(list(tasks))
    notes: List[str] = []
    changed = essential_mobilisation(clone, days=days, log=notes)
    if not changed:
        return dict(status="NO_MOBILISATION_TASK", applied_to_baseline=False, notes=notes)
    solved = _solve(clone, start_date)
    from core.optimizer import analyze_schedule
    opt = analyze_schedule(solved["tasks"], start_date=start_date, verify=True)
    return dict(
        status="RE_SOLVED",
        applied_to_baseline=False,
        mutation=dict(kind="ESSENTIAL_MOBILISATION", days=int(days),
                      task_ids=[t.get("id") for t in changed],
                      evidence_fields=["core.scenario_orchestrator.essential_mobilisation"]),
        clone_solved=dict(start_date=solved.get("start_date"),
                          finish_date=solved.get("finish_date")),
        clone_opt=opt,
        notes=notes,
    )


_NO_MUTATION_REASON = {
    "SECTIONAL_HANDOVER": (
        "no approved clone mutation: inspection FS gates on the current graph are not rewritten. "
        "Instantiating extra zones on a single floor is N1 / PENDING-05. "
        "Template A/B1/B2 re-expansion stays opt-in on pack --with-scenarios and is never auto-selected"),
    "PROGRESSIVE_T&C": (
        "no approved clone mutation: splitting T&C would invent activities and durations "
        "without scope evidence (R3)"),
    "RESOURCE_REVIEW": (
        "no approved clone mutation: raising workfront/crew requires contractor confirmation, "
        "not an engine rewrite"),
}


def attach_register_comparisons(register: Dict[str, Any],
                                tasks: Sequence[Dict[str, Any]],
                                solved: Optional[Dict[str, Any]],
                                optimizer: Optional[Dict[str, Any]],
                                start_date: Optional[str] = None) -> Dict[str, Any]:
    """Persist clone re-solve facts onto the register. Never writes into ``tasks``."""
    start = start_date or (solved or {}).get("start_date") or (solved or {}).get("start")
    if hasattr(start, "strftime"):
        start = start.strftime("%Y-%m-%d")
    baseline = programme_snapshot(solved, optimizer)
    mob_result = None
    if start:
        mob_result = clone_mobilisation_resolve(tasks, str(start)[:10])

    for entry in register.get("register") or []:
        entry["applied"] = False
        cat = entry.get("category")
        if cat == "MOBILISATION_REVIEW" and mob_result and mob_result.get("status") == "RE_SOLVED":
            after = programme_snapshot(mob_result["clone_solved"], mob_result["clone_opt"])
            entry["quantified"] = True
            entry["clone_comparison"] = dict(
                status="RE_SOLVED",
                applied=False,
                recommended=False,
                labelling="CALCULATED SCENARIO DIFFERENCE — not an achieved saving; "
                          "essential mobilisation 4d is NOT a product-approved duration",
                product_approved=False,
                mutation=mob_result["mutation"],
                finish_before=baseline.get("finish"),
                finish_after=after.get("finish"),
                driving_chain_before=baseline.get("driving_chain"),
                driving_chain_after=after.get("driving_chain"),
                critical_task_count_before=baseline.get("critical_task_count"),
                critical_task_count_after=after.get("critical_task_count"),
                driving_chain_length_before=baseline.get("driving_chain_length"),
                driving_chain_length_after=after.get("driving_chain_length"),
                risk=entry.get("risk"),
                notes=mob_result.get("notes") or [],
                evidence_fields=[
                    "_pm_optimisation_register.json#register[].clone_comparison",
                    "core.scenario_orchestrator.clone_mobilisation_resolve",
                    "core.solver_engine.solve_schedule (clone only)",
                ],
            )
            continue
        reason = _NO_MUTATION_REASON.get(
            cat, "no approved clone mutation exists for this category")
        if cat == "MOBILISATION_REVIEW":
            reason = "mobilisation clone was not re-solved (missing start date or task)"
        entry["clone_comparison"] = dict(
            status="NO_APPROVED_CLONE_MUTATION",
            applied=False,
            recommended=False,
            quantified=False,
            reason=reason,
            finish_before=baseline.get("finish"),
            finish_after=None,
            driving_chain_before=baseline.get("driving_chain"),
            driving_chain_after=None,
            risk=entry.get("risk"),
            evidence_fields=[
                "_pm_optimisation_register.json#register[].clone_comparison.status",
            ],
        )

    register["closed_loop"] = dict(
        baseline_applied=False,
        applied=False,
        recommended_scenario=None,
        quantified=any(bool(e.get("quantified")) for e in (register.get("register") or [])),
        product_approved=False,
        re_solve_module="core.scenario_orchestrator",
        persisted_on_default_run=True,
        baseline=baseline,
        note=("Clone comparison is persisted on the Senior PM review path as read-only facts. "
              "ESSENTIAL_MOBILISATION 4d is an engine B2 candidate only — not product-approved "
              "and must not be applied. Sectional / T&C / workfront clones are not approved. "
              "Proposals remain applied=false; recommended_scenario remains null."),
    )
    register["note"] = (
        "The register is produced even when no target date exists. "
        "Clone comparison is read-only and not product-approved; "
        "proposals remain applied=false.")
    return register


def negative_float_report(solved: Dict[str, Any]) -> Dict[str, Any]:
    """Zero-negative-float assertion used by the multi-floor acceptance gate."""
    from core.optimizer import analyze_schedule
    opt = analyze_schedule(solved["tasks"], start_date=solved.get("start_date")
                           or solved.get("start") or "2026-10-12", verify=True)
    by_task = (opt.get("float") or {}).get("by_task") or []
    neg = [r for r in by_task if (r.get("float_days") or 0) < 0]
    return dict(checked=len(by_task), negative_float=len(neg),
                samples=neg[:10], pass_=(len(neg) == 0),
                critical_path_length=opt["critical_path"].get("critical_task_count"),
                driving_chain_length=opt["critical_path"].get("driving_chain_length"))
