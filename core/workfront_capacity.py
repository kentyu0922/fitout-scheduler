# -*- coding: utf-8 -*-
"""V6.4.4 canonical physical workfront capacity model (FIX-01/04/05/06/09).

One canonical definition of a workfront, consumed by the Productivity, Dependency,
Spatial, Resource and Scenario engines, so no module divides ``area`` by its own
hard-coded divisor.

The canonical workfront derivation priority (WORKFRONT INPUT PRIORITY):

1. explicit project zoning / floor data
2. explicit user-defined workfronts (``--workfronts``)
3. confirmed contractor workfront plan
4. Spatial Engine derived workfronts
5. area-based planning default  ->  ``source_type=PLANNING_ASSUMPTION``, ``confidence=PROVISIONAL``

Physical capacity is NOT a global headcount ceiling. It is:

    workfront_count x max_crews_per_workfront x workers_per_crew

subject to trade-specific constraints (FIX-04). ``crew_count`` (number of crews) is
kept distinct from ``workers_per_crew`` (crew size) (FIX-06).
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADE_CAPACITY_PATH = os.path.join(BASE_DIR, "config", "trade_capacity.json")

SOURCE_TYPE_PLANNING_ASSUMPTION = "PLANNING_ASSUMPTION"
CONFIDENCE_PROVISIONAL = "PROVISIONAL"

_CACHE: Dict[str, Any] = {}


def load_trade_capacity(path: Optional[str] = None) -> Dict[str, Any]:
    """Load and validate the canonical trade capacity config (cached)."""
    key = path or TRADE_CAPACITY_PATH
    if key not in _CACHE:
        with open(key, encoding="utf-8") as f:
            cfg = json.load(f)
        validate_trade_capacity(cfg)
        _CACHE[key] = cfg
    return _CACHE[key]


def validate_trade_capacity(cfg: Dict[str, Any]) -> None:
    wf = cfg.get("workfront") or {}
    for k in ("recommended_zone_area_sqm", "min_workfronts", "max_workfronts"):
        if k not in wf or float(wf[k]) <= 0:
            raise ValueError(f"trade_capacity.workfront.{k} missing or not > 0")
    dflt = cfg.get("default_capacity") or {}
    if int(dflt.get("max_crews_per_workfront", 0)) < 1:
        raise ValueError("trade_capacity.default_capacity.max_crews_per_workfront must be >= 1")
    if not dflt.get("basis"):
        raise ValueError("trade_capacity.default_capacity.basis is mandatory")
    for name, spec in (cfg.get("by_activity_type") or {}).items():
        if int(spec.get("max_crews_per_workfront", 0)) < 1:
            raise ValueError(f"trade_capacity.by_activity_type.{name}.max_crews_per_workfront must be >= 1")


def _spec_for(activity_type: str, cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    by_at = cfg.get("by_activity_type") or {}
    if activity_type in by_at:
        spec = dict(cfg.get("default_capacity") or {})
        spec.update(by_at[activity_type])
        return spec, f"by_activity_type.{activity_type}"
    return (cfg.get("default_capacity") or {}), "default_capacity"


def resolve_workfronts(area_sqm: float, explicit_workfronts: Optional[int] = None,
                       cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Canonical workfront model for a project area (V6.4.4.1 P1-01).

    Two concepts are deliberately SEPARATED so +1 ㎡ cannot cause a schedule cliff:

    A. ``effective_workfront_capacity`` — CONTINUOUS (fractional). Physical capacity may
       scale progressively with area, e.g. 2000 ㎡ → 1.00, 2200 ㎡ → 1.10, 3000 ㎡ → 1.50,
       4000 ㎡ → 2.00. This drives physical worker capacity, feasible crew and resource
       demand satisfaction, so crew and duration move smoothly with area.

    B. ``sectional_workfront_count`` — DISCRETE (integer). Dependency / sectional handover
       needs real, independently executable zones, so it counts only *whole* practical
       zones: ``floor(area / recommended_zone_area_sqm)``. A fractional capacity value
       never creates a second handover zone by itself.

    Priority (spec "INPUT PRIORITY"): explicit zoning > explicit user workfronts >
    contractor plan > Spatial Engine zones > area-based provisional capacity. Only the
    last is used here, and it is marked PROVISIONAL / PLANNING_ASSUMPTION.
    """
    cfg = cfg or load_trade_capacity()
    wf_cfg = cfg["workfront"]
    cap = int(wf_cfg["max_workfronts"])
    min_wf = int(wf_cfg["min_workfronts"])
    rec = float(wf_cfg["recommended_zone_area_sqm"])

    if explicit_workfronts is not None:
        if isinstance(explicit_workfronts, bool) or int(explicit_workfronts) != explicit_workfronts or explicit_workfronts < 1:
            raise ValueError("explicit_workfronts must be a positive integer")
        count = max(min_wf, min(cap, int(explicit_workfronts)))
        return dict(
            workfront_count=count, active_workfronts=count,
            effective_workfront_capacity=float(count),
            sectional_workfront_count=count,
            workfront_area_sqm=round(float(area_sqm) / count, 1) if area_sqm else None,
            minimum_practical_zone_sqm=float(wf_cfg.get("min_zone_area_sqm", 800)),
            basis=f"explicit --workfronts {int(explicit_workfronts)}"
                  + (f" capped at {cap}" if int(explicit_workfronts) > cap else "")
                  + " (a genuine discrete topology — allowed to be a step)",
            confidence="EXPLICIT" if int(explicit_workfronts) <= cap else CONFIDENCE_PROVISIONAL,
            source_type="EXPLICIT_USER",
        )

    area = float(area_sqm or 0)
    # A. continuous capacity (fractional) — no ceil(), so no +1 ㎡ cliff
    eff = (area / rec) if area > 0 else float(min_wf)
    eff = max(float(min_wf), min(float(cap), eff))
    # B. discrete sectional workfronts (integer) — only WHOLE practical zones exist
    sectional = int(math.floor(area / rec)) if area > 0 else min_wf
    sectional = max(min_wf, min(cap, sectional))
    return dict(
        workfront_count=sectional,
        active_workfronts=sectional,
        effective_workfront_capacity=round(eff, 6),
        sectional_workfront_count=sectional,
        workfront_area_sqm=round(area / sectional, 1) if sectional and area else None,
        minimum_practical_zone_sqm=float(wf_cfg.get("min_zone_area_sqm", 800)),
        basis=(f"effective_workfront_capacity = clamp({area:g}㎡ / {rec:g}㎡, {min_wf}, {cap}) = {eff:.4f} "
               f"(continuous, fractional); sectional_workfront_count = clamp(floor({area:g}㎡ / {rec:g}㎡), "
               f"{min_wf}, {cap}) = {sectional} (discrete, whole zones only)"),
        confidence=CONFIDENCE_PROVISIONAL,
        source_type=SOURCE_TYPE_PLANNING_ASSUMPTION,
    )


def workers_per_crew(activity_type: str, cfg: Optional[Dict[str, Any]] = None,
                     rates: Optional[Dict[str, Any]] = None) -> int:
    """Crew size for one activity. References productivity_rates.default_crew_size —
    never invents a second crew size (provenance rule)."""
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    entry = (rates.get("activity_types") or {}).get(activity_type) or {}
    return int(entry.get("default_crew_size", 1) or 1)


def physical_capacity_workers(area_sqm: float, activity_type: str,
                              explicit_workfronts: Optional[int] = None,
                              cfg: Optional[Dict[str, Any]] = None,
                              rates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Canonical physical worker capacity for one trade at a project scale.

    Returns workfront_count, max_crews_per_workfront, workers_per_crew,
    physical_capacity_crews, physical_capacity_workers, basis, confidence, source_type.
    """
    cfg = cfg or load_trade_capacity()
    spec, source = _spec_for(activity_type, cfg)
    wf = resolve_workfronts(area_sqm, explicit_workfronts, cfg)
    wpc = workers_per_crew(activity_type, cfg, rates)
    max_crews = int(spec.get("max_crews_per_workfront", 1))
    min_crew = int(spec.get("minimum_crew", 1) or 1)
    # V6.4.4.1 P1-01: physical capacity uses the CONTINUOUS effective workfront capacity
    # (fractional) so crew/duration scale smoothly; sectional_workfront_count stays discrete
    # and is only used for handover topology.
    effective = float(wf["effective_workfront_capacity"])
    # V3.8（2026-09-23）: 容量分区面积与每面人数上限可按工序覆盖。
    # 只影响**物理容量**（连续、分数），**不影响** wf["sectional_workfront_count"]
    # —— 后者是交接拓扑（离散），resolve_workfronts 的 docstring 已明确两者分离，
    # 就是为了避免「+1㎡ 造成排期 cliff」。因此这里不改全局 recommended_zone_area_sqm，
    # 只在容量侧按工序换口径。
    # 依据：PM 2026-09-23 给的工序下限，用引擎自己的工效反推，只有 500㎡/面 能与之下限自洽
    # （2000㎡/面 口径下 22 条中 16 条跑不进自己签的 D 的 1.5 倍）。
    cap_zone = spec.get("capacity_zone_area_sqm")
    # 2026-09-24 修正：本分支原先无条件覆盖 effective，把 resolve_workfronts 已经按
    # EXPLICIT_USER 定好的工作面数冲掉 —— 实测 physical_capacity_workers(6000,
    # explicit_workfronts=6) 与完全不指定时给出同一个 96，调用方显式指定的值静默失效；
    # area=0 时更直接落到 min_workfronts（恒为 1 面）。resolve_workfronts 的
    # INPUT PRIORITY 已声明「explicit 优先」，此处让路以保持一致。
    if cap_zone and explicit_workfronts is None:
        _area = float(area_sqm or 0)
        _wf_cfg = cfg["workfront"]
        _lo, _hi = int(_wf_cfg["min_workfronts"]), int(_wf_cfg["max_workfronts"])
        effective = (max(float(_lo), min(float(_hi), _area / float(cap_zone)))
                     if _area > 0 else float(_lo))
    wpw = spec.get("workers_per_workfront")
    if wpw:
        # 每面人数上限由工序自己声明（取自该工序的 crew_scaling.max_workers_per_front）。
        # 它不必是 workers_per_crew 的整数倍，故不走 max_crews × crew 这条路。
        capacity_workers = effective * float(wpw)
        capacity_crews = (capacity_workers / wpc) if wpc else capacity_workers
        # 2026-09-24 修正：这里返回「实际生效的每面班组数」，使 canonical 公式
        #     physical_capacity_workers == effective_workfront_capacity
        #                                 × max_crews_per_workfront × workers_per_crew
        # 恒成立。原先返回 trade_capacity 里的配置值 max_crews，一旦它与
        # workers_per_workfront 不一致（22 条带新口径的工序里有 15 条如此），
        # 这条公式两边就对不上，而页面与文档都声称该公式成立 —— 属于静默不一致。
        # 配置原值改由 max_crews_per_workfront_config 返回，两者是否一致可直接判别。
        max_crews_effective = (float(wpw) / float(wpc)) if wpc else float(wpw)
    else:
        capacity_crews = effective * max_crews
        capacity_workers = capacity_crews * wpc
        max_crews_effective = max_crews
    return dict(
        activity_type=activity_type,
        workfront_count=int(wf["workfront_count"]),
        active_workfronts=int(wf["sectional_workfront_count"]),
        effective_workfront_capacity=round(effective, 6),
        sectional_workfront_count=int(wf["sectional_workfront_count"]),
        workfront_area_sqm=wf["workfront_area_sqm"],
        minimum_practical_zone_sqm=wf["minimum_practical_zone_sqm"],
        max_crews_per_workfront=max_crews_effective,
        max_crews_per_workfront_config=max_crews,
        max_crews_per_workfront_agrees_with_config=(
            True if wpw is None else abs(max_crews_effective - max_crews) < 1e-9),
        workers_per_crew=wpc,
        minimum_crew=min_crew,
        maximum_crew=(int(spec.get("maximum_crew")) if spec.get("maximum_crew")
                      else round(capacity_workers, 6)),
        physical_capacity_crews=round(capacity_crews, 6),
        physical_capacity_workers=round(capacity_workers, 6),
        capacity_zone_area_sqm_used=(float(cap_zone) if cap_zone else None),
        workers_per_workfront_used=(float(wpw) if wpw else None),
        trade_specific_workfront_capacity=dict(
            max_crews_per_workfront=max_crews, workers_per_crew=wpc),
        stacking_class=spec.get("stacking_class", "general"),
        congestion_class=spec.get("congestion_class", "default"),
        basis=spec.get("basis"),
        confidence=wf.get("confidence", CONFIDENCE_PROVISIONAL),
        source_type=wf.get("source_type", SOURCE_TYPE_PLANNING_ASSUMPTION),
        source=f"trade_capacity.{source}",
    )


def feasible_crew(theoretical_workers: int, physical_capacity_workers: int,
                  minimum_crew: int = 1) -> int:
    """Feasible crew = theoretical demand capped by physical capacity, floored at minimum."""
    return max(int(minimum_crew), min(int(theoretical_workers), int(physical_capacity_workers)))


def congestion_factor(activity_type: str, workers_per_workfront: int,
                      cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """FIX-06/07: congestion depends on per-workfront DENSITY (workers in one workfront),
    not on the project-wide headcount. Reuses the existing congestion_model table in
    resource_constraints.json (crew is not linear productivity)."""
    from core.workfront_model import trade_of
    from core.resource_model import crew_efficiency
    trade = trade_of(activity_type)
    return crew_efficiency(trade, max(1, int(workers_per_workfront)), cfg)


def shift_multiplier_trace(activity_type: str, workers: int, shifts: float = 1.0,
                           night: bool = False, base_rate: Optional[float] = None,
                           rate_level_factor: Optional[float] = None) -> Dict[str, Any]:
    """FIX-17: expose every multiplier so compounded acceleration is auditable, and flag
    POTENTIAL_DOUBLE_COUNT when two multipliers encode overlapping capacity assumptions.

    Each multiplier must represent a DISTINCT physical effect:
      * rate_level_factor   — optimistic/pessimistic rate evidence (not a capacity gain)
      * congestion_factor   — per-workfront density loss
      * shift_factor        — additional working hours per calendar day
      * workfront_factor    — additional parallel sections
    """
    from core.resource_model import crew_efficiency, shift_factor
    from core.workfront_model import trade_of
    trade = trade_of(activity_type)
    cong = crew_efficiency(trade, max(1, int(workers)))
    sf = shift_factor(shifts, night=night)
    sf_mult = sf.get("output_multiplier")
    overlapping: list = []
    if sf_mult is not None and float(sf_mult) > 1.0 and night:
        overlapping.append("night_shift_multiplier overlaps rate-level optimism")
    if rate_level_factor is not None and float(rate_level_factor) > 1.0 and float(shifts) > 1.0:
        overlapping.append("optimistic rate_level combined with extra shifts may double-count")
    final = None
    if base_rate is not None and sf_mult is not None:
        final = (float(base_rate) * (float(rate_level_factor or 1.0))
                 * float(cong["crew_efficiency"]) * float(sf_mult))
    return dict(
        activity_type=activity_type, trade=trade, workers=int(workers),
        base_productivity=(round(float(base_rate), 6) if base_rate is not None else None),
        rate_level_factor=round(float(rate_level_factor or 1.0), 6),
        crew_factor=int(workers),
        congestion_factor=round(float(cong["crew_efficiency"]), 6),
        shift_factor=(round(float(sf_mult), 6) if sf_mult is not None else None),
        workfront_factor=None,   # set by the caller when a workfront scenario is applied
        final_effective_productivity=(round(final, 6) if final is not None else None),
        # V6.4.4.1 P1-02 spec field names
        shift_count=float(shifts),
        raw_shift_ratio=float(shifts),
        governed_shift_multiplier=(round(float(sf_mult), 6) if sf_mult is not None else None),
        shift_derate_factor=(round(float(sf.get("derate_factor")), 6)
                             if sf.get("derate_factor") is not None else None),
        shift_basis=sf.get("basis"),
        shift_confidence=sf.get("status", "provisional"),
        double_count_flag="POTENTIAL_DOUBLE_COUNT" if overlapping else None,
        double_count_reasons=overlapping,
        shift_status=sf.get("status"),
        note=("Night output has no evidence → INSUFFICIENT_DATA, never a default doubling."
              if (night and sf_mult is None) else ""),
    )


def resource_scaling_warning(area_sqm: float, rows: Optional[list] = None,
                             large_project_threshold: float = 10000.0,
                             cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """FIX-18: diagnostic guardrail. Emits RESOURCE_SCALING_WARNING when a large project's
    crew demand grows roughly linearly with area while duration stays near the crew-sizing
    horizon and the executable workfront capacity does not justify it.

    Warning only — it never hard-codes a duration.
    """
    cfg = cfg or load_trade_capacity()
    rows = rows or []
    warnings: list = []
    for r in rows:
        try:
            area = float(r.get("area_sqm") or r.get("area") or 0)
            theoretical = r.get("theoretical_crew_demand") or r.get("theoretical_crew")
            feasible = r.get("feasible_crew")
            duration = r.get("achievable_duration_days") or r.get("duration_days")
            target = r.get("crew_sizing_target_days") or r.get("target_duration_days")
            wf = r.get("workfront_count")
            if not (area >= large_project_threshold and theoretical and feasible and duration and target):
                continue
            if float(duration) > float(target) * 1.25:
                continue                      # duration already scaled up — nothing suspicious
            cap = int(r.get("physical_capacity_workers") or 0)
            if cap and int(theoretical) > cap and wf:
                warnings.append(dict(
                    code="RESOURCE_SCALING_WARNING",
                    activity_type=r.get("activity_type"),
                    area_sqm=area,
                    theoretical_crew=int(theoretical), feasible_crew=int(feasible),
                    physical_capacity_workers=cap, workfront_count=int(wf),
                    achievable_duration_days=duration, crew_sizing_target_days=target,
                    message=(f"{r.get('activity_type')}: duration stays near the crew-sizing "
                             f"horizon ({duration}d vs {target}d) at {area:g}㎡ while theoretical "
                             f"demand ({theoretical}) exceeds executable workfront capacity "
                             f"({cap} = {wf} workfronts x capacity). Review physical resource "
                             f"assumptions.")))
        except (TypeError, ValueError):
            continue
    return dict(
        large_project_threshold=large_project_threshold,
        warning_count=len(warnings),
        warnings=warnings,
        status="RESOURCE_SCALING_WARNING" if warnings else "OK",
        note="Warning only — no duration is hard-coded or overridden.",
    )


# ---------------------------------------------------------------------------
# capacity status (FIX-03)
# ---------------------------------------------------------------------------
STATUS_FEASIBLE = "FEASIBLE"
STATUS_WORKFRONT_CONSTRAINED = "WORKFRONT_CONSTRAINED"
STATUS_RESOURCE_CONSTRAINED = "RESOURCE_CONSTRAINED"
STATUS_SPATIAL_CONSTRAINED = "SPATIAL_CONSTRAINED"
STATUS_CONGESTION_CONSTRAINED = "CONGESTION_CONSTRAINED"
STATUS_PRACTICAL_CREW_LIMIT = "PRACTICAL_CREW_LIMIT"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def classify_capacity(theoretical_workers: int, feasible: int,
                      physical_capacity_workers: int,
                      available_workers: Optional[int] = None,
                      congestion_factor: Optional[float] = None,
                      min_practical_crew: Optional[int] = None,
                      max_practical_crew: Optional[int] = None) -> Dict[str, Any]:
    """FIX-03: one capacity_status, plus primary/secondary constraint decomposition."""
    constraints: list = []
    if available_workers is not None and feasible > available_workers:
        constraints.append(STATUS_RESOURCE_CONSTRAINED)
    if feasible >= physical_capacity_workers and theoretical_workers > physical_capacity_workers:
        constraints.append(STATUS_WORKFRONT_CONSTRAINED)
    if congestion_factor is not None and congestion_factor < 1.0 - 1e-9:
        constraints.append(STATUS_CONGESTION_CONSTRAINED)
    if max_practical_crew is not None and theoretical_workers > max_practical_crew and feasible == max_practical_crew:
        constraints.append(STATUS_PRACTICAL_CREW_LIMIT)
    if constraints:
        status = constraints[0]
    elif available_workers is None:
        # physical capacity sufficient, but contractor availability unknown (FIX-13)
        status = STATUS_INSUFFICIENT_DATA
    else:
        status = STATUS_FEASIBLE
    return dict(
        capacity_status=status,
        primary_constraint=constraints[0] if constraints else None,
        secondary_constraints=constraints[1:] if len(constraints) > 1 else [],
    )
