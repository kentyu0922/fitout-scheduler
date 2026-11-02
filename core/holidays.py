# -*- coding: utf-8 -*-
"""
holidays.py - 节假日统一加载 + 工作日工具模块（全 skill 唯一入口）

v3 约定：config/holidays.json 为扁平列表结构
    [ {"name": "春节大假", "start": "2027-02-06", "finish": "2027-02-20"}, ... ]

所有求解器 / MPP 生成器 / 诊断工具统一从此加载，消除四处重复实现导致的漂移。
文件缺失或解析失败时 fallback 内置默认表（仅保证不崩，不代表准确）。

v3 化繁为简：合并原 calendar_utils.py 的 is_client_workday / get_latest_client_workday_before
+ solver_engine.py 的 is_workday / build_holiday_bitmap / add_workdays 到此模块。

接口:
    load_holiday_pairs()                    -> [(start, end)]       纯区间
    load_holiday_raw()                      -> [{"name",...}]       原始
    is_workday(d, holiday_data=None)        -> bool                 工作日判断（兼容 bitmap/set/list/dict）
    build_holiday_bitmap(holiday_pairs)     -> Set[date]            节假日位图（O(1) 查找）
    add_workdays(start, n, holiday_bitmap)  -> date                 增加工作日
    get_latest_client_workday_before(...)   -> date                 倒排客户工作日
"""
import os
import json
import datetime
from datetime import timedelta
from typing import List, Dict, Any, Optional, Set, Tuple, Union
import logging

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE, "config", "holidays.json")

# Fallback mirrors sourced 2026 notices; future years remain explicit planning assumptions.
DEFAULT_HOLIDAYS = [{'name': '元旦', 'start': '2026-01-01', 'finish': '2026-01-03', 'basis_status': 'official_notice', 'basis': '国办发明电〔2025〕7号', 'source_url': 'https://www.gov.cn/zhengce/content/202511/content_7047090.htm'}, {'name': '春节', 'start': '2026-02-15', 'finish': '2026-02-23', 'basis_status': 'official_notice', 'basis': '国办发明电〔2025〕7号', 'source_url': 'https://www.gov.cn/zhengce/content/202511/content_7047090.htm'}, {'name': '清明节', 'start': '2026-04-04', 'finish': '2026-04-06', 'basis_status': 'official_notice', 'basis': '国办发明电〔2025〕7号', 'source_url': 'https://www.gov.cn/zhengce/content/202511/content_7047090.htm'}, {'name': '劳动节', 'start': '2026-05-01', 'finish': '2026-05-05', 'basis_status': 'official_notice', 'basis': '国办发明电〔2025〕7号', 'source_url': 'https://www.gov.cn/zhengce/content/202511/content_7047090.htm'}, {'name': '端午节', 'start': '2026-06-19', 'finish': '2026-06-21', 'basis_status': 'official_notice', 'basis': '国办发明电〔2025〕7号', 'source_url': 'https://www.gov.cn/zhengce/content/202511/content_7047090.htm'}, {'name': '中秋节', 'start': '2026-09-25', 'finish': '2026-09-27', 'basis_status': 'official_notice', 'basis': '国办发明电〔2025〕7号', 'source_url': 'https://www.gov.cn/zhengce/content/202511/content_7047090.htm'}, {'name': '国庆黄金周', 'start': '2026-10-01', 'finish': '2026-10-07', 'basis_status': 'official_notice', 'basis': '国办发明电〔2025〕7号', 'source_url': 'https://www.gov.cn/zhengce/content/202511/content_7047090.htm'}, {'name': '元旦', 'start': '2027-01-01', 'finish': '2027-01-03', 'basis_status': 'planning_assumption_incomplete', 'basis': '不完整（依据待补）：未来年度停工占位，规划假设；正式发布后更新，非已公布法定假期', 'source_url': None}, {'name': '春节元宵大假', 'start': '2027-02-06', 'finish': '2027-02-20', 'basis_status': 'planning_assumption_incomplete', 'basis': '不完整（依据待补）：未来年度停工占位，规划假设；正式发布后更新，非已公布法定假期', 'source_url': None}, {'name': '清明节', 'start': '2027-04-03', 'finish': '2027-04-05', 'basis_status': 'planning_assumption_incomplete', 'basis': '不完整（依据待补）：未来年度停工占位，规划假设；正式发布后更新，非已公布法定假期', 'source_url': None}, {'name': '劳动节', 'start': '2027-05-01', 'finish': '2027-05-05', 'basis_status': 'planning_assumption_incomplete', 'basis': '不完整（依据待补）：未来年度停工占位，规划假设；正式发布后更新，非已公布法定假期', 'source_url': None}, {'name': '端午节', 'start': '2027-06-09', 'finish': '2027-06-11', 'basis_status': 'planning_assumption_incomplete', 'basis': '不完整（依据待补）：未来年度停工占位，规划假设；正式发布后更新，非已公布法定假期', 'source_url': None}, {'name': '中秋节', 'start': '2027-09-15', 'finish': '2027-09-17', 'basis_status': 'planning_assumption_incomplete', 'basis': '不完整（依据待补）：未来年度停工占位，规划假设；正式发布后更新，非已公布法定假期', 'source_url': None}, {'name': '国庆黄金周', 'start': '2027-10-01', 'finish': '2027-10-07', 'basis_status': 'planning_assumption_incomplete', 'basis': '不完整（依据待补）：未来年度停工占位，规划假设；正式发布后更新，非已公布法定假期', 'source_url': None}]


def _read_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return data
    except Exception as exc:

        logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

    return None


def load_holiday_raw():
    """返回 [{"name","start","finish"}]，优先 config/holidays.json。"""
    cfg = _read_config()
    if cfg:
        return cfg
    return [dict(d) for d in DEFAULT_HOLIDAYS]


def load_holiday_pairs():
    """返回 [(start, end)] 纯区间（求解器用）。"""
    return [(d.get("start"), d.get("finish")) for d in load_holiday_raw() if d.get("start") and d.get("finish")]


# ==================== 工作日工具（合并自 calendar_utils + solver_engine） ====================

def build_holiday_bitmap(active_holidays: List[Tuple[str, str]]) -> Set[datetime.date]:
    """构建节假日缓存位图 (O(1) 查找复杂度)。

    从 solver_engine 迁入，接口不变。
    """
    bitmap: Set[datetime.date] = set()
    for h_start, h_end in active_holidays:
        d_start = datetime.datetime.strptime(h_start, "%Y-%m-%d").date()
        d_end = datetime.datetime.strptime(h_end, "%Y-%m-%d").date()
        curr = d_start
        while curr <= d_end:
            bitmap.add(curr)
            curr += timedelta(days=1)
    return bitmap


def is_workday(d: datetime.date, holiday_data: Optional[Union[Set[datetime.date], List, Tuple]] = None) -> bool:
    """判断是否为工作日 (周一至周五且非法定节假日)。

    合并自 solver_engine.is_workday + calendar_utils.is_client_workday。
    兼容多种 holiday_data 输入格式：
        - Set[datetime.date]  : 预构建位图（O(1) 查找，求解器热路径用）
        - List[Tuple[str,str]]: 区间对列表 [(start, end)]
        - List[Dict]          : 原始节假日列表 [{"start","finish"}]
        - None                : 仅判断周末
    """
    if d.weekday() >= 5:  # 周六周日
        return False
    if holiday_data is None:
        return True
    if isinstance(holiday_data, set):
        # 位图模式（O(1)）
        return d not in holiday_data
    # 列表模式（O(n) 逐区间扫描）
    d_str = d.strftime("%Y-%m-%d")
    for item in holiday_data:
        if isinstance(item, (tuple, list)):
            start, end = item[0], item[1]
            if start <= d_str <= end:
                return False
        elif isinstance(item, dict):
            start = item.get("start", "")
            end = item.get("finish", "")
            if start <= d_str <= end:
                return False
    return True


def add_workdays(start_date: datetime.date, workdays: int,
                 holiday_bitmap: Set[datetime.date]) -> datetime.date:
    """计算增加指定工作日后的日期。

    从 solver_engine 迁入，接口不变。
    """
    curr = start_date
    if workdays == 0:
        while not is_workday(curr, holiday_bitmap):
            curr += timedelta(days=1)
        return curr

    count = 0
    while count < workdays:
        curr += timedelta(days=1)
        if is_workday(curr, holiday_bitmap):
            count += 1
    return curr


def get_latest_client_workday_before(target_date: datetime.date,
                                     days_before: int = 7,
                                     holidays: Optional[List[Dict[str, str]]] = None) -> datetime.date:
    """获取目标日前 N 天内最合适的【客户工作日】。

    从 calendar_utils 迁入，接口不变。
    """
    cand = target_date - timedelta(days=days_before)
    while not is_workday(cand, holidays):
        cand -= timedelta(days=1)
    return cand

