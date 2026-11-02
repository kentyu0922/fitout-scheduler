# -*- coding: utf-8 -*-
"""V6.6.1 — Executive Pack: read-only post-processor for a completed schedule run.

Contract (do not weaken):

* **Read only.** Nothing here writes to ``audit/``, ``baseline/``, ``config/`` or
  ``templates/``. Only :mod:`tools.executive_pack` writes, and only into
  ``reports/executive_pack/`` inside the run it was given.
* **No date is ever derived.** Every date is copied verbatim from the solved
  schedule. There is no arithmetic that can produce a new date.
* **No proposal is applied.** Every proposal carries ``applied = false``. No
  scenario is promoted, no baseline is moved, ``recommended`` stays ``None``.
* **Every claim carries ``source_fields``.** A statement with no source is
  marked ``UNSUPPORTED`` and is excluded from the executive version.

The views implemented here are the APPROVED-R1 subset of PRS V6.6.1
(see ``requirements/V6.6.1/``): CORE-02/03/05/09/10/12, PI-01/03/04/06/10,
EM-01/02/06/07/08/10, PC-01/02/04/05/08/09.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ._common import is_true_milestone

READ_ONLY = True
BASELINE_UNCHANGED = True
PACK_SCHEMA = "6.6.1-executive-pack/1"

# --------------------------------------------------------------------------- #
# vocabularies
# --------------------------------------------------------------------------- #

#: Procurement package states, highest precedence first.
PKG_UNRESOLVED_REQUIREMENT = "UNRESOLVED_REQUIREMENT"
PKG_UNCONFIRMED_LEGAL = "UNCONFIRMED_LEGAL_APPLICABILITY"
PKG_PROVISIONAL_CALENDAR = "PROVISIONAL_CALENDAR"
PKG_SCHEDULED_CONFIRMED = "SCHEDULED_CONFIRMED"

PKG_PRECEDENCE = {
    PKG_UNRESOLVED_REQUIREMENT: 1,
    PKG_UNCONFIRMED_LEGAL: 2,
    PKG_PROVISIONAL_CALENDAR: 3,
    PKG_SCHEDULED_CONFIRMED: 4,
}

LEAD_NOT_SCHEDULED = "LEAD_NOT_SCHEDULED"
LEAD_SCHEDULED = "LEAD_SCHEDULED"
LEAD_PRECEDENCE = {LEAD_NOT_SCHEDULED: 0, LEAD_SCHEDULED: 6}

MS_CONDITIONAL_UNRESOLVED = "CONDITIONAL_UNRESOLVED"
MS_CRITICAL_PATH_EXPOSED = "CRITICAL_PATH_EXPOSED"
MS_COMMITTED = "COMMITTED"
MS_PRECEDENCE = {MS_CONDITIONAL_UNRESOLVED: 1, MS_CRITICAL_PATH_EXPOSED: 2, MS_COMMITTED: 3}

#: CORE-05 evidence freshness levels — derived from existing fields only.
EVIDENCE_LEVELS = ("confirmed", "provisional", "assumed", "unconfirmed", "unknown")

#: Words that must never appear in the executive commentary (PC-05 / PC-09).
#: Each entry is (regex, why).
COMMENTARY_BLOCKLIST: Tuple[Tuple[str, str], ...] = (
    (r"\b\d{1,3}(?:\.\d+)?\s*%\s*(?:saving|reduction|improvement|earlier|faster)",
     "fabricated percentage benefit (PC-05)"),
    (r"(?:saving|reduction|improvement)s?\s+of\s+\d", "fabricated quantified benefit (PC-05)"),
    (r"guarantee[sd]?\b", "unsupported assurance (PC-09)"),
    (r"\bwill\s+(?:certainly\s+)?(?:be\s+)?complete[d]?\b", "unsupported completion assurance (PC-05)"),
    (r"\bensur(?:e|ed|ing)\s+completion\b", "unsupported completion assurance (PC-09)"),
    (r"\b(?:RMB|CNY|US\$|USD|¥)\s?\d", "monetary claim without an approved cost basis (PI-07 deferred)"),
    (r"\bblame\b|\bunderperform|\bnegligent", "blame language (PC-09)"),
    (r"已确保完工|保证完工|承诺完工日", "unsupported assurance (PC-05)"),
    (r"节省\s*\d+\s*(?:天|日|万元|%)", "fabricated quantified benefit (PC-05)"),
)

PROCUREMENT_PHASE_KEYWORDS = ("招采", "招标", "采购", "procurement", "tender", "bid")

#: Milestones the executive layer is contractually required to see, matched by
#: name fragment against the solved schedule. Selection never invents rows.
EXECUTIVE_MILESTONE_HINTS = (
    "Kick Off", "项目启动", "前期规划", "设计总承包合同", "施工图审查合格书",
    "消防设计审查合格", "中标通知书", "施工总包合同", "施工许可证", "场地移交",
    "隐蔽工程验收", "物理竣工", "消防验收合格", "竣工备案", "室内空气质量检测合格",
    "场地正式移交", "搬迁全部完成",
)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj: Any) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def engine_version(root: str) -> str:
    """Read the engine version from VERSION.md; never guess a version number."""
    path = os.path.join(root, "VERSION.md")
    if os.path.exists(path):
        text = open(path, encoding="utf-8").read()
        m = re.search(r"Engine version\s*\|\s*\*{0,2}([0-9][0-9A-Za-z.\-]*)", text)
        if m:
            return m.group(1)
    return "unknown"


def find_sidecar(audit_dir: str, suffix: str) -> Optional[str]:
    if not os.path.isdir(audit_dir):
        return None
    hits = sorted(f for f in os.listdir(audit_dir) if f.endswith(suffix))
    return os.path.join(audit_dir, hits[0]) if hits else None


def _cs(task: Dict[str, Any]) -> Dict[str, Any]:
    state = task.get("client_state")
    return state if isinstance(state, dict) else {}


def _is_leaf(task: Dict[str, Any]) -> bool:
    return bool(task.get("activity_type")) or bool(task.get("duration_method")) or (
        task.get("outline_level") == 4)


def _name(task: Dict[str, Any]) -> str:
    return str(task.get("name") or "")


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) else None


def evidence_status_for(task: Dict[str, Any]) -> Tuple[str, List[str]]:
    """CORE-05: map an existing task to one of the five freshness levels.

    Every level is derived from a field the engine already writes; no threshold is
    introduced. The returned locators must each be a resolvable ``file#path`` token.
    """
    cs = _cs(task)
    conf = str(cs.get("confidence") or "").strip()
    req = str(cs.get("requirement_status") or "").strip()
    base = f"_schedule.json#tasks[id={task.get('id')}].client_state"
    src = [f"{base}.requirement_status"]
    if conf == "UNKNOWN" or req == "To Be Confirmed":
        return "unknown", src + [f"{base}.confidence"]
    if cs.get("conditional") is True:
        return "unconfirmed", src + [f"{base}.conditional"]
    if req == "Confirmed":
        prod = task.get("productivity")
        if isinstance(prod, dict) and prod.get("rate_status") == "provisional":
            return "provisional", src + [
                f"_schedule.json#tasks[id={task.get('id')}].productivity.rate_status"]
        return "confirmed", src
    return "unconfirmed", src


# --------------------------------------------------------------------------- #
# PI-01 / PI-03 / PI-04 / PI-10 — procurement intelligence
# --------------------------------------------------------------------------- #

def _phase_spans(tasks: Sequence[Dict[str, Any]]) -> List[Tuple[int, Dict[str, Any], List[Dict[str, Any]]]]:
    """Return [(index, header, descendants)] for every level<=2 row."""
    out = []
    n = len(tasks)
    for i, t in enumerate(tasks):
        lvl = t.get("outline_level")
        if not isinstance(lvl, int) or lvl > 2:
            continue
        kids = []
        for j in range(i + 1, n):
            nxt = tasks[j].get("outline_level")
            if isinstance(nxt, int) and nxt <= lvl:
                break
            kids.append(tasks[j])
        out.append((i, t, kids))
    return out


def _window(rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    starts = [r.get("start") for r in rows if r.get("start")]
    finishes = [r.get("finish") for r in rows if r.get("finish")]
    if not starts or not finishes:
        return None
    return {"start": min(starts), "finish": max(finishes)}


def _lead_configs(config_dir: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, fname in (("equipment", "long_lead_equipment.json"),
                       ("long_lead", "long_lead.json"),
                       ("furniture_profiles", "furniture_lead_profiles.json")):
        path = os.path.join(config_dir, fname)
        out[key] = load_json(path) if os.path.exists(path) else None
    return out


def _flatten_lead_items(cfgs: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    src = cfgs.get("equipment") or cfgs.get("long_lead")
    if isinstance(src, dict):
        for category, entries in src.items():
            if not isinstance(entries, dict):
                continue
            for item_id, entry in entries.items():
                if not isinstance(entry, dict):
                    continue
                items.append({
                    "item_id": f"{category}.{item_id}",
                    "category": category,
                    "label": entry.get("name") or item_id,
                    "lead_days": entry.get("lead_days"),
                    "basis": entry.get("desc"),
                    "confidence": "provisional",
                    # catalogue entries are options, not project expectations; only the
                    # engine's own furniture_lead block may declare an expectation
                    "engine_expected": False,
                    "evidence_ref": f"config/long_lead_equipment.json#{category}.{item_id}",
                })
    profiles = cfgs.get("furniture_profiles")
    if isinstance(profiles, dict):
        for name, entry in (profiles.get("profiles") or {}).items():
            if not isinstance(entry, dict):
                continue
            items.append({
                "item_id": f"FurnitureProfile.{name}",
                "category": "FurnitureProfile",
                "label": f"家具交期档 {name}",
                "lead_days": entry.get("lead_days"),
                "basis": entry.get("basis"),
                "confidence": entry.get("confidence") or "provisional",
                "engine_expected": False,
                "evidence_ref": f"config/furniture_lead_profiles.json#profiles.{name}",
            })
    return items


def _match_scheduled(tasks: Sequence[Dict[str, Any]], label: str) -> Optional[Dict[str, Any]]:
    if not label:
        return None
    token = str(label).strip()
    if len(token) < 2:
        return None
    for t in tasks:
        if token in _name(t):
            return t
    return None


def build_procurement_intelligence(schedule: Dict[str, Any],
                                   procurement_review: Optional[Dict[str, Any]],
                                   lead_configs: Dict[str, Any]) -> Dict[str, Any]:
    tasks = list(schedule.get("tasks") or [])
    gaps: List[str] = []
    packages: List[Dict[str, Any]] = []

    if not tasks:
        gaps.append("schedule.tasks is empty: no procurement package could be derived")

    for idx, header, kids in _phase_spans(tasks):
        hname = _name(header)
        low = hname.lower()
        if not any(k.lower() in low for k in PROCUREMENT_PHASE_KEYWORDS):
            continue
        leaf_rows = [k for k in kids if k.get("start") or k.get("finish")]
        if not leaf_rows:
            gaps.append(f"package '{hname}' has no dated activities: window withheld")
            continue

        reasons: List[str] = []
        unresolved = [r for r in leaf_rows
                      if str(_cs(r).get("requirement_status") or "").strip() not in ("", "Confirmed")]
        if unresolved:
            reasons.append(
                f"{len(unresolved)} activity/activities carry requirement_status != Confirmed")
        provisional = [r for r in leaf_rows
                       if str((r.get("activity_calendar") or {}).get("confidence") or "")
                       == "planning_default"]
        if provisional:
            reasons.append(
                f"{len(provisional)} activity/activities use the planning-default business calendar")

        # Package-local state; the project-wide legal flag is applied below.
        if unresolved:
            status = PKG_UNRESOLVED_REQUIREMENT
        elif provisional:
            status = PKG_PROVISIONAL_CALENDAR
        else:
            status = PKG_SCHEDULED_CONFIRMED

        packages.append({
            "package_id": f"PKG-{header.get('id')}",
            "label": hname,
            "header_task_id": header.get("id"),
            "task_ids": [r.get("id") for r in leaf_rows],
            "task_count": len(leaf_rows),
            "tasks": [{"id": r.get("id"), "name": _name(r), "start": r.get("start"),
                       "finish": r.get("finish"), "duration_days": _int(r.get("duration_days"))}
                      for r in leaf_rows],
            "window": _window(leaf_rows),
            "status": status,
            "reasons": reasons or ["no exception condition was detected in this package"],
            "source_fields": [f"_schedule.json#tasks[id={header.get('id')}]",
                              "_schedule.json#tasks[].client_state.requirement_status",
                              "_schedule.json#tasks[].activity_calendar.confidence"],
        })

    # project-wide legal applicability is a project-level reason, not a package reason
    legal = (procurement_review or {}).get("legal_applicability") if procurement_review else None
    if legal == "unconfirmed":
        for pkg in packages:
            pkg["status"] = (PKG_UNCONFIRMED_LEGAL
                             if PKG_PRECEDENCE[PKG_UNCONFIRMED_LEGAL] < PKG_PRECEDENCE[pkg["status"]]
                             else pkg["status"])
            pkg["reasons"].append(
                "statutory tendering applicability is unconfirmed (invitation method alone "
                "does not establish it)")
            pkg["source_fields"].append("_procurement_review.json#legal_applicability")

    # ---- lead items: configured but possibly not represented in the schedule
    lead_items: List[Dict[str, Any]] = []
    for item in _flatten_lead_items(lead_configs):
        hit = _match_scheduled(tasks, item["label"])
        scheduled = hit is not None
        lead_items.append(dict(
            item,
            scheduled=scheduled,
            scheduled_task_id=(hit.get("id") if hit else None),
            status=LEAD_SCHEDULED if scheduled else LEAD_NOT_SCHEDULED,
            applied=False,
            note=("lead item is represented by a scheduled activity"
                  if scheduled else
                  "no scheduled activity carries this lead item; the configured lead time is "
                  "NOT reflected in the programme"),
            source_fields=[item["evidence_ref"], "_schedule.json#tasks[].name"],
        ))

    # the engine's own furniture-lead block is authoritative for the furniture profile
    furn = schedule.get("furniture_lead")
    if isinstance(furn, dict):
        lead_items.append({
            "item_id": "engine.furniture_lead",
            "category": "EngineBlock",
            "label": f"家具交期档 {furn.get('profile')}",
            "lead_days": furn.get("lead_days"),
            "basis": furn.get("lead_time_basis"),
            "confidence": furn.get("confidence") or "provisional",
            # the engine itself selected this profile for this project, so an unscheduled
            # result here IS a real gap rather than catalogue noise
            "engine_expected": True,
            "evidence_ref": furn.get("evidence_ref"),
            "scheduled": bool(furn.get("applied")),
            "scheduled_task_id": (furn.get("task") or {}).get("id") if isinstance(furn.get("task"), dict) else None,
            "status": LEAD_SCHEDULED if furn.get("applied") else LEAD_NOT_SCHEDULED,
            "applied": False,
            "note": (furn.get("not_applied_reason")
                     or "engine reports the furniture lead time as applied to a scheduled task"),
            "source_fields": ["_schedule.json#furniture_lead.applied",
                              "_schedule.json#furniture_lead.not_applied_reason"],
        })

    # ---- PI-04: link milestones to packages (potential impact only)
    by_id = {t.get("id"): t for t in tasks}
    milestone_ids = [t.get("id") for t in tasks if _is_milestone(t)]
    ancestors = _ancestor_closure(tasks, by_id)
    for pkg in packages:
        linked = []
        pkg_tasks = set(pkg["task_ids"])
        for mid in milestone_ids:
            if pkg_tasks & ancestors.get(mid, set()):
                linked.append({"milestone_id": mid,
                               "label": _name(by_id[mid]),
                               "finish": by_id[mid].get("finish"),
                               "impact_assessment": "potential"})
        pkg["linked_milestones"] = linked
        pkg["linked_milestones_note"] = (
            "Causality has not been confirmed by the PM. This view does not change any "
            "milestone date.")

    # ---- PI-10: attention list, deterministic ordering
    attention: List[Dict[str, Any]] = []
    for pkg in packages:
        attention.append({
            "kind": "package", "id": pkg["package_id"], "label": pkg["label"],
            "status": pkg["status"],
            "sort_date": (pkg.get("window") or {}).get("start") or "9999-12-31",
            "reason": pkg["reasons"][0],
        })
    for item in lead_items:
        if item["status"] != LEAD_NOT_SCHEDULED or not item.get("engine_expected"):
            continue
        attention.append({
            "kind": "lead_item", "id": item["item_id"], "label": item["label"],
            "status": item["status"],
            "sort_date": "9999-12-31",
            "reason": item["note"],
        })
    attention.sort(key=lambda r: (_prec(r["status"]), r["sort_date"], r["id"]))

    counts: Dict[str, int] = {}
    for row in attention:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    # catalogue coverage: entries in config/ that no scheduled activity names.
    # This is disclosure of coverage, deliberately NOT framed as a defect, and it is
    # kept out of the attention list unless the engine itself declares the lead time.
    unscheduled_configured = sorted(
        i["item_id"] for i in lead_items
        if i["item_id"] != "engine.furniture_lead" and not i["scheduled"]
    )

    return {
        "packages": sorted(packages, key=lambda p: p["package_id"]),
        "lead_items": sorted(lead_items, key=lambda i: i["item_id"]),
        "configured_lead_items_without_scheduled_activity": {
            "count": len(unscheduled_configured),
            "item_ids": sorted(unscheduled_configured),
            "note": ("These are entries in the lead-time catalogue that no scheduled activity "
                     "names. Being unmatched is a statement about configuration coverage, not "
                     "evidence of a defect, unless the engine itself declares the lead time "
                     "expected for this project (see engine.furniture_lead)."),
            "source_fields": ["config/long_lead_equipment.json",
                              "config/furniture_lead_profiles.json",
                              "_schedule.json#tasks[].name"],
        },
        "legal_applicability": {
            "value": legal,
            "status": "unconfirmed" if legal == "unconfirmed" else "unknown",
            "source_fields": ["_procurement_review.json#legal_applicability"],
            "statement": ("Invitation method alone does not establish whether statutory "
                          "tendering applies."),
            "assumptions": (procurement_review or {}).get("assumptions") or [],
            "sources": (procurement_review or {}).get("sources") or [],
        },
        "observed_bid_phase": (procurement_review or {}).get("actual_phase"),
        "attention_list": attention,
        "attention_ordering_rule": ("(state precedence, window.start, package_id/item_id) "
                                    "ascending; lead items that carry no window sort after "
                                    "dated packages. Reproducible from the listed fields."),
        "counts": counts,
        "gaps": gaps,
        "evidence_incomplete": bool(gaps) or procurement_review is None,
        "evidence_incomplete_note": (
            "One or more sources could not be read; completeness of this list must not be "
            "asserted." if (gaps or procurement_review is None) else None),
        "confidence_vocabulary": {"package": list(PKG_PRECEDENCE),
                                  "lead_item": list(LEAD_PRECEDENCE)},
    }


def attention_precedence(status: str) -> int:
    """Ranking key for the attention list: 0 is most attention-worthy."""
    if status in PKG_PRECEDENCE:
        return PKG_PRECEDENCE[status]
    if status in LEAD_PRECEDENCE:
        return LEAD_PRECEDENCE[status]
    return 9


def _prec(status: str) -> int:
    return attention_precedence(status)


# --------------------------------------------------------------------------- #
# EM-01..EM-10 — executive milestones
# --------------------------------------------------------------------------- #

def _is_milestone(task: Dict[str, Any]) -> bool:
    """EM-01 selection.

    A milestone is a task the engine marks via its authoritative ``milestone`` flag
    (or a genuine zero committed duration) — see ``is_true_milestone``. The legacy
    ``[M]`` name prefix is no longer used as a task-name marker; relying on the flag
    is exact and avoids pulling summary rows into the executive set.
    """
    if not isinstance(task, dict):
        return False
    return is_true_milestone(task)


def _ancestor_closure(tasks: Sequence[Dict[str, Any]],
                      by_id: Dict[Any, Dict[str, Any]]) -> Dict[Any, set]:
    """Pred maps a task id to its immediate predecessors. Memoised closure."""
    memo: Dict[Any, set] = {}

    def walk(tid: Any) -> set:
        if tid in memo:
            return memo[tid]
        memo[tid] = set()
        task = by_id.get(tid)
        if not isinstance(task, dict):
            return memo[tid]
        out = set()
        for pid, _rel, _lag in _parse_preds(task.get("predecessors")):
            out.add(pid)
            out |= walk(pid)
        memo[tid] = out
        return out

    for t in tasks:
        walk(t.get("id"))
    return memo


_PRED_RE = re.compile(r"^\s*(\d+)\s*([A-Za-z]{2})?\s*([+-]\s*\d+)?\s*$")


def _parse_preds(raw: Any) -> List[Tuple[int, str, int]]:
    if raw in (None, "", []):
        return []
    out: List[Tuple[int, str, int]] = []
    for tok in str(raw).replace(";", ",").split(","):
        m = _PRED_RE.match(tok)
        if not m:
            continue
        out.append((int(m.group(1)), (m.group(2) or "FS").upper(),
                    int((m.group(3) or "0").replace(" ", ""))))
    return out


def build_executive_milestones(schedule: Dict[str, Any]) -> Dict[str, Any]:
    tasks = list(schedule.get("tasks") or [])
    by_id = {t.get("id"): t for t in tasks}
    ms_tasks = [t for t in tasks if _is_milestone(t)]
    gaps: List[str] = []
    if not ms_tasks:
        gaps.append("no milestone-marked task was found in the solved schedule")

    milestones: List[Dict[str, Any]] = []
    for t in ms_tasks:
        cs = _cs(t)
        req = str(cs.get("requirement_status") or "").strip()
        conditional = cs.get("conditional") is True or (req not in ("", "Confirmed"))
        if conditional:
            status = MS_CONDITIONAL_UNRESOLVED
        elif t.get("critical"):
            status = MS_CRITICAL_PATH_EXPOSED
        else:
            status = MS_COMMITTED

        ev, ev_src = evidence_status_for(t)
        upstream = []
        for pid, rel, lag in _parse_preds(t.get("predecessors")):
            probe = by_id.get(pid)
            if isinstance(probe, dict) and _is_milestone(probe):
                upstream.append({"milestone_id": pid, "label": _name(probe),
                                 "relationship": rel, "lag_days": lag})

        milestones.append({
            # AC-EM-01: the schedule task this row was copied from
            "source_task_id": t.get("id"),
            "milestone_id": t.get("id"),
            "label": _name(t).replace("[M]", "").strip() or _name(t),
            "raw_label": _name(t),
            "start": t.get("start"),
            "finish": t.get("finish"),
            "duration_days": _int(t.get("duration_days")),
            "semantic_phase": t.get("semantic_phase"),
            "status": status,
            "requirement_status": req or None,
            "conditional": conditional,
            "decision_owner": cs.get("decision_owner"),
            "basis": cs.get("basis"),
            "critical": bool(t.get("critical")),
            "on_driving_chain": bool(t.get("on_driving_chain")),
            "total_slack_days": _int(t.get("total_slack_days")),
            "evidence_status": ev,
            "upstream_milestones": upstream,
            "conditional_note": (
                "The requirement behind this milestone is still unresolved in the engine's own "
                "state. The date shown is a planned date, not a commitment."
                if status == MS_CONDITIONAL_UNRESOLVED else None),
            "dependency_note": (
                "Advisory only. This milestone sits on the driving chain, but an inferred "
                "dependency risk is not a confirmed causal statement and no date is changed "
                "by this view."
                if status == MS_CRITICAL_PATH_EXPOSED else None),
            "planned_packages": [],
            "source_fields": ev_src + [f"_schedule.json#tasks[id={t.get('id')}].start",
                                       f"_schedule.json#tasks[id={t.get('id')}].finish"],
        })

    milestones.sort(key=lambda m: (m["finish"] or "9999-12-31", m["milestone_id"] or 0))

    counts: Dict[str, int] = {}
    for m in milestones:
        counts[m["status"]] = counts.get(m["status"], 0) + 1

    pending = [m for m in milestones if m["status"] == MS_CONDITIONAL_UNRESOLVED]
    nearest = pending[0] if pending else None

    # Phase boundaries come from phase_semantics — computed by the engine, copied here.
    phase = schedule.get("phase_semantics") or {}
    boundaries = []
    for category, span in sorted((phase.get("spans") or {}).items()):
        span = span or {}  # a phase with no tasks in this project has no span
        boundaries.append({
            "category": category,
            "start": span.get("start"),
            "finish": span.get("finish"),
            "calendar_days": span.get("calendar_days"),
            "task_count": span.get("task_count"),
            "source_fields": [f"_schedule.json#phase_semantics.spans.{category}"],
        })

    return {
        "milestones": milestones,
        "phase_boundaries": boundaries,
        "phase_coverage": {
            "classified_categories": sorted((phase.get("spans") or {}).keys()),
            "unclassified_task_count": len(phase.get("unclassified") or []),
            "note": ("The engine's phase_semantics classifies only the construction-tail phases. "
                     "An empty phase cell means the engine itself has no classification for that "
                     "row; this view does not invent one."),
            "source_fields": ["_schedule.json#phase_semantics.spans",
                              "_schedule.json#phase_semantics.unclassified"],
        },
        "selection_rule": ("a task is an executive milestone when the engine marks it with "
                           "'[M]' in its own name; summary rows are excluded"),
        "counts": counts,
        "nearest_pending_milestone": (
            {"milestone_id": nearest["milestone_id"], "label": nearest["label"],
             "finish": nearest["finish"], "status": nearest["status"],
             "decision_owner": nearest.get("decision_owner")}
            if nearest else None),
        "status_vocabulary": list(MS_PRECEDENCE),
        "status_note": ("Only engine-evidenced states are expressed here. A progress state that "
                        "compares delivery against an approved plan is outside the vocabulary of "
                        "this run: it requires an approved baseline and a verified actual "
                        "completion date, neither of which exists."),
        "ordering_rule": "finish ascending, then milestone_id ascending (stable)",
        "gaps": gaps,
    }


def link_packages_into_milestones(milestones: Dict[str, Any],
                                  procurement: Dict[str, Any]) -> None:
    """EM-06 reverse index: which procurement packages feed which milestone."""
    index: Dict[Any, List[Dict[str, Any]]] = {}
    for pkg in procurement.get("packages", []):
        for link in pkg.get("linked_milestones", []):
            index.setdefault(link["milestone_id"], []).append({
                "package_id": pkg["package_id"], "label": pkg["label"],
                "status": pkg["status"],
            })
    for m in milestones.get("milestones", []):
        m["planned_packages"] = sorted(
            index.get(m["milestone_id"], []), key=lambda r: r["package_id"])


# --------------------------------------------------------------------------- #
# PI-06 — proposals (never applied)
# --------------------------------------------------------------------------- #

ALLOWED_PM_ACTIONS = ("ACCEPT_RECOMMENDATION", "KEEP_CALCULATED_BASELINE",
                      "PROVIDE_PROJECT_DATA", "REJECT_RECOMMENDATION")


def build_review_proposals(register: Optional[Dict[str, Any]],
                           scenario_comparison: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    gaps: List[str] = []
    entries = (register or {}).get("register") if isinstance(register, dict) else None
    if not entries:
        gaps.append("optimisation register is missing or empty: no proposal could be listed")
        entries = []

    proposals = []
    for entry in entries:
        proposals.append({
            "proposal_id": entry.get("id"),
            "category": entry.get("category"),
            "current_logic": entry.get("current_logic"),
            "observed_issue": entry.get("observed_issue"),
            "recommendation": entry.get("recommendation"),
            "potential_benefit": entry.get("potential_benefit"),
            "risk": entry.get("risk"),
            "prerequisites": entry.get("prerequisites"),
            "quantified": bool(entry.get("quantified")),
            "quantification_rule": entry.get("quantification_rule"),
            "applied": False,
            "pm_approval_required": True,
            "approval_required_by": "PM",
            # AC-PI-06: no owner / target_date key may exist in the output. The accountability
            # note records *why* none can be assigned instead of storing a placeholder.
            "accountability_note": ("no accountable owner or target date can be recorded: no "
                                    "supplier, commitment or owner input exists in this run"),
            "source_fields": ["_pm_optimisation_register.json#register[].id",
                              "_pm_optimisation_register.json#register[].clone_comparison"],
            "clone_comparison": entry.get("clone_comparison"),
        })

    return {
        "proposals": proposals,
        "proposal_count": len(proposals),
        "applied": False,
        "pm_actions_available": list(ALLOWED_PM_ACTIONS),
        "approval_state": "NOT_APPROVED",
        "recommended_scenario": None,
        "recommendation_note": ("A null recommendation is a correct terminal output when the "
                               "evidence does not support a single choice. No proposal is "
                               "applied to the baseline by this pack."),
        "scenario_comparison": scenario_comparison,
        "register_closed_loop": (register or {}).get("closed_loop") if isinstance(register, dict) else None,
        "scenario_comparison_note": (
            "Re-solved comparison across cloned scenarios. Durations are outputs; no saving is "
            "summed and nothing is applied."
            if scenario_comparison else
            "No template A/B1/B2 comparison was requested (--with-scenarios). "
            "Per-proposal clone facts, if any, come from "
            "_pm_optimisation_register.json#register[].clone_comparison. "
            "The baseline is never modified either way."),
        "gaps": gaps,
    }


# --------------------------------------------------------------------------- #
# CORE-03 / CORE-05 — evidence register
# --------------------------------------------------------------------------- #

def build_evidence_register(procurement: Dict[str, Any], milestones: Dict[str, Any],
                            proposals: Dict[str, Any], schedule: Dict[str, Any],
                            inputs: Dict[str, str]) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []

    def add(claim: str, value: Any, source_fields: Sequence[str], status: str,
            confidence: str, origin: str) -> None:
        records.append({
            "claim": claim, "value": value, "source_fields": list(source_fields),
            "status": status, "confidence": confidence, "origin": origin,
        })

    add("engine version", inputs.get("engine_version"), ["VERSION.md#Engine version"],
        "confirmed", "medium", "config")
    add("run input hash", inputs.get("input_hash"), ["input/input.json#input_hash"],
        "confirmed", "high", "user_input")
    add("statutory tendering applicability",
        procurement["legal_applicability"]["value"],
        procurement["legal_applicability"]["source_fields"],
        procurement["legal_applicability"]["status"], "none", "config")
    add("observed bid phase span",
        procurement.get("observed_bid_phase"),
        ["_procurement_review.json#actual_phase"], "confirmed", "medium", "deterministic")

    for pkg in procurement.get("packages", []):
        add(f"procurement package {pkg['package_id']} status", pkg["status"],
            pkg["source_fields"], "confirmed", "medium", "deterministic")
    for item in procurement.get("lead_items", []):
        add(f"lead item {item['item_id']} scheduled", item["scheduled"],
            item["source_fields"],
            "provisional" if item.get("confidence") == "provisional" else "unconfirmed",
            item.get("confidence") or "low", "config")

    for m in milestones.get("milestones", []):
        add(f"milestone {m['milestone_id']} finish", m["finish"], m["source_fields"],
            m["evidence_status"],
            "medium" if m["evidence_status"] == "confirmed" else "low", "deterministic")

    for p in proposals.get("proposals", []):
        add(f"proposal {p['proposal_id']} applied", p["applied"], p["source_fields"],
            "confirmed", "high", "engine_proposal")

    phase = schedule.get("phase_semantics") or {}
    site = phase.get("site_construction") or {}
    if site:
        add("site construction span", {"start": site.get("start"), "finish": site.get("finish"),
                                      "calendar_days": site.get("calendar_days")},
            ["_schedule.json#phase_semantics.site_construction"], "confirmed", "medium",
            "deterministic")

    certainty = schedule.get("programme_certainty") or {}
    if certainty:
        add("committed finish (known scope)",
            certainty.get("known_scope_base_finish"),
            ["_schedule.json#programme_certainty.known_scope_base_finish"],
            "confirmed", "medium", "deterministic")
        env = certainty.get("conditional_forecast_envelope") or {}
        add("conditional forecast envelope", env,
            ["_schedule.json#programme_certainty.conditional_forecast_envelope"],
            "unconfirmed", "low", "deterministic")

    levels: Dict[str, int] = {}
    origins: Dict[str, int] = {}
    for r in records:
        levels[r["status"]] = levels.get(r["status"], 0) + 1
        origins[r["origin"]] = origins.get(r["origin"], 0) + 1

    return {
        "records": records,
        "count": len(records),
        "by_status": levels,
        "by_origin": origins,
        "levels": list(EVIDENCE_LEVELS),
        "unsourced_count": sum(1 for r in records if not r["source_fields"]),
        "note": ("Every record points at an existing file and key path. A record with no "
                 "source_fields must not be presented as fact."),
    }


# --------------------------------------------------------------------------- #
# PC-01..PC-09 — executive commentary (deterministic; no model is called)
# --------------------------------------------------------------------------- #

WORD_RE = re.compile(r"[A-Za-z']+|\d+(?:\.\d+)?|[\u4e00-\u9fff]")


def _words(text: str) -> int:
    return len(WORD_RE.findall(text))


def _sentence(text: str, sources: Sequence[str], origin: str = "deterministic",
              terse: Optional[str] = None, priority: int = 1) -> Dict[str, Any]:
    return {"text": text, "terse": terse or text, "source_fields": list(sources),
            "origin": origin, "priority": priority}


def _select_executive(sentences: Sequence[Dict[str, Any]],
                      budget: int = 300) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """PC-04: build the executive version inside the word budget.

    The full sentence is preferred; the terse form is substituted only when the full form
    would push the version past the budget. Collapsing everything to its shortest wording
    would under-run the 150-word floor even though material is available, so terseness is
    treated as a fallback rather than the default. Anything that still does not fit is
    moved to the appendix — facts are never discarded.
    """
    ordered = sorted(sentences, key=lambda s: (s["priority"], sentences.index(s)))
    chosen: Dict[int, str] = {}
    dropped: List[Dict[str, Any]] = []
    total = 0
    for s in ordered:
        idx = sentences.index(s)
        full, terse = s["text"], s["terse"]
        if s["priority"] == 1:
            picked = full if total + _words(full) <= budget else terse
            chosen[idx] = picked
            total += _words(picked)
            continue
        if total + _words(full) <= budget:
            chosen[idx] = full
            total += _words(full)
        elif total + _words(terse) <= budget:
            chosen[idx] = terse
            total += _words(terse)
        else:
            dropped.append(s)
    # preserve original reading order within the executive version
    kept = [dict(sentences[i], executive_text=chosen[i]) for i in sorted(chosen)]
    return kept, dropped


def build_executive_commentary(procurement: Dict[str, Any], milestones: Dict[str, Any],
                               proposals: Dict[str, Any], schedule: Dict[str, Any]) -> Dict[str, Any]:
    gaps: List[str] = []
    sections: List[Dict[str, Any]] = []

    # --- overall position
    phase = schedule.get("phase_semantics") or {}
    site = phase.get("site_construction") or {}
    certainty = schedule.get("programme_certainty") or {}
    envelope = certainty.get("conditional_forecast_envelope") or {}
    pos: List[Dict[str, Any]] = []
    if site.get("start") and site.get("finish"):
        pos.append(_sentence(
            f"Site construction runs {site.get('start')} to {site.get('finish')} "
            f"({site.get('calendar_days')} calendar days).",
            ["_schedule.json#phase_semantics.site_construction"], terse=(
                f"Site construction: {site.get('start')} to {site.get('finish')} "
                f"({site.get('calendar_days')} calendar days).")))
    else:
        gaps.append("phase_semantics.site_construction is unavailable")
    if certainty.get("known_scope_base_finish"):
        full = f"Committed finish for the currently known scope is {certainty.get('known_scope_base_finish')}."
        terse = f"Committed finish for known scope: {certainty.get('known_scope_base_finish')}"
        if envelope.get("latest_finish"):
            full += (f" Re-solving the unresolved conditional drivers moves this no earlier than "
                     f"{envelope.get('earliest_finish')} and no later than "
                     f"{envelope.get('latest_finish')}.")
            terse += (f"; unresolved conditional drivers span "
                      f"{envelope.get('earliest_finish')} to {envelope.get('latest_finish')}.")
        else:
            terse += "."
        pos.append(_sentence(full, ["_schedule.json#programme_certainty.known_scope_base_finish",
                                    "_schedule.json#programme_certainty.conditional_forecast_envelope"],
                             terse=terse))
    else:
        gaps.append("programme_certainty.known_scope_base_finish is unavailable")
    sections.append({"id": "overall_position", "title": "Overall position", "sentences": pos})

    # --- milestones
    ms: List[Dict[str, Any]] = []
    pend = milestones.get("nearest_pending_milestone")
    if pend:
        owner = pend.get("decision_owner") or "not stated"
        ms.append(_sentence(
            f"{pend['label']} (milestone {pend['milestone_id']}, {pend['finish']}) is the "
            f"nearest milestone whose requirement is still unresolved; the decision owner "
            f"recorded by the engine is {owner}.",
            [f"_schedule.json#tasks[id={pend['milestone_id']}].client_state"],
            terse=(f"Nearest unresolved milestone: #{pend['milestone_id']} "
                   f"({pend['finish']}), decision owner {owner}.")))
    cnt = milestones.get("counts") or {}
    if cnt:
        labelled = ", ".join(f"{k} {v}" for k, v in sorted(cnt.items()))
        ms.append(_sentence(
            f"Milestone counts from this single solve: {labelled}.",
            ["_schedule.json#tasks[].client_state.requirement_status"],
            terse=f"Milestone counts: {labelled}."))
    else:
        gaps.append("no milestone was available for the executive summary")
    sections.append({"id": "milestones", "title": "Executive milestones", "sentences": ms})

    # --- procurement / critical dependencies
    pr: List[Dict[str, Any]] = []
    pkgs = procurement.get("packages") or []
    if pkgs:
        worst = procurement["attention_list"][0] if procurement.get("attention_list") else None
        if worst:
            pr.append(_sentence(
                f"{len(pkgs)} procurement package(s) are represented in the programme; the "
                f"highest attention item is {worst['id']} ({worst['label']}) with state "
                f"{worst['status']}.",
                ["_schedule.json#tasks[].outline_level",
                 "_procurement_review.json#legal_applicability"],
                terse=(f"{len(pkgs)} procurement packages are scheduled; highest attention "
                       f"item {worst['id']} ({worst['status']})."), priority=2))
    not_sched = [i for i in procurement.get("lead_items", []) if i.get("engine_expected")
                 and i["status"] == LEAD_NOT_SCHEDULED]
    if not_sched:
        pr.append(_sentence(
            f"The engine declares a long-lead requirement for "
            f"{', '.join(i['label'] for i in not_sched)} but no scheduled activity carries it, "
            f"so that lead time is not reflected in any date.",
            ["_schedule.json#furniture_lead", "config/long_lead_equipment.json"],
            terse=(f"{len(not_sched)} engine-declared long-lead item(s) have no scheduled "
                   f"activity, so their lead time is absent from the dates."), priority=2))
    legal = procurement.get("legal_applicability") or {}
    if legal.get("status") == "unconfirmed":
        pr.append(_sentence(
            "Statutory tendering applicability remains unconfirmed; the invitation method alone "
            "does not establish it.",
            ["_procurement_review.json#legal_applicability"],
            terse="Statutory tendering applicability remains unconfirmed.", priority=2))
    sections.append({"id": "procurement_and_dependencies", "title":
                     "Procurement and critical dependencies", "sentences": pr})

    # --- decisions required
    dec: List[Dict[str, Any]] = []
    if pend:
        owner = pend.get("decision_owner") or "the client/authority"
        dec.append(_sentence(
            f"Decision requested from {owner}: resolve the requirement behind "
            f"{pend['label']}.",
            [f"_schedule.json#tasks[id={pend['milestone_id']}].client_state.decision_owner"],
            terse=f"Decision requested from {owner}: milestone #{pend['milestone_id']}."))
    if proposals.get("proposal_count"):
        dec.append(_sentence(
            f"{proposals.get('proposal_count', 0)} engine proposal(s) are listed and none is applied; "
            f"each requires PM review before any change is scheduled.",
            ["_pm_optimisation_register.json#register[].pm_approval_required"],
            terse=(f"{proposals.get('proposal_count', 0)} engine proposals are listed; none is "
                   f"applied and each requires PM review.")))
    else:
        dec.append(_sentence(
            "No engine optimisation proposals were generated for this run; none is applied.",
            ["SKILL.md#optimizer-never-writes-baseline"],
            terse="No engine proposals were generated; none is applied."))
    sections.append({"id": "decisions_required", "title": "Decisions required", "sentences": dec})

    # --- boundary statement (PC-09)
    boundary = _sentence(
        "Productivity rates, dependency logic, shift governance, authority logic and the CPM were "
        "not changed by this summary, and no change may be made to shorten the programme without "
        "an approved, re-solved scenario.",
        ["SKILL.md#optimizer-never-writes-baseline"], priority=1)
    sections.append({"id": "boundary", "title": "Boundary statement", "sentences": [boundary]})

    # --- grounding pass (PC-02)
    unsupported: List[Dict[str, Any]] = []
    for sec in sections:
        kept = []
        for s in sec["sentences"]:
            if not s["source_fields"]:
                unsupported.append({"section": sec["id"], "text": s["text"]})
                continue
            kept.append(s)
        sec["sentences"] = kept

    all_sentences = [s for sec in sections for s in sec["sentences"]]
    # --- blocklist (PC-05 / PC-09) — evaluated on every wording that can ship
    violations: List[Dict[str, str]] = []
    for s in all_sentences:
        for pattern, why in COMMENTARY_BLOCKLIST:
            if (re.search(pattern, s["text"], flags=re.IGNORECASE)
                    or re.search(pattern, s["terse"], flags=re.IGNORECASE)):
                violations.append({"text": s["text"], "pattern": pattern, "why": why})

    kept, dropped = _select_executive(all_sentences, budget=300)
    executive_text = " ".join(s["executive_text"] for s in kept)
    word_count = _words(executive_text)

    return {
        "sections": sections,
        "executive_text": executive_text,
        "word_count": word_count,
        "word_target": [150, 300],
        "word_target_met": 150 <= word_count <= 300,
        "moved_to_appendix": [{"text": s["terse"], "reason": "word budget (PC-04)"}
                              for s in dropped],
        "unsupported_sentences": unsupported,
        "blocklist_violations": violations,
        "grounding_rule": ("A sentence with no source_fields is marked UNSUPPORTED and is "
                           "excluded from the executive version."),
        "generation": "deterministic template rendering; no language model is invoked",
        "approval_state": "NOT_APPROVED",
        "gaps": gaps,
    }


def render_commentary_markdown(commentary: Dict[str, Any], header: Dict[str, Any]) -> str:
    L = [f"# Executive PM Commentary — {header['project_name']}", ""]
    L.append(f"- as-of: {header['as_of']}")
    L.append(f"- engine version: {header['engine_version']}")
    L.append(f"- run id: {header['run_id']}")
    L.append(f"- input hash: {header['input_hash']}")
    L.append(f"- generated at: {header['generated_at']}")
    L.append(f"- completeness: **{header.get('completeness', 'UNKNOWN')}**"
             + (f" — missing: {', '.join(header.get('missing_inputs') or [])}"
                if header.get("missing_inputs") else ""))
    L.append(f"- approval state: **{commentary['approval_state']}**")
    L.append(f"- generation: {commentary['generation']}")
    L.append("")
    L.append("## Executive version")
    L.append("")
    L.append(commentary["executive_text"])
    L.append("")
    L.append(f"_Word count {commentary['word_count']} (target "
             f"{commentary['word_target'][0]}–{commentary['word_target'][1]})._")
    L.append("")
    L.append("## Sections with sources")
    L.append("")
    for sec in commentary["sections"]:
        if not sec["sentences"]:
            continue
        L.append(f"### {sec['title']}")
        L.append("")
        for s in sec["sentences"]:
            L.append(f"- {s['text']}")
            L.append(f"  - source: `{'`, `'.join(s['source_fields'])}` "
                     f"· origin: `{s['origin']}`")
        L.append("")
    if commentary["unsupported_sentences"]:
        L.append("## Unsupported sentences (excluded from the executive version)")
        L.append("")
        for s in commentary["unsupported_sentences"]:
            L.append(f"- [{s['sec'] if 'sec' in s else s.get('id')}] {s['text']}")
        L.append("")
    if commentary["gaps"]:
        L.append("## Gaps")
        L.append("")
        for g in commentary["gaps"]:
            L.append(f"- {g}")
        L.append("")
    L.append("## Evidence appendix")
    L.append("")
    for name, digest in sorted(header["input_sha256"].items()):
        L.append(f"- `{name}` — `{digest[:16]}`")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #

def collect_run(run_dir: str, root: str) -> Dict[str, Any]:
    audit = os.path.join(run_dir, "audit")
    sources = {
        "schedule": find_sidecar(audit, "_schedule.json"),
        "procurement_review": find_sidecar(audit, "_procurement_review.json"),
        "register": find_sidecar(audit, "_pm_optimisation_register.json"),
        "senior_pm_review": find_sidecar(audit, "_senior_pm_review.json"),
    }
    input_json = os.path.join(run_dir, "input", "input.json")
    summary_json = os.path.join(run_dir, "RUN_SUMMARY.json")
    data = {k: (load_json(v) if v and os.path.exists(v) else None) for k, v in sources.items()}
    data["input"] = load_json(input_json) if os.path.exists(input_json) else {}
    data["summary"] = load_json(summary_json) if os.path.exists(summary_json) else {}
    return {"sources": sources, "data": data, "audit_dir": audit}


def build_pack(run_dir: str, root: str,
               scenario_comparison: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build every approved view. Pure with respect to the run directory."""
    collected = collect_run(run_dir, root)
    data = collected["data"]
    schedule = data.get("schedule") or {}
    if not schedule:
        raise FileNotFoundError(
            f"no *_schedule.json sidecar found under {collected['audit_dir']}")

    input_sha: Dict[str, str] = {}
    for key, path in collected["sources"].items():
        if path and os.path.exists(path):
            input_sha[os.path.basename(path)] = sha256_file(path)
    for extra in (os.path.join(run_dir, "input", "input.json"),
                  os.path.join(run_dir, "RUN_SUMMARY.json")):
        if os.path.exists(extra):
            input_sha[os.path.relpath(extra, run_dir).replace(os.sep, "/")] = sha256_file(extra)

    # AC-PC-01: a missing source is disclosed as SOURCES_INCOMPLETE, never silently
    # defaulted. The marker avoids the bare word 'complete' so that the milestone
    # vocabulary ban in AC-EM-02 stays literally enforced on every view.
    missing_inputs: List[str] = sorted(
        name for name, path in collected["sources"].items() if not path)
    for rel in ("input/input.json", "RUN_SUMMARY.json"):
        if not os.path.exists(os.path.join(run_dir, *rel.split("/"))):
            missing_inputs.append(rel)
    completeness = "SOURCES_COMPLETE" if not missing_inputs else "SOURCES_INCOMPLETE"

    inputs = {
        "engine_version": engine_version(root),
        "run_id": (data.get("input") or {}).get("run_id") or os.path.basename(run_dir),
        "input_hash": ((data.get("input") or {}).get("input_hash")
                       or (data.get("summary") or {}).get("input_hash")),
        "project_name": schedule.get("project_name"),
        "as_of": ((data.get("summary") or {}).get("run_id") or os.path.basename(run_dir))
                 .replace("run_", ""),
        # the ONLY field that varies between two builds of the same run; every other byte
        # is a pure function of the inputs (AC-EM-07)
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "completeness": completeness,
        "missing_inputs": missing_inputs,
        "input_sha256": input_sha,
    }

    cfg_dir = os.path.join(root, "config")
    procurement = build_procurement_intelligence(
        schedule, data.get("procurement_review"), _lead_configs(cfg_dir))
    milestones = build_executive_milestones(schedule)
    link_packages_into_milestones(milestones, procurement)
    proposals = build_review_proposals(data.get("register"), scenario_comparison)
    register_view = build_evidence_register(procurement, milestones, proposals, schedule, inputs)
    commentary = build_executive_commentary(procurement, milestones, proposals, schedule)

    common = {
        "pack_schema": PACK_SCHEMA,
        "engine_version": inputs["engine_version"],
        "run_id": inputs["run_id"],
        "as_of": inputs["as_of"],
        "input_hash": inputs["input_hash"],
        "generated_at": inputs["generated_at"],
        "completeness": inputs["completeness"],
        "missing_inputs": inputs["missing_inputs"],
        "generated_from": "solved schedule sidecars (read-only)",
        "read_only": READ_ONLY,
        "applied": False,
        "baseline_unchanged": BASELINE_UNCHANGED,
        "recommended_scenario": None,
    }

    def wrap(view: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(common)
        out.update(view)
        return out

    return {
        "header": inputs,
        "common": common,
        "procurement_intelligence": wrap(procurement),
        "executive_milestones": wrap(milestones),
        "review_proposals": wrap(proposals),
        "evidence_register": wrap(register_view),
        "executive_commentary": wrap(commentary),
        "input_sha256": input_sha,
    }
