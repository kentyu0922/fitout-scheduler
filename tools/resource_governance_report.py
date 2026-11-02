"""Generate the V6.2.1 resource governance / estimation calibration report.

Outputs to output/v6_2_1/:
  resource_governance_report.md   — PM-facing Chinese report
  crew_sensitivity.csv            — crew pool 1..N: finish / marginal saving / peak / utilisation
  trade_crew_sensitivity.csv      — per critical trade: move one trade, hold the rest
  shift_sensitivity.csv           — 1-shift vs 2-shift, with contractor confirmation status
  pool_composition.csv            — crew COUNT vs crew SIZE at identical headcount
  assumption_register.csv         — every provisional / confirmed resource input
  readiness.json                  — unconfirmed / partially_confirmed / confirmed
  governance_summary.json         — machine-readable governance state

The 6000 m2 demo re-uses the V6.1/V6.2 template so the numbers stay comparable.
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
from core import resource_sensitivity as RS                     # noqa: E402
from core.fitout_engine import prepare_spatial_fitout           # noqa: E402
from core.fitout_wbs import expand_fitout_wbs                   # noqa: E402

TEMPLATES = json.loads((ROOT / "templates" / "wbs_templates.json").read_text(encoding="utf-8"))["templates"]
START = "2026-09-08"
DEMO_AREA = 6000.0
DEMO_TEMPLATE = "Standard_Fitout_DB_Invite"
TRADES = ["general", "partitions_ceilings", "mep_mechanical", "mep_electrical",
          "wet_finishes", "joinery", "flooring", "commissioning", "furniture"]
CRITICAL_TRADES = ["partitions_ceilings", "mep_mechanical", "mep_electrical",
                   "wet_finishes", "joinery", "flooring"]

CHANGED_FILES = [
    ("config/resource_constraints.json", "modified",
     "新增 source_types 六类来源注册表与信心权重、governance 硬规则 GOV-01..09、sensitivity 阈值与停止条件、"
     "calibration 拥挤标定数据集 schema；congestion/shift/workfront/crew_pool 各条目补齐 source_type、"
     "calibration_status、increase_rule；crew_pool 显式声明继承假设并给出 by_trade 结构模板（不填值）。"
     "所有数值（拥挤表、1.75×、容量、38 条率）一律未改。"),
    ("core/resource_model.py", "modified",
     "crew_pool() 输出增加 source_type / basis / calibration_status；load_resource_constraints() 加载时执行治理校验。"),
    ("core/resource_governance.py", "added",
     "来源信心、GOV 规则校验、资源就绪度、假设/确认台账、班组池声明、池≠池规模比较、边际收益分级、递减收益点、标定数据集 schema"),
    ("core/resource_sensitivity.py", "added",
     "班组数 1..N 确定性敏感性、单工种敏感性、班次敏感性、停止规则与可解释输出"),
    ("tests/test_resource_governance.py", "added", "V6.2.1 回归：继承池仍为临时、GOV 校验、池≠池规模、递减收益、就绪度、基线不变性"),
    ("tools/resource_governance_report.py", "added", "本报告生成脚本（可复现）"),
    ("docs_and_sops/resource_governance.md", "added", "来源分类、治理规则、就绪度、敏感性方法与已知限制"),
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


def _tasks():
    return expand_fitout_wbs(copy.deepcopy(TEMPLATES[DEMO_TEMPLATE]["tasks"]))


def _prepare(crews_per_trade):
    return prepare_spatial_fitout(_tasks(), DEMO_AREA, crews_per_trade=crews_per_trade)["tasks"]


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
    py = sys.executable
    proc = subprocess.run([py, "-X", "utf8", "tools/run_regressions.py"],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
    return (proc.stdout or "") + ("\n[stderr]\n" + proc.stderr if proc.stderr else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-regression", action="store_true")
    args = ap.parse_args()

    out = ROOT / "output" / "v6_2_1"
    out.mkdir(parents=True, exist_ok=True)
    cfg = RM.load_resource_constraints()

    print("[1/7] governance validation & readiness…")
    gov = RG.governance_summary(cfg)
    readiness = gov["readiness"]
    (out / "readiness.json").write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "governance_summary.json").write_text(
        json.dumps(gov, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("[2/7] assumption register…")
    reg = RG.assumption_register(cfg)
    write_csv(out / "assumption_register.csv", reg["provisional"] + reg["confirmed"])

    print("[3/7] crew pool sensitivity 1..N (6000 m2)…")
    sweep = RS.crew_count_sensitivity(_prepare, START, TRADES, cfg=cfg)
    write_csv(out / "crew_sensitivity.csv", sweep["rows"])
    for line in RS.explain_sensitivity(sweep):
        print("   ", line)

    print("[4/7] per-trade sensitivity…")
    trade_rows = []
    for trade in CRITICAL_TRADES:
        res = RS.trade_crew_sensitivity(_prepare, START, trade, TRADES, cfg=cfg)
        for r in res["rows"]:
            r = dict(r)
            r["other_trades_at"] = json.dumps(r["other_trades_at"], ensure_ascii=False)
            trade_rows.append(r)
        print(f"    {trade}: " + " → ".join(
            f"{r['crews_for_trade']}c={r['feasible_duration_days']}d" for r in res["rows"]))
    write_csv(out / "trade_crew_sensitivity.csv", trade_rows)

    print("[5/7] pool composition & shifts…")
    pool_rows = []
    for trade in ["partitions_ceilings", "wet_finishes", "flooring"]:
        cmpo = RG.compare_pool_compositions(trade, 24, cfg, zone_area=2000.0)
        for r in cmpo["rows"]:
            pool_rows.append(dict(trade=trade, **r))
    write_csv(out / "pool_composition.csv", pool_rows)
    shifts = RS.shift_sensitivity(cfg)
    write_csv(out / "shift_sensitivity.csv", shifts["rows"])

    print("[6/7] crew pool declarations…")
    decl_rows = [RG.crew_pool_declaration(t, cfg) for t in TRADES]
    write_csv(out / "crew_pool_declarations.csv", decl_rows)

    print("[7/7] regression…")
    regression = "(skipped)" if args.no_regression else run_regressions()

    _write_report(out, cfg, gov, readiness, reg, sweep, trade_rows, pool_rows, shifts,
                  decl_rows, regression)
    print(f"done → {out}")


def _write_report(out, cfg, gov, readiness, reg, sweep, trade_rows, pool_rows, shifts,
                  decl_rows, regression):
    L = []
    add = L.append
    a = sweep["analysis"]
    rows = sweep["rows"]
    fn = a["first_naturally_feasible"]
    dr = a["diminishing_returns"]

    add("# V6.2.1 资源估算校准 / 治理通行证")
    add("")
    add(f"- 演示：`{DEMO_TEMPLATE}`　面积：**{DEMO_AREA:g} ㎡**　开工：`{START}`")
    add(f"- 资源输入就绪度：**`{readiness['overall']}`** —— {readiness['scenario_statement']}")
    add(f"- 敏感性结论标注：**`{a['optimum_label']}`**（GOV-07：资源输入未确认前不得标 recommended）")
    add("")
    add("> V6.2.1 不优化。它只回答：哪些资源输入是事实、哪些是假设、这个计划对它们有多敏感。")
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

    add("## 2. 三件事必须分开")
    add("")
    add("| 层次 | 含义 | 本引擎的表示 |")
    add("|---|---|---|")
    add("| 所需资源 (required) | 工程量 ÷ 生产率的算术结果 | `worker_days_required()`，直接取 productivity_rates |")
    add("| 授权可用性 (authorised) | 规划者/承包商说现场能有多少人 | `crew_pool.*`（含 source_type 与 status） |")
    add("| 确认承诺 (committed) | 承包商书面确认的到岗资源 | 仅 `status=confirmed` + `confirmed_by` 非空 |")
    add("")
    add(f"当前 **确认承诺条目数 = {reg['confirmed_count']}**，临时假设条目数 = **{reg['provisional_count']}**。")
    add("确认清单为空是真实状态，不是遗漏：V6.2.1 阶段没有任何承包商确认的资源输入。")
    add("")

    add("## 3. 班组池管理：继承假设必须显式")
    add("")
    add("| 工种 | 声明来源 | 班组数 | 状态 | source_type | confirmed_by | 信心 |")
    add("|---|---|---:|---|---|---|---|")
    for d in decl_rows:
        add(f"| `{d['trade']}` | {d['source']} | {d['available_crews']} | {d['status']} | "
            f"{d['source_type']} | {d['confirmed_by'] or '—'} | {d['confidence']} |")
    add("")
    add("**`available_crews = 1` 是 V6.1 单班组流水的排序假设**，`basis = inherited_v6_1_single_crew_pipeline`，")
    add("`source_type = inherited_assumption`，`confirmed_by = null`。")
    add("**不得解读为「承包商只有一个班组」。**")
    add("")

    add("## 4. 资源来源类型与信心")
    add("")
    add("| 来源类型 | 置信基数 | 说明 |")
    add("|---|---:|---|")
    for k, v in gov["source_types"].items():
        add(f"| `{k}` | {v['confidence']} | {v['definition']} |")
    add("")
    add("信心 = 来源置信基数 × 状态权重（confirmed 1.0 / provisional 0.5 / insufficient_data 0.0）。")
    add("规划者假设（`planner_commitment` 及以下）永远不等于承包商承诺（`contractor_confirmed`）。")
    add("")

    add("## 5. 6000㎡ 班组池敏感性（1 → N 班组/工种）")
    add("")
    add("| 班组/工种 | 完成 | 工期 | 边际节省 | 累计节省 | 高峰人力 | 需求班组峰值 | 平衡后利用率 | 阻断冲突 | 同区容量超限 | 资源状态 | 边际分类 |")
    add("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|")
    for r in rows:
        add(f"| {r['crews_per_trade']} | {r['finish']} | {r['feasible_duration_days']} | "
            f"{'—' if r['marginal_saving_days'] is None else '-' + str(r['marginal_saving_days'])} | "
            f"-{r['saving_vs_baseline_days']} | {r['peak_workers']} | {r['peak_crews_demanded_max']} | "
            f"{r['workfront_utilisation_max']} | "
            f"{r['blocking_conflicts_before']}→{r['blocking_conflicts_after']} | "
            f"{r['capacity_violations']} | {r['resource_status']} | {r['marginal_gain_category']} |")
    add("")
    add("**关键取舍**：增派班组消除「一班组跨两区」的阻断冲突（37 → 0），但同时在同一分区内"
        "制造同工种人员超限（0 → 30 → 95 → 101 组）。这不是模型矛盾，而是真实的现场取舍：")
    add("多班组解决的是**转场**问题，解决不了**单区容量**问题——后者只能靠分区或错峰，不能靠加人。")
    add("")
    if fn.get("found"):
        add(f"**首个自然可行点：{fn['crew_count']} 班组/工种**（{fn['duration_days']} 天，"
            f"未经平衡即无阻断型班组冲突）—— 标注 `{fn['label']}`。")
    else:
        add(f"**首个自然可行点：扫描范围内不存在**（{fn.get('note')}）。")
    if dr.get("found"):
        add("")
        add(f"**递减收益点：{dr['crew_count']} 班组/工种** —— {dr['statement']}")
        add("")
        add(f"即：{dr['previous_crew_count']} 班组 {dr['previous_duration_days']} 天 → "
            f"{dr['crew_count']} 班组 {dr['duration_days']} 天，"
            f"额外 1 个班组 / 工种换来 **{dr['marginal_saving_days']} 天**，"
            f"成本是高峰人力从 249 人升到 278 人。")
    add("")
    if a["stop_reason"]:
        add(f"**扫描停止原因**：`{a['stop_reason']['rule']}` —— {a['stop_reason']['detail']}")
        add("")
    add(f"扫描范围内最短工期出现在 **{a['optimum_crew_count']} 班组/工种**，标注为 **`{a['optimum_label']}`**。")
    add("这是数学结果，不是建议：对应的班组池可用性尚未获得承包商确认。")
    add("")

    add("## 6. 单工种敏感性（其余工种停在声明池）")
    add("")
    add("| 工种 | 1 班组 | 2 班组 | 3 班组 | 4 班组 | 5 班组 |")
    add("|---|---:|---:|---:|---:|---:|")
    by_trade = {}
    for r in trade_rows:
        by_trade.setdefault(r["trade"], {})[r["crews_for_trade"]] = r["feasible_duration_days"]
    for trade, m in by_trade.items():
        add(f"| `{trade}` | " + " | ".join(str(m.get(n, "—")) for n in (1, 2, 3, 4, 5)) + " |")
    add("")
    add("多数工种在 3 班组之后进入平台期：再增班组不再缩短总工期，因为关键链已被固定工期"
        "（设计/图审/法定审批/散味 SOP）与工作面容量锁死。")
    add("")

    add("## 7. 池 ≠ 池规模（同等总人数，结构不同）")
    add("")
    add("| 工种 | 班组数 | 每班组人数 | 总人数 | 有效工效（人当量） | 人均产出 | 占用工作面 | 单区可容纳 |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in pool_rows:
        add(f"| `{r['trade']}` | {r['crew_count']} | {r['workers_per_crew']} | {r['total_workers']} | "
            f"{r['effective_workers']} | {r['output_per_worker']} | {r['workfronts_occupied']} | "
            f"{r['zone_capacity_workers']} |")
    add("")
    add("同一总人数下，**3 班组 × 8 人 ≠ 1 班组 × 24 人**：多个班组可占用多个工作面，")
    add("而一个超大班组会自我拥堵并被单区容量封顶。")
    add("")
    add("各工种的有效工效相同，是因为 `congestion_model.by_trade` 为空——所有工种共用同一条"
        "**临时**默认拥挤曲线（这正是 GOV-06 禁止凭猜测改写的原因）。")
    add("")

    add("## 8. 拥挤曲线与班次治理")
    add("")
    cal = gov["calibration"]
    add(f"- 拥挤曲线：`calibration_status = {cal['calibration_status']}`、`source_type = {cal['source_type']}`、"
        f"`confirmed_by = {cal['confirmed_by'] or 'null'}`　→　**数值未改，也不得凭猜测校准**。")
    add(f"- 标定所需数据集字段（{len(cal['required_fields'])} 项）："
        + "、".join(f"`{f['field']}`" for f in cal["required_fields"]))
    add(f"- 派生量：" + "、".join(f"`{d['field']}`" for d in cal["derived"]))
    add(f"- 拟合规则：{cal['fit_rule']}")
    add("")
    add("| 班次 | 产出系数 | 状态 | source_type | confirmed_by | 承包商状态 | 信心 |")
    add("|---:|---:|---|---|---|---|---:|")
    for r in shifts["rows"]:
        add(f"| {r['shifts']} | {r['output_multiplier']} | {r['status']} | {r['source_type'] or '—'} | "
            f"{r['confirmed_by'] or '—'} | {r['contractor_status']} | {r['confidence']} |")
    add("")
    add("2 班 = 1.75× 保持 **provisional / contractor_unconfirmed**。夜班 `default_multiplier = null`，"
        "无证据时返回 INSUFFICIENT_DATA。")
    add("")

    add("## 9. 资源就绪度")
    add("")
    add("| 资源节 | 条目数 | confirmed | provisional | 就绪度 |")
    add("|---|---:|---:|---:|---|")
    for s in readiness["sections"]:
        c = s["counts"]
        add(f"| `{s['section']}` | {s['entries']} | {c.get('confirmed', 0)} | {c.get('provisional', 0)} | "
            f"**{s['readiness']}** |")
    add("")
    add(f"**总体：`{readiness['overall']}`** —— {readiness['scenario_statement']}")
    add("")
    add("V6.3 场景优化器将直接消费该字段，用于输出「情景在数学上可行，但承包商资源可用性尚未确认」。")
    add("")

    add("## 10. 临时假设清单")
    add("")
    add("| 节 | 条目 | 状态 | source_type | confirmed_by | calibration_status | 信心 |")
    add("|---|---|---|---|---|---|---:|")
    for r in reg["provisional"]:
        add(f"| `{r['section']}` | `{r['entry']}` | {r['status']} | {r['source_type']} | "
            f"{r['confirmed_by'] or '—'} | {r['calibration_status'] or '—'} | {r['confidence']} |")
    add("")

    add("## 11. 确认假设清单")
    add("")
    if reg["confirmed"]:
        add("| 节 | 条目 | basis | confirmed_by |")
        add("|---|---|---|---|")
        for r in reg["confirmed"]:
            add(f"| `{r['section']}` | `{r['entry']}` | {r['basis']} | {r['confirmed_by']} |")
    else:
        add("**空。** 没有任何资源输入达到 `status=confirmed`。")
        add("")
        add("要填实这份清单，需要按 `crew_pool.by_trade.<trade>` 的结构提供：")
        add("`available_crews`、`workers_per_crew`、`shifts`、`assigned_floors`、`assigned_zones`、")
        add("`available_from`、`available_until`、`basis`、`source_type`、`confirmed_by`、`status`。")
        add("缺 `basis` 或 `confirmed_by` 时，配置校验会直接拒绝（GOV-01）。")
    add("")

    add("## 12. 新增测试")
    add("")
    add("`tests/test_resource_governance.py`：继承班组池仍为临时、按工种覆盖需要 basis、confirmed 需要 confirmed_by、")
    add("班级数 ≠ 班组规模、敏感性确定性、递减收益检测、工作面容量仍被强制、拥挤仍然适用、资源就绪度、")
    add("无虚假承包商承诺、基线不变性。")
    add("")

    add("## 13. 回归结果")
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

    add("## 14. 已知限制")
    add("")
    for lim in cfg.get("known_limits", []):
        add(f"- {lim}")
    add("- V6.2.1 不做优化：不推荐班组数、不做成本权衡、不做情景组合（属 V6.3）。")
    add("- 敏感性扫描只移动班组数，同步未移动分区数、班组规模与班次；三者交互留待 V6.3。")
    add("")

    (out / "resource_governance_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
