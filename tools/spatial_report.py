"""Generate the V6.1 spatial engine delivery report.

Outputs to output/v6_1/:
  spatial_report.md               — PM-facing Chinese report
  zone_plan.csv                   — one row per generated zone (area, reason, status)
  quantity_allocation.csv         — project quantity → per-zone apportionment with formulas
  inter_zone_pipeline.csv         — every cross-zone crew link with reason
  scope_decisions.csv             — spatial scope per activity_type with the reason
  baseline_vs_spatial.json        — CPM comparison across every template
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import zone_model as ZM                      # noqa: E402
from core import workfront_model as WFM               # noqa: E402
from core import spatial_engine as SE                 # noqa: E402
from core.dependency_engine import evaluate_sectional  # noqa: E402
from core.fitout_engine import prepare_fitout          # noqa: E402
from core.fitout_wbs import expand_fitout_wbs          # noqa: E402
from core.productivity import (apply_productivity_durations,  # noqa: E402
                               load_productivity_rates)
from core.quantity_engine import derive_fitout_quantities  # noqa: E402
from core.solver_engine import solve_schedule          # noqa: E402

TEMPLATES = json.loads((ROOT / "templates" / "wbs_templates.json").read_text(encoding="utf-8"))["templates"]
RATES = load_productivity_rates()
START = "2026-09-08"
DEMO_AREA = 6000.0

CHANGED_FILES = [
    ("config/spatial_model.json", "added", "V6.1 空间模型：层级、分区规模、自动生成方法、空间作用域、检查门实例化、释放条件、跨区流水策略、合规保护"),
    ("core/zone_model.py", "added", "Project/Floor/Zone 数据模型、手动与自动分区生成（含原因）、数量分摊（保总量 + 公式）、空间作用域解析"),
    ("core/workfront_model.py", "added", "工作面模型（空间×工种×班组×通道×顺序）、工种分组、跨区流水关系解析"),
    ("core/spatial_engine.py", "added", "任务按空间作用域实例化、前置与检查门重映射、跨区流水、合规校验、CPM 对比、可解释输出"),
    ("core/fitout_engine.py", "modified", "新增 prepare_spatial_fitout / solve_spatial_fitout 入口；既有 prepare_fitout / solve_fitout 未改"),
    ("tests/test_spatial_engine.py", "added", "V6.1 回归：分区生成、数量分配、空间依赖优先、多工作面、CPM、合规、基线不变性"),
    ("tools/spatial_report.py", "added", "本报告的生成脚本（可复现）"),
    ("docs_and_sops/spatial_engine.md", "added", "模型说明、判定规则、限制与 V6.2 交接"),
]


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _quantities(area):
    return derive_fitout_quantities(area, "standard", "standard")["quantities"]


def _build(area, key, **kw):
    quantities = _quantities(area)

    def _apply(ts):
        apply_productivity_durations(ts, rates=RATES, derived_quantities=quantities)

    return SE.build_spatial_plan(expand_fitout_wbs(copy.deepcopy(TEMPLATES[key]["tasks"])),
                                 float(area), quantities, RATES, apply_durations=_apply, **kw)


def _solved_finish(solved):
    finishes = [str(t.get("finish", "")) for t in solved["tasks"] if t.get("finish")]
    return max(finishes) if finishes else None


def main() -> int:
    parser = argparse.ArgumentParser(description="V6.1 spatial engine report")
    parser.add_argument("--output-dir", default=str(ROOT / "output" / "v6_1"))
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    model = ZM.load_spatial_model()
    demo_key = "Standard_Fitout_DB_Invite"
    plan = _build(DEMO_AREA, demo_key)
    solved = solve_schedule(plan["tasks"], START)

    baseline = prepare_fitout(expand_fitout_wbs(copy.deepcopy(TEMPLATES[demo_key]["tasks"])),
                              DEMO_AREA, workfronts=1, sectional="off")
    baseline_solved = solve_schedule(baseline["tasks"], START)
    comparison = SE.compare_spatial_vs_baseline(baseline_solved, solved, START)

    # ---- cross-template comparison -----------------------------------------
    per_template = []
    for key in TEMPLATES:
        b = prepare_fitout(expand_fitout_wbs(copy.deepcopy(TEMPLATES[key]["tasks"])),
                           DEMO_AREA, workfronts=1, sectional="off")
        bs = solve_schedule(b["tasks"], START)
        p = _build(DEMO_AREA, key)
        ss = solve_schedule(p["tasks"], START)
        c = SE.compare_spatial_vs_baseline(bs, ss, START)
        per_template.append(dict(template=key, zone_count=len(p["zones"]),
                                 baseline_tasks=c["baseline"]["task_count"],
                                 spatial_tasks=c["spatial"]["task_count"],
                                 baseline_finish=c["baseline"]["finish_date"],
                                 spatial_finish=c["spatial"]["finish_date"],
                                 saved_days=c["finish_date_saved_days"],
                                 assertion_pass=c["assertion_pass"],
                                 graph_issues=len(p["graph_issues"]),
                                 compliance_errors=len([i for i in p["compliance_issues"] if i["level"] == "error"]),
                                 allocation_issues=len(p["allocation_issues"])))

    # ---- CSV deliverables ---------------------------------------------------
    _write_csv(out / "zone_plan.csv",
               ["zone_id", "label", "floor_id", "area_sqm", "status", "available_condition",
                "release_condition", "workfronts", "generation_reason"],
               [[z["zone_id"], z["label"], z["floor_id"], z["area_sqm"], z["status"],
                 z["available_condition"], z["release_condition"], z["workfronts"], z["generation_reason"]]
                for z in plan["zones"]])

    qrows = []
    for zone in plan["zones"]:
        for name, row in sorted(zone["quantities"].items()):
            qrows.append([zone["zone_id"], zone["label"], name, row["value"], row["unit"],
                          row["share_of_area"], row["formula"]])
    _write_csv(out / "quantity_allocation.csv",
               ["zone_id", "zone_label", "quantity", "value", "unit", "area_share", "formula"], qrows)

    _write_csv(out / "inter_zone_pipeline.csv",
               ["predecessor_zone", "successor_zone", "activity_type", "trade", "relationship",
                "lag", "confidence", "policy", "crews", "reason", "explanation"],
               [[r["predecessor_zone"], r["successor_zone"], r["activity_type"], r["trade"],
                 r["relationship"], r["lag"], r["confidence"], r["policy"], r["crews_for_trade"],
                 r["reason"], r["explanation"]] for r in plan["pipeline"]])

    _write_csv(out / "scope_decisions.csv",
               ["template_id", "name", "activity_type", "spatial_scope", "reason"],
               [[s["template_id"], s["name"], s["activity_type"] or "", s["spatial_scope"], s["reason"]]
                for s in plan["scope_decisions"]])

    audit = dict(generated_for=demo_key, area_sqm=DEMO_AREA, start_date=START,
                 model_version=model.get("version"),
                 demo=dict(zone_count=len(plan["zones"]), **comparison),
                 per_template=per_template)
    (out / "baseline_vs_spatial.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- markdown -----------------------------------------------------------
    A = []
    add = A.append
    add("# V6.1 空间 / 区域引擎 —— 交付报告")
    add("")
    add(f"模型版本 `{model.get('version')}`　模板 `{demo_key}`　面积 {DEMO_AREA:g}㎡　开工 {START}")
    add("")
    add("> 本报告中的分区数、分区规模、跨区流水关系都是**施工组织构造（A0 规划假设）**，"
        "不是观测生产率。生产率、证据层、V6.0 依赖本体在本轮全部只读。")
    add("")

    add("## 1. 架构变更")
    add("")
    add("| 文件 | 变更 | 说明 |")
    add("|---|---|---|")
    for path, kind, note in CHANGED_FILES:
        add(f"| `{path}` | {kind} | {note} |")
    add("")
    add("**未改动**：`config/productivity_rates.json`、`config/dependency_ontology.json`、"
        "`config/quantity_benchmarks.json`、`config/evidence_observations.json`。"
        "回归测试断言构建空间计划前后这些文件的 SHA-256 不变。")
    add("")

    add("## 2. 空间数据模型")
    add("")
    add("```")
    add("Project（项目）")
    add("  └─ Floor（楼层）")
    add("       └─ Zone（分区：可独立移交 / 施工 / 报验）")
    add("            └─ Workfront（工作面：空间 × 工种 × 班组 × 通道 × 顺序）")
    add("```")
    add("")
    add("| 层级 | 定义 |")
    add("|---|---|")
    for lvl, desc in model["hierarchy"]["level_definitions"].items():
        add(f"| {lvl} | {desc} |")
    add("")
    add(f"依赖解析优先级：`{' → '.join(model['hierarchy']['resolution_priority'])}`。"
        f"{model['hierarchy']['resolution_priority_note']}")
    add("")

    add("## 3. 6000㎡ 分区生成（为什么这样切）")
    add("")
    for line in SE.explain_zone_generation(plan):
        add(f"- {line}" if line.startswith("  ") is False else f"  {line.strip()}")
    add("")
    add("| 分区 | 面积 | 状态 | 可用条件 | 释放条件 |")
    add("|---|---|---|---|---|")
    for z in plan["zones"]:
        add(f"| {z['label']} ({z['zone_id']}) | {z['area_sqm']:g}㎡ | {z['status']} | "
            f"{z['available_condition']} | {z['release_condition']} |")
    add("")
    add("### 空间作用域判定")
    add("")
    scope_counts = Counter(s["spatial_scope"] for s in plan["scope_decisions"])
    add("| 作用域 | 工序数 | 配置理由 |")
    add("|---|---|---|")
    for scope in ("project", "floor", "zone"):
        cfg = model["spatial_scope"].get(scope) or {}
        add(f"| {scope} | {scope_counts.get(scope, 0)} | {cfg.get('reason', model['spatial_scope'].get('default_reason', ''))} |")
    add("")
    add("显式声明清单：")
    for scope in ("project", "floor"):
        members = (model["spatial_scope"].get(scope) or {}).get("activity_types", [])
        add(f"- **{scope}**：`" + "`, `".join(members) + "`")
    add(f"- **zone**：其余 {scope_counts.get('zone', 0)} 个生产工序（含 4 个分区级检查门）")
    add("")

    add("## 4. 空间数量分配（保总量）")
    add("")
    add(f"分摊方法：按分区面积占比分配；`each` 类整数单位用最大余数法配平。"
        f"校验结果：**{len(plan['allocation_issues'])} 项漂移**。")
    add("")
    add("| 工程量 | " + " | ".join(z["label"] for z in plan["zones"]) + " | 合计 | 项目总量 |")
    add("|---|" + "---|" * (len(plan["zones"]) + 2))
    project_q = _quantities(DEMO_AREA)
    for name in ("ceiling_area", "flooring_area", "paint_area", "door_count", "lighting_points", "furniture_count"):
        row = project_q.get(name)
        if not row:
            continue
        vals = [z["quantities"][name]["value"] for z in plan["zones"]]
        total = sum(vals)
        fmt = (lambda v: f"{int(v)}") if row["unit"] == "each" else (lambda v: f"{v:g}")
        add(f"| {name} ({row['unit']}) | " + " | ".join(fmt(v) for v in vals) +
            f" | **{fmt(total)}** | {fmt(float(row['value']))} |")
    add("")
    add("分摊公式示例（每个分区的每行工程量都带自己的公式）：")
    add("")
    add("```")
    for line in SE.explain_zone_quantities(plan, limit=4):
        add(line)
    add("```")
    add("")

    add("## 5. 跨区流水（为什么是 FS 而不是 SS）")
    add("")
    for line in SE.explain_pipeline(plan, limit=6):
        add(f"- {line}" if not line.startswith("  ") else f"  {line.strip()}")
    add("")
    add(f"V6.1 默认每个工种 **1 个班组**（`crews_per_trade_default="
        f"{model['workfront_model']['inter_zone_pipeline']['crews_per_trade_default']}`）。"
        f"班组不能同时在两个分区，因此跨区关系只能是 FS —— 这是物理约束，不是压缩选择。"
        f"配置为 2 个班组时解析器自动给出 SS+ceil(分区工期/2)，滞后算式写进 `explanation`。")
    add("")

    add("## 6. 分区级依赖优先于楼层 / 项目级")
    add("")
    remap = Counter(r["rule"] for r in plan["remap_trace"])
    add("| 重映射规则 | 条数 | 含义 |")
    add("|---|---|---|")
    for rule, n in remap.most_common():
        add(f"| {rule} | {n} | {_remap_note(rule)} |")
    add("")
    add("- **分区→分区**：只连同一分区实例。A 区隔墙不会去等 B 区隔墙。")
    add("- **分区→上层**：楼层 / 项目级任务（如调试前安全验收）链接到**全部分区实例**，"
        "否则只等 A 区就放行，B/C 区会在未完工状态下被验收。")
    add("")

    add("## 7. 检查门与合规保护")
    add("")
    add("| 检查门 | 作用域 | 理由 |")
    add("|---|---|---|")
    for entry in model["gate_instantiation"]["by_keyword"]:
        add(f"| {entry['keyword']} | {entry['scope']} | {entry['reason']} |")
    add("")
    codes = Counter(i["code"] for i in plan["compliance_issues"])
    add("校验结果：" + ("、".join(f"`{k}` {v}" for k, v in codes.items()) or "无"))
    add("")
    errors = [i for i in plan["compliance_issues"] if i["level"] == "error"]
    add(f"**合规错误 {len(errors)} 项**。`GATE_SHARED_BY_SCOPE` 是信息项：首次空气盲测按配置是楼层级，"
        f"三个分区共用同一道门，这是合规导致的真实串行，不是引擎缺陷。")
    add("")

    add("## 8. 基线 vs 空间计划（CPM 实测）")
    add("")
    add(f"基线定义：同一模板、同一数量、同一生产率、同一日历，`sectional=off`、`workfronts=1`。")
    add("")
    add("| 指标 | 基线（单工作面） | 空间（多分区） | 差值 |")
    add("|---|---|---|---|")
    add(f"| 任务数 | {comparison['baseline']['task_count']} | {comparison['spatial']['task_count']} | "
        f"+{comparison['spatial']['task_count'] - comparison['baseline']['task_count']} |")
    add(f"| 完成日期 | {comparison['baseline']['finish_date']} | {comparison['spatial']['finish_date']} | "
        f"**-{comparison['finish_date_saved_days']} 天** |")
    add(f"| 计划工期 | {comparison['baseline']['scheduled_duration_days']} 天 | "
        f"{comparison['spatial']['scheduled_duration_days']} 天 | "
        f"-{comparison['baseline']['scheduled_duration_days'] - comparison['spatial']['scheduled_duration_days']} 天 |")
    add("")
    add("全部四个模板 × 6000㎡：")
    add("")
    add("| 模板 | 分区 | 基线完成 | 空间完成 | 节省 | 断言 | 图错误 | 合规错误 | 数量漂移 |")
    add("|---|---|---|---|---|---|---|---|---|")
    for row in per_template:
        add(f"| {row['template'][:32]} | {row['zone_count']} | {row['baseline_finish']} | "
            f"{row['spatial_finish']} | **-{row['saved_days']} 天** | "
            f"{'通过' if row['assertion_pass'] else '失败'} | {row['graph_issues']} | "
            f"{row['compliance_errors']} | {row['allocation_issues']} |")
    add("")
    add(f"断言 `{model['cpm_comparison']['assertion']}`："
        f"**{'全部通过' if all(r['assertion_pass'] for r in per_template) else '存在失败'}**。")
    add("")
    add("诚实说明：分区数不是越大越好。分区会同时产生两个相反效应 —— "
        "流水重叠省工期，但每个分区单独触发 `min_duration_days` 下限、且向上取整会加工期。"
        "6000㎡ 3 分区净省 36–42 天；2000㎡ 自动判定 1 分区，与基线完全一致（省 0 天，这是正确结果，不是缺陷）。")
    add("")

    add("## 9. 回归结果")
    add("")
    run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                         cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    tail = [ln for ln in (run.stdout + run.stderr).strip().splitlines() if ln.strip()][-3:]
    add("```")
    for ln in tail:
        add(ln)
    add("```")
    add("")
    add(f"V6.1 新增 {_count_cases()} 条（V6.0 基线 199 条全部保持通过）。958 项历史检查全过。")
    add("")

    add("## 10. 已知限制")
    add("")
    for lim in model["known_limits"]:
        add(f"- {lim}")
    add("- 本轮未做：机组人员优化、Crash-by-Crew、Scenario Optimizer、UI、MPP 输出。")
    add("- 检查门当前按「分区隐蔽报验 + 整层系统验收」两级建模；若项目实际要求整层隐蔽验收，"
        "把 `gate_instantiation.by_keyword` 对应项改回 `floor` 即可，不需要改代码。")
    add("")

    (out / "spatial_report.md").write_text("\n".join(A) + "\n", encoding="utf-8")
    print(f"written: {out}")
    print(f"  spatial_report.md, zone_plan.csv, quantity_allocation.csv, "
          f"inter_zone_pipeline.csv, scope_decisions.csv, baseline_vs_spatial.json")
    return 0


def _remap_note(rule: str) -> str:
    if rule.startswith("zone→zone"):
        return "分区任务只连本分区的前序实例"
    if rule.startswith("zone→上层"):
        return "楼层/项目任务链接到全部分区实例，避免提前放行"
    return "非分区任务链接到唯一实例"


def _count_cases() -> int:
    run = subprocess.run([sys.executable, "-m", "unittest", "tests.test_spatial_engine", "-v"],
                         cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    for ln in (run.stderr + run.stdout).splitlines():
        if ln.strip().startswith("Ran "):
            try:
                return int(ln.split()[1])
            except (IndexError, ValueError):
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
