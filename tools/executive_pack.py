# -*- coding: utf-8 -*-
"""V6.6.1 — write the executive pack for one completed run.

Read-only with respect to the schedule: this tool never touches ``audit/``,
``baseline/``, ``config/`` or ``templates/``. It writes only into
``<run>/reports/executive_pack/``.

Usage::

    python tools/executive_pack.py                     # latest run under output_mpp
    python tools/executive_pack.py --run output_mpp/run_20260915_142632
    python tools/executive_pack.py --all               # every run found
    python tools/executive_pack.py --with-scenarios    # add a re-solved scenario comparison
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import executive_pack as EP  # noqa: E402

OUTDIR_NAME = "executive_pack"


# --------------------------------------------------------------------------- #
# markdown renderers
# --------------------------------------------------------------------------- #

def _null(value) -> str:
    """Render JSON null as `null` rather than Python's `None` in reader-facing markdown."""
    return "null" if value is None else str(value)


def _md_table(headers, rows) -> str:
    L = ["| " + " | ".join(str(h) for h in headers) + " |",
         "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        L.append("| " + " | ".join("—" if c is None else str(c) for c in r) + " |")
    return "\n".join(L)


def _provenance(pack, view) -> list:
    """Every view carries the same provenance footer: as-of, version, hash, completeness."""
    h = pack["header"]
    line = (f"- completeness: **{view.get('completeness', 'UNKNOWN')}**")
    if view.get("missing_inputs"):
        line += f" — missing: {', '.join(view['missing_inputs'])}"
    return [f"- as-of: {h['as_of']} · generated at `{view['generated_at']}` · "
            f"engine version {h['engine_version']}",
            f"- project: {h['project_name']} · run id: `{h['run_id']}`",
            f"- input hash: `{h['input_hash']}`",
            line,
            f"- read only: **{view['read_only']}** · applied to baseline: "
            f"**{view['applied']}** · baseline unchanged: **{view['baseline_unchanged']}**"]


def render_procurement(pack) -> str:
    view = pack["procurement_intelligence"]
    L = ["# Procurement Intelligence (read-only view)", ""] + _provenance(pack, view)
    L += ["", f"Ordering: {view['attention_ordering_rule']}", ""]
    L += ["## Attention list", "",
          _md_table(["#", "kind", "id", "label", "state", "reason"],
                    [[i + 1, r["kind"], r["id"], r["label"], r["status"], r["reason"]]
                     for i, r in enumerate(view["attention_list"])]), ""]
    L += ["## Scheduled procurement packages", ""]
    for pkg in view["packages"]:
        w = pkg.get("window") or {}
        L += [f"### {pkg['package_id']} — {pkg['label']}", "",
              f"- window: {w.get('start')} → {w.get('finish')}",
              f"- state: **{pkg['status']}**",
              f"- activities: {pkg['task_count']}", ""]
        for reason in pkg["reasons"]:
            L.append(f"  - reason: {reason}")
        if pkg["linked_milestones"]:
            L += ["", "  - potentially affected milestones (impact is *potential*; no date is changed):"]
            for m in pkg["linked_milestones"]:
                L.append(f"    - {m['milestone_id']} {m['label']} ({m['finish']}) "
                         f"— `{m['impact_assessment']}`")
        L += ["", "  - source: " + ", ".join(f"`{s}`" for s in pkg["source_fields"]), ""]
    L += ["## Configured long-lead items", "",
          "Catalogue entries whose lead time is *not* carried by any scheduled activity "
          "are shown as `LEAD_NOT_SCHEDULED`. Only the row the engine itself declares "
          "(`engine-declared = True`) is treated as a programme gap.", "",
          _md_table(["id", "item", "lead days", "scheduled", "engine-declared", "state"],
                    [[i["item_id"], i["label"], i.get("lead_days"), i["scheduled"],
                      i.get("engine_expected"), i["status"]]
                     for i in view["lead_items"]]), ""]
    declared = [i for i in view["lead_items"]
                if i.get("engine_expected") and i["status"] == "LEAD_NOT_SCHEDULED"]
    for i in declared:
        L += [f"> Engine-declared gap — `{i['item_id']}`: {i['note']}", ""]
    cl = view.get("configured_lead_items_without_scheduled_activity") or {}
    if cl:
        L += [f"Configured lead items with no matching scheduled activity: **{cl['count']}**", "",
              f"> {cl['note']}", ""]
    legal = view["legal_applicability"]
    L += ["## Statutory tendering applicability", "",
          f"- value: **{legal['value']}** (status `{legal['status']}`)",
          f"- {legal['statement']}"]
    for a in legal["assumptions"]:
        L.append(f"  - assumption: {a}")
    for s in legal["sources"]:
        L.append(f"  - source: {s}")
    L += ["", f"- evidence incomplete: **{view['evidence_incomplete']}**"]
    if view.get("evidence_incomplete_note"):
        L.append(f"  - {view['evidence_incomplete_note']}")
    if view["gaps"]:
        L += ["", "## Gaps", ""] + [f"- {g}" for g in view["gaps"]]
    L.append("")
    return "\n".join(L)


def render_milestones(pack) -> str:
    view = pack["executive_milestones"]
    L = ["# Executive Milestones (read-only view)", ""] + _provenance(pack, view)
    L += ["", f"> {view['status_note']}", "", f"Ordering: {view['ordering_rule']}", ""]
    L += [_md_table(["id", "milestone", "finish", "phase", "state", "evidence", "critical", "slack"],
                    [[m["milestone_id"], m["label"], m["finish"], m["semantic_phase"],
                      m["status"], m["evidence_status"], m["critical"], m["total_slack_days"]]
                     for m in view["milestones"]]), ""]
    L += [f"Selection rule: {view['selection_rule']}", ""]
    pc = view.get("phase_coverage")
    if pc:
        L += [f"Phase coverage: the engine classifies {', '.join(pc['classified_categories'])}; "
              f"{pc['unclassified_task_count']} row(s) are unclassified. {pc['note']}", ""]
    if view.get("phase_boundaries"):
        L += ["## Phase boundaries (from phase_semantics)", "",
              _md_table(["phase", "start", "finish", "calendar days", "activities"],
                        [[b["category"], b["start"], b["finish"], b["calendar_days"],
                          b["task_count"]] for b in view["phase_boundaries"]]), ""]
    L += ["## Counts", ""]
    for k, v in sorted(view["counts"].items()):
        L.append(f"- {k}: {v}")
    near = view.get("nearest_pending_milestone")
    L += ["", "## Nearest pending milestone", ""]
    L.append(f"- {near['label']} ({near['finish']}) — owner recorded by engine: "
             f"{near.get('decision_owner') or 'not stated'}" if near else "- none")
    L += ["", "## Detail with sources", ""]
    for m in view["milestones"]:
        L.append(f"### {m['milestone_id']} — {m['label']}")
        L.append(f"- {m['start']} → {m['finish']} · {m['duration_days']}d · "
                 f"requirement `{m['requirement_status']}`")
        if m.get("conditional_note"):
            L.append(f"- {m['conditional_note']}")
        if m.get("dependency_note"):
            L.append(f"- {m['dependency_note']}")
        if m["upstream_milestones"]:
            L.append("- upstream milestones: " + ", ".join(
                f"{u['milestone_id']} {u['label']} ({u['relationship']})"
                for u in m["upstream_milestones"]))
        if m["planned_packages"]:
            L.append("- procurement packages feeding this milestone: " + ", ".join(
                f"{p['package_id']} [{p['status']}]" for p in m["planned_packages"]))
        L.append("- source: " + ", ".join(f"`{s}`" for s in m["source_fields"]))
        L.append("")
    if view["gaps"]:
        L += ["## Gaps", ""] + [f"- {g}" for g in view["gaps"]] + [""]
    return "\n".join(L)


def render_proposals(pack) -> str:
    view = pack["review_proposals"]
    L = ["# Engine Proposals — not applied", ""] + _provenance(pack, view)
    L += ["", f"- approval state: **{view['approval_state']}**",
          f"- proposals: {view['proposal_count']}",
          f"- recommended scenario: `{_null(view['recommended_scenario'])}`",
          f"- {view['recommendation_note']}", "",
          f"> {view['scenario_comparison_note']}", ""]
    L += [_md_table(["id", "category", "recommendation", "applied", "approval", "quantified"],
                    [[p["proposal_id"], p["category"], p["recommendation"], p["applied"],
                      p["approval_required_by"], p["quantified"]]
                     for p in view["proposals"]]), ""]
    L += [f"> {view['proposals'][0]['accountability_note']}", ""] if view["proposals"] else []
    if view["scenario_comparison"]:
        comp = view["scenario_comparison"]
        L += ["## Re-solved scenario comparison (clones; baseline untouched)", "",
              f"> {comp.get('labelling', '')}", "", f"{comp.get('rule', '')}", ""]
        rows = comp.get("rows") or []
        if rows:
            keys = ["scenario", "label", "physical_construction_working_days",
                    "calculated_difference_vs_a_pct"]
            L += [_md_table([k for k in keys],
                            [[r.get(k) for k in keys] for r in rows]), ""]
    L += ["", f"PM actions available: {', '.join(view['pm_actions_available'])}"]
    if view["gaps"]:
        L += ["", "## Gaps", ""] + [f"- {g}" for g in view["gaps"]]
    L.append("")
    return "\n".join(L)


def render_evidence(pack) -> str:
    view = pack["evidence_register"]
    L = ["# Evidence Register", ""] + _provenance(pack, view)
    L += ["", f"- records: {view['count']} · unsourced: {view['unsourced_count']}",
          f"> {view['note']}", ""]
    L += ["## By status", ""]
    for k, v in sorted(view["by_status"].items()):
        L.append(f"- {k}: {v}")
    L += ["", "## By origin", ""]
    for k, v in sorted(view["by_origin"].items()):
        L.append(f"- {k}: {v}")
    L += ["", "## Records", "",
          _md_table(["claim", "value", "status", "confidence", "origin", "source"],
                    [[r["claim"], json.dumps(r["value"], ensure_ascii=False)[:60],
                      r["status"], r["confidence"], r["origin"],
                      ", ".join(r["source_fields"])] for r in view["records"]]), ""]
    return "\n".join(L)


def render_index(pack) -> str:
    v = pack
    h = v["header"]
    pi = v["procurement_intelligence"]
    em = v["executive_milestones"]
    pc = v["executive_commentary"]
    rp = v["review_proposals"]
    er = v["evidence_register"]
    L = ["# EXECUTIVE_PACK_INDEX", "",
         "## Export header (PC-08 contract)", "",
         f"| field | value |", "|---|---|",
         f"| as-of | {h['as_of']} |",
         f"| engine version | {h['engine_version']} |",
         f"| run id | `{h['run_id']}` |",
         f"| input hash | `{h['input_hash']}` |",
         f"| generated at | `{h['generated_at']}` |",
         f"| completeness | **{v['common']['completeness']}** |",
         f"| missing inputs | {', '.join(v['common']['missing_inputs']) or '—'} |",
         f"| pack schema | `{v['common']['pack_schema']}` |",
         f"| read only | **{v['common']['read_only']}** |",
         f"| applied to baseline | **{v['common']['applied']}** |",
         f"| baseline unchanged | **{v['common']['baseline_unchanged']}** |",
         f"| recommended scenario | `{_null(v['common']['recommended_scenario'])}` |",
         "", "## Views", "",
         _md_table(["file", "read this for", "key reading"],
                   [["_procurement_intelligence.md", "采购包、长交期项、法定适用性",
                     f"{len(pi['packages'])} packages · {len(pi['lead_items'])} lead items · "
                     f"incomplete={pi['evidence_incomplete']}"],
                    ["_executive_milestones.md", "执行里程碑与证据强度",
                     f"{len(em['milestones'])} milestones · "
                     f"nearest pending = {(em.get('nearest_pending_milestone') or {}).get('label')}"],
                    ["_executive_commentary.md", "执行摘要（确定性渲染）",
                     f"{pc['word_count']} words · unsupported={len(pc['unsupported_sentences'])} · "
                     f"blocklist hits={len(pc['blocklist_violations'])}"],
                    ["_review_proposals.md", "引擎提案（未应用）",
                     f"{rp['proposal_count']} proposals · state={rp['approval_state']}"],
                    ["_evidence_register.md", "每条论断的出处与证据强度",
                     f"{er['count']} records · unsourced={er['unsourced_count']}"]]),
         "", "## Input fingerprints", "",
         _md_table(["input", "sha256 (16)"],
                   [[k, d[:16]] for k, d in sorted(h["input_sha256"].items())]),
         "", "## Evidence appendix", "",
         "Every factual sentence in the executive commentary carries `source_fields` pointing at "
         "the files listed above. A sentence without a source is marked `UNSUPPORTED` and is "
         "excluded from the executive version.", ""]
    gaps = []
    for name, view in (("procurement", pi), ("milestones", em), ("commentary", pc),
                       ("proposals", rp)):
        for g in view.get("gaps", []):
            gaps.append(f"- [{name}] {g}")
    L += ["## Gaps", ""]
    L += gaps or ["- none"]
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #

def write_view(out_dir: str, name: str, obj, markdown: str) -> None:
    with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, name + ".md"), "w", encoding="utf-8") as fh:
        fh.write(markdown)


def scenario_comparison_for(run_dir: str, root: str):
    """Optional closed-loop re-solve. Solves CLONES only; never touches the baseline."""
    from core import scenario_orchestrator as SO
    from core.fitout_engine import prepare_fitout  # noqa: F401 (import-validated upstream)
    import copy

    inp = os.path.join(run_dir, "input", "input.json")
    if not os.path.exists(inp):
        return None
    args = (EP.load_json(inp) or {}).get("args") or {}
    city, area = args.get("city"), args.get("area")
    start = args.get("start_date")
    if not (city and area and start):
        return None
    delivery, bidding = args.get("delivery"), args.get("bidding")
    if not (delivery and bidding):
        return None
    mode = args.get("mode") or {
        ("DB", "invite"): "MNC_Standard_Fitout_DB_Invite",
        ("DB", "public"): "MNC_Standard_Fitout_DB_Public",
        ("DBB", "invite"): "MNC_Standard_Fitout_DBB_Invite",
        ("DBB", "public"): "MNC_Standard_Fitout_Office_DBB",
    }.get((delivery, bidding))
    if not mode:
        return None
    templates = EP.load_json(os.path.join(root, "templates", "wbs_templates.json"))["templates"]
    if mode not in templates:
        return None
    from core.fitout_wbs import expand_fitout_wbs
    tasks = expand_fitout_wbs(copy.deepcopy(templates[mode]["tasks"]))
    floors = int(args.get("floors") or 1)
    zones = args.get("zones")
    return SO.run_all(tasks, float(area), start, floors=floors,
                      zones_per_floor=(int(zones) if zones else None))["comparison"]


def build_one(run_dir: str, root: str, out_dir: str, with_scenarios: bool) -> Dict[str, object]:
    comparison = None
    if with_scenarios:
        try:
            comparison = scenario_comparison_for(run_dir, root)
        except Exception as exc:  # pragma: no cover - defensive
            comparison = {"error": f"{type(exc).__name__}: {exc}", "rows": [],
                          "rule": "scenario comparison could not be produced; baseline untouched"}
    pack = EP.build_pack(run_dir, root, scenario_comparison=comparison)
    os.makedirs(out_dir, exist_ok=True)
    write_view(out_dir, "_procurement_intelligence", pack["procurement_intelligence"],
               render_procurement(pack))
    write_view(out_dir, "_executive_milestones", pack["executive_milestones"],
               render_milestones(pack))
    write_view(out_dir, "_review_proposals", pack["review_proposals"],
               render_proposals(pack))
    write_view(out_dir, "_evidence_register", pack["evidence_register"],
               render_evidence(pack))
    write_view(out_dir, "_executive_commentary", pack["executive_commentary"],
               EP.render_commentary_markdown(pack["executive_commentary"], pack["header"]))
    with open(os.path.join(out_dir, "EXECUTIVE_PACK_INDEX.md"), "w", encoding="utf-8") as fh:
        fh.write(render_index(pack))
    return pack


def main() -> None:
    ap = argparse.ArgumentParser(description="V6.6.1 executive pack (read-only)")
    ap.add_argument("--run", help="run directory, e.g. output_mpp/run_20260915_142632")
    ap.add_argument("--all", action="store_true", help="process every run under output_mpp")
    ap.add_argument("--out", help="output directory (default <run>/reports/executive_pack)")
    ap.add_argument("--with-scenarios", action="store_true",
                    help="also produce a re-solved scenario comparison (clones only)")
    args = ap.parse_args()

    base = os.path.join(ROOT, "output_mpp")
    if args.run:
        runs = [args.run if os.path.isabs(args.run) else os.path.join(ROOT, args.run)]
    elif args.all:
        runs = sorted(d for d in (os.path.join(base, x) for x in os.listdir(base))
                      if os.path.isdir(d) and os.path.isdir(os.path.join(d, "audit"))) \
            if os.path.isdir(base) else []
    else:
        if not os.path.isdir(base):
            print(f"no output_mpp directory under {ROOT}", file=sys.stderr)
            raise SystemExit(2)
        cands = [os.path.join(base, x) for x in os.listdir(base)
                 if x.startswith("run_") and os.path.isdir(os.path.join(base, x, "audit"))]
        runs = [max(cands, key=os.path.getmtime)] if cands else []

    if not runs:
        print("no completed run found", file=sys.stderr)
        raise SystemExit(2)

    for run_dir in runs:
        out_dir = args.out or os.path.join(run_dir, "reports", OUTDIR_NAME)
        try:
            pack = build_one(run_dir, ROOT, out_dir, args.with_scenarios)
        except FileNotFoundError as exc:
            print(f"[skip] {run_dir}: {exc}")
            continue
        pc = pack["executive_commentary"]
        pi = pack["procurement_intelligence"]
        em = pack["executive_milestones"]
        print(f"[ok] {run_dir}")
        print(f"     packages={len(pi['packages'])} lead_items={len(pi['lead_items'])} "
              f"attention={len(pi['attention_list'])}")
        print(f"     milestones={len(em['milestones'])} counts={em['counts']}")
        print(f"     commentary words={pc['word_count']} unsupported="
              f"{len(pc['unsupported_sentences'])} blocklist={len(pc['blocklist_violations'])}")
        print(f"     proposals={pack['review_proposals']['proposal_count']} applied="
              f"{pack['common']['applied']} baseline_unchanged={pack['common']['baseline_unchanged']}")
        print(f"     -> {out_dir}")


if __name__ == "__main__":
    main()
