"""P1-06 Procurement profiles: right-sized serialisation for the tender phase.

The WBS template chains Phase 2 procurement tasks as a single FS critical chain.
That is the safe default, but a small invite tender legitimately overlaps
shortlist drafting with RFP preparation and RFI clarification with tender
evaluation. This module applies a DECLARED overlap profile — never a guess:
every re-link comes from config/procurement_profiles.json with a
relationship_reason, and the four hard gates (bid submission, BAFO, LOA,
contract) always remain FS.

Public tender is never fast-tracked: statutory waiting/publicity windows are
not compressible by project size.
"""
import json
import os
from typing import Any, Dict, List, Optional

from core._common import parse_predecessor

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_BASE, "config", "procurement_profiles.json")

_cache: Optional[Dict[str, Any]] = None


class ProcurementProfileError(ValueError):
    """Raised for unknown/unimplemented profile keys or config errors."""


def load_procurement_profiles() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _cache = json.load(f)
        unknown = set(_cache["profiles"]) - {"STANDARD", "FAST_TRACK_SMALL", "COMPLEX"}
        if unknown:
            raise ProcurementProfileError(f"unknown procurement profiles in config: {sorted(unknown)}")
    return _cache


def resolve_procurement_profile(profile_key: Optional[str] = None,
                                area_sqm: Optional[float] = None,
                                bidding: Optional[str] = None) -> Dict[str, Any]:
    """Resolve the effective profile.

    profile_key=None -> auto-select by area and bidding mode with an explicit
    basis string (never a silent default). FAST_TRACK_SMALL only applies to
    invite tender at or below the configured area cap.
    """
    cfg = load_procurement_profiles()
    defaulted = False
    if profile_key is None:
        profile_key = cfg.get("default_profile", "STANDARD")
        # Auto fast-track: small area AND invite tender. Public tender keeps
        # statutory windows — size alone never unlocks overlap.
        if (area_sqm is not None and bidding == "invite"
                and area_sqm <= cfg["profiles"]["FAST_TRACK_SMALL"]["applies_when"]["area_sqm_max"]):
            profile_key = "FAST_TRACK_SMALL"
            defaulted = True
            basis = (f"auto: area {area_sqm:g}sqm <= {cfg['profiles']['FAST_TRACK_SMALL']['applies_when']['area_sqm_max']} "
                     f"AND bidding=invite (user did not select explicitly)")
        elif (area_sqm is not None
              and area_sqm >= cfg["profiles"]["COMPLEX"]["applies_when"].get("area_sqm_min", 0)):
            profile_key = "COMPLEX"
            defaulted = True
            basis = f"auto: area {area_sqm:g}sqm >= {cfg['profiles']['COMPLEX']['applies_when']['area_sqm_min']}"
        else:
            defaulted = True
            basis = f"auto: default profile (area={area_sqm}, bidding={bidding})"
    if profile_key not in cfg["profiles"]:
        raise ProcurementProfileError(
            f"unknown procurement profile '{profile_key}'; valid: {sorted(cfg['profiles'])}")
    if bidding == "public" and profile_key == "FAST_TRACK_SMALL":
        raise ProcurementProfileError(
            "public tender cannot use FAST_TRACK_SMALL: statutory waiting/publicity windows "
            "are not compressible by project size")
    return dict(profile=profile_key,
                config=cfg["profiles"][profile_key],
                defaulted=defaulted,
                basis=basis if profile_key is not None and defaulted else "explicit user selection",
                version=cfg.get("version"))


def _find(tasks: List[Dict[str, Any]], needle: str) -> Optional[Dict[str, Any]]:
    for t in tasks:
        if needle in str(t.get("name", "")):
            return t
    return None


def apply_procurement_profile(tasks: List[Dict[str, Any]], resolved: Dict[str, Any]) -> Dict[str, Any]:
    """Apply declared overlaps to the task list (in place where matched).

    For each overlap, the successor's FS reference to the predecessor is
    replaced by the declared relationship (e.g. SS+1). If the successor does
    not actually FS-reference the declared predecessor, the overlap is SKIPPED
    with a reason — never invented.
    """
    profile = resolved["profile"]
    cfg = resolved["config"]
    applied, skipped = [], []
    for ov in cfg.get("overlaps", []):
        succ = _find(tasks, ov["successor_name_contains"])
        pred = _find(tasks, ov["predecessor_name_contains"])
        if succ is None or pred is None:
            skipped.append(dict(rule=ov, reason=f"task not found by name "
                                                f"(succ={ov['successor_name_contains']!r} found={succ is not None}, "
                                                f"pred={ov['predecessor_name_contains']!r} found={pred is not None})"))
            continue
        rels = parse_predecessor(str(succ.get("predecessors", "") or ""))
        entry = next((r for r in rels if r[0] == pred["id"]), None)
        if entry is None or entry[1] != "FS":
            skipped.append(dict(rule=ov, reason=f"successor '{succ['name']}' does not FS-reference "
                                               f"'{pred['name']}' (current: {entry}); refusing to invent a link"))
            continue
        rel, lag = ov["relationship"], int(ov.get("lag", 0))
        parts = []
        for pid, r, l in rels:
            if pid == pred["id"]:
                parts.append(f"{pid}{rel}{'+' + str(lag) if lag >= 0 else lag}")
            else:
                parts.append(f"{pid}{r}{'+' + str(l) if l >= 0 else l}")
        old = succ.get("predecessors", "")
        succ["predecessors"] = ",".join(parts)
        succ.setdefault("procurement_overlap", []).append(dict(
            from_task=pred["id"], relationship=rel, lag=lag,
            parallelisable=ov.get("parallelisable", False),
            relationship_reason=ov.get("relationship_reason", "")))
        applied.append(dict(successor=dict(id=succ["id"], name=succ["name"]),
                            predecessor=dict(id=pred["id"], name=pred["name"]),
                            old_predecessors=old, new_predecessors=succ["predecessors"],
                            relationship=rel, lag=lag,
                            parallelisable=ov.get("parallelisable", False),
                            relationship_reason=ov.get("relationship_reason", "")))
    # Protected gates must remain FS-gated: verify and report, never relax.
    protected = []
    for kw in cfg.get("protected_gates", []):
        for t in tasks:
            if kw in str(t.get("name", "")):
                rels = parse_predecessor(str(t.get("predecessors", "") or ""))
                protected.append(dict(task=dict(id=t["id"], name=t["name"]), keyword=kw,
                                       predecessors=[dict(id=p, relationship=r, lag=l) for p, r, l in rels],
                                       all_fs=all(r == "FS" for _, r, _ in rels) if rels else False))
    return dict(profile=profile, applied=applied, skipped=skipped, protected_gates=protected,
                basis=resolved.get("basis"), defaulted=resolved.get("defaulted", False))


def procurement_profile_block(resolved: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    """Serializable basis block for the schedule JSON / reports."""
    return dict(profile=report["profile"],
                basis=report.get("basis"),
                defaulted=report.get("defaulted", False),
                overlaps_applied=[dict(successor=a["successor"]["name"], predecessor=a["predecessor"]["name"],
                                       relationship=a["relationship"], lag=a["lag"],
                                       parallelisable=a["parallelisable"],
                                       relationship_reason=a["relationship_reason"])
                                  for a in report["applied"]],
                overlaps_skipped=[dict(rule=s["rule"].get("successor_name_contains"),
                                       reason=s["reason"]) for s in report["skipped"]],
                protected_gates=[dict(task=p["task"]["name"], all_fs=p["all_fs"])
                                 for p in report["protected_gates"]])
