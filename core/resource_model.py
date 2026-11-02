"""V6.2 resource model — crews, congestion, shifts, workfront capacity, crew pool.

The question this module answers
--------------------------------
V6.0 answered "what must happen before what?".
V6.1 answered "where can work happen at the same time?".
V6.2 answers "do we actually have enough people and physical room to execute that plan?".

Hard rules
----------
1. This module owns NO productivity numbers. Every rate, factor and minimum comes from
   config/productivity_rates.json through core.productivity. There is no second rate
   library, and no factor is invented at call time.
2. Crew is NOT linear productivity: effective output = rate x workers x congestion(workers).
   The congestion curve comes from config/resource_constraints.json only.
3. Shifts are not linear either: 2 shifts != 2x output unless the config says so, and a
   night shift without evidence returns INSUFFICIENT_DATA instead of a guess.
4. Every factor without evidence is reported as `provisional` with its basis, so a planner
   can see exactly which numbers are engineering judgement and which are measured.
5. Nothing here writes to config/productivity_rates.json, config/spatial_model.json,
   config/dependency_ontology.json or config/duration_semantics.json.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOURCE_CONSTRAINTS_PATH = os.path.join(BASE_DIR, "config", "resource_constraints.json")

STATUS_BASELINE = "baseline"
STATUS_PROVISIONAL = "provisional"
STATUS_INSUFFICIENT_DATA = "insufficient_data"

FEASIBLE = "FEASIBLE"
RESOURCE_CONSTRAINED = "RESOURCE_CONSTRAINED"
WORKFRONT_CONSTRAINED = "WORKFRONT_CONSTRAINED"
PHYSICAL_MINIMUM = "PHYSICAL_MINIMUM"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

LEVELS_INTERFERENCE = ("low", "medium", "high", "blocked")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def load_resource_constraints(path: Optional[str] = None) -> Dict[str, Any]:
    with open(path or RESOURCE_CONSTRAINTS_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    validate_resource_constraints(cfg)
    # V6.2.1: resource inputs are additionally checked for provenance integrity, so an
    # assumption can never be loaded as if it were a contractor commitment.
    from core.resource_governance import validate_governance
    validate_governance(cfg)
    return cfg


def validate_resource_constraints(cfg: Dict[str, Any]) -> None:
    """Reject a malformed resource config instead of silently defaulting."""
    if not isinstance(cfg, dict):
        raise ValueError("resource_constraints must be a JSON object")
    if (cfg.get("provenance") or {}).get("productivity_source") != "config/productivity_rates.json":
        raise ValueError("resource_constraints.provenance.productivity_source must stay 'config/productivity_rates.json'")

    cong = cfg.get("congestion_model") or {}
    if not cong:
        raise ValueError("resource_constraints: congestion_model is mandatory")
    for name, spec in [("default", cong.get("default") or {})] + list((cong.get("by_trade") or {}).items()):
        table = (spec or {}).get("crew_efficiency_table")
        if not table:
            raise ValueError(f"congestion_model '{name}': crew_efficiency_table is mandatory")
        vals = {int(k): float(v) for k, v in table.items()}
        if 1 not in vals or abs(vals[1] - 1.0) > 1e-9:
            raise ValueError(f"congestion_model '{name}': table[1] must be exactly 1.0 (continuity with the rate basis)")
        ordered = [vals[k] for k in sorted(vals)]
        if any(b > a + 1e-9 for a, b in zip(ordered, ordered[1:])):
            raise ValueError(f"congestion_model '{name}': efficiency must be non-increasing as crew grows")
        if any(not (0 < v <= 1) for v in vals.values()):
            raise ValueError(f"congestion_model '{name}': efficiency must be in (0,1]")
        if not (spec or {}).get("basis"):
            raise ValueError(f"congestion_model '{name}': 'basis' is mandatory")

    shifts = (cfg.get("shift_model") or {}).get("shifts") or {}
    for key, spec in shifts.items():
        if float(spec.get("output_multiplier", 0)) <= 0:
            raise ValueError(f"shift_model.shifts['{key}']: output_multiplier must be > 0")
        if float(spec.get("output_multiplier", 0)) > float(key):
            raise ValueError(f"shift_model.shifts['{key}']: output_multiplier must not exceed the shift count "
                             "(2 shifts cannot out-produce 2x)")
        if not spec.get("basis"):
            raise ValueError(f"shift_model.shifts['{key}']: 'basis' is mandatory")
    if (cfg.get("shift_model") or {}).get("night_shift", {}).get("default_multiplier") is not None:
        raise ValueError("shift_model.night_shift.default_multiplier must stay null (night output needs evidence)")

    cap = cfg.get("workfront_capacity") or {}
    for name, spec in [("default", cap.get("default") or {})] + list((cap.get("by_trade") or {}).items()):
        if int((spec or {}).get("max_parallel_workfronts", 0)) < 1:
            raise ValueError(f"workfront_capacity '{name}': max_parallel_workfronts must be >= 1")
        if int((spec or {}).get("max_workers_per_workfront", 0)) < 1:
            raise ValueError(f"workfront_capacity '{name}': max_workers_per_workfront must be >= 1")
        if not (spec or {}).get("basis"):
            raise ValueError(f"workfront_capacity '{name}': 'basis' is mandatory")

    matrix = (cfg.get("trade_interference") or {}).get("matrix") or {}
    for key, spec in matrix.items():
        if "|" not in key:
            raise ValueError(f"trade_interference key '{key}' must be 'tradeA|tradeB'")
        if spec.get("level") not in LEVELS_INTERFERENCE:
            raise ValueError(f"trade_interference '{key}': level must be one of {list(LEVELS_INTERFERENCE)}")
        if not spec.get("reason"):
            raise ValueError(f"trade_interference '{key}': 'reason' is mandatory")


# ---------------------------------------------------------------------------
# trades
# ---------------------------------------------------------------------------
def trade_of(activity_type: Optional[str], model: Optional[Dict[str, Any]] = None) -> str:
    from core.workfront_model import trade_of as _trade_of
    return _trade_of(activity_type, model)


def _spec_for(trade: Optional[str], section: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    by_trade = section.get("by_trade") or {}
    if trade and trade in by_trade:
        return by_trade[trade], f"by_trade.{trade}"
    return (section.get("default") or {}), "default"


# ---------------------------------------------------------------------------
# congestion: crew is not linear productivity
# ---------------------------------------------------------------------------
def crew_efficiency_table(trade: Optional[str], cfg: Optional[Dict[str, Any]] = None) -> Dict[int, float]:
    cfg = cfg or load_resource_constraints()
    spec, _ = _spec_for(trade, cfg["congestion_model"], cfg)
    return {int(k): float(v) for k, v in (spec.get("crew_efficiency_table") or {}).items()}


def crew_efficiency(trade: Optional[str], workers: int, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Deterministic congestion factor for one crew size.

    Unlisted crew sizes take the NEXT LARGER key's (lower) efficiency — never a more
    favourable one — and sizes beyond the table clamp at the last value. No interpolation,
    no LLM judgement.
    """
    cfg = cfg or load_resource_constraints()
    cong = cfg["congestion_model"]
    spec, source = _spec_for(trade, cong, cfg)
    table = crew_efficiency_table(trade, cfg)
    n = max(1, int(workers))
    if n in table:
        eff, rule = table[n], "exact_table_entry"
    else:
        larger = [k for k in sorted(table) if k > n]
        if larger:
            eff, rule = table[larger[0]], f"step_down_to_next_declared_crew_size({larger[0]})"
        else:
            eff, rule = table[max(table)], "clamp_at_last_declared_crew_size"
    floor = float(spec.get("min_efficiency", 0.0) or 0.0)
    if floor and eff < floor:
        eff, rule = floor, rule + "+min_efficiency_floor"
    return dict(workers=n, crew_efficiency=round(float(eff), 6), rule=rule,
                basis=spec.get("basis"), status=spec.get("status", STATUS_PROVISIONAL),
                confirmed_by=spec.get("confirmed_by"), source=f"congestion_model.{source}",
                effective_workers=round(n * float(eff), 4))


def congestion_loss_workers(trade: Optional[str], workers: int, cfg: Optional[Dict[str, Any]] = None) -> float:
    """How many worker-equivalents are lost to congestion at this crew size."""
    res = crew_efficiency(trade, workers, cfg)
    return round(float(workers) - res["effective_workers"], 4)


def assert_sublinear(cfg: Optional[Dict[str, Any]] = None, trade: Optional[str] = None) -> Dict[str, Any]:
    """Prove the model is sub-linear: output(2n) < 2 x output(n)."""
    cfg = cfg or load_resource_constraints()
    table = crew_efficiency_table(trade, cfg)
    rows = []
    for n in sorted(table):
        rows.append(dict(workers=n, efficiency=table[n], effective_workers=round(n * table[n], 4)))
    # Marginal output per added worker must be non-increasing (concavity = diminishing returns).
    slopes = []
    for a, b in zip(rows, rows[1:]):
        slopes.append(round((b["effective_workers"] - a["effective_workers"]) / (b["workers"] - a["workers"]), 6))
    for i in range(len(rows)):
        rows[i] = dict(rows[i], marginal_output_per_worker=(slopes[i - 1] if i else None))
    violations = [dict(between=(rows[i]["workers"], rows[i + 1]["workers"]),
                       slope=(slopes[i - 1], slopes[i]))
                  for i in range(1, len(slopes)) if slopes[i] > slopes[i - 1] + 1e-9]
    double_checks = []
    for a in rows:
        for b in rows:
            if b["workers"] == 2 * a["workers"] and a["workers"] >= 2:
                double_checks.append(dict(base=a["workers"], doubled=b["workers"],
                                          base_output=a["effective_workers"],
                                          doubled_output=b["effective_workers"],
                                          strictly_less_than_double=b["effective_workers"] < 2 * a["effective_workers"]))
    bad_doubling = [d for d in double_checks if not d["strictly_less_than_double"]]
    return dict(rows=rows, slopes=slopes, violations=violations,
                doubling_checks=double_checks, doubling_violations=bad_doubling,
                assertion="marginal output per added worker is non-increasing (crew is not linear productivity)",
                assertion_pass=not violations and not bad_doubling)


# ---------------------------------------------------------------------------
# shifts
# ---------------------------------------------------------------------------
def shift_factor(shifts: float, night: bool = False, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Output multiplier for `shifts` shifts per day. 2 shifts is NOT 2x by default."""
    cfg = cfg or load_resource_constraints()
    sm = cfg.get("shift_model") or {}
    hours = float(sm.get("hours_per_shift", 8) or 8)
    if night:
        night_cfg = sm.get("night_shift") or {}
        if night_cfg.get("default_multiplier") is None:
            return dict(shifts=shifts, night=True, output_multiplier=None, derate_factor=None,
                        status=STATUS_INSUFFICIENT_DATA, basis=night_cfg.get("rule", ""),
                        constraints=night_cfg.get("constraints") or [],
                        note="夜班产出无证据 → 返回 INSUFFICIENT_DATA，不得默认夜班翻倍。")
    key = str(int(shifts)) if float(shifts).is_integer() else str(shifts)
    spec = (sm.get("shifts") or {}).get(key)
    if spec is None:
        return dict(shifts=shifts, night=False, output_multiplier=None, derate_factor=None,
                    status=STATUS_INSUFFICIENT_DATA,
                    basis=f"config 未声明 {shifts} 班的产出系数；不得线性外推。",
                    constraints=[], note="未声明的班次返回 INSUFFICIENT_DATA。")
    mult = float(spec["output_multiplier"])
    return dict(shifts=shifts, night=False, output_multiplier=round(mult, 6),
                derate_factor=round(mult / float(shifts), 6),
                hours_per_shift=hours,
                status=spec.get("status", STATUS_PROVISIONAL),
                basis=spec.get("basis"), confirmed_by=spec.get("confirmed_by"),
                constraints=spec.get("constraints") or [],
                note=("班次产出 = " f"{mult:g}×（折减 {mult / float(shifts):g} 施加于名义 {shifts:g}×）"))


# ---------------------------------------------------------------------------
# workfront / zone physical capacity
# ---------------------------------------------------------------------------
def workfront_capacity(trade: Optional[str], zone_area: Optional[float], cfg: Optional[Dict[str, Any]] = None,
                       area_per_front: Optional[float] = None) -> Dict[str, Any]:
    """How many workers of this trade can physically stand in one zone at once."""
    cfg = cfg or load_resource_constraints()
    cap = cfg.get("workfront_capacity") or {}
    spec, source = _spec_for(trade, cap, cfg)
    per_front = float(area_per_front if area_per_front else cap.get("zone_area_per_workfront_default", 500) or 500)
    max_fronts = int(spec.get("max_parallel_workfronts", 1))
    per_wf = int(spec.get("max_workers_per_workfront", 1))
    max_in_zone = int(spec.get("max_workers_in_zone", max_fronts * per_wf))
    if zone_area:
        fronts_by_area = max(1, int(math.ceil(float(zone_area) / per_front)))
    else:
        fronts_by_area = max_fronts
    fronts = min(fronts_by_area, max_fronts)
    raw_capacity = fronts * per_wf
    capacity = min(raw_capacity, max_in_zone)
    return dict(trade=trade, zone_area=zone_area, area_per_workfront=per_front,
                fronts_by_area=fronts_by_area, max_parallel_workfronts=max_fronts,
                usable_workfronts=fronts, max_workers_per_workfront=per_wf,
                max_workers_in_zone=max_in_zone, raw_capacity_workers=raw_capacity,
                capacity_workers=int(capacity),
                binding=("max_workers_in_zone" if capacity < raw_capacity
                         else ("max_parallel_workfronts" if fronts < fronts_by_area else "zone_area")),
                formula=(("min(ceil(" + (f"{zone_area:g}" if zone_area else "—") + f"/{per_front:g})"
                          + (f"={fronts_by_area}" if zone_area else "")
                          + f", {max_fronts})={fronts} 个工作面 × "
                          f"{per_wf} 人/工作面 = {raw_capacity} 人，再受分区上限 {max_in_zone} 人约束 → {capacity} 人")),
                status=spec.get("status", STATUS_PROVISIONAL), basis=spec.get("basis"),
                confirmed_by=spec.get("confirmed_by"), source=f"workfront_capacity.{source}")


# ---------------------------------------------------------------------------
# crew pool availability
# ---------------------------------------------------------------------------
def crew_pool(trade: Optional[str], cfg: Optional[Dict[str, Any]] = None,
              rates: Optional[Dict[str, Any]] = None,
              activity_type: Optional[str] = None) -> Dict[str, Any]:
    """Available crews of one trade. Defaults preserve the V6.1 single-crew assumption and
    say so explicitly — an undeclared pool is a provisional assumption, not a commitment."""
    cfg = cfg or load_resource_constraints()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    pool_cfg = cfg.get("crew_pool") or {}
    spec, source = _spec_for(trade, pool_cfg, cfg)
    entry = (rates.get("activity_types") or {}).get(activity_type or "") or {}
    default_crew = int(entry.get("default_crew_size", 1) or 1)
    # A crew is at most the number of workers one workfront can hold — that number already
    # exists in the rate library, so it is referenced, not invented.
    per_front = int(((entry.get("crew_scaling") or {}) or {}).get("max_workers_per_front", 0) or 0)
    cap = workfront_capacity(trade, None, cfg)
    # Nominal physical ceiling of one zone, used when no zone area is known.
    nominal_zone_capacity = int(cap["max_workers_per_workfront"] * cap["max_parallel_workfronts"])
    min_practical = int(spec.get("min_practical_workers") or default_crew)
    workers_per_crew = int(spec.get("workers_per_crew") or (per_front or default_crew))
    available_crews = int(spec.get("available_crews", 1) or 1)
    max_practical = int(spec.get("max_practical_workers")
                        or max(workers_per_crew, available_crews * workers_per_crew))
    return dict(trade=trade, available_crews=available_crews, workers_per_crew=workers_per_crew,
                available_workers=available_crews * workers_per_crew,
                nominal_zone_capacity_workers=nominal_zone_capacity,
                min_practical_workers=min_practical, max_practical_workers=max_practical,
                shifts=spec.get("shifts", 1), hours_per_shift=spec.get("hours_per_shift", 8),
                assigned_floors=spec.get("assigned_floors"), assigned_zones=spec.get("assigned_zones"),
                available_from=spec.get("available_from"), available_until=spec.get("available_until"),
                crews=[dict(crew_id=f"{trade or 'general'}-C{i + 1:02d}", workers=workers_per_crew,
                            assigned_floors=spec.get("assigned_floors"), assigned_zones=spec.get("assigned_zones"),
                            shifts=spec.get("shifts", 1), hours_per_shift=spec.get("hours_per_shift", 8))
                       for i in range(available_crews)],
                status=spec.get("status", STATUS_PROVISIONAL), confirmed_by=spec.get("confirmed_by"),
                source_type=spec.get("source_type"), basis=spec.get("basis"),
                calibration_status=spec.get("calibration_status"),
                availability_source=pool_cfg.get("availability_source_default"),
                source=f"crew_pool.{source}",
                note=("未显式声明班组池时沿用 V6.1 单班组流水假设（available_crews=1）；"
                      "这是规划假设，不是承包商的资源承诺。"))


# ---------------------------------------------------------------------------
# demand: worker-days from the ONE productivity source
# ---------------------------------------------------------------------------
def worker_days_required(activity_type: str, quantity: float, rates: Optional[Dict[str, Any]] = None,
                         factors: Optional[Dict[str, Any]] = None, rate_level: Optional[str] = None,
                         base_efficiency: float = 1.0) -> Dict[str, Any]:
    """Worker-days at unit congestion and single shift, straight from the rate library.

    worker_days = quantity x PI(factors) / (rate x workfront_efficiency)
    Duration follows from the same library: raw = worker_days / (workers x congestion x shift_mult).
    """
    from core.productivity import compute_activity_duration
    res = compute_activity_duration(activity_type, quantity, crew_size=1, factors=factors, rates=rates,
                                    quantity_source="measured", rate_level=rate_level,
                                    shifts=1.0, workfront_efficiency=base_efficiency)
    return dict(activity_type=activity_type, quantity=float(quantity), unit=res["unit"],
                productivity_rate=res["productivity_rate"], rate_level=res["rate_level"],
                rate_table=res["rate_table"], rate_status=res["rate_status"],
                evidence_level=res["evidence_level"], confidence=res["confidence"],
                factors=res["factors"], factor_labels=res["factor_labels"],
                factor_product=res["factor_product"],
                worker_days=round(float(res["calculated_duration"]), 6),
                worker_hours=round(float(res["calculated_duration"]) * 8.0, 4),
                scope_id=res.get("scope_id"),
                formula=(f"{float(quantity):g} {res['unit']} × {res['factor_product']:g} 修正 ÷ "
                         f"({res['productivity_rate']:g} {res['unit']}/工日 × {base_efficiency:g} 效率) = "
                         f"{res['calculated_duration']:g} 工日"),
                productivity_source="config/productivity_rates.json (via core.productivity)")


def duration_for_workers(worker_days: float, trade: Optional[str], workers: int, cfg: Optional[Dict[str, Any]] = None,
                         shifts: float = 1.0, night: bool = False) -> Dict[str, Any]:
    """Raw (un-rounded, un-floored) duration for a given crew size, congestion and shift pattern."""
    cfg = cfg or load_resource_constraints()
    n = max(1, int(workers))
    cong = crew_efficiency(trade, n, cfg)
    sf = shift_factor(shifts, night=night, cfg=cfg)
    if sf["output_multiplier"] is None:
        return dict(workers=n, trade=trade, raw_duration=None, status=STATUS_INSUFFICIENT_DATA,
                    crew_efficiency=cong, shift=sf,
                    note="班次产出无证据 → 无法给出工期（INSUFFICIENT_DATA）。")
    effective_output = n * cong["crew_efficiency"] * sf["output_multiplier"]
    raw = float(worker_days) / effective_output if effective_output > 0 else float("inf")
    return dict(workers=n, trade=trade, worker_days=round(float(worker_days), 6),
                crew_efficiency=cong, shift=sf,
                effective_workers=round(n * cong["crew_efficiency"], 4),
                effective_output_per_day=round(effective_output, 4),
                raw_duration=round(raw, 6), rounded_duration=int(math.ceil(round(raw, 6))),
                status="ok",
                formula=(f"{worker_days:g} 工日 ÷ (人数 {n} × 拥挤系数 {cong['crew_efficiency']:g} "
                         f"× 班次系数 {sf['output_multiplier']:g}) = {raw:g} 天"))


def resource_adjusted_efficiency(trade: Optional[str], workers: int, cfg: Optional[Dict[str, Any]] = None,
                                 shifts: float = 1.0, night: bool = False,
                                 base_efficiency: float = 1.0) -> Dict[str, Any]:
    """The single `workfront_efficiency` value that folds congestion + shift derate into the
    existing productivity formula, so V6.2 never forks the duration math."""
    cfg = cfg or load_resource_constraints()
    cong = crew_efficiency(trade, workers, cfg)
    sf = shift_factor(shifts, night=night, cfg=cfg)
    if sf["output_multiplier"] is None:
        return dict(value=None, status=STATUS_INSUFFICIENT_DATA, congestion=cong, shift=sf)
    value = float(base_efficiency) * cong["crew_efficiency"] * float(sf["derate_factor"])
    return dict(value=round(value, 6), base_efficiency=float(base_efficiency),
                congestion=cong, shift=sf, status="ok",
                formula=f"{base_efficiency:g} × 拥挤 {cong['crew_efficiency']:g} × 班次折减 {sf['derate_factor']:g}")


# ---------------------------------------------------------------------------
# per-task demand
# ---------------------------------------------------------------------------
def task_demand(task: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None,
                rates: Optional[Dict[str, Any]] = None,
                model: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Resource demand of one production task. Returns None for gates / non-production tasks."""
    activity = task.get("activity_type")
    if not activity or task.get("inspection_gate") or task.get("is_mobilisation") or task.get("milestone"):
        return None
    if task.get("duration_method") != "productivity_formula":
        return None
    prod = task.get("productivity") or {}
    qty = float(prod.get("quantity", task.get("quantity", 0)) or 0)
    if qty <= 0:
        return None
    cfg = cfg or load_resource_constraints()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    trade = trade_of(activity, model)
    workers = int(prod.get("crew_size", task.get("crew_size", 1)) or 1)
    dm = worker_days_required(activity, qty, rates=rates, factors=prod.get("factors"),
                              rate_level=prod.get("rate_level"),
                              base_efficiency=float(prod.get("workfront_efficiency", 1.0) or 1.0))
    cong = crew_efficiency(trade, workers, cfg)
    return dict(task_id=task.get("id"), name=task.get("name"), activity_type=activity, trade=trade,
                zone=task.get("zone"), zone_label=task.get("zone_label"),
                zone_area_sqm=task.get("zone_area_sqm"), quantity=qty, unit=dm["unit"],
                worker_days=dm["worker_days"], worker_hours=dm["worker_hours"],
                workers=workers, crew_efficiency=cong["crew_efficiency"],
                effective_workers=cong["effective_workers"],
                duration_days=int(task.get("duration_days", task.get("duration", 0)) or 0),
                rate_status=dm["rate_status"], evidence_level=dm["evidence_level"],
                productivity_rate=dm["productivity_rate"])


def build_demand_rows(tasks: Sequence[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None,
                      rates: Optional[Dict[str, Any]] = None,
                      model: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    cfg = cfg or load_resource_constraints()
    rows = []
    for t in tasks:
        row = task_demand(t, cfg=cfg, rates=rates, model=model)
        if row:
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------
def _to_date(value: Any):
    from datetime import date, datetime
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def peak_manpower(tasks: Sequence[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None,
                  rates: Optional[Dict[str, Any]] = None,
                  model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Peak concurrent headcount, overall and per trade, from solved start/finish dates."""
    cfg = cfg or load_resource_constraints()
    by_id = tasks_by_id(tasks)
    intervals: List[Tuple[Any, Any, str, int, Any]] = []
    for r in build_demand_rows(tasks, cfg, rates, model):
        t = by_id.get(r["task_id"]) or {}
        s, f = _to_date(t.get("start")), _to_date(t.get("finish"))
        if not s or not f or f < s:
            continue
        intervals.append((s, f, r["trade"], int(r["workers"]), r["task_id"]))
    if not intervals:
        return dict(peak_workers=0, peak_date=None, by_trade={}, samples=0)
    dates = sorted({d for s, f, *_ in intervals for d in (s, f)})
    best = (0, None)
    best_by_trade: Dict[str, int] = {}
    for d in dates:
        total = sum(w for s, f, _tr, w, _id in intervals if s <= d <= f)
        if total > best[0]:
            best = (total, d.isoformat())
        per: Dict[str, int] = {}
        for s, f, tr, w, _id in intervals:
            if s <= d <= f:
                per[tr] = per.get(tr, 0) + w
        for tr, v in per.items():
            best_by_trade[tr] = max(best_by_trade.get(tr, 0), v)
    return dict(peak_workers=best[0], peak_date=best[1], by_trade=best_by_trade,
                samples=len(intervals))


def tasks_by_id(tasks: Sequence[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    return {t.get("id"): t for t in tasks}
