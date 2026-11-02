"""P1-11 — fit-out scope profiles.

Inputting only an area used to schedule the complete trade list as fact.
Profiles make the scope assumption explicit: excluded trades become visible
zero-scope placeholders, the default remains full scope but is disclosed as
ASSUMED_SCOPE with its assumed trades, reused systems and unknowns. CUSTOM is
rejected on selection rather than defaulted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / "config" / "scope_profiles.json"

PROFILE_KEYS = ("shell_to_catb", "cat_a_to_catb", "existing_refurb",
                "light_refresh", "full_renovation", "custom")


class ScopeProfileError(ValueError):
    pass


def load_scope_profiles() -> Dict[str, Any]:
    with open(_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def resolve_scope_profile(profile_key: Optional[str] = None,
                           site_condition: Optional[str] = None) -> Dict[str, Any]:
    """Resolve a scope profile. None -> ASSUMED_SCOPE (full trade list, disclosed)."""
    unknowns = ["per-trade inclusion (no measured takeoff)"]
    sc = (site_condition or "").lower()
    if sc in ("", "unknown", "none"):
        unknowns.insert(0, "demolition scope (site condition)")
    if profile_key is None:
        return {
            "profile": None,
            "assumed_scope": True,
            "status": "ASSUMED_SCOPE",
            "excluded_activity_types": [],
            "assumed_reuse": [],
            "unknowns": unknowns,
            "note": ("Only an area was provided: the full trade list is scheduled as an "
                     "ASSUMED SCOPE, not a confirmed scope. Supply --scope_profile and/or "
                     "measured --quantities to make it explicit."),
        }
    key = str(profile_key).lower()
    if key not in PROFILE_KEYS:
        raise ScopeProfileError(
            f"unknown scope profile '{profile_key}'; expected one of {list(PROFILE_KEYS)}")
    cfg = load_scope_profiles()
    profile = cfg["profiles"][key.upper()]
    if profile.get("implemented") is False:
        raise ScopeProfileError(
            f"scope profile '{key}' is declared but not selectable: "
            f"{profile.get('not_implemented_reason', 'no reason recorded')}")
    included = profile.get("included_activity_types")
    return {
        "profile": key.upper(),
        "assumed_scope": False,
        "status": "EXPLICIT_SCOPE",
        "included_activity_types": included,
        "excluded_activity_types": list(profile.get("excluded_activity_types") or []),
        "assumed_reuse": list(profile.get("assumed_reuse") or []),
        "unknowns": ([n for n in [profile.get("notes")] if n] or []),
        "description": profile.get("description", ""),
        "source": "config/scope_profiles.json",
    }


def scope_profile_exclusions(resolved: Dict[str, Any],
                             active_activity_types: List[str]) -> List[str]:
    """Effective excluded set = explicit exclusions + (complement of an inclusion list)."""
    excluded = set(resolved.get("excluded_activity_types") or [])
    included = resolved.get("included_activity_types")
    if included is not None:
        excluded |= {a for a in active_activity_types if a not in included}
    return sorted(excluded)
