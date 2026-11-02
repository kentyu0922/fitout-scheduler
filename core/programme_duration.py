"""Programme duration vs site construction duration (V6.4, GOV-17).

Why this exists
---------------
"总工期 492 天" gets read as "现场干了 492 天". It is not. A fitout programme contains
design, tendering, permit approval and statutory waiting — none of which are site
production, and several of which cannot be compressed by adding people or shifts.

This module splits the solved programme into explicitly declared segments and reports
the two numbers separately. It never retimes anything; it reads a solved schedule.

Segment mapping is config-driven (config/production_candidate.json → programme_segments).
A Phase that matches no rule is an error, not a silent fallback.
"""
from __future__ import annotations

import copy
import datetime as _dt
import re
from typing import Any, Dict, List, Optional, Sequence

from core import holidays as HOL
from core.recommendation_gate import load_production_candidate_config

PHASE_LEVEL = 2


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _d(value: Any) -> Optional[_dt.date]:
    if value is None:
        return None
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _calendar_days(start: Optional[_dt.date], finish: Optional[_dt.date]) -> Optional[int]:
    if not start or not finish:
        return None
    return (finish - start).days + 1


def _working_days(start: Optional[_dt.date], finish: Optional[_dt.date]) -> Optional[int]:
    if not start or not finish or finish < start:
        return None
    hol = HOL.load_holiday_pairs()
    n, cur = 0, start
    while cur <= finish:
        if HOL.is_workday(cur, hol):
            n += 1
        cur += _dt.timedelta(days=1)
    return n


def _phase_no(name: str, pattern: str) -> Optional[int]:
    m = re.match(pattern, str(name or "").strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# phase → segment classification
# ---------------------------------------------------------------------------
def classify_phase(name: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Map one Phase summary row to a declared segment. Raises when unmatched."""
    cfg = cfg or load_production_candidate_config()
    seg_cfg = cfg.get("programme_segments") or {}
    pattern = seg_cfg.get("phase_no_regex") or r"^Phase\s*(\d+)"
    no = _phase_no(name, pattern)
    matches = []
    for rule in seg_cfg.get("phase_rules") or []:
        hit_no = rule.get("phase_no") is None or rule.get("phase_no") == no
        keys = [k for k in (rule.get("name_contains") or []) if k]
        hit_name = (not keys) or any(k in str(name or "") for k in keys)
        if hit_no and hit_name:
            matches.append(rule)
    if not matches:
        fb = seg_cfg.get("unmatched_fallback") or {}
        if fb.get("on_unmatched", "error") == "error":
            raise ValueError(
                f"Phase 未命中 programme_segments.phase_rules：{name!r}（phase_no={no}）。"
                f"必须在 config/production_candidate.json 显式补规则，禁止静默归类。")
        return dict(phase_no=no, name=name, segment=fb.get("segment"), label_zh=name,
                    statutory_wait=False, compressible=False, matched=False)
    if len(matches) > 1 and seg_cfg.get("require_unique_match", True):
        raise ValueError(f"Phase 命中多条规则：{name!r} → {[m.get('segment') for m in matches]}")
    rule = matches[0]
    return dict(phase_no=no, name=name, segment=rule.get("segment"),
                label_zh=rule.get("label_zh") or name,
                statutory_wait=bool(rule.get("statutory_wait")),
                compressible=bool(rule.get("compressible")),
                statutory_note=rule.get("statutory_note"),
                matched=True)


def phase_index(tasks: Sequence[Dict[str, Any]],
                cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Group tasks under their nearest preceding Phase summary row."""
    cfg = cfg or load_production_candidate_config()
    phases: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    unmatched: List[str] = []
    for t in tasks:
        level = int(t.get("outline_level", t.get("level", 1)) or 1)
        if level == PHASE_LEVEL:
            try:
                meta = classify_phase(t.get("name"), cfg)
            except ValueError as exc:
                unmatched.append(str(exc))
                meta = dict(phase_no=_phase_no(t.get("name"),
                                               (cfg.get("programme_segments") or {}).get(
                                                   "phase_no_regex", r"^Phase\s*(\d+)")),
                            name=t.get("name"), segment=None, label_zh=t.get("name"),
                            statutory_wait=False, compressible=False, matched=False)
            current = dict(meta, task_ids=[t.get("id")], task_count=0)
            phases.append(current)
            continue
        if current is None:
            continue
        if level > PHASE_LEVEL and int(t.get("duration_days", t.get("duration", 0)) or 0) >= 0:
            current["task_ids"].append(t.get("id"))
    if unmatched:
        raise ValueError(" | ".join(unmatched))
    return phases


# ---------------------------------------------------------------------------
# breakdown
# ---------------------------------------------------------------------------
def programme_breakdown(solved_tasks: Sequence[Dict[str, Any]],
                        cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Split a solved task list into Programme / Site Construction / Closeout readouts.

    solved_tasks must already carry start/finish (i.e. output of solve_schedule).
    """
    cfg = cfg or load_production_candidate_config()
    seg_defs = ((cfg.get("programme_segments") or {}).get("segments") or {})
    phases = phase_index(solved_tasks, cfg)
    by_id = {t.get("id"): t for t in solved_tasks}

    def span(ids):
        starts = [_d(by_id[i].get("start")) for i in ids if i in by_id]
        finishes = [_d(by_id[i].get("finish")) for i in ids if i in by_id]
        starts = [s for s in starts if s]
        finishes = [f for f in finishes if f]
        if not starts or not finishes:
            return None, None
        return min(starts), max(finishes)

    phase_rows: List[Dict[str, Any]] = []
    seg_acc: Dict[str, Dict[str, Any]] = {}
    for p in phases:
        s, f = span(p["task_ids"])
        if s is None:
            continue
        row = dict(phase_no=p["phase_no"], phase_name=p["name"], segment=p["segment"],
                   label_zh=p["label_zh"], start=s.isoformat(), finish=f.isoformat(),
                   calendar_days=_calendar_days(s, f), working_days=_working_days(s, f),
                   task_count=len([i for i in p["task_ids"] if i in by_id]),
                   statutory_wait=p["statutory_wait"], compressible=p["compressible"],
                   counts_toward_site_construction=bool(
                       (seg_defs.get(p["segment"]) or {}).get("counts_toward_site_construction")))
        if p.get("statutory_note"):
            row["statutory_note"] = p["statutory_note"]
        phase_rows.append(row)

        acc = seg_acc.setdefault(p["segment"] or "unclassified",
                                 dict(segment=p["segment"] or "unclassified",
                                      label_zh=(seg_defs.get(p["segment"]) or {}).get("label_zh")
                                      or p["label_zh"],
                                      counts_toward_site_construction=bool(
                                          (seg_defs.get(p["segment"]) or {}).get(
                                              "counts_toward_site_construction")),
                                      starts=[], finishes=[], phase_nos=[], task_count=0))
        acc["starts"].append(s)
        acc["finishes"].append(f)
        acc["phase_nos"].append(p["phase_no"])
        acc["task_count"] += row["task_count"]

    all_start, all_finish = span([t.get("id") for t in solved_tasks])
    site_ids = [i for p in phases for i in p["task_ids"]
                if (seg_defs.get(p["segment"]) or {}).get("counts_toward_site_construction")]
    site_start, site_finish = span(site_ids) if site_ids else (None, None)

    segments = []
    for key in ("pre_construction", "site_construction", "closeout"):
        if key not in seg_acc:
            continue
        a = seg_acc[key]
        s, f = min(a["starts"]), max(a["finishes"])
        segments.append(dict(segment=key, label_zh=a["label_zh"],
                             start=s.isoformat(), finish=f.isoformat(),
                             calendar_days=_calendar_days(s, f),
                             working_days=_working_days(s, f),
                             phase_nos=a["phase_nos"], task_count=a["task_count"],
                             counts_toward_site_construction=a["counts_toward_site_construction"]))
    for key, a in seg_acc.items():
        if key in ("pre_construction", "site_construction", "closeout"):
            continue
        s, f = min(a["starts"]), max(a["finishes"])
        segments.append(dict(segment=key, label_zh=a["label_zh"], start=s.isoformat(),
                             finish=f.isoformat(), calendar_days=_calendar_days(s, f),
                             working_days=_working_days(s, f), phase_nos=a["phase_nos"],
                             task_count=a["task_count"],
                             counts_toward_site_construction=False))

    p_cal = _calendar_days(all_start, all_finish)
    s_cal = _calendar_days(site_start, site_finish)
    statutory = [r for r in phase_rows if r["statutory_wait"]]
    statutory_days = sum(r["calendar_days"] or 0 for r in statutory)

    return dict(
        programme=dict(start=(all_start.isoformat() if all_start else None),
                       finish=(all_finish.isoformat() if all_finish else None),
                       calendar_days=p_cal, working_days=_working_days(all_start, all_finish),
                       unit="日历天 / 工作日"),
        site_construction=dict(start=(site_start.isoformat() if site_start else None),
                               finish=(site_finish.isoformat() if site_finish else None),
                               calendar_days=s_cal,
                               working_days=_working_days(site_start, site_finish),
                               share_of_programme_pct=(round(100.0 * s_cal / p_cal, 1)
                                                       if p_cal and s_cal else None),
                               unit="日历天 / 工作日"),
        non_site_days=(dict(calendar_days=(p_cal - s_cal) if (p_cal and s_cal) else None,
                            share_pct=(round(100.0 * (p_cal - s_cal) / p_cal, 1)
                                       if p_cal and s_cal else None))),
        statutory_wait=dict(phase_nos=[r["phase_no"] for r in statutory],
                            calendar_days_span=statutory_days,
                            note=("按段跨度累加；段之间可能重叠或留有间隙，故为跨度上界而非净等待天数。")),
        segments=segments,
        phases=phase_rows,
        definitions=(cfg.get("programme_segments") or {}).get("definitions"),
        must_not_be_read_as=("Programme Duration 不是现场施工工期；"
                             "资源杠杆（班组 / 班次 / 分区）只作用于 site_construction 段。"),
    )


def explain(breakdown: Dict[str, Any]) -> List[str]:
    """Short PM-facing lines."""
    p = breakdown["programme"]
    s = breakdown["site_construction"]
    out = [f"Programme Duration（总工期）: {p['calendar_days']} 日历天 "
           f"({p['start']} → {p['finish']})",
           f"Site Construction Duration（现场施工工期）: {s['calendar_days']} 日历天 "
           f"({s['start']} → {s['finish']})，占总工期 {s['share_of_programme_pct']}%"]
    for seg in breakdown["segments"]:
        out.append(f"  · {seg['label_zh']}: {seg['calendar_days']} 日历天 "
                   f"({seg['start']} → {seg['finish']})")
    if breakdown["statutory_wait"]["phase_nos"]:
        out.append(f"法定等待段 Phase {breakdown['statutory_wait']['phase_nos']}："
                   f"跨度 {breakdown['statutory_wait']['calendar_days_span']} 日历天，"
                   f"不可由加人/加班压缩。")
    return out
