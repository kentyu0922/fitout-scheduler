"""Recommendation gate — the single place where "should we recommend something?" is decided.

Added in V6.4 to fix a real defect in V6.3: ``scenario_optimizer.scenario_recommendation``
returned ``recommended=<fastest scenario>`` as soon as ``resource_readiness`` became
``confirmed``. That is GOV-14/GOV-15 violation by construction — "shortest duration" is a
single metric being dressed up as a decision, and cost / crew availability / contract
boundaries were never in the engine's scope.

The gate is deliberately boring:

    recommended is None. Always. In every code path. In every evidence state.

`confirmed` does not produce a recommendation. It only moves ``decision_state`` from
NO_RECOMMENDATION to PM_DECISION_REQUIRED, and hands over a *parallel* candidate set that
is never ranked.

Three consumers share this gate: scenarios (V6.3), target date, and fast-track.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

from core import resource_governance as RG

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "production_candidate.json")

NO_RECOMMENDATION = "NO_RECOMMENDATION"
PM_DECISION_REQUIRED = "PM_DECISION_REQUIRED"

OPTION = "OPTION"

_CACHE: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def load_production_candidate_config(path: Optional[str] = None,
                                     reload: bool = False) -> Dict[str, Any]:
    key = path or CONFIG_PATH
    if not reload and key in _CACHE:
        return _CACHE[key]
    with open(key, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    _CACHE[key] = data
    return data


def validate_production_candidate_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Structural validation. Raises ValueError on anything that would weaken the gate."""
    problems: List[str] = []
    if not cfg.get("version"):
        problems.append("missing version")
    gate = cfg.get("recommendation_gate") or {}
    if not gate:
        problems.append("missing recommendation_gate")
    else:
        never = gate.get("never_emits") or {}
        if "recommended" not in never:
            problems.append("recommendation_gate.never_emits.recommended must be present")
        elif never.get("recommended") is not None:
            problems.append("recommendation_gate.never_emits.recommended must be null")
    rules = ((cfg.get("governance") or {}).get("rules") or [])
    ids = {r.get("id") for r in rules}
    for required in ("GOV-15", "GOV-16", "GOV-17", "GOV-18", "GOV-19"):
        if required not in ids:
            problems.append(f"missing governance rule {required}")
    seg = (cfg.get("programme_segments") or {})
    if not seg.get("phase_rules"):
        problems.append("missing programme_segments.phase_rules")
    if (cfg.get("fast_track") or {}).get("forbidden_classes") is None:
        problems.append("missing fast_track.forbidden_classes")
    if problems:
        raise ValueError("production_candidate config invalid: " + "; ".join(problems))
    return dict(ok=True, rules=sorted(i for i in ids if i),
                out_of_scope=((cfg.get("provenance") or {}).get("explicitly_out_of_scope") or []))


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
def evaluate(candidates: Sequence[Dict[str, Any]],
             cfg: Optional[Dict[str, Any]] = None,
             resource_cfg: Optional[Dict[str, Any]] = None,
             required_confirmations: Optional[Sequence[Dict[str, Any]]] = None,
             context: Optional[Dict[str, Any]] = None,
             candidate_note: Optional[str] = None) -> Dict[str, Any]:
    """Return the gate decision. ``recommended`` is always None — see module docstring.

    candidates       parallel candidate rows (never ranked here)
    required_confirmations  what must be confirmed before the state can move
    context          free-form label, e.g. "scenario" / "target_date" / "fast_track"
    """
    pcfg = cfg or load_production_candidate_config()
    validate_production_candidate_config(pcfg)

    readiness = RG.resource_readiness(resource_cfg)
    confirmed = readiness.get("overall") == RG.READINESS_CONFIRMED
    decision_state = PM_DECISION_REQUIRED if confirmed else NO_RECOMMENDATION

    candidate_ids = [c.get("id") for c in candidates if c.get("id") is not None]
    blockers: List[Dict[str, str]] = []

    if not confirmed:
        blockers.append(dict(
            code="RESOURCE_NOT_CONFIRMED",
            rule="GOV-15",
            message=("资源输入未确认（班组池 / 班次折算 / 工作面容量 / 拥挤曲线）。"
                     "在此证据强度下选择任何一个候选都等于把假设当决策。"),
        ))
    else:
        blockers.append(dict(
            code="SCOPE_BEYOND_ENGINE",
            rule="GOV-15",
            message=("资源输入已确认，但工期最短不等于最优：成本、班组可获得性、合同边界不在引擎判定范围内。"
                     "引擎只交付并列候选集，由 PM 决策。"),
        ))
    if not candidate_ids:
        blockers.append(dict(
            code="NO_CANDIDATE",
            rule="GOV-15",
            message="没有可用候选（全部不可用或数据不足），因此既无推荐也无候选集。",
        ))

    state_meta = ((pcfg.get("recommendation_gate") or {}).get("decision_states") or {}).get(
        decision_state) or {}

    return dict(
        recommended=None,
        label=decision_state,
        decision_state=decision_state,
        decision_state_zh=state_meta.get("label_zh"),
        decision_state_meaning=state_meta.get("meaning"),
        is_terminal_state=True,
        is_missing_data=False,
        context=context,
        candidate_ids=candidate_ids,
        candidate_count=len(candidate_ids),
        candidate_note=candidate_note or (
            "候选集为并列关系，引擎不排序、不打分、不挑选。"),
        blockers=blockers,
        readiness=dict(overall=readiness.get("overall"),
                       unconfirmed_sections=readiness.get("unconfirmed_sections"),
                       statement=readiness.get("scenario_statement")),
        required_confirmations=list(required_confirmations or []),
        statement=readiness.get("scenario_statement"),
        engine_role=((pcfg.get("recommendation_gate") or {}).get("never_emits") or {}).get(
            "rule"),
        must_not_be_read_as=((pcfg.get("recommendation_gate") or {}).get("never_emits") or {}).get(
            "must_not_be_read_as"),
        next_step=("按 required_confirmations 补齐承包商确认；确认后 decision_state 升为 "
                   "PM_DECISION_REQUIRED，recommended 仍为 null。"),
    )


def assert_no_recommendation(payload: Dict[str, Any]) -> None:
    """Hard assertion for downstream code and tests. Raises if anything leaked a pick."""
    if "recommended" not in payload:
        raise AssertionError("gate payload missing 'recommended' key")
    if payload["recommended"] is not None:
        raise AssertionError(
            f"GOV-15 violation: recommended must stay null, got {payload['recommended']!r}")
    if payload.get("decision_state") not in (NO_RECOMMENDATION, PM_DECISION_REQUIRED):
        raise AssertionError(f"unknown decision_state {payload.get('decision_state')!r}")


def one_line(payload: Dict[str, Any]) -> str:
    """Short human-readable verdict for reports and logs."""
    zh = payload.get("decision_state_zh") or payload.get("decision_state")
    return f"recommended=null（{zh}），候选 {payload.get('candidate_count', 0)} 个并列"
