"""Generate the V6.2 resource intelligence delivery report.

Outputs to output/v6_2/:
  resource_report.md              — PM-facing Chinese report
  architecture_changes.md         — what changed and why (V6.2 only)
  crew_demand.csv                 — per-task worker-days / headcount / trade / zone
  crew_conflicts.csv              — crew-in-two-zones + zone-capacity conflicts
  trade_interference.csv          — same-zone cross-trade interference rows
  scenario_comparison.json        — A / B / C comparison with resource feasibility
  zone_sweep_with_resources.csv   — zone count sweep under resource constraints
  resource_allocation_examples.md — the "why not simply add people" worked examples

The 6000 m2 demo re-uses the V6.1 template so the spatial-only numbers stay comparable.
"""
from __future__ import annotations

import copy
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import resource_model as RM                 # noqa: E402
from core import resource_optimizer as RO             # noqa: E402
from core import fragmentation as FR                  # noqa: E402
from core.fitout_engine import prepare_fitout, prepare_spatial_fitout   # noqa: E402
from core.fitout_wbs import expand_fitout_wbs         # noqa: E402
from core.solver_engine import solve_schedule         # noqa: E402
from core.optimizer import analyze_schedule           # noqa: E402
from core import duration_semantics as DS             # noqa: E402

TEMPLATES = json.loads((ROOT / "templates" / "wbs_templates.json").read_text(encoding="utf-8"))["templates"]
RATES = None  # loaded lazily
START = "2026-09-08"
DEMO_AREA = 6000.0
DEMO_TEMPLATE = "MNC_Standard_Fitout_DB_Invite"
TARGET_DATE = "2027-05-15"

CHANGED_FILES = [
    ("config/resource_constraints.json", "added",
     "拥挤系数表、班次折算（2班≠2×）、工作面/分区容量、班组池可用性、最小/最大实用人数、工种干扰矩阵、优化行为边界"),
    ("core/resource_model.py", "added",
     "工人日需求（复用 productivity 单一数据源）、班组效率与拥挤、班次折算、工作面容量、班组池、需求行与高峰人力"),
    ("core/resource_optimizer.py", "added",
     "逆向班组计算、可行性状态机、班组冲突检测、工种干扰、容量封顶、班组平衡、目标日期分析、基线不变性断言、可解释输出"),
    ("tests/test_resource_intelligence.py", "added",
     "V6.2 回归：工日、逆向班组、拥挤单调性、工作面容量、班组冲突、工种干扰、班次、物理下限、关键路径定位、基线不变性"),
    ("tools/resource_report.py", "added", "本报告的生成脚本（可复现）"),
    ("docs_and_sops/resource_intelligence.md", "added", "资源模型、判定规则、状态机、限制与暂定假设"),
]

UNCHANGED_BASELINES = [
    "config/productivity_rates.json（38 条默认率，硬保护）",
    "config/dependency_ontology.json（V6.0）",
    "config/spatial_model.json（V6.1）",
    "config/duration_semantics.json（V6.1.1）",
    "config/evidence_observations.json（V5.3）",
]


def _templates_tasks():
    return expand_fitout_wbs(copy.deepcopy(TEMPLATES[DEMO_TEMPLATE]["tasks"]))


def _solve(tasks):
    return solve_schedule(tasks, START)


def scenario_a():
    """A — baseline, single workfront, no spatial decomposition."""
    prep = prepare_fitout(_templates_tasks(), DEMO_AREA, workfronts=1, sectional="off")
    solved = _solve(prep["tasks"])
    return prep, solved


def scenario_b():
    """B — spatial only (V6.1). Mathematically fastest, resource feasibility unchecked."""
    prep = prepare_spatial_fitout(_templates_tasks(), DEMO_AREA)
    solved = _solve(copy.deepcopy(prep["tasks"]))
    return prep, solved


def scenario_c(prep_b, crews_per_trade=None):
    """C — spatial + feasible resource allocation.

    Two corrections, both of which can only make the plan MORE honest, never faster:
      1. crew right-sizing: a zone instance cannot carry more workers than the zone holds
      2. crew levelling: one crew cannot occupy two workfronts on the same day
    """
    tasks = copy.deepcopy(prep_b["tasks"])
    caps = RO.apply_workfront_capacity_caps(tasks)
    levelled = RO.level_crew_conflicts(tasks, START, crews_per_trade=crews_per_trade)
    return caps, levelled


def _finish(solved):
    return solved.get("finish_date")


def _duration_days(finish):
    return FR._days_between(START, finish) if finish else None


def _critical_count(tasks):
    try:
        a = analyze_schedule(tasks, start_date=START, verify=False, compliance_check=False)
        return len(a["critical_path"]["chain"]), [n["id"] for n in a["critical_path"]["chain"]]
    except Exception:
        return 0, []


def write_csv(path: Path, rows, fieldnames=None):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = fieldnames or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                        for k, v in r.items()})


def main():
    out = ROOT / "output" / "v6_2"
    out.mkdir(parents=True, exist_ok=True)

    cfg = RM.load_resource_constraints()
    print("[1/6] scenario A (baseline, single workfront)…")
    prep_a, solved_a = scenario_a()
    plan_a = RO.build_resource_plan(solved_a["tasks"], cfg=cfg)

    print("[2/6] scenario B (spatial only)…")
    prep_b, solved_b = scenario_b()
    plan_b = RO.build_resource_plan(solved_b["tasks"], cfg=cfg)

    print("[3/6] scenario C (spatial + feasible resources)…")
    caps, levelled = scenario_c(prep_b)
    solved_c_tasks = levelled["tasks"]
    plan_c = RO.build_resource_plan(solved_c_tasks, cfg=cfg)

    print("[4/6] target-date analysis…")
    prep_b2 = prepare_spatial_fitout(_templates_tasks(), DEMO_AREA)
    target = RO.optimise_for_target_date(prep_b2["tasks"], START, TARGET_DATE)

    print("[5/7] crew-pool sensitivity…")
    trades = ["general", "partitions_ceilings", "mep_mechanical", "mep_electrical",
              "wet_finishes", "joinery", "flooring", "commissioning", "furniture"]
    sens_rows = []
    for n in (1, 2, 3, 4):
        cp = {tr: n for tr in trades}
        prep_n = prepare_spatial_fitout(_templates_tasks(), DEMO_AREA, crews_per_trade=cp)
        solved_n = _solve(copy.deepcopy(prep_n["tasks"]))
        caps_n = RO.apply_workfront_capacity_caps(prep_n["tasks"])
        lev_n = RO.level_crew_conflicts(prep_n["tasks"], START, crews_per_trade=cp)
        plan_n = RO.build_resource_plan(lev_n["tasks"], cfg=cfg)
        sens_rows.append(dict(crews_per_trade=n,
                              raw_finish=_finish(solved_n),
                              raw_duration_days=_duration_days(_finish(solved_n)),
                              feasible_finish=lev_n["finish_date"],
                              feasible_duration_days=_duration_days(lev_n["finish_date"]),
                              levelling_edges=lev_n["edge_count"],
                              blocking_before=lev_n["blocking_before"],
                              blocking_after=lev_n["blocking_after"],
                              crew_caps_applied=len(caps_n),
                              peak_workers=plan_n["peak"]["peak_workers"],
                              naturally_feasible=lev_n["blocking_before"] == 0))
    write_csv(out / "crew_pool_sensitivity.csv", sens_rows)

    print("[6/7] zone sweep with resource constraints…")
    sweep_rows = []
    for n in (1, 2, 3, 4, 5, 6, 8):
        prep_n = prepare_spatial_fitout(_templates_tasks(), DEMO_AREA, zone_mode="manual",
                                        manual_zones=[{"zone_id": f"Z{i + 1}", "area_sqm": a}
                                                      for i, a in enumerate(FR.split_area(DEMO_AREA, n))])
        solved_n = _solve(copy.deepcopy(prep_n["tasks"]))
        plan_n = RO.build_resource_plan(solved_n["tasks"], cfg=cfg)
        caps_n = RO.apply_workfront_capacity_caps(prep_n["tasks"])
        lev_n = RO.level_crew_conflicts(prep_n["tasks"], START)
        plan_nc = RO.build_resource_plan(lev_n["tasks"], cfg=cfg)
        sweep_rows.append(dict(
            zone_count=n,
            unconstrained_finish=_finish(solved_n),
            unconstrained_duration_days=_duration_days(_finish(solved_n)),
            unconstrained_blocking_conflicts=len(plan_n["crew_conflicts"]["blocking_conflicts"]),
            resource_feasible_finish=lev_n["finish_date"],
            resource_feasible_duration_days=_duration_days(lev_n["finish_date"]),
            levelling_edges=lev_n["edge_count"],
            crew_caps_applied=len(caps_n),
            blocking_before=lev_n["blocking_before"],
            blocking_after=lev_n["blocking_after"],
            peak_workers=plan_nc["peak"]["peak_workers"],
            task_count=len(lev_n["tasks"]),
            feasible=lev_n["blocking_after"] == 0,
        ))
    write_csv(out / "zone_sweep_with_resources.csv", sweep_rows)

    print("[7/7] writing artefacts…")
    write_csv(out / "crew_demand.csv", plan_c["demand"])
    write_csv(out / "crew_conflicts.csv", plan_b["crew_conflicts"]["conflicts"])
    write_csv(out / "trade_interference.csv", plan_b["trade_interference"]["rows"])

    crit_b = _critical_count(solved_b["tasks"])
    crit_c = _critical_count(solved_c_tasks)
    comparison = dict(
        area_sqm=DEMO_AREA, template=DEMO_TEMPLATE, start_date=START,
        scenarios=[
            dict(key="A", label="基线 / 单工作面", finish=_finish(solved_a),
                 duration_days=_duration_days(_finish(solved_a)),
                 task_count=len(solved_a["tasks"]),
                 critical_chain_length=_critical_count(solved_a["tasks"])[0],
                 peak_workers=plan_a["peak"]["peak_workers"],
                 total_worker_days=plan_a["total_worker_days"],
                 blocking_conflicts=len(plan_a["crew_conflicts"]["blocking_conflicts"]),
                 zone_conflicts=len([c for c in plan_a["crew_conflicts"]["conflicts"]
                                     if c["kind"] == "workers_exceed_zone_capacity"]),
                 resource_feasible=not plan_a["crew_conflicts"]["conflicts"]),
            dict(key="B", label="仅空间（V6.1）", finish=_finish(solved_b),
                 duration_days=_duration_days(_finish(solved_b)),
                 task_count=len(solved_b["tasks"]),
                 critical_chain_length=crit_b[0],
                 peak_workers=plan_b["peak"]["peak_workers"],
                 total_worker_days=plan_b["total_worker_days"],
                 blocking_conflicts=len(plan_b["crew_conflicts"]["blocking_conflicts"]),
                 zone_conflicts=len([c for c in plan_b["crew_conflicts"]["conflicts"]
                                     if c["kind"] == "workers_exceed_zone_capacity"]),
                 resource_feasible=not plan_b["crew_conflicts"]["conflicts"]),
            dict(key="C", label="空间 + 可行资源配置（V6.2）", finish=levelled["finish_date"],
                 duration_days=_duration_days(levelled["finish_date"]),
                 task_count=len(solved_c_tasks),
                 critical_chain_length=crit_c[0],
                 peak_workers=plan_c["peak"]["peak_workers"],
                 total_worker_days=plan_c["total_worker_days"],
                 blocking_conflicts=len(plan_c["crew_conflicts"]["blocking_conflicts"]),
                 zone_conflicts=len([c for c in plan_c["crew_conflicts"]["conflicts"]
                                     if c["kind"] == "workers_exceed_zone_capacity"]),
                 resource_feasible=not plan_c["crew_conflicts"]["conflicts"],
                 crew_caps_applied=len(caps), levelling_edges=levelled["edge_count"],
                 blocking_before=levelled["blocking_before"],
                 blocking_after=levelled["blocking_after"]),
        ],
        target_date_analysis={k: v for k, v in target.items()
                              if k not in ("tasks", "activities_attempted", "skipped_non_critical")},
        zone_sweep=sweep_rows,
        crew_pool_sensitivity=sens_rows,
        provisions={
            "congestion_model": "provisional（未现场标定）",
            "shift_model": "1 班 baseline / 2 班 provisional / 夜班 insufficient_data",
            "workfront_capacity": "provisional",
            "crew_pool": "provisional（默认沿用 V6.1 单班组假设）",
            "productivity_rates": "unchanged (38, hard-protected)",
        },
    )
    (out / "scenario_comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---- worked examples for the "why not simply add people" question -------------
    ex_rows = []
    for activity, qty, tgt, zone in (("gypsum_ceiling", 2400.0, 12, 2000.0),
                                     ("gypsum_ceiling", 1200.0, 12, 1000.0),
                                     ("painting", 3600.0, 15, 2000.0),
                                     ("carpet", 2400.0, 10, 2000.0)):
        if activity not in json.loads((ROOT / "config" / "productivity_rates.json")
                                      .read_text(encoding="utf-8"))["activity_types"]:
            continue
        r = RO.reverse_crew_requirement(activity, qty, tgt, zone_area=zone)
        ex_rows.append(r)
    (out / "resource_allocation_examples.json").write_text(
        json.dumps(ex_rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    _write_report(out, cfg, comparison, plan_a, plan_b, plan_c, caps, levelled,
                  target, sweep_rows, sens_rows, ex_rows, crit_b, crit_c,
                  solved_a, solved_b, solved_c_tasks)
    _write_architecture(out)
    print(f"done → {out}")


def _write_report(out, cfg, comparison, plan_a, plan_b, plan_c, caps, levelled,
                  target, sweep_rows, sens_rows, ex_rows, crit_b, crit_c, solved_a, solved_b, solved_c):
    L = []
    add = L.append
    A = {s["key"]: s for s in comparison["scenarios"]}
    add("# V6.2 资源智能 — 6000㎡ 演示报告")
    add("")
    add(f"- 模板：`{DEMO_TEMPLATE}`　面积：**{DEMO_AREA:g} ㎡**　开工：`{START}`")
    add(f"- 生成脚本：`tools/resource_report.py`（可复现）")
    add("- 生产率唯一来源：`config/productivity_rates.json`（38 条，未改动）")
    add("")
    add("---")
    add("")
    add("## 1. 结论先行")
    add("")
    add(f"1. **B（仅空间）不是可执行方案**：完成日 {A['B']['finish']}，"
        f"但存在 **{A['B']['blocking_conflicts']} 组班组冲突**"
        f"（同一班组同一天被要求出现在两个分区）+ **{A['B']['zone_conflicts']} 组分区容量超限**。"
        f"数学上最快 ≠ 现场能建。")
    add(f"2. **C（空间 + 可行资源）完成日 {A['C']['finish']}**，比 B 晚 "
        f"{(A['C']['duration_days'] - A['B']['duration_days']):+d} 天 —— 这是为可行性付出的真实代价，"
        f"不是保守估计。")
    add(f"3. **高峰人力**：A {A['A']['peak_workers']} 人 → B {A['B']['peak_workers']} 人 → "
        f"C {A['C']['peak_workers']} 人。B 的高峰来自 V6.1 按项目级工程量给每个分区实例配同一套班组，"
        f"资源引擎如实暴露了它。")
    _gb = target["critical_chain_gap_breakdown"]
    _chain_total = sum(_gb[k] for k in ("gate_days", "fixed_template_days",
                                        "production_crashable_days", "mobilisation_days", "milestone_days"))
    add(f"4. **目标日 {TARGET_DATE} 不可通过加人达成**：缺口 {target['gap_days']} 天，"
        f"人力压缩贡献 **{target['achieved_saving_days']} 天**。关键链 "
        f"{_gb['chain_task_count']} 项共 {_chain_total} 天，其中 "
        f"{_gb['fixed_template_days']} 天是设计/图审/法定审批/散味 SOP 等固定工期，加人无效。")
    add("")
    add("---")
    add("")
    add("## 2. 三方案对比")
    add("")
    add("| 指标 | A 基线/单工作面 | B 仅空间（V6.1） | C 空间+可行资源（V6.2） |")
    add("|---|---:|---:|---:|")
    for label, key in (("完成日期", "finish"), ("总工期(日历天)", "duration_days"), ("任务数", "task_count"),
                       ("关键链长度", "critical_chain_length"), ("高峰人力(人)", "peak_workers"),
                       ("总工日", "total_worker_days"), ("班组冲突(阻断)", "blocking_conflicts"),
                       ("分区容量超限", "zone_conflicts")):
        fmt = (lambda v: f"{v:g}" if isinstance(v, (int, float)) else (v or "—"))
        add(f"| {label} | {fmt(A['A'][key])} | {fmt(A['B'][key])} | {fmt(A['C'][key])} |")
    add(f"| 资源可行 | {'是' if A['A']['resource_feasible'] else '否'} | "
        f"{'是' if A['B']['resource_feasible'] else '否'} | "
        f"{'是' if A['C']['resource_feasible'] else '否'} |")
    add("")
    add(f"C 施加了 **{len(caps)} 处人数封顶**（投入人数 > 分区物理容量）与 "
        f"**{levelled['edge_count']} 条班组交接边**（同一班组不能同时在两个作业面）。"
        f"阻断冲突 {levelled['blocking_before']} → **{levelled['blocking_after']}**。")
    add("")
    add("### C 的人数封顶（前 10）")
    add("")
    add("| 任务 | 工序 | 工种 | 分区面积 | 原人数 | 封顶后 | 工期变化 |")
    add("|---|---|---|---:|---:|---:|---:|")
    for c in sorted(caps, key=lambda x: -x["days_added"])[:10]:
        add(f"| #{c['task_id']} {str(c['name'])[:20]} | {c['activity_type']} | {c['trade']} | "
            f"{c['zone_area_sqm'] or '—'} | {c['previous_workers']} | {c['capped_workers']} | "
            f"{c['previous_duration_days']} → {c['new_duration_days']} (+{c['days_added']}) |")
    add("")
    add("---")
    add("")
    add("## 3. 为什么不能简单加人（拥挤模型）")
    add("")
    add("有效产出 = 基础生产率 × 人数 × **拥挤系数(人数)**。人数 1 时系数恒为 1.0，"
        "保证与既有生产率口径连续；随人数上升单调下降。")
    add("")
    add("| 人数 | 拥挤系数 | 有效人数 | 边际产出/人 |")
    add("|---:|---:|---:|---:|")
    sub = RM.assert_sublinear(cfg)
    for r in sub["rows"]:
        add(f"| {r['workers']} | {r['efficiency']:g} | {r['effective_workers']:g} | "
            f"{r['marginal_output_per_worker'] if r['marginal_output_per_worker'] is not None else '—'} |")
    add("")
    add(f"断言：{sub['assertion']} → **{'通过' if sub['assertion_pass'] else '不通过'}**")
    add("")
    add("### 反算实例")
    add("")
    for r in ex_rows:
        add("```")
        add(RO.explain_allocation(r))
        add("```")
        add("")
    add("---")
    add("")
    add("## 4. 班组冲突与工种干扰")
    add("")
    add(f"- B 方案阻断型冲突（同一班组同日在多分区）：**{A['B']['blocking_conflicts']}**")
    add(f"- B 方案分区容量超限：**{A['B']['zone_conflicts']}**")
    add(f"- C 方案阻断型冲突：**{A['C']['blocking_conflicts']}**（已消除）")
    add("")
    add("### 各工种峰值并发与可用班组（C 方案）")
    add("")
    add("| 工种 | 作业面数 | 峰值并发班组 | 可用班组 | 利用率 | 峰值人数 | 状态 |")
    add("|---|---:|---:|---:|---:|---:|---|")
    for t in plan_c["crew_conflicts"]["by_trade"]:
        add(f"| {t['trade']} | {t['workfronts']} | {t['peak_concurrent_crews']} | "
            f"{t['available_crews']} | {t['crew_utilisation']} | {t['peak_concurrent_workers']} | {t['status']} |")
    add("")
    ic = plan_b["trade_interference"]["counts"]
    add(f"### 工种干扰（B 方案，同区同时作业）：medium {ic['medium']} / high {ic['high']} / "
        f"blocked {ic['blocked']}")
    add("")
    top = plan_b["trade_interference"]["rows"][:8]
    if top:
        add("| 工种组合 | 等级 | 分区 | 重叠(天) | 原因 |")
        add("|---|---|---|---:|---|")
        for r in top:
            add(f"| {r['pair']} | {r['level']} | {r['zone']} | {r['overlap_days']} | {str(r['reason'])[:44]} |")
        add("")
    add("---")
    add("")
    add("## 5. 目标日期分析")
    add("")
    add(f"- 目标完成日：**{TARGET_DATE}**；B 方案完成：{target['baseline_finish']}；"
        f"缺口 **{target['gap_days']} 天**")
    add(f"- 近关键阈值：{target['near_critical_float_days']} 天；"
        f"单次人数增长上限：{target['max_crew_growth_factor']:g}×")
    add(f"- 驱动链生产活动 **{len(target['activities_attempted'])}** 项被评估，"
        f"实际可压缩 **{len(target['activities'])}** 项，节省 **{target['achieved_saving_days']} 天**")
    add(f"- 未投入资源的非关键活动：**{len(target['skipped_non_critical'])}** 项")
    add(f"- 基线不变性：{'通过' if target['baseline_unchanged']['assertion_pass'] else '不通过'}"
        f"（比对 {target['baseline_unchanged']['checked']} 条任务的工期）")
    add("")
    gb = target["critical_chain_gap_breakdown"]
    add("### 关键链工期构成（为什么加人换不来工期）")
    add("")
    add("| 类别 | 天数 | 能否用人力压缩 |")
    add("|---|---:|---|")
    add(f"| 固定工期（设计/图审/报建/散味 SOP/保洁） | {gb['fixed_template_days']} | 否 |")
    add(f"| 可压缩生产活动 | {gb['production_crashable_days']} | 是（受容量与物理下限约束） |")
    add(f"| 检查门 | {gb['gate_days']} | 否 |")
    add(f"| 动员 | {gb['mobilisation_days']} | 否 |")
    add(f"| 里程碑 | {gb['milestone_days']} | 否 |")
    add("")
    add("---")
    add("")
    add("## 6. 分区数扫描：加入资源约束后 5 区还最快吗？")
    add("")
    add("| 分区数 | 无约束完成日 | 无约束工期 | 阻断冲突 | 资源可行完成日 | 可行工期 | 平衡边 | 封顶 | 高峰人力 |")
    add("|---:|---|---:|---:|---|---:|---:|---:|---:|")
    for r in sweep_rows:
        add(f"| {r['zone_count']} | {r['unconstrained_finish']} | {r['unconstrained_duration_days']} | "
            f"{r['unconstrained_blocking_conflicts']} | {r['resource_feasible_finish']} | "
            f"{r['resource_feasible_duration_days']} | {r['levelling_edges']} | {r['crew_caps_applied']} | "
            f"{r['peak_workers']} |")
    add("")
    ubest = min(sweep_rows, key=lambda r: (r["unconstrained_duration_days"] or 1e9))
    cbest = min(sweep_rows, key=lambda r: (r["resource_feasible_duration_days"] or 1e9))
    add(f"- **无约束最快：{ubest['zone_count']} 区**（{ubest['unconstrained_duration_days']} 天）—— 与 V6.1.1 结论一致")
    add(f"- **资源可行最快：{cbest['zone_count']} 区**（{cbest['resource_feasible_duration_days']} 天）")
    feas = [(r["zone_count"], r["resource_feasible_duration_days"]) for r in sweep_rows
            if r["resource_feasible_duration_days"]]
    worse = [(a[0], b[0]) for a, b in zip(feas, feas[1:]) if b[1] > a[1]]
    if worse:
        add(f"- 资源可行曲线**非单调**：{', '.join(f'{a} 区 < {b} 区（更多分区反而更慢）' for a, b in worse[:3])}。"
            f"分区越多越好的假设在资源约束下不成立。")
    add(f"- 资源可行域整体（{min(d for _, d in feas)}–{max(d for _, d in feas)} 天）仍显著慢于 "
        f"无约束结果（{min(r['unconstrained_duration_days'] for r in sweep_rows)} 天）："
        f"缺口来自班组数不足与分区容量封顶，不是分区策略本身。")
    if ubest["zone_count"] != cbest["zone_count"]:
        add(f"- **结论：加入资源约束后最优分区数从 {ubest['zone_count']} 变为 {cbest['zone_count']}。"
            f"V6.1.1 的\"数学最快\"在资源可行域内不成立。**")
    else:
        add("- 结论：资源可行域内最优分区数与无约束一致，但该结论只在当前（临时假设的）班组池下成立。")
    add("")
    add("---")
    add("")
    add("## 7. 班组敏感性：需要几个班组才能兑现空间方案？")
    add("")
    add("分区并行的前提是有足够的班组。班组池默认沿用 V6.1 的单班组假设，"
        "下表把每工种班组数作为**情景变量**扫描（临时假设，需确认）。")
    add("")
    add("| 每工种班组数 | 未平衡完成日 | 阻断冲突 | 可行完成日 | 可行工期 | 平衡边 | 高峰人力 |")
    add("|---:|---|---:|---|---:|---:|---:|")
    for r in sens_rows:
        add(f"| {r['crews_per_trade']} | {r['raw_finish']} | {r['blocking_before']} | "
            f"{r['feasible_finish']} | {r['feasible_duration_days']} | {r['levelling_edges']} | "
            f"{r['peak_workers']} |")
    add("")
    first_natural = next((r for r in sens_rows if r["naturally_feasible"]), None)
    if first_natural:
        add(f"**首个自然可行（无需强制平衡）的班组数：每工种 {first_natural['crews_per_trade']} 个**，"
            f"可行工期 {first_natural['feasible_duration_days']} 天。再往上加班组收益迅速递减。")
    else:
        add("1–4 个班组都需要强制平衡才能可行。")
    add("")
    add("---")
    add("")
    add("## 8. 已知限制与暂定假设")
    add("")
    add("| 假设 | 状态 | 影响 |")
    add("|---|---|---|")
    add("| 拥挤系数表 | provisional / confirmed_by=null | 反算人数与可达工期全部依赖它；未现场标定 |")
    add("| 2 班产出 1.75× | provisional / confirmed_by=null | 若改为 2.0×，所有班次场景工期会缩短约 12% |")
    add("| 夜班 | insufficient_data | 不产出结果，拒绝猜测 |")
    add("| 工作面/分区容量 | provisional / confirmed_by=null | 决定人数封顶位置 |")
    add("| 班组池 = 1 班组/工种 | V6.1 单班组假设，非承包商承诺 | 决定能否分区并行 |")
    add("| 工种干扰矩阵 | 仅覆盖核心装修工种 | 未列组合一律 low（保守缺省，非验证结论）|")
    add("| 生产率率值 | rate_status=provisional, 证据 L0 | 所有工日需求的基数 |")
    add("")
    add("### V6.2 明确不做")
    add("")
    add("- 完整资源优化/平衡求解器（只做可行性诊断与确定性修正）")
    add("- 成本优化（不引入单价与成本曲线）")
    add("- UI / MPP 重设计")
    add("- 修改证据基线或 productivity_rates")
    add("- 由模型代替计划工程师决定人力（所有临时因子显式标注）")
    add("")
    add("---")
    add("")
    add("## 9. 产出文件")
    add("")
    add("| 文件 | 内容 |")
    add("|---|---|")
    add("| `crew_demand.csv` | 每个任务的工日/工时、投入人数、有效人数、工种、分区 |")
    add("| `crew_conflicts.csv` | 班组冲突与分区容量超限明细（含任务、分区、日期、缺口） |")
    add("| `trade_interference.csv` | 同区同期的工种干扰明细 |")
    add("| `scenario_comparison.json` | A/B/C 完整对比 + 目标日期分析 |")
    add("| `zone_sweep_with_resources.csv` | 分区数 × 资源约束扫描 |")
    add("| `crew_pool_sensitivity.csv` | 每工种班组数 × 可行工期 |")
    add("| `resource_allocation_examples.json` | 反算实例（含可解释分解） |")
    add("| `architecture_changes.md` | 本版架构变更 |")
    add("")
    (out / "resource_report.md").write_text("\n".join(L), encoding="utf-8")


def _write_architecture(out):
    L = ["# V6.2 架构变更", "",
         "## 新增", "", "| 文件 | 说明 |", "|---|---|"]
    for path, kind, desc in CHANGED_FILES:
        L.append(f"| `{path}` | {kind} — {desc} |")
    L += ["", "## 未改动的冻结基线", ""]
    for b in UNCHANGED_BASELINES:
        L.append(f"- {b}")
    L += ["", "## 资源模型结构（config/resource_constraints.json）", "",
          "```",
          "resource_constraints.json",
          "├── congestion_model      人数 → 拥挤系数表（1 人 = 1.0，单调不增，越界 clamp）",
          "├── shift_model           1 班 baseline / 2 班 1.75× provisional / 夜班 insufficient_data",
          "├── workfront_capacity    分区面积 → 可用工作面数 × 每面人数 → 分区物理容量（可按工种覆盖）",
          "├── crew_pool             crew_id / 每班组人数 / 可用班组数 / 分配楼层与分区 / 可用窗口 / 最小-最大实用人数",
          "├── trade_interference    low / medium / high / blocked 矩阵（核心装修工种）",
          "└── optimisation          近关键阈值、人数增长上限、永不压缩对象、物理下限来源",
          "```", "",
          "## 核心公式（与 V5.3/V6.1.1 共用同一生产率数据源）", "",
          "```",
          "worker_days   = quantity × Πfactors ÷ (rate × workfront_efficiency)      # 单一来源：productivity_rates.json",
          "effective_out = workers × congestion(workers) × shift_multiplier(shifts)",
          "raw_duration  = worker_days ÷ effective_out",
          "final         = max(物理下限[V6.1.1 作用域], ceil(raw_duration))",
          "```", "",
          "## 状态机", "",
          "| 状态 | 触发条件 |",
          "|---|---|",
          "| FEASIBLE | 可行人数下的可达工期 ≤ 目标工期 |",
          "| RESOURCE_CONSTRAINED | 班组池/实用上限不允许投入所需人数 |",
          "| WORKFRONT_CONSTRAINED | 分区物理容量不允许投入所需人数 |",
          "| PHYSICAL_MINIMUM | 已触及 V6.1.1 声明的物理最短工期，加人无效 |",
          "| INSUFFICIENT_DATA | 班次/夜班产出无证据，拒绝猜测 |", ""]
    (out / "architecture_changes.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
