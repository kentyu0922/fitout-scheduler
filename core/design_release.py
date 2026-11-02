"""P1-07 D&B design package release gates.

The D&B template still behaves like DBB: landlord submission waits for the
full IFC set AND third-party drawing review. Real D&B fast-track submits the
landlord package on the confirmed SD (30% concept) and lets it run parallel
to construction-drawing production and review.

This module applies the DECLARED re-links from
config/design_release_gates.json — DB delivery only — and stamps a
design_release_gate field on each re-linked task. The statutory protection is
explicit: the government construction-permit application still FS-references
the drawing-review certificate; site construction only starts behind that
chain. If a declared re-link cannot be verified against the task list it is
skipped with a reason — never invented.
"""
import json
import os
from typing import Any, Dict, List, Optional

from core._common import parse_predecessor

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_BASE, "config", "design_release_gates.json")

_cache: Optional[Dict[str, Any]] = None


class DesignReleaseError(ValueError):
    """Raised for config errors or non-DB usage."""


def load_design_release_gates() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def design_release_applies(delivery: Optional[str]) -> bool:
    cfg = load_design_release_gates()
    return (delivery or "").upper() in {d.upper() for d in cfg.get("applies_to_delivery", [])}


def _find(tasks: List[Dict[str, Any]], needle: str) -> Optional[Dict[str, Any]]:
    for t in tasks:
        if needle in str(t.get("name", "")):
            return t
    return None


def apply_design_release(tasks: List[Dict[str, Any]],
                         delivery: Optional[str] = None,
                         enabled: Optional[bool] = None) -> Dict[str, Any]:
    """Apply D&B design-release re-links. DBB is left sequential by design.

    enabled=None -> auto (on for DB, off otherwise); enabled=True on a DBB
    project is a config violation and raises.
    """
    cfg = load_design_release_gates()
    applies = design_release_applies(delivery)
    if enabled is None:
        enabled = applies
    if enabled and not applies:
        raise DesignReleaseError(
            f"design release re-links apply to {cfg.get('applies_to_delivery')} only; "
            f"delivery={delivery!r} must remain sequential (DBB separates design and "
            "construction contracts, so there is no design-build package release to accelerate)")
    if not enabled:
        return dict(enabled=False, delivery=delivery,
                    reason="design package release applies to D&B (DB) delivery only",
                    applied=[], skipped=[], statutory_protections=[])

    applied, skipped = [], []
    gate_anchors = {}
    for gate_key, g in cfg["gates"].items():
        anchor = _find(tasks, g["anchor_name_contains"])
        if anchor is None:
            skipped.append(dict(kind="gate_anchor_missing", gate=gate_key,
                                reason=f"anchor task not found by {g['anchor_name_contains']!r}"))
            continue
        gate_anchors[gate_key] = dict(id=anchor["id"], name=anchor["name"], maturity=g["maturity"])

    for rl in cfg.get("relinks", []):
        task = _find(tasks, rl["task_name_contains"])
        from_anchor = gate_anchors.get(rl["from_gate"])
        to_anchor = gate_anchors.get(rl["to_gate"])
        if task is None:
            skipped.append(dict(kind="relink", rule=rl, reason=f"task not found by {rl['task_name_contains']!r}"))
            continue
        if from_anchor is None or to_anchor is None:
            skipped.append(dict(kind="relink", rule=rl,
                                reason="from/to gate anchor missing (see gate_anchor_missing)"))
            continue
        rels = parse_predecessor(str(task.get("predecessors", "") or ""))
        entry = next((r for r in rels if r[0] == from_anchor["id"]), None)
        if entry is None or entry[1] != "FS":
            skipped.append(dict(kind="relink", rule=rl,
                                reason=f"'{task['name']}' does not FS-reference gate anchor "
                                        f"'{from_anchor['name']}' (current: {entry}); refusing to invent a link"))
            continue
        parts = []
        for pid, r, l in rels:
            if pid == from_anchor["id"]:
                parts.append(f"{to_anchor['id']}{rl['relationship']}{'+' + str(l) if l >= 0 else l}")
            else:
                parts.append(f"{pid}{r}{'+' + str(l) if l >= 0 else l}")
        old = task.get("predecessors", "")
        task["predecessors"] = ",".join(parts)
        task["design_release_gate"] = rl.get("design_release_gate")
        task["design_release_reason"] = rl.get("reason", "")
        applied.append(dict(task=dict(id=task["id"], name=task["name"]),
                            from_gate=dict(key=rl["from_gate"], **{k: from_anchor[k] for k in ("id", "name")}),
                            to_gate=dict(key=rl["to_gate"], **{k: to_anchor[k] for k in ("id", "name")}),
                            design_release_gate=rl.get("design_release_gate"),
                            old_predecessors=old, new_predecessors=task["predecessors"],
                            reason=rl.get("reason", "")))

    # Statutory protections: verify each declared protection still holds.
    # A protection whose task is absent from the WBS (e.g. Phase 4 folded by a
    # construction-permit exemption) is reported as satisfied=None with the
    # reason — never silently False, never silently True.
    protections = []
    for sp in cfg.get("statutory_protections", []):
        anchor = gate_anchors.get(sp["must_reference_gate"])
        task = _find(tasks, sp["task_name_contains"])
        ok = False
        if anchor is not None and task is not None:
            rels = parse_predecessor(str(task.get("predecessors", "") or ""))
            entry = next((r for r in rels if r[0] == anchor["id"]), None)
            ok = entry is not None and entry[1] == sp["relationship"]
        if task is None:
            protections.append(dict(task=None, gate=sp["must_reference_gate"],
                                    required=sp["relationship"], satisfied=None,
                                    reason=f"protected task not present in WBS "
                                           f"(e.g. permit exemption folded the phase) — "
                                           f"{sp.get('reason', '')}"))
            continue
        protections.append(dict(task=dict(id=task["id"], name=task["name"]),
                                gate=sp["must_reference_gate"], required=sp["relationship"],
                                satisfied=ok, reason=sp.get("reason", "")))
    return dict(enabled=True, delivery=delivery, applied=applied, skipped=skipped,
                statutory_protections=protections, gates=gate_anchors,
                version=cfg.get("version"))


def design_release_block(report: Dict[str, Any]) -> Dict[str, Any]:
    """Serializable basis block for the schedule JSON / reports."""
    return dict(enabled=report.get("enabled", False),
                delivery=report.get("delivery"),
                reason=report.get("reason"),
                relinks=[dict(task=a["task"]["name"], from_gate=a["from_gate"]["key"],
                              to_gate=a["to_gate"]["key"], design_release_gate=a["design_release_gate"],
                              reason=a["reason"]) for a in report.get("applied", [])],
                skipped=[dict(kind=s.get("kind"), reason=s["reason"]) for s in report.get("skipped", [])],
                statutory_protections=[dict(task=(p["task"] or {}).get("name"), gate=p["gate"],
                                            required=p["required"], satisfied=p["satisfied"])
                                       for p in report.get("statutory_protections", [])])
