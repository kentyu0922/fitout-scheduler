# -*- coding: utf-8 -*-
"""
solver_engine.py - 高级 PMP 全年限动态节假日 CPM 求解引擎 (v3 单源化)

v3 关键变更：
  * 节假日统一由 config/holidays.json 经 core/holidays 加载，删除原 GLOBAL_HOLIDAY_REGISTRY 双源。
  * 新增 compute_cpm_metrics()：在正向解算结果上做反向遍历，估算总时差与关键路径，
    供离线预览；MPP 仍为负责任务日期与浮时的最终权威（保留委托，见 SKILL.md）。
"""
import sys
import os
import json
import copy
import datetime
import calendar
from datetime import timedelta
import re
from typing import List, Dict, Any, Optional, Set, Tuple

# v3 单源化：节假日统一从 config/holidays.json 加载（core/holidays 为唯一入口）
# 化繁为简：is_workday / build_holiday_bitmap / add_workdays 从 holidays 导入
try:
    from core.holidays import load_holiday_pairs, load_holiday_raw, is_workday, build_holiday_bitmap, add_workdays
except Exception:  # pragma: no cover - 兜底，保证独立运行不崩
    def load_holiday_pairs():
        return []
    def load_holiday_raw():
        return []
    def build_holiday_bitmap(active_holidays):
        return set()
    def is_workday(dt, holiday_bitmap):
        if dt.weekday() in (5, 6):
            return False
        return dt not in holiday_bitmap
    def add_workdays(start_date, workdays, holiday_bitmap):
        import datetime as _dt
        from datetime import timedelta as _td
        curr = start_date
        if workdays == 0:
            while not is_workday(curr, holiday_bitmap):
                curr += _td(days=1)
            return curr
        count = 0
        while count < workdays:
            curr += _td(days=1)
            if is_workday(curr, holiday_bitmap):
                count += 1
        return curr

# 统一提取高频常量，消除魔法字符串
RELOCATION_KEYWORDS = {"搬家", "搬迁", "正式入驻", "Relocation"}
CONSTRUCTION_PHASE_KEYWORDS = {"四", "五", "六", "施工", "Construction", "搬迁", "Relocation"}
EXCLUDE_CONSTRUCTION_KEYWORDS = {"图", "招", "前", "验收", "审批", "结算"}
# 政府审批/报建节点（施工许可证办理、图审合格证、质安监报监等）为法定行政行为，
# 周末与法定节假日均不办公 → 强制 5 天日历，绝不可套用施工7天日历。
# 与 mpp_renderer 物理渲染一致：MPP 侧该类叶子任务无 work_weekend 标记，落标准5天日历。
GOV_APPROVAL_KEYWORDS = {"施工许可证", "许可证办理", "图审合格证", "图审机构审查", "第三方图审", "质安监报监", "施工许可行政审批"}

# 预编译前置任务正则表达式
PREDECESSOR_REGEX = re.compile(r'^(\d+)(FS|FF|SS|SF)?([+-]\d+)?')


def parse_predecessor_token(part: str) -> Optional[Tuple[int, str, int]]:
    """Parse one predecessor token ("57", "58SS+3", "63FF", "5FS-2d") → (id, type, lag).

    Same normalisation as the forward pass (upper-case, strip spaces and day suffixes) so
    the backward pass in compute_cpm_metrics sees exactly the links solve_schedule used.
    """
    clean_part = str(part).strip().upper().replace(' ', '').replace('DAYS', '').replace('DAY', '').replace('D', '')
    match = PREDECESSOR_REGEX.match(clean_part)
    if not match:
        return None
    return int(match.group(1)), (match.group(2) or "FS"), (int(match.group(3)) if match.group(3) else 0)

def get_holidays_for_years(start_year: int, end_year: int, custom_holidays: Optional[List] = None) -> List[Tuple[str, str]]:
    """
    v3 单源：从 config/holidays.json 加载全量法定节假日区间（已由 core/holidays 合并跨年）。
    custom_holidays 作为可选覆盖/追加（main.py 传入同一份 config，确保与 MPP 日历一致）。
    """
    holiday_list: List[Tuple[str, str]] = list(load_holiday_pairs())
    if custom_holidays:
        for c in custom_holidays:
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                holiday_list.append((c[0], c[1]))
    return holiday_list


# ==================== 模式感知工作日工具（修复 7天施工日历 与 MPP 不同步） ====================
# 旧逻辑: work_weekend 任务传 set() 空位图 -> 既忽略周末也忽略法定节假日, 与 MPP「施工7天日历」
# (周末工作 + 法定节假日停工) 不一致, 导致施工阶段求解器日期比 MPP 早 ~14 天(春节长度)。
# 中途修正: 改为传全量 holiday_bitmap -> 但 MPP 施工日历「仅春节停工」, 元旦/国庆照常施工,
#   仍会过修正 ~6 天(求解器比 MPP 晚)。
# 最终逻辑(与 mpp_renderer 物理注入的「施工7天日历」严格一致): 三种模式
#   ignore_h     : 24小时连续作业, 任意日皆工作
#   work_weekend : 7天施工日历 -> 周末工作, 且仅春节(含元宵返岗)停工, 其余法定节假日照常施工
#   standard     : 标准5天 -> 周末与全部法定节假日均停工
def _is_working_day(d: datetime.date, ignore_h: bool, work_weekend: bool, holiday_bitmap, spring_festival_bitmap, cal_mode: str = '7D') -> bool:
    if ignore_h:
        return True
    if cal_mode == "WEEKEND":
        if d.weekday() not in (5, 6):
            return False
        return d not in spring_festival_bitmap
    if work_weekend:
        # P1-12 construction calendar profiles:
        #   7D: weekends working, only Spring Festival off (locked policy)
        #   6D: Sunday off, statutory holidays working except Spring Festival
        #   5D: construction falls back to the standard client workweek
        if cal_mode == '6D':
            if d.weekday() == 6:            # Sunday
                return False
            return d not in spring_festival_bitmap
        if cal_mode == '5D':
            return d.weekday() < 5 and d not in holiday_bitmap
        return d not in spring_festival_bitmap
    if d.weekday() >= 5:             # 周末
        return False
    # 周一~周五
    return d not in holiday_bitmap                # 标准5天: 全部法定节假日停工


def _add_working_days(start: datetime.date, n: int, ignore_h: bool, work_weekend: bool, holiday_bitmap, spring_festival_bitmap, cal_mode: str = '7D') -> datetime.date:
    """从 start 起推进 n 个工作日 (n 可负); 工作日定义见 _is_working_day。n==0 时返回首个工作日(含 start)。"""
    if ignore_h:
        return start + timedelta(days=n)
    if n == 0:
        cur = start
        while not _is_working_day(cur, ignore_h, work_weekend, holiday_bitmap, spring_festival_bitmap, cal_mode):
            cur += timedelta(days=1)
        return cur
    sign = 1 if n > 0 else -1
    cnt = 0
    cur = start
    while cnt < abs(n):
        cur += timedelta(days=sign)
        if _is_working_day(cur, ignore_h, work_weekend, holiday_bitmap, spring_festival_bitmap, cal_mode):
            cnt += 1
    return cur


def build_calendar_bitmaps(start_year: int, custom_holidays: Optional[List] = None) -> Tuple[Set[datetime.date], Set[datetime.date]]:
    """
    返回 (holiday_bitmap, spring_festival_bitmap)，供正向解算与优化器反向遍历共用同一套日历口径。

    holiday_bitmap        : 全部法定节假日（标准5天日历停工）
    spring_festival_bitmap: 仅春节(含元宵返岗)区间（施工7天日历唯一停工例外，与 mpp_renderer 注入策略一致）
    """
    active_holidays = get_holidays_for_years(start_year, start_year + 5, custom_holidays)
    holiday_bitmap = build_holiday_bitmap(active_holidays)
    spring_festival_raw = [h for h in load_holiday_raw() if "春节" in (h.get("name", "") or "")]
    spring_festival_bitmap = build_holiday_bitmap(
        [(h["start"], h["finish"]) for h in spring_festival_raw if h.get("start") and h.get("finish")]
    )
    return holiday_bitmap, spring_festival_bitmap


def dependency_boundary(pred_date, pred_boundary, successor_duration, link, lag, add):
    """Resolve a whole-workday dependency without discarding 08:00/17:00 boundaries.

    FIX-01 cross-calendar boundary: a ZERO-DURATION successor (milestone, or an
    UNRESOLVED conditional placeholder) sits exactly at its predecessor's finish
    and is NOT re-snapped into the successor's calendar. MS Project keeps a
    milestone at the predecessor's finish instant, and snapping it forward moved
    milestones off the date MS Project reports — a solver/MPP drift. With
    per-activity calendars (site 7D followed by a 5D review activity) the
    predecessor can legitimately finish on a day the successor's calendar does
    not work, so this case is no longer rare.
    """
    boundary = pred_boundary
    base = pred_date
    if successor_duration == 0:
        # add(x, 0) snaps to the next working day — exactly what must NOT happen
        # for a zero-duration successor.
        at_predecessor = base if not lag else add(base, lag)
        return at_predecessor, (boundary if boundary == 'end_of_day' else 'start_of_day')
    if link in ('FS', 'SS') and boundary == 'end_of_day':
        base += timedelta(days=1)
        boundary = 'start_of_day'
    snapped = add(base, 0)
    if snapped != base:
        boundary = 'start_of_day'
    base = snapped
    return add(base, lag), boundary


def solve_schedule(tasks: List[Dict[str, Any]], project_start_str: str, custom_holidays: Optional[List] = None, construction_calendar_mode: str = '7D') -> Dict[str, Any]:
    """
    执行 CPM 网络计划正向推演（v3：节假日单源）。

    P1-12: construction_calendar_mode 选定施工日历档位（5D/6D/7D，默认 7D 兼容
    锁定的双日历策略）。仅影响 work_weekend（施工日历）任务；设计/政府节点
    恒为客户工作周日历，不受档位影响。
    """
    if construction_calendar_mode not in ('5D', '6D', '7D'):
        raise ValueError(f"construction_calendar_mode must be one of 5D/6D/7D, got {construction_calendar_mode}")
    cal_mode = construction_calendar_mode
    proj_start = datetime.date.fromisoformat(project_start_str)
    
    # 动态评估可能跨越的年份跨度 (如从项目开始年份后推 5 年)
    # 春节(含元宵返岗)停工位图 —— MPP「施工7天日历」仅对春节停工, 其余法定节假日照常施工。
    # 与 mpp_renderer 物理注入的施工日历例外策略完全一致, 杜绝求解器/MPP 二次漂移。
    holiday_bitmap, spring_festival_bitmap = build_calendar_bitmaps(proj_start.year, custom_holidays)

    # 建立 ID 到 Task 的字典
    task_dict = {t["id"]: t for t in tasks}
    dates = {}

    from core.module_graph import dependency_order
    phase_by_id = {}
    display_phase = ''
    for task in tasks:
        if task.get('outline_level', task.get('level', 3)) <= 2:
            display_phase = task.get('name','')
        phase_by_id[task['id']] = display_phase
    current_phase_name = ""
    for t in dependency_order(tasks):
        current_phase_name = phase_by_id[t['id']]
        tid = t["id"]
        level = t.get("outline_level", t.get("level", 3))
        dur = int(t.get("duration_days", t.get("duration", 0)) or 0)
        name_str = str(t.get("name", ""))
        preds_str = str(t.get("predecessors", ""))

        if level <= 2:
            current_phase_name = name_str
            continue

        cal_setting = str(t.get("calendar", ""))
        phase_str = str(t.get("phase", "")) or current_phase_name
        
        # 大原则自动判定：现场施工与竣工阶段默认套用 施工7天日历
        is_phase_4_5_6 = any(kw in current_phase_name for kw in CONSTRUCTION_PHASE_KEYWORDS) and not any(kw in current_phase_name for kw in EXCLUDE_CONSTRUCTION_KEYWORDS)
        
        # 例外规则：【正式搬迁/搬家】节点必须在客户工作日进行！
        is_relocation = any(kw in name_str for kw in RELOCATION_KEYWORDS)
        # 例外规则：【政府审批/报建】节点（施工许可证办理、图审合格证、质安监报监等）为法定行政行为，
        # 周末与法定节假日均不办公 → 强制 5 天日历，不得套用施工7天日历。
        is_gov_approval = any(kw in current_phase_name for kw in GOV_APPROVAL_KEYWORDS) or any(kw in name_str for kw in GOV_APPROVAL_KEYWORDS)

        ignore_h = t.get("ignore_holidays", False) or cal_setting in ["24小时", "24Hours", "24 Hours"]

        if is_relocation or is_gov_approval:
            work_weekend = False
        else:
            work_weekend = t.get("work_weekend", False) or cal_setting in ["施工日历", "施工7天日历", "7天8小时", "施工7天"] or (is_phase_4_5_6 and not cal_setting and not t.get("is_standard_calendar_patch", False))

        # FIX-01: ACTIVITY CALENDAR. When main.py has resolved a per-activity
        # calendar, it (and only it) drives this task's working-week pattern.
        # The construction calendar mode must never propagate to design / tender /
        # client approval / authority review activities.
        task_cal = cal_mode
        ac = t.get("activity_calendar")
        if isinstance(ac, dict) and ac.get("calendar_mode"):
            task_cal = ac["calendar_mode"]
            work_weekend = int(ac.get("working_days_per_week", 5)) > 5
        if is_relocation or is_gov_approval:
            work_weekend = False

        weekend_only = (
            str(t.get("calendar_window") or "").lower() in ("weekend", "weekend_only")
            or str(cal_setting).upper() == "WEEKEND"
            or "周末全量割接" in name_str
        )
        if weekend_only:
            task_cal = "WEEKEND"
            work_weekend = True
        # 模式: ignore_h=24h连续 / work_weekend=7天施工(周末工作, 法定节假日停工) / standard=5天
        early_start = proj_start
        min_finish_constraint = None
        start_boundary = 'start_of_day'
        finish_constraint_boundary = 'start_of_day'

        if preds_str:
            parts = [p.strip() for p in preds_str.split(",")]
            max_start_from_preds = proj_start

            for part in parts:
                if not part: continue
                parsed = parse_predecessor_token(part)
                if not parsed: continue
                pid, link_type, lag_val = parsed

                if pid in dates:
                    p_start = datetime.date.fromisoformat(dates[pid]["start"])
                    p_finish = datetime.date.fromisoformat(dates[pid]["finish"])

                    if link_type in ('FS', 'SS', 'FF'):
                        pred_task = task_dict[pid]
                        pred_dur = float(pred_task.get('duration_days', pred_task.get('duration', 0)) or 0)
                        anchor = p_start if link_type == 'SS' else p_finish
                        boundary = dates[pid].get('start_boundary' if link_type == 'SS' else 'finish_boundary',
                                                 'start_of_day' if link_type == 'SS' or pred_dur == 0 else 'end_of_day')
                        add = lambda d, n: _add_working_days(d, n, ignore_h, work_weekend, holiday_bitmap, spring_festival_bitmap, task_cal)
                        target, boundary = dependency_boundary(anchor, boundary, dur, link_type, lag_val, add)
                        rank = lambda d, b: (d, b == 'end_of_day')
                        if link_type == 'FF':
                            if min_finish_constraint is None or rank(target, boundary) > rank(min_finish_constraint, finish_constraint_boundary):
                                min_finish_constraint, finish_constraint_boundary = target, boundary
                        elif rank(target, boundary) > rank(max_start_from_preds, start_boundary):
                            max_start_from_preds, start_boundary = target, boundary
                        continue
            if max_start_from_preds > early_start:
                early_start = max_start_from_preds
        else:
            early_start = _add_working_days(early_start, 0, ignore_h, work_weekend, holiday_bitmap, spring_festival_bitmap, task_cal)

        if dur == 0:
            early_finish = early_start
        else:
            early_finish = _add_working_days(early_start, dur - 1, ignore_h, work_weekend, holiday_bitmap, spring_festival_bitmap, task_cal)
                
        # 应用 FF 结束约束
        if min_finish_constraint and (min_finish_constraint, finish_constraint_boundary == 'end_of_day') > (early_finish, start_boundary == 'end_of_day' if dur == 0 else True):
            early_finish = min_finish_constraint
            # 反算 early_start
            curr = early_finish
            count = 0
            target_days = dur - 1 if dur > 0 else 0
            while count < target_days:
                curr -= timedelta(days=1)
                if _is_working_day(curr, ignore_h, work_weekend, holiday_bitmap, spring_festival_bitmap, task_cal):
                    count += 1
            early_start = curr
            if dur == 0:
                start_boundary = finish_constraint_boundary

        dates[tid] = {
            "start": early_start.strftime("%Y-%m-%d"),
            "finish": early_finish.strftime("%Y-%m-%d"),
            "work_weekend": work_weekend,
            "start_boundary": start_boundary,
            "finish_boundary": start_boundary if dur == 0 else 'end_of_day'
        }

    # 动态分级向上汇总 Summary Tasks (多级大纲树自底向上向上归纳)
    n = len(tasks)
    is_summary = [False] * n
    for i in range(n):
        lvl = tasks[i].get("outline_level", tasks[i].get("level", 1))
        if i + 1 < n:
            next_lvl = tasks[i+1].get("outline_level", tasks[i+1].get("level", 1))
            if next_lvl > lvl:
                is_summary[i] = True

    # 自底向上递归汇总：汇总节点的 Start 取所有后代叶节点的 min(start)，Finish 取 max(finish)
    for i in range(n - 1, -1, -1):
        if is_summary[i]:
            parent_lvl = tasks[i].get("outline_level", tasks[i].get("level", 1))
            child_starts = []
            child_finishes = []
            for j in range(i + 1, n):
                c_lvl = tasks[j].get("outline_level", tasks[j].get("level", 1))
                if c_lvl <= parent_lvl:
                    break
                if not is_summary[j] and tasks[j]["id"] in dates:
                    child_starts.append(dates[tasks[j]["id"]]["start"])
                    child_finishes.append(dates[tasks[j]["id"]]["finish"])
            if child_starts:
                min_s = min(child_starts)
                max_f = max(child_finishes)
                dates[tasks[i]["id"]] = {"start": min_s, "finish": max_f, "work_weekend": False}

    # 更新 tasks
    for t in tasks:
        tid = t["id"]
        if tid in dates:
            t["start"] = dates[tid]["start"]
            t["finish"] = dates[tid]["finish"]
            t["use_construction_cal"] = dates[tid].get("work_weekend", False)
            t['start_boundary'] = dates[tid].get('start_boundary', 'start_of_day')
            t['finish_boundary'] = dates[tid].get('finish_boundary', 'end_of_day')

    finish_date = max(d["finish"] for d in dates.values()) if dates else project_start_str
    return {
        "start_date": project_start_str,
        "finish_date": finish_date,
        "tasks": tasks
    }


def compute_cpm_metrics(tasks: List[Dict[str, Any]], project_end: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    v3 离线关键路径估算（工作日轴反向遍历 + 总时差 + 关键路径标记）。

    说明：基于 solve_schedule 已求出的 start/finish，在「工作日轴」上做反向遍历，给出离线
    预览用的总时差与关键路径。所有日期换算统一走工作日口径，避免周末虚增/虚减浮时。
    MPP 在最终写盘时仍会对任务日期与浮时做权威重算（保留委托）。
    假设前提：任务列表按拓扑顺序（id 升序、前置指向更早 id）。

    反向遍历按链接类型区分（与正向 solve_schedule 口径一致）：
      FS+lag : LF(P) = LS(S) - 1 - lag；P 为 0 工期里程碑时后继同日开始 → LF(P) = LS(S) - lag
      SS+lag : LS(P) = LS(S) - lag  → LF(P) = LS(S) - lag + dur(P) - 1
      FF+lag : LF(P) = LF(S) - lag
    否则 SS 前置会被当作 FS 处理而得到虚假负浮时（分区 SS+lag 规则下尤为明显）。
    """
    dated = [t for t in tasks if t.get("start") and t.get("finish")]
    if not dated:
        return tasks

    def to_date(x):
        return datetime.date.fromisoformat(x)

    bitmap = build_holiday_bitmap(load_holiday_pairs())
    es = {t["id"]: to_date(t["start"]) for t in dated}
    ef = {t["id"]: to_date(t["finish"]) for t in dated}
    proj_start = min(es.values())
    proj_end_dt = to_date(project_end) if project_end else max(ef.values())

    # 工作日轴：index = 自项目起点(含)起的第几个工作日；起点为 0
    def wd_index(d: datetime.date) -> int:
        n = 0
        cur = proj_start
        while cur <= d:
            if is_workday(cur, bitmap):
                n += 1
            cur += timedelta(days=1)
        return n - 1

    es_wd = {i: wd_index(es[i]) for i in es}
    dur_axis = {t["id"]: max(1, int(t.get("duration", 0) or 0)) for t in dated}
    ef_wd = {i: es_wd[i] + dur_axis[i] - 1 for i in es}
    proj_end_wd = max(ef_wd.values())

    # 后继映射：pid -> [(sid, link_type, lag)]
    # Only zero-duration milestones release on the same date; a one-day task
    # occupies its entire workday even though its start and finish dates match.
    same_day_pred = {t["id"]: float(t.get('duration_days', t.get('duration', 0)) or 0) == 0 for t in dated}
    succ: Dict[int, List[Tuple[int, str, int]]] = {t["id"]: [] for t in dated}
    for t in dated:
        for p in str(t.get("predecessors", "")).split(","):
            p = p.strip()
            if not p:
                continue
            parsed = parse_predecessor_token(p)
            if parsed:
                pid, link_type, lag = parsed
                if pid in succ and pid != t["id"]:
                    succ[pid].append((t["id"], link_type, lag))

    ls_wd: Dict[int, int] = {}
    lf_wd: Dict[int, int] = {}
    from core.module_graph import dependency_order
    for t in reversed([n for n in dependency_order(tasks) if n.get("start") and n.get("finish")]):
        i = t["id"]
        candidates: List[int] = []
        for s, link_type, lag in succ[i]:
            if s not in ls_wd:
                continue
            if link_type == "SS":
                candidates.append(ls_wd[s] - max(lag, 0) + dur_axis[i] - 1)
            elif link_type == "FF":
                candidates.append(lf_wd[s] - max(lag, 0))
            else:  # FS (SF treated as FS)
                successor = next(x for x in dated if x['id'] == s)
                successor_zero = float(successor.get('duration_days', successor.get('duration', 0)) or 0) == 0
                end_boundary = t.get('finish_boundary', 'start_of_day' if same_day_pred[i] else 'end_of_day') == 'end_of_day'
                gap = int(end_boundary and not successor_zero)
                candidates.append(ls_wd[s] - gap - lag)
        if candidates:
            lf_wd[i] = min(candidates)
        else:
            lf_wd[i] = proj_end_wd
        ls_wd[i] = lf_wd[i] - (dur_axis[i] - 1)
        slack = ls_wd[i] - es_wd[i]
        t["total_slack_days"] = slack
        t["critical"] = (slack <= 0)

    critical_ids = [t["id"] for t in dated if t.get("critical")]
    logger = _get_logger()
    logger.info(f"[CPM估算] 关键路径节点数={len(critical_ids)}，项目终点={proj_end_dt.isoformat()}")
    return tasks


def _get_logger():
    import logging
    return logging.getLogger("solver_engine")


def _find_target_task(tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    倒排目标锚定：返回项目中【真正最晚完成】的任务（即项目末端交付里程碑），
    而非第一个含 '搬家/搬迁' 关键词的任务。

    旧实现用 RELOCATION_KEYWORDS 取首个匹配，会误抓早期含搬迁词的规划/动员任务，
    导致倒排把错误的早期任务对齐到死线、真正项目末端被推后数月。
    项目末端 = finish 字典序最大者（ISO 日期串可直接比较），稳健且无需依赖关键词。
    """
    best = None
    for t in tasks:
        f = t.get("finish")
        if not f:
            continue
        if best is None or f > best.get("finish", ""):
            best = t
    return best


def move_in_date(tasks: List[Dict[str, Any]]) -> Optional[datetime.date]:
    """The move-in date a PM's target refers to: start of the client's move (handover step 4).

    Falls back to the programme end for networks without a handover module.
    """
    move = next((t for t in tasks if t.get('module_id') == 'core_handover' and t.get('module_step') == 4), None)
    day = move and (move.get('start') or move.get('start_date'))
    if day:
        return datetime.date.fromisoformat(day)
    return _latest_finish(tasks)


def _latest_finish(tasks: List[Dict[str, Any]]) -> Optional[datetime.date]:
    best = _find_target_task(tasks)
    if not best or not best.get("finish"):
        return None
    return datetime.date.fromisoformat(best["finish"])


def _forward_solve(tasks: List[Dict[str, Any]], kickoff: datetime.date,
                   active_holidays: Optional[List], construction_calendar_mode: str) -> Dict[str, Any]:
    return solve_schedule(
        copy.deepcopy(tasks), kickoff.strftime("%Y-%m-%d"),
        active_holidays, construction_calendar_mode=construction_calendar_mode,
    )


def solve_schedule_with_target(tasks: List[Dict[str, Any]], target_finish_date_str: str, target_buffer_days: int = 10, active_holidays: Optional[List] = None, construction_calendar_mode: str = '7D') -> Dict[str, Any]:
    """Latest feasible Kick-off such that the move-in is on or before the planning target.

    Uses the same forward solver for every candidate. Calendar-day translation is only a
    seed; the search walks later starts while the target is still met.
    """
    target_dt = datetime.date.fromisoformat(target_finish_date_str)

    if not active_holidays:
        active_holidays = get_holidays_for_years(target_dt.year - 3, target_dt.year + 3)
    holiday_bitmap = build_holiday_bitmap(active_holidays)

    move_date = target_dt - timedelta(days=target_buffer_days)
    while not is_workday(move_date, holiday_bitmap):
        move_date -= timedelta(days=1)

    dummy_start = datetime.date(2026, 8, 1)
    res_dummy = _forward_solve(tasks, dummy_start, active_holidays, construction_calendar_mode)
    d_finish_dummy = move_in_date(res_dummy["tasks"])
    d_start_dummy = datetime.date.fromisoformat(res_dummy["start_date"])
    if d_finish_dummy is None:
        raise ValueError("backward schedule: dummy forward solve produced no finish date")

    seed = d_start_dummy + timedelta(days=(move_date - d_finish_dummy).days)
    while not is_workday(seed, holiday_bitmap):
        seed -= timedelta(days=1)

    def meets(kick: datetime.date) -> Tuple[bool, Dict[str, Any]]:
        res = _forward_solve(tasks, kick, active_holidays, construction_calendar_mode)
        fin = move_in_date(res["tasks"])
        ok = fin is not None and fin <= move_date
        return ok, res

    lo = seed
    ok, res = meets(lo)
    span = 14
    while not ok and span <= 720:
        lo = lo - timedelta(days=span)
        ok, res = meets(lo)
        span *= 2

    if not ok:
        res["target_move_date"] = move_date.strftime("%Y-%m-%d")
        res["target_deadline"] = target_finish_date_str
        res["target_met"] = False
        res["latest_feasible_start"] = None
        res["backward_search"] = "no_feasible_start_in_search_window"
        return res

    hi = move_date
    best = res
    best_start = datetime.date.fromisoformat(res["start_date"])
    while (hi - lo).days > 1:
        mid = lo + timedelta(days=(hi - lo).days // 2)
        ok_mid, res_mid = meets(mid)
        if ok_mid:
            lo = mid
            best = res_mid
            best_start = datetime.date.fromisoformat(res_mid["start_date"])
        else:
            hi = mid

    # Confirm the last accepted start and one day later (latest, not earliest).
    ok_lo, res_lo = meets(lo)
    if ok_lo:
        best = res_lo
        best_start = datetime.date.fromisoformat(res_lo["start_date"])
    for _ in range(7):
        probe = best_start + timedelta(days=1)
        ok_p, res_p = meets(probe)
        if not ok_p:
            break
        best = res_p
        best_start = datetime.date.fromisoformat(res_p["start_date"])

    best["target_move_date"] = move_date.strftime("%Y-%m-%d")
    best["target_deadline"] = target_finish_date_str
    best["target_met"] = True
    best["latest_feasible_start"] = best["start_date"]
    best["backward_search"] = "latest_feasible_forward_verified"
    return best

if __name__ == "__main__":
    if len(sys.argv) > 1:
        input_json = sys.argv[1]
        if os.path.exists(input_json):
            with open(input_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            res = solve_schedule(data.get("tasks", []), data.get("project_start", "2026-09-01"))
            # v3 修复：默认输出 .solved.json，永不原地覆盖输入文件
            if "output_json" in data:
                out_file = data["output_json"]
            else:
                base, _ = os.path.splitext(input_json)
                out_file = base + ".solved.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            print(f"[solver_engine] Solved schedule successfully. Finish date: {res['finish_date']} -> {out_file}")
