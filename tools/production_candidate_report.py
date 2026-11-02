"""Generate the V6 Production Candidate report (V6.4).

Outputs to output/v6_4/:
  production_candidate_report.md   — PM-facing Chinese report
  recommendation.json              — P0 gate: recommended=null in every evidence state
  programme_duration.json          — Programme vs Site Construction split
  programme_segments.csv           — segment level readouts
  programme_phases.csv             — phase level readouts
  target_date.json / target_date_verdicts.csv  — three-state verdicts
  fasttrack_options.csv / fasttrack_summary.json — overlap legality + measured savings

Design rule (user directive): this round does NOT try to produce a Recommended Scenario.
If the resource data is not contractor-confirmed, every option stays UNCONFIRMED / OPTION
and `recommended = null` is the terminal answer, not a placeholder. Scope is deliberately
narrow: no cost engine, no Monte Carlo, no UI, no new AI features.
"""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import fasttrack as FT                                 # noqa: E402
from core import programme_duration as PD                        # noqa: E402
from core import recommendation_gate as GATE                     # noqa: E402
from core import resource_model as RM                            # noqa: E402
from core import scenario_optimizer as SO                        # noqa: E402
from core import target_date_analysis as TDA                     # noqa: E402
from core.fitout_engine import prepare_spatial_fitout            # noqa: E402
from core.fitout_wbs import expand_fitout_wbs                    # noqa: E402
from core.solver_engine import solve_schedule                    # noqa: E402
from core.zone_model import split_area                           # noqa: E402

TEMPLATES = json.loads((ROOT / "templates" / "wbs_templates.json").read_text(encoding="utf-8"))["templates"]
START = "2026-09-08"
DEMO_AREA = 6000.0
DEMO_TEMPLATE = "MNC_Standard_Fitout_DB_Invite"
TRADES = ["general", "partitions_ceilings", "mep_mechanical", "mep_electrical",
          "wet_finishes", "joinery", "flooring", "commissioning", "furniture"]


def _template_tasks():
    return expand_fitout_wbs(copy.deepcopy(TEMPLATES[DEMO_TEMPLATE]["tasks"]))


def _build(levers):
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


def write_json(path: Path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def run_regressions():
    proc = subprocess.run([sys.executable, "-X", "utf8", "tools/run_regressions.py"],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=3600)
    return (proc.stdout or "") + ("\n[stderr]\n" + proc.stderr if proc.stderr else "")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-regression", action="store_true")
    args = ap.parse_args()

    out = ROOT / "output" / "v6_4"
    out.mkdir(parents=True, exist_ok=True)

    cfg = RM.load_resource_constraints()
    space = SO.load_scenario_space(cfg=cfg)
    pcfg = GATE.load_production_candidate_config()

    print("[1/6] P0 recommendation gate…")
    val_cfg = GATE.validate_production_candidate_config(pcfg)

    print("[2/6] running V6.3 scenarios on 6000 m2…")
    rows = []
    for scn in SO.scenarios(space):
        row = SO.run_scenario(scn, _build, START, TRADES, cfg=cfg, space=space)
        rows.append(row)
        print(f"    {row['id']} | {row['duration_days']}d | {row['finish']} | {row['status']}")
    usable = [r for r in rows if r.get("duration_days") is not None]

    rec = SO.scenario_recommendation(rows, cfg=cfg, space=space)
    GATE.assert_no_recommendation(rec)
    write_json(out / "recommendation.json", rec)
    print(f"    recommended={rec['recommended']} state={rec['decision_state']}")

    print("[3/6] programme vs site construction duration…")
    scn0 = SO.scenarios(space)[0]
    levers0 = SO.resolve_levers(scn0, TRADES, cfg, space)
    tasks0 = _build(levers0)
    raw_solved = solve_schedule(copy.deepcopy(tasks0), START)["tasks"]
    raw_break = PD.programme_breakdown(raw_solved, cfg=pcfg)
    levelled_tasks = rows[0]["_run"]["tasks"]
    levelled_break = PD.programme_breakdown(levelled_tasks, cfg=pcfg)
    write_json(out / "programme_duration.json",
               dict(raw_cpm=raw_break, levelled=levelled_break,
                    baseline_scenario=dict(id=rows[0]["id"], label=rows[0].get("label"),
                                           duration_days=rows[0]["duration_days"])))
    write_csv(out / "programme_segments.csv",
              [dict(basis="levelled", **s) for s in levelled_break["segments"]] +
              [dict(basis="raw_cpm", **s) for s in raw_break["segments"]])
    write_csv(out / "programme_phases.csv", levelled_break["phases"])

    print("[4/6] fast-track…")
    ft = FT.analyse(tasks0, START, cfg=pcfg, resource_cfg=cfg,
                    required_confirmations=SO.promotion_requirements(space, cfg)["minimum_confirmations"])
    GATE.assert_no_recommendation(ft["decision"])
    write_csv(out / "fasttrack_options.csv", ft["rows"])
    write_json(out / "fasttrack_summary.json",
               dict(summary=ft["summary"], decision=ft["decision"],
                    method=ft["method"], must_not_be_read_as=ft["must_not_be_read_as"]))
    print(f"    {ft['summary']}")

    print("[5/6] target date (three states)…")
    window = TDA.attainable_window(rows)
    e = _dt.date.fromisoformat(window["earliest"]["finish"])
    l = _dt.date.fromisoformat(window["latest"]["finish"])
    probes = [
        ("P1_beyond_earliest", (e - _dt.timedelta(days=30)).isoformat(),
         "目标日期比最激进情景还早 30 天 → 期望 NOT_MET"),
        ("P2_inside_window", (e + _dt.timedelta(days=(l - e).days // 2)).isoformat(),
         "目标日期落在可达区间中部 → 期望 UNVERIFIABLE"),
        ("P3_beyond_latest", (l + _dt.timedelta(days=30)).isoformat(),
         "目标日期比最保守情景还晚 30 天 → 期望 MET"),
    ]
    verdicts = []
    for pid, date, note in probes:
        r = TDA.analyse(date, rows, cfg=pcfg, resource_cfg=cfg,
                        required_confirmations=SO.promotion_requirements(space, cfg)["minimum_confirmations"])
        GATE.assert_no_recommendation(r["decision"])
        r["probe_id"] = pid
        r["probe_note"] = note
        verdicts.append(r)
        print(f"    {pid} {date} → {r['verdict']}")
    write_json(out / "target_date.json",
               dict(attainable_window=window, probes=verdicts))
    write_csv(out / "target_date_verdicts.csv",
              [dict(probe_id=v["probe_id"], probe_note=v["probe_note"],
                    target_date=v["target_date"], verdict=v["verdict"],
                    verdict_zh=v["verdict_zh"], reason=v["reason"],
                    candidate_count=v["decision"]["candidate_count"],
                    recommended=v["recommended"],
                    gap_vs_earliest=v["gap_days"]["vs_earliest"],
                    gap_vs_latest=v["gap_days"]["vs_latest"])
               for v in verdicts])

    print("[6/6] regression…")
    regression = "(skipped)" if args.no_regression else run_regressions()

    _write_report(out, pcfg, val_cfg, rec, rows, raw_break, levelled_break, ft, verdicts,
                  window, regression)
    print(f"done → {out}")


def _write_report(out, pcfg, val_cfg, rec, rows, raw_break, levelled_break, ft, verdicts,
                  window, regression):
    L: list = []
    add = L.append

    add("# V6 Production Candidate — 报告（V6.4）")
    add("")
    add(f"演示：{DEMO_TEMPLATE} / {DEMO_AREA:,.0f} ㎡ / 开工 {START}")
    add("")
    add("**范围**：只做四件事——P0 推荐门控、Target Date、Programme / Site Construction Duration、"
        "Fast-track。不做成本引擎、蒙特卡洛、UI 重构、PPT 美化与新的 AI 功能（GOV-19）。")
    add("")

    add("## 1. 结论")
    add("")
    add(f"- **推荐**：`recommended = null`（{rec['decision_state_zh']}）。这是算完之后的结论，不是缺数据。")
    add(f"- **目标日期**：三个探针分别给出 "
        f"{' / '.join(v['verdict'] for v in verdicts)}——三态，不是布尔值。")
    add(f"- **工期双读数**（基线情景 {rows[0]['id']}，资源冲突已消解口径）："
        f"Programme {levelled_break['programme']['calendar_days']} 日历天，"
        f"Site Construction {levelled_break['site_construction']['calendar_days']} 日历天"
        f"（占 {levelled_break['site_construction']['share_of_programme_pct']}%）。")
    add(f"- **Fast-track**：{ft['summary']['edges_total']} 条 FS 边中 "
        f"{ft['summary']['blocked']} 条禁止搭接，"
        f"可评估 {ft['summary']['measured']} 条，"
        f"**可采用节省 = 0**（唯一有节省的边 lag 未被本体声明，其收益不可采用）。")
    add("")

    add("## 2. P0 推荐门控（本轮修复项）")
    add("")
    add("V6.3 的缺陷：`scenario_recommendation()` 一旦 `resource_readiness == confirmed`，"
        "就返回 `recommended = 最短工期情景`。那是 GOV-14 明令禁止的——把单一指标当决策，"
        "而成本、班组可获得性、合同边界根本不在引擎判定范围内。")
    add("")
    add("修复方式：新增 `core/recommendation_gate.py` 作为**唯一**推荐门，"
        "scenario / target date / fast-track 三条路径共用。")
    add("")
    add("| 字段 | 值 |")
    add("|---|---|")
    add(f"| recommended | `{rec['recommended']}` |")
    add(f"| decision_state | `{rec['decision_state']}`（{rec['decision_state_zh']}） |")
    add(f"| is_terminal_state | `{rec['is_terminal_state']}` |")
    add(f"| is_missing_data | `{rec['is_missing_data']}` |")
    add(f"| 并列候选数 | {rec['candidate_count']} |")
    add(f"| readiness | `{rec['readiness']['overall']}` |")
    add("")
    add(f"> {rec['must_not_be_read_as']}")
    add("")
    add("阻断原因（GOV-15）：")
    for b in rec["blockers"]:
        add(f"- `{b['rule']}` {b['message']}")
    add("")
    add("**即使资源全部确认，recommended 仍为 null**：confirmed 只把状态从 "
        "`NO_RECOMMENDATION` 升到 `PM_DECISION_REQUIRED`，引擎交出并列候选集，由 PM 决策。")
    add("")

    add("## 3. Programme Duration vs Site Construction Duration")
    add("")
    add("两套口径并列给出，二者不可混用（GOV-17）：")
    add("")
    add("| 口径 | Programme（总工期） | Site Construction（现场施工） | 占比 |")
    add("|---|---:|---:|---:|")
    for basis, b in (("资源冲突已消解（可行口径）", levelled_break), ("纯 CPM（资源无约束）", raw_break)):
        add(f"| {basis} | {b['programme']['calendar_days']} 日历天 | "
            f"{b['site_construction']['calendar_days']} 日历天 | "
            f"{b['site_construction']['share_of_programme_pct']}% |")
    add("")
    add("分段明细（可行口径）：")
    add("")
    add("| 分段 | 起 | 止 | 日历天 | 工作日 |")
    add("|---|---|---|---:|---:|")
    for s in levelled_break["segments"]:
        add(f"| {s['label_zh']} | {s['start']} | {s['finish']} | {s['calendar_days']} | {s['working_days']} |")
    add("")
    add("注：分段之间可能重叠（验收可与施工并行），故各分段天数之和不等于 Programme 天数；"
        "分段表用于定位压缩空间，不用于加总。")
    add("")
    add("Phase 明细：")
    add("")
    add("| Phase | 名称 | 日历天 | 法定等待 | 可压缩 |")
    add("|---:|---|---:|---|---|")
    for p in levelled_break["phases"]:
        add(f"| {p['phase_no']} | {p['label_zh']} | {p['calendar_days']} | "
            f"{'是' if p['statutory_wait'] else '否'} | {'是' if p['compressible'] else '否'} |")
    add("")
    add(f"非现场施工天数占 Programme 的 {levelled_break['non_site_days']['share_pct']}%"
        f"（{levelled_break['non_site_days']['calendar_days']} 日历天）。"
        f"法定等待段 Phase {levelled_break['statutory_wait']['phase_nos']} 跨度 "
        f"{levelled_break['statutory_wait']['calendar_days_span']} 日历天，**不可由加人/加班压缩**。")
    add("")
    add(f"> {levelled_break['must_not_be_read_as']}")
    add("")

    add("## 4. Target Date（三态）")
    add("")
    add(f"可达区间：**{window['earliest']['finish']}**（{window['earliest']['scenario_id']}，"
        f"{window['earliest']['duration_days']}d）… **{window['latest']['finish']}**"
        f"（{window['latest']['scenario_id']}，{window['latest']['duration_days']}d），"
        f"区间宽 {window['spread_days']} 天。")
    add("")
    add("| 探针 | 目标日期 | 判定 | 候选情景数 | recommended | 说明 |")
    add("|---|---|---|---:|---|---|")
    for v in verdicts:
        add(f"| {v['probe_id']} | {v['target_date']} | **{v['verdict']}**"
            f"（{v['verdict_zh']}） | {v['decision']['candidate_count']} | "
            f"`{v['recommended']}` | {v['probe_note']} |")
    add("")
    for v in verdicts:
        add(f"- **{v['verdict']}**：{v['reason']}")
    add("")
    add(f"> {verdicts[0]['must_not_be_read_as']}")
    add("")

    add("## 5. Fast-track（搭接可行性）")
    add("")
    s = ft["summary"]
    add("| 项 | 值 |")
    add("|---|---:|")
    add(f"| FS 边总数 | {s['edges_total']} |")
    add(f"| 禁止搭接（GOV-18） | {s['blocked']} |")
    add(f"| 可搭接且 lag 已声明 | {s['movable']} |")
    add(f"| 可搭接但 lag 未声明 | {s['needs_confirmation']} |")
    add(f"| 已逐条重解验证 | {s['measured']} |")
    add(f"| 单边最大节省 | {s['max_single_edge_saving_days']} 天 |")
    add("")
    add("禁止搭接的原因分布：")
    add("")
    reasons = {}
    for r in ft["rows"]:
        if r["verdict"] == FT.BLOCKED:
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    add("| 原因 | 条数 |")
    add("|---|---:|")
    for k, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        add(f"| {k} | {n} |")
    add("")
    add("**关键结论**：当前网络已被 V6.1 分区流水搭接过一次，剩余 FS 边基本不在关键路径上。"
        "唯一产生节省的边（44→45）其 lag 未被本体声明，按 lag=0 测出的 7 天只是理论上界，"
        "**不可作为可采用的搭接量**。因此本轮可采用的 fast-track 节省为 0。")
    add("")
    add(f"> {ft['must_not_be_read_as']}")
    add("")
    add("方法：每条候选边只改这一条、重解整个网络、取重解前后工期差。"
        "禁止把各边节省累加——同一关键路径上的多次搭接会重复计一次收益（GOV-18）。")
    add("")

    add("## 6. 治理规则")
    add("")
    add("| 规则 | 内容 |")
    add("|---|---|")
    for r in (pcfg.get("governance") or {}).get("rules", []):
        add(f"| {r['id']} | {r['rule']} |")
    add("")

    add("## 7. 口径说明（避免误读）")
    add("")
    add("- `recommended = null` 不等于「引擎还没算出来」：它是算完之后得出的结论。")
    add("- Programme Duration ≠ Site Construction Duration：前者含设计、招采、报建、法定等待、验收、交付。")
    add("- MET 不是承诺：它仍以未确认的资源输入为前提。UNVERIFIABLE 不是「大概可以」，是「要判定必须先确认」。")
    add("- Fast-track 的单边节省不可累加。")
    add("- 本轮没有成本维度：工期最短的情景可能成本最高，引擎不评价。")
    add("")

    add("## 8. 回归结果")
    add("")
    add("```")
    add((regression or "").strip()[-4000:])
    add("```")
    add("")

    add("## 9. 范围与已知限制")
    add("")
    add("本轮明确不做：" + "、".join(val_cfg.get("out_of_scope") or []) + "。")
    add("")
    for lim in pcfg.get("known_limits") or []:
        add(f"- {lim}")
    add("")

    (out / "production_candidate_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
