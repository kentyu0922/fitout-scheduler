# -*- coding: utf-8 -*-
"""Fast-track SCENARIO — V6.6.1, GOV-18 compliant opt-in switch.

This module lands a *fast-track scenario switch* in the engine. It is NOT a baseline
edit and NEVER mutates the committed schedule. Given the prepared task graph it:

  1. deep-clones the graph (baseline untouched);
  2. applies three GOV-18-legal transform families, each individually gated by the
     same dependency ontology that ``core.fasttrack`` uses for its diagnostic:
       * overlap_edges    — legal FS -> SS+lag overlaps (reuses edge_inventory MOVABLE);
       * long_lead_advance — long-lead procurement ORDERING overlaps design release
                             (information dependency only, not the install gate);
       * parallel_tender  — D&B tender issued at SD instead of DD (information
                             dependency only);
  3. re-solves the cloned network ONCE and reports the CALCULATED finish + delta.

Hard refusals (identical to GOV-18): statutory-wait phases, inspection gates, and
government-approval-named tasks are NEVER overlapped. ``needs_confirmation`` edges
(lag not declared in the ontology) are counted but NOT applied — they require a PM /
contractor confirmation first.

Every result is labelled CALCULATED / POTENTIAL. ``applied_to_baseline`` is always
False and ``recommended`` is always False: this is a what-if, never an auto-applied
improvement.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional, Sequence

from core._common import parse_predecessor
from core import dependency_ontology as DO
from core import recommendation_gate as GATE
from core.recommendation_gate import load_production_candidate_config
from core.solver_engine import GOV_APPROVAL_KEYWORDS, solve_schedule
from core.fasttrack import edge_inventory, _split_predecessors, MOVABLE, NEEDS_CONFIRMATION, BLOCKED


LONG_LEAD_HINTS = ("家具", "FF&E", "软装", "AV", "音视频", "洁净", "工艺设备",
                   "设备订货", "订货", "采购", "procurement", "long lead")
TENDER_HINTS = ("招标", "tender", "评标", "发包", "发包")


def _split_pred_tokens(task: Dict[str, Any]) -> List[str]:
    raw = str(task.get("predecessors", task.get("predecessors_template", "")) or "")
    if not raw.strip():
        return []
    return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]


def _categorize(successor_name: str) -> str:
    nm = str(successor_name or "")
    if any(h.lower() in nm.lower() for h in TENDER_HINTS):
        return "parallel_tender"
    if any(h.lower() in nm.lower() for h in LONG_LEAD_HINTS):
        return "long_lead_advance"
    return "overlap_edges"


def _apply_movable_edges(tasks: Sequence[Dict[str, Any]],
                         rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert every MOVABLE FS edge to SS+lag on the (already cloned) task list.

    Returns a list of applied-edge facts for the audit trail. NEEDS_CONFIRMATION and
    BLOCKED rows are never touched here.
    """
    by_id = {t.get("id"): t for t in tasks}
    applied: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("verdict") != MOVABLE:
            continue
        succ = by_id.get(r["successor_id"])
        pred = by_id.get(r["predecessor_id"])
        if succ is None or pred is None:
            continue
        pred_dur = int(pred.get("duration_days", pred.get("duration", 0)) or 0)
        workfronts = max(1, int(r.get("workfronts") or 1))
        lag, basis = DO.compute_pipeline_lag(pred_dur, workfronts,
                                             int(r.get("lag_min") or 0),
                                             int(r.get("lag_max") or 0))
        toks = _split_pred_tokens(succ)
        idx = int(r.get("token_index") or 0)
        if idx >= len(toks):
            continue
        toks[idx] = f"{r['predecessor_id']}SS+{lag}"
        succ["predecessors"] = ",".join(toks)
        applied.append(dict(
            edge_id=r["edge_id"], predecessor_name=r["predecessor_name"],
            successor_name=r["successor_name"], lag_days=lag, lag_basis=basis,
            dependency_class=r.get("dependency_class"),
            technique=_categorize(r["successor_name"]),
        ))
    return applied


def run_fasttrack_scenario(tasks: Sequence[Dict[str, Any]], start_date: str,
                           cal_mode: str, holidays_pairs,
                           baseline_finish: Optional[str] = None,
                           cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build and solve the fast-track scenario on a CLONE. Baseline is never mutated.

    Parameters
    ----------
    tasks : prepared task graph (pre- or post-solve; only ``predecessors`` matter).
    start_date : kick-off date used for the forward re-solve.
    cal_mode : '5d' | '6d' | '7d' construction calendar mode.
    holidays_pairs : solver holiday exception list.
    baseline_finish : the already-solved committed finish (for the delta). Optional.
    """
    pcfg = cfg or load_production_candidate_config()
    clone = copy.deepcopy(list(tasks))

    # 1) inventory every FS edge under the GOV-18 ontology (no mutation).
    try:
        rows = edge_inventory(clone, cfg=pcfg)
    except Exception as exc:  # pragma: no cover - defensive
        return dict(status="EDGE_INVENTORY_FAILED", applied_to_baseline=False,
                    recommended=False, error=str(exc),
                    labelling="快轨情景未能构建（不影响基线）。")

    movable = [r for r in rows if r["verdict"] == MOVABLE]
    needs_confirmation = [r for r in rows if r["verdict"] == NEEDS_CONFIRMATION]
    blocked = [r for r in rows if r["verdict"] == BLOCKED]

    # 2) apply ONLY the fully-legal (MOVABLE) overlaps; clone already taken.
    applied = _apply_movable_edges(clone, rows)

    by_technique: Dict[str, int] = {}
    for a in applied:
        by_technique[a["technique"]] = by_technique.get(a["technique"], 0) + 1

    # 3) re-solve ONCE (combined effect, not summed per-edge).
    try:
        solved = solve_schedule(clone, start_date, custom_holidays=holidays_pairs,
                                construction_calendar_mode=cal_mode)
    except Exception as exc:  # pragma: no cover - defensive
        return dict(status="RE_SOLVE_FAILED", applied_to_baseline=False,
                    recommended=False, error=str(exc),
                    labelling="快轨情景重解失败（不影响基线）。")

    ft_finish = max((t.get("finish") for t in solved.get("tasks", []) if t.get("finish")),
                    default=solved.get("finish_date"))

    def _delta(a: Optional[str], b: Optional[str]) -> Optional[int]:
        from datetime import date
        if not a or not b:
            return None
        try:
            return (date.fromisoformat(b) - date.fromisoformat(a)).days
        except Exception:
            return None

    saving = _delta(ft_finish, baseline_finish)

    return dict(
        status="OK",
        scenario="fast_track",
        label="快轨情景（GOV-18 合法搭接 / 长周期提前 / 并行招标）",
        applied_to_baseline=False,
        recommended=False,
        labelling=("CALCULATED POTENTIAL PROGRAMME — not an achieved saving; "
                   "statutory holds (空治养护 / 图审 / 施工许可) are never compressed; "
                   "NEEDS_CONFIRMATION edges require PM/contractor sign-off before use."),
        rule="Scenario duration is an OUTPUT. No duration is tuned to reach a preferred answer.",
        baseline_finish=baseline_finish,
        fasttrack_finish=ft_finish,
        saving_days=saving,
        edges_total=len(rows),
        blocked=len(blocked),
        needs_confirmation=len(needs_confirmation),
        applied_edges=len(applied),
        applied_by_technique=by_technique,
        applied_details=applied,
        needs_confirmation_details=[
            dict(edge_id=r["edge_id"], successor_name=r["successor_name"],
                 reason=r.get("reason"))
            for r in needs_confirmation[:40]
        ],
        note=("Single-workfront (scope=project, not zone) graphs yield 0 legal overlaps under "
              "GOV-18; the 6–7 month target for such projects requires multi-zone sectional "
              "flow (engine --sectional / --zoning_mode / --workfronts), which is a separate, "
              "explicit lever — not a compression of statutory time."),
    )


def write_fasttrack_audit(payload: Dict[str, Any], path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
