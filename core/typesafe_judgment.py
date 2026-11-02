# -*- coding: utf-8 -*-
"""Closed-question review of an engine run.

Code keeps the arithmetic. A model may only judge wording and the handoff.
Nothing here writes duration_days, starts, finishes, or a client commitment.
The package does not contain an API credential and does not call the network.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

_READING_KEYS = (
    "overlapped_site_calendar_days",
    "envelope_band",
    "envelope_low",
    "envelope_high",
    "envelope_status",
)


def span_position(readings: Optional[Dict[str, Any]]) -> str:
    """Compare the solved site span with the engine band. This is arithmetic."""
    readings = readings or {}
    span = readings.get("overlapped_site_calendar_days")
    low = readings.get("envelope_low")
    high = readings.get("envelope_high")
    if span is None or low is None or high is None:
        return "unknown"
    span_n = float(span)
    if span_n < float(low):
        return "short_of_band"
    if span_n > float(high):
        return "long_of_band"
    return "inside_band"


def build_review_request(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Build one request with independent questions over the same observed state.

    Question ids are for code. The instructions carry the full meaning.
    """
    readings = dict(summary.get("duration_readings") or {})
    observed_readings = {key: readings.get(key) for key in _READING_KEYS}
    state = {
        "observed": {
            "project_name": summary.get("project_name"),
            "area": summary.get("area"),
            "start_date": summary.get("start_date"),
            "finish_date": summary.get("finish_date"),
            "decision_status": summary.get("decision_status"),
            "duration_readings": observed_readings,
            "span_position": span_position(readings),
            "client_wording": summary.get("client_wording") or "",
        },
        "policy": {
            "dates_stay_a_draft_until_a_named_pm_accepts": True,
            "model_answers_must_not_change_duration_days": True,
            "choice_confidence_is_not_permission_to_commit": True,
        },
    }
    return {
        "model": "jev-latest",
        "state": state,
        "questions": {
            "wording_is_a_commitment": {
                "type": "noul",
                "instructions": (
                    "Does `observed.client_wording` present the finish date as a "
                    "contractual commitment?"
                ),
                "criteria": {
                    "true": "Yes. The wording commits the finish date to the client.",
                    "false": (
                        "No. The wording keeps the date as a draft, or there is no "
                        "client wording."
                    ),
                },
            },
            "handoff": {
                "type": "choice",
                "instructions": (
                    "Which handoff fits `observed.span_position`, "
                    "`observed.decision_status`, and `policy`? "
                    "The span comparison is already in `observed.span_position`."
                ),
                "criteria": {
                    "deliver_as_draft": (
                        "Hand the programme to a named PM as a planning draft."
                    ),
                    "escalate_to_named_pm": (
                        "A named PM has to see the wording or the span position "
                        "before anyone uses the run."
                    ),
                    "hold_for_missing_evidence": (
                        "A verified duration source is missing, so the run cannot "
                        "support a claim about a checked quota."
                    ),
                    "no_matching_handoff": "None of the handoffs fit.",
                },
            },
        },
    }


def interpret_answers(answers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn raw judgments into a handoff. A near-half noul is uncertainty.

    A wording commitment is its own stop. It is not averaged with the choice.
    Choice confidence does not authorize a client commitment.
    """
    answers = answers or {}
    commitment = (answers.get("wording_is_a_commitment") or {}).get("noul")
    handoff = answers.get("handoff") or {}
    choice = handoff.get("choice")
    confidence = handoff.get("confidence")
    reasons = []
    action = "escalate_to_named_pm"

    if commitment is None:
        reasons.append("missing_commitment_noul")
    else:
        commitment_n = float(commitment)
        if 0.4 <= commitment_n <= 0.6:
            reasons.append("noul_near_half")
        elif commitment_n >= 0.8:
            reasons.append("wording_presents_a_commitment")
        elif choice == "hold_for_missing_evidence":
            action = "hold_for_missing_evidence"
            reasons.append(choice)
        elif choice == "deliver_as_draft":
            if confidence is None or float(confidence) < 0.8:
                reasons.append("choice_confidence_is_not_permission")
            else:
                action = "deliver_as_draft"
                reasons.append("draft_wording")
        elif choice in ("escalate_to_named_pm", "no_matching_handoff", None):
            reasons.append(choice or "missing_handoff")
        else:
            reasons.append("unrecognized_handoff")

    return {
        "action": action,
        "reasons": reasons,
        "writes_duration_days": False,
        "client_commitment": False,
    }
