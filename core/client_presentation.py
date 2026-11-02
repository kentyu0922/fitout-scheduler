# -*- coding: utf-8 -*-
"""V6.5.0 client presentation layer (FIX-10 / FIX-11 / FIX-12).

Splits the ENGINE view from the CLIENT view without touching the frozen scheduling engine.

* FIX-10 — deterministic Task Name sanitisation for client-facing output. State markers
  (``[未决]`` / ``[Pending]`` / ``[Conditional]`` / ``[Provisional]``) and *explanatory*
  parentheses are removed from the Task Name. Legitimate technical identifiers (codes,
  dimensions, standard numbers) are preserved.
* FIX-11 — the removed state is never destroyed: it is migrated into structured fields
  (Requirement Status / Conditional / Basis / Confidence / Potential Duration / Decision
  Owner / Required By) and into a dedicated assumption register.
* FIX-12 — three separate output layers (Client Executive / PM Control / Engine Audit).

The canonical internal activity ID and the canonical internal ``name`` are NEVER modified.
Only ``client_task_name`` is added.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# FIX-10 — deterministic sanitisation
# ---------------------------------------------------------------------------
# State markers that must never appear in a client-facing Task Name.
STATE_MARKERS = ("[未决]", "[Pending]", "[Conditional]", "[Provisional]", "[Pending]",
                 "[未決]", "[TBC]", "[UNKNOWN]")

# Content tokens that mark a parenthetical group as STATE / EXPLANATION metadata
# (removable) rather than a technical identifier (keepable).
STATE_TOKENS = (
    "未决", "未決", "未确认", "未確認", "待确认", "待確認", "暂定", "暫定", "待定",
    "条件", "條件", "条件性", "临时", "臨時", "估计", "估計", "推测", "推測",
    "标准办公", "標準辦公", "不确认", "不確認",
    "pending", "conditional", "provisional", "tbc", "to be confirmed", "unknown",
    "assumed", "assumption", "draft", "placeholder", "unresolved",
)

# A parenthetical whose content looks like a technical identifier (code / dimension /
# standard number / equipment tag) is preserved even if it also contains a state word.
_TECHNICAL_PATTERNS = (
    re.compile(r"\d"),                      # any digit -> dimension / code / standard no.
    re.compile(r"[A-Za-z]{2,}\s*\d"),       # e.g. GB 50016, DN100, C30
    re.compile(r"[φΦø]\s*\d"),              # diameter
)

_BRACKET_GROUP = re.compile(r"[（(]([^（()）]*)[)）]")
# Square-bracket groups are ENGINE metadata, never part of a professional task name.
# They may appear mid-string (e.g. "A区 [M] 隔墙及主管管线隐蔽工程验收"), not only at the start.
_MARKER_ANY = re.compile(r"[\[【][^\[\]【】]{0,24}[\]】]")
_LEADING_MARKER = re.compile(r"^\s*\[[^\]]{1,24}\]\s*")
# Engine emphasis decoration (milestone stars etc.) — not part of a professional task name.
# Includes the U+FE0F variation selector that follows emoji such as ⭐️.
# Applied ANYWHERE in the string (a star is never legitimate inside a task name).
_DECORATION_ANY = re.compile(
    r"[\ufe0f\u2b50\u2605\u2606\u2726\u2727\u2728\u25aa\u25ab\u25c6\u25c7"
    r"\u25a0\u25a1\u25b2\u25b3\u25ba\u25b6\u25cf\u25cb]")
# Leading decoration may also include dashes/spaces.
_DECORATION = re.compile(
    r"^[\s\ufe0f\u2b50\u2605\u2606\u2726\u2727\u2728\u25aa\u25ab\u25c6\u25c7"
    r"\u25a0\u25a1\u25b2\u25b3\u25ba\u25b6\u25cf\u25cb\u2013\u2014]+")


def _is_state_parenthetical(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return True                       # empty () is pure noise
    low = text.lower()
    if any(tok in low or tok in text for tok in STATE_TOKENS):
        # unless it also carries a genuine technical identifier
        return not any(p.search(text) for p in _TECHNICAL_PATTERNS)
    return False


def sanitize_task_name(name: Optional[str]) -> str:
    """Return the client-facing Task Name for an internal task name.

    Deterministic, reversible-by-lookup (the original is kept on the task), and safe for
    official technical identifiers.
    """
    if name is None:
        return ""
    out = str(name)
    # 1. strip any leading state marker (repeat, in case of stacking)
    prev = None
    while prev != out:
        prev = out
        out = _LEADING_MARKER.sub("", out, count=1).lstrip()
    # 2. drop EVERY square-bracket marker, wherever it appears
    prev = None
    while prev != out:
        prev = out
        out = _MARKER_ANY.sub(" ", out)
    # 3. drop explanatory / state parenthetical groups (technical identifiers are preserved)
    def _repl(m: "re.Match[str]") -> str:
        return "" if _is_state_parenthetical(m.group(1)) else m.group(0)
    out = _BRACKET_GROUP.sub(_repl, out)
    # 4. drop engine emphasis decoration (milestone stars etc.) anywhere
    out = _DECORATION_ANY.sub("", out)
    out = _DECORATION.sub("", out)
    # 5. tidy up spacing left behind
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"\s+([，。、）])", r"\1", out).strip()
    return out


# ---------------------------------------------------------------------------
# FIX-11 — state migration
# ---------------------------------------------------------------------------
def extract_state_metadata(task: Dict[str, Any],
                           assumption_register: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build the structured state record for one task (never stored in the Task Name)."""
    internal = str(task.get("name", ""))
    unresolved = bool(task.get("unresolved_placeholder")
                      or task.get("duration_status") == "UNRESOLVED"
                      or task.get("conditional_requirement"))
    scope_excl = task.get("scope_exclusion")
    conditional = bool(task.get("conditional_requirement")) or bool(scope_excl)
    status = "To Be Confirmed" if unresolved else ("Excluded / Not Confirmed" if scope_excl else "Confirmed")
    basis_parts = []
    if task.get("conditional_requirement"):
        drv = task["conditional_requirement"]
        basis_parts.append("Conditional requirement: %s (%s)" % (drv.get("driver_id"), drv.get("status")))
    if scope_excl:
        basis_parts.append(str(scope_excl.get("reason") or "scope exclusion"))
    prod = task.get("productivity") or {}
    if task.get("duration_authority") == "l2_master_beat":
        basis_parts.append(
            "L2 master occupancy %sd (diagnostic Q/R/C %s)"
            % (task.get("duration_days"), task.get("diagnostic_days"))
        )
    pot = task.get("template_duration") if unresolved else task.get("duration_days")
    rec = dict(
        task_id=task.get("id"),
        internal_name=internal,
        client_task_name=sanitize_task_name(internal),
        requirement_status=status,
        conditional=conditional,
        basis="; ".join(basis_parts) or "Engine state: %s" % (task.get("duration_method") or "n/a"),
        confidence=prod.get("confidence") or ("UNKNOWN" if unresolved else "medium"),
        potential_duration=(pot if pot is not None else "UNKNOWN"),
        decision_owner="Client / Authority" if unresolved else None,
        required_by=None,
        marker_in_internal_name=internal.startswith("[未决]") or internal != sanitize_task_name(internal),
    )
    if assumption_register is not None and rec["marker_in_internal_name"]:
        rec["note"] = ("Internal display name carried state metadata; removed from the "
                       "client-facing Task Name and preserved here.")
        assumption_register.append(rec)
    return rec


def apply_client_presentation(tasks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Add ``client_task_name`` + ``client_presentation`` to every task (in place).

    Returns {"tasks": tasks, "assumption_register": [...], "renamed": [...],
             "metadata_destination": ...}. The canonical ``name`` is never modified.
    """
    register: List[Dict[str, Any]] = []
    renamed: List[Dict[str, Any]] = []
    for t in tasks:
        client = sanitize_task_name(t.get("name"))
        t["client_task_name"] = client
        t["client_presentation"] = True
        meta = extract_state_metadata(t, register)
        t["client_state"] = meta
        if client != str(t.get("name", "")):
            renamed.append(dict(task_id=t.get("id"), before=str(t.get("name")),
                                after=client, state=meta["requirement_status"]))
    return dict(
        tasks=tasks,
        assumption_register=register,
        renamed=renamed,
        metadata_destination=dict(
            primary="MPP custom fields (Requirement Status / Conditional / Basis / Confidence / "
                    "Potential Duration / Decision Owner / Required By)",
            fallback="Task Notes + this assumption register when custom fields are unavailable",
            internal="core task['name'] retains the full internal state (never rewritten)",
        ),
    )


# ---------------------------------------------------------------------------
# FIX-12 — three output layers
# ---------------------------------------------------------------------------
LAYER1 = "CLIENT_EXECUTIVE_PROGRAMME"
LAYER2 = "PM_CONTROL_PROGRAMME"
LAYER3 = "ENGINE_AUDIT_ASSUMPTION_DETAIL"


def _is_summary(t: Dict[str, Any]) -> bool:
    lvl = t.get("outline_level") or t.get("level") or 3
    return int(lvl) <= 2


def build_three_layers(tasks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Partition the same schedule into three presentational layers (no math changes)."""
    layer1, layer2, layer3 = [], [], []
    for t in tasks:
        lvl = int(t.get("outline_level") or t.get("level") or 3)
        is_ms = bool(t.get("milestone"))
        summary = lvl <= 2
        row_common = dict(id=t.get("id"), client_task_name=t.get("client_task_name"),
                          internal_name=t.get("name"), start=t.get("start"),
                          finish=t.get("finish"), milestone=is_ms)
        if summary or is_ms:
            layer1.append(row_common)
        if not summary:
            layer2.append(dict(row_common,
                               duration_days=t.get("duration_days"),
                               critical=t.get("critical"),
                               total_slack_days=t.get("total_slack_days"),
                               crew_size=(t.get("productivity") or {}).get("crew_size"),
                               zone=t.get("zone"),
                               predecessors=t.get("predecessors")))
        layer3.append(dict(row_common,
                           duration_days=t.get("duration_days"),
                           duration_method=t.get("duration_method"),
                           productivity=t.get("productivity"),
                           client_state=t.get("client_state"),
                           activity_type=t.get("activity_type"),
                           scope_exclusion=t.get("scope_exclusion"),
                           unresolved_placeholder=t.get("unresolved_placeholder"),
                           duration_status=t.get("duration_status")))
    return dict(
        layer1=dict(name=LAYER1, role="client-facing, no engine jargon",
                    contents=["Client Milestones", "Major Design", "Tender", "Procurement",
                              "Construction", "T&C", "Handover"],
                    rows=layer1),
        layer2=dict(name=LAYER2, role="PM control view",
                    contents=["Detailed activities", "Critical Path", "Float", "Resources",
                              "Zones", "Dependencies", "Control milestones"],
                    rows=layer2),
        layer3=dict(name=LAYER3, role="engine audit / assumption detail",
                    contents=["UNKNOWN", "Provisional assumptions", "Evidence", "Theoretical crew",
                              "Feasible crew", "Congestion", "Conditional logic",
                              "Benchmark status", "Calculation trace"],
                    rows=layer3),
        separation_rule="The three layers are never mixed into one MPP view.",
    )


def audit_task_names(tasks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """MPP acceptance test (FIX-10): search the client-facing Task Name column.

    The audit mirrors the sanitisation rule exactly, so a legitimate official technical
    identifier such as ``(LOA)`` / ``(RFP)`` / ``(In-Wall Inspection)`` is NOT a violation,
    while a state parenthetical such as ``（未确认）`` or ``[未决]`` is.
    """
    hard_tokens = ("[", "]", "未决", "未決", "Pending", "pending", "Conditional",
                   "conditional", "Provisional", "provisional")
    paren_tokens = ("(", ")", "（", "）")
    violations = []
    for t in tasks:
        client = t.get("client_task_name")
        if client is None:
            client = sanitize_task_name(t.get("name"))
        reason = None
        for token in hard_tokens:
            if token in client:
                reason = f"hard token {token!r}"
                break
        if reason is None and any(tok in client for tok in paren_tokens):
            # a parenthesis is only a violation when its content is state metadata
            for m in _BRACKET_GROUP.finditer(client):
                if _is_state_parenthetical(m.group(1)):
                    reason = f"state parenthetical {m.group(0)!r}"
                    break
            if reason is None:
                # unbalanced / empty parentheses also count
                if len(re.findall(r"[（(]", client)) != len(re.findall(r"[)）]", client)):
                    reason = "unbalanced parenthesis"
        if reason:
            violations.append(dict(task_id=t.get("id"), client_task_name=client, reason=reason))
    return dict(
        checked=len(tasks),
        violations=violations,
        violation_count=len(violations),
        pass_=(len(violations) == 0),
        rule="Task Name must contain no metadata / status usage, except legitimate official "
             "technical identifiers.",
        exceptions="Parenthetical groups carrying a code / dimension / standard number "
                   "(e.g. (LOA), (RFP), (DN100), (GB 50016)) are legitimate and preserved.",
    )
