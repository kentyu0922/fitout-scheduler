# -*- coding: utf-8 -*-
"""Client-facing visibility: dated work only.

Internal WBS may keep unconfirmed placeholders (UNKNOWN demolition, UNRESOLVED
IAQ). Those must not appear on the executive Gantt as empty bars. Ask, or hide.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from core._common import is_true_milestone

HIDE_OUT_OF_SCOPE = "out_of_scope_unconfirmed"
HIDE_UNRESOLVED = "unresolved_not_committed"
HIDE_ZERO_WORK = "zero_duration_work"
HIDE_NO_DATES = "missing_start_or_finish"
HIDE_EMPTY_SUMMARY = "no_dated_work_under_summary"


def _lvl(task: Dict[str, Any]) -> int:
    return int(task.get("outline_level", task.get("level") or 3))


def _dur(task: Dict[str, Any]) -> float:
    try:
        return float(task.get("duration_days", task.get("duration") or 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _dated(task: Dict[str, Any]) -> bool:
    return bool(task.get("start")) and bool(task.get("finish"))


def work_item_hide_reason(task: Dict[str, Any]) -> Optional[str]:
    """Why a row must not appear on the client Gantt. None = candidate to show."""
    if task.get("duration_method") == "out_of_scope" or task.get("scope_exclusion"):
        return HIDE_OUT_OF_SCOPE
    if task.get("unresolved_placeholder") or task.get("duration_status") == "UNRESOLVED":
        return HIDE_UNRESOLVED
    if not _dated(task):
        return HIDE_NO_DATES
    lvl = _lvl(task)
    if lvl <= 2 or task.get("summary"):
        return None
    if is_true_milestone(task):
        return None
    if _dur(task) <= 0:
        return HIDE_ZERO_WORK
    return None


def _has_visible_descendant(tasks: Sequence[Dict[str, Any]], index: int) -> bool:
    parent_lvl = _lvl(tasks[index])
    for later in tasks[index + 1:]:
        later_lvl = _lvl(later)
        if later_lvl <= parent_lvl:
            break
        if later.get("client_visible"):
            return True
    return False


def collect_open_decisions(tasks: Sequence[Dict[str, Any]],
                           site_condition: Optional[str] = None) -> List[Dict[str, Any]]:
    decisions: List[Dict[str, Any]] = []
    sc = (site_condition or "unknown").lower()
    if sc in ("", "unknown", "none"):
        decisions.append({
            "id": "SITE_CONDITION",
            "ask": "现场是毛坯交付（不拆）还是要拆现有装修？",
            "options": [
                {"value": "shell", "label": "毛坯/shell，不排拆除"},
                {"value": "cat_b_existing", "label": "存量精装拆除"},
                {"value": "full_strip", "label": "全拆"},
                {"value": "renovation_partial", "label": "局部拆除（需拆除比例）"},
            ],
            "if_strip_l2": "确认要拆后，总控按标准层写入拆除约 8 天、清运约 4 天（L0 窗口，可重叠进场）。",
            "now": "拆除未确认，甘特不画 0 天工序，避免客户看成没有拆除或已确定不拆。",
        })
    seen_iaq = False
    for t in tasks:
        drv = (t.get("conditional_requirement") or {}).get("driver_id")
        if drv == "OCCUPANCY_IAQ" and t.get("duration_status") == "UNRESOLVED" and not seen_iaq:
            seen_iaq = True
            decisions.append({
                "id": "OCCUPANCY_IAQ",
                "ask": "是否要求 CMA 空气检测 / flush-out？（默认双测属于规划政策，需核实项目要求）",
                "options": [
                    {"value": "STANDARD", "label": "要：首次 GB 50325，二次 GB/T 18883"},
                    {"value": "NO_FORMAL_IAQ", "label": "本案明确不做空气检测"},
                ],
                "now": "空气项未确认，不在甘特上画无工期占位。",
            })
    seen_auth = False
    for t in tasks:
        drv = (t.get("conditional_requirement") or {}).get("driver_id")
        if drv == "AUTHORITY_ACCEPTANCE_CHAIN" and t.get("duration_status") == "UNRESOLVED" and not seen_auth:
            seen_auth = True
            decisions.append({
                "id": "AUTHORITY_ACCEPTANCE_CHAIN",
                "ask": "消防图审/验收备案与联合竣工是否纳入本案？（当前为保守规划预留，项目适用性待确认）",
                "options": [
                    {"value": "STANDARD", "label": "需要纳入，按已确认适用范围排期"},
                    {"value": "UNKNOWN", "label": "诊断占位，甘特不画"},
                ],
                "now": "权威闭环未确认，不在甘特上画无工期占位。",
            })
    return decisions


def apply_client_visibility(tasks: Sequence[Dict[str, Any]],
                            site_condition: Optional[str] = None) -> Dict[str, Any]:
    """Stamp client_visible on each task. Audit JSON keeps hidden rows."""
    hidden: List[Dict[str, Any]] = []
    for t in tasks:
        reason = work_item_hide_reason(t)
        t["client_visible"] = reason is None
        if reason:
            t["client_hide_reason"] = reason
            hidden.append({"id": t.get("id"), "name": t.get("name"), "reason": reason})
        else:
            t.pop("client_hide_reason", None)
    for i in range(len(tasks)-1, -1, -1):
        t = tasks[i]
        if _lvl(t) <= 1:
            continue
        nxt = tasks[i + 1] if i + 1 < len(tasks) else None
        is_summary = bool(t.get("summary")) or _lvl(t) <= 2 or (
            nxt is not None and _lvl(nxt) > _lvl(t)
        )
        if not is_summary:
            continue
        if t.get("client_visible") and not _has_visible_descendant(tasks, i):
            t["client_visible"] = False
            t["client_hide_reason"] = HIDE_EMPTY_SUMMARY
            hidden.append({"id": t.get("id"), "name": t.get("name"), "reason": HIDE_EMPTY_SUMMARY})
    decisions = collect_open_decisions(tasks, site_condition)
    visible_n = sum(1 for t in tasks if t.get("client_visible"))
    return dict(
        visible=visible_n,
        hidden=len(hidden),
        hidden_rows=hidden,
        open_decisions=decisions,
        rule="Client Gantt shows only dated milestones and positive-duration work. Unconfirmed scope is a question, not a 0-day bar.",
    )


def client_visible_tasks(tasks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [t for t in tasks if t.get("client_visible") is not False
            and work_item_hide_reason(t) is None]
