# -*- coding: utf-8 -*-
"""
core/productivity.py — quantity → productivity → duration

Scope (V5-A): `suspended_ceiling`, `partition_framing`, `flooring`, `painting` — every
activity type declared in config/productivity_rates.json. Everything else keeps the
hard-coded template duration untouched. The two paths coexist via an activity-type switch:

  * task has `activity_type` (known in the rate library) + `quantity`
        → duration is computed here and the explainable breakdown is attached
          as task["productivity"], task["duration_method"] = "productivity_formula"
  * task has no `activity_type`
        → not touched (template / calibration duration stands)

Formula (integer workdays, per SKILL.md):
    rate                = rates_per_worker_day[rate_level]      (low | typical | high)
    daily_output        = rate × crew_size
    base_duration       = quantity / daily_output
    calculated_duration = base_duration × Π factors
    final_duration      = max(min_duration_days, ceil(calculated_duration))

V6.1.1 duration semantics (see config/duration_semantics.json, core/duration_semantics.py):
  * `target_days` in crew_scaling is a CREW SIZING HORIZON ONLY. It is not a duration
    cap, not a guaranteed duration and not a per-zone minimum. Duration may be — and
    frequently is — larger or smaller than target_days.
  * `min_duration_days` here is used with scope LEGACY_UNSPECIFIED. V6.1.1 resolves the
    real scope (PROJECT / FLOOR / ZONE / MOBILISATION / INSPECTION_CYCLE) per spatial
    instance and never multiplies a project-level minimum by the zone count.
  * Every computed duration carries raw_calculated_duration / rounded_duration /
    applied_min_duration / min_duration_scope / rounding_penalty_days /
    minimum_duration_penalty_days so discretisation inflation stays visible.

Rates, rate-level tables and factor tables live only in config/productivity_rates.json
(single source, same rule as holidays / city permits). No live lookups, and no task-level
rate override: a task may choose *which* configured level applies (`rate_level`) but can
never supply its own rate — an LLM or a caller must not invent productivity or durations.

Quantity precedence (Quantity Engine v0 hook):
    1. task["quantity"]            measured BOQ / takeoff           → quantity_source "measured"
    2. derived_quantities[key]     core.quantity_engine output, looked up via the rate
                                   entry's `quantity_key` (e.g. ceiling_area)
                                                                    → quantity_source "derived_from_area"
    3. legacy bridge ratio         gross area × quantity_from_area_ratio (tag_legacy_pilot_tasks only)
                                                                    → quantity_source "derived_from_area"
"""
import os
import json
import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RATES_PATH = os.path.join(BASE_DIR, "config", "productivity_rates.json")

METHOD_FORMULA = "productivity_formula"
METHOD_TEMPLATE = "template"

QTY_MEASURED = "measured"
QTY_DERIVED = "derived_from_area"

_DEFAULT_LEVELS = ["low", "medium", "high"]
_DEFAULT_RATE_LEVELS = ["low", "typical", "high"]
_DEFAULT_RATE_LEVEL = "typical"

# Task keys that would smuggle a rate past the config. They are ignored with a warning.
_FORBIDDEN_TASK_RATE_KEYS = ("productivity_rate", "rate_per_worker_day", "rates_per_worker_day", "daily_output")


def _rate_levels(rates: Dict[str, Any]) -> List[str]:
    return list(rates.get("rate_levels") or _DEFAULT_RATE_LEVELS)


def _default_rate_level(rates: Dict[str, Any]) -> str:
    return rates.get("default_rate_level") or _DEFAULT_RATE_LEVEL


def _rate_table(entry: Dict[str, Any], levels: Sequence[str], name: str) -> Dict[str, float]:
    """Normalise one activity's rate spec into {level: rate}.

    Preferred form is `rates_per_worker_day: {low, typical, high}`. A legacy scalar
    `rate_per_worker_day` is accepted and used for every level (no range information).
    """
    if "rates_per_worker_day" in entry:
        raw = entry["rates_per_worker_day"]
        if not isinstance(raw, dict):
            raise ValueError(f"{name}: rates_per_worker_day must be a map of level -> rate")
        missing = [lv for lv in levels if lv not in raw]
        if missing:
            raise ValueError(f"{name}: rates_per_worker_day missing level(s) {missing}; required {list(levels)}")
        table = {lv: float(raw[lv]) for lv in levels}
    elif "rate_per_worker_day" in entry:
        table = {lv: float(entry["rate_per_worker_day"]) for lv in levels}
    else:
        raise ValueError(f"{name}: no rates_per_worker_day / rate_per_worker_day in rate library")
    for lv, r in table.items():
        if not (math.isfinite(r) and r > 0):
            raise ValueError(f"{name}: rate for level '{lv}' must be > 0, got {r}")
    ordered = [table[lv] for lv in levels]
    if any(a > b for a, b in zip(ordered, ordered[1:])):
        raise ValueError(f"{name}: rates must be non-decreasing across levels {list(levels)}, got {ordered}")
    return table


def validate_rate_library(rates: Dict[str, Any]) -> None:
    """Structural check of the whole library. Raises ValueError on the first defect."""
    if "activity_types" not in rates or not isinstance(rates["activity_types"], dict):
        raise ValueError("productivity rates lack 'activity_types' map")
    levels = _rate_levels(rates)
    if _default_rate_level(rates) not in levels:
        raise ValueError(f"default_rate_level '{_default_rate_level(rates)}' not in rate_levels {levels}")
    for name, entry in rates["activity_types"].items():
        for k in ("unit", "default_crew_size", "min_duration_days", "factors"):
            if k not in entry:
                raise ValueError(f"{name}: missing '{k}'")
        _rate_table(entry, levels, name)
        if int(entry["default_crew_size"]) < 1:
            raise ValueError(f"{name}: default_crew_size must be >= 1")
        if int(entry["min_duration_days"]) < 1:
            raise ValueError(f"{name}: min_duration_days must be >= 1")
        for fname, table in entry["factors"].items():
            if not isinstance(table, dict) or not table:
                raise ValueError(f"{name}: factor table '{fname}' must be a non-empty map")
            for key, m in table.items():
                if not (float(m) > 0):
                    raise ValueError(f"{name}: factor {fname}.{key} must be > 0")
        bridge = entry.get("legacy_template_bridge")
        if bridge is not None:
            if not bridge.get("match_keywords"):
                raise ValueError(f"{name}: legacy_template_bridge needs match_keywords")
            if not (float(bridge.get("quantity_from_area_ratio", 1.0)) > 0):
                raise ValueError(f"{name}: legacy_template_bridge.quantity_from_area_ratio must be > 0")
            for fname, key in (bridge.get("factors") or {}).items():
                if fname not in entry["factors"] or key not in entry["factors"][fname]:
                    raise ValueError(f"{name}: legacy_template_bridge.factors {fname}={key!r} not in factor tables")


def load_productivity_rates(path: Optional[str] = None) -> Dict[str, Any]:
    """Load and validate the rate library. Raises on missing/invalid file (fail loudly, no silent defaults)."""
    p = path or RATES_PATH
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    try:
        validate_rate_library(data)
    except ValueError as ex:
        raise ValueError(f"productivity rates file {p}: {ex}")
    return data


def bridged_activity_types(rates: Optional[Dict[str, Any]] = None) -> List[str]:
    """Activity types that can be applied to legacy templates (have a keyword bridge), config order."""
    rates = rates or load_productivity_rates()
    return [k for k, v in rates["activity_types"].items() if "legacy_template_bridge" in v]


def _downgrade(level: str, levels: Sequence[str]) -> str:
    idx = list(levels).index(level) if level in levels else 0
    return levels[max(0, idx - 1)]


def _resolve_factors(spec: Dict[str, Any], tables: Dict[str, Dict[str, float]]):
    """Turn {name: key|multiplier} into ({name: multiplier}, {name: label}).

    Every factor table in config gets an entry (default 1.0 when unspecified) so the
    explanation always lists the full factor set, not just the ones the caller set.
    """
    spec = dict(spec or {})
    multipliers: Dict[str, float] = {}
    labels: Dict[str, str] = {}
    for name, table in tables.items():
        if name not in spec:
            multipliers[name] = 1.0
            labels[name] = "unspecified(1.0)"
            continue
        val = spec.pop(name)
        if isinstance(val, str):
            if val not in table:
                raise ValueError(f"unknown {name} factor key '{val}'; allowed: {sorted(table)}")
            multipliers[name] = float(table[val])
            labels[name] = val
        else:
            multipliers[name] = float(val)
            labels[name] = "custom"
    for name, val in spec.items():
        # ad-hoc numeric factor not in config tables
        if isinstance(val, str):
            raise ValueError(f"factor '{name}' is not in the rate library and has no numeric value")
        multipliers[name] = float(val)
        labels[name] = "custom"
    for name, m in multipliers.items():
        if not (math.isfinite(m) and m > 0):
            raise ValueError(f"factor '{name}' must be > 0, got {m}")
    return multipliers, labels


def duration_parts(qty: float, rate: float, crew: int, factor_product: float) -> tuple:
    """V6.1.1: the single source of the raw → rounded split.

    Diagnostics and the final duration must never disagree, so both go through here.
    round(…, 6) guards against float noise turning an exact 24.0 into 25 after ceil.
    """
    calculated = qty / (rate * crew) * factor_product
    raw = round(calculated, 6)
    return raw, int(math.ceil(raw))


def final_duration_days(qty: float, rate: float, crew: int, factor_product: float, min_days: int) -> int:
    """ceil(raw) then floor at the minimum. `min_days` is whatever the caller resolved."""
    _raw, rounded = duration_parts(qty, rate, crew, factor_product)
    return max(int(min_days), rounded)


def _final_days(qty: float, rate: float, crew: int, factor_product: float, min_days: int) -> int:
    return final_duration_days(qty, rate, crew, factor_product, min_days)


def compute_activity_duration(
    activity_type: str,
    quantity: float,
    crew_size: Optional[int] = None,
    factors: Optional[Dict[str, Any]] = None,
    rates: Optional[Dict[str, Any]] = None,
    quantity_source: str = QTY_MEASURED,
    rate_level: Optional[str] = None,
    shifts: float = 1.0,
    workfront_efficiency: float = 1.0,
    project_area: Optional[float] = None,
    workfronts: Optional[int] = None,
    min_duration_days: Optional[int] = None,
    min_duration_scope: str = "LEGACY_UNSPECIFIED",
) -> Dict[str, Any]:
    """Compute one activity's duration from quantity and productivity.

    Returns an explainable dict (quantity, unit, productivity_rate, rate_level, rate_table,
    crew_size, factors, calculated_duration, final_duration, duration_by_rate_level,
    confidence, ...). Raises ValueError on bad input or unknown activity type — callers
    decide whether to fall back to the template path.
    """
    rates = rates or load_productivity_rates()
    lib = rates["activity_types"]
    if activity_type not in lib:
        raise ValueError(f"activity_type '{activity_type}' not in rate library {sorted(lib)}")
    entry = lib[activity_type]
    levels = rates.get("confidence_levels") or _DEFAULT_LEVELS
    rate_levels = _rate_levels(rates)

    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        raise ValueError(f"quantity must be numeric, got {quantity!r}")
    if not (math.isfinite(qty) and qty > 0):
        raise ValueError(f"quantity must be > 0, got {qty}")

    table = _rate_table(entry, rate_levels, activity_type)
    level = rate_level or _default_rate_level(rates)
    if level not in table:
        raise ValueError(f"unknown rate_level '{level}'; allowed: {rate_levels}")
    rate = table[level]

    reasons: List[str] = []
    confidence = entry.get("base_confidence", "medium")
    if confidence not in levels:
        confidence = levels[0]
    evidence_level = entry.get('evidence', {}).get('level', 'L0')
    if entry.get('rate_status', 'provisional') == 'provisional' or entry.get('source', {}).get('confidence') == 'low':
        confidence = 'low'
    reasons.append(f"rate evidence = {evidence_level}; status = {entry.get('rate_status', 'provisional')}; base confidence = {confidence}")
    if rate_level is None:
        reasons.append(f"rate_level not given → library default '{level}' ({rate:g} {entry.get('unit', '')}/worker-day)")
    else:
        reasons.append(f"rate_level pinned by task = '{level}' ({rate:g} {entry.get('unit', '')}/worker-day)")

    capacity_info = None
    congestion_factor_val = 1.0
    theoretical_crew = None
    if crew_size is None:
        crew = int(entry.get("default_crew_size", 1))
        crew_source = "default"
        confidence = _downgrade(confidence, levels)
        reasons.append(f"crew_size not given → default {crew} → confidence downgraded to {confidence}")
        if project_area is not None or workfronts is not None:
            crew = recommend_crew(activity_type, qty, project_area, workfronts, rates)
            crew_source = 'recommended'
            crew_detail = recommend_crew_detail(activity_type, qty, project_area, workfronts, rates)
            theoretical_crew = crew_detail.get('theoretical_workers')
            capacity_info = crew_detail
            # FIX-06/07: congestion depends on per-workfront density (feasible workers
            # spread over the canonical workfronts), not on the project-wide headcount.
            # Folding it into workfront_efficiency keeps the frozen duration formula
            # (quantity / (rate x crew x shifts x workfront_efficiency) x factors) intact.
            if crew_detail.get('crew_basis') == 'minimum_practical_crew_applied':
                reasons.append(
                    f"theoretical {crew_detail['theoretical_workers']} worker(s) raised to practical minimum "
                    f"{crew_detail['minimum_practical_crew']} (provisional, no field evidence)")
            elif crew_detail.get('workfront_capped_below_practical_minimum'):
                reasons.append(
                    f"physical workfront cap {crew_detail['physical_cap']} is below the practical minimum "
                    f"{crew_detail['minimum_practical_crew']}; the physical cap governs")
            elif crew_detail.get('crew_basis') == 'physical_workfront_capacity_applied':
                reasons.append(
                    f"theoretical {crew_detail['theoretical_workers']} worker(s) capped to feasible "
                    f"{crew} by physical workfront capacity "
                    f"({crew_detail['workfront_count']} workfronts x "
                    f"{crew_detail.get('max_crews_per_workfront')} crews x "
                    f"{crew_detail.get('workers_per_crew')} workers/crew = "
                    f"{crew_detail['physical_cap']}) (provisional)")
    else:
        if not math.isfinite(float(crew_size)) or float(crew_size) != int(crew_size) or isinstance(crew_size, bool):
            raise ValueError('crew_size must be a positive integer')
        crew = int(crew_size)
        crew_source = "task"
    if crew < 1:
        raise ValueError(f"crew_size must be >= 1, got {crew}")

    # FIX-06/07: congestion depends on per-workfront DENSITY (feasible workers spread over
    # the canonical workfronts), not on the project-wide headcount. It applies whenever the
    # workfront context is known — for auto-sized crews and for explicitly pinned crews alike
    # (the spatial pipeline pins a project crew but still knows project_area / workfronts), so
    # the flat and spatial pipelines cannot drift apart. Folding it into workfront_efficiency
    # keeps the frozen duration formula intact while congestion actively reduces output.
    if project_area is not None or workfronts is not None:
        from core.workfront_capacity import congestion_factor, resolve_workfronts
        try:
            if project_area is not None:
                # V6.4.4.1 P1-01: congestion density uses the CONTINUOUS effective workfront
                # capacity, so it moves smoothly with area instead of stepping at a boundary.
                wf_eff = float(resolve_workfronts(float(project_area))["effective_workfront_capacity"])
            else:
                wf_eff = float(workfronts)
        except (TypeError, ValueError):
            wf_eff = 1.0
        wf_eff = max(1e-9, wf_eff)
        density = max(1, int(math.ceil(crew / wf_eff)))
        cong = congestion_factor(activity_type, density)
        congestion_factor_val = float(cong.get("crew_efficiency", 1.0) or 1.0)
        if congestion_factor_val < 1.0 - 1e-9:
            reasons.append(
                f"per-workfront density {density} worker(s) over {wf_eff:.2f} effective workfront(s) → congestion "
                f"efficiency {congestion_factor_val:g} (provisional crew-efficiency curve; no field evidence)")

    if quantity_source != QTY_MEASURED:
        confidence = _downgrade(confidence, levels)
        reasons.append(f"quantity is {quantity_source} (not measured takeoff) → confidence downgraded to {confidence}")

    multipliers, labels = _resolve_factors(factors or {}, entry.get("factors", {}))
    factor_product = 1.0
    for m in multipliers.values():
        factor_product *= m

    shifts, base_workfront_efficiency = float(shifts), float(workfront_efficiency)
    if not (math.isfinite(shifts) and shifts > 0 and math.isfinite(base_workfront_efficiency) and 0 < base_workfront_efficiency <= 1):
        raise ValueError('shifts must be positive finite; efficiency must be in (0,1]')
    # V6.4.4.1 P1-02: ONE canonical shift multiplier. The governed shift model
    # (config/resource_constraints.json) says 2 shifts => output_multiplier 1.75
    # (derate_factor 0.875), NOT 2.00x. The active duration path previously used
    # resource_factor = shifts x workfront_efficiency, i.e. a raw 2.00x — inconsistent with
    # the governed model, the resource engine and the Scenario Optimizer. The spec's flow is
    #   base_productivity x feasible_crew x governed_shift_multiplier x workfront_efficiency
    #   x congestion_factor x distinct modifiers
    # so the governed multiplier replaces the raw shift count, and workfront_efficiency stays
    # exactly what the caller passed (times congestion).
    from core.resource_model import shift_factor as _governed_shift_factor
    _sf = _governed_shift_factor(shifts)
    governed_shift_multiplier = _sf.get("output_multiplier")
    if governed_shift_multiplier is None:
        raise ValueError(
            f"shifts={shifts:g}: governed shift output multiplier is {_sf.get('status')} "
            f"({_sf.get('basis') or 'no evidence'}); refusing to substitute the raw shift count")
    shift_derate_factor = float(_sf.get("derate_factor") or (float(governed_shift_multiplier) / shifts))
    if abs(shifts * shift_derate_factor - float(governed_shift_multiplier)) > 1e-9:
        raise ValueError("governed shift derate does not reconcile with the output multiplier")
    # FIX-07: congestion (per-workfront density) is folded into workfront_efficiency so the
    # frozen duration formula is preserved while congestion actively reduces output.
    workfront_efficiency = base_workfront_efficiency * congestion_factor_val
    resource_factor = float(governed_shift_multiplier) * workfront_efficiency
    daily_output = rate * crew * resource_factor
    base_duration = qty / daily_output
    calculated = base_duration * factor_product
    min_days = int(min_duration_days if min_duration_days is not None else entry.get("min_duration_days", 1))
    raw_duration, rounded_duration = duration_parts(qty, rate * resource_factor, crew, factor_product)
    final = max(min_days, rounded_duration)
    by_level = {lv: _final_days(qty, table[lv] * resource_factor, crew, factor_product, min_days) for lv in rate_levels}

    # --- FIX-02/03/12/15: theoretical vs feasible crew, capacity status, achievability ---
    target_days = int((entry.get('crew_scaling') or {}).get('target_days', 0) or 0)
    resource_block: Dict[str, Any] = {}
    if crew_source == 'recommended' and capacity_info is not None:
        feasible = crew
        physical_cap = int(capacity_info.get('physical_cap') or 0)
        achievable = final
        target_achievable = (target_days > 0) and (achievable <= target_days)
        resource_block = dict(
            theoretical_crew_demand=theoretical_crew,
            requested_crew=theoretical_crew,
            available_crew=None,               # FIX-13: contractor availability is UNKNOWN
            physical_capacity_workers=physical_cap,
            physical_capacity_crews=capacity_info.get('physical_capacity_crews'),
            feasible_crew=feasible,
            crew_count=int(math.ceil(feasible / int(capacity_info.get('workers_per_crew') or 1))) if capacity_info.get('workers_per_crew') else None,
            workers_per_crew=capacity_info.get('workers_per_crew'),
            max_crews_per_workfront=capacity_info.get('max_crews_per_workfront'),
            workfront_count=capacity_info.get('workfront_count'),
            effective_workfront_capacity=capacity_info.get('effective_workfront_capacity'),
            sectional_workfront_count=capacity_info.get('sectional_workfront_count'),
            resource_gap=(theoretical_crew - feasible if theoretical_crew else None),
            worker_gap=(theoretical_crew - feasible if theoretical_crew else None),
            target_duration_days=target_days,
            crew_sizing_target_days=target_days,
            achievable_duration_days=achievable,
            target_achievable=target_achievable,
            capacity_status=(
                'WORKFRONT_CONSTRAINED' if (theoretical_crew and physical_cap and theoretical_crew > feasible)
                else ('FEASIBLE' if physical_cap >= feasible else 'WORKFRONT_CONSTRAINED')),
            congestion_factor=round(congestion_factor_val, 6),
            congestion_status='PROVISIONAL' if congestion_factor_val < 1.0 - 1e-9 else 'BASELINE',
        )

    return {
        "method": METHOD_FORMULA,
        "activity_type": activity_type,
        "quantity": qty,
        "unit": entry.get("unit", ""),
        "quantity_source": quantity_source,
        "productivity_rate": rate,
        "productivity_unit": f"{entry.get('unit', '')}/worker-day",
        "rate_level": level,
        "rate_level_source": "task" if rate_level is not None else "default",
        "rate_table": table,
        "crew_size": crew,
        "crew_source": crew_source,
        "crew_basis": crew_detail.get('crew_basis', crew_source) if crew_size is None and (project_area is not None or workfronts is not None) else ("task" if crew_size is not None else "library_default_crew_size"),
        "crew_confidence": ("low" if str(load_crew_practicality().get('status')) == 'provisional' else "medium") if crew_size is None and (project_area is not None or workfronts is not None) else "explicit",
        "recommended_crew": recommend_crew(activity_type, qty, project_area, workfronts, rates),
        "shifts": shifts,
        "workfront_efficiency": workfront_efficiency,
        "base_workfront_efficiency": round(base_workfront_efficiency, 6),
        "congestion_factor": round(congestion_factor_val, 6),
        # V6.4.4.1 P1-02 shift governance trace
        "shift_count": shifts,
        "raw_shift_ratio": shifts,
        "governed_shift_multiplier": round(float(governed_shift_multiplier), 6),
        "shift_derate_factor": round(shift_derate_factor, 6),
        "shift_basis": _sf.get("basis"),
        "shift_confidence": _sf.get("status", "provisional"),
        "shift_source": "resource_constraints.shift_model (canonical)",
        **resource_block,
        "rate_status": entry.get('rate_status', 'provisional'),
        "source": entry.get('source', {'confidence': 'low', 'reference': None}),
        "scope_id": entry.get('scope_id'),
        "evidence": entry.get('evidence', {'tier':'provisional_fallback'}),
        "crew_sizing_policy": entry.get('crew_scaling'),
        "workfronts": workfronts,
        # 2026-09-24：project_area 与 workfronts 同时传入时，容量推导只认 project_area，
        # workfronts 会被静默忽略（实测同一输入两种传法给出 feasible 91 vs 12 两个结果）。
        # 这里把「实际采用了哪个来源」与「哪个被丢弃」显式输出，避免调用方以为
        # 自己指定的工作面数生效了。语义：显式优先级见 workfront_source。
        "workfront_source": (
            "EXPLICIT_WORKFRONTS" if (project_area is None and workfronts is not None)
            else "AREA_DERIVED" if (project_area is not None and workfronts is None)
            else "AREA_DERIVED__IGNORING_EXPLICIT_WORKFRONTS"
            if (project_area is not None and workfronts is not None) else None),
        "workfronts_ignored": (
            workfronts if (project_area is not None and workfronts is not None) else None),
        "resource_demand_note": 'Recommended trade crew is not proof of availability or shared-pool leveling',
        "daily_output": round(daily_output, 4),
        "factors": multipliers,
        "factor_labels": labels,
        "factor_product": round(factor_product, 6),
        "base_duration": round(base_duration, 4),
        "calculated_duration": round(calculated, 4),
        "final_duration": final,
        "duration_by_rate_level": by_level,
        "min_duration_days": min_days,
        # --- V6.1.1 duration diagnostics (additive; nothing above changes) -----------
        # raw / rounded / applied minimum, and how many days each step added. Lets a
        # planner see how much of a duration is physics and how much is model artefact.
        "raw_calculated_duration": raw_duration,
        "rounded_duration": rounded_duration,
        "applied_min_duration": int(min_days),
        "min_duration_scope": min_duration_scope,
        "rounding_penalty_days": round(rounded_duration - raw_duration, 6),
        "minimum_duration_penalty_days": int(final - rounded_duration),
        "rounding": "ceil_to_integer_workday_then_floor_at_min",
        "confidence": confidence,
        "confidence_reasons": reasons,
        "evidence_level": evidence_level,
        "rationale": f"{qty:g} {entry.get('unit', '')} / ({rate:g} x {crew} workers x {shifts:g} shifts x {workfront_efficiency:g} efficiency) x {factor_product:g} modifiers = {calculated:g}; ceil and minimum {min_days} => {final} workdays. Quantity: {quantity_source}. Evidence: {evidence_level}; confidence: {confidence}.",
        "rates_version": rates.get("version", "unknown"),
    }


def load_crew_practicality():
    """P1-05: practical crew bounds. Provisional construction-organisation defaults,
    explicitly NOT field evidence; never mutates the productivity baseline."""
    global _CREW_PRACTICALITY_CACHE
    if _CREW_PRACTICALITY_CACHE is None:
        path = Path(__file__).resolve().parents[1] / "config" / "crew_practicality.json"
        with open(path, encoding="utf-8") as f:
            _CREW_PRACTICALITY_CACHE = json.load(f)
    return _CREW_PRACTICALITY_CACHE


_CREW_PRACTICALITY_CACHE = None


def recommend_crew(activity_type, quantity, project_area=None, workfronts=None, rates=None):
    """Size a FEASIBLE crew (FIX-02). `target_days` is a CREW SIZING HORIZON — it answers
    "how many workers would it take to finish in target_days", never "the activity lasts
    target_days". The theoretical demand is capped by the canonical physical workfront
    capacity (FIX-01/04/05): workfront_count x max_crews_per_workfront x workers_per_crew,
    so the returned crew is what can physically execute, and the resulting duration is
    routinely longer than target_days. Nothing downstream may clamp a duration to
    target_days.

    P1-05: the theoretical demand is first raised to the practical minimum crew
    (a trade is not staffed by one isolated worker) and optionally capped by a
    practical maximum, BEFORE the physical workfront cap. Practical bounds are
    provisional config, never a productivity change.
    """
    entry = (rates or load_productivity_rates())['activity_types'][activity_type]
    policy = entry.get('crew_scaling')
    if not policy or (project_area is None and workfronts is None):
        return int(entry['default_crew_size'])
    if project_area is not None and (not math.isfinite(float(project_area)) or float(project_area) <= 0):
        raise ValueError('project_area must be positive finite')
    if workfronts is not None and (int(workfronts) != workfronts or workfronts < 1):
        raise ValueError('workfronts must be positive integer')
    demand = math.ceil(float(quantity) / (entry['rates_per_worker_day']['typical'] * policy['target_days']))
    practical = load_crew_practicality()
    min_p = practical.get('by_activity_type', {}).get(activity_type, practical.get('default_min'))
    max_p = practical.get('by_activity_type', {}).get('max:' + activity_type, practical.get('default_max'))
    # Canonical physical capacity (FIX-01/04/05). One workfront model everywhere; no
    # module divides area by its own hard-coded divisor any more.
    from core.workfront_capacity import physical_capacity_workers
    if project_area is not None:
        cap_info = physical_capacity_workers(float(project_area), activity_type, rates=rates)
    else:
        cap_info = physical_capacity_workers(0.0, activity_type, explicit_workfronts=workfronts, rates=rates)
    # V6.4.4.1 P1-01: keep the physical cap FRACTIONAL. Truncating it to int() reintroduced a
    # 1-worker step at every whole-capacity boundary (e.g. 3999 → 11 vs 4000 → 12 workers),
    # which is exactly the discontinuity this patch removes.
    physical_cap = float(cap_info['physical_capacity_workers'])
    bounded = float(demand)
    if min_p:
        bounded = max(bounded, float(min_p))
    if max_p:
        bounded = min(bounded, float(max_p))
    crew = max(1.0, min(bounded, physical_cap))
    return crew


def recommend_crew_detail(activity_type, quantity, project_area=None, workfronts=None, rates=None):
    """recommend_crew with the FIX-02 trace: theoretical / feasible / physical capacity."""
    entry = (rates or load_productivity_rates())['activity_types'][activity_type]
    policy = entry.get('crew_scaling')
    crew = recommend_crew(activity_type, quantity, project_area, workfronts, rates)
    detail = {
        "crew": crew,
        "feasible_crew": crew,
        "theoretical_workers": None,
        "minimum_practical_crew": None,
        "maximum_practical_crew": None,
        "physical_cap": None,
        "workfront_count": None,
        "workfront_capped_below_practical_minimum": False,
        "crew_basis": "library_default_or_scaling",
    }
    if not policy or (project_area is None and workfronts is None):
        detail["crew_basis"] = "library_default_crew_size"
        return detail
    demand = math.ceil(float(quantity) / (entry['rates_per_worker_day']['typical'] * policy['target_days']))
    practical = load_crew_practicality()
    min_p = practical.get('by_activity_type', {}).get(activity_type, practical.get('default_min'))
    max_p = practical.get('by_activity_type', {}).get('max:' + activity_type, practical.get('default_max'))
    from core.workfront_capacity import physical_capacity_workers
    if project_area is not None:
        cap_info = physical_capacity_workers(float(project_area), activity_type, rates=rates)
    else:
        cap_info = physical_capacity_workers(0.0, activity_type, explicit_workfronts=workfronts, rates=rates)
    physical_cap = float(cap_info['physical_capacity_workers'])
    bounded = max(float(demand), float(min_p)) if min_p else float(demand)
    if max_p:
        bounded = min(bounded, float(max_p))
    detail.update({
        "theoretical_workers": demand,
        "feasible_crew": crew,
        "minimum_practical_crew": int(min_p) if min_p else None,
        "maximum_practical_crew": int(max_p) if max_p else None,
        "physical_cap": physical_cap,
        "physical_capacity_workers": physical_cap,
        "physical_capacity_crews": cap_info.get('physical_capacity_crews'),
        "workfront_count": cap_info.get('workfront_count'),
        "effective_workfront_capacity": cap_info.get('effective_workfront_capacity'),
        "sectional_workfront_count": cap_info.get('sectional_workfront_count'),
        "max_crews_per_workfront": cap_info.get('max_crews_per_workfront'),
        "workers_per_crew": cap_info.get('workers_per_crew'),
        "workfront_capped_below_practical_minimum": bool(min_p and physical_cap < int(min_p) and crew < int(min_p)),
    })
    if crew < demand:
        detail["crew_basis"] = "physical_workfront_capacity_applied"
    elif min_p and demand < int(min_p) and crew >= int(min_p):
        detail["crew_basis"] = "minimum_practical_crew_applied"
    elif detail["workfront_capped_below_practical_minimum"]:
        detail["crew_basis"] = "workfront_cap_below_practical_minimum"
    return detail


def _as_quantity_map(derived: Any) -> Dict[str, float]:
    """Accept core.quantity_engine result / {name: value} / None → {name: value} (lazy import, no cycle)."""
    if not derived:
        return {}
    from core.quantity_engine import as_quantity_map
    return as_quantity_map(derived)


def resolve_derived_quantity(
    activity_type: str,
    derived_quantities: Any,
    rates: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Quantity Engine hook: look up the rate entry's `quantity_key` in derived quantities.

    Returns {"quantity", "quantity_key", "quantity_source"} or None when the activity type
    declares no `quantity_key` or the engine output lacks it. Never raises on a miss.
    """
    rates = rates or load_productivity_rates()
    entry = rates["activity_types"].get(activity_type) or {}
    key = entry.get("quantity_key")
    if not key:
        return None
    qmap = _as_quantity_map(derived_quantities)
    if key not in qmap:
        return None
    return {"quantity": float(qmap[key]), "quantity_key": key, "quantity_source": QTY_DERIVED}


def apply_productivity_durations(
    tasks: List[Dict[str, Any]],
    rates: Optional[Dict[str, Any]] = None,
    log: Optional[logging.Logger] = None,
    derived_quantities: Any = None,
) -> List[Dict[str, Any]]:
    """Activity-type switch. Tasks with `activity_type` + `quantity` get a formula duration
    and an explainable `productivity` block; all other tasks are returned untouched.

    `derived_quantities` (optional) is a core.quantity_engine result or {name: value} map.
    A task with `activity_type` but no measured `quantity` is filled from it via the rate
    entry's `quantity_key` and flagged `derived_from_area`; a measured quantity always wins.

    Unknown activity types / missing quantity / invalid factors / unknown rate_level do NOT
    raise: the task keeps its template duration and a warning is logged, so the legacy path
    is never broken by a half-tagged task. Task-level rate keys are ignored (config only).
    """
    log = log or logger
    rates = rates or load_productivity_rates()
    n_applied = 0
    for t in tasks:
        atype = t.get("activity_type")
        if not atype:
            continue
        prev = int(round(float(t.get("duration_days", t.get("duration", 0)) or 0)))
        if t.get("quantity") is None and derived_quantities:
            hit = resolve_derived_quantity(atype, derived_quantities, rates)
            if hit:
                t["quantity"] = hit["quantity"]
                t["quantity_source"] = hit["quantity_source"]
                t["quantity_key"] = hit["quantity_key"]
                t.setdefault("unit", rates["activity_types"][atype].get("unit", ""))
                log.info(
                    f"  -> [productivity] task {t.get('id')} '{t.get('name')}': no measured quantity → "
                    f"quantity engine {hit['quantity_key']} = {hit['quantity']:g}{t.get('unit', '')} ({QTY_DERIVED})"
                )
        if t.get("quantity") is None:
            log.warning(f"  -> [productivity] task {t.get('id')} '{t.get('name')}' has activity_type={atype} but no quantity; keeping template duration {prev}d")
            continue
        smuggled = [k for k in _FORBIDDEN_TASK_RATE_KEYS if k in t]
        if smuggled:
            log.warning(
                f"  -> [productivity] task {t.get('id')} '{t.get('name')}' carries task-level rate key(s) {smuggled}; "
                f"ignored — rates come only from config/productivity_rates.json (choose a level via rate_level instead)"
            )
        try:
            res = compute_activity_duration(
                atype,
                t["quantity"],
                crew_size=t.get("crew_size"),
                factors=t.get("factors"),
                rates=rates,
                quantity_source=t.get("quantity_source", QTY_MEASURED),
                rate_level=t.get("rate_level"),
                shifts=t.get('shifts', 1),
                workfront_efficiency=t.get('workfront_efficiency', 1),
                project_area=t.get('project_area'),
                workfronts=t.get('workfronts'),
            )
        except ValueError as ex:
            log.warning(f"  -> [productivity] task {t.get('id')} '{t.get('name')}': {ex}; keeping template duration {prev}d")
            continue
        res["template_duration"] = prev
        if t.get("quantity_key"):
            res["quantity_key"] = t["quantity_key"]
        t["duration_days"] = res["final_duration"]
        t["duration"] = res["final_duration"]
        t["duration_method"] = METHOD_FORMULA
        t["productivity"] = res
        n_applied += 1
        rng = res["duration_by_rate_level"]
        log.info(
            f"  -> [productivity] task {t.get('id')} '{t.get('name')}': {atype} "
            f"{res['quantity']:g}{res['unit']} ÷ ({res['productivity_rate']:g}{res['productivity_unit']}[{res['rate_level']}] × crew {res['crew_size']}) "
            f"× factors {res['factor_product']:g} = {res['calculated_duration']:g} → {res['final_duration']}d "
            f"(range by rate level {rng}; template was {prev}d; confidence={res['confidence']})"
        )
    if n_applied:
        log.info(f"  -> [productivity] formula path applied to {n_applied} task(s); all others keep template durations.")
    return tasks


def tag_legacy_pilot_tasks(
    tasks: List[Dict[str, Any]],
    area_sqm: float,
    activity_types: Union[str, Sequence[str]] = ("suspended_ceiling",),
    quantity_override: Optional[float] = None,
    rates: Optional[Dict[str, Any]] = None,
    log: Optional[logging.Logger] = None,
    quantity_overrides: Optional[Dict[str, float]] = None,
    rate_level: Optional[str] = None,
    derived_quantities: Any = None,
) -> List[int]:
    """Bridge for hard-coded WBS templates (opt-in, `--productivity_pilot`).

    Legacy tasks carry no quantity. For each requested activity type with a
    `legacy_template_bridge` in the rate library, leaf tasks whose name contains a
    match keyword are tagged with `activity_type` and a quantity, in precedence order:
    caller's measured value (`quantity_overrides[type]`, or `quantity_override` for every
    type) → Quantity Engine output (`derived_quantities`, via the entry's `quantity_key`)
    → gross area × bridge ratio. Both non-measured paths are flagged `derived_from_area`.
    Bridge-level default factors (e.g. a composite legacy task) are copied onto the task so
    they show up in the explanation. `activity_types="all"` selects every bridged type in
    config order. Templates on disk are never modified. Returns the tagged task ids.
    """
    log = log or logger
    rates = rates or load_productivity_rates()
    if isinstance(activity_types, str):
        activity_types = bridged_activity_types(rates) if activity_types == "all" else [activity_types]
    overrides = dict(quantity_overrides or {})
    tagged: List[int] = []
    for atype in activity_types:
        entry = rates["activity_types"].get(atype)
        if not entry or "legacy_template_bridge" not in entry:
            log.warning(f"  -> [productivity] no legacy bridge for activity_type={atype}; nothing tagged")
            continue
        bridge = entry["legacy_template_bridge"]
        kws = bridge.get("match_keywords", [])
        ratio = float(bridge.get("quantity_from_area_ratio", 1.0))
        engine_hit = resolve_derived_quantity(atype, derived_quantities, rates) if derived_quantities else None
        override = overrides.get(atype, quantity_override)
        for t in tasks:
            if t.get("activity_type") or t.get("milestone"):
                continue
            if int(t.get("outline_level", t.get("level", 3))) < 3:
                continue
            if not (float(t.get("duration_days", t.get("duration", 0)) or 0) > 0):
                continue
            name = str(t.get("name", ""))
            if not any(k in name for k in kws):
                continue
            t["activity_type"] = atype
            t["unit"] = entry.get("unit", "")
            if override is not None:
                t["quantity"] = float(override)
                t["quantity_source"] = QTY_MEASURED
                how = "measured override"
            elif engine_hit:
                t["quantity"] = engine_hit["quantity"]
                t["quantity_source"] = engine_hit["quantity_source"]
                t["quantity_key"] = engine_hit["quantity_key"]
                how = f"quantity engine {engine_hit['quantity_key']}"
            else:
                t["quantity"] = round(float(area_sqm) * ratio, 1)
                t["quantity_source"] = QTY_DERIVED
                how = f"legacy bridge {area_sqm:g} × {ratio:g}"
            if bridge.get("factors") and not t.get("factors"):
                t["factors"] = dict(bridge["factors"])
            if rate_level:
                t["rate_level"] = rate_level
            tagged.append(t["id"])
            log.info(
                f"  -> [productivity] pilot-tagged task {t['id']} '{name}' as {atype}: "
                f"quantity={t['quantity']:g}{t['unit']} ({t['quantity_source']}, {how})"
                + (f", factors={t['factors']}" if t.get("factors") else "")
                + (f", rate_level={rate_level}" if rate_level else "")
            )
    return tagged


def productivity_summary(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Explainable rows for every formula-path task (for logs / sidecar JSON)."""
    rows = []
    for t in tasks:
        if t.get("duration_method") == METHOD_FORMULA and t.get("productivity"):
            row = {"id": t.get("id"), "name": t.get("name"),
                   "start": t.get("start"), "finish": t.get("finish"),
                   "critical": t.get("critical"), "total_slack_days": t.get("total_slack_days")}
            row.update(t["productivity"])
            rows.append(row)
    return rows
