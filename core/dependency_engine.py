# -*- coding: utf-8 -*-
"""
core/dependency_engine.py — Dependency Engine v0 (sectional SS+lag rules)

Default WBS templates link site trades Finish-to-Start through inspection gates:

    partition frame ─┐
                     ├─FS→ in-wall inspection ─FS→ ceiling grid / MEP 2nd fix ─FS→ ...
    MEP mains ───────┘

That is right for a single-workfront floor. On a large *sectional* project (several
zones / workfronts) trades flow zone by zone, so the successor trade starts once the
predecessor has cleared roughly one zone: a Start-to-Start link with a lag.

This module applies the rule table in config/dependency_rules.json:

  * activation  — sectional when area >= threshold OR workfronts >= min (mode "auto"),
                  or forced with mode "on" / "off". The decision and its reasons are
                  returned so the caller can log *why* rules fired (or not).
  * lag         — deterministic: clamp(ceil(pred_duration / workfronts), min, max).
  * gate        — the inspection gate the successor used to wait for (FS) is kept as
                  Finish-to-Finish so the trade cannot complete before its inspection
                  completes and the gate is never left dangling.
  * explain     — every rewritten task carries `predecessors_before_rules` and a
                  `dependency_rules` list (rule id, predecessor, lag basis, gate).

Non-sectional projects: no task is touched (template FS behaviour stands).
Rules only add links pointing to *earlier* tasks, so they cannot create cycles; the
resulting graph is still checked by validate_dependency_graph (Kahn's sort).
"""
import os
import json
import math
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from core._common import split_predecessor_id_suffix, parse_predecessor
except ImportError:  # pragma: no cover
    from _common import split_predecessor_id_suffix, parse_predecessor  # type: ignore

try:
    from core.dependency_ontology import (  # V6.0 typed ontology (authoritative)
        apply_ontology_dependencies, compute_pipeline_lag, load_ontology)
except ImportError:  # pragma: no cover
    from dependency_ontology import (  # type: ignore
        apply_ontology_dependencies, compute_pipeline_lag, load_ontology)

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(BASE_DIR, "config", "dependency_rules.json")

METHOD_SECTIONAL = "sectional_ss_lag"
MODES = ("auto", "on", "off")
_ALLOWED_RELATIONSHIPS = ("SS",)          # v0: only SS rules
_ALLOWED_GATE_RELATIONSHIPS = ("FF", "drop")


# ---------------------------------------------------------------------------
# rule table
# ---------------------------------------------------------------------------
def load_dependency_rules(path: Optional[str] = None) -> Dict[str, Any]:
    """Load and validate the rule table. Fails loudly on a malformed table."""
    p = path or RULES_PATH
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data.get("rules"), list):
        raise ValueError(f"dependency rules file {p} lacks a 'rules' list")
    act = data.get("activation") or {}
    for key in ("sectional_area_sqm_min", "workfront_area_sqm", "min_workfronts", "max_workfronts"):
        if key not in act or not (float(act[key]) > 0):
            raise ValueError(f"activation.{key} missing or not > 0 in {p}")
    if act.get("mode_default", "auto") not in MODES:
        raise ValueError(f"activation.mode_default must be one of {MODES}")
    gate = data.get("gate_policy") or {}
    if gate.get("relationship", "FF") not in _ALLOWED_GATE_RELATIONSHIPS:
        raise ValueError(f"gate_policy.relationship must be one of {_ALLOWED_GATE_RELATIONSHIPS}")

    seen = set()
    for rule in data.get('fitout_rules', []):
        if not rule.get('predecessor') or not rule.get('successor'):
            raise ValueError('Fitout rule requires predecessor and successor activity types')
        if rule.get('sectional_relationship') not in ('FS', 'SS', 'FF'):
            raise ValueError('Fitout relationship must be FS, SS or FF')
        for field in ('lag', 'max_lag'):
            value = rule.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f'Fitout {field} must be a nonnegative integer')
    for r in data["rules"]:
        rid = r.get("id")
        if not rid or rid in seen:
            raise ValueError(f"rule id missing or duplicated: {rid!r}")
        seen.add(rid)
        for key in ("predecessor_keywords", "successor_keywords"):
            if not r.get(key) or not isinstance(r[key], list):
                raise ValueError(f"rule {rid}: {key} must be a non-empty list")
        if r.get("relationship", "SS") not in _ALLOWED_RELATIONSHIPS:
            raise ValueError(f"rule {rid}: relationship must be one of {_ALLOWED_RELATIONSHIPS} (v0)")
        lag = r.get("lag_days") or {}
        lo, hi = int(lag.get("min", 0)), int(lag.get("max", 0))
        if lo < 0 or hi < lo:
            raise ValueError(f"rule {rid}: lag_days must satisfy 0 <= min <= max, got {lag}")
        if "min_workfronts" in r and int(r["min_workfronts"]) < 1:
            raise ValueError(f"rule {rid}: min_workfronts must be >= 1")
    return data


# ---------------------------------------------------------------------------
# activation
# ---------------------------------------------------------------------------
def derive_workfronts(area_sqm: float, rules: Dict[str, Any], workfronts: Optional[int] = None) -> Tuple[int, str]:
    """Number of zones/workfronts and a human-readable basis string.

    FIX-01/09/10: the workfront count now comes from the ONE canonical model in
    core.workfront_capacity, the same source the productivity and spatial engines consume.
    It previously divided area by its own ``workfront_area_sqm`` (2500) with floor(), which
    (a) disagreed with the productivity engine's 500 sqm/front and (b) produced a hard
    topology cliff (3000 → 1 front, 6000 → 2 fronts).
    """
    from core.workfront_capacity import resolve_workfronts
    act = rules["activation"]
    cap = int(act["max_workfronts"])
    if workfronts is not None:
        wf = max(1, min(cap, int(workfronts)))
        basis = f"explicit --workfronts {int(workfronts)}" + (f" capped at {cap}" if int(workfronts) > cap else "")
        return wf, basis
    canonical = resolve_workfronts(float(area_sqm or 0))
    # V6.4.4.1 P1-01: sectional handover uses the DISCRETE sectional_workfront_count (whole
    # practical zones), never the fractional effective capacity. This is what stops the
    # sectional gate from flipping when the fractional capacity crosses an integer boundary.
    wf = max(1, min(cap, int(canonical["sectional_workfront_count"])))
    return wf, (f"sectional_workfront_count = {wf} (discrete whole zones; "
                f"effective_workfront_capacity = {canonical['effective_workfront_capacity']:.4f} is used "
                f"for physical capacity only) [{canonical['source_type']}]")


def evaluate_sectional(
    area_sqm: float,
    rules: Optional[Dict[str, Any]] = None,
    mode: str = "auto",
    workfronts: Optional[int] = None,
) -> Dict[str, Any]:
    """Decide whether the project is sectional. Returns the decision with reasons."""
    rules = rules or load_dependency_rules()
    if mode not in MODES:
        raise ValueError(f"sectional mode must be one of {MODES}, got {mode!r}")
    act = rules["activation"]
    wf, wf_basis = derive_workfronts(area_sqm, rules, workfronts)
    area_min = float(act["sectional_area_sqm_min"])
    min_wf = int(act["min_workfronts"])
    reasons: List[str] = []

    if mode == "off":
        sectional = False
        reasons.append("forced off (--sectional off): template FS logic kept")
    elif mode == "on":
        sectional = True
        reasons.append("forced on (--sectional on)")
    else:
        by_area = float(area_sqm or 0) >= area_min
        by_wf = wf >= min_wf
        sectional = by_area or by_wf
        reasons.append(f"area {float(area_sqm or 0):g}㎡ {'>=' if by_area else '<'} sectional_area_sqm_min {area_min:g}㎡ → {'sectional' if by_area else 'not sectional'} by area")
        reasons.append(f"workfronts {wf} {'>=' if by_wf else '<'} min_workfronts {min_wf} → {'sectional' if by_wf else 'not sectional'} by workfronts")
    return {
        "sectional": sectional,
        "mode": mode,
        "area_sqm": area_sqm,
        "workfronts": wf,
        "workfronts_basis": wf_basis,
        "reasons": reasons,
        "rules_version": rules.get("version", "unknown"),
    }


def compute_lag(pred_duration_days: int, workfronts: int, lag_min: int, lag_max: int) -> Tuple[int, str]:
    """Deterministic lag: clamp(ceil(pred_duration / workfronts), min, max).

    Delegates to the V6.0 ontology so the sectional pipeline formula has exactly
    one implementation (this signature is kept for existing callers/tests).
    """
    return compute_pipeline_lag(pred_duration_days, workfronts, lag_min, lag_max)


# ---------------------------------------------------------------------------
# matching helpers
# ---------------------------------------------------------------------------
def _is_leaf_trade(t: Dict[str, Any], scope: Dict[str, Any]) -> bool:
    if t.get("milestone"):
        return False
    lvl = int(t.get("outline_level", t.get("level", 3)) or 3)
    if lvl < int(scope.get("min_outline_level", 3)):
        return False
    if scope.get("require_positive_duration", True):
        if not (float(t.get("duration_days", t.get("duration", 0)) or 0) > 0):
            return False
    return True


def _name_matches(t: Dict[str, Any], keywords: Sequence[str]) -> bool:
    name = str(t.get("name", ""))
    return any(k in name for k in keywords)


def _nearest_preceding(tasks: List[Dict[str, Any]], idx: int, keywords: Sequence[str], scope: Dict[str, Any]) -> Optional[int]:
    for j in range(idx - 1, -1, -1):
        if _is_leaf_trade(tasks[j], scope) and _name_matches(tasks[j], keywords):
            return j
    return None


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------
def apply_dependency_rules(
    tasks: List[Dict[str, Any]],
    area_sqm: float,
    mode: str = "auto",
    workfronts: Optional[int] = None,
    rules: Optional[Dict[str, Any]] = None,
    log: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Rewrite successor predecessor strings per the sectional rule table (in place).

    Returns a report: {"decision", "applied": [rows], "skipped": [rows]}. When the project
    is not sectional, tasks are returned untouched and "applied" is empty.
    """
    log = log or logger
    rules = rules or load_dependency_rules()
    decision = evaluate_sectional(area_sqm, rules, mode=mode, workfronts=workfronts)
    report: Dict[str, Any] = {"decision": decision, "applied": [], "skipped": []}

    log.info(f"  -> [dependency] sectional={decision['sectional']} (mode={mode}); workfronts={decision['workfronts']} [{decision['workfronts_basis']}]")
    for r in decision["reasons"]:
        log.info(f"  -> [dependency]   · {r}")
    if not decision["sectional"]:
        log.info("  -> [dependency] not sectional → template FS logic kept (0 rules applied)")
        return report

    scope = rules.get("scope") or {}
    gate_pol = rules.get("gate_policy") or {}
    gate_rel = gate_pol.get("relationship", "FF")
    gate_lag = int(gate_pol.get("lag_days", 0) or 0)
    wf = decision["workfronts"]
    by_id = {int(t["id"]): t for t in tasks}

    for rule in rules["rules"]:
        rid = rule["id"]
        rule_min_wf = int(rule.get("min_workfronts", 0) or 0)
        if rule_min_wf and wf < rule_min_wf:
            report["skipped"].append({"rule_id": rid, "reason": f"workfronts {wf} < rule min_workfronts {rule_min_wf}"})
            continue
        succ_idx = [i for i, t in enumerate(tasks) if _is_leaf_trade(t, scope) and _name_matches(t, rule["successor_keywords"])]
        if not succ_idx:
            report["skipped"].append({"rule_id": rid, "reason": f"no successor task matches {rule['successor_keywords']}"})
            continue
        for si in succ_idx:
            S = tasks[si]
            pi = _nearest_preceding(tasks, si, rule["predecessor_keywords"], scope)
            if pi is None:
                report["skipped"].append({"rule_id": rid, "successor_id": S["id"],
                                          "reason": f"no predecessor task matching {rule['predecessor_keywords']} precedes '{S.get('name')}'"})
                continue
            P = tasks[pi]
            pid = int(P["id"])
            p_dur = int(round(float(P.get("duration_days", P.get("duration", 0)) or 0)))
            lag_spec = rule.get("lag_days") or {}
            lag, lag_basis = compute_lag(p_dur, wf, int(lag_spec.get("min", 0)), int(lag_spec.get("max", 0)))

            original = str(S.get("predecessors", "") or "")
            if "predecessors_before_rules" not in S:
                S["predecessors_before_rules"] = original
            new_tokens: List[str] = []
            gates: List[Dict[str, Any]] = []
            for tok_id, suffix in split_predecessor_id_suffix(original):
                if tok_id == pid:
                    continue  # replaced by the SS link below
                gate_task = by_id.get(tok_id)
                suffix_u = (suffix or "").upper().replace(" ", "")
                is_plain_fs = suffix_u in ("", "FS")
                if gate_task is not None and is_plain_fs and _name_matches(gate_task, rule.get("gate_keywords") or []):
                    if gate_rel == "drop":
                        gates.append({"gate_id": tok_id, "gate_name": gate_task.get("name"), "gate_relationship": "dropped"})
                        continue
                    tok = f"{tok_id}FF" + (f"+{gate_lag}" if gate_lag else "")
                    gates.append({"gate_id": tok_id, "gate_name": gate_task.get("name"), "gate_relationship": tok[len(str(tok_id)):]})
                    new_tokens.append(tok)
                else:
                    new_tokens.append(f"{tok_id}{suffix}")
            ss_tok = f"{pid}{rule.get('relationship', 'SS')}+{lag}"
            new_tokens.append(ss_tok)
            deduped: List[str] = []
            for tok in new_tokens:
                if tok not in deduped:
                    deduped.append(tok)
            S["predecessors"] = ",".join(deduped)
            S["dependency_method"] = METHOD_SECTIONAL
            entry = {
                "rule_id": rid,
                "label": rule.get("label_zh") or rule.get("label_en") or rid,
                "successor_id": S["id"],
                "successor_name": S.get("name"),
                "predecessor_id": pid,
                "predecessor_name": P.get("name"),
                "predecessor_duration_days": p_dur,
                "relationship": rule.get("relationship", "SS"),
                "lag_days": lag,
                "lag_basis": lag_basis,
                "workfronts": wf,
                "gates": gates,
                "predecessors_before": original,
                "predecessors_after": S["predecessors"],
            }
            S.setdefault("dependency_rules", []).append(entry)
            report["applied"].append(entry)
            log.info(
                f"  -> [dependency][{rid}] #{S['id']} '{S.get('name')}': predecessors '{original}' → '{S['predecessors']}' "
                f"({ss_tok} on '{P.get('name')}', lag {lag_basis}"
                + (f"; gate {', '.join(str(g['gate_id']) + g['gate_relationship'] for g in gates)}" if gates else "")
                + ")"
            )
    log.info(f"  -> [dependency] {len(report['applied'])} rule application(s), {len(report['skipped'])} skipped; rules v{rules.get('version')}")
    return report


def dependency_report(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Explainable rows for every task re-linked by the engine (for logs / sidecar JSON)."""
    rows: List[Dict[str, Any]] = []
    for t in tasks:
        if t.get("dependency_method") == METHOD_SECTIONAL and t.get("dependency_rules"):
            rows.append({
                "id": t.get("id"),
                "name": t.get("name"),
                "start": t.get("start"),
                "finish": t.get("finish"),
                "critical": t.get("critical"),
                "total_slack_days": t.get("total_slack_days"),
                "predecessors_before_rules": t.get("predecessors_before_rules"),
                "predecessors": t.get("predecessors"),
                "rules": t.get("dependency_rules"),
            })
    return rows


def apply_fitout_dependencies(tasks, decision, mode="typed", onto=None):
    """Typed dependencies resolved by the V6.0 activity-type ontology.

    Production mode (``mode="typed"``) resolves every link from
    ``config/dependency_ontology.json``: condition → class → relationship → lag,
    each carrying reason, why-not-FS / why-not-SS and confidence.

    ``mode="auto"`` additionally falls back to the legacy keyword table
    (config/dependency_rules.json#rules) for pairs the ontology does not model;
    those rows are marked source="legacy.keyword" and confidence "low".

    The flat ``fitout_rules`` table remains a compatibility source only; a
    regression test asserts the ontology still covers it.

    Inspection gates stay explicit ``FS`` releases and are never dropped.
    """
    report = apply_ontology_dependencies(tasks, decision, onto=onto, mode=mode)
    issues = validate_dependency_graph(tasks)
    if issues:
        raise ValueError(str(issues))
    return dict(decision=report['decision'], applied=report['applied'],
                skipped=report['skipped'], explanations=report['explanations'],
                unbound_conditions=report['unbound_conditions'],
                protection=report['protection'])


# ---------------------------------------------------------------------------
# graph validation (Kahn's topological sort)
# ---------------------------------------------------------------------------
def validate_dependency_graph(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check the predecessor network the solver will consume.

    Codes: PRED_UNKNOWN, PRED_SELF, PRED_DUPLICATE, PRED_CYCLE.
    Display order may differ from execution order: solve_schedule now sorts
    dependencies internally, preserving the original WBS for presentation.
    """
    issues: List[Dict[str, Any]] = []
    order = {int(t["id"]): i for i, t in enumerate(tasks)}
    if len(order) != len(tasks):
        issues.append({'level':'error','code':'PRED_DUPLICATE','task_id':None,
                       'message':'task identifiers must be unique'})
    edges: Dict[int, List[int]] = {int(t["id"]): [] for t in tasks}
    indeg: Dict[int, int] = {int(t["id"]): 0 for t in tasks}
    for t in tasks:
        sid = int(t["id"])
        for pid, _typ, _lag in parse_predecessor(str(t.get("predecessors", "") or "")):
            if pid == sid:
                issues.append({"level": "error", "code": "PRED_SELF", "task_id": sid,
                               "message": f"task {sid} '{t.get('name')}' lists itself as predecessor"})
                continue
            if pid not in order:
                issues.append({"level": "error", "code": "PRED_UNKNOWN", "task_id": sid,
                               "message": f"task {sid} '{t.get('name')}' references unknown predecessor {pid}"})
                continue
            edges[pid].append(sid)
            indeg[sid] += 1
    queue = sorted(i for i, d in indeg.items() if d == 0)
    visited = 0
    while queue:
        n = queue.pop(0)
        visited += 1
        for m in edges[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if visited != len(tasks):
        stuck = sorted(i for i, d in indeg.items() if d > 0)
        issues.append({"level": "error", "code": "PRED_CYCLE", "task_id": stuck[0] if stuck else None,
                       "message": f"dependency cycle detected among tasks {stuck}"})
    return issues
