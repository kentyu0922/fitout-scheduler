"""P1-10 Furniture long-lead profiles.

The WBS template hard-codes one universal furniture production/delivery
duration, which is optimistic for commercial office projects. This module
replaces that single number with a DECLARED lead-time profile from
config/furniture_lead_profiles.json (STANDARD_LOCAL / CUSTOM_LOCAL / IMPORTED
/ SYSTEM_FURNITURE / LOOSE_FURNITURE / CUSTOM_JOINERY), each with an
evidence-backed or explicitly provisional basis.

Lead time is a supply-chain fact, not labour: it is NEVER scaled by area.
Default SYSTEM_FURNITURE (35d) is a programme policy, same class as dual IAQ.
"""
import json
import os
from typing import Any, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_BASE, "config", "furniture_lead_profiles.json")

_cache: Optional[Dict[str, Any]] = None


class FurnitureLeadError(ValueError):
    """Raised for unknown profile keys or config errors."""


def load_furniture_lead_profiles() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _cache = json.load(f)
        unknown = set(_cache["profiles"]) - {"STANDARD_LOCAL", "CUSTOM_LOCAL", "IMPORTED",
                                             "SYSTEM_FURNITURE", "LOOSE_FURNITURE", "CUSTOM_JOINERY"}
        if unknown:
            raise FurnitureLeadError(f"unknown furniture lead profiles in config: {sorted(unknown)}")
    return _cache


def resolve_furniture_lead(profile_key: Optional[str] = None) -> Dict[str, Any]:
    """Resolve the effective furniture lead profile.

    profile_key=None -> configured default SYSTEM_FURNITURE as committed policy.
    """
    cfg = load_furniture_lead_profiles()
    key = profile_key or cfg.get("default_profile", "SYSTEM_FURNITURE")
    assumed = False
    if key not in cfg["profiles"]:
        raise FurnitureLeadError(
            f"unknown furniture lead profile '{key}'; valid: {sorted(cfg['profiles'])}")
    entry = cfg["profiles"][key]
    return dict(profile=key, lead_days=int(entry["lead_days"]),
                lead_time_basis=entry["basis"], confidence=entry["confidence"],
                evidence_ref=entry.get("evidence_ref"), assumed=assumed,
                version=cfg.get("version"))


def apply_furniture_lead(tasks: List[Dict[str, Any]], resolved: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the resolved profile to the furniture production/delivery task.

    The task is located by name; its duration becomes the profile lead time
    (the template value is preserved in template_lead_days for audit). If no
    task matches, the profile is reported as not_applied — never invented.
    """
    cfg = load_furniture_lead_profiles()
    needle = cfg.get("furniture_task_name_contains", "家具生产交货")
    task = next((t for t in tasks if needle in str(t.get("name", "")) or
                 t.get('activity_uid') == 'pmp.process.furniture.remaining'), None)
    if task is None:
        return dict(applied=False, reason=f"no task found by {needle!r}",
                    resolved=resolved)
    template_days = task.get("duration_days")
    task["duration_days"] = resolved["lead_days"]
    task["duration"] = resolved["lead_days"]
    task["furniture_lead_profile"] = resolved["profile"]
    task["lead_time_basis"] = resolved["lead_time_basis"]
    task["lead_time_confidence"] = resolved["confidence"]
    task["lead_time_evidence_ref"] = resolved["evidence_ref"]
    task["lead_time_assumed"] = resolved["assumed"]
    task["template_lead_days"] = template_days
    return dict(applied=True, task=dict(id=task["id"], name=task["name"]),
                template_lead_days=template_days, profile_lead_days=resolved["lead_days"],
                profile=resolved["profile"], lead_time_basis=resolved["lead_time_basis"],
                confidence=resolved["confidence"], evidence_ref=resolved["evidence_ref"],
                assumed=resolved["assumed"])


def furniture_lead_block(resolved: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    """Serializable basis block for the schedule JSON / reports."""
    return dict(profile=resolved["profile"], lead_days=resolved["lead_days"],
                lead_time_basis=resolved["lead_time_basis"], confidence=resolved["confidence"],
                evidence_ref=resolved["evidence_ref"],
                assumed=resolved["assumed"],
                applied=report.get("applied", False),
                task=(report.get("task") or {}).get("name"),
                template_lead_days=report.get("template_lead_days"),
                not_applied_reason=report.get("reason"))
