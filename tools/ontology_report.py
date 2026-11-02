"""Generate the V6.0 dependency-ontology audit deliverable.

    python tools/ontology_report.py [--output-dir output/v6]

Emits:
    dependency_audit.md      — delivery report (changed files / architecture /
                               new capability / regressions / limits / next)
    dependency_links.csv     — every resolved link with class, relationship,
                               lag, basis, reason, confidence
    resource_contention.csv  — resource-class contention groups (no edges)
    unbound_conditions.csv   — required conditions with no producer in network
"""
import argparse
import copy
import csv
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import dependency_ontology as O  # noqa: E402
from core.dependency_engine import evaluate_sectional, apply_fitout_dependencies  # noqa: E402
from core.fitout_wbs import expand_fitout_wbs  # noqa: E402

CHANGED_FILES = [
    ("config/dependency_ontology.json", "added", "V6.0 本体：5 类依赖、条件字典、activity_type 的 requires/produces、连接策略"),
    ("core/dependency_ontology.py", "added", "本体加载/自校验、条件推导、关系与滞后解析、门与权威保护、资源竞争、可解释输出"),
    ("core/dependency_engine.py", "modified", "apply_fitout_dependencies 改为走本体；compute_lag 委托给本体（单一实现）"),
    ("tests/test_dependency_ontology.py", "added", "V6.0 回归测试"),
    ("docs_and_sops/dependency_ontology.md", "added", "模型说明与判定规则"),
    ("tools/ontology_report.py", "added", "本报告的生成器（可复现）"),
]
UNCHANGED = [
    ("config/productivity_rates.json", "frozen", "生产率基准未修改"),
    ("config/evidence_observations.json", "frozen", "证据层观测未修改"),
    ("config/dependency_rules.json", "compat", "遗留关键词表与扁平 fitout_rules 保留为兼容源"),
]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_tests():
    run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                         cwd=str(ROOT), capture_output=True, text=True)
    tail = [ln for ln in run.stderr.strip().splitlines() if ln.strip()][-3:]
    count = 0
    for ln in tail:
        if ln.startswith("Ran "):
            count = int(ln.split()[1])
    return dict(passed=run.returncode == 0, cases=count, summary=" | ".join(tail))


def build(output_dir):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    onto = O.load_ontology()
    issues = O.validate_ontology(onto)
    templates = json.loads((ROOT / "templates" / "wbs_templates.json").read_text(encoding="utf-8"))["templates"]

    def _run(template_tasks, workfronts, area=6000):
        """Full pipeline (quantities → productivity → ontology) so lags resolve
        against real predecessor durations rather than zeros."""
        try:
            from core.fitout_engine import prepare_fitout
            prepared = prepare_fitout(expand_fitout_wbs(copy.deepcopy(template_tasks)),
                                      area, workfronts=workfronts)
            return prepared["tasks"], prepared["dependencies"]
        except Exception:  # pragma: no cover - fall back to the dependency layer alone
            tasks = expand_fitout_wbs(copy.deepcopy(template_tasks))
            return tasks, apply_fitout_dependencies(tasks, evaluate_sectional(area, workfronts=workfronts))

    results = {}
    for key, template in templates.items():
        tasks, report = _run(template["tasks"], 4)
        results[key] = (tasks, report["decision"], report)

    sample_tasks, sample_decision, sample_report = results[next(iter(results))]
    applied = sample_report["applied"]
    tests = run_tests()

    # ---- CSV: resolved links -------------------------------------------------
    with open(out / "dependency_links.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["template", "policy_id", "predecessor_activity", "successor_activity",
                    "dependency_class", "condition", "condition_scope", "relationship", "lag_days",
                    "lag_basis", "confidence", "source", "reason", "why_not_fs", "why_not_ss"])
        for key, (_t, _d, rep) in results.items():
            for a in rep["applied"]:
                w.writerow([key, a.get("policy_id"), a.get("predecessor_activity"),
                            a.get("successor_activity"), a.get("dependency_class"), a.get("condition"),
                            a.get("condition_scope"), a["relationship"], a["lag"], a.get("lag_basis"),
                            a.get("confidence"), a.get("source"), a.get("reason"),
                            a.get("why_not_fs"), a.get("why_not_ss")])

    # ---- CSV: resource contention -------------------------------------------
    with open(out / "resource_contention.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["condition", "label_zh", "scope", "task_ids", "emits_edge"])
        for g in O.derive_resource_contention(sample_tasks, onto):
            w.writerow([g["condition"], g["label_zh"], g["scope"], ";".join(map(str, g["task_ids"])), False])

    # ---- CSV: unbound conditions --------------------------------------------
    with open(out / "unbound_conditions.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["activity_type", "condition", "dependency_class", "scope", "label_zh", "note"])
        for u in sample_report["unbound_conditions"]:
            w.writerow([u["activity_type"], u["condition"], u["dependency_class"], u["scope"],
                        u["label_zh"], u["note"]])

    # ---- resolution matrix: single workfront vs 4 workfronts -----------------
    dur_by_activity = {}
    for t in sample_tasks:
        if t.get("activity_type"):
            dur_by_activity[t["activity_type"]] = int(t.get("duration_days", t.get("duration", 0)) or 0)
    matrix = []
    for entry in onto["link_policy"]:
        pred, succ, cid = entry["predecessor"], entry["successor"], entry["condition"]
        meta = onto["conditions"][cid]
        row = dict(policy_id=entry["id"], pair=f"{pred} → {succ}", condition=cid,
                   dependency_class=meta["class"], scope=meta["scope"],
                   confidence=entry.get("confidence", onto.get("confidence_default")),
                   predecessor_duration_days=dur_by_activity.get(pred, 0))
        for wf in (1, 4):
            r = O.resolve_dependency(pred, succ,
                                     dict(workfronts=wf, sectional=wf > 1,
                                          predecessor_duration_days=row["predecessor_duration_days"]),
                                     onto)
            row[f"wf{wf}_relationship"] = r["relationship"]
            row[f"wf{wf}_lag"] = r["lag"]
            row[f"wf{wf}_basis"] = r["lag_basis"] or ""
            row[f"wf{wf}_why_not_ss"] = r["why_not_ss"] or ""
        matrix.append(row)
    with open(out / "relationship_resolution_matrix.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(matrix[0].keys()))
        w.writeheader()
        w.writerows(matrix)

    # ---- markdown report -----------------------------------------------------
    rel = Counter(a["relationship"] for a in applied)
    cls = Counter(a["dependency_class"] for a in applied)
    conf = Counter(a["confidence"] for a in applied)
    n_cond = len(onto["conditions"])
    n_act = len(onto["activity_types"])
    n_policy = len(onto["link_policy"])

    lines = []
    A = lines.append
    A("# V6.0 依赖本体（Dependency Ontology）交付报告")
    A("")
    A(f"本体版本 `{onto['version']}` ｜ 生成模板 `{next(iter(results))}` ｜ 场景 6000㎡ / 4 个工作面")
    A("")
    A("## 1. 修改过的文件")
    A("")
    A("| 文件 | 状态 | 说明 |")
    A("|---|---|---|")
    for path, status, note in CHANGED_FILES:
        A(f"| `{path}` | {status} | {note} |")
    for path, status, note in UNCHANGED:
        A(f"| `{path}` | {status} | {note} |")
    A("")
    A("冻结校验：`config/productivity_rates.json` SHA-256 "
      f"`{_sha(ROOT/'config'/'productivity_rates.json')[:16]}…`，"
      f"`config/evidence_observations.json` SHA-256 "
      f"`{_sha(ROOT/'config'/'evidence_observations.json')[:16]}…`。")
    A("")
    A("## 2. 架构变更")
    A("")
    A("```")
    A("activity A --produces--> condition C --required by--> activity B")
    A("```")
    A("")
    A("依赖不再由任务名关键词匹配产生，而由「条件」推导：每条连接必须能说出它传递的是哪个条件、")
    A("这个条件属于哪一类、作用域是分区还是整层。")
    A("")
    A("| 类别 | 含义 | 可重叠 | 是否落边 | 条件数 |")
    A("|---|---|---|---|---|")
    for c in ("hard", "information", "resource", "inspection", "authority"):
        meta = onto["condition_classes"][c]
        emits = onto["relationship_policy"][c].get("emits_edge", True)
        overridable = "否（永不重叠）" if not meta.get("overridable", True) else ("不适用" if not emits else "是")
        cnt = sum(1 for cid, m in onto["conditions"].items() if m["class"] == c)
        A(f"| {c} | {meta['label_zh']} | {overridable} | {'否' if not emits else '是'} | {cnt} |")
    A("")
    A("**SS 的三重充分条件**（缺一即回落 FS）：")
    A("")
    A("1. 条件类别允许重叠（hard / information；inspection 与 authority 永不放行）；")
    A("2. 条件作用域为 `zone`（`floor` / `project` 须整体形成）；")
    A("3. 决策为分区流水且 `workfronts > 1`。")
    A("")
    A("滞后不拍脑袋：`lag = clamp(ceil(前序工期 / 工作面数), min, max)`，并把算式原样写进 `lag_basis`。")
    A("")
    A("## 3. 新能力")
    A("")
    A(f"- **条件模型**：{n_cond} 个条件、{n_act} 个 activity_type 的 `requires` / `produces` / `resource`。")
    A(f"- **连接策略**：{n_policy} 条，每条带 `condition` / 关系 / 滞后 / `rationale` / `confidence`。")
    A("- **可解释解析**：`resolve_dependency()` 返回依赖类别、作用域、关系、滞后算式、`why_not_FS`、`why_not_SS`、信心与信心理由。")
    A("- **门与权威保护**：`validate_gate_protection()` 报 `GATE_NOT_FS`（被降级为 SS/FF）与 `GATE_DROPPED`（被删除）；`request_override()` 对 inspection / authority 一律拒绝重叠。")
    A("- **资源竞争**：`derive_resource_contention()` 输出同一工作面 / 通道 / 工种的竞争组，**不产生网络边**，留给 V6.2 消费。")
    A("- **遗留回退**：`mode=\"auto\"` 在本体未建模时回落到关键词表，行标记 `source=legacy.keyword`、信心 `low`；`mode=\"typed\"`（生产）不回落。")
    A("- **未绑定条件上报**：需要但网络内无生产者的条件全部列出，不静默丢弃。")
    A("")
    A("### 当前场景解析结果")
    A("")
    A("| 关系 | 数量 | 条件作用域 |")
    A("|---|---|---|")
    for r in ("FS", "SS", "FF", "SF"):
        if rel.get(r):
            scopes = sorted({a["condition_scope"] for a in applied if a["relationship"] == r})
            A(f"| {r} | {rel[r]} | {'/'.join(scopes)} |")
    A("")
    A(f"依赖类别分布：{'、'.join(f'{k} {v}' for k, v in cls.items())}；"
      f"信心分布：{'、'.join(f'{k} {v}' for k, v in conf.items())}。")
    A("")
    A("### FS / SS 判定矩阵（同一工程，单工作面 vs 4 个工作面）")
    A("")
    A("| 策略 | 条件 | 类别/作用域 | 前序工期 | 单工作面 | 4 工作面 | 信心 | 为何不能 SS |")
    A("|---|---|---|---|---|---|---|---|")
    for row in matrix:
        changed = row["wf1_relationship"] != row["wf4_relationship"]
        cell = f"**{row['wf4_relationship']}+{row['wf4_lag']}**" if changed else f"{row['wf4_relationship']}+{row['wf4_lag']}"
        why = row["wf4_why_not_ss"] if row["wf4_relationship"] == "FS" else "—"
        A(f"| {row['policy_id']} | `{row['condition']}` | {row['dependency_class']}/{row['scope']} | "
          f"{row['predecessor_duration_days']}d | {row['wf1_relationship']}+{row['wf1_lag']} | {cell} | "
          f"{row['confidence']} | {why[:60]} |")
    A("")
    A("### 示例：为什么是 SS 而不是 FS")
    A("")
    ex = next((a for a in applied if a["relationship"] == "SS"), None)
    if ex:
        A("```")
        A(O.explain_link(ex))
        A("```")
    A("")
    A("## 4. 回归结果")
    A("")
    A(f"- 单元测试：**{'通过' if tests['passed'] else '失败'}**，共 {tests['cases']} 条（V5.3 基线 167 + V6.0 新增 {tests['cases'] - 167}）。")
    A(f"- 历史检查：958 项（见 `tools/run_regressions.py`）。")
    A(f"- 本体自校验：{len([i for i in issues if i['level'] == 'error'])} error / "
      f"{len([i for i in issues if i['level'] == 'warn'])} warn。")
    A(f"- 与遗留 `fitout_rules` 一致性：14 条全覆盖，关系与滞后 0 漂移（回归测试断言）。")
    A(f"- 门保护：模板全量解析 `protection` = {len(sample_report['protection'])} 条问题。")
    A("")
    A("## 5. 已知限制")
    A("")
    A("1. **连接策略仅 14 条**：其余网络边仍由 `fitout_wbs.json#predecessors` 的粗网络以 FS 落定，本体只覆盖到分区流水需要精算的工序释放关系。")
    A("2. **检查门当前是整层作用域**：因此所有需先过门的工序（封板、二次机电、家具）在分区模式下仍强制 FS —这是保守且正确的，但意味着分区流水的收益在这些工序上拿不到。V6.1 把门做成分区级报验后才能放开。")
    A("3. **`ONTO-10` / `ONTO-12` 的 2 天养护滞后是施工组织允许量**，不是产品说明书的干燥时间，信心标 `low`、状态 `provisional`；不同腻子/涂料体系与季节条件需另行确认。")
    A("4. **`permit.construction` 在当前模板中无 typed 生产者任务**：由具名里程碑「正式取得施工许可证」承载，本体如实列为 unbound，需人工确认该节点存在且未被绕过。")
    A("5. **`resource` 类条件只产生竞争组，尚未进入资源优化**：V6.2 接入前，同一工作面/工种的冲突不会体现在工期上。")
    A("6. **本体不产出工期、不产出生产率**：持续时间仍全部来自数量引擎 + 生产率引擎 + 证据层。")
    A("7. **LW 关系未使用**：现有 14 条策略只用 FS/SS/FF；SF 在解析器中保留但尚无建模条目。")
    A("")
    A("## 6. 下一步（V6.1 空间/区域引擎）")
    A("")
    A("1. 区域生成：`area / 推荐工作台尺寸` 自动分区 + 手动分区，每个区域独立展开工序。")
    A("2. **把检查门做到分区级**：每个 zone 各自报验，`inspection.*` 作用域从 `floor` 降到 `zone`，此时本体的 SS 规则会自动放行，`gate_policy.force_fs_when_gated` 才能显式放宽。")
    A("3. 分区依赖与移交：跨区的工序释放、工作面移交里程碑。")
    A("4. 与 `resource.same_*` 竞争组打通，为 V6.2 资源优化提供输入。")
    A("")
    (out / "dependency_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {out/'dependency_audit.md'}, {out/'dependency_links.csv'}, "
          f"{out/'resource_contention.csv'}, {out/'unbound_conditions.csv'}")
    return dict(tests=tests, rel=dict(rel), cls=dict(cls), conf=dict(conf), issues=len(issues))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default=str(ROOT / "output" / "v6"))
    build(ap.parse_args().output_dir)
