"""Project-level fire acceptance/filing vs room-local MoCap fire door and cameras."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.module_graph import add_pred, dependency_order
from core._common import parse_predecessor


def _find(tasks: List[Dict[str, Any]], *needles: str) -> Optional[Dict[str, Any]]:
    for needle in needles:
        hit = next((t for t in tasks if needle in str(t.get("name") or "")), None)
        if hit:
            return hit
    return None


def _drop_pred(task: Dict[str, Any], pred_id: int) -> None:
    parts = []
    for pid, rel, lag in parse_predecessor(str(task.get("predecessors") or "")):
        if pid == pred_id:
            continue
        parts.append(str(pid) + rel + (f"{lag:+d}" if lag else ""))
    task["predecessors"] = ",".join(dict.fromkeys(parts))


def apply_fire_sequence(tasks: List[Dict[str, Any]], facts: Dict[str, Any]) -> Dict[str, Any]:
    hydro = _find(tasks, "喷淋系统24小时严密性", "24小时严密性", "满水打压")
    strength = _find(tasks, "喷淋系统强度试验")
    third = _find(tasks, "第三方消防设施检测", "消防设施与电气防火检测", "消防设施功能测试", "消防设施自查")
    linkage = _find(tasks, "全系统 T&C 联动调试", "消防联动")
    fire_apply = _find(tasks, "特殊建设工程消防验收申请", "消防验收备案申报",
                       "消防验收申报与现场评定", "消防验收/竣工消防备案申报",
                       "住建主管部门消防验收")
    fire_result = _find(tasks, "取得消防验收合格意见", "取得消防验收备案凭证",
                        "消防验收通过或备案完成", "消防备案完成",
                        "取得《建设工程消防验收合格意见书》")
    completion = _find(tasks, "工程竣工验收备案", "竣工验收备案", "住建局联合竣工")
    physical = _find(tasks, "物理竣工")
    cam = next((t for t in tasks if t.get("mocap_subtask") == "mocap_camera_mount"), None)
    cal = next((t for t in tasks if t.get("mocap_subtask") == "mocap_calibration_ue5"), None)
    ready = next((t for t in tasks if t.get("mocap_subtask") == "mocap_ready"), None)
    optical = next((t for t in tasks if t.get("mocap_subtask") == "mocap_optical_finishes"), None)
    acoustic_install = next((t for t in tasks if t.get("acoustic_finish_role") == "install"), None)
    acoustic_clean = next((t for t in tasks if t.get("acoustic_finish_role") == "clean"), None)
    floor = next((t for t in tasks if t.get("activity_type") in {"raised_floor", "vinyl", "carpet", "tile_stone"}), None)
    partition = next((t for t in tasks if t.get("activity_type") == "partition_framing"), None)
    wall = _find(tasks, "隔墙及主管管线隐蔽")
    seq = max(t["id"] for t in tasks) + 1
    changes = []

    # Project-level 消防验收/备案 after tests, before 竣工验收备案（58号令第31、36条；沪建规范〔2025〕12号第10条）。
    for pred in (hydro, strength, third, linkage, physical):
        if fire_apply and pred:
            add_pred(fire_apply, pred)
    if fire_result and fire_apply:
        add_pred(fire_result, fire_apply)
    if completion and fire_result:
        add_pred(completion, fire_result)
    elif completion and fire_apply:
        add_pred(completion, fire_apply)
    changes.append("project fire after sprinkler test, third-party detection, linkage; before completion filing")

    a06 = facts.get("addon_a06") in {"required", "需要"}
    frame = leaf = None
    if a06:
        frame = next((t for t in tasks if t.get("activity_uid") == "pmp.mocap.fire_door_frame"), None)
        if frame is None:
            frame = dict(
                id=seq, name="动捕房间防火门框随隔墙安装",
                client_task_name="动捕房间防火门框随隔墙安装",
                outline_level=3, duration_days=2, duration=2, calendar="7D",
                activity_uid="pmp.mocap.fire_door_frame", mocap_subtask="mocap_fire_door_frame",
                specialist_core_step=True, phase="实体施工",
                duration_basis="GB 50877-2014第5.3.8条：门框与墙体牢固连接；行业惯例框随隔墙（调研#17）。",
            )
            seq += 1
            tasks.append(frame)
        if partition:
            add_pred(frame, partition)
        if wall:
            add_pred(wall, frame)
        leaf = next((t for t in tasks if t.get("activity_uid") == "pmp.mocap.fire_door_leaf"), None)
        if leaf is None:
            leaf = dict(
                id=seq, name="动捕房间防火门扇安装（吸声完成面及地面标高确定后）",
                client_task_name="动捕房间防火门扇安装",
                outline_level=3, duration_days=2, duration=2, calendar="7D",
                activity_uid="pmp.mocap.fire_door_leaf", mocap_subtask="mocap_fire_door_leaf",
                specialist_core_step=True, phase="实体施工",
                duration_basis="GB 50877-2014第5.3.10、7.1.1条：门扇与地面间隙≤9mm，调试后验收；行业惯例扇在墙面完成面及地面标高确定后安装，并须在消防联调之前（调研#17）。",
            )
            seq += 1
            tasks.append(leaf)
        add_pred(leaf, frame)
        for pred in (optical, acoustic_install, floor):
            if pred:
                add_pred(leaf, pred)
        if linkage:
            add_pred(linkage, leaf)
        changes.append("MoCap fire-door frame with partitions; leaf after acoustic face/floor, before linkage")

    # Cameras after fire linkage AND project fire acceptance. Do not wait for
    # cameras on the fire path (that cycle was: physical←cameras←fire←physical).
    if cam:
        if physical:
            _drop_pred(physical, cam["id"])
        if ready and physical:
            _drop_pred(physical, ready["id"])
        if cal and physical:
            _drop_pred(physical, cal["id"])
        if fire_apply:
            _drop_pred(fire_apply, cam["id"])
        if fire_result:
            _drop_pred(fire_result, cam["id"])
            add_pred(cam, fire_result)
        elif fire_apply:
            add_pred(cam, fire_apply)
        if linkage:
            add_pred(cam, linkage)
        if acoustic_clean:
            add_pred(cam, acoustic_clean)
        cam["input_notes"] = str(cam.get("input_notes") or "") + (
            "\n光学相机上架须在消防联动调试及项目级消防验收/备案之后；房间保洁不是唯一前置。"
        )
        changes.append("cameras after linkage and project fire; physical completion does not wait cameras")

    if physical and frame:
        add_pred(physical, frame)
    if physical and optical:
        add_pred(physical, optical)

    dependency_order(tasks)
    if tasks:
        tasks[0]["fire_sequence"] = dict(changes=changes)
    return dict(applied=True, changes=changes,
                fire_apply_id=fire_apply["id"] if fire_apply else None,
                fire_result_id=fire_result["id"] if fire_result else None,
                camera_id=cam["id"] if cam else None,
                door_frame_id=frame["id"] if frame else None,
                door_leaf_id=leaf["id"] if leaf else None)
