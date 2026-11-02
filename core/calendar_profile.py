"""P1-12 — construction calendar profiles.

The construction calendar used to be silently 7x8. Profiles now make the
choice explicit: 5D / 6D / 7D are implemented end-to-end (solver + MPP
renderer); NIGHT_ALLOWED / WEEKEND_ONLY_SPECIAL / CUSTOM are declared but
rejected on selection — never silently approximated. The default remains 7D
for backward compatibility with the locked dual-calendar policy, but every
run discloses calendar_basis and recommends 6D.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / "config" / "calendar_profiles.json"

IMPLEMENTED_MODES = ("5D", "6D", "7D")


class CalendarProfileError(ValueError):
    """Raised when a declared-but-unimplemented profile is selected."""


def load_calendar_profiles() -> Dict[str, Any]:
    with open(_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def resolve_calendar_profile(profile_key: Optional[str] = None,
                              building_restriction: str = "none") -> Dict[str, Any]:
    """Resolve the effective construction calendar mode + disclosure metadata.

    profile_key None -> default 7D (locked policy) with loud disclosure.
    building_restriction 'no_weekend_work' forces 5D regardless of profile.
    """
    cfg = load_calendar_profiles()
    restrictions = cfg.get("building_restrictions", {})
    if building_restriction not in restrictions:
        raise CalendarProfileError(
            f"unknown building_restriction '{building_restriction}'; expected one of {sorted(restrictions)}")

    defaulted = profile_key is None
    key = (profile_key or "7D").upper()
    profiles = cfg.get("profiles", {})
    if key not in profiles:
        raise CalendarProfileError(
            f"unknown calendar profile '{profile_key}'; implemented: {list(IMPLEMENTED_MODES)}")
    profile = profiles[key]
    if not profile.get("implemented"):
        raise CalendarProfileError(
            f"calendar profile '{key}' is declared but not implemented: "
            f"{profile.get('not_implemented_reason', 'no reason recorded')}")

    forced_by_restriction = False
    if building_restriction == "no_weekend_work" and key != "5D":
        key = "5D"
        profile = profiles["5D"]
        forced_by_restriction = True

    return {
        "calendar_mode": key,
        "defaulted_to_7d": defaulted,
        "forced_by_building_restriction": forced_by_restriction,
        "building_restriction": building_restriction,
        "working_days_per_week": profile["working_days_per_week"],
        "weekend_policy": profile["weekend_policy"],
        "statutory_holiday_policy": profile["statutory_holiday_policy"],
        "spring_festival_policy": profile["spring_festival_policy"],
        "shift_policy": profile["shift_policy"],
        "recommended_default": cfg.get("recommended_default"),
        "disclosure": (cfg.get("default_disclosure") if defaulted
                       else f"construction calendar profile {key} selected explicitly"),
        "source": "config/calendar_profiles.json",
    }


def calendar_basis_block(resolved: Dict[str, Any]) -> Dict[str, Any]:
    """The metadata block written into every schedule artifact."""
    return {
        "calendar_basis": resolved["calendar_mode"],
        "defaulted_to_7d": resolved["defaulted_to_7d"],
        "forced_by_building_restriction": resolved["forced_by_building_restriction"],
        "building_restriction": resolved["building_restriction"],
        "working_days_per_week": resolved["working_days_per_week"],
        "weekend_policy": resolved["weekend_policy"],
        "statutory_holiday_policy": resolved["statutory_holiday_policy"],
        "spring_festival_policy": resolved["spring_festival_policy"],
        "shift_policy": resolved["shift_policy"],
        "recommended_default": resolved["recommended_default"],
        "disclosure": resolved["disclosure"],
        "source": resolved["source"],
    }
