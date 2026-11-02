"""P0-04 — Authority Decision Model.

Single-bool permit exemption conflated several independent legal matters.
This module splits them and never fakes certainty:

    construction_permit_required   — decidable from config city rules
    fire_design_review_required    — UNKNOWN without verified per-city fire rules
    fire_acceptance_required       — UNKNOWN (acceptance vs filing depends on
                                     building classification)
    fire_filing_required           — UNKNOWN
    landlord_approval_required     — TRUE (contractual, all templates)
    third_party_testing_required   — UNKNOWN (tasks retained conservatively)
    joint_completion_required     — UNKNOWN (city window exists; project
                                     applicability unconfirmed)

Key rule: a construction-permit exemption removes ONLY the permit. Fire,
landlord and completion obligations are different legal matters; when their
applicability has no config evidence the status is UNKNOWN / NEEDS_CONFIRMATION,
never silently TRUE.

Every decision carries status / reason / source / confidence. Conflicting
evidence forces UNKNOWN / NEEDS_CONFIRMATION.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "TRUE", "FALSE", "UNKNOWN",
    "decide_authority_requirements",
    "authority_decision_trace",
    "authority_wbs_consistency",
]

TRUE = "TRUE"
FALSE = "FALSE"
UNKNOWN = "UNKNOWN"

NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"

_CONSTRUCTION_PERMIT = "construction_permit_required"
_FIRE_KEYS = ("fire_design_review_required", "fire_acceptance_required",
              "fire_filing_required", "third_party_testing_required",
              "joint_completion_required")

_PERMIT_TASK_KEYWORDS = ("施工许可证办理", "政府施工许可证申报",
                         "质安监报监及政府施工许可证", "正式取得施工许可证")


def _decision(status: str, reason: str, source: str, confidence: str) -> Dict[str, Any]:
    """FIX-03 F — every requirement carries the full decision trace.

    committed_to_baseline / potential_schedule_impact / decision_required make
    the three-state rule machine-checkable:
        TRUE    -> committed_to_baseline True, decision_required False
        FALSE   -> committed_to_baseline False (folded), impact None
        UNKNOWN -> committed_to_baseline False, impact YES, decision_required True
    """
    return {
        "status": status,
        "reason": reason,
        "source": source,
        "confidence": confidence,
        "legal_status": status,
        "included_in_programme": status == TRUE,
        "programme_basis": "rule_evaluation" if status != UNKNOWN else "unresolved",
        "pm_confirmation": "not_recorded",
        "committed_to_baseline": status == TRUE,
        "potential_schedule_impact": (status == UNKNOWN),
        "decision_required": (status == UNKNOWN),
    }


def _decide_construction_permit(permit_info: Dict[str, Any],
                                 area_sqm: Optional[float],
                                 cost_10k_rmb: Optional[float]) -> Dict[str, Any]:
    rule_type = permit_info.get("rule_type")
    area_thr = permit_info.get("area_threshold")
    cost_thr = permit_info.get("cost_threshold_10k")
    policy_doc = permit_info.get("policy_doc") or "config/city_permit.json"
    source = f"config/city_permit.json ({policy_doc})"
    is_fallback = "兜底" in str(policy_doc) or permit_info.get("province") == "全国通用(兜底)"
    confidence = "low" if is_fallback else "medium"

    if rule_type == "OR_EXEMPT":
        if area_sqm is not None and area_thr is not None and area_sqm < area_thr:
            return _decision(FALSE,
                             f"area {area_sqm} < {area_thr} under OR_EXEMPT — exempt on area alone, cost-independent",
                             source, confidence)
        if cost_10k_rmb is None:
            return _decision(UNKNOWN,
                             f"area does not exempt under OR_EXEMPT (threshold {area_thr}) and cost is UNKNOWN; "
                             "the OR rule cannot conclude without the cost",
                             source, confidence)
        if cost_thr is not None and cost_10k_rmb < cost_thr:
            return _decision(FALSE,
                             f"cost {cost_10k_rmb}万 < {cost_thr}万 under OR_EXEMPT",
                             source, confidence)
        return _decision(TRUE,
                         f"area {area_sqm} >= {area_thr} and cost {cost_10k_rmb}万 >= {cost_thr}万 under OR_EXEMPT",
                         source, confidence)
    # AND-style exemption: below BOTH thresholds to be exempt
    if area_sqm is not None and area_thr is not None and area_sqm >= area_thr:
        return _decision(TRUE,
                         f"area {area_sqm} >= {area_thr} — exemption impossible under AND rule",
                         source, confidence)
    if cost_10k_rmb is None:
        return _decision(UNKNOWN,
                         "cost UNKNOWN; AND exemption cannot be concluded",
                         source, confidence)
    if cost_thr is not None and area_thr is not None and area_sqm is not None \
            and area_sqm < area_thr and cost_10k_rmb < cost_thr:
        return _decision(FALSE,
                         f"area {area_sqm} < {area_thr} and cost {cost_10k_rmb}万 < {cost_thr}万",
                         source, confidence)
    return _decision(TRUE,
                     f"area {area_sqm} / cost {cost_10k_rmb}万 fail AND exemption",
                     source, confidence)


def decide_authority_requirements(permit_info: Dict[str, Any],
                                   area_sqm: Optional[float] = None,
                                   cost_10k_rmb: Optional[float] = None,
                                   statutory_baseline: bool = False) -> Dict[str, Any]:
    """Split the single permit bool into independent authority decisions."""
    requirements: Dict[str, Dict[str, Any]] = {}

    requirements[_CONSTRUCTION_PERMIT] = _decide_construction_permit(
        permit_info, area_sqm, cost_10k_rmb)

    if statutory_baseline:
        policy_source = "programme_policy:authority_profile=STANDARD (not a verified per-city legal determination)"
        policy_reason = (
            "Included by STANDARD programme policy so the fire/completion chain stays on "
            "the L2 programme. This is not evidence that the matter is legally mandatory "
            "for this city and building; legal applicability is unverified."
        )
        requirements["fire_design_review_required"] = _decision(
            UNKNOWN, policy_reason, policy_source, "low")
        requirements["fire_acceptance_required"] = _decision(
            UNKNOWN, policy_reason, policy_source, "low")
        requirements["fire_filing_required"] = _decision(
            UNKNOWN, policy_reason, policy_source, "low")
        requirements["third_party_testing_required"] = _decision(
            UNKNOWN, policy_reason, policy_source, "low")
        requirements["joint_completion_required"] = _decision(
            UNKNOWN,
            policy_reason + f" Joint-completion window in config is "
            f"{permit_info.get('joint_completion_days_min', 15)} working days.",
            policy_source, "low")
        for key in ("fire_design_review_required", "fire_acceptance_required",
                    "fire_filing_required", "third_party_testing_required",
                    "joint_completion_required"):
            requirements[key]["legal_applicability"] = "UNVERIFIED_PROGRAMME_POLICY"
            requirements[key]["included_in_programme"] = True
            requirements[key]["programme_basis"] = "conservative_STANDARD_allowance"
        if requirements[_CONSTRUCTION_PERMIT]["status"] == UNKNOWN:
            # Preserve the STANDARD planning window, not a fabricated legal ruling.
            requirements[_CONSTRUCTION_PERMIT]["included_in_programme"] = True
            requirements[_CONSTRUCTION_PERMIT]["programme_basis"] = "conservative_STANDARD_allowance"
    else:
        no_fire_evidence = ("No verified per-city fire rule in config/city_permit.json; "
                            "construction-permit exemption does NOT remove fire obligations — "
                            "different legal matters. Applicability requires confirmation.")
        fire_source = "config/city_permit.json (no fire applicability rule present)"
        requirements["fire_design_review_required"] = _decision(UNKNOWN, no_fire_evidence, fire_source, "low")
        requirements["fire_acceptance_required"] = _decision(
            UNKNOWN, no_fire_evidence + " Acceptance vs filing depends on building classification.",
            fire_source, "low")
        requirements["fire_filing_required"] = _decision(
            UNKNOWN, no_fire_evidence + " Filing applies only where acceptance does not.",
            fire_source, "low")
        requirements["third_party_testing_required"] = _decision(
            UNKNOWN, no_fire_evidence + " Third-party testing tasks are retained conservatively.",
            fire_source, "low")
        requirements["joint_completion_required"] = _decision(
            UNKNOWN,
            "City joint-completion window exists in config (joint_completion_days_min="
            f"{permit_info.get('joint_completion_days_min')}) but project-level applicability is unconfirmed.",
            "config/city_permit.json (window only, not applicability)", "low")
    requirements["landlord_approval_required"] = _decision(
        TRUE,
        "Property-management submission and safety agreement are retained in every template; "
        "contractual obligation independent of statutory permits.",
        "templates/wbs_templates.json (property submission tasks)", "medium")

    conflicts: List[Dict[str, Any]] = []
    # A conflict is when one requirement is claimed both required and exempt by
    # different rules. With config-only evidence this is currently structural:
    # the permit decision contradicts the exemption bool the caller folded on.
    folded_exempt = permit_info.get("is_exempt")
    permit_status = requirements[_CONSTRUCTION_PERMIT]["status"]
    if folded_exempt is True and permit_status == TRUE:
        requirements[_CONSTRUCTION_PERMIT] = _decision(
            UNKNOWN,
            "Conflicting evidence: exemption bool TRUE but rule evaluation says required — "
            "reclassified to " + NEEDS_CONFIRMATION,
            requirements[_CONSTRUCTION_PERMIT]["source"], "low")
        conflicts.append({"requirement": _CONSTRUCTION_PERMIT, "resolution": NEEDS_CONFIRMATION})
    elif folded_exempt is False and permit_status == FALSE:
        requirements[_CONSTRUCTION_PERMIT] = _decision(
            UNKNOWN,
            "Conflicting evidence: exemption bool FALSE but rule evaluation says exempt — "
            "reclassified to " + NEEDS_CONFIRMATION,
            requirements[_CONSTRUCTION_PERMIT]["source"], "low")
        conflicts.append({"requirement": _CONSTRUCTION_PERMIT, "resolution": NEEDS_CONFIRMATION})

    unknown = [k for k, v in requirements.items() if v["status"] == UNKNOWN]
    return {
        "requirements": requirements,
        "conflicts": conflicts,
        "unknown_requirements": unknown,
        "needs_confirmation": bool(unknown or conflicts),
        "note": ("UNKNOWN means the engine refuses to guess; fire/landlord/completion "
                 "obligations are modelled independently of the construction permit."),
    }


def authority_decision_trace(decisions: Dict[str, Any]) -> List[str]:
    """Human-readable trace lines: Requirement / Status / Reason / Source / Confidence."""
    lines = ["AUTHORITY DECISION TRACE"]
    for key, d in decisions["requirements"].items():
        lines.append(f"  {key:<32} {d['status']:<8} confidence={d['confidence']}")
        lines.append(f"    reason: {d['reason']}")
        lines.append(f"    source: {d['source']}")
    if decisions["conflicts"]:
        lines.append(f"  conflicts: {decisions['conflicts']}")
    if decisions["unknown_requirements"]:
        lines.append("  needs confirmation: " + ", ".join(decisions["unknown_requirements"]))
    return lines


def load_authority_task_map() -> Dict[str, Any]:
    import json
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "authority_task_map.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _resolve_task_disposition(matched_statuses: List[str]) -> str:
    """P0-01: precedence resolution — NEVER first-match-wins.

        any TRUE     -> MANDATORY_COMMITTED
        else any UNKNOWN -> CONDITIONAL_PLACEHOLDER
        else (all FALSE) -> REMOVED_OR_FOLDED
    """
    if TRUE in matched_statuses:
        return "MANDATORY_COMMITTED"
    if UNKNOWN in matched_statuses:
        return "CONDITIONAL_PLACEHOLDER"
    return "REMOVED_OR_FOLDED"


def map_authority_tasks(tasks: List[Dict[str, Any]],
                        decisions: Dict[str, Any]) -> Dict[str, Any]:
    """Map each authority requirement to the WBS activities it governs.

    FIX-03: an UNKNOWN requirement keeps its activities VISIBLE but they become
    CONDITIONAL PLACEHOLDERS — never a confirmed mandatory duration inside the
    committed baseline. Requirements with no matching task are reported as
    missing anchors, never invented.

    P0-01: a single WBS task may match MULTIPLE authority requirements (e.g.
    fire_acceptance_required and fire_filing_required both match "竣工消防备案").
    First-match-wins is forbidden: the task's disposition is resolved by
    precedence over ALL matching requirements so that one FALSE requirement can
    never fold a task another TRUE / UNKNOWN requirement still governs.
    """
    mapping = load_authority_task_map()["requirement_task_map"]
    out: Dict[str, Any] = {}
    task_to_reqs: Dict[Any, List[str]] = {}

    for req_name, spec in mapping.items():
        decision = decisions["requirements"].get(req_name)
        if decision is None:
            continue
        matched = []
        for t in tasks:
            name = str(t.get("name", ""))
            if (req_name in t.get('authority_requirements', []) if t.get('authority_path_resolved')
                    else any(kw in name for kw in spec["keywords"])):
                matched.append(t)
        status = decision["status"]
        entry = {
            "requirement_name": req_name,
            "status": status,
            "legal_status": decision.get("legal_status", status),
            "included_in_programme": decision.get("included_in_programme", status == TRUE),
            "programme_basis": decision.get("programme_basis"),
            "pm_confirmation": decision.get("pm_confirmation", "not_recorded"),
            "basis": decision["reason"],
            "source": decision["source"],
            "confidence": decision["confidence"],
            "committed_to_baseline": decision.get("committed_to_baseline", status == TRUE),
            "potential_schedule_impact": decision.get("potential_schedule_impact", status == UNKNOWN),
            "decision_required": decision.get("decision_required", status == UNKNOWN),
            "chain": spec["chain"],
            "tasks": [{"id": t["id"], "name": str(t.get("name", "")),
                       "template_duration_days": t.get("duration_days", t.get("duration", 0))}
                      for t in matched],
            "missing_anchor": not matched,
            "disposition": ("PLANNING_ALLOWANCE" if status == UNKNOWN and decision.get("included_in_programme")
                            else "MANDATORY_COMMITTED" if status == TRUE
                            else "REMOVED_OR_FOLDED" if status == FALSE
                            else "CONDITIONAL_PLACEHOLDER"),
        }
        for t in matched:
            task_to_reqs.setdefault(t["id"], []).append(req_name)
        out[req_name] = entry

    # ---- P0-01: per-task resolution over ALL matching requirements ---------
    task_resolutions: Dict[Any, Dict[str, Any]] = {}
    for tid, req_names in task_to_reqs.items():
        statuses = [TRUE if out[rn]["included_in_programme"] else out[rn]["status"] for rn in req_names]
        resolved = _resolve_task_disposition(statuses)
        if resolved == "MANDATORY_COMMITTED" and not any(out[rn]["status"] == TRUE for rn in req_names):
            resolved = "PLANNING_ALLOWANCE"
        matched_reqs = [{"requirement_name": rn, "status": out[rn]["status"]}
                        for rn in req_names]
        task_resolutions[tid] = {
            "task_id": tid,
            "matched_requirements": matched_reqs,
            "resolved_status": resolved,
            "resolution_reason": (
                "at least one matching requirement is TRUE"
                if resolved == "MANDATORY_COMMITTED"
                else "conservative programme inclusion; legal applicability remains UNKNOWN"
                if resolved == "PLANNING_ALLOWANCE"
                else "no TRUE match; at least one matching requirement is UNKNOWN"
                if resolved == "CONDITIONAL_PLACEHOLDER"
                else "all matching requirements are FALSE"),
        }

    return {
        "requirements": out,
        # P0-01: traceability of every shared task's final disposition.
        "task_resolutions": task_resolutions,
        "conditional_task_ids": sorted(tid for tid, r in task_resolutions.items()
                                       if r["resolved_status"] == "CONDITIONAL_PLACEHOLDER"),
        "mandatory_task_ids": sorted(tid for tid, r in task_resolutions.items()
                                     if r["resolved_status"] == "MANDATORY_COMMITTED"),
        "planning_allowance_task_ids": sorted(tid for tid, r in task_resolutions.items()
                                               if r["resolved_status"] == "PLANNING_ALLOWANCE"),
        "folded_task_ids": sorted(tid for tid, r in task_resolutions.items()
                                  if r["resolved_status"] == "REMOVED_OR_FOLDED"),
        "missing_anchors": [k for k, v in out.items() if v["missing_anchor"]],
    }


def authority_wbs_consistency(tasks: List[Dict[str, Any]],
                              decisions: Dict[str, Any]) -> List[Dict[str, str]]:
    """No contradictory WBS generation.

    - construction_permit FALSE  -> no permit-application tasks may remain
    - construction_permit TRUE   -> the permit milestone must be present
    - UNKNOWN fire obligations   -> conservative retention is acceptable and
                                    disclosed (not a contradiction)
    """
    issues: List[Dict[str, str]] = []
    status = decisions["requirements"][_CONSTRUCTION_PERMIT]["status"]
    permit_tasks = [t for t in tasks if any(k in str(t.get("name", "")) for k in _PERMIT_TASK_KEYWORDS)]
    if status == FALSE and permit_tasks:
        issues.append({"issue": "permit_tasks_present_while_exempt",
                       "tasks": ",".join(str(t.get("id")) for t in permit_tasks)})
    if status == TRUE and not permit_tasks:
        issues.append({"issue": "permit_milestone_missing_while_required"})
    return issues
