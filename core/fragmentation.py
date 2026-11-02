"""V6.1.1 spatial fragmentation diagnostics.

Splits one project into 1, 2, 3, … N zones and reports BOTH curves side by side:

    benefit   — inter-zone pipeline overlap pulls the finish date in
    cost      — rounding, per-zone minimums and the loss of long continuous runs
                push it back out

More zones is NOT assumed to be better. The sweep simply makes the trade-off visible
and locates the point of diminishing returns. Deciding a zone count also needs physical
workfront capacity, available crews, congestion, access/logistics and a minimum
practical zone size — none of which exist in V6.1.1, so the "recommendation" here is
explicitly labelled as requiring V6.2 inputs.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from core import duration_semantics as DS
from core.zone_model import split_area


def _days_between(start: str, end: str) -> Optional[int]:
    try:
        a = date.fromisoformat(str(start)[:10])
        b = date.fromisoformat(str(end)[:10])
    except (TypeError, ValueError):
        return None
    return (b - a).days


def _quantity_drift(plan: Dict[str, Any]) -> float:
    """Σ|Σzone quantity − project quantity| over every apportioned quantity. Must be 0."""
    total = 0.0
    for issue in plan.get("allocation_issues") or []:
        total += abs(float(issue.get("delta", 0) or 0))
    return round(total, 6)


def run_one(tasks: Sequence[Dict[str, Any]], area_sqm: float, start_date: str,
            zone_count: int, semantics: Optional[Dict[str, Any]] = None,
            **context) -> Dict[str, Any]:
    """Build + solve one n-zone variant and return its diagnostic row."""
    from core.fitout_engine import prepare_spatial_fitout
    from core.solver_engine import solve_schedule

    areas = split_area(float(area_sqm), int(zone_count))
    manual = [{"zone_id": f"Z{i + 1}", "area_sqm": a} for i, a in enumerate(areas)]
    prepared = prepare_spatial_fitout(list(tasks), float(area_sqm), zone_mode="manual",
                                      manual_zones=manual, semantics=semantics, **context)
    solved = solve_schedule(prepared["tasks"], start_date)
    plan = prepared["plan"]
    totals = (plan.get("duration_diagnostics") or {}).get("totals") or {}
    finish = solved.get("finish_date")
    return dict(
        zone_count=int(zone_count),
        zone_areas=[round(float(a), 2) for a in areas],
        finish_date=finish,
        cpm_duration_days=_days_between(start_date, finish) if finish else None,
        task_count=len(prepared["tasks"]),
        raw_calculated_days=totals.get("raw_calculated_days", 0.0),
        rounded_days=totals.get("rounded_days", 0),
        final_days=totals.get("final_days", 0),
        rounding_penalty_days=totals.get("rounding_penalty_days", 0.0),
        minimum_duration_penalty_days=totals.get("minimum_duration_penalty_days", 0),
        legacy_unclassified_min_penalty_days=totals.get("legacy_min_penalty_days", 0),
        quantity_drift=_quantity_drift(plan),
        graph_issues=len(plan.get("graph_issues") or []),
        compliance_errors=len([i for i in (plan.get("compliance_issues") or [])
                               if i.get("level") == "error"]),
        aggregate_underrun_warnings=len(plan.get("duration_semantics", {}).get("aggregate_checks") or []),
    )


def sweep_zone_counts(tasks: Sequence[Dict[str, Any]], area_sqm: float, start_date: str,
                      zone_counts: Optional[Sequence[int]] = None,
                      semantics: Optional[Dict[str, Any]] = None,
                      **context) -> Dict[str, Any]:
    """Run the 1..N zone sweep. Returns {rows, analysis, config}."""
    cfg = semantics or DS.load_duration_semantics()
    counts = list(zone_counts) if zone_counts else list(
        (cfg.get("fragmentation_sweep") or {}).get("default_zone_counts") or [1, 2, 3, 4])
    rows: List[Dict[str, Any]] = []
    for n in counts:
        row = run_one(tasks, area_sqm, start_date, int(n), semantics=cfg, **context)
        rows.append(row)
    rows.sort(key=lambda r: r["zone_count"])
    analysis = analyse_sweep(rows, cfg)
    return dict(area_sqm=float(area_sqm), start_date=start_date,
                semantics_version=cfg.get("version"), rows=rows, analysis=analysis)


def analyse_sweep(rows: Sequence[Dict[str, Any]],
                  cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Marginal savings, monotonicity and the point of diminishing returns."""
    cfg = cfg or DS.load_duration_semantics()
    sweep_cfg = cfg.get("fragmentation_sweep") or {}
    threshold = float(sweep_cfg.get("diminishing_return_threshold_days", 1) or 0)

    rows = sorted(rows, key=lambda r: r["zone_count"])
    marginal: List[Dict[str, Any]] = []
    previous: Optional[Dict[str, Any]] = None
    for row in rows:
        saving = None
        if previous is not None and row["cpm_duration_days"] is not None and previous["cpm_duration_days"] is not None:
            saving = previous["cpm_duration_days"] - row["cpm_duration_days"]
        marginal.append(dict(zone_count=row["zone_count"], finish_date=row["finish_date"],
                             cpm_duration_days=row["cpm_duration_days"],
                             marginal_saving_days=saving,
                             rounding_penalty_days=row["rounding_penalty_days"],
                             minimum_duration_penalty_days=row["minimum_duration_penalty_days"],
                             legacy_unclassified_min_penalty_days=row["legacy_unclassified_min_penalty_days"],
                             task_count=row["task_count"]))
        previous = row

    savings = [m["marginal_saving_days"] for m in marginal if m["marginal_saving_days"] is not None]
    negative = [m for m in marginal if (m["marginal_saving_days"] or 0) < 0]
    finished = [r for r in rows if r["cpm_duration_days"] is not None]
    best = min(finished, key=lambda r: (r["cpm_duration_days"], r["zone_count"])) if finished else None
    worst = max(finished, key=lambda r: (r["cpm_duration_days"], r["zone_count"])) if finished else None

    # Diminishing returns = the point after which adding zones never again buys more than
    # `threshold` days. Scanning from the end (not from the start) is deliberate: the
    # marginal curve is not monotone, so "first dip" would fire on rounding noise.
    diminishing_from = None
    last_worthwhile = None
    for idx, m in enumerate(marginal):
        s = m["marginal_saving_days"]
        if s is not None and s > threshold:
            last_worthwhile = idx
    if last_worthwhile is not None and last_worthwhile + 1 < len(marginal):
        diminishing_from = marginal[last_worthwhile + 1]["zone_count"]
    elif last_worthwhile is None and marginal:
        diminishing_from = marginal[0]["zone_count"]

    monotonic = all(s >= 0 for s in savings)
    # A smooth physical curve would give non-increasing marginal savings. When the
    # marginal saving goes back UP after falling, that extra "gain" is integer-division
    # luck (e.g. 10 days of work splits exactly into 5x2 but not 3x or 4x), not physics.
    non_smooth = any(b > a for a, b in zip(savings, savings[1:]))
    if best and worst:
        spread = worst["cpm_duration_days"] - best["cpm_duration_days"]
    else:
        spread = None

    observation = (
        f"分区从 {rows[0]['zone_count']} 增到 {rows[-1]['zone_count']}："
        f"最短 {best['cpm_duration_days']} 天（{best['zone_count']} 分区），"
        f"最长 {worst['cpm_duration_days']} 天（{worst['zone_count']} 分区），"
        f"极差 {spread} 天。" if (best and worst) else "无可比较结果。")
    if negative:
        observation += ("⚠ 存在「分区增加反而更慢」的区段（"
                        + "；".join(f"{m['zone_count']} 分区 {m['marginal_saving_days']} 天" for m in negative)
                        + "）。原因是取整惩罚与分区级最短工期随分区数线性累加，吞掉了流水重叠的收益；"
                          "正确处置是减少分区数，而不是放宽工期下限。")
    else:
        observation += "本区间内未出现「分区增加反而更慢」，但收益递减点仍需结合 V6.2 资源输入判断。"

    return dict(
        threshold_days=threshold,
        marginal=marginal,
        best=best,
        worst=worst,
        monotonic_improvement=monotonic,
        more_zones_not_always_better=bool(negative),
        negative_segments=negative,
        finish_curve_non_smooth=non_smooth,
        non_smooth_note=(
            "边际节省出现回升，说明部分「收益」来自整数除法的运气（例如 10 天工程量在 5 分区恰好 "
            "5×2 天无取整损失，3/4 分区反而各段向上取整），不是施工组织物理规律。"
            "不得把扫描出的最优分区数当作物理结论。" if non_smooth else ""),
        diminishing_returns_from_zone_count=diminishing_from,
        finish_spread_days=spread,
        observation=observation,
        recommendation=dict(
            zone_count=(best["zone_count"] if best else None),
            basis="仅按 CPM 完成日期最短",
            status="DIAGNOSTIC_ONLY",
            requires_v6_2_inputs=True,
            missing_inputs=(sweep_cfg.get("missing_inputs_for_real_recommendation_zh")
                            or sweep_cfg.get("missing_inputs_for_real_recommendation") or []),
            note=("此数字只说明「按当前模型，几区最短」，不构成施工组织建议。"
                  "真实分区数还需物理工作面容量、可用班组、拥堵、通行物流与最小可实施分区规模，"
                  "这些输入由 V6.2 资源/班组优化提供。"),
        ),
    )


def explain_sweep(sweep: Dict[str, Any]) -> List[str]:
    a = sweep["analysis"]
    lines = [f"分区碎片化扫描（{sweep['area_sqm']:g}㎡，起点 {sweep['start_date']}）：",
             "| 分区 | 完成日期 | CPM 工期 | 边际节省 | 取整惩罚 | 下限惩罚 | 任务数 |",
             "|---|---|---|---|---|---|---|"]
    for m in a["marginal"]:
        saving = "—" if m["marginal_saving_days"] is None else f"{m['marginal_saving_days']:+d}"
        lines.append(f"| {m['zone_count']} | {m['finish_date']} | {m['cpm_duration_days']} 天 | {saving} 天 | "
                     f"{m['rounding_penalty_days']:g} 天 | {m['minimum_duration_penalty_days']} 天 | {m['task_count']} |")
    lines.append("")
    lines.append(a["observation"])
    if a.get("non_smooth_note"):
        lines.append(a["non_smooth_note"])
    rec = a["recommendation"]
    lines.append(f"仅按 CPM 最短：{rec['zone_count']} 分区（{rec['status']}，"
                 f"缺 V6.2 输入：{'、'.join(rec['missing_inputs'])}）。")
    return lines
