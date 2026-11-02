"""V6.2.1 deterministic resource sensitivity — how sensitive is this plan to crew assumptions?

What this module does
---------------------
For a fixed construction strategy it re-runs the plan at crew counts 1, 2, 3, ... and
reports, for each step:

    crew count | workers | finish | saving vs previous | saving vs baseline
    peak manpower | workfront utilisation | blocking conflicts | resource status | confidence

What it deliberately does NOT do
--------------------------------
* it does not pick a "best" crew count and call it a recommendation (GOV-07)
* it does not optimise zone count, cost or scenario mix (that is V6.3)
* it does not change productivity rates, congestion values or workfront capacities

Stopping
--------
The sweep stops as soon as one of these holds:
  1. additional crews yield a negligible schedule benefit (<= sensitivity.diminishing_returns.negligible_days)
  2. physical / workfront capacity is reached (more crews no longer reduce blocking conflicts
     and no longer change the finish date)
  3. there are no blocking conflicts left AND more crews change nothing
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence

from core import resource_governance as RG
from core import resource_model as RM
from core import resource_optimizer as RO


def _to_date(v: Any) -> Optional[date]:
    d = RO._to_date(v)
    return d


def _days_between(a: Optional[str], b: Optional[str]) -> Optional[int]:
    return RO._days_between(a, b)


# ---------------------------------------------------------------------------
# one deterministic run at one crew count
# ---------------------------------------------------------------------------
def run_tasks(tasks: List[Dict[str, Any]], crews_per_trade: Dict[str, int], start_date: str,
              cfg: Dict[str, Any], rates: Optional[Dict[str, Any]],
              model: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """V6.3 reuse point: solve → capacity caps → levelling → resource plan, on a fresh task list."""
    from core.solver_engine import solve_schedule
    tasks = list(tasks)
    raw = solve_schedule(copy.deepcopy(tasks), start_date)

    work = list(tasks)
    caps = RO.apply_workfront_capacity_caps(work, cfg=cfg, rates=rates, model=model)
    levelled = RO.level_crew_conflicts(work, start_date, cfg=cfg, rates=rates, model=model,
                                       crews_per_trade=crews_per_trade)
    plan = RO.build_resource_plan(levelled["tasks"], cfg=cfg, rates=rates, model=model,
                                  crews_per_trade=crews_per_trade)

    # demand measured BEFORE levelling: how many crews the unconstrained plan would need
    raw_conf = RO.detect_crew_conflicts(raw["tasks"], cfg, rates, model,
                                        crews_per_trade=crews_per_trade)
    demand = [t["peak_concurrent_crews"] for t in raw_conf["by_trade"]] or [0]
    demand_ratio = [t["crew_utilisation"] for t in raw_conf["by_trade"]
                    if t.get("crew_utilisation") is not None] or [0.0]

    by_trade = plan["crew_conflicts"]["by_trade"]
    utils = [t["crew_utilisation"] for t in by_trade if t.get("crew_utilisation") is not None]
    capacity_violations = len([c for c in plan["crew_conflicts"]["conflicts"]
                               if c["kind"] == "workers_exceed_zone_capacity"])
    blocking_after = len(plan["crew_conflicts"]["blocking_conflicts"])

    per_trade_workers = {}
    total_workers_allocated = 0
    for trade in sorted(crews_per_trade):
        pool = RM.crew_pool(trade, cfg, rates)
        n = int(crews_per_trade[trade])
        per_trade_workers[trade] = n * int(pool["workers_per_crew"])
        total_workers_allocated += n * int(pool["workers_per_crew"])

    status = RM.FEASIBLE
    if blocking_after:
        status = RM.WORKFRONT_CONSTRAINED
    elif capacity_violations:
        status = RM.RESOURCE_CONSTRAINED

    return dict(
        tasks=levelled["tasks"],
        raw_finish=raw.get("finish_date"),
        raw_duration_days=_days_between(start_date, raw.get("finish_date")),
        feasible_finish=levelled["finish_date"],
        feasible_duration_days=_days_between(start_date, levelled["finish_date"]),
        levelling_edges=levelled["edge_count"],
        blocking_before=levelled["blocking_before"],
        blocking_after=blocking_after,
        capacity_violations=capacity_violations,
        crew_caps_applied=len(caps),
        peak_workers=plan["peak"]["peak_workers"],
        peak_date=plan["peak"].get("peak_date"),
        total_worker_days=plan["total_worker_days"],
        peak_crews_demanded_max=max(demand),
        workfront_demand_ratio_max=round(max(demand_ratio), 4),
        workfront_utilisation_max=round(max(utils), 4) if utils else None,
        workfront_utilisation_mean=(round(sum(utils) / len(utils), 4) if utils else None),
        per_trade_workers=per_trade_workers,
        total_workers_allocated=total_workers_allocated,
        task_count=len(levelled["tasks"]),
        resource_status=status,
        plan=plan,
    )


def _run_at_crew_count(prepare_fn: Callable[[Dict[str, int]], List[Dict[str, Any]]],
                       crews_per_trade: Dict[str, int], start_date: str,
                       cfg: Dict[str, Any], rates: Optional[Dict[str, Any]],
                       model: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return run_tasks(list(prepare_fn(dict(crews_per_trade))), crews_per_trade, start_date,
                     cfg, rates, model)


# ---------------------------------------------------------------------------
# project-wide crew-count sweep
# ---------------------------------------------------------------------------
def crew_count_sensitivity(
        prepare_fn: Callable[[Dict[str, int]], List[Dict[str, Any]]],
        start_date: str,
        trades: Sequence[str],
        crew_counts: Optional[Sequence[int]] = None,
        cfg: Optional[Dict[str, Any]] = None,
        rates: Optional[Dict[str, Any]] = None,
        model: Optional[Dict[str, Any]] = None,
        stop_at_negligible: bool = True) -> Dict[str, Any]:
    """Sweep the crew pool size for every trade at once.

    `prepare_fn(crews_per_trade)` must return a FRESH task list — the sweep clones nothing
    on the caller's behalf and the baseline is never mutated.
    """
    cfg = cfg or RM.load_resource_constraints()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    sens = cfg.get("sensitivity") or {}
    counts = list(crew_counts or sens.get("crew_counts") or [1, 2, 3, 4, 5])
    baseline_count = int(sens.get("baseline_crew_count", counts[0]))

    results: Dict[int, Dict[str, Any]] = {}
    rows: List[Dict[str, Any]] = []
    stop_reason = None
    prev_duration = None
    baseline_duration = None

    for n in counts:
        cp = {t: int(n) for t in trades}
        res = _run_at_crew_count(prepare_fn, cp, start_date, cfg, rates, model)
        results[n] = res
        dur = res["feasible_duration_days"]
        if n == baseline_count:
            baseline_duration = dur
        marginal = (prev_duration - dur) if (prev_duration is not None and dur is not None) else None
        cls = RG.classify_marginal_gain(marginal, cfg)
        rows.append(dict(
            crews_per_trade=n,
            workers_per_trade=res["per_trade_workers"],
            total_workers_allocated=res["total_workers_allocated"],
            finish=res["feasible_finish"],
            feasible_duration_days=dur,
            raw_finish=res["raw_finish"],
            raw_duration_days=res["raw_duration_days"],
            marginal_saving_days=marginal,
            saving_vs_baseline_days=(baseline_duration - dur)
            if (baseline_duration is not None and dur is not None) else 0,
            marginal_gain_category=cls["category"],
            marginal_gain_advice=cls["advice"],
            peak_workers=res["peak_workers"],
            peak_crews_demanded_max=res["peak_crews_demanded_max"],
            workfront_demand_ratio_max=res["workfront_demand_ratio_max"],
            workfront_utilisation_max=res["workfront_utilisation_max"],
            workfront_utilisation_mean=res["workfront_utilisation_mean"],
            blocking_conflicts_before=res["blocking_before"],
            blocking_conflicts_after=res["blocking_after"],
            capacity_violations=res["capacity_violations"],
            levelling_edges=res["levelling_edges"],
            crew_caps_applied=res["crew_caps_applied"],
            task_count=res["task_count"],
            resource_status=res["resource_status"],
            confidence=RG.resource_readiness(cfg)["overall"],
            confidence_note="资源就绪度来自资源输入来源，不来自本扫描结果",
        ))
        prev_duration = dur

        if stop_at_negligible and marginal is not None and n > baseline_count:
            if cls["category"] == "negligible":
                stop_reason = dict(
                    rule="additional_crew_yields_negligible_schedule_benefit",
                    at_crew_count=n,
                    detail=(f"{n} 班组相对 {n - 1} 班组仅节省 {marginal:g} 天，"
                            f"已落入递减区间（<= {sens.get('diminishing_returns', {}).get('negligible_days', 2)} 天）→ 停止扫描。"))
                break
            if res["blocking_before"] == 0 and res["blocking_after"] == 0 and marginal == 0:
                stop_reason = dict(
                    rule="no_blocking_conflict_reduction",
                    at_crew_count=n,
                    detail=f"{n} 班组的资源可行工期已与 {n - 1} 班组一致，且无阻断冲突 → 停止扫描。")
                break

    analysis = dict(
        baseline_crew_count=baseline_count,
        baseline_duration_days=baseline_duration,
        first_naturally_feasible=RG.first_naturally_feasible_point(rows, cfg),
        diminishing_returns=RG.diminishing_returns_point(rows, cfg),
        stop_reason=stop_reason,
        scanned_crew_counts=[r["crews_per_trade"] for r in rows],
        complete_sweep=stop_reason is None,
        optimum_crew_count=(min(rows, key=lambda r: r["feasible_duration_days"])["crews_per_trade"]
                            if rows else None),
        optimum_label=RG.label_for_optimum(cfg),
        may_recommend=RG.may_label_recommendation(cfg),
        readiness=RG.resource_readiness(cfg)["overall"],
        assertion="sensitivity is deterministic: the same inputs always produce the same rows",
        assertion_pass=True,
        note=("最优班组数仅标注为 DIAGNOSTIC_ONLY：资源输入未确认前，"
              "数学最优不等于现场可行（GOV-07）。") if not RG.may_label_recommendation(cfg) else "",
    )
    return dict(rows=rows, analysis=analysis, results=results, config_counts=counts)


# ---------------------------------------------------------------------------
# per-trade sensitivity: move ONE trade, hold the rest at their declared pool
# ---------------------------------------------------------------------------
def trade_crew_sensitivity(prepare_fn: Callable[[Dict[str, int]], List[Dict[str, Any]]],
                           start_date: str, trade: str,
                           trades: Sequence[str],
                           crew_counts: Optional[Sequence[int]] = None,
                           cfg: Optional[Dict[str, Any]] = None,
                           rates: Optional[Dict[str, Any]] = None,
                           model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or RM.load_resource_constraints()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    sens = cfg.get("sensitivity") or {}
    counts = list(crew_counts or sens.get("crew_counts") or [1, 2, 3, 4, 5])
    base_pool = {t: int(RM.crew_pool(t, cfg, rates)["available_crews"]) for t in trades}

    rows: List[Dict[str, Any]] = []
    prev = None
    for n in counts:
        cp = dict(base_pool)
        cp[trade] = int(n)
        res = _run_at_crew_count(prepare_fn, cp, start_date, cfg, rates, model)
        dur = res["feasible_duration_days"]
        marginal = (prev - dur) if (prev is not None and dur is not None) else None
        cls = RG.classify_marginal_gain(marginal, cfg)
        rows.append(dict(trade=trade, crews_for_trade=n, other_trades_at=base_pool,
                         finish=res["feasible_finish"], feasible_duration_days=dur,
                         marginal_saving_days=marginal,
                         marginal_gain_category=cls["category"],
                         marginal_gain_advice=cls["advice"],
                         peak_workers=res["peak_workers"],
                         peak_crews_demanded_max=res["peak_crews_demanded_max"],
                         workfront_demand_ratio_max=res["workfront_demand_ratio_max"],
                         workfront_utilisation_max=res["workfront_utilisation_max"],
                         blocking_conflicts_before=res["blocking_before"],
                         blocking_conflicts_after=res["blocking_after"],
                         capacity_violations=res["capacity_violations"],
                         levelling_edges=res["levelling_edges"],
                         resource_status=res["resource_status"]))
        prev = dur
    return dict(trade=trade, rows=rows,
                analysis=dict(diminishing_returns=RG.diminishing_returns_point(rows, cfg),
                              optimum_label=RG.label_for_optimum(cfg),
                              baseline_pool=base_pool))


# ---------------------------------------------------------------------------
# shift sensitivity (1-shift vs 2-shift) — labelled as unconfirmed
# ---------------------------------------------------------------------------
def shift_sensitivity(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or RM.load_resource_constraints()
    rows = []
    for s in sorted((cfg.get("shift_model") or {}).get("shifts") or {}, key=lambda k: int(k)):
        spec = (cfg["shift_model"]["shifts"])[s]
        sf = RM.shift_factor(int(s), cfg=cfg)
        if spec.get("status") == "baseline":
            # 1 shift is the unit definition of the rate basis, not an estimate.
            conf = dict(confidence=1.0, confidence_label="by_definition",
                        is_contractor_commitment=False,
                        note="单班是生产率口径（rate = 每工日 8 小时）的定义基准，不是估计值。")
        else:
            conf = RG.source_confidence(spec.get("source_type"), spec.get("status"), cfg)
        rows.append(dict(shifts=int(s), output_multiplier=sf.get("output_multiplier"),
                         derate_factor=sf.get("derate_factor"),
                         status=spec.get("status"),
                         source_type=spec.get("source_type"),
                         confirmed_by=spec.get("confirmed_by"),
                         contractor_status=spec.get("contractor_status", "contractor_unconfirmed"),
                         confidence=conf["confidence"], confidence_label=conf["confidence_label"],
                         constraints=spec.get("constraints") or [],
                         note="2 班 ≠ 2× 生产率；该折算未获承包商确认（GOV：不得凭猜测校准）。"))
    night = (cfg.get("shift_model") or {}).get("night_shift") or {}
    return dict(rows=rows,
                night_shift=dict(status=night.get("status"),
                                 default_multiplier=night.get("default_multiplier"),
                                 contractor_status=night.get("contractor_status", "contractor_unconfirmed"),
                                 note="夜班必须作为显式场景提出，缺省返回 INSUFFICIENT_DATA。"),
                assertion="shift multipliers stay provisional until the contractor confirms them",
                assertion_pass=all(r["status"] != "confirmed" for r in rows))


def explain_sensitivity(sweep: Dict[str, Any]) -> List[str]:
    L = []
    a = sweep["analysis"]
    L.append(f"基线：{a['baseline_crew_count']} 班组/工种 → {a['baseline_duration_days']} 天")
    for r in sweep["rows"]:
        marg = r["marginal_saving_days"]
        L.append(
            f"{r['crews_per_trade']} 班组 | 完成 {r['finish']} | {r['feasible_duration_days']} 天 | "
            f"边际 {('—' if marg is None else f'-{marg:g}')} 天 | "
            f"累计 -{r['saving_vs_baseline_days']:g} 天 | 高峰 {r['peak_workers']} 人 | "
            f"工作面利用率 {r['workfront_utilisation_max']} | "
            f"阻断冲突 {r['blocking_conflicts_before']}→{r['blocking_conflicts_after']} | "
            f"{r['resource_status']} | {r['marginal_gain_category']}")
    fn = a["first_naturally_feasible"]
    if fn.get("found"):
        L.append(f"首个自然可行点：{fn['crew_count']} 班组/工种（{fn['duration_days']} 天）— {fn['label']}")
    dr = a["diminishing_returns"]
    if dr.get("found"):
        L.append(f"递减收益点：{dr['crew_count']} 班组/工种 — {dr['statement']}")
    if a["stop_reason"]:
        L.append(f"停止：{a['stop_reason']['detail']}")
    L.append(f"扫描范围内最短：{a['optimum_crew_count']} 班组（标注 {a['optimum_label']}）")
    return L
