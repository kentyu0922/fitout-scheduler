"""Locate control dates on a solved programme."""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional


def _parse(value) -> Optional[datetime.date]:
    if not value:
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    text = str(value)[:10]
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def find_task(tasks: List[Dict[str, Any]], *needles: str) -> Optional[Dict[str, Any]]:
    for needle in needles:
        for task in tasks:
            if needle in str(task.get("name") or "") or needle == task.get("mocap_subtask") or needle == task.get("activity_uid"):
                return task
    return None


def _permit_task(tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    hit = next((t for t in tasks if t.get("permit_role") == "result"), None)
    if hit:
        return hit
    return find_task(tasks, "正式取得施工许可证", "施工许可取得", "取得施工许可证")


def finish_of(task: Optional[Dict[str, Any]]) -> Optional[datetime.date]:
    if not task:
        return None
    return _parse(task.get("finish") or task.get("end"))


def start_of(task: Optional[Dict[str, Any]]) -> Optional[datetime.date]:
    if not task:
        return None
    return _parse(task.get("start"))


def permit_date(tasks: List[Dict[str, Any]]) -> Optional[datetime.date]:
    return finish_of(_permit_task(tasks))


def wall_inspection_date(tasks: List[Dict[str, Any]]) -> Optional[datetime.date]:
    return finish_of(find_task(tasks, "隔墙及主管管线隐蔽工程验收", "隔墙隐蔽验收"))


def ceiling_inspection_date(tasks: List[Dict[str, Any]]) -> Optional[datetime.date]:
    return start_of(find_task(tasks, "天花隐蔽工程验收", "天花隐蔽验收")) or finish_of(
        find_task(tasks, "天花隐蔽工程验收", "天花隐蔽验收"))


def completion_date(tasks: List[Dict[str, Any]]) -> Optional[datetime.date]:
    t = find_task(tasks, "搬迁全部完成", "正式营业")
    return finish_of(t) or finish_of(find_task(tasks, "物理竣工"))


def mocap_subtasks(tasks: List[Dict[str, Any]]) -> List[str]:
    keys = [str(t.get("mocap_subtask")) for t in tasks if t.get("mocap_subtask")]
    if keys:
        return keys
    aliases = {
        "pmp.mocap.gate_fire_inspection": "gate_fire_inspection",
        "pmp.mocap.fire_door_frame": "mocap_fire_door_frame",
        "pmp.mocap.fire_door_leaf": "mocap_fire_door_leaf",
    }
    out = []
    for t in tasks:
        uid = str(t.get("activity_uid") or "")
        if uid in aliases:
            out.append(aliases[uid])
        elif t.get("specialist_core_step") and t.get("module_id") == "mocap" and t.get("mocap_step"):
            step = {1: "mocap_acoustic_base", 3: "mocap_truss_grid", 5: "mocap_silent_mep",
                    2: "mocap_optical_finishes", 4: "mocap_camera_mount", 6: "mocap_calibration_ue5",
                    7: "mocap_ready"}.get(int(t["mocap_step"]))
            if step:
                out.append(step)
    return out


def working_days_between(start, finish, profile: str = "7D") -> Optional[int]:
    if not start or not finish:
        return None
    from datetime import timedelta
    from core.holidays import load_holiday_pairs, is_workday, build_holiday_bitmap
    cur = start + timedelta(days=1)
    n = 0
    bitmap = build_holiday_bitmap(load_holiday_pairs()) if str(profile).upper() in {"5D", "5"} else None
    while cur <= finish:
        if str(profile).upper() in {"7D", "7"}:
            n += 1
        elif is_workday(cur, bitmap):
            n += 1
        else:
            if str(profile).upper() in {"6D", "6"} and cur.weekday() != 6:
                n += 1
        cur += timedelta(days=1)
    return n


def kickoff_date(tasks: List[Dict[str, Any]]) -> Optional[datetime.date]:
    return start_of(find_task(tasks, "Kick Off")) or start_of(next((t for t in tasks if t.get("start")), None))


def construction_drawings_task(tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((t for t in tasks if "全专业施工图" in str(t.get("name") or "")
                 and int(t.get("duration_days") or 0) > 0 and not t.get("summary")
                 and "审查" not in str(t.get("name") or "")), None)


def drawing_review_task(tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    aliases = ("第三方图审机构审查", "施工图送审与审查", "第三方施工图审查")
    return next((t for t in tasks if t.get("control_role") == "drawing_review"), None) or find_task(tasks, *aliases)


def drawing_rework_task(tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return find_task(tasks, "图审意见修改与一次复审", "图审意见答复与修改", "图审意见答复、修改与复核")


def drawing_certificate_task(tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((t for t in tasks if (
        "施工图审查合格" in str(t.get("name") or "") or "图审合格证" in str(t.get("name") or "")
    ) and "修改" not in str(t.get("name") or "")), None)


def demolition_task(tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((t for t in tasks if t.get("activity_type") == "demolition"), None) or find_task(
        tasks, "原有装修拆除")


def snapshot(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    kick = kickoff_date(tasks)
    permit = permit_date(tasks)
    wall = wall_inspection_date(tasks)
    ceiling = ceiling_inspection_date(tasks)
    done = completion_date(tasks)
    lead = (permit - kick).days if permit and kick else None
    ceiling_task = find_task(tasks, "天花隐蔽工程验收", "天花隐蔽验收")
    profile = str((ceiling_task or {}).get("calendar") or "7D")
    planned = int((ceiling_task or {}).get("concealed_gap_working_days") or 0) or None
    gap_cal = (ceiling - wall).days if ceiling and wall else None
    gap_wd = working_days_between(wall, ceiling, profile) if ceiling and wall else None
    return dict(
        kickoff=kick.isoformat() if kick else None,
        permit=permit.isoformat() if permit else None,
        wall_concealed=wall.isoformat() if wall else None,
        ceiling_concealed=ceiling.isoformat() if ceiling else None,
        completion=done.isoformat() if done else None,
        permit_lead_calendar_days=lead,
        concealed_gap_calendar_days=gap_cal,
        concealed_gap_working_days=planned or gap_wd,
        mocap_subtasks=mocap_subtasks(tasks),
        drawings_finish=finish_of(construction_drawings_task(tasks)).isoformat() if finish_of(construction_drawings_task(tasks)) else None,
        review_start=start_of(drawing_review_task(tasks)).isoformat() if start_of(drawing_review_task(tasks)) else None,
        review_finish=finish_of(drawing_review_task(tasks)).isoformat() if finish_of(drawing_review_task(tasks)) else None,
        rework_finish=finish_of(drawing_rework_task(tasks)).isoformat() if finish_of(drawing_rework_task(tasks)) else None,
        certificate_finish=finish_of(drawing_certificate_task(tasks)).isoformat() if finish_of(drawing_certificate_task(tasks)) else None,
        demolition_start=start_of(demolition_task(tasks)).isoformat() if start_of(demolition_task(tasks)) else None,
        drawings_days=int((construction_drawings_task(tasks) or {}).get("duration_days") or 0) or None,
    )
