# -*- coding: utf-8 -*-
"""Isolated night-window placer. Not wired into main.py or solve_schedule.

Production CPM (`solver_engine.solve_schedule`) is date-granularity 5D/6D/7D
with shift_policy single_shift_8h. This module places datetime windows
22:00→05:00 so overnight work is not faked as 8h day-shifts.

Synthetic clocks here are test data, not Hangzhou property permits.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.furniture_install_plan import compute_furniture_install_nights
from core.solver_engine import parse_predecessor_token

SYNTHETIC_MARK = "SYNTHETIC_TEST_DATA_NOT_HANGZHOU_PROPERTY_PERMIT"

FULL_WINDOW_ONLY_BASIS = (
    "Placement policy FULL_WINDOW_ONLY: a night task occupies the next complete "
    "permitted window after predecessor finish. A predecessor finishing at 23:30 "
    "skips the remainder of that night. This is this policy, not a general rule "
    "for all construction activities."
)

PRODUCTION_NIGHT_LIMITS = dict(
    solver_granularity="date_by_default",
    solver_shift="single_shift_8h_by_default",
    overnight_supported_in_solve_schedule="opt_in_night_window_enabled_synthetic_forward_only",
    night_allowed_profile_implemented=False,
    cpm_complete_when_night_enabled=False,
    mpp_export_not_in_this_phase=True,
    interfaces_still_date_only_when_disabled=[
        "config/calendar_profiles.json → profiles.NIGHT_ALLOWED.implemented",
        "core/solver_engine.py → default solve_schedule start/finish dates",
        "core/resource_model.py → shift_factor(night=True)",
        "core/mpp_renderer (day calendars; deferred until night forward-pass is accepted)",
    ],
    note="night_window defaults off. Do not pass night counts as duration_days.",
)


class NightWindowScheduleError(ValueError):
    pass


def production_solver_night_capability() -> Dict[str, Any]:
    return dict(PRODUCTION_NIGHT_LIMITS)


def _parse_clock(text: str) -> Tuple[int, int]:
    hour, minute = text.strip().split(":")
    return int(hour), int(minute)


def window_for_start_date(day, *, start_clock: str, finish_clock: str) -> Dict[str, Any]:
    sh, sm = _parse_clock(start_clock)
    fh, fm = _parse_clock(finish_clock)
    start = datetime(day.year, day.month, day.day, sh, sm)
    finish = datetime(day.year, day.month, day.day, fh, fm) + timedelta(days=1)
    hours = (finish - start).total_seconds() / 3600.0
    return dict(
        calendar_date=day.isoformat(),
        start=start,
        finish=finish,
        permitted_hours=hours,
        overnight=True,
    )


def in_window(moment: datetime, window: Dict[str, Any]) -> bool:
    return window["start"] <= moment < window["finish"]


def next_full_window(
    after: datetime,
    *,
    start_clock: str = "22:00",
    finish_clock: str = "05:00",
    nights_per_week: int = 7,
) -> Dict[str, Any]:
    """First complete night window that starts at or after `after`.

    A partial remaining night is not a full window. Weekly frequency is not
    inferred from window length.
    """
    if nights_per_week not in (5, 6, 7):
        raise NightWindowScheduleError("nights_per_week must be 5, 6 or 7")
    sh, sm = _parse_clock(start_clock)
    candidate_day = after.date()
    # If still inside last night (00:00–finish), that window started yesterday.
    fh, fm = _parse_clock(finish_clock)
    morning_end = datetime(after.year, after.month, after.day, fh, fm)
    if after < morning_end:
        candidate_day = (after - timedelta(days=1)).date()
    start_today = datetime(candidate_day.year, candidate_day.month, candidate_day.day, sh, sm)
    if after <= start_today:
        return window_for_start_date(candidate_day, start_clock=start_clock, finish_clock=finish_clock)
    # After window start (including during the night): skip to next calendar night.
    nxt = candidate_day + timedelta(days=1)
    return window_for_start_date(nxt, start_clock=start_clock, finish_clock=finish_clock)


def is_night_task(task: Dict[str, Any]) -> bool:
    unit = str(task.get("duration_unit") or "").strip()
    if unit == "night_window":
        return True
    if task.get("duration_nights") is not None:
        return True
    return False


def validate_night_window_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not cfg or not cfg.get("enabled"):
        return {}
    if not cfg.get("synthetic"):
        raise NightWindowScheduleError(
            "night_window is internal synthetic-only in this phase; "
            "do not attach Hangzhou property permits")
    policy = cfg.get("placement_policy") or "FULL_WINDOW_ONLY"
    if policy != "FULL_WINDOW_ONLY":
        raise NightWindowScheduleError(
            f"unsupported placement_policy {policy!r}; only FULL_WINDOW_ONLY")
    if cfg.get("derate_effective_vs_8h"):
        raise NightWindowScheduleError("do not derate team-night rate by 6/8")
    nights = cfg.get("nights_per_week", 7)
    if nights not in (5, 6, 7):
        raise NightWindowScheduleError("nights_per_week must be 5, 6 or 7")
    start_clock = cfg.get("permitted_start") or "22:00"
    finish_clock = cfg.get("permitted_finish") or "05:00"
    hours = cfg.get("effective_hours")
    sample = window_for_start_date(
        datetime(2000, 1, 1).date(), start_clock=start_clock, finish_clock=finish_clock)
    if hours is not None and float(hours) - sample["permitted_hours"] > 1e-9:
        raise NightWindowScheduleError("effective_hours exceed permitted window")
    return dict(
        enabled=True,
        synthetic=True,
        placement_policy="FULL_WINDOW_ONLY",
        nights_per_week=int(nights),
        permitted_start=start_clock,
        permitted_finish=finish_clock,
        effective_hours=hours,
        day_start_clock=cfg.get("day_start_clock") or "08:00",
        day_finish_clock=cfg.get("day_finish_clock") or "18:00",
        non_work_dates=set(cfg.get("non_work_dates") or []),
        source=cfg.get("source") or SYNTHETIC_MARK,
    )


def place_nights(
    after: datetime,
    night_count: int,
    calendar: Dict[str, Any],
    is_working_day=None,
) -> List[Dict[str, Any]]:
    count = int(night_count)
    if count < 0:
        raise NightWindowScheduleError("night_count must be >= 0")
    if count == 0:
        return []
    blocked = set(calendar.get("non_work_dates") or [])
    windows = []
    cursor = after
    for _ in range(count):
        win = None
        for _guard in range(400):
            win = next_full_window(
                cursor,
                start_clock=calendar["permitted_start"],
                finish_clock=calendar["permitted_finish"],
                nights_per_week=int(calendar.get("nights_per_week") or 7),
            )
            day_key = win["start"].date().isoformat()
            work_ok = is_working_day(win["start"].date()) if is_working_day else True
            if work_ok and day_key not in blocked:
                break
            cursor = win["finish"]
            win = None
        if win is None:
            raise NightWindowScheduleError("no working night window found within 400 days")
        windows.append(win)
        cursor = win["finish"]
    return windows


def _topo(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id = {int(t["id"]): t for t in tasks}
    if len(by_id) != len(tasks):
        raise NightWindowScheduleError("duplicate task id")
    pending = set(by_id)
    ordered: List[Dict[str, Any]] = []
    while pending:
        ready = []
        for tid in list(pending):
            preds = []
            for part in str(by_id[tid].get("predecessors") or "").split(","):
                if not part.strip():
                    continue
                parsed = parse_predecessor_token(part)
                if not parsed:
                    raise NightWindowScheduleError(f"bad predecessor {part!r}")
                preds.append(parsed[0])
            if all(p not in pending for p in preds):
                ready.append(tid)
        if not ready:
            raise NightWindowScheduleError("cycle or missing predecessor")
        for tid in sorted(ready):
            pending.remove(tid)
            ordered.append(by_id[tid])
    return ordered


def schedule_night_graph(tasks: List[Dict[str, Any]], calendar: Dict[str, Any]) -> Dict[str, Any]:
    """Independent experimental placer. Not the client master-programme solver."""
    if not calendar.get("synthetic"):
        raise NightWindowScheduleError("calendar.synthetic must be true; do not use Hangzhou permits here")
    validate_night_window_config(dict(calendar, enabled=True))
    placed = []
    results: Dict[int, Dict[str, Any]] = {}
    for task in _topo(tasks):
        tid = int(task["id"])
        after = None
        for part in str(task.get("predecessors") or "").split(","):
            if not part.strip():
                continue
            parsed = parse_predecessor_token(part)
            if not parsed:
                raise NightWindowScheduleError(f"bad predecessor {part!r}")
            pid, rel, lag = parsed
            if rel != "FS" or lag != 0:
                raise NightWindowScheduleError("experimental placer supports FS+0 only")
            pred = results[pid]
            cand = pred["finish"]
            after = cand if after is None else max(after, cand)
        if task.get("fixed_finish"):
            finish = datetime.fromisoformat(str(task["fixed_finish"]))
            start = datetime.fromisoformat(str(task["fixed_start"])) if task.get("fixed_start") else finish
            row = dict(
                id=tid, name=task.get("name"), start=start, finish=finish,
                occupied_nights=[], night_count=0,
                predecessors=task.get("predecessors") or "",
                basis=task.get("basis") or "fixed fixture datetime",
                synthetic=True, synthetic_mark=SYNTHETIC_MARK,
            )
        else:
            nights = int(task["night_count"])
            if after is None:
                raise NightWindowScheduleError(f"task {tid} has no predecessor and no fixed times")
            windows = place_nights(after, nights, calendar)
            row = dict(
                id=tid, name=task.get("name"),
                start=windows[0]["start"] if windows else after,
                finish=windows[-1]["finish"] if windows else after,
                occupied_nights=[
                    dict(start=w["start"].isoformat(sep=" "), finish=w["finish"].isoformat(sep=" "))
                    for w in windows
                ],
                night_count=nights,
                predecessors=task.get("predecessors") or "",
                basis=task.get("basis") or FULL_WINDOW_ONLY_BASIS,
                synthetic=True, synthetic_mark=SYNTHETIC_MARK,
            )
        results[tid] = row
        placed.append(row)
    return dict(
        kind="experimental_night_window_schedule",
        synthetic=True,
        synthetic_mark=SYNTHETIC_MARK,
        hangzhou_committed_schedule=False,
        applied=False,
        engine_patch=False,
        wired_into_main=False,
        forward_pass_only=True,
        cpm_complete=False,
        calendar=dict(calendar),
        production_solver=production_solver_night_capability(),
        tasks=placed,
    )


def _parse_dt(value: Any) -> Any:
    if value is None or hasattr(value, "hour"):
        return value
    text = str(value)
    if "T" in text:
        return datetime.fromisoformat(text)
    if len(text) > 10:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return datetime.strptime(text, "%Y-%m-%d")


def combine_clock(day, clock_str: str) -> datetime:
    hour, minute = _parse_clock(clock_str)
    return datetime(day.year, day.month, day.day, hour, minute)


def synthetic_furniture_fixture() -> Dict[str, Any]:
    plan = compute_furniture_install_nights(
        quantity_each=105,
        quantity_source="SYNTHETIC_ASSUMED_QUANTITY_NOT_PROJECT_BOM",
        rate_value=50,
        rate_unit="each_per_team_night",
        crew_size=3,
        risk_factor=0.70,
        buffer_nights=1,
        night_window_basis="SYNTHETIC 22:00–05:00; team night not 6/8 derated",
        estimated_by="synthetic_test",
    )
    calendar = dict(
        synthetic=True,
        source=SYNTHETIC_MARK,
        nights_per_week=7,
        permitted_start="22:00",
        permitted_finish="05:00",
        effective_hours=6,
        derate_effective_vs_8h=False,
        confirmation_status="TEST_FIXTURE",
    )
    tasks = [
        dict(
            id=1,
            name="合成前置释放（测试夹具）",
            fixed_start="2026-10-12T18:00:00",
            fixed_finish="2026-10-12T18:00:00",
            predecessors="",
            basis="synthetic predecessor finish; not a Hangzhou statutory release",
        ),
        dict(
            id=2,
            name="合成家具安装（3 安装夜）",
            night_count=plan["installation_nights"],
            predecessors="1FS",
            basis=(
                f"furniture_install_plan installation_nights={plan['installation_nights']}; "
                "each_per_team_night 50×0.70; not 6/8 of 8h day"
            ),
        ),
        dict(
            id=3,
            name="合成家具缓冲（1 夜）",
            night_count=plan["buffer_nights"],
            predecessors="2FS",
            basis="buffer_nights listed separately from installation nights",
        ),
    ]
    result = schedule_night_graph(tasks, calendar)
    result["furniture_plan"] = dict(
        installation_nights=plan["installation_nights"],
        buffer_nights=plan["buffer_nights"],
        candidate_total_nights=plan["candidate_total_nights"],
        rate_unit=plan["rate_unit"],
        derated_capacity_each_per_night=plan["derated_capacity_each_per_night"],
        applied=plan["applied"],
        finish_after=plan["finish_after"],
    )
    return result


def render_node_table(payload: Dict[str, Any]) -> str:
    lines = [
        "# 合成夜班窗口排程（非杭州承诺图）",
        "",
        f"- 标记： `{payload['synthetic_mark']}`",
        "- 定位：独立实验，**未**接入客户主进度求解器 / main.py",
        f"- 正向实验 only：未计算关键路径 / 总浮时",
        f"- 策略： FULL_WINDOW_ONLY（23:30 顺延下一完整夜班，不是通用施工规则）",
        "- 4 夜 ≠ 4 个 8h 工作日。团队夜不按 6/8 二次折减。测试时刻不是杭州物业许可。",
        "",
        "## 节点",
        "",
        "| ID | 任务 | 开始 | 完成 | 占用夜班 | 前置 | 计算依据 | 合成 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in payload["tasks"]:
        nights = "; ".join(f"{n['start']}→{n['finish']}" for n in t["occupied_nights"]) or "—"
        start = t["start"].isoformat(sep=" ") if hasattr(t["start"], "isoformat") else t["start"]
        finish = t["finish"].isoformat(sep=" ") if hasattr(t["finish"], "isoformat") else t["finish"]
        lines.append(
            f"| {t['id']} | {t['name']} | {start} | {finish} | {nights} | `{t['predecessors'] or '—'}` | {t['basis']} | {t['synthetic']} |"
        )
    lines += [
        "",
        "## 尚未纳入本阶段",
        "",
        "- 完整 CPM（反向、总时差、关键路径）",
        "- MPP 夜班日历导出",
        "- 杭州项目承诺日期",
        "",
    ]
    return "\n".join(lines)
