"""V6.2 resource optimizer — feasibility before optimisation.

What this module does
---------------------
1. Inverse crew sizing: quantity + target duration → required workers, then validate that
   requirement against congestion, workfront capacity, the available crew pool, shifts and
   the V6.1.1 physical minimums. It reports what is FEASIBLE, not what is mathematically
   shortest.
2. Crew contention: consumes the V6.0 `derive_resource_contention()` groups and the V6.1
   `trade_contention()` groups and turns them into time-phased conflicts — the same crew
   cannot be in two zones on the same day.
3. Trade interference: same-zone concurrency between different trades, scored
   low / medium / high / blocked from an explicit matrix.
4. Target-date analysis: CPM → critical + near-critical → inverse resource sizing →
   capacity check → re-solve on a clone. Never a blanket compression of every activity.

Hard rules
----------
* The baseline is never mutated. Every proposal is built on a deep copy and re-solved;
  `assert_baseline_unchanged()` proves it.
* Inspection gates, authority gates, mobilisation and milestones are never crashed.
* Congestion, shift and capacity factors come only from config/resource_constraints.json
  and are flagged `provisional` when they have no evidence.
* Productivity comes only from config/productivity_rates.json via core.productivity.
"""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core import resource_model as RM

FEASIBLE = RM.FEASIBLE
RESOURCE_CONSTRAINED = RM.RESOURCE_CONSTRAINED
WORKFRONT_CONSTRAINED = RM.WORKFRONT_CONSTRAINED
PHYSICAL_MINIMUM = RM.PHYSICAL_MINIMUM
INSUFFICIENT_DATA = RM.INSUFFICIENT_DATA


def _to_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _days_between(a: Optional[str], b: Optional[str]) -> Optional[int]:
    da, db = _to_date(a), _to_date(b)
    if not da or not db:
        return None
    return (db - da).days


def is_production_task(task: Dict[str, Any]) -> bool:
    """Crashable work only. Gates, permits, mobilisation and milestones are not a capacity problem."""
    if task.get("inspection_gate") or task.get("is_mobilisation") or task.get("milestone"):
        return False
    if not task.get("activity_type"):
        return False
    if int(task.get("duration_days", task.get("duration", 0)) or 0) <= 0:
        return False
    return task.get("duration_method") in (None, "productivity_formula")


# ---------------------------------------------------------------------------
# 3. inverse crew calculation
# ---------------------------------------------------------------------------
def reverse_crew_requirement(activity_type: str, quantity: float, target_days: float,
                             trade: Optional[str] = None, rates: Optional[Dict[str, Any]] = None,
                             cfg: Optional[Dict[str, Any]] = None,
                             factors: Optional[Dict[str, Any]] = None,
                             rate_level: Optional[str] = None,
                             shifts: float = 1.0, night: bool = False,
                             base_efficiency: float = 1.0,
                             zone_area: Optional[float] = None,
                             available_crews: Optional[int] = None,
                             instance_scope: str = "zone",
                             semantics: Optional[Dict[str, Any]] = None,
                             model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Quantity + target duration → how many workers, and can they physically be there?

    The theoretical headcount is quantity / rate / target_days. That number is then
    validated against congestion, workfront capacity, the crew pool and the V6.1.1
    physical minimum. The answer is what is FEASIBLE, not what is arithmetically shortest.
    """
    cfg = cfg or RM.load_resource_constraints()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    trade = trade or RM.trade_of(activity_type, model)

    dm = RM.worker_days_required(activity_type, quantity, rates=rates, factors=factors,
                                 rate_level=rate_level, base_efficiency=base_efficiency)
    sf = RM.shift_factor(shifts, night=night, cfg=cfg)
    provisional: List[str] = []

    if sf["output_multiplier"] is None:
        return dict(activity_type=activity_type, trade=trade, quantity=quantity, unit=dm["unit"],
                    target_days=target_days, worker_days=dm["worker_days"],
                    theoretical_workers=None, recommended_workers=None, feasible_workers=None,
                    achievable_duration_days=None, status=INSUFFICIENT_DATA,
                    binding_constraint="shift_model", shift=sf, demand=dm,
                    provisional_factors=["shift_model"],
                    explanation=f"{shifts:g} 班（夜班={night}）的产出系数无证据 → 返回 INSUFFICIENT_DATA，不猜测。")
    if sf["status"] == RM.STATUS_PROVISIONAL:
        provisional.append("shift_output_multiplier")
    if dm["rate_status"] != "calibrated":
        provisional.append("productivity_rate")

    shift_mult = float(sf["output_multiplier"])
    pool = RM.crew_pool(trade, cfg, rates, activity_type=activity_type)
    if available_crews is not None:
        n_crews = max(1, int(available_crews))
        per_crew = int(pool["workers_per_crew"])
        pool = dict(pool, available_crews=n_crews,
                    available_workers=n_crews * per_crew,
                    max_practical_workers=max(per_crew, n_crews * per_crew))
    cap = RM.workfront_capacity(trade, zone_area, cfg,
                                area_per_front=(rates["activity_types"].get(activity_type, {})
                                                .get("crew_scaling", {}) or {}).get("area_per_front"))

    required_effective = dm["worker_days"] / (float(target_days) * shift_mult)
    theoretical_workers = max(1, int(math.ceil(round(required_effective, 6))))

    # smallest crew whose CONGESTED output meets the requirement
    max_search = max(int(pool["max_practical_workers"]), theoretical_workers + 1)
    recommended = None
    for n in range(1, max_search + 1):
        if n * RM.crew_efficiency(trade, n, cfg)["crew_efficiency"] >= required_effective - 1e-9:
            recommended = n
            break
    unreachable = recommended is None
    if unreachable:
        # Not even the practical ceiling reaches the target — congestion, not arithmetic.
        recommended = max_search

    max_practical = int(pool["max_practical_workers"])
    zone_cap = int(cap["capacity_workers"])
    pool_cap = int(pool["available_workers"])
    feasible_workers = min(recommended, max_practical, zone_cap, pool_cap)

    if unreachable:
        binding = "practical_ceiling_congestion"
    elif feasible_workers < recommended:
        limits = {"workfront_capacity": zone_cap, "crew_pool": pool_cap,
                  "max_practical_workers": max_practical}
        binding = min((k for k, v in limits.items() if v < recommended),
                      key=lambda k: (limits[k], k), default="none")
    else:
        binding = "none"

    cong = RM.crew_efficiency(trade, feasible_workers, cfg)
    dur = RM.duration_for_workers(dm["worker_days"], trade, feasible_workers, cfg,
                                  shifts=shifts, night=night)

    # --- V6.1.1 physical minimum: more crew may never break a real minimum -------------
    from core import duration_semantics as DS
    semantics = semantics or DS.load_duration_semantics()
    minres = DS.resolve_min_for_instance(activity_type, instance_scope=instance_scope,
                                         is_gate=False, cfg=semantics, rates=rates)
    applied_min = int(minres["applied_min_duration"] or 0)
    raw_rounded = int(dur["rounded_duration"])
    achievable = max(applied_min, raw_rounded)
    if minres["min_duration_scope"] == DS.SCOPE_LEGACY:
        provisional.append("min_duration_legacy_unclassified")
    if applied_min and raw_rounded < applied_min:
        provisional.append("min_duration_binding")

    if achievable > float(target_days):
        if applied_min >= raw_rounded and applied_min > 0:
            status = PHYSICAL_MINIMUM
        elif binding == "workfront_capacity":
            status = WORKFRONT_CONSTRAINED
        else:
            status = RESOURCE_CONSTRAINED
    else:
        status = FEASIBLE

    effective_workers = round(feasible_workers * cong["crew_efficiency"], 4)
    return dict(
        activity_type=activity_type, trade=trade, quantity=float(quantity), unit=dm["unit"],
        target_days=float(target_days),
        productivity_rate=dm["productivity_rate"], rate_level=dm["rate_level"],
        rate_status=dm["rate_status"], evidence_level=dm["evidence_level"],
        worker_days=dm["worker_days"], worker_hours=dm["worker_hours"],
        required_effective_workers=round(required_effective, 4),
        theoretical_workers=theoretical_workers,
        recommended_workers=int(recommended),
        feasible_workers=int(feasible_workers),
        crew_efficiency=cong["crew_efficiency"],
        effective_workers=effective_workers,
        congestion_loss_workers=round(int(feasible_workers) - effective_workers, 4),
        workfront_capacity_workers=int(cap["capacity_workers"]),
        workfront_capacity_formula=cap["formula"],
        max_practical_workers=max_practical,
        available_crews=pool["available_crews"], available_workers=pool["available_workers"],
        pool_status=pool["status"],
        shifts=shifts, shift_output_multiplier=shift_mult, shift_status=sf["status"],
        raw_duration_days=dur["raw_duration"], rounded_duration_days=raw_rounded,
        applied_min_duration=applied_min, min_duration_scope=minres["min_duration_scope"],
        minimum_duration_penalty_days=int(achievable - raw_rounded),
        achievable_duration_days=int(achievable),
        status=status, binding_constraint=binding,
        provisional_factors=sorted(set(provisional)),
        demand=dm, capacity=cap, pool=pool, shift=sf, congestion=cong,
        min_duration_reason=minres["reason"],
    )


# ---------------------------------------------------------------------------
# 14. explainability
# ---------------------------------------------------------------------------
def explain_allocation(row: Dict[str, Any]) -> str:
    """Human-readable answer to 'why not simply add more people?'."""
    if row.get("status") == INSUFFICIENT_DATA:
        return (f"{row.get('activity_type')}：目标 {row.get('target_days')} 天 → "
                f"{row.get('explanation', '数据不足，无法给出资源需求（不猜测）。')}")
    L = [f"工序：{row['activity_type']}（工种 {row['trade']}）",
         f"  工程量   : {row['quantity']:g} {row['unit']}",
         f"  生产率   : {row['productivity_rate']:g} {row['unit']}/工日（{row['rate_level']}，证据 {row['evidence_level']}，状态 {row['rate_status']}）",
         f"  工日需求 : {row['worker_days']:g} 工日 / {row['worker_hours']:g} 工时",
         f"  目标工期 : {row['target_days']:g} 天",
         f"  理论人数 : {row['theoretical_workers']} 人（{row['worker_days']:g} ÷ {row['target_days']:g} ÷ 班次 {row['shift_output_multiplier']:g}）",
         f"  建议人数 : {row['recommended_workers']} 人（考虑拥挤后的等效产能需求 {row['required_effective_workers']:g} 人）",
         f"  可行人数 : {row['feasible_workers']} 人（约束：{row['binding_constraint']}；"
         f"工作面容量 {row['workfront_capacity_workers']} 人，实用上限 {row['max_practical_workers']} 人，可用 {row['available_workers']} 人）",
         f"  有效人数 : {row['effective_workers']:g} 人（拥挤系数 {row['crew_efficiency']:g}，"
         f"损失 {row['congestion_loss_workers']:g} 人）—— 这就是不能简单加人的原因",
         f"  可达工期 : {row['achievable_duration_days']} 天（原始 {row['raw_duration_days']:g} → 取整 {row['rounded_duration_days']}"
         f" → 下限 {row['applied_min_duration']}[{row['min_duration_scope']}]）",
         f"  状态     : {row['status']}"]
    if row.get("provisional_factors"):
        L.append(f"  临时假设 : {', '.join(row['provisional_factors'])}（无现场标定，需确认）")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 6. crew contention (consumes V6.0 / V6.1 groups)
# ---------------------------------------------------------------------------
def _intervals(tasks: Sequence[Dict[str, Any]], cfg: Dict[str, Any], rates: Dict[str, Any],
               model: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for t in tasks:
        row = RM.task_demand(t, cfg=cfg, rates=rates, model=model)
        if not row:
            continue
        s, f = _to_date(t.get("start")), _to_date(t.get("finish"))
        if not s or not f or f < s:
            continue
        out.append(dict(task_id=t["id"], name=t.get("name"), trade=row["trade"],
                        activity_type=row["activity_type"], zone=t.get("zone"),
                        zone_label=t.get("zone_label"), workers=row["workers"],
                        start=s, finish=f))
    return out


def detect_crew_conflicts(tasks: Sequence[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None,
                          rates: Optional[Dict[str, Any]] = None,
                          model: Optional[Dict[str, Any]] = None,
                          crews_per_trade: Optional[Dict[str, int]] = None,
                          workers_per_crew: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Time-phased crew conflicts: one crew cannot occupy two zones on the same day."""
    cfg = cfg or RM.load_resource_constraints()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    intervals = _intervals(tasks, cfg, rates, model)
    by_trade: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for iv in intervals:
        by_trade[iv["trade"]].append(iv)

    def _zone_area(zone_id: Any) -> Optional[float]:
        for t in tasks:
            if t.get("zone") == zone_id and t.get("zone_area_sqm"):
                return float(t["zone_area_sqm"])
        return None

    rows: List[Dict[str, Any]] = []
    worst: Dict[str, Dict[str, Any]] = {}
    for trade, items in sorted(by_trade.items()):
        pool = RM.crew_pool(trade, cfg, rates)
        available_crews = int((crews_per_trade or {}).get(trade, pool["available_crews"]))
        per_crew = int((workers_per_crew or {}).get(trade, pool["workers_per_crew"]))
        available_workers = available_crews * per_crew
        dates = sorted({d for iv in items for d in (iv["start"], iv["finish"])})
        peak_crews = 0
        peak_workers = 0
        peak_day = None
        conflicts: List[Dict[str, Any]] = []
        seen = set()
        for d in dates:
            active = [iv for iv in items if iv["start"] <= d <= iv["finish"]]
            if not active:
                continue
            zones = {iv["zone"] for iv in active}
            # (a) CREW conflict: one crew cannot stand in two zones on the same day.
            crews_needed = len(zones)
            workers_needed = sum(iv["workers"] for iv in active)
            if crews_needed > peak_crews or (crews_needed == peak_crews and workers_needed > peak_workers):
                peak_crews, peak_workers, peak_day = crews_needed, workers_needed, d.isoformat()
            if crews_needed > available_crews:
                key = ("crew", trade, tuple(sorted(str(z) for z in zones)), d)
                if key not in seen:
                    seen.add(key)
                    conflicts.append(dict(
                        kind="crew_in_two_zones", trade=trade, date=d.isoformat(),
                        concurrent_crews_required=crews_needed, available_crews=available_crews,
                        crew_shortfall=crews_needed - available_crews,
                        concurrent_workers_required=workers_needed, available_workers=available_workers,
                        worker_shortfall=0, zones=sorted(str(z) for z in zones),
                        task_ids=[iv["task_id"] for iv in active], severity="blocking",
                        resolution="增派班组（crews_per_trade）或保持分区串行 FS 流水",
                        note="同一班组同一时间不能同时在两个分区 —— 物理约束，不是排程选择。"))
            # (b) WORKFRONT conflict: too many workers of one trade inside one zone.
            for zone in sorted(zones, key=str):
                in_zone = [iv for iv in active if iv["zone"] == zone]
                z_workers = sum(iv["workers"] for iv in in_zone)
                z_cap = RM.workfront_capacity(trade, _zone_area(zone), cfg)["capacity_workers"]
                if z_workers > z_cap:
                    key = ("zone", trade, str(zone), d)
                    if key not in seen:
                        seen.add(key)
                        conflicts.append(dict(
                            kind="workers_exceed_zone_capacity", trade=trade, date=d.isoformat(),
                            zone=str(zone), zone_area_sqm=_zone_area(zone),
                            concurrent_crews_required=1, available_crews=available_crews,
                            crew_shortfall=0,
                            concurrent_workers_required=z_workers, available_workers=z_cap,
                            worker_shortfall=z_workers - z_cap, zones=[str(zone)],
                            task_ids=[iv["task_id"] for iv in in_zone], severity="constrained",
                            resolution="拆分为更多分区或降低该分区投入人数（工期随之延长）",
                            note="单区可容纳人数由工作面容量决定，不能靠增加班组突破。"))
        worst[trade] = dict(trade=trade, workfronts=len(items), peak_concurrent_crews=peak_crews,
                            peak_concurrent_workers=peak_workers, peak_date=peak_day,
                            available_crews=available_crews, available_workers=available_workers,
                            crew_utilisation=round(peak_crews / available_crews, 4) if available_crews else None,
                            conflict_days=len({c["date"] for c in conflicts}),
                            status=(RM.FEASIBLE if not conflicts else
                                    (RESOURCE_CONSTRAINED if all(c["severity"] == "constrained" for c in conflicts)
                                     else WORKFRONT_CONSTRAINED)))
        rows.extend(conflicts)
    return dict(by_trade=[worst[k] for k in sorted(worst)], conflicts=rows,
                conflict_count=len(rows),
                blocking_conflicts=[c for c in rows if c["severity"] == "blocking"],
                assertion="same crew cannot occupy two zones on the same day",
                assertion_pass=not any(c["severity"] == "blocking" for c in rows))


# ---------------------------------------------------------------------------
# 7. trade interference
# ---------------------------------------------------------------------------
def trade_interference(tasks: Sequence[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None,
                       rates: Optional[Dict[str, Any]] = None,
                       model: Optional[Dict[str, Any]] = None,
                       min_level: str = "medium") -> Dict[str, Any]:
    """Same-zone concurrency between different trades, scored from an explicit matrix."""
    cfg = cfg or RM.load_resource_constraints()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    matrix = (cfg.get("trade_interference") or {}).get("matrix") or {}
    default_level = (cfg.get("trade_interference") or {}).get("default_level", "low")
    order = list(RM.LEVELS_INTERFERENCE)
    floor_idx = order.index(min_level)

    intervals = _intervals(tasks, cfg, rates, model)
    rows: List[Dict[str, Any]] = []
    seen = set()
    for a in intervals:
        for b in intervals:
            if a["task_id"] >= b["task_id"] or a["trade"] == b["trade"]:
                continue
            if a["zone"] != b["zone"]:
                continue
            latest_start = max(a["start"], b["start"])
            earliest_finish = min(a["finish"], b["finish"])
            if latest_start > earliest_finish:
                continue
            key = "|".join(sorted([a["trade"], b["trade"]]))
            spec = matrix.get(key) or matrix.get("|".join(reversed(key.split("|")))) or {}
            level = spec.get("level", default_level)
            if order.index(level) < floor_idx:
                continue
            sig = (a["task_id"], b["task_id"])
            if sig in seen:
                continue
            seen.add(sig)
            rows.append(dict(trade_a=a["trade"], trade_b=b["trade"], pair=key, level=level,
                             reason=spec.get("reason", "未声明的工种组合，按缺省等级处理（保守缺省，非验证结论）"),
                             zone=a["zone"], zone_label=a["zone_label"],
                             task_a=a["task_id"], task_b=b["task_id"],
                             activity_a=a["activity_type"], activity_b=b["activity_type"],
                             overlap_days=(earliest_finish - latest_start).days + 1,
                             overlap_window=f"{latest_start.isoformat()}..{earliest_finish.isoformat()}",
                             declared=bool(spec)))
    counts = {lv: sum(1 for r in rows if r["level"] == lv) for lv in order}
    return dict(rows=sorted(rows, key=lambda r: (-order.index(r["level"]), r["pair"])),
                counts=counts, blocked=count_by_level(rows, "blocked"),
                high=count_by_level(rows, "high"),
                min_level_reported=min_level)


def count_by_level(rows: Sequence[Dict[str, Any]], level: str) -> List[Dict[str, Any]]:
    return [r for r in rows if r["level"] == level]


# ---------------------------------------------------------------------------
# feasibility corrections (never applied to the baseline — the caller clones first)
# ---------------------------------------------------------------------------
def apply_workfront_capacity_caps(tasks: Sequence[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None,
                                  rates: Optional[Dict[str, Any]] = None,
                                  semantics: Optional[Dict[str, Any]] = None,
                                  model: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Cap each task's crew at what the zone can physically hold.

    V6.1 pins one project-level crew per zone instance. When that crew is larger than the
    zone's workfront capacity the plan is arithmetically valid but physically impossible.
    This shrinks the crew and lets the duration grow — the honest direction.
    """
    from core import duration_semantics as DS
    cfg = cfg or RM.load_resource_constraints()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    semantics = semantics or DS.load_duration_semantics()
    rows: List[Dict[str, Any]] = []
    for task in tasks:
        if not is_production_task(task):
            continue
        prod = task.get("productivity") or {}
        crew = int(prod.get("crew_size") or task.get("crew_size") or 1)
        trade = RM.trade_of(task.get("activity_type"), model)
        # A zone instance is capped by its own area; a project-level instance works the
        # whole project area at once, so it is capped by the project area.
        zone_area = task.get("zone_area_sqm") or task.get("project_area")
        cap = RM.workfront_capacity(trade, zone_area, cfg)
        limit = int(cap["capacity_workers"]) if zone_area else int(
            RM.crew_pool(trade, cfg, rates, task.get("activity_type"))["nominal_zone_capacity_workers"])
        if crew <= limit:
            continue
        applied = _apply_crew(task, limit, trade, cfg, rates, semantics)
        if not applied.get("applied"):
            continue
        rows.append(dict(task_id=task["id"], name=task.get("name"), activity_type=task.get("activity_type"),
                         trade=trade, zone=task.get("zone"), zone_area_sqm=zone_area,
                         previous_workers=crew, capped_workers=limit,
                         capacity_formula=cap["formula"],
                         previous_duration_days=applied["previous_duration_days"],
                         new_duration_days=applied["new_duration_days"],
                         days_added=int(applied["new_duration_days"]) - int(applied["previous_duration_days"]),
                         reason=f"分区物理容量 {limit} 人 < 投入 {crew} 人 → 人数下调，工期如实延长"))
    return rows


def _topo_reorder(tasks: List[Dict[str, Any]]) -> bool:
    """Re-sort the task list into topological order in place.

    solve_schedule walks the list in order and silently drops a predecessor that appears
    later in the list, so a levelling edge is only effective once the list is topologically
    sorted. Ids are untouched — only the list order changes.
    """
    from core._common import split_predecessor_id_suffix
    ids = [t["id"] for t in tasks]
    pos = {tid: i for i, tid in enumerate(ids)}
    indeg = {tid: 0 for tid in ids}
    succ: Dict[Any, List[Any]] = defaultdict(list)
    for t in tasks:
        seen = set()
        for pid, _s in split_predecessor_id_suffix(str(t.get("predecessors", "") or "")):
            if pid in pos and pid != t["id"] and pid not in seen:
                seen.add(pid)
                succ[pid].append(t["id"])
                indeg[t["id"]] += 1
    queue = [tid for tid in ids if indeg[tid] == 0]
    out: List[Any] = []
    while queue:
        cur = queue.pop(0)
        out.append(cur)
        for s in sorted(succ.get(cur, []), key=lambda x: pos[x]):
            indeg[s] -= 1
            if indeg[s] == 0:
                queue.append(s)
        queue.sort(key=lambda x: pos[x])
    if len(out) != len(ids):
        return False  # cycle: leave the list untouched rather than corrupt it
    by_id = {t["id"]: t for t in tasks}
    tasks[:] = [by_id[i] for i in out]
    return True


def _ancestors(task_id: Any, by_id: Dict[Any, Dict[str, Any]]) -> set:
    from core._common import split_predecessor_id_suffix
    seen = set()
    stack = [task_id]
    while stack:
        cur = stack.pop()
        t = by_id.get(cur)
        if not t:
            continue
        for pid, _s in split_predecessor_id_suffix(str(t.get("predecessors", "") or "")):
            if pid not in seen:
                seen.add(pid)
                stack.append(pid)
    return seen


def level_crew_conflicts(tasks: Sequence[Dict[str, Any]], start_date: str,
                         cfg: Optional[Dict[str, Any]] = None,
                         rates: Optional[Dict[str, Any]] = None,
                         model: Optional[Dict[str, Any]] = None,
                         crews_per_trade: Optional[Dict[str, int]] = None,
                         max_iterations: int = 6) -> Dict[str, Any]:
    """Deterministic crew levelling: at most `available_crews` tasks of one trade may run
    at the same time. Overlapping pairs are serialised with an explicit FS handover edge.

    This is feasibility, not optimisation: levelling can only delay work, never pull it in.
    Every added edge carries the reason it exists.
    """
    from core.solver_engine import solve_schedule
    cfg = cfg or RM.load_resource_constraints()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    crews_per_trade = crews_per_trade or {}

    work = copy.deepcopy(list(tasks))
    first = solve_schedule(copy.deepcopy(work), start_date)
    order = {}
    for t in first["tasks"]:
        if t.get("start"):
            order[t["id"]] = (str(t["start"]), int(t["id"]))
    trade_of_task = {}
    for t in work:
        if is_production_task(t):
            trade_of_task[t["id"]] = RM.trade_of(t.get("activity_type"), model)

    added: List[Dict[str, Any]] = []
    iterations: List[Dict[str, Any]] = []
    before_conflicts = None
    for it in range(1, max_iterations + 1):
        solved = solve_schedule(copy.deepcopy(work), start_date)
        cs = detect_crew_conflicts(solved["tasks"], cfg, rates, model, crews_per_trade=crews_per_trade)
        if before_conflicts is None:
            before_conflicts = cs
        blocking = [c for c in cs["conflicts"] if c["severity"] == "blocking"]
        if not blocking:
            iterations.append(dict(iteration=it, blocking_conflicts=0, edges_added=0, note="无班组冲突，收敛"))
            break
        by_id = {t["id"]: t for t in work}
        new_edges = 0
        for trade, items in _group_by_trade(solved["tasks"], trade_of_task).items():
            pool = RM.crew_pool(trade, cfg, rates)
            available = int(crews_per_trade.get(trade, pool["available_crews"]))
            seq = sorted(items, key=lambda t: order.get(t["id"], ("9999", t["id"])))
            for i in range(len(seq) - available):
                # Mutate the tasks in `work`, not the throw-away solved copies.
                pred = by_id.get(seq[i]["id"])
                succ = by_id.get(seq[i + available]["id"])
                if pred is None or succ is None:
                    continue
                if pred["id"] == succ["id"]:
                    continue
                # A cycle would appear if the successor already reaches the predecessor.
                if succ["id"] in _ancestors(pred["id"], by_id):
                    continue
                tokens = [x for x in str(succ.get("predecessors", "") or "").split(",") if x.strip()]
                token = f"{pred['id']}FS"
                if token in tokens:
                    continue
                tokens.append(token)
                succ["predecessors"] = ",".join(dict.fromkeys(tokens))
                succ.setdefault("resource_levelling_edges", []).append(pred["id"])
                prevs = [x for x in tokens if x.startswith(f"{pred['id']}")]
                added.append(dict(predecessor=pred["id"], successor=succ["id"], trade=trade,
                                  relationship="FS", lag=0,
                                  pred_zone=pred.get("zone"), succ_zone=succ.get("zone"),
                                  same_zone=(pred.get("zone") is not None
                                             and pred.get("zone") == succ.get("zone")),
                                  previous_relationship=(prevs[0] if prevs else None),
                                  overrides_ontology_link=bool(prevs) and not prevs[0].startswith(f"{pred['id']}FS"),
                                  reason=(f"{trade} 可用班组 {available} 个：#{pred['id']}"
                                          f"（{pred.get('zone_label') or pred.get('zone') or '项目级'}）未完成时，"
                                          f"同一班组不能同时作业于 #{succ['id']}"
                                          f"（{succ.get('zone_label') or succ.get('zone') or '项目级'}）"
                                          + (f"；原关系 {prevs[0]} 被资源约束覆盖为 FS"
                                             if prevs and not prevs[0].startswith(f"{pred['id']}FS") else ""))))
                new_edges += 1
        # Levelling edges only take effect once the list is topologically sorted; otherwise
        # solve_schedule drops them as forward references.
        reordered = _topo_reorder(work) if new_edges else True
        iterations.append(dict(iteration=it, blocking_conflicts=len(blocking),
                               edges_added=new_edges, topologically_reordered=bool(reordered)))
        if new_edges == 0:
            iterations[-1]["note"] = "无可用边可加（避免成环）→ 停止"
            break

    final = solve_schedule(work, start_date)
    after_conflicts = detect_crew_conflicts(final["tasks"], cfg, rates, model, crews_per_trade=crews_per_trade)
    return dict(tasks=final["tasks"], finish_date=final["finish_date"],
                edges=added, edge_count=len(added), iterations=iterations,
                conflicts_before=before_conflicts, conflicts_after=after_conflicts,
                blocking_before=len([c for c in (before_conflicts or {}).get("conflicts", [])
                                     if c["severity"] == "blocking"]),
                blocking_after=len([c for c in after_conflicts["conflicts"] if c["severity"] == "blocking"]),
                note="班组平衡只会延后工作，不会提前；这是可行性修正，不是工期优化。")


def _group_by_trade(tasks: Sequence[Dict[str, Any]], trade_of_task: Dict[Any, str]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in tasks:
        tr = trade_of_task.get(t.get("id"))
        if tr and t.get("start") and t.get("finish"):
            out[tr].append(t)
    return out


# ---------------------------------------------------------------------------
# 10. resource feasibility for a whole plan
# ---------------------------------------------------------------------------
def build_resource_plan(tasks: Sequence[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None,
                        rates: Optional[Dict[str, Any]] = None,
                        model: Optional[Dict[str, Any]] = None,
                        crews_per_trade: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Full resource picture of one solved plan: demand, peak manpower, utilisation, conflicts."""
    cfg = cfg or RM.load_resource_constraints()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    demand = RM.build_demand_rows(tasks, cfg, rates, model)
    peak = RM.peak_manpower(tasks, cfg, rates, model)
    conflicts = detect_crew_conflicts(tasks, cfg, rates, model, crews_per_trade=crews_per_trade)
    interference = trade_interference(tasks, cfg, rates, model)

    by_trade: Dict[str, Dict[str, Any]] = {}
    for r in demand:
        b = by_trade.setdefault(r["trade"], dict(trade=r["trade"], instances=0, worker_days=0.0,
                                                 worker_hours=0.0, quantity_by_unit=defaultdict(float)))
        b["instances"] += 1
        b["worker_days"] = round(b["worker_days"] + float(r["worker_days"]), 4)
        b["worker_hours"] = round(b["worker_hours"] + float(r["worker_hours"]), 2)
        b["quantity_by_unit"][r["unit"]] = round(b["quantity_by_unit"][r["unit"]] + float(r["quantity"]), 4)
    for row in conflicts["by_trade"]:
        if row["trade"] in by_trade:
            by_trade[row["trade"]].update(
                peak_concurrent_crews=row["peak_concurrent_crews"],
                available_crews=row["available_crews"],
                crew_utilisation=row["crew_utilisation"],
                peak_concurrent_workers=row["peak_concurrent_workers"],
                status=row["status"])
    for b in by_trade.values():
        b["quantity_by_unit"] = dict(b["quantity_by_unit"])
        b.setdefault("status", RM.FEASIBLE)

    total_worker_days = round(sum(float(r["worker_days"]) for r in demand), 4)
    total_worker_hours = round(sum(float(r["worker_hours"]) for r in demand), 2)
    return dict(
        version=cfg.get("version"),
        demand=demand,
        demand_instance_count=len(demand),
        total_worker_days=total_worker_days,
        total_worker_hours=total_worker_hours,
        by_trade=[by_trade[k] for k in sorted(by_trade)],
        peak=peak,
        crew_conflicts=conflicts,
        trade_interference=interference,
        status=(RM.RESOURCE_CONSTRAINED if conflicts["conflict_count"] else RM.FEASIBLE),
        provisional_factors=sorted({f for r in demand if r["rate_status"] != "calibrated"
                                    for f in ("productivity_rate",)})
        + ["congestion_model(provisional)", "workfront_capacity(provisional)", "crew_pool(provisional)"],
    )


# ---------------------------------------------------------------------------
# 11/12. target-date analysis
# ---------------------------------------------------------------------------
def snapshot(tasks: Sequence[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    return {t.get("id"): dict(duration_days=int(t.get("duration_days", t.get("duration", 0)) or 0),
                              crew_size=(t.get("productivity") or {}).get("crew_size"),
                              workfront_efficiency=(t.get("productivity") or {}).get("workfront_efficiency"))
            for t in tasks}


def assert_baseline_unchanged(before: Dict[Any, Dict[str, Any]], after: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    diffs = []
    for t in after:
        b = before.get(t.get("id"))
        if b is None:
            diffs.append(dict(task_id=t.get("id"), issue="task not in baseline snapshot"))
            continue
        if int(t.get("duration_days", t.get("duration", 0)) or 0) != b["duration_days"]:
            diffs.append(dict(task_id=t.get("id"), field="duration_days",
                              before=b["duration_days"],
                              after=int(t.get("duration_days", t.get("duration", 0)) or 0)))
    return dict(checked=len(after), diffs=diffs,
                assertion="baseline schedule is never silently modified",
                assertion_pass=not diffs)


def _apply_crew(task: Dict[str, Any], workers: int, trade: str, cfg: Dict[str, Any],
                rates: Dict[str, Any], semantics: Dict[str, Any],
                shifts: float = 1.0, night: bool = False) -> Dict[str, Any]:
    """Recompute one task's duration at a new crew size, honouring congestion + V6.1.1 minimums."""
    from core.productivity import compute_activity_duration
    prod = task.get("productivity") or {}
    base_eff = task.get("resource_base_efficiency")
    if base_eff is None:
        base_eff = float(prod.get("workfront_efficiency", 1.0) or 1.0)
        task["resource_base_efficiency"] = base_eff
    eff = RM.resource_adjusted_efficiency(trade, workers, cfg, shifts=shifts, night=night,
                                          base_efficiency=float(base_eff))
    if eff.get("value") is None:
        return dict(applied=False, reason="INSUFFICIENT_DATA: 班次产出无证据")
    res = compute_activity_duration(task["activity_type"], float(prod.get("quantity", task.get("quantity"))),
                                    crew_size=int(workers), factors=prod.get("factors"), rates=rates,
                                    quantity_source=prod.get("quantity_source", "measured"),
                                    rate_level=prod.get("rate_level"), shifts=shifts,
                                    workfront_efficiency=eff["value"])
    from core import duration_semantics as DS
    minres = DS.resolve_min_for_instance(task["activity_type"],
                                         instance_scope=str(task.get("spatial_scope") or "project"),
                                         is_gate=False, cfg=semantics, rates=rates)
    applied_min = int(minres["applied_min_duration"] or 0)
    rounded = int(res["rounded_duration"])
    final = max(applied_min, rounded)
    previous = int(task.get("duration_days", task.get("duration", 0)) or 0)
    task["duration_days"] = final
    task["duration"] = final
    prod.update(crew_size=int(workers), workfront_efficiency=eff["value"],
                final_duration=final, rounded_duration=rounded,
                applied_min_duration=applied_min, min_duration_scope=minres["min_duration_scope"],
                raw_calculated_duration=res["raw_calculated_duration"],
                rounding_penalty_days=res["rounding_penalty_days"],
                minimum_duration_penalty_days=int(final - rounded),
                crew_model="v6_2_resource_optimised",
                congestion_factor=eff["congestion"]["crew_efficiency"],
                shift_multiplier=eff["shift"]["output_multiplier"])
    task.setdefault("resource_proposal", {})
    task["resource_proposal"].update(workers=int(workers), trade=trade,
                                     previous_duration_days=previous,
                                     new_duration_days=final,
                                     congestion_factor=eff["congestion"]["crew_efficiency"],
                                     effective_workers=round(int(workers) * eff["congestion"]["crew_efficiency"], 4),
                                     applied_min_duration=applied_min,
                                     min_duration_scope=minres["min_duration_scope"])
    return dict(applied=True, previous_duration_days=previous, new_duration_days=final,
                effective_workers=round(int(workers) * eff["congestion"]["crew_efficiency"], 4),
                applied_min_duration=applied_min, min_duration_scope=minres["min_duration_scope"])


def optimise_for_target_date(baseline_tasks: Sequence[Dict[str, Any]], start_date: str,
                             target_finish_date: str, cfg: Optional[Dict[str, Any]] = None,
                             rates: Optional[Dict[str, Any]] = None,
                             semantics: Optional[Dict[str, Any]] = None,
                             near_critical_float_days: Optional[int] = None,
                             max_growth_factor: Optional[float] = None,
                             shifts: float = 1.0, night: bool = False,
                             model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Target date → CPM → critical/near-critical → inverse resource sizing → re-solve.

    Never compresses every activity by an averaged amount. Only critical and near-critical
    production work receives additional crew, and only up to what the site can physically hold.
    """
    from core.solver_engine import solve_schedule
    from core.optimizer import analyze_schedule
    from core import duration_semantics as DS

    cfg = cfg or RM.load_resource_constraints()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    semantics = semantics or DS.load_duration_semantics()
    opt = cfg.get("optimisation") or {}
    threshold = int(near_critical_float_days if near_critical_float_days is not None
                    else opt.get("near_critical_float_days", 5))
    growth = float(max_growth_factor if max_growth_factor is not None
                   else opt.get("max_crew_growth_factor", 3.0))

    before_snapshot = snapshot(baseline_tasks)
    analysis_source = copy.deepcopy(list(baseline_tasks))
    solved0 = solve_schedule(analysis_source, start_date)
    finish0 = solved0["finish_date"]
    analysis = analyze_schedule(solved0["tasks"], start_date=start_date, verify=False, compliance_check=False)
    float_by_id = {f["id"]: f for f in (analysis.get("float") or {}).get("by_task", [])}
    gap = _days_between(target_finish_date, finish0) or 0

    proposal = copy.deepcopy(list(baseline_tasks))
    by_id = {t["id"]: t for t in proposal}
    rows: List[Dict[str, Any]] = []
    remaining = gap
    skipped_non_critical: List[Dict[str, Any]] = []

    candidates = []
    for t in solved0["tasks"]:
        if not is_production_task(t):
            continue
        f = float_by_id.get(t["id"]) or {}
        flt = f.get("float_days")
        on_chain = bool(f.get("on_driving_chain"))
        if flt is None and not on_chain:
            continue
        dur0 = int(t.get("duration_days", t.get("duration", 0)) or 0)
        # Critical = on the driving chain (calendar-boundary float can make a driving task
        # look like it has a few days of float). Near-critical = float within the threshold.
        if on_chain or (flt is not None and flt <= threshold):
            candidates.append((0 if on_chain else 1, float(flt or 0), -dur0, t["id"]))
        else:
            skipped_non_critical.append(dict(task_id=t["id"], name=t.get("name"), float_days=flt,
                                             reason=f"总浮时 {flt} 天 > 近关键阈值 {threshold} 天且不在驱动链上 → 不投入资源"))
    candidates.sort()

    for _chain_rank, flt, _neg_dur, tid in candidates:
        if remaining <= 0:
            break
        src = by_id.get(tid)
        if not src or not is_production_task(src):
            continue
        prod = src.get("productivity") or {}
        qty = float(prod.get("quantity", src.get("quantity", 0)) or 0)
        if qty <= 0:
            continue
        current = int(src.get("duration_days", src.get("duration", 0)) or 0)
        base_crew = int(prod.get("crew_size") or 1)
        trade = RM.trade_of(src.get("activity_type"), model)
        zone_area = src.get("zone_area_sqm")

        minres = DS.resolve_min_for_instance(src.get("activity_type"),
                                             instance_scope=str(src.get("spatial_scope") or "project"),
                                             is_gate=False, cfg=semantics, rates=rates)
        applied_min = int(minres["applied_min_duration"] or 0)
        if current <= applied_min:
            rows.append(dict(task_id=tid, name=src.get("name"), activity_type=src.get("activity_type"),
                             trade=trade, float_days=flt, baseline_duration_days=current,
                             proposed_workers=base_crew, new_duration_days=current,
                             saving_days=0, status=PHYSICAL_MINIMUM,
                             reason=f"已处于最短工期下限 {applied_min} 天（{minres['min_duration_scope']}）→ 加人无效"))
            continue

        wanted = max(applied_min, current - remaining)
        inv = reverse_crew_requirement(src["activity_type"], qty, wanted, trade=trade, rates=rates, cfg=cfg,
                                       factors=prod.get("factors"), rate_level=prod.get("rate_level"),
                                       shifts=shifts, night=night,
                                       base_efficiency=float(src.get("resource_base_efficiency")
                                                             or prod.get("workfront_efficiency", 1.0) or 1.0),
                                       zone_area=zone_area,
                                       instance_scope=str(src.get("spatial_scope") or "project"),
                                       semantics=semantics, model=model)
        target_workers = int(inv.get("feasible_workers") or base_crew)
        capped = min(target_workers, max(base_crew, int(math.ceil(base_crew * growth))))
        if capped <= base_crew:
            rows.append(dict(task_id=tid, name=src.get("name"), activity_type=src.get("activity_type"),
                             trade=trade, float_days=flt, baseline_duration_days=current,
                             proposed_workers=base_crew, new_duration_days=current, saving_days=0,
                             status=(inv.get("status") or RESOURCE_CONSTRAINED),
                             reason=(f"资源上限（工作面容量 {inv.get('workfront_capacity_workers')} 人 / "
                                     f"实用上限 {inv.get('max_practical_workers')} 人 / 增长上限 {growth:g}×）"
                                     "已不允许增加人数 → 不可压缩")))
            continue

        applied = _apply_crew(src, capped, trade, cfg, rates, semantics, shifts=shifts, night=night)
        if not applied.get("applied"):
            continue
        new_dur = int(applied["new_duration_days"])
        saving = current - new_dur
        if saving > 0:
            remaining -= saving
        rows.append(dict(task_id=tid, name=src.get("name"), activity_type=src.get("activity_type"),
                         trade=trade, float_days=flt, zone=src.get("zone"),
                         baseline_duration_days=current, baseline_workers=base_crew,
                         proposed_workers=capped, effective_workers=applied["effective_workers"],
                         congestion_factor=prod.get("congestion_factor"),
                         new_duration_days=new_dur, saving_days=saving,
                         applied_min_duration=applied["applied_min_duration"],
                         min_duration_scope=applied["min_duration_scope"],
                         theoretical_workers=inv.get("theoretical_workers"),
                         recommended_workers=inv.get("recommended_workers"),
                         workfront_capacity_workers=inv.get("workfront_capacity_workers"),
                         status=(FEASIBLE if saving > 0 else PHYSICAL_MINIMUM),
                         reason=("反算目标工期 " + f"{wanted} 天 → 需要 {inv.get('recommended_workers')} 人，"
                                 f"可行 {capped} 人（拥挤后有效 {applied['effective_workers']:g} 人），"
                                 f"工期 {current} → {new_dur} 天" if saving > 0 else
                                 f"最短工期下限 {applied['applied_min_duration']} 天限制，加人后工期无变化")))

    solved1 = solve_schedule(proposal, start_date)
    finish1 = solved1["finish_date"]
    saved = _days_between(finish1, finish0) or 0

    # Where does the remaining gap actually live? A target date usually cannot be bought
    # with labour when the critical chain is design, statutory review or a venting period.
    by_id0 = {t["id"]: t for t in solved0["tasks"]}
    _breakdown = dict(gate_days=0, fixed_template_days=0, production_crashable_days=0,
                      mobilisation_days=0, milestone_days=0)
    _detail = {k: [] for k in _breakdown}
    for n in analysis["critical_path"]["chain"]:
        t = by_id0.get(n["id"]) or {}
        d = int(n.get("duration_days", 0) or 0)
        if t.get("inspection_gate"):
            key = "gate_days"
        elif t.get("is_mobilisation"):
            key = "mobilisation_days"
        elif t.get("milestone") or int(t.get("duration_days", 0) or 0) == 0:
            key = "milestone_days"
        elif is_production_task(t):
            key = "production_crashable_days"
        else:
            key = "fixed_template_days"
        _breakdown[key] += d
        _detail[key].append(dict(task_id=n["id"], name=t.get("name"), duration_days=d))
    gap_breakdown = dict(_breakdown, chain_task_count=len(analysis["critical_path"]["chain"]),
                         note=("关键链上能被人力压缩的只有 production_crashable_days；"
                               "设计/图审/法定审批/散味 SOP 与检查门不是产能问题，加人无效。"),
                         detail=_detail)

    return dict(
        start_date=start_date, target_finish_date=target_finish_date,
        baseline_finish=finish0, proposal_finish=finish1,
        gap_days=gap, achieved_saving_days=saved, residual_gap_days=max(0, gap - saved),
        near_critical_float_days=threshold, max_crew_growth_factor=growth,
        critical_chain_gap_breakdown=gap_breakdown,
        critical_task_count=len(analysis["critical_path"]["chain"]),
        critical_chain_ids=[n["id"] for n in analysis["critical_path"]["chain"]],
        activities=[r for r in rows if r.get("saving_days", 0) > 0],
        activities_attempted=rows,
        skipped_non_critical=skipped_non_critical,
        baseline_unchanged=assert_baseline_unchanged(before_snapshot, list(baseline_tasks)),
        comparison=dict(baseline_finish=finish0, proposal_finish=finish1,
                        finish_date_saved_days=saved,
                        assertion="proposal_finish <= baseline_finish",
                        assertion_pass=saved >= 0),
        tasks=proposal,
        status=(FEASIBLE if gap - saved <= 0 else
                (PHYSICAL_MINIMUM if all(r.get("status") == PHYSICAL_MINIMUM
                                          for r in rows if r.get("saving_days", 0) == 0) else RESOURCE_CONSTRAINED)),
    )
