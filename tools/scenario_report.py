"""Generate the V6.3 scenario optimizer report.

Outputs to output/v6_3/:
  scenario_report.md          — PM-facing Chinese report
  scenario_register.csv       — one row per scenario: levers / finish / peak / conflicts
  scenario_comparison.csv     — side-by-side table (never ranked)
  scenario_preconditions.csv  — what each scenario bets on, with provenance
  dominance_front.json        — Pareto candidate set (NOT a recommendation)
  recommendation.json         — recommended=null is the expected, correct output
  promotion_requirements.json — what must be confirmed before any scenario may be labelled

Design rule (user directive): this round does NOT try to produce a "Recommended Scenario".
If the resource data is not contractor-confirmed, every scenario stays UNCONFIRMED / OPTION
and `recommended = null` is the terminal answer, not a placeholder.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import resource_governance as RG                      # noqa: E402
from core import resource_model as RM                           # noqa: E402
from core import scenario_optimizer as SO                       # noqa: E402
from core.fitout_engine import prepare_spatial_fitout           # noqa: E402
from core.fitout_wbs import expand_fitout_wbs                   # noqa: E402
from core.zone_model import split_area                          # noqa: E402

TEMPLATES = json.loads((ROOT / "templates" / "wbs_templates.json").read_text(encoding="utf-8"))["templates"]
START = "2026-09-08"
DEMO_AREA = 6000.0
DEMO_TEMPLATE = "MNC_Standard_Fitout_DB_Invite"
TRADES = ["general", "partitions_ceilings", "mep_mechanical", "mep_electrical",
          "wet_finishes", "joinery", "flooring", "commissioning", "furniture"]

CHANGED_FILES = [
    ("config/scenario_space.json", "added",
     "显式枚举 7 个情景（分区数 × 班组池 × 班次三杠杆）、杠杆证据状态、比较轴与不排序规则、"
     "GOV-10..14 场景治理规则、晋升所需确认清单。情景只能『选择』已声明取值，不得改写物理约束。"),
    ("core/scenario_optimizer.py", "added",
     "场景运行/分类、前置条件与证据状态、可比性门控（GOV-13）、Pareto 并列候选集、"
     "无推荐输出（GOV-10/GOV-14）、晋升所需确认清单。直接消费 resource_readiness()。"),
    ("core/resource_sensitivity.py", "modified",
     "把 _run_at_crew_count 的执行体提取为公开的 run_tasks()，供 V6.3 复用；行为与输出不变。"),
    ("tests/test_scenario_optimizer.py", "added",
     "V6.3 回归：无推荐门控、可比性拒绝排序、Pareto 非推荐、情景不得改写物理约束、基线不变性、冻结基线 SHA。"),
    ("tools/scenario_report.py", "added", "本报告生成脚本（可复现）"),
    ("docs_and_sops/scenario_optimizer.md", "added", "情景定义、杠杆、治理规则、无推荐原则与已知限制"),
]

UNCHANGED_BASELINES = [
    "config/productivity_rates.json（38 条默认率）",
    "config/dependency_ontology.json（V6.0）",
    "config/spatial_model.json（V6.1）",
    "config/duration_semantics.json（V6.1.1）",
    "config/evidence_observations.json（V5.3）",
    "congestion_model 拥挤系数表数值",
    "shift_model 2 班 1.75× 数值",
    "workfront_capacity 各工种容量数值",
]


def _template_tasks():
    return expand_fitout_wbs(copy.deepcopy(TEMPLATES[DEMO_TEMPLATE]["tasks"]))


def _build(levers):
    """Build a FRESH task list for one resolved lever set."""
    tasks = _template_tasks()
    mult = levers["shifts"]["applied_multiplier"]
    if mult and float(mult) != 1.0:
        for t in tasks:
            if t.get("activity_type") and not t.get("inspection_gate"):
                t["shifts"] = float(mult)
    zc = levers["zone_count"]["value"]
    manual = None
    if zc:
        manual = [{"zone_id": f"Z{i + 1}", "area_sqm": a}
                  for i, a in enumerate(split_area(DEMO_AREA, int(zc)))]
    res = prepare_spatial_fitout(tasks, DEMO_AREA,
                                 zone_mode=("manual" if manual else "auto"),
                                 manual_zones=manual,
                                 crews_per_trade=levers["crews_per_trade"]["value"])
    return res["tasks"]


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


def run_regressions():
    proc = subprocess.run([sys.executable, "-X", "utf8", "tools/run_regressions.py"],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=3600)
    return (proc.stdout or "") + ("\n[stderr]\n" + proc.stderr if proc.stderr else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-regression", action="store_true")
    args = ap.parse_args()

    out = ROOT / "output" / "v6_3"
    out.mkdir(parents=True, exist_ok=True)
    cfg = RM.load_resource_constraints()
    space = SO.load_scenario_space(cfg=cfg)

    print("[1/6] scenario space validation…")
    val = SO.validate_scenario_space(space, cfg)
    print(f"    {val['scenario_count']} 情景，规则 {val['rules']}")

    print("[2/6] running scenarios on 6000 m2…")
    rows = []
    for scn in SO.scenarios(space):
        row = SO.run_scenario(scn, _build, START, TRADES, cfg=cfg, space=space)
        row.pop("_run", None)
        rows.append(row)
        if row.get("status") == RM.INSUFFICIENT_DATA:
            print(f"    {row['id']}: INSUFFICIENT_DATA — {row.get('blocking_reason')}")
        else:
            print(f"    {row['id']} | 分区{row['zone_count']} | {row['crews_per_trade']}班组 | "
                  f"{row['shifts']}班 | {row['finish']} | {row['duration_days']}天 | "
                  f"高峰{row['peak_workers']} | {row['status']} | {row['classification']}")

    print("[3/6] comparison (no ranking) & Pareto candidate set…")
    comparison = SO.compare_scenarios(rows, cfg, space)
    front = SO.dominance_front(rows, cfg, space)

    print("[4/6] lever isolation (one lever at a time)…")
    effects = {lv: SO.lever_effect(rows, lv) for lv in SO.LEVER_KEYS}
    eff_rows = []
    for lv, eff in effects.items():
        eff_rows.extend(eff["rows"])
        for r in eff["rows"]:
            print(f"    {lv}: {r['from_value']}→{r['to_value']} "
                  f"({r['from_id']}→{r['to_id']}) {r['from_duration_days']}→{r['to_duration_days']} 天 "
                  f"(-{r['saved_days']} / {r['saved_pct']}%) 高峰 {r['peak_workers_from']}→{r['peak_workers_to']}")

    print("[5/6] recommendation…")
    rec = SO.scenario_recommendation(rows, cfg, space)
    print(f"    recommended = {rec['recommended']!r}  label = {rec['label']}  "
          f"terminal = {rec.get('is_terminal_state')}")
    promo = SO.promotion_requirements(space, cfg)

    print("[5/6] writing artifacts…")
    (out / "recommendation.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "dominance_front.json").write_text(
        json.dumps(front, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "promotion_requirements.json").write_text(
        json.dumps(promo, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "scenario_comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    write_csv(out / "scenario_register.csv", rows,
              fieldnames=["id", "label", "role", "zone_count", "crews_per_trade", "shifts",
                          "shift_multiplier_applied", "finish", "duration_days", "peak_workers",
                          "total_worker_days", "blocking_conflicts_before",
                          "blocking_conflicts_after", "capacity_violations", "levelling_edges",
                          "workfront_utilisation_max", "task_count", "status",
                          "evidence_state", "classification", "unconfirmed_dependency_count"])
    write_csv(out / "scenario_comparison.csv", comparison["table"])
    write_csv(out / "scenario_lever_effects.csv", eff_rows)
    pre_rows = []
    for r in rows:
        for p in r.get("preconditions", []):
            pre_rows.append(dict(scenario_id=r["id"], **p))
    write_csv(out / "scenario_preconditions.csv", pre_rows)

    print("[6/6] regression…")
    regression = "(skipped)" if args.no_regression else run_regressions()

    _write_report(out, space, cfg, val, rows, comparison, front, rec, promo, effects, regression)
    print(f"done → {out}")


def _write_report(out, space, cfg, val, rows, comparison, front, rec, promo, effects, regression):
    L = []
    add = L.append
    readiness = RG.resource_readiness(cfg)

    add("# V6.3 场景优化器")
    add("")
    add(f"- 演示：`{DEMO_TEMPLATE}`　面积：**{DEMO_AREA:g} ㎡**　开工：`{START}`")
    add(f"- 情景数：**{len(rows)}**　杠杆：分区数 / 班组池 / 班次")
    add(f"- 资源输入就绪度：**`{readiness['overall']}`**")
    add(f"- **本轮推荐：`{rec['recommended']}`（`{rec['label']}`）**")
    add("")
    add("> 本轮不产出推荐情景。所有情景在数学上可行，但承包商资源可用性尚未确认 —— ")
    add("> 这是当前证据下的正确结论，不是缺数据。宁可没有推荐，也不要假装有确定答案。")
    add("")

    add("## 1. 修改过的文件")
    add("")
    add("| 文件 | 变更 | 说明 |")
    add("|---|---|---|")
    for f, kind, note in CHANGED_FILES:
        add(f"| `{f}` | {kind} | {note} |")
    add("")
    add("**未改动基线（SHA 校验）**：" + "、".join(f"`{b}`" for b in UNCHANGED_BASELINES[:5]))
    add("")
    add("数值类未改动项：" + "、".join(f"`{b}`" for b in UNCHANGED_BASELINES[5:]) + "。")
    add("")

    add("## 2. 为什么没有推荐")
    add("")
    add(f"- `recommended` = **null**，`is_terminal_state` = **true**，`is_missing_data` = **false**")
    add(f"- 原因：{rec['reason']}")
    add(f"- 结论句：{rec['statement']}")
    add(f"- 未确认的资源节：{', '.join(f'`{s}`' for s in rec['unconfirmed_sections']) or '—'}")
    if rec.get("shortest_duration_id"):
        add(f"- 读数（**不是推荐**）：最短工期情景 `{rec['shortest_duration_id']}` = "
            f"{rec['shortest_duration_days']} 天")
    add("")
    add("GOV-14：禁止用『最短工期』『最小高峰人力』等单一指标充当推荐。")
    add("GOV-10：只有 `resource_readiness.overall == confirmed` 时才允许出现 `recommended` 标签。")
    add("")
    add("### 要让它变成推荐，必须先确认什么")
    add("")
    add("| 配置路径 | 必须补齐的字段 | 为什么 |")
    add("|---|---|---|")
    for m in promo["minimum_confirmations"]:
        add(f"| `{m['path']}` | {', '.join(f'`{f}`' for f in m['fields'])} | {m['why']} |")
    add("")
    add(f"当前就绪度 `{promo['current_readiness']}` / 需要 `{promo['requires_readiness']}` → "
        f"**{'已满足' if promo['satisfied'] else '未满足'}**。")
    add(f"{promo['engine_role']}")
    add("")

    add("## 3. 情景登记册（并列，不排序）")
    add("")
    add("| 情景 | 分区 | 班组/工种 | 班次 | 完成 | 工期 | 高峰人力 | 总工日 | 阻断冲突 | 容量超限 | 状态 | 证据状态 | 分类 |")
    add("|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---|---|---|---|")
    for r in rows:
        if r.get("status") == RM.INSUFFICIENT_DATA:
            add(f"| `{r['id']}` {r['label']} | — | — | — | — | — | — | — | — | — | "
                f"INSUFFICIENT_DATA | — | {r['classification']} |")
            continue
        add(f"| `{r['id']}` {r['label']} | {r['zone_count']} | {r['crews_per_trade']} | {r['shifts']} "
            f"(×{r['shift_multiplier_applied']}) | {r['finish']} | {r['duration_days']} | "
            f"{r['peak_workers']} | {r['total_worker_days']} | "
            f"{r['blocking_conflicts_before']}→{r['blocking_conflicts_after']} | "
            f"{r['capacity_violations']} | {r['status']} | {r['evidence_state']} | "
            f"**{r['classification']}** |")
    add("")
    add(f"排序：`{comparison['ranking']}` —— {comparison['note']}")
    add("")
    add(f"> {comparison['caveat']}")
    add("")

    add("## 4. 每个情景赌的是什么（前置条件）")
    add("")
    add("| 情景 | 杠杆 | 配置路径 | 状态 | source_type | confirmed_by | 信心 | 权重 | 说明 |")
    add("|---|---|---|---|---|---|---:|---|---|")
    for r in rows:
        for p in r.get("preconditions", []):
            add(f"| `{r['id']}` | {p['lever']} | `{p['path']}` | {p['status']} | "
                f"{p['source_type'] or '—'} | {p['confirmed_by'] or '—'} | {p['confidence']} | "
                f"{p['weight']} | {p['note']} |")
    add("")
    add("未确认依赖条数相同的情景才会真正可比。当前所有情景共享同一组未确认输入，")
    add("因此上表的工期差异是**同一组假设下的差异**，不是不同证据强度下的差异（GOV-13）。")
    add("")

    add("## 5. 非支配集（并列候选，非推荐）")
    add("")
    add("| 维度 | 取值 |")
    add("|---|---|")
    for d in front["dimensions"]:
        add(f"| {d} | 越小越好 |")
    add("")
    add("| 情景 | 工期 | 高峰人力 | 证据状态 | 分类 |")
    add("|---|---:|---:|---|---|")
    for r in front["rows"]:
        add(f"| `{r['id']}` | {r['duration_days']} | {r['peak_workers']} | {r['evidence_state']} | "
            f"{r['classification']} |")
    add("")
    add(f"集合：`{', '.join(f'`{i}`' for i in front['ids']) or '—'}`　标注 `{front['label']}`　"
        f"**是推荐：{front['is_recommendation']}**")
    add("")
    add(f"{front['note']}")
    add("")

    add("## 6. 杠杆隔离：每个杠杆单独值多少天")
    add("")
    add("多杠杆同时移动时，情景之间的总差值无法归因。下表只移动一个杠杆，其余固定。")
    add("")
    add("| 杠杆 | 固定 | 从 | 到 | 工期 | 节省 | 幅度 | 高峰人力 | 容量超限 | 状态 |")
    add("|---|---|---|---|---:|---:|---:|---:|---:|---|---|")
    for lv in SO.LEVER_KEYS:
        for r in effects[lv]["rows"]:
            held = " / ".join(f"{k}={v}" for k, v in r["held"].items())
            add(f"| {lv} | {held} | {r['from_id']} ({r['from_value']}) | {r['to_id']} ({r['to_value']}) | "
                f"{r['from_duration_days']}→{r['to_duration_days']} | -{r['saved_days']} | "
                f"{r['saved_pct']}% | {r['peak_workers_from']}→{r['peak_workers_to']} | "
                f"{r['capacity_violations_from']}→{r['capacity_violations_to']} | {r['status_to']} |")
    add("")
    add("读数方法：这是**同一组未确认假设下的杠杆弹性**，不是收益承诺。"
        "尤其注意班次杠杆——它只作用于生产类任务，对设计、报建、检查门与法定等待期无效，"
        "因此 1.75× 的折算系数远不等于总工期 ×1/1.75。")
    add("")

    add("## 7. 情景治理规则")
    add("")
    add("| 规则 | 内容 | 动作 | 理由 |")
    add("|---|---|---|---|")
    for r in (space.get("governance") or {}).get("rules", []):
        add(f"| `{r['id']}` | {r['rule']} | {r['action']} | {r['reason']} |")
    add("")
    add("情景**不得改动**（GOV-11）：" +
        "、".join(f"`{b}`" for b in space.get("never_moved_by_a_scenario", [])[:4]) + "。")
    add("")

    add("## 8. 资源就绪度")
    add("")
    add("| 资源节 | 条目数 | confirmed | provisional | 就绪度 |")
    add("|---|---:|---:|---:|---|")
    for s in readiness["sections"]:
        c = s["counts"]
        add(f"| `{s['section']}` | {s['entries']} | {c.get('confirmed', 0)} | {c.get('provisional', 0)} | "
            f"**{s['readiness']}** |")
    add("")
    add(f"**总体 `{readiness['overall']}`** —— {readiness['scenario_statement']}")
    add("")

    add("## 9. 口径说明（避免误读）")
    add("")
    add("- **班次只缩短工期，不改变单日人数**：高峰人力不随班次变化，因为班次进入工期公式，"
        "不进入人力直方图。")
    add("- **2 班按 1.75× 折算**，该折算 `source_type=inherited_assumption`、"
        "`contractor_status=contractor_unconfirmed`。")
    add("- **总工日不是造价**：它是投入量代理，不含单价、管理费与赶工溢价。")
    add("- **分区为等面积机械切分**，未考虑平面形状、竖向分段与验收批次。")
    add("- **所有情景共享同一套未标定生产率**：情景之间的比较不证明任何生产率。")
    add("")

    add("## 10. 回归结果")
    add("")
    if regression == "(skipped)":
        add("（本次生成跳过回归）")
    else:
        tail = [ln for ln in regression.strip().splitlines() if ln.strip()][-14:]
        add("```")
        for ln in tail:
            add(ln)
        add("```")
    add("")

    add("## 11. 已知限制")
    add("")
    for lim in space.get("known_limits", []):
        add(f"- {lim}")
    add("")

    (out / "scenario_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
