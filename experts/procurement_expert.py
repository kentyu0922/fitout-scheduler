# -*- coding: utf-8 -*-
"""
experts/procurement_expert.py — 招采与长周期设备评估专家 (Procurement & Long-Lead Expert)
===================================================================================
核心职能：
1. 单源读取 config/long_lead_equipment.json (兼容 config/long_lead.json)；
2. 提供长周期机电、暖通、精装及办公家具的法定与行业基准交期查询 (Lead Time)；
3. 配合合规引擎 core/compliance.py，对 WBS 排期中的设备采购制造周期进行合规与风险断言。
"""

import os
import json
import logging
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH_PRIMARY = os.path.join(BASE_DIR, "config", "long_lead_equipment.json")
CONFIG_PATH_FALLBACK = os.path.join(BASE_DIR, "config", "long_lead.json")

_CACHE_CONFIG: Optional[Dict[str, Any]] = None


def load_long_lead_config() -> Dict[str, Any]:
    """单源加载长周期设备与系统家具交付周期配置数据库。"""
    global _CACHE_CONFIG
    if _CACHE_CONFIG is not None:
        return _CACHE_CONFIG

    target_path = CONFIG_PATH_PRIMARY if os.path.exists(CONFIG_PATH_PRIMARY) else CONFIG_PATH_FALLBACK
    if not os.path.exists(target_path):
        logger.warning(f"[procurement_expert] 未找到长周期设备配置文件: {CONFIG_PATH_PRIMARY}")
        return {}

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            _CACHE_CONFIG = json.load(f)
            logger.info(f"[procurement_expert] 成功加载长周期设备基准库: {os.path.basename(target_path)}")
            return _CACHE_CONFIG
    except Exception as ex:
        logger.error(f"[procurement_expert] 加载长周期设备配置失败: {ex}")
        return {}


def get_all_long_lead_items() -> List[Tuple[str, str, int, str]]:
    """返回所有已登记的长周期设备扁平列表: [(分类, 键名, 基准交期工日, 描述), ...]"""
    cfg = load_long_lead_config()
    items = []
    for cat, sub in cfg.items():
        if isinstance(sub, dict):
            for k, info in sub.items():
                if isinstance(info, dict):
                    items.append((cat, k, info.get("lead_days", 0), info.get("name", k)))
    return items


def match_task_to_long_lead_equipment(task_name: str) -> Optional[Dict[str, Any]]:
    """根据任务名称模糊匹配是否属于已收录的长周期设备项。
    
    例如: "进口冷水机组订购" -> 匹配到 Chiller_Imported (120工日)
    """
    cfg = load_long_lead_config()
    task_name_lower = task_name.lower()

    for cat, sub in cfg.items():
        if not isinstance(sub, dict):
            continue
        for key, info in sub.items():
            if not isinstance(info, dict):
                continue
            eq_name = info.get("name", "")
            # 匹配中英文关键词
            if (eq_name and eq_name in task_name) or (key.lower() in task_name_lower):
                return {
                    "category": cat,
                    "key": key,
                    "name": eq_name or key,
                    "lead_days_benchmark": info.get("lead_days", 0),
                    "desc": info.get("desc", "")
                }
    return None


def audit_tasks_long_lead_duration(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """对 WBS 任务中出现的长周期设备采购周期进行合规性审计。
    
    若排期中给定的交货工期明显低于基准交期（低于基准的 60%），则触发预警。
    """
    issues = []
    for t in tasks:
        t_name = t.get("name", "")
        duration = t.get("duration_days", t.get("duration", 0))
        match = match_task_to_long_lead_equipment(t_name)
        if match:
            benchmark = match["lead_days_benchmark"]
            # 若不是纯里程碑且工期严重不足
            if duration > 0 and duration < benchmark * 0.6:
                issues.append({
                    "task_id": t.get("id"),
                    "task_name": t_name,
                    "equipment": match["name"],
                    "allocated_days": duration,
                    "benchmark_days": benchmark,
                    "category": match["category"],
                    "message": (
                        f"任务 [{t_name}] 分配交期为 {duration} 工作日，"
                        f"低于行业基准推荐交期 {benchmark} 工作日（{match['desc']}），存在长周期断货风险！"
                    )
                })
    return issues
