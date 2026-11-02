"""P1-09 Occupancy readiness profiles: no universal hard-coded tail.

The template ships one near-fixed tail (first IAQ → furniture → flush-out →
second IAQ → cleaning → move) with fixed durations. That is NOT a universal
rule: client requirement, local requirement, certification requirement and
project strategy decide the real tail. This module applies a DECLARED
occupancy_readiness_profile from config/occupancy_readiness_profiles.json:

- STANDARD (product default: dual IAQ GB 50325 then GB/T 18883, committed),
  CLIENT_STRICT, CERTIFICATION_REQUIRED, NO_FORMAL_IAQ (explicit opt-out only).
  CUSTOM is declared-but-not-implemented and rejected.
- Durations of the tail tasks are set from the profile parameters with the
  template value preserved for audit.
- NO_FORMAL_IAQ folds formal IAQ tasks into visible zero-day placeholders —
  never silently dropped.
- CERTIFICATION_REQUIRED protects the second IAQ: if the profile requires it
  and the WBS lacks it, the run fails closed.
"""
import json
import os
from typing import Any, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_BASE, "config", "occupancy_readiness_profiles.json")

_cache: Optional[Dict[str, Any]] = None


class OccupancyProfileError(ValueError):
    """Raised for unknown/unimplemented profiles or protected-gate violations."""


def load_occupancy_profiles() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _cache = json.load(f)
        for key, p in _cache["profiles"].items():
            if key == "CUSTOM":
                continue
            for req in ("first_iaq_days", "flushout_days", "second_iaq_days",
                        "final_cleaning_days", "furniture_sequencing"):
                if req not in p.get("parameters", {}):
                    raise OccupancyProfileError(f"profile {key} missing parameter {req}")
    return _cache


UNKNOWN_PROFILE = "UNKNOWN"
FIRST_IAQ_STANDARD = "GB 50325"
SECOND_IAQ_STANDARD = "GB/T 18883"

UNKNOWN_BASIS = (
    "Occupancy / IAQ was explicitly left UNKNOWN (diagnostic only). "
    "Programme policy otherwise always commits dual IAQ: first GB 50325, second GB/T 18883."
)

FIRM_DUAL_IAQ_BASIS = (
    "Firm dual-IAQ policy for every project: 首次室内空气检测 GB 50325（基材/竣工验收），"
    "二次室内空气检测 GB/T 18883（家具安装及采样条件核实后、交付入住）。"
    "This sequence is committed by default; the only explicit opt-out is "
    "--occupancy_profile NO_FORMAL_IAQ."
)


def resolve_occupancy_profile(profile_key: Optional[str] = None,
                               flushout_days: Optional[int] = None,
                               occupancy_basis: Optional[str] = None,
                               confirmed_by: Optional[str] = None) -> Dict[str, Any]:
    """Resolve the effective occupancy / IAQ requirement.

    Product policy (2026-09-21): dual IAQ is committed on every project
    (GB 50325 then GB/T 18883) unless the caller explicitly selects
    NO_FORMAL_IAQ or diagnostic UNKNOWN.

      * No input / STANDARD        -> CONFIRMED_POLICY, dual GB tests committed.
      * --flushout_days + basis    -> CONFIRMED with an explicit basis / confirmed_by.
      * explicit --occupancy_profile -> CONFIRMED_SELECTION.
      * profile_key UNKNOWN        -> diagnostic UNRESOLVED placeholders (tests / scenarios).
    """
    cfg = load_occupancy_profiles()
    version = cfg.get("version")

    # --- E. CONFIRMED INPUT: explicit flush-out with an explicit basis --------
    if flushout_days is not None:
        if not (occupancy_basis or "").strip():
            raise OccupancyProfileError(
                "--flushout_days requires an explicit --occupancy_basis "
                "(client_requirement / local_regulation / certification_scheme ...); "
                "an untraceable number is not accepted into the committed programme")
        base = cfg["profiles"][cfg.get("default_profile", "STANDARD")]
        params = dict(base["parameters"])
        if int(flushout_days) < 0:
            raise OccupancyProfileError("flushout_days must be nonnegative")
        params["flushout_days"] = int(flushout_days)
        return dict(profile="CONFIRMED_CUSTOM", status="CONFIRMED", committed=True,
                    assumed=False, parameters=params,
                    requires_second_iaq=bool(base.get("requires_second_iaq", True)),
                    formal_iaq=bool(base.get("formal_iaq", True)), certification=None,
                    basis=occupancy_basis.strip(),
                    confirmed_by=(confirmed_by or "unattributed").strip(),
                    confidence="declared_input", flushout_duration=int(flushout_days),
                    first_iaq_standard=FIRST_IAQ_STANDARD,
                    second_iaq_standard=SECOND_IAQ_STANDARD,
                    version=version)

    key_raw = None if profile_key is None else str(profile_key).strip()
    if key_raw and key_raw.upper() == UNKNOWN_PROFILE:
        return dict(profile=UNKNOWN_PROFILE, status="UNKNOWN", committed=False,
                    assumed=False, parameters=None,
                    requires_second_iaq="UNKNOWN", formal_iaq="UNKNOWN",
                    certification=None, basis=UNKNOWN_BASIS,
                    confirmed_by=None, confidence="none",
                    flushout_duration="UNRESOLVED", version=version)

    # --- A. DEFAULT STATE: dual GB IAQ is a firm programme rule ---------------
    if not key_raw:
        key = cfg.get("default_profile", "STANDARD")
        p = cfg["profiles"][key]
        return dict(profile=key, status="CONFIRMED_POLICY", committed=True,
                    assumed=False, parameters=dict(p["parameters"]),
                    requires_second_iaq=p.get("requires_second_iaq", True),
                    formal_iaq=p.get("formal_iaq", True),
                    certification=p.get("certification"),
                    basis=FIRM_DUAL_IAQ_BASIS, confidence="policy",
                    confirmed_by=(confirmed_by or "programme_policy"),
                    flushout_duration=p["parameters"]["flushout_days"],
                    first_iaq_standard=FIRST_IAQ_STANDARD,
                    second_iaq_standard=SECOND_IAQ_STANDARD,
                    version=version)

    assumed = False
    key = profile_key
    if key == "CUSTOM":
        raise OccupancyProfileError(
            "CUSTOM occupancy profile requires explicit per-parameter input and is "
            "not implemented; choose STANDARD/CLIENT_STRICT/CERTIFICATION_REQUIRED/NO_FORMAL_IAQ "
            "or extend config/occupancy_readiness_profiles.json")
    if key not in cfg["profiles"]:
        raise OccupancyProfileError(f"unknown occupancy profile '{key}'; "
                                    f"valid: {sorted(k for k in cfg['profiles'] if k != 'CUSTOM')}")
    p = cfg["profiles"][key]
    return dict(profile=key, status="CONFIRMED_SELECTION", committed=True,
                parameters=dict(p["parameters"]),
                requires_second_iaq=p.get("requires_second_iaq", True),
                formal_iaq=p.get("formal_iaq", True),
                certification=p.get("certification"),
                basis=p["basis"] if key == "NO_FORMAL_IAQ" else FIRM_DUAL_IAQ_BASIS,
                confidence=("policy" if key == "STANDARD" else p.get("confidence", "provisional")),
                assumed=assumed, confirmed_by=(confirmed_by or "unattributed"),
                flushout_duration=p["parameters"]["flushout_days"],
                first_iaq_standard=FIRST_IAQ_STANDARD,
                second_iaq_standard=SECOND_IAQ_STANDARD,
                version=cfg.get("version"))


def conditional_iaq_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """FIX-02 B — the IAQ / flush-out activities that stay visible but carry a
    UNRESOLVED duration when the requirement is UNKNOWN."""
    cfg = load_occupancy_profiles()
    anchors = cfg["task_anchors"]
    needles = [anchors["first_iaq"], anchors["flushout"], anchors["second_iaq"],
               anchors["iaq_pass_milestone"]]
    out = []
    for t in tasks:
        name = str(t.get("name", ""))
        if any(nd in name for nd in needles):
            out.append({"id": t["id"], "name": name,
                        "template_duration_days": t.get("duration_days", t.get("duration", 0))})
    return out


def occupancy_scenario_options(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """FIX-02 C — diagnostic scenarios A/B/C/D.

    Each option returns concrete per-task durations so the CPM can be RE-SOLVED
    (never added up) for every scenario. No option is auto-selected.
    """
    cfg = load_occupancy_profiles()
    anchors = cfg["task_anchors"]
    mapping = {"first_iaq": "first_iaq_days", "flushout": "flushout_days",
               "second_iaq": "second_iaq_days"}
    options = []
    for key, label in (("NO_FORMAL_IAQ", "SCENARIO A — NO_FORMAL_IAQ"),
                       ("STANDARD", "SCENARIO B — STANDARD"),
                       ("CLIENT_STRICT", "SCENARIO C — CLIENT_STRICT"),
                       ("CERTIFICATION_REQUIRED", "SCENARIO D — CERTIFICATION_REQUIRED")):
        p = cfg["profiles"][key]
        params = p["parameters"]
        durations: Dict[int, int] = {}
        for anchor, param in mapping.items():
            needle = anchors[anchor]
            for t in tasks:
                if needle in str(t.get("name", "")):
                    durations[t["id"]] = int(params[param])
        for t in tasks:
            if anchors["iaq_pass_milestone"] in str(t.get("name", "")):
                durations[t["id"]] = 0 if not p.get("requires_second_iaq", True) else 0
        options.append({
            "option_id": key,
            "label": label,
            "flushout_days": int(params["flushout_days"]),
            "testing_days": int(params["first_iaq_days"]) + int(params["second_iaq_days"]),
            "basis": p["basis"],
            "confidence": p.get("confidence", "provisional"),
            "durations": durations,
        })
    return options


def _find(tasks: List[Dict[str, Any]], needle: str) -> Optional[Dict[str, Any]]:
    for t in tasks:
        if needle in str(t.get("name", "")):
            return t
    return None


def apply_occupancy_profile(tasks: List[Dict[str, Any]], resolved: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the profile's tail parameters. Anchors missing from the WBS are
    reported per-anchor (not invented); a required second IAQ missing from the
    WBS fails closed under CERTIFICATION_REQUIRED-style protection."""
    # ---- FIX-02 A/B: UNKNOWN occupancy requirement --------------------------
    # UNKNOWN is not STANDARD, not 35d, not 14d, and not "0-day because we
    # silently dropped it". The activities stay visible as CONDITIONAL
    # PLACEHOLDERS with an UNRESOLVED duration; the exposure is reported
    # separately and re-solved as a scenario.
    if resolved.get("profile") == UNKNOWN_PROFILE or resolved.get("status") == "UNKNOWN":
        conditional = []
        for item in conditional_iaq_tasks(tasks):
            task = next((t for t in tasks if t.get("id") == item["id"]), None)
            if task is None:
                continue
            old = task.get("duration_days", task.get("duration", 0))
            task["duration_days"] = 0
            task["duration"] = 0
            task["duration_status"] = "UNRESOLVED"
            task["committed_to_baseline"] = False
            task["unresolved_placeholder"] = True
            task["template_duration_days"] = old
            task["conditional_requirement"] = dict(
                driver_id="OCCUPANCY_IAQ", requirement="IAQ / Flush-out Requirement",
                status="UNKNOWN", baseline_commitment="UNRESOLVED",
                committed_to_baseline=False, potential_schedule_impact=True,
                decision_required=True, template_duration_days=old,
                reason="Occupancy / IAQ requirement not supplied — UNKNOWN is not "
                       "converted into a committed duration")
            conditional.append(dict(task=dict(id=item["id"], name=item["name"]),
                                    template_duration_days=old,
                                    status="UNKNOWN", baseline_commitment="UNRESOLVED",
                                    committed_to_baseline=False,
                                    decision_required=True))
        return dict(profile=UNKNOWN_PROFILE, status="UNKNOWN", committed=False,
                    assumed=False, basis=resolved["basis"], confidence="none",
                    formal_iaq="UNKNOWN", requires_second_iaq="UNKNOWN",
                    certification=None, parameters=None,
                    flushout_duration="UNRESOLVED",
                    duration_overrides=[], folded=[], missing_anchors=[],
                    conditional_requirements=conditional, furniture_sequencing=None)

    cfg = load_occupancy_profiles()
    anchors = cfg["task_anchors"]
    params = resolved["parameters"]
    duration_overrides, folded, missing = [], [], []

    def _anchor(key: str) -> Optional[Dict[str, Any]]:
        task = _find(tasks, anchors[key])
        if task is None:
            missing.append(dict(anchor=key, needle=anchors[key],
                                reason="task not found in WBS — profile parameter not applied"))
        return task

    def _set_duration(anchor_key: str, param_key: str, fold_reason: Optional[str] = None) -> None:
        task = _anchor(anchor_key)
        if task is None:
            return
        old = task.get("duration_days")
        new = int(params[param_key])
        task["duration_days"] = new
        task["duration"] = new
        task["occupancy_profile"] = resolved["profile"]
        if anchor_key == "flushout":
            task["explicit_ventilation_window"] = resolved.get("status") == "CONFIRMED" and new > 0
        if fold_reason:
            task["occupancy_fold"] = dict(reason=fold_reason, profile=resolved["profile"],
                                          template_duration_days=old)
            folded.append(dict(task=dict(id=task["id"], name=task["name"]),
                               template_duration_days=old, reason=fold_reason))
        duration_overrides.append(dict(anchor=anchor_key, task=dict(id=task["id"], name=task["name"]),
                                       old_duration_days=old, new_duration_days=new))

    formal = resolved["formal_iaq"]
    _set_duration("first_iaq", "first_iaq_days",
                  None if formal else "NO_FORMAL_IAQ: 客户声明不做正式首次空气盲测，折叠为0天占位（非删除）")
    _set_duration("flushout", "flushout_days")
    _set_duration("final_cleaning", "final_cleaning_days")

    second = _anchor("second_iaq")
    iaq_milestone = _anchor("iaq_pass_milestone")
    if resolved["requires_second_iaq"]:
        if second is None:
            raise OccupancyProfileError(
                f"occupancy profile {resolved['profile']} requires a second IAQ retest but the WBS "
                f"has no '{anchors['second_iaq']}' task — certification protection fails closed, "
                f"refusing to schedule without it")
        _set_duration("second_iaq", "second_iaq_days")
    else:
        for task in (second, iaq_milestone):
            if task is None:
                continue
            old = task.get("duration_days")
            task["duration_days"] = 0
            task["duration"] = 0
            task["milestone"] = True
            task["occupancy_fold"] = dict(
                reason=f"profile {resolved['profile']}: requires_second_iaq=false — 二次复测折叠为0天占位（非删除）",
                profile=resolved["profile"], template_duration_days=old)
            folded.append(dict(task=dict(id=task["id"], name=task["name"]),
                               template_duration_days=old,
                               reason=task["occupancy_fold"]["reason"]))
            duration_overrides.append(dict(anchor="second_iaq" if task is second else "iaq_pass_milestone",
                                           task=dict(id=task["id"], name=task["name"]),
                                           old_duration_days=old, new_duration_days=0))

    first_task = _anchor("first_iaq")
    if first_task is not None:
        first_task["iaq_standard"] = resolved.get("first_iaq_standard") or FIRST_IAQ_STANDARD
    if second is not None and resolved.get("requires_second_iaq"):
        second["iaq_standard"] = resolved.get("second_iaq_standard") or SECOND_IAQ_STANDARD

    # Expose parameter provenance without changing the adopted scheduling policy.
    cfg_profile = cfg['profiles'].get(resolved['profile'], {})
    for task in (first_task, _anchor('flushout'), second):
        if task is None: continue
        declared = resolved.get('status') == 'CONFIRMED'
        task['duration_provenance'] = dict(
            source_type='user_declared' if declared else 'internal_policy',
            source=resolved.get('basis') if declared else 'config/occupancy_readiness_profiles.json',
            basis=resolved.get('basis') if declared else cfg_profile.get('basis','内部规划参数'),
            confirmation_status='declared_not_independently_verified' if declared else 'pending_project_confirmation',
            regulatory_duration_verified=False)
        task['input_notes'] = str(task.get('input_notes','')) + '\n工期来源：' + str(task['duration_provenance']['basis']) + '；项目适用性待确认，未核实为法定等待天数。'

    # Furniture sequencing is VERIFIED against the template order, never re-written.
    seq = params.get("furniture_sequencing")
    furniture = _anchor("furniture_install")
    sequencing_check = None
    if furniture is not None:
        first_iaq = _find(tasks, anchors["first_iaq"])
        flushout = _find(tasks, anchors["flushout"])
        if seq == "after_first_iaq" and first_iaq is not None:
            sequencing_check = dict(declared=seq,
                                    satisfied=(furniture["id"] > first_iaq["id"]),
                                    note="declared sequencing verified against WBS order; "
                                         "topology never rewritten by the profile")
        elif seq == "after_flushout" and flushout is not None:
            sequencing_check = dict(declared=seq,
                                    satisfied=(furniture["id"] > flushout["id"]),
                                    note="declared sequencing verified against WBS order; "
                                         "topology never rewritten by the profile")
    return dict(parameter_source="internal_policy" if resolved.get("status") != "CONFIRMED" else "user_declared",
                project_confirmation="pending" if resolved.get("status") != "CONFIRMED" else "declared_unverified",
                regulatory_duration_verified=False,
                profile=resolved["profile"], status=resolved.get("status", "CONFIRMED_SELECTION"),
                committed=resolved.get("committed", True),
                assumed=resolved["assumed"],
                basis=resolved["basis"], confidence=resolved["confidence"],
                flushout_duration=resolved.get("flushout_duration"),
                formal_iaq=resolved["formal_iaq"], requires_second_iaq=resolved["requires_second_iaq"],
                certification=resolved["certification"],
                duration_overrides=duration_overrides, folded=folded,
                missing_anchors=missing, conditional_requirements=[],
                furniture_sequencing=sequencing_check)


def occupancy_profile_block(resolved: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    """Serializable basis block for the schedule JSON / reports."""
    return dict(parameter_source="internal_policy" if resolved.get("status") != "CONFIRMED" else "user_declared",
                project_confirmation="pending" if resolved.get("status") != "CONFIRMED" else "declared_unverified",
                regulatory_duration_verified=False,
                profile=resolved["profile"], status=resolved.get("status", "CONFIRMED_SELECTION"),
                committed=resolved.get("committed", True),
                assumed=resolved["assumed"],
                basis=resolved["basis"], confidence=resolved["confidence"],
                parameters=resolved["parameters"],
                flushout_duration=resolved.get("flushout_duration"),
                formal_iaq=resolved["formal_iaq"],
                requires_second_iaq=resolved["requires_second_iaq"],
                certification=resolved["certification"],
                first_iaq_standard=resolved.get("first_iaq_standard") or FIRST_IAQ_STANDARD,
                second_iaq_standard=resolved.get("second_iaq_standard") or SECOND_IAQ_STANDARD,
                duration_overrides=report.get("duration_overrides"),
                folded=[f["task"]["name"] for f in report.get("folded", [])],
                missing_anchors=report.get("missing_anchors"),
                conditional_requirements=report.get("conditional_requirements", []),
                furniture_sequencing=report.get("furniture_sequencing"))
