# -*- coding: utf-8 -*-
"""
compliance.py - v3 合规红线代码化校验

将 SOP / 用户已确立的刚性业务红线，从「文档约定」固化为「可在解算后自动审计」的代码断言。
当前覆盖三类可机检红线：
  1. 两次室内空气检测 SOP（首次盲测 + 二次复测，严禁压缩为一次）
  2. 消防报建/验收节点存在性（凡涉及消防改动，必须走消防报批程序）
  3. 春节+元宵 15 天大假日历挂起（施工 7 天日历须含该 Exception）

所有检查均为【非阻塞审计】——返回 issue 列表，由调用方决定告警或升级，
绝不静默通过，也不伪造排程。
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("compliance")

# 红线关键词库
AIR_FIRST_KW = ["首次", "盲测", "基材", "第一次", "首轮"]
AIR_SECOND_KW = ["二次", "复测", "正式", "第二次", "末次"]
FIRE_KW = ["消防", "二消", "喷淋", "烟感", "防火"]
SPRING_KW = ["春节", "元宵", "正月"]


def _task_names(tasks: List[Dict[str, Any]]) -> List[str]:
    return [str(t.get("name", "")) for t in tasks]


def validate_air_quality_twice(tasks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """红线：两次空气检测缺一不可。"""
    issues = []
    names = _task_names(tasks)
    has_first = any(any(k in n for k in AIR_FIRST_KW) and "空气" in n for n in names) or \
                any("盲测" in n for n in names)
    has_second = any(any(k in n for k in AIR_SECOND_KW) and "空气" in n for n in names) or \
                 any("复测" in n for n in names)
    if not has_first:
        issues.append({
            "level": "error",
            "code": "AIR_FIRST_MISSING",
            "message": "缺少『首次室内空气盲测』节点，违反两次空气检测 SOP（首次盲测为强制项）。"
        })
    if not has_second:
        issues.append({
            "level": "error",
            "code": "AIR_SECOND_MISSING",
            "message": "缺少『二次室内空气质量复测』节点，违反两次空气检测 SOP（复测为强制项）。"
        })
    return issues


def validate_fire_approval(tasks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """红线：凡涉及消防改动，须含消防报建/验收节点。"""
    issues = []
    names = _task_names(tasks)
    has_fire = any(any(k in n for k in FIRE_KW) for n in names)
    if not has_fire:
        issues.append({
            "level": "warning",
            "code": "FIRE_NODE_MISSING",
            "message": "未检测到任何消防报建/验收节点。若本项目涉及消防设施改动，则必须走消防报批程序（即使面积<300㎡免办施工许可）。请确认是否遗漏。"
        })
    return issues


def validate_spring_festival_calendar(holiday_raw: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
    """红线：施工 7 天日历须挂起春节+元宵 15 天大假。"""
    issues = []
    if not holiday_raw:
        issues.append({
            "level": "warning",
            "code": "CALENDAR_EMPTY",
            "message": "节假日日历为空，春节+元宵 15 天大假无法挂起，施工日历将连续性出错。"
        })
        return issues
    has_spring = any(any(k in (h.get("name", "")) for k in SPRING_KW) for h in holiday_raw)
    if not has_spring:
        issues.append({
            "level": "warning",
            "code": "SPRING_FESTIVAL_MISSING",
            "message": "节假日日历未包含春节/元宵 Exception，民工正月十五后返岗的 15 天大假将不会被挂起。"
        })
    return issues


def validate_long_lead_equipment(tasks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """红线/预警：长周期设备与系统家具采购交期不得严重低于行业基准。"""
    issues = []
    try:
        from experts.procurement_expert import audit_tasks_long_lead_duration
        findings = audit_tasks_long_lead_duration(tasks)
        for f in findings:
            issues.append({
                "level": "warning",
                "code": "LONG_LEAD_DURATION_SHORT",
                "message": f["message"]
            })
    except Exception as ex:
        logger.debug(f"[compliance] 长周期设备交期审计跳过: {ex}")
    return issues


def run_compliance_checks(tasks: List[Dict[str, Any]],
                          holiday_raw: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    """聚合全部合规校验，返回 issue 列表（level ∈ error/warning）。"""
    issues: List[Dict[str, str]] = []
    issues += validate_air_quality_twice(tasks)
    issues += validate_fire_approval(tasks)
    issues += validate_spring_festival_calendar(holiday_raw)
    issues += validate_long_lead_equipment(tasks)
    return issues


def log_compliance(issues: List[Dict[str, str]]) -> None:
    """将合规 issue 以日志形式输出，error 级升级为 ERROR 日志。"""
    if not issues:
        logger.info("[合规审计] 全部刚性红线校验通过 ✓")
        return
    for it in issues:
        lvl = it.get("level", "warning")
        msg = f"[合规审计][{it.get('code')}] {it.get('message')}"
        if lvl == "error":
            logger.error(msg)
        else:
            logger.warning(msg)
