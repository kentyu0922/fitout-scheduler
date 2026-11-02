# -*- coding: utf-8 -*-
"""Research-first programme intelligence: classify, disclose baseline, then scale.

Pipeline: sequence → baseline at reference area → classify scale/lock → scaling law → CPM.
Duration authority is ENGINE_AUTHORITY. Client intake does not lock b0.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from core.l2_master_duration import (
    AUTHORITY, _is_gov_duration_lock, _is_timed_site_test, _is_construction_phase_task,
    _is_not_area_scaled, load_l2_duration_policy,
)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "config", "programme_intelligence.json")
_CFG: Optional[Dict[str, Any]] = None


def load_intelligence_policy(path: Optional[str] = None) -> Dict[str, Any]:
    global _CFG
    cfg_path = path or CONFIG_PATH
    if path is None and _CFG is not None:
        return _CFG
    with open(cfg_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if path is None:
        _CFG = data
    return data


def classify_lane(task: Dict[str, Any], catalog: Dict[str, Any]) -> str:
    if task.get("milestone") or int(task.get("duration_days") or task.get("duration") or 0) <= 0:
        return "NOT_SITE"
    if _is_gov_duration_lock(task):
        return "STATUTORY_LOCK"
    if _is_timed_site_test(task):
        return "TIMED_TEST"
    if _is_not_area_scaled(task):
        return "NOT_SITE"
    atype = task.get("activity_type")
    if atype and atype in catalog:
        return "SCALE_RESEARCHED"
    if _is_construction_phase_task(task):
        return "SCALE_TEMPLATE"
    return "NOT_SITE"


def build_programme_judgement(tasks: List[Dict[str, Any]], *, area_sqm: float,
                              intel: Optional[Dict[str, Any]] = None,
                              l2_policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    intel = intel or load_intelligence_policy()
    l2_policy = l2_policy or load_l2_duration_policy()
    catalog = l2_policy.get("activity_types") or {}
    ref = float(intel.get("reference_area_sqm") or l2_policy.get("plate_ref_m2") or 3000)
    exponent = float(intel.get("area_exponent") or l2_policy.get("area_exponent") or 0.4)
    counts: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []
    for t in tasks:
        if int(t.get("outline_level") or t.get("level") or 3) <= 2 and not t.get("activity_type"):
            if int(t.get("duration_days") or 0) <= 0:
                continue
        lane = classify_lane(t, catalog)
        counts[lane] = counts.get(lane, 0) + 1
        l2 = t.get("l2_duration") or {}
        rows.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "lane": lane,
            "activity_type": t.get("activity_type"),
            "package_id": l2.get("package_id"),
            "reference_area_sqm": ref,
            "b0_days": l2.get("b0_days") or l2.get("template_b0_days"),
            "scaled_days": t.get("duration_days"),
            "duration_authority": t.get("duration_authority"),
            "area_exponent": exponent if lane.startswith("SCALE") else None,
        })
    return {
        "pipeline": intel.get("pipeline"),
        "research_status": intel.get("research_status"),
        "reference_area_sqm": ref,
        "project_area_sqm": float(area_sqm),
        "area_exponent": exponent,
        "construction_package_sequence": intel.get("construction_package_sequence"),
        "lane_counts": counts,
        "note": intel.get("note"),
        "tasks": rows,
    }
