"""FIX-01 — ACTIVITY CALENDAR SEMANTICS (V6.4.2).

Three distinct calendar concepts must never be conflated:

    PROJECT CALENDAR   — the default calendar a task inherits when nothing is declared.
    ACTIVITY CALENDAR  — the working-day pattern of THIS activity type.
    RESOURCE CALENDAR  — the availability of an individual crew / person.

A 7-day SITE operating calendar is a legitimate planning assumption for
mainland-China commercial fit-out. It is NOT a licence to run Design, Tender,
Client Approval or Authority Review on a 7-day week, and it is NOT a statement
that one worker works seven consecutive days.

This module:
  * classifies every leaf activity into an activity class with a deterministic
    priority: explicit activity_class > specialised keyword (procurement /
    authority) > generic keyword > phase fallback > declared fallback — always
    with a basis;
  * resolves a calendar per activity from config/activity_calendar.json;
  * binds SITE_CONSTRUCTION / SITE_INSTALLATION to the selected construction
    calendar profile (5D/6D/7D) so --calendar_profile still works, while no other
    class may inherit it;
  * supports project override per activity class;
  * emits a per-activity trace: calendar_id / calendar_type /
    working_days_per_week / holiday_policy / basis / confidence / override_source.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "ActivityCalendarError",
    "ACTIVITY_CLASSES",
    "load_activity_calendar_config",
    "classify_activity",
    "resolve_activity_calendar",
    "apply_activity_calendars",
    "activity_calendar_block",
    "resource_calendar_statement",
    "parse_overrides",
]

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_BASE, "config", "activity_calendar.json")

ACTIVITY_CLASSES = (
    "SITE_CONSTRUCTION",
    "SITE_INSTALLATION",
    "TESTING_COMMISSIONING",
    "DESIGN",
    "CONSULTANT_REVIEW",
    "CLIENT_APPROVAL",
    "TENDER_COMMERCIAL",
    "AUTHORITY_APPROVAL",
    "LANDLORD_APPROVAL",
    "FACTORY_PRODUCTION",
    "LOGISTICS_DELIVERY",
    "HANDOVER_MOVE",
    "PROJECT_MANAGEMENT",
)

_CACHE: Optional[Dict[str, Any]] = None


class ActivityCalendarError(ValueError):
    """Raised for an invalid override payload or unknown calendar definition."""


def load_activity_calendar_config() -> Dict[str, Any]:
    global _CACHE
    if _CACHE is None:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _CACHE = json.load(f)
    return _CACHE


def _is_leaf(task: Dict[str, Any]) -> bool:
    return int(task.get("outline_level", task.get("level", 3)) or 3) >= 3


def classify_activity(task: Dict[str, Any],
                      phase_name: str = "") -> Tuple[str, str]:
    """Return (activity_class, classification_basis) for one task.

    P1-03 — deterministic priority (specialised beats generic):
        1. explicit ``activity_class`` field (declared calendar class)
        2. specialised keyword rules (procurement / authority disambiguators)
        3. generic name keyword rules
        4. phase fallback
        5. declared fallback

    Every branch carries a basis string. A generic keyword such as ``机电``
    (site construction) or ``审查`` (consultant review) must never override a
    specific procurement (``招采`` / ``采购``) or statutory-authority
    (``消防设计审查``) semantics, because those are checked in the specialised
    tier first.
    """
    cfg = load_activity_calendar_config()
    name = str(task.get("name", ""))

    # 1. Explicit declared calendar class wins (activity_type/class override).
    declared = str(task.get("activity_class") or "").strip()
    if declared:
        if declared in ACTIVITY_CLASSES:
            return declared, "explicit activity_class field: %r" % declared
        if declared in cfg.get("class_defaults", {}):
            return declared, "explicit activity_class field: %r" % declared

    # 2. Specialised disambiguators (procurement / authority) before generic.
    for rule in cfg.get("specialised_classification_rules", []):
        for kw in rule["keywords"]:
            if kw in name:
                return rule["class"], "%s '%s'" % (rule["basis"], kw)

    # 3. Generic name keyword rules.
    for rule in cfg["classification_rules"]:
        for kw in rule["keywords"]:
            if kw in name:
                return rule["class"], "%s '%s'" % (rule["basis"], kw)

    # 4. Phase fallback.
    phase = str(phase_name or task.get("phase", "") or "")
    for rule in cfg["phase_fallback_rules"]:
        for kw in rule["phase_keywords"]:
            if kw in phase:
                return rule["class"], "%s '%s' (phase='%s')" % (rule["basis"], kw, phase)

    # 5. Declared fallback.
    fb = cfg["fallback"]
    return fb["class"], "%s (name='%s')" % (fb["basis"], name)


def parse_overrides(raw: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Parse a project override spec.

    Accepted forms (JSON):
      '{"FACTORY_PRODUCTION": {"working_days_per_week": 7}}'
      '{"DESIGN": {"calendar_id": "SITE_7D"}}'
    Also accepts a path to a .json file.
    """
    if not raw:
        return {}
    text = raw.strip()
    if not text.startswith("{"):
        with open(text, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ActivityCalendarError("activity calendar override must be a JSON object")
    cfg = load_activity_calendar_config()
    allowed = set(cfg["override"]["supported_classes"])
    allowed_wd = set(cfg["override"]["allowed_working_days_per_week"])
    out: Dict[str, Dict[str, Any]] = {}
    for cls, spec in data.items():
        if cls not in allowed:
            raise ActivityCalendarError(
                f"unknown activity class '{cls}' in override; supported: {sorted(allowed)}")
        if not isinstance(spec, dict):
            raise ActivityCalendarError(f"override for '{cls}' must be an object")
        entry: Dict[str, Any] = {}
        if "calendar_id" in spec:
            if spec["calendar_id"] not in cfg["calendar_definitions"]:
                raise ActivityCalendarError(
                    f"unknown calendar_id '{spec['calendar_id']}' for '{cls}'")
            entry["calendar_id"] = spec["calendar_id"]
        if "working_days_per_week" in spec:
            wd = int(spec["working_days_per_week"])
            if wd not in allowed_wd:
                raise ActivityCalendarError(
                    f"working_days_per_week must be one of {sorted(allowed_wd)}, got {wd}")
            entry["working_days_per_week"] = wd
        if "basis" in spec:
            entry["basis"] = str(spec["basis"])
        if not entry:
            raise ActivityCalendarError(f"override for '{cls}' carries no recognised key")
        out[cls] = entry
    return out


def resolve_activity_calendar(task: Dict[str, Any],
                              phase_name: str = "",
                              construction_calendar_mode: str = "7D",
                              overrides: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Resolve the effective activity calendar for one task."""
    cfg = load_activity_calendar_config()
    defs = cfg["calendar_definitions"]
    cls, basis = classify_activity(task, phase_name)
    if cls not in cfg["class_defaults"]:
        raise ActivityCalendarError(f"activity class '{cls}' has no calendar default")
    default = cfg["class_defaults"][cls]

    calendar_id = default["calendar_id"]
    working_days = None
    override_source = None
    confidence = default.get("confidence", "planning_default")
    resolved_basis = default["basis"]

    ov = (overrides or {}).get(cls)
    if ov:
        override_source = cfg["override"]["override_source_template"]
        if "calendar_id" in ov:
            calendar_id = ov["calendar_id"]
        if "working_days_per_week" in ov:
            working_days = ov["working_days_per_week"]
        if "basis" in ov:
            resolved_basis = ov["basis"]
        confidence = "project_override"
        basis = "%s | override applied (%s)" % (basis, override_source)

    # Site classes are bound to the selected construction calendar profile, so
    # --calendar_profile 5d/6d still governs site work — and ONLY site work.
    if default.get("site_calendar_bound") and "working_days_per_week" not in (ov or {}):
        working_days = int({"5D": 5, "6D": 6, "7D": 7}[construction_calendar_mode])
        calendar_id = "SITE_%s" % construction_calendar_mode
        if not ov:
            resolved_basis = "%s (bound to construction calendar profile %s)" % (
                default["basis"], construction_calendar_mode)

    if calendar_id not in defs:
        raise ActivityCalendarError(f"calendar_id '{calendar_id}' is not defined")
    definition = defs[calendar_id]
    if working_days is None:
        working_days = int(definition["working_days_per_week"])

    block = {
        "calendar_id": calendar_id,
        "calendar_type": definition["calendar_type"],
        "activity_class": cls,
        "working_days_per_week": working_days,
        "holiday_policy": definition["holiday_policy"],
        "basis": resolved_basis,
        "confidence": confidence,
        "override_source": override_source,
        "classification_basis": basis,
        "calendar_mode": "%dD" % working_days,
        "site_calendar_bound": bool(default.get("site_calendar_bound")),
        "note": default.get("note"),
    }
    return block


def apply_activity_calendars(tasks: List[Dict[str, Any]],
                              construction_calendar_mode: str = "7D",
                              overrides: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Assign a resolved activity calendar to every leaf task.

    Returns a report with the per-class summary, the override log and the
    explicit statement that no class silently inherited the site calendar.
    """
    phase = ""
    assignments: List[Dict[str, Any]] = []
    by_class: Dict[str, Dict[str, Any]] = {}
    for t in tasks:
        level = int(t.get("outline_level", t.get("level", 3)) or 3)
        if level <= 2:
            phase = str(t.get("name", ""))
            continue
        block = resolve_activity_calendar(t, phase, construction_calendar_mode, overrides)
        if t.get("elapsed_window"):
            block.update(calendar_id="ELAPSED_7D",calendar_mode="7D",working_days_per_week=7,holiday_policy="none",basis="Continuous calendar-day procurement window; applicability separately disclosed",site_calendar_bound=False)
        t["activity_calendar"] = block
        t["calendar_id"] = block["calendar_id"]
        summary = by_class.setdefault(block["activity_class"], {
            "activity_class": block["activity_class"],
            "calendar_id": block["calendar_id"],
            "working_days_per_week": block["working_days_per_week"],
            "holiday_policy": block["holiday_policy"],
            "basis": block["basis"],
            "confidence": block["confidence"],
            "override_source": block["override_source"],
            "task_ids": [],
        })
        summary["task_ids"].append(t.get("id"))
        assignments.append({"id": t.get("id"), "name": str(t.get("name", "")),
                            **{k: block[k] for k in ("calendar_id", "calendar_type",
                                                     "activity_class", "working_days_per_week",
                                                     "holiday_policy", "basis", "confidence",
                                                     "override_source", "classification_basis")}})

    propagators = sorted(c for c, s in by_class.items()
                         if s["working_days_per_week"] > 5
                         and c not in ("SITE_CONSTRUCTION", "SITE_INSTALLATION"))
    report = {
        "version": load_activity_calendar_config()["version"],
        "construction_calendar_mode": construction_calendar_mode,
        "site_calendar_classes": sorted(c for c, s in by_class.items() if s["working_days_per_week"] > 5),
        "business_calendar_classes": sorted(c for c, s in by_class.items() if s["working_days_per_week"] <= 5),
        "non_site_classes_on_extended_week": propagators,
        "no_global_7d_propagation": not propagators or all(
            c in ("FACTORY_PRODUCTION", "LOGISTICS_DELIVERY") for c in propagators),
        "overrides": {k: v for k, v in (overrides or {}).items()},
        "by_class": by_class,
        "assignments": assignments,
        "resource_calendar": resource_calendar_statement(),
    }
    return report


def activity_calendar_block(report: Dict[str, Any]) -> Dict[str, Any]:
    """Serializable summary block for the schedule payload."""
    return {
        "version": report["version"],
        "construction_calendar_mode": report["construction_calendar_mode"],
        "site_calendar_classes": report["site_calendar_classes"],
        "business_calendar_classes": report["business_calendar_classes"],
        "non_site_classes_on_extended_week": report["non_site_classes_on_extended_week"],
        "no_global_7d_propagation": report["no_global_7d_propagation"],
        "overrides": report["overrides"],
        "by_class": {k: {kk: vv for kk, vv in v.items() if kk != "task_ids"}
                     for k, v in report["by_class"].items()},
        "resource_calendar": report["resource_calendar"],
        "assignment_count": len(report["assignments"]),
    }


def resource_calendar_statement() -> Dict[str, Any]:
    """The standing disclosure that an activity calendar is not a resource calendar."""
    cfg = load_activity_calendar_config()
    rc = cfg["resource_calendar"]
    return {
        "implemented": rc["implemented"],
        "statement": rc["statement"],
        "disclosure": rc["disclosure"],
        "not_implemented_reason": rc["not_implemented_reason"],
    }
