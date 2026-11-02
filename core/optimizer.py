# -*- coding: utf-8 -*-
"""
core/optimizer.py — Schedule Optimizer v0 (read-only, deterministic, explainable)

Consumes the output of `solve_schedule` (tasks with start/finish) and produces:

  1. Critical path / float summary
       * calendar-aware backward pass that exactly inverts the forward solver's link
         semantics (FS / SS / FF + lag) on each task's OWN calendar
         (construction 7-day / standard 5-day / 24h) → total float in working days
       * the *driving chain*: walking back from the latest-finishing task through the
         predecessor whose constraint actually set each task's start
       * float distribution, near-critical list, per-phase critical counts
  2. Fast-track suggestions (v0 = ONE rule: plain FS → SS+lag overlap on the driving chain)
       * cites both activities, their floats, durations, current and proposed relationship
       * durations are never invented or changed — only the relationship token changes
       * every suggestion is verified by re-solving a deep copy of the schedule; the
         baseline task list is never mutated

What v0 is NOT: it does not apply anything, does not crash durations, does not add
resources, and does not evaluate combinations of suggestions. The legacy preview
`compute_cpm_metrics` (5-day axis) is left untouched for report compatibility;
its count is reported alongside for transparency. MS Project remains the authority for
final dates/float when an `.mpp` is produced.
"""
import copy
import pickle
import datetime
import json
import logging
import math
import os
import re
import sys
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

if __name__ == "__main__":  # allow `python core/optimizer.py solved.json` from the repo root
    _BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _BASE not in sys.path:
        sys.path.insert(0, _BASE)

from core.solver_engine import (
    PREDECESSOR_REGEX,
    GOV_APPROVAL_KEYWORDS,
    RELOCATION_KEYWORDS,
    _add_working_days,
    _is_working_day,
    build_calendar_bitmaps,
    solve_schedule,
)

logger = logging.getLogger("optimizer")

OPTIMIZER_VERSION = "v0"
FLOAT_UNIT = "working days in each task's own calendar"

CAL_24H = "24h"
CAL_CONSTRUCTION_7D = "construction_7d"
CAL_STANDARD_5D = "standard_5d"

# Keyword groups whose activities must not be overlapped by the FS→SS rule. Each entry:
# (group, keywords, reason). Checked against BOTH the predecessor and the successor name.
FAST_TRACK_EXCLUSIONS: List[Tuple[str, List[str], str]] = [
    ("statutory_approval",
     sorted(set(GOV_APPROVAL_KEYWORDS) | {"审查", "审批", "备案", "许可", "报监", "报建", "申报", "批文", "政府", "住建"}),
     "government / statutory processing window — cannot start before its submission input is complete"),
    ("inspection_acceptance",
     ["验收", "检测", "复测", "盲测", "查验", "自检", "移交", "Inspection"],
     "inspection or acceptance cannot begin before the work it inspects is finished"),
    ("iaq_sop",
     ["空气", "散味", "通风", "家具", "净化", "IAQ"],
     "dual IAQ sequence (first test → furniture → purge → second test) is a compliance hard chain"),
    ("procurement_window",
     ["招标", "投标", "回标", "标书", "标前", "清标", "述标", "评标", "定标", "中标", "LOA", "BAFO",
      "报价", "谈判", "合同", "签约", "资格预审", "短名单", "RFP"],
     "tender / award / contract windows are legal-commercial durations set by city rules, not site logic"),
    ("handover_relocation",
     sorted(set(RELOCATION_KEYWORDS) | {"搬迁", "入驻", "Handover"}),
     "handover and relocation follow completion on client workdays; no overlap"),
]


# --------------------------------------------------------------------------- helpers
def _to_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _dur(t: Dict[str, Any]) -> int:
    return int(t.get("duration_days", t.get("duration", 0)) or 0)


def _level(t: Dict[str, Any]) -> int:
    return int(t.get("outline_level", t.get("level", 3)))


def _calendar_mode(t: Dict[str, Any]) -> Tuple[bool, bool]:
    """(ignore_h, work_weekend) — mirrors solve_schedule; work_weekend is what the solver recorded."""
    cal_setting = str(t.get("calendar", ""))
    ignore_h = bool(t.get("ignore_holidays", False)) or cal_setting in ["24小时", "24Hours", "24 Hours"]
    work_weekend = bool(t.get("use_construction_cal", False))
    return ignore_h, work_weekend


def _effective_cal_mode(t: Dict[str, Any], default: str) -> str:
    """FIX-01: prefer the per-activity calendar resolved upstream; the global
    construction mode is only a fallback for tasks without one."""
    ac = t.get("activity_calendar")
    if isinstance(ac, dict) and ac.get("calendar_mode"):
        return ac["calendar_mode"]
    return default


def _calendar_label(t: Dict[str, Any]) -> str:
    ignore_h, work_weekend = _calendar_mode(t)
    if ignore_h:
        return CAL_24H
    ac = t.get("activity_calendar")
    if isinstance(ac, dict) and ac.get("calendar_id"):
        return ac["calendar_id"]
    return CAL_CONSTRUCTION_7D if work_weekend else CAL_STANDARD_5D


def _parse_predecessors(preds_str: str) -> List[Tuple[int, str, int, str]]:
    """→ [(pred_id, link_type, lag, raw_token)] using the solver's own regex/normalisation."""
    out = []
    for part in str(preds_str or "").split(","):
        raw = part.strip()
        if not raw:
            continue
        clean = raw.upper().replace(" ", "").replace("DAYS", "").replace("DAY", "").replace("D", "")
        m = PREDECESSOR_REGEX.match(clean)
        if not m:
            continue
        out.append((int(m.group(1)), m.group(2) or "FS", int(m.group(3)) if m.group(3) else 0, raw))
    return out


def _leaf_flags(tasks: List[Dict[str, Any]]) -> List[bool]:
    """Leaf = outline level >= 3 and not a parent of the next task (same rule as solve_schedule)."""
    n = len(tasks)
    flags = []
    for i, t in enumerate(tasks):
        lvl = _level(t)
        is_summary = lvl <= 2 or (i + 1 < n and _level(tasks[i + 1]) > lvl)
        flags.append(not is_summary)
    return flags


class _Cal:
    """Working-day arithmetic bound to the solver's holiday bitmaps."""

    def __init__(self, start_year: int, custom_holidays=None, construction_calendar_mode: str = '7D'):
        self.hol, self.spring = build_calendar_bitmaps(start_year, custom_holidays)
        self.cal_mode = construction_calendar_mode
        self._count_prefix = {}
        self._count_origin = datetime.date(start_year-1,1,1).toordinal()

    def is_working(self, d: datetime.date, t: Dict[str, Any]) -> bool:
        ih, ww = _calendar_mode(t)
        return _is_working_day(d, ih, ww, self.hol, self.spring,
                               _effective_cal_mode(t, self.cal_mode))

    def add(self, d: datetime.date, n: int, t: Dict[str, Any]) -> datetime.date:
        ih, ww = _calendar_mode(t)
        return _add_working_days(d, n, ih, ww, self.hol, self.spring,
                                 _effective_cal_mode(t, self.cal_mode))

    def latest_working_on_or_before(self, d: datetime.date, t: Dict[str, Any]) -> datetime.date:
        guard = 0
        while not self.is_working(d, t) and guard < 400:
            d -= timedelta(days=1)
            guard += 1
        return d

    def count_between(self, a: datetime.date, b: datetime.date, t: Dict[str, Any]) -> int:
        """Signed number of working days (in t's calendar) in (a, b]; negative when b < a."""
        if b == a:
            return 0
        sign = 1 if b > a else -1
        lo, hi = (a, b) if b > a else (b, a)
        ih, ww = _calendar_mode(t)
        mode = _effective_cal_mode(t, self.cal_mode)
        key = (ih, ww, mode)
        low, high = lo.toordinal(), hi.toordinal()
        origin, prefix = self._count_prefix.get(key, (min(low,self._count_origin), [0]))
        if low < origin:
            origin, prefix = low, [0]
        while origin + len(prefix) - 1 < high:
            current = datetime.date.fromordinal(origin + len(prefix))
            prefix.append(prefix[-1] + int(_is_working_day(current,ih,ww,self.hol,self.spring,mode)))
        self._count_prefix[key] = origin, prefix
        return sign * (prefix[high-origin] - prefix[low-origin])



# --------------------------------------------------------------------------- forward-constraint replay
def _constraint_boundary(pred, succ, link, lag, cal):
    from core.solver_engine import dependency_boundary
    key = 'start' if link == 'SS' else 'finish'
    boundary = pred.get(key + '_boundary', 'start_of_day' if key == 'start' or _dur(pred) == 0 else 'end_of_day')
    return dependency_boundary(_to_date(pred[key]), boundary, _dur(succ), link, lag, lambda d,n: cal.add(d,n,succ))


def _constraint_from_pred(pred: Dict[str, Any], succ: Dict[str, Any], link: str, lag: int, cal: _Cal):
    """Replay the solver's forward rule for one link. Returns ('start'|'finish', date)."""
    if link in ('FS','SS','FF'):
        d, _ = _constraint_boundary(pred, succ, link, lag, cal)
        return ('finish' if link == 'FF' else 'start'), d
    return None, None


def _driving_predecessors(task: Dict[str, Any], by_id: Dict[int, Dict[str, Any]], leaf_ids: set, cal: _Cal) -> List[Dict[str, Any]]:
    """Predecessors whose replayed constraint equals the task's actual start (or finish for FF)."""
    drivers = []
    s, f = _to_date(task["start"]), _to_date(task["finish"])
    for pid, link, lag, raw in _parse_predecessors(task.get("predecessors", "")):
        if pid not in leaf_ids or pid == task["id"]:
            continue
        kind, d = _constraint_from_pred(by_id[pid], task, link, lag, cal)
        if kind is None:
            continue
        _, boundary = _constraint_boundary(by_id[pid], task, link, lag, cal)
        actual_boundary = task.get(kind + '_boundary', 'start_of_day' if kind == 'start' or _dur(task) == 0 else 'end_of_day')
        if ((kind == "start" and d == s) or (kind == "finish" and d == f)) and boundary == actual_boundary:
            drivers.append({"id": pid, "link": link, "lag": lag, "token": raw})
    return drivers


# --------------------------------------------------------------------------- backward pass
def compute_total_float(tasks: List[Dict[str, Any]], cal: _Cal) -> Dict[int, Dict[str, Any]]:
    """
    Calendar-aware backward pass. For each leaf: LF/LS and total float (working days in its
    own calendar). Exactly inverts the solver's forward rules so a task that drives the
    project finish gets float 0 rather than a 5-day-axis artefact.
    """
    flags = _leaf_flags(tasks)
    leaves = [t for t, is_leaf in zip(tasks, flags) if is_leaf and t.get("start") and t.get("finish")]
    by_id = {t["id"]: t for t in leaves}
    leaf_ids = set(by_id)
    if not leaves:
        return {}

    project_finish = max(_to_date(t["finish"]) for t in leaves)

    succs: Dict[int, List[Tuple[int, str, int]]] = {i: [] for i in leaf_ids}
    for t in leaves:
        for pid, link, lag, _ in _parse_predecessors(t.get("predecessors", "")):
            if pid in leaf_ids and pid != t["id"]:
                succs[pid].append((t["id"], link, lag))

    lf: Dict[int, datetime.date] = {}
    ls: Dict[int, datetime.date] = {}
    from core.module_graph import dependency_order
    for t in reversed(dependency_order(tasks)):
        if t["id"] not in leaf_ids:
            continue
        i = t["id"]
        dur = _dur(t)
        bounds: List[datetime.date] = []
        for j, link, lag in succs[i]:
            if j not in ls:
                continue
            sj = by_id[j]
            if link == "FS":
                # Mirror forward FS: only a zero-duration predecessor releases
                # on its finish date; a one-day task still occupies that day.
                offset = int(_dur(sj) > 0 and t.get('finish_boundary', 'start_of_day' if _dur(t) == 0 else 'end_of_day') == 'end_of_day')
                f = ls[j] - timedelta(days=offset)
                guard = 0
                def release(candidate):
                    pred = dict(t, finish=candidate.isoformat())
                    date, boundary = _constraint_boundary(pred, sj, 'FS', lag, cal)
                    return date, boundary == 'end_of_day'
                latest = (ls[j], sj.get('start_boundary', 'start_of_day') == 'end_of_day')
                while release(f) > latest and guard < 400:
                    f -= timedelta(days=1)
                    guard += 1
                bounds.append(f)
            elif link == "SS":
                s = ls[j]
                guard = 0
                while cal.add(s, lag if lag > 0 else 0, sj) > ls[j] and guard < 400:
                    s -= timedelta(days=1)
                    guard += 1
                s = cal.latest_working_on_or_before(s, t)
                bounds.append(cal.add(s, dur - 1, t) if dur > 0 else s)
            elif link == "FF":
                f = lf[j]
                guard = 0
                while cal.add(f, lag if lag > 0 else 0, sj) > lf[j] and guard < 400:
                    f -= timedelta(days=1)
                    guard += 1
                bounds.append(f)
        late_finish = min(bounds) if bounds else project_finish
        # FIX-01 cross-calendar milestone: a ZERO-duration task may legitimately sit on a
        # non-working day of its own calendar (it is anchored to a predecessor finish
        # produced on another activity calendar). Forcing it onto a working day here
        # would over-tighten the predecessor's late finish and manufacture negative float.
        if dur > 0:
            late_finish = cal.latest_working_on_or_before(late_finish, t)
        lf[i] = late_finish
        ls[i] = cal.add(late_finish, -(dur - 1), t) if dur > 0 else late_finish

    out: Dict[int, Dict[str, Any]] = {}
    for t in leaves:
        i = t["id"]
        ef = _to_date(t["finish"])
        fl = cal.count_between(ef, lf[i], t)
        out[i] = {
            "id": i,
            "name": t.get("name", ""),
            "duration_days": _dur(t),
            "start": t["start"],
            "finish": t["finish"],
            "late_start": ls[i].isoformat(),
            "late_finish": lf[i].isoformat(),
            "float_days": fl,
            "critical": fl <= 0,
            "milestone": _dur(t) == 0,
            "calendar": _calendar_label(t),
        }
    return out


# --------------------------------------------------------------------------- driving chain
def driving_critical_chain(tasks: List[Dict[str, Any]], floats: Dict[int, Dict[str, Any]], cal: _Cal) -> List[Dict[str, Any]]:
    """Walk back from the latest-finishing leaf through the predecessor that actually set each start."""
    flags = _leaf_flags(tasks)
    leaves = [t for t, is_leaf in zip(tasks, flags) if is_leaf and t.get("start") and t.get("finish")]
    if not leaves:
        return []
    by_id = {t["id"]: t for t in leaves}
    leaf_ids = set(by_id)

    end = max(leaves, key=lambda t: (t["finish"], t["id"]))
    chain: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = end
    seen = set()
    while cur is not None and cur["id"] not in seen:
        seen.add(cur["id"])
        node = {
            "id": cur["id"],
            "name": cur.get("name", ""),
            "duration_days": _dur(cur),
            "start": cur["start"],
            "finish": cur["finish"],
            "float_days": floats.get(cur["id"], {}).get("float_days"),
            "calendar": _calendar_label(cur),
            "link_from_predecessor": None,
            "co_drivers": [],
        }
        drivers = _driving_predecessors(cur, by_id, leaf_ids, cal)
        nxt = None
        if drivers:
            # deterministic pick: lowest float, then longest duration, then lowest id
            drivers.sort(key=lambda d: (floats.get(d["id"], {}).get("float_days", 0), -_dur(by_id[d["id"]]), d["id"]))
            chosen = drivers[0]
            node["co_drivers"] = [d["id"] for d in drivers[1:]]
            node["link_from_predecessor"] = {"predecessor_id": chosen["id"], "type": chosen["link"], "lag": chosen["lag"], "token": chosen["token"]}
            nxt = by_id[chosen["id"]]
        chain.append(node)
        cur = nxt
    chain.reverse()
    # A driving node can still show float > 0 when its successor runs on a different calendar (e.g. a
    # 7-day construction milestone ending Sunday before a 5-day task starting Monday): the finish can
    # slip inside the successor's non-working days. MS Project shows the same slack. Flag it, don't hide it.
    for n in chain:
        f = n.get("float_days")
        n["calendar_boundary_float"] = bool(f and f > 0)
        if n["calendar_boundary_float"]:
            n["note"] = (f"drives the finish but has {f}d float: a downstream calendar boundary / same-day milestone rule "
                         f"absorbs up to {f} working day(s) of slip on this task")
    return chain


# --------------------------------------------------------------------------- fast-track rule
def _exclusion_reason(name: str) -> Optional[Tuple[str, str, str]]:
    for group, kws, reason in FAST_TRACK_EXCLUSIONS:
        for kw in kws:
            if kw and kw.lower() in name.lower():
                return group, kw, reason
    return None


def _replace_pred_token(preds_str: str, pred_id: int, new_token: str) -> str:
    parts = [p.strip() for p in str(preds_str or "").split(",") if p.strip()]
    out = []
    for p in parts:
        m = re.match(r"^(\d+)", p)
        if m and int(m.group(1)) == pred_id:
            out.append(new_token)
        else:
            out.append(p)
    return ",".join(out)


def apply_suggestion(tasks: List[Dict[str, Any]], suggestion: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return (deep-copied tasks with the suggestion's relationship change, change_log). Baseline untouched."""
    new_tasks = _strip_solution(tasks)
    ch = suggestion["change"]
    target = next(t for t in new_tasks if t["id"] == ch["task_id"])
    predecessor = next(t for t in new_tasks if t['id'] == suggestion['predecessor']['id'])
    if any(t.get('inspection_gate') or t.get('inspection_required') or t.get('inspection_gate_ids') for t in (target, predecessor)):
        raise ValueError('Cannot apply fast-track changes across explicit inspection requirements')
    before = str(target.get("predecessors", ""))
    after = _replace_pred_token(before, suggestion["predecessor"]["id"], suggestion["proposed_relationship"])
    target["predecessors"] = after
    change_log = {"task_id": ch["task_id"], "field": "predecessors", "before": before, "after": after,
                  "durations_changed": False, "reason": suggestion["why"][0] if suggestion.get("why") else ""}
    return new_tasks, change_log


def _fast_track_candidates(chain: List[Dict[str, Any]], floats: Dict[int, Dict[str, Any]], by_id: Dict[int, Dict[str, Any]],
                           overlap_ratio: float, max_overlap_days: int, min_overlap_days: int):
    suggestions: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for k in range(1, len(chain)):
        pred_node, succ_node = chain[k - 1], chain[k]
        link = succ_node.get("link_from_predecessor") or {}
        pid, sid = pred_node["id"], succ_node["id"]
        pred, succ = by_id[pid], by_id[sid]
        pair = {"predecessor_id": pid, "successor_id": sid, "predecessor_name": pred.get("name", ""), "successor_name": succ.get("name", "")}
        if any(t.get('inspection_gate') or t.get('inspection_required') or t.get('inspection_gate_ids') for t in (pred, succ)):
            skipped.append({**pair, 'reason': 'explicit inspection requirement cannot be fast-tracked'})
            continue

        if link.get("type") != "FS" or link.get("lag", 0) != 0:
            skipped.append({**pair, "reason": f"link is {link.get('type')}{link.get('lag') or ''}, v0 only converts plain FS"})
            continue
        dp, ds = _dur(pred), _dur(succ)
        if dp == 0 or ds == 0:
            skipped.append({**pair, "reason": "milestone in pair — nothing to overlap"})
            continue
        if succ_node.get("co_drivers"):
            skipped.append({**pair, "reason": f"successor is co-driven by {succ_node['co_drivers']}; overlapping one predecessor alone cannot move it"})
            continue
        excl = _exclusion_reason(pred.get("name", "")) or _exclusion_reason(succ.get("name", ""))
        if excl:
            group, kw, reason = excl
            skipped.append({**pair, "reason": f"excluded [{group}] keyword '{kw}': {reason}"})
            continue
        overlap = min(math.floor(dp * overlap_ratio), math.floor(ds * overlap_ratio), max_overlap_days)
        if overlap < min_overlap_days:
            skipped.append({**pair, "reason": f"durations {dp}d/{ds}d too short for a ≥{min_overlap_days}d overlap at ratio {overlap_ratio}"})
            continue
        ss_lag = dp - overlap
        proposed = f"{pid}SS+{ss_lag}"
        fp, fs = floats[pid]["float_days"], floats[sid]["float_days"]
        suggestions.append({
            "type": "fs_to_ss_overlap",
            "predecessor": {"id": pid, "name": pred.get("name", ""), "duration_days": dp, "start": pred["start"], "finish": pred["finish"],
                            "float_days": fp, "calendar": _calendar_label(pred)},
            "successor": {"id": sid, "name": succ.get("name", ""), "duration_days": ds, "start": succ["start"], "finish": succ["finish"],
                          "float_days": fs, "calendar": _calendar_label(succ)},
            "current_relationship": f"{pid} FS+0 (token '{link.get('token')}')",
            "proposed_relationship": proposed,
            "overlap_days": overlap,
            "estimated_saving_days": overlap,
            "estimated_saving_unit": f"working days ({_calendar_label(succ)}), upper bound — holds only while no parallel path becomes critical",
            "change": {"task_id": sid, "field": "predecessors", "before_token": link.get("token"), "after_token": proposed},
            "durations_changed": False,
            "applied_to_baseline": False,
            "why": [
                f"#{pid} '{pred.get('name', '')}' ({dp}d) and #{sid} '{succ.get('name', '')}' ({ds}d) are consecutive on the driving critical chain "
                f"(float {fp}d / {fs}d) and linked plain FS, so every day of overlap moves the project finish until another path binds.",
                f"Proposed: let #{sid} start after {ss_lag} working day(s) of #{pid} are done (SS+{ss_lag}) → {overlap}d overlap "
                f"= floor(min({dp}, {ds}) × {overlap_ratio}) capped at {max_overlap_days}d. Both durations stay exactly as in the baseline.",
            ],
            "risk": [
                "Trade stacking on the same zone: confirm with the site team that the successor can start on partially completed areas.",
                "Overlap raises rework risk if the predecessor's early output changes; hold the successor on completed zones only.",
            ],
        })
    return suggestions, skipped


# --------------------------------------------------------------------------- summaries
def _float_distribution(floats: Dict[int, Dict[str, Any]], near_threshold: int) -> Dict[str, int]:
    dist = {"critical_0": 0, f"near_critical_1_{near_threshold}": 0, f"moderate_{near_threshold + 1}_15": 0, "high_gt_15": 0, "negative": 0}
    for f in floats.values():
        v = f["float_days"]
        if v < 0:
            dist["negative"] += 1
        elif v == 0:
            dist["critical_0"] += 1
        elif v <= near_threshold:
            dist[f"near_critical_1_{near_threshold}"] += 1
        elif v <= 15:
            dist[f"moderate_{near_threshold + 1}_15"] += 1
        else:
            dist["high_gt_15"] += 1
    return dist


def _phase_summary(tasks: List[Dict[str, Any]], floats: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    phases = []
    cur = None
    for t in tasks:
        lvl = _level(t)
        if lvl <= 2:
            if lvl == 2 or cur is None:
                cur = {"id": t["id"], "name": t.get("name", ""), "start": t.get("start"), "finish": t.get("finish"), "leaf_count": 0, "critical_count": 0}
                phases.append(cur)
            continue
        if cur is not None and t["id"] in floats:
            cur["leaf_count"] += 1
            if floats[t["id"]]["critical"]:
                cur["critical_count"] += 1
    return [p for p in phases if p["leaf_count"]]


# --------------------------------------------------------------------------- entry point
def analyze_schedule(
    tasks: List[Dict[str, Any]],
    start_date: Optional[str] = None,
    custom_holidays: Optional[List] = None,
    *,
    max_suggestions: int = 3,
    overlap_ratio: float = 0.5,
    max_overlap_days: int = 5,
    min_overlap_days: int = 1,
    near_critical_threshold: int = 5,
    verify: bool = True,
    compliance_check: bool = True,
    construction_calendar_mode: str = '7D',
) -> Dict[str, Any]:
    """
    Deterministic optimizer summary for an already-solved task list. Never mutates `tasks`.
    `start_date` (YYYY-MM-DD) enables re-solve verification of each suggestion on a copy.
    """
    baseline_snapshot = pickle.dumps(tasks, protocol=5)  # Exact immutable snapshot; no deserialization.
    flags = _leaf_flags(tasks)
    leaves = [t for t, is_leaf in zip(tasks, flags) if is_leaf and t.get("start") and t.get("finish")]
    if not leaves:
        raise ValueError("optimizer needs solved tasks (start/finish present); run solve_schedule first")
    by_id = {t["id"]: t for t in leaves}

    proj_start = min(_to_date(t["start"]) for t in leaves)
    proj_finish = max(_to_date(t["finish"]) for t in leaves)
    cal = _Cal(proj_start.year, custom_holidays, construction_calendar_mode=construction_calendar_mode)

    floats = compute_total_float(tasks, cal)
    chain = driving_critical_chain(tasks, floats, cal)
    chain_ids = {n["id"] for n in chain}
    for f in floats.values():
        f["on_driving_chain"] = f["id"] in chain_ids
        # P1-13: four DISTINCT concepts, never conflated.
        # CPM_CRITICAL  = float == 0 (pure float logic, chain membership irrelevant)
        # DRIVING_CHAIN = on the forward-driving chain but float > 0 (calendar boundary)
        # NEAR_CRITICAL = 0 < float <= threshold, not on the chain
        # NON_CRITICAL  = everything else
        if f["float_days"] <= 0:
            f["critical_status"] = "CPM_CRITICAL"
        elif f["id"] in chain_ids:
            f["critical_status"] = "DRIVING_CHAIN"
        elif f["float_days"] <= near_critical_threshold:
            f["critical_status"] = "NEAR_CRITICAL"
        else:
            f["critical_status"] = "NON_CRITICAL"
        # calendar_boundary_float: the task drives the project finish yet shows
        # float > 0 — a downstream calendar boundary (mixed 5D/7D calendars,
        # same-day milestone rules) absorbs the slip. Only meaningful on the chain.
        f["calendar_boundary_float"] = bool(f["id"] in chain_ids and f["float_days"] > 0)
    n_crit = sum(1 for f in floats.values() if f["critical"])
    legacy_crit = sum(1 for t in leaves if t.get("critical") is True) if any("critical" in t for t in leaves) else None

    # P1-13: near_critical now matches the NEAR_CRITICAL critical_status exactly —
    # chain members with small float are DRIVING_CHAIN, a different concept.
    near = sorted((f for f in floats.values()
                   if 0 < f["float_days"] <= near_critical_threshold and not f["on_driving_chain"]),
                  key=lambda f: (f["float_days"], f["id"]))
    longest = sorted((n for n in chain if n["duration_days"] > 0), key=lambda n: (-n["duration_days"], n["id"]))[:5]

    suggestions, skipped = _fast_track_candidates(chain, floats, by_id, overlap_ratio, max_overlap_days, min_overlap_days)
    suggestions.sort(key=lambda s: (-s["estimated_saving_days"], s["predecessor"]["id"]))
    suggestions = suggestions[:max_suggestions]
    for k, s in enumerate(suggestions, 1):
        s["id"] = f"FT-{k}"

    verification_note = "not verified (no start_date given)"
    baseline_resolve_consistent = None
    if verify and start_date:
        base_res = solve_schedule(_strip_solution(tasks), start_date, custom_holidays)
        base_finish = max(t["finish"] for t in base_res["tasks"] if t.get("finish"))
        baseline_resolve_consistent = (base_finish == proj_finish.isoformat())
        verification_note = "each suggestion re-solved on a deep copy with solve_schedule; baseline untouched"
        for s in suggestions:
            new_tasks, change_log = apply_suggestion(tasks, s)
            res = solve_schedule(new_tasks, start_date, custom_holidays)
            new_finish = max(t["finish"] for t in res["tasks"] if t.get("finish"))
            saving_cal = (proj_finish - _to_date(new_finish)).days
            new_floats = compute_total_float(res["tasks"], cal)
            new_chain = driving_critical_chain(res["tasks"], new_floats, cal)
            n_err = None
            if compliance_check:
                try:
                    from core import compliance as _c
                    from core import holidays as _h
                    n_err = sum(1 for it in _c.run_compliance_checks(res["tasks"], holiday_raw=_h.load_holiday_raw()) if it.get("level") == "error")
                except Exception as ex:  # pragma: no cover - compliance is advisory here
                    logger.warning(f"[optimizer] compliance re-check skipped: {ex}")
            s["verified"] = {
                "method": "re-solve of a deep copy (solve_schedule), baseline untouched",
                "baseline_finish": proj_finish.isoformat(),
                "new_finish": new_finish,
                "saving_calendar_days": saving_cal,
                "successor_new_start": next((t["start"] for t in res["tasks"] if t["id"] == s["successor"]["id"]), None),
                "new_critical_chain_ids": [n["id"] for n in new_chain],
                "critical_chain_changed": [n["id"] for n in new_chain] != [n["id"] for n in chain],
                "compliance_errors_after": n_err,
                "change_log": change_log,
            }
            if saving_cal < s["estimated_saving_days"]:
                s["why"].append(
                    f"Re-solve shows {saving_cal} calendar day(s) saved vs the {s['estimated_saving_days']}d working-day estimate: "
                    "another path (or a calendar boundary) becomes binding — see verified.new_critical_chain_ids."
                )
            elif saving_cal > s["estimated_saving_days"]:
                s["why"].append(
                    f"Re-solve shows {saving_cal} calendar day(s) saved for a {s['estimated_saving_days']} working-day overlap: "
                    "downstream 5-day-calendar tasks moved across a weekend/holiday, so calendar saving exceeds the working-day estimate."
                )

    assert pickle.dumps(tasks, protocol=5) == baseline_snapshot, "optimizer must not mutate the baseline task list"

    return {
        "optimizer_version": OPTIMIZER_VERSION,
        "scope": {
            "does": ["critical path / float summary (calendar-aware backward pass)",
                     "fast-track suggestion: plain FS → SS+lag overlap on the driving chain, re-solve verified"],
            "does_not": ["apply changes", "change any duration", "crash / add resources", "evaluate suggestion combinations",
                         "replace MS Project as the authority for final dates and float"],
            "float_unit": FLOAT_UNIT,
            "legacy_preview_note": "compute_cpm_metrics (5-day axis) is unchanged and used by existing reports; its count is shown as legacy_preview_critical_count",
        },
        "project": {
            "start": proj_start.isoformat(),
            "finish": proj_finish.isoformat(),
            "calendar_days": (proj_finish - proj_start).days,
            "task_count": len(tasks),
            "leaf_count": len(leaves),
            "milestone_count": sum(1 for t in leaves if _dur(t) == 0),
            "baseline_resolve_consistent": baseline_resolve_consistent,
        },
        "critical_path": {
            "definition": "chain = forward-driving predecessors from project start to latest finish (exact); "
                          "critical_task_count = leaves with float 0 (may differ from chain length at calendar boundaries)",
            "critical_status_definitions": {
                "CPM_CRITICAL": "total float == 0 (pure float logic)",
                "DRIVING_CHAIN": "on the forward-driving chain but float > 0 — a downstream calendar "
                                 "boundary absorbs the slip (calendar_boundary_float = true)",
                "NEAR_CRITICAL": f"0 < total float <= {near_critical_threshold} working days, off the chain",
                "NON_CRITICAL": "total float beyond the near-critical threshold",
            },
            "driving_chain_length": len(chain),
            "chain_nodes_with_calendar_boundary_float": sum(1 for n in chain if n.get("calendar_boundary_float")),
            "critical_task_count": n_crit,
            "critical_status_counts": {
                status: sum(1 for f in floats.values() if f.get("critical_status") == status)
                for status in ("CPM_CRITICAL", "DRIVING_CHAIN", "NEAR_CRITICAL", "NON_CRITICAL")
            },
            "chain_vs_critical_explanation": (
                f"driving chain has {len(chain)} nodes but only {n_crit} task(s) have float 0: "
                f"{sum(1 for n in chain if n.get('calendar_boundary_float'))} chain node(s) drive the finish "
                f"yet show float > 0 because a downstream calendar boundary (mixed 5D/7D calendars or a "
                f"same-day milestone rule) absorbs their slip. 'Critical' (float=0) and 'driving the finish' "
                f"are different questions — read critical_status, not the bare critical flag, per activity."
            ),
            "legacy_preview_critical_count": legacy_crit,
            "chain": chain,
            "longest_critical_activities": longest,
        },
        "float": {
            "unit": FLOAT_UNIT,
            "distribution": _float_distribution(floats, near_critical_threshold),
            "near_critical": near,
            "max_float_task": max(floats.values(), key=lambda f: (f["float_days"], -f["id"])) if floats else None,
            "by_task": [floats[t["id"]] for t in leaves],
        },
        "phases": _phase_summary(tasks, floats),
        "fast_track": {
            "rule": {
                "type": "fs_to_ss_overlap",
                "overlap_ratio": overlap_ratio,
                "max_overlap_days": max_overlap_days,
                "min_overlap_days": min_overlap_days,
                "exclusions": [{"group": g, "keywords": k, "reason": r} for g, k, r in FAST_TRACK_EXCLUSIONS],
            },
            "verification": verification_note,
            "suggestion_count": len(suggestions),
            "suggestions": suggestions,
            "skipped_chain_pairs": skipped,
        },
    }


def _strip_solution(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deep copy with solver-written fields removed, ready to be re-solved."""
    new_tasks = [dict(t) for t in tasks]  # Solver and suggestions replace top-level fields only.
    for t in new_tasks:
        for k in ("start", "finish", "use_construction_cal", "critical", "total_slack_days"):
            t.pop(k, None)
    return new_tasks


# --------------------------------------------------------------------------- text report
def render_report(result: Dict[str, Any]) -> str:
    p, cp, fl, ft = result["project"], result["critical_path"], result["float"], result["fast_track"]
    lines = [
        f"[optimizer {result['optimizer_version']}] project {p['start']} → {p['finish']} ({p['calendar_days']} calendar days), "
        f"{p['leaf_count']} leaf tasks ({p['milestone_count']} milestones)",
        f"  critical: driving chain {cp['driving_chain_length']} nodes"
        + (f" ({cp['chain_nodes_with_calendar_boundary_float']} with calendar-boundary float > 0)" if cp.get("chain_nodes_with_calendar_boundary_float") else "")
        + f"; tasks with float 0 = {cp['critical_task_count']}"
        + (f" (legacy preview flagged {cp['legacy_preview_critical_count']})" if cp.get("legacy_preview_critical_count") is not None else ""),
        f"  critical_status: {cp.get('critical_status_counts')}",
        f"  why chain != critical: {cp.get('chain_vs_critical_explanation')}",
        f"  float ({fl['unit']}): {fl['distribution']}",
    ]
    if fl.get("near_critical"):
        lines.append("  near-critical: " + "; ".join(f"#{f['id']} {f['name']} float={f['float_days']}d" for f in fl["near_critical"][:8]))
    lines.append("  driving chain:")
    for n in cp["chain"]:
        link = n.get("link_from_predecessor")
        via = f" ← #{link['predecessor_id']} {link['type']}{('+' + str(link['lag'])) if link and link.get('lag') else ''}" if link else " (project start)"
        lines.append(f"    #{n['id']:<3} {n['duration_days']:>3}d {n['start']}→{n['finish']} float={n['float_days']} [{n['calendar']}] {n['name']}{via}")
    lines.append(f"  fast-track suggestions ({ft['suggestion_count']}; {ft['verification']}):")
    if not ft["suggestions"]:
        lines.append("    none — every consecutive plain-FS pair on the driving chain is excluded or too short (see skipped_chain_pairs)")
    for s in ft["suggestions"]:
        pr, su = s["predecessor"], s["successor"]
        lines.append(
            f"    {s['id']}: #{pr['id']} '{pr['name']}' ({pr['duration_days']}d, float {pr['float_days']}) → "
            f"#{su['id']} '{su['name']}' ({su['duration_days']}d, float {su['float_days']}): "
            f"{s['current_relationship']} ⇒ {s['proposed_relationship']} (overlap {s['overlap_days']}d, est. saving {s['estimated_saving_days']}d)"
        )
        v = s.get("verified")
        if v:
            lines.append(
                f"          verified: finish {v['baseline_finish']} → {v['new_finish']} ({v['saving_calendar_days']} calendar days), "
                f"compliance errors after = {v['compliance_errors_after']}, chain changed = {v['critical_chain_changed']}; NOT applied to baseline"
            )
        for w in s["why"]:
            lines.append(f"          why: {w}")
    if ft["skipped_chain_pairs"]:
        lines.append(f"  skipped chain pairs: {len(ft['skipped_chain_pairs'])} (reasons in JSON: statutory / inspection / IAQ / procurement / milestone / too short)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI
def _main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Schedule Optimizer v0 — critical path / float summary + fast-track suggestions from a solved JSON")
    ap.add_argument("solved_json", help="output of core/solver_engine.py (has start_date + tasks with start/finish)")
    ap.add_argument("--out", help="write full result JSON here")
    ap.add_argument("--max", type=int, default=3)
    a = ap.parse_args(argv)
    with open(a.solved_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = analyze_schedule(data["tasks"], data.get("start_date"), max_suggestions=a.max)
    print(render_report(result))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[optimizer] written: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
