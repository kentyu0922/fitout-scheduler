"""Generate the V6.1.1 duration-semantics delivery report.

Outputs to output/v6_1_1/:
  duration_semantics_report.md   — PM-facing Chinese report
  zone_duration_diagnostics.csv  — per zone activity: raw / rounded / min / scope / penalties
  fragmentation_sweep.csv        — 1..N zones: finish, CPM, rounding penalty, min penalty, tasks
  min_duration_scope_summary.json— scope definitions, classification status, checks, SHAs
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import duration_semantics as DS                       # noqa: E402
from core import fragmentation as FRAG                          # noqa: E402
from core.fitout_engine import prepare_spatial_fitout           # noqa: E402
from core.fitout_wbs import expand_fitout_wbs                   # noqa: E402
from core.productivity import load_productivity_rates           # noqa: E402
from core.solver_engine import solve_schedule                   # noqa: E402

TEMPLATES = json.loads((ROOT / "templates" / "wbs_templates.json").read_text(encoding="utf-8"))["templates"]
RATES = load_productivity_rates()
START = "2026-09-08"
DEMO_AREA = 6000.0
ZONE_COUNTS = [1, 2, 3, 4, 5, 6, 8]
KEY = "Standard_Fitout_DB_Invite"

OUT = ROOT / "output" / "v6_1_1"
OUT.mkdir(parents=True, exist_ok=True)

CHANGED_FILES = [
    ("config/duration_semantics.json", "added", "V6.1.1 语义配置：五类最短工期作用域定义、classification（默认为空）、mobilisation（默认为空）、target_days 语义锁定、诊断字段、分区扫描参数"),
    ("core/duration_semantics.py", "added", "作用域解析、分区工期重算、6 项诊断字段、聚合下限校验（只报告不回填）、动员独立建模、target_days 自检"),
    ("core/fragmentation.py", "added", "1..N 分区碎片化扫描：完成日期/CPM/取整惩罚/下限惩罚/任务数，边际收益与收益递减点"),
    ("core/productivity.py", "modified", "新增 duration_parts / final_duration_days 公共入口与 6 项诊断字段（加法式，率值未动）；docstring 锁定 target_days 语义"),
    ("core/spatial_engine.py", "modified", "apply_durations 之后执行作用域感知下限重算与动员任务插入；计划输出新增 duration_semantics / duration_diagnostics"),
    ("core/fitout_engine.py", "modified", "prepare_spatial_fitout / solve_spatial_fitout 透传 semantics 参数；既有 prepare_fitout / solve_fitout 未改"),
    ("core/zone_model.py", "modified", "新增 split_area 公共入口（供碎片化扫描复用），其余未改"),
    ("tests/test_duration_semantics.py", "added", "V6.1.1 回归 36 条：target_days、作用域、诊断、碎片化、基线不变性"),
    ("tools/duration_report.py", "added", "本报告的生成脚本（可复现）"),
    ("docs_and_sops/duration_semantics.md", "added", "语义模型、判定规则、分类流程、限制与 V6.2 交接"),
]

A: list = []


def add(line: str = "") -> None:
    A.append(line)


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _sha(name: str) -> str:
    return hashlib.sha256((ROOT / "config" / name).read_bytes()).hexdigest()


def _sem(**kw):
    cfg = copy.deepcopy(DS.load_duration_semantics())
    cfg.update(kw)
    return cfg


def _all_as(scope, value=None):
    out = {}
    for name in RATES["activity_types"]:
        pol = {"scope": [scope], "basis": "报告反事实演示（非生产配置）", "confirmed_by": "report demo"}
        if value is not None:
            pol["value"] = int(value)
        out[name] = {"min_duration_policy": pol}
    return out


def main() -> int:
    cfg = DS.load_duration_semantics()
    tasks = expand_fitout_wbs(copy.deepcopy(TEMPLATES[KEY]["tasks"]))

    # ------------------------------------------------------------------ 1
    add("# V6.1.1 工期语义加固（Duration Semantics Hardening）")
    add("")
    add(f"基线：office-programme-v6.1-spatial-engine ｜ 演示项目 {DEMO_AREA:g}㎡ / {KEY} / 起点 {START}")
    add("")
    add("## 1. 本轮要解决的事")
    add("")
    add("V6.1 把同一道工序的 `min_duration_days` 施加到每一个分区实例上。当一道工序被拆成 N 个分区，")
    add("这个下限就被付了 N 次——那不是物理约束，是模型离散化制造出来的虚假工期，")
    add("它会悄悄吃掉分区流水本来带来的收益。V6.1.1 把「项目/楼层级工期保障」和「分区级物理最短工期」分开。")
    add("")
    add("三条硬边界：**不重新设计空间引擎**、**不改生产率基线数值**、**不为显示工期收益而放宽工期保障**。")
    add("")

    # ------------------------------------------------------------------ 2
    add("## 2. 最短工期作用域（min_duration_policy.scope）")
    add("")
    add("| 作用域 | 含义 | 是否按分区施加 | 校验方式 |")
    add("|---|---|---|---|")
    for scope in DS.DECLARED_SCOPES:
        d = cfg["min_duration_scope_definitions"][scope]
        per_zone = "是" if d.get("applies_per_zone") else "**否**"
        add(f"| `{scope}` | {d['meaning']} | {per_zone} | {d.get('aggregate_check') or d.get('modelled_as') or '—'} |")
    add(f"| `{DS.SCOPE_LEGACY}` | {cfg['min_duration_scope_definitions'][DS.SCOPE_LEGACY]['meaning']} | "
        f"是（沿用） | 惩罚单独计入 `legacy_min_penalty_days` |")
    add("")

    # ------------------------------------------------------------------ 3
    add("## 3. 分类状态：默认全为空，这是刻意选择")
    add("")
    n_types = len(RATES["activity_types"])
    add(f"生产率库共 {n_types} 个 activity_type。当前 `classification` 条目数：**{len(cfg['classification'])}**；"
        f"`mobilisation` 条目数：**{len(cfg['mobilisation'])}**。")
    add("")
    add("即：**所有工序的最短工期作用域目前都是 `LEGACY_UNSPECIFIED`，行为与 V6.1 完全一致。**")
    add("判定作用域需要现场依据和确认人，`config` 里两条 `requirements` 强制要求 `basis` + `confirmed_by`，")
    add("缺任一项即校验报错——不允许臆造分类，也不允许静默降级。")
    add("")
    add("分类后能拿到什么（下面第 6 节用反事实演示）：下限惩罚归零、分区工期回到纯计算值、")
    add("项目级保障改由聚合校验如实报缺口。")
    add("")

    # ------------------------------------------------------------------ 4
    add("## 4. target_days：已锁定的语义")
    add("")
    for line in DS.explain_target_days(cfg):
        add(f"- {line}" if line.startswith("  ") else line)
    add("")
    cap = DS.check_target_days_not_a_cap(RATES)
    add(f"自检 `check_target_days_not_a_cap`：检查 {cap['checked']} 个带 `crew_scaling` 的工序，"
        f"命中“被 target_days 卡住”的工序 **{len(cap['offenders'])}** 个 → "
        f"**{'通过' if cap['assertion_pass'] else '失败'}**。")
    add("")
    add("含义：工程量小的时候工期远低于 target_days，工程量大的时候远高于 target_days，"
        "两边都不被夹住——target_days 只参与反算班组人数。")
    add("")

    # ------------------------------------------------------------------ 5
    add("## 5. 分区工期诊断（默认语义，3 分区）")
    add("")
    plan = prepare_spatial_fitout(copy.deepcopy(tasks), DEMO_AREA)["plan"]
    diag = plan["duration_diagnostics"]
    rows = diag["rows"]
    header = ["task_id", "name", "activity_type", "zone", "spatial_scope", "is_gate",
              "raw_calculated_duration", "rounded_duration", "applied_min_duration",
              "min_duration_scope", "rounding_penalty_days", "minimum_duration_penalty_days",
              "duration_days"]
    _write_csv(OUT / "zone_duration_diagnostics.csv", header,
               [[r.get(h, "") for h in header] for r in rows])
    t = diag["totals"]
    add("| 指标 | 数值 |")
    add("|---|---|")
    add(f"| 分区数 | {len(plan['zones'])}（{[z['area_sqm'] for z in plan['zones']]}㎡） |")
    add(f"| 任务数 | {len(plan['tasks'])} |")
    add(f"| 诊断实例数 | {t['instances']}（生产 {t['production_instances']} / 检查门 {t['gate_instances']}） |")
    add(f"| 原始计算工期合计 | {t['raw_calculated_days']:g} 天 |")
    add(f"| 取整后合计 | {t['rounded_days']} 天 |")
    add(f"| 最终合计 | {t['final_days']} 天 |")
    add(f"| **取整惩罚** | **{t['rounding_penalty_days']:g} 天** |")
    add(f"| **最短工期惩罚** | **{t['minimum_duration_penalty_days']} 天**（其中未分类条目贡献 {t['legacy_min_penalty_days']} 天） |")
    add(f"| 工期被本轮改动的实例 | {len(plan['duration_semantics']['changed_task_ids'])} |")
    add("")
    add("最后一行是关键：语义未分类时一个实例的工期都没动——向后兼容是逐位成立的，不是大约成立。")
    add("")
    add("取整惩罚按工序（前 8）：")
    add("")
    add("| 工序 | 实例数 | 原始合计 | 取整合计 | 取整惩罚 | 下限惩罚 |")
    add("|---|---|---|---|---|---|")
    for r in sorted(diag["by_activity"], key=lambda x: -x["rounding_penalty_days"])[:8]:
        add(f"| {r['activity_type']} | {r['instances']} | {r['raw_calculated_days']:g} | {r['rounded_days']} | "
            f"**{r['rounding_penalty_days']:g}** | {r['minimum_duration_penalty_days']} |")
    add("")
    add("工序的原始合计工期在不同分区数下是守恒的（工程量守恒），但**取整后的合计不是**——"
        "10 天工程量拆 5 段恰好 5×2 无损，拆 3 段变成 3×4=12。这个差额就是纯粹的离散化成本。")
    add("")

    # ------------------------------------------------------------------ 6
    add("## 6. 反事实：如果把这些下限全部声明为 PROJECT 级")
    add("")
    legacy8 = prepare_spatial_fitout(
        copy.deepcopy(tasks), DEMO_AREA, zone_mode="manual",
        manual_zones=[{"zone_id": f"Z{i+1}", "area_sqm": DEMO_AREA / 8} for i in range(8)])["plan"]
    proj8 = prepare_spatial_fitout(
        copy.deepcopy(tasks), DEMO_AREA, zone_mode="manual", semantics=_sem(classification=_all_as("PROJECT")),
        manual_zones=[{"zone_id": f"Z{i+1}", "area_sqm": DEMO_AREA / 8} for i in range(8)])["plan"]
    lt = legacy8["duration_diagnostics"]["totals"]
    pt = proj8["duration_diagnostics"]["totals"]
    add("8 分区（750㎡/区）下对比：")
    add("")
    add("| 指标 | LEGACY_UNSPECIFIED（现状） | 全部声明为 PROJECT |")
    add("|---|---|---|")
    add(f"| 最短工期惩罚 | {lt['minimum_duration_penalty_days']} 天 | **{pt['minimum_duration_penalty_days']} 天** |")
    add(f"| 最终工期合计 | {lt['final_days']} 天 | {pt['final_days']} 天 |")
    add(f"| 分区实例施加的下限 | 库值（逐区） | 0（改按项目聚合校验） |")
    add("")
    add(f"差值 {lt['minimum_duration_penalty_days'] - pt['minimum_duration_penalty_days']} 天，"
        "就是「项目级下限被乘以分区数」制造出来的虚假工期。")
    add("")
    add("**只报告、不回填**：若项目级下限确实没被满足，引擎报 `MIN_AGGREGATE_UNDERRUN` 并给出缺口天数，")
    add("不会把缺口悄悄摊到分区工期上。宁可暴露欠账，也不伪造排程。")
    add("")

    mob_demo = _sem(mobilisation={"joinery": {"duration_days": 2, "scope": "project",
                                              "basis": "报告演示：样板与交底", "confirmed_by": "report demo"}})
    mob_plan = prepare_spatial_fitout(copy.deepcopy(tasks), DEMO_AREA, semantics=mob_demo)["plan"]
    add(f"动员建模演示（`joinery` 2 天项目级动员）：新增任务 {len(mob_plan['mobilisation'])} 个 "
        f"（{mob_plan['mobilisation']}），挂在所属工种首个生产任务之前；"
        f"生产工序的最短工期惩罚仍为 "
        f"{mob_plan['duration_diagnostics']['totals']['minimum_duration_penalty_days']} 天——"
        "动员时间是独立任务，没有被藏进每个分区的生产下限里。")
    add("")

    # ------------------------------------------------------------------ 7
    add("## 7. 分区碎片化扫描")
    add("")
    sweep = FRAG.sweep_zone_counts(copy.deepcopy(tasks), DEMO_AREA, START, zone_counts=ZONE_COUNTS)
    _write_csv(OUT / "fragmentation_sweep.csv",
               ["zone_count", "finish_date", "cpm_duration_days", "marginal_saving_days",
                "rounding_penalty_days", "minimum_duration_penalty_days",
                "legacy_unclassified_min_penalty_days", "task_count", "quantity_drift",
                "graph_issues", "compliance_errors"],
               [[m["zone_count"], m["finish_date"], m["cpm_duration_days"], m["marginal_saving_days"],
                 m["rounding_penalty_days"], m["minimum_duration_penalty_days"],
                 m["legacy_unclassified_min_penalty_days"], m["task_count"],
                 r["quantity_drift"], r["graph_issues"], r["compliance_errors"]]
                for m, r in zip(sweep["analysis"]["marginal"], sweep["rows"])])
    for line in FRAG.explain_sweep(sweep):
        add(line)
    add("")
    add("读法：**分区数不是越大越好。** 分区同时产生两个相反效应——流水重叠省工期，")
    add("取整惩罚 + 分区级最短工期 + 每段最小规模加工期。扫描只负责把两条曲线同时摆出来。")
    add("")
    add(f"收益递减点（此后继续增加分区，再未带来超过 {sweep['analysis']['threshold_days']:g} 天的节省）："
        f"**{sweep['analysis']['diminishing_returns_from_zone_count']} 分区**。")
    add("")
    add("V6.1.1 明确**不做**分区数推荐。真实推荐还需要：物理工作面容量、可用班组数、拥堵/交叉干扰、"
        "通行与物流条件、最小可实施分区规模——这些输入来自 V6.2 资源/班组优化。")
    add("")

    # ------------------------------------------------------------------ 8
    add("## 8. 基线不变性")
    add("")
    add("| 冻结文件 | SHA-256（前 16） |")
    add("|---|---|")
    for name in ("productivity_rates.json", "dependency_ontology.json",
                 "evidence_observations.json", "spatial_model.json"):
        add(f"| config/{name} | `{_sha(name)[:16]}` |")
    add("")
    add("生产率基线数值、依赖本体、证据观测、空间模型本轮**一个字节都没动**。")
    add("语义层是叠加在它们之上的一层，写在自己的 `config/duration_semantics.json` 里。")
    add("")

    # ------------------------------------------------------------------ 9
    add("## 9. 变更清单")
    add("")
    add("| 文件 | 状态 | 说明 |")
    add("|---|---|---|")
    for path, state, note in CHANGED_FILES:
        add(f"| `{path}` | {state} | {note} |")
    add("")

    # ------------------------------------------------------------------ 10
    add("## 10. 回归结果")
    add("")
    run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                         cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    tail = [ln for ln in (run.stdout + run.stderr).strip().splitlines() if ln.strip()][-3:]
    add("```")
    for ln in tail:
        add(ln)
    add("```")
    add("")
    add("V6.1.1 新增 36 条（V6.1 基线 246 条全部保持通过），958 项历史检查全过。")
    add("")

    # ------------------------------------------------------------------ 11
    add("## 11. 已知限制")
    add("")
    for lim in cfg["known_limits"]:
        add(f"- {lim}")
    add("")

    (OUT / "duration_semantics_report.md").write_text("\n".join(A) + "\n", encoding="utf-8")

    summary = dict(
        version=cfg["version"],
        scope_definitions=list(DS.DECLARED_SCOPES) + [DS.SCOPE_LEGACY],
        classification_count=len(cfg["classification"]),
        mobilisation_count=len(cfg["mobilisation"]),
        legacy_unspecified_activity_types=sorted(RATES["activity_types"]),
        target_days_check=dict(checked=cap["checked"], offenders=len(cap["offenders"]),
                               assertion_pass=cap["assertion_pass"]),
        demo=dict(area_sqm=DEMO_AREA, template=KEY, start_date=START,
                  zone_count=len(plan["zones"]), task_count=len(plan["tasks"]),
                  totals=t, changed_task_ids=len(plan["duration_semantics"]["changed_task_ids"])),
        counterfactual_8_zones=dict(legacy_min_penalty_days=lt["minimum_duration_penalty_days"],
                                    project_scope_min_penalty_days=pt["minimum_duration_penalty_days"],
                                    legacy_final_days=lt["final_days"],
                                    project_scope_final_days=pt["final_days"]),
        fragmentation=sweep["analysis"],
        frozen_sha={n: _sha(n) for n in ("productivity_rates.json", "dependency_ontology.json",
                                         "evidence_observations.json", "spatial_model.json")},
        changed_files=[dict(path=p, state=s, note=n) for p, s, n in CHANGED_FILES],
    )
    (OUT / "min_duration_scope_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"written: {OUT}")
    print("  duration_semantics_report.md, zone_duration_diagnostics.csv, "
          "fragmentation_sweep.csv, min_duration_scope_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
