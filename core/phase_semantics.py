"""P1-08 Phase 6 semantics: site construction vs occupancy readiness.

The template's Phase 6 header "实体施工阶段" spans physical construction AND
the post-completion occupancy tail (first IAQ, furniture, flush-out, second
IAQ), which made a 450sqm project read as ~141 days of "construction".

This module classifies every task into a DECLARED semantic category
(config/phase_semantics.json) and computes per-category spans. The
site-construction span is anchor-driven (Site Takeover → 物理竣工), so it can
only ever cover true physical construction scope. Everything not matched by a
declared rule outside the anchor window stays unclassified (None) — never
guessed into a category.
"""
import json
import os
from datetime import date
from typing import Any, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_BASE, "config", "phase_semantics.json")

_cache: Optional[Dict[str, Any]] = None


class PhaseSemanticsError(ValueError):
    """Raised when declared anchors are missing from the task list."""


def load_phase_semantics() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def _find(tasks: List[Dict[str, Any]], needle: str) -> Optional[Dict[str, Any]]:
    for t in tasks:
        if needle in str(t.get("name", "")):
            return t
    return None


def _parse_date(value: str) -> Optional[date]:
    try:
        y, m, d = str(value).split("-")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def _level(task: Dict[str, Any]) -> int:
    return int(task.get("outline_level") or 3)


def _is_summary(tasks: List[Dict[str, Any]], i: int) -> bool:
    """Same rule as the optimizer/solver: summary = level <= 2 or parent of the next task."""
    lvl = _level(tasks[i])
    if lvl <= 2:
        return True
    return i + 1 < len(tasks) and _level(tasks[i + 1]) > lvl


def classify_semantic_phases(tasks: List[Dict[str, Any]]) -> Dict[int, Optional[str]]:
    """Per-task semantic category. Only DECLARED rules assign categories.

    Tail-category keywords (furniture/IAQ/testing/handover) apply only to tasks
    positioned AFTER the physical-completion anchor: furniture PROCUREMENT
    (Phase 5 vendor award / production lead) and construction-phase T&C are
    different concepts from the occupancy tail and must not inflate its span.
    Summary headers (Stage/Phase groupings) are never classified and never
    count toward spans — only real leaf work does.
    """
    cfg = load_phase_semantics()
    cats = cfg["semantic_categories"]
    order = cfg.get("classification_order",
                    ["FURNITURE_INSTALLATION", "IAQ_FLUSHOUT", "TESTING_COMMISSIONING",
                     "HANDOVER_MOVE", "SITE_CONSTRUCTION"])

    start_anchor = _find(tasks, cfg["anchors"]["site_construction_start"]["task_name_contains"])
    end_anchor = _find(tasks, cfg["anchors"]["physical_completion"]["task_name_contains"])
    if start_anchor is None or end_anchor is None:
        raise PhaseSemanticsError(
            f"site construction anchors missing: start={start_anchor is not None}, "
            f"physical_completion={end_anchor is not None}")
    positions = {t["id"]: i for i, t in enumerate(tasks)}
    lo = min(positions[start_anchor["id"]], positions[end_anchor["id"]])
    hi = max(positions[start_anchor["id"]], positions[end_anchor["id"]])
    tail_from = hi  # tail categories only after physical completion
    # Chronology, not list position: a task starting on/after physical completion is tail work
    # even when the template lists it before the anchor (furniture receipt/installation).
    completion = _parse_date(end_anchor.get("finish"))

    out: Dict[int, Optional[str]] = {}
    for i, t in enumerate(tasks):
        if _is_summary(tasks, i):
            out[t["id"]] = None  # summary headers never carry a category
            continue
        name = str(t.get("name", ""))
        pos = positions.get(t["id"], -1)
        assigned: Optional[str] = None
        for cat in order:
            spec = cats.get(cat, {})
            kws = spec.get("task_name_contains_any") or []
            started = _parse_date(t.get("start"))
            after = pos > tail_from or bool(completion and started and started >= completion)
            if kws and after and any(k in name for k in kws):
                assigned = cat
                break
            if spec.get("fallback_between_anchors") and lo <= pos <= hi:
                assigned = cat
                break
        out[t["id"]] = assigned
    # The two anchors themselves are always SITE_CONSTRUCTION (span endpoints).
    out[start_anchor["id"]] = "SITE_CONSTRUCTION"
    out[end_anchor["id"]] = "SITE_CONSTRUCTION"
    return out


def semantic_phase_spans(tasks: List[Dict[str, Any]],
                         classification: Optional[Dict[int, Optional[str]]] = None) -> Dict[str, Any]:
    """Category spans + the executive site_construction_days reading.

    site_construction is anchor-driven: start of Site Takeover to finish of
    物理竣工. Category spans are min(start)..max(finish) over member tasks.
    """
    cfg = load_phase_semantics()
    classification = classification or classify_semantic_phases(tasks)
    start_anchor = _find(tasks, cfg["anchors"]["site_construction_start"]["task_name_contains"])
    end_anchor = _find(tasks, cfg["anchors"]["physical_completion"]["task_name_contains"])

    members: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in cfg["semantic_categories"]}
    unclassified = []
    for t in tasks:
        cat = classification.get(t["id"])
        if cat:
            members[cat].append(t)
        else:
            unclassified.append(dict(id=t["id"], name=t.get("name", "")))

    def _span(ts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        dated = [_parse_date(t.get("start")) for t in ts if _parse_date(t.get("start"))]
        dated_f = [_parse_date(t.get("finish")) for t in ts if _parse_date(t.get("finish"))]
        if not dated or not dated_f:
            return None
        s, f = min(dated), max(dated_f)
        return dict(start=s.isoformat(), finish=f.isoformat(), calendar_days=(f - s).days,
                    task_count=len(ts))

    spans = {cat: _span(ts) for cat, ts in members.items()}
    # Executive reading: true construction span only. Anchor-driven, so the
    # occupancy tail (furniture/IAQ/handover) can never inflate it.
    a, b = _parse_date(start_anchor.get("start")), _parse_date(end_anchor.get("finish"))
    site_construction_days = (b - a).days if a and b else None
    return dict(
        version=cfg.get("version"),
        site_construction=dict(
            start=start_anchor.get("start"), finish=end_anchor.get("finish"),
            calendar_days=site_construction_days,
            anchors=dict(site_construction_start=start_anchor["name"],
                         physical_completion=end_anchor["name"]),
            definition=cfg["semantic_categories"]["SITE_CONSTRUCTION"]["span"]),
        spans=spans,
        unclassified=unclassified,
        categories={cat: spec["description"] for cat, spec in cfg["semantic_categories"].items()})


def phase_semantics_block(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Serializable block: per-task labels + spans + executive reading."""
    classification = classify_semantic_phases(tasks)
    for t in tasks:
        t["semantic_phase"] = classification.get(t["id"])
    return semantic_phase_spans(tasks, classification)
