# -*- coding: utf-8 -*-
"""
core/_common.py — 统一共享层

收纳被 mpp_renderer / msp_automation / main / solver_engine / sub_wbs_splicer
重复使用的常量、正则和工具函数，消除四处拷贝导致的漂移风险。

接口:
    CONSTRUCTION_CAL_NAME           施工7天日历常量
    parse_predecessor(pred_str)     前置任务字符串解析
    create_task_with_outline(...)   MS Project COM 任务创建 + 大纲缩进状态机
    MSProjectSession                (re-export) COM 会话上下文管理器
"""
import re
import logging
from typing import List, Tuple, Dict, Any, Optional

# ---------------------- 常量 ----------------------

CONSTRUCTION_CAL_NAME = "施工7天日历"
# FIX-01: factory production runs on its own provisional 6-day calendar; it must
# not silently inherit the site construction calendar.
FACTORY_CAL_NAME = "工厂6天日历"
LOGISTICS_CAL_NAME = "物流7天日历"
WEEKEND_CAL_NAME = "周末割接日历"

# P1-01: an UNRESOLVED conditional activity is NOT a completion milestone. This
# marker is prepended to the physical MPP display name so that (a) the reader
# can see the activity is still open and (b) read-back renderers (PDF) can
# distinguish it from a genuine zero-duration contractual milestone.
UNRESOLVED_MARK = "[未决] "

# 摘要级施工日历触发关键词（与 mpp_renderer 原硬编码一致）
_DEFAULT_WEEKEND_KEYWORDS = ["施工", "Phase 4", "苏州DBB"]

# 政府审批/报建阶段排除词：施工许可证办理、图审合格证、质安监报监等属法定行政行为，
# 周末与法定节假日均不办公 → 不得套用施工7天日历（强制5天）。与 solver_engine
# 的 GOV_APPROVAL_KEYWORDS 对齐，消除“摘要名含‘施工’误套7天”的漂移。
_GOV_APPROVAL_EXCLUDE_KEYWORDS = ["施工许可证", "许可证办理", "图审合格证", "质安监报监", "审批"]

# ---------------------- 里程碑 / 未决占位语义 ----------------------

def is_unresolved_placeholder(task_dict: Dict[str, Any]) -> bool:
    """True when a task is an UNRESOLVED conditional activity, not a real task.

    P1-01: such activities carry zero *committed* duration but are NOT
    milestones. They are detected by any of three explicit signals set by the
    conditional pipeline, never by duration alone.
    """
    return bool(
        task_dict.get("unresolved_placeholder")
        or task_dict.get("duration_status") == "UNRESOLVED"
        or task_dict.get("conditional_requirement")
    )


def is_true_milestone(task_dict: Dict[str, Any]) -> bool:
    """Explicit milestone semantics (P1-01).

    A task is a milestone ONLY when it is NOT an UNRESOLVED conditional
    activity AND it is either explicitly flagged ``milestone`` or has a genuine
    zero committed duration. ``duration == 0`` alone must never turn an
    unresolved physical task (template 14d / 8d) into a completion diamond.

    The UNRESOLVED guard takes precedence over the template ``milestone`` flag:
    a template completion event (e.g. "[M] 取得消防验收合格意见书") whose
    underlying requirement chain is UNKNOWN is a *conditional* activity, not a
    committed completion point, and must not be rendered as a diamond.

    A scope-exclusion placeholder (demolition / waste under UNKNOWN site
    condition, or an explicitly excluded trade) is likewise an unresolved
    physical activity, never a completion milestone.
    """
    if is_unresolved_placeholder(task_dict):
        return False
    if task_dict.get("scope_exclusion"):
        return False
    if task_dict.get("milestone"):
        return True
    raw = task_dict.get("duration_days", task_dict.get("duration", 0))
    try:
        dur = int(round(float(raw))) if raw not in (None, "") else 0
    except (TypeError, ValueError):
        dur = 0
    return dur == 0


# ---------------------- 前置任务解析 ----------------------

_PRED_RE = re.compile(r'^(\d+)(FS|FF|SS|SF)?([+-]?\d+)?')


def parse_predecessor(pred_str: str) -> List[Tuple[int, str, int]]:
    """解析前置任务字符串，返回 [(task_id, link_type, lag_days), ...]。

    支持 MSP 原生语法：
        "8"          -> (8, "FS", 0)
        "8SS+3"      -> (8, "SS", 3)
        "5FF-1"      -> (5, "FF", -1)
        "8SS+3,5FF"  -> [(8, "SS", 3), (5, "FF", 0)]

    Args:
        pred_str: 逗号分隔的前置任务字符串

    Returns:
        List of (task_id, link_type, lag_days) tuples
    """
    if not pred_str or not pred_str.strip():
        return []
    results: List[Tuple[int, str, int]] = []
    for part in pred_str.split(","):
        part = part.strip()
        if not part:
            continue
        m = _PRED_RE.match(part.upper())
        if not m or not m.group(1):
            continue
        pid = int(m.group(1))
        link_type = m.group(2) or "FS"
        lag = int(m.group(3)) if m.group(3) else 0
        results.append((pid, link_type, lag))
    return results


def split_predecessor_id_suffix(pred_str: str) -> List[Tuple[int, str]]:
    """轻量拆分：返回 [(task_id, suffix), ...]，suffix 为数字后面的原始字符串。

    供 sub_wbs_splicer / main.py 的 ID 重映射使用（只需 id + 原始后缀）。
    """
    if not pred_str or not pred_str.strip():
        return []
    results: List[Tuple[int, str]] = []
    for part in pred_str.split(","):
        part = part.strip()
        if not part:
            continue
        _i = 0
        while _i < len(part) and part[_i].isdigit():
            _i += 1
        if _i == 0:
            continue
        pid = int(part[:_i])
        suffix = part[_i:]
        results.append((pid, suffix))
    return results


# ---------------------- COM 任务创建 + 大纲缩进 ----------------------

def create_task_with_outline(
    project,
    task_dict: Dict[str, Any],
    current_depth: List[int],
    construction_cal_name: str = CONSTRUCTION_CAL_NAME,
    work_weekend_keywords: Optional[List[str]] = None,
    logger: Optional[logging.Logger] = None,
):
    """在 MS Project COM project 中按大纲层级新增单个任务。

    统一 mpp_renderer.build_mpp (L200-267) 与 msp_automation._create_one_task (L62-118)
    的重复实现：任务创建 → Manual/Estimated → 大纲缩进状态机 → 工期/里程碑 → 施工日历指派。

    Args:
        project: MS Project COM Project 对象
        task_dict: 任务字典，需含 id/name/duration/outline_level 等
        current_depth: 单元素列表 [int]，可变（函数内就地更新当前大纲深度）
        construction_cal_name: 施工7天日历名称
        work_weekend_keywords: 触发施工日历的摘要节点关键词（默认 _DEFAULT_WEEKEND_KEYWORDS）
        logger: 可选日志器

    Returns:
        MS Project COM Task 对象
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    t_id = task_dict.get("id")
    name = str(task_dict.get("name", f"任务{t_id}"))
    dur = task_dict.get("duration", task_dict.get("duration_days", 0))
    target_level = int(task_dict.get("outline_level", task_dict.get("level", 3)))
    # P1-01: milestone semantics are explicit. An UNRESOLVED conditional task
    # (template 14d/8d, zero committed duration) is NOT a completion milestone
    # and must never be written as a diamond.
    is_ms = is_true_milestone(task_dict)
    unresolved = is_unresolved_placeholder(task_dict)
    # V6.5.0 FIX-10/11 (client presentation layer). Default behaviour is unchanged: the
    # internal display name carries UNRESOLVED_MARK. When the caller marks a task for
    # client-facing presentation, the sanitised client name is used and the state moves to
    # the ClientState custom field / notes instead of the Task Name. This is presentation
    # only — it changes no duration, date, dependency or milestone flag.
    client_name = None
    if task_dict.get("client_presentation"):
        candidate = task_dict.get("client_task_name")
        if candidate:
            client_name = str(candidate)
    if client_name:
        name = client_name
    elif unresolved:
        name = UNRESOLVED_MARK + name
    work_weekend = bool(task_dict.get("use_construction_cal", task_dict.get("work_weekend", False)))

    keywords = work_weekend_keywords or _DEFAULT_WEEKEND_KEYWORDS

    # 1. 创建任务
    t_obj = project.Tasks.Add(name)
    try:
        t_obj.Manual = False
        t_obj.Estimated = False
    except Exception as e:
        logger.debug(f"[_common] 任务 {t_id} 属性设置失败: {e}")

    # V6.5.0 FIX-11: when the state was removed from the client-facing Task Name it is
    # preserved here (task Notes) so the UNKNOWN is migrated, never deleted.
    state = task_dict.get("client_state")
    if state and (state.get("conditional") or state.get("requirement_status") != "Confirmed"):
        try:
            t_obj.Notes = (
                f"Requirement Status: {state.get('requirement_status')}\n"
                f"Conditional: {'Yes' if state.get('conditional') else 'No'}\n"
                f"Basis: {state.get('basis')}\n"
                f"Confidence: {state.get('confidence')}\n"
                f"Potential Duration: {state.get('potential_duration')}\n"
                f"Decision Owner: {state.get('decision_owner') or '-'}\n"
                f"Required By: {state.get('required_by') or '-'}\n"
                f"[internal name] {state.get('internal_name')}"
            )
        except Exception as e:
            logger.debug(f"[_common] 任务 {t_id} Notes 设置失败: {e}")

    if task_dict.get('input_notes'):
        previous = getattr(t_obj, 'Notes', '') or ''
        t_obj.Notes = (str(previous) + '\n' + str(task_dict['input_notes'])).strip()
    # A new Project task does not reliably inherit the last requested depth.
    # Read the actual task depth and fail if Project refuses the hierarchy.
    for _ in range(target_level + int(t_obj.OutlineLevel) + 2):
        actual = int(t_obj.OutlineLevel)
        if actual == target_level:
            break
        if actual < target_level:
            t_obj.OutlineIndent()
        else:
            t_obj.OutlineOutdent()
        if int(t_obj.OutlineLevel) == actual:
            raise RuntimeError(f'MPP_OUTLINE_NOT_APPLIED: task {t_id}, actual={actual}, requested={target_level}')
    if int(t_obj.OutlineLevel) != target_level:
        raise RuntimeError(f'MPP_OUTLINE_MISMATCH: task {t_id}')
    current_depth[0] = target_level

    # 3. 叶子节点写工期/里程碑
    if target_level >= 3 and not task_dict.get("summary"):
        if is_ms:
            try:
                t_obj.Milestone = True
                t_obj.Duration = "0d"
                t_obj.Estimated = False
            except Exception as e:
                logger.debug(f"[_common] 里程碑设置失败 {t_id}: {e}")
        else:
            try:
                _fdur = float(dur)
                if abs(_fdur - round(_fdur)) > 1e-9:
                    logger.warning(f"[_common] 任务 {t_id} 工期 {dur} 含小数点，已强制取整为 {int(round(_fdur))}d")
                int_dur = int(round(_fdur))
                t_obj.Duration = f"{int_dur}d"
                t_obj.Estimated = False
                # P1-01: MS Project auto-promotes any zero-duration task to a
                # milestone. An UNRESOLVED conditional activity carries zero
                # *committed* duration but is NOT a milestone, so explicitly pin
                # Milestone=False so a subsequent CalculateProject() does not
                # re-infer a diamond. COM probe evidence: explicit
                # Milestone=False survives CalculateProject even when
                # Duration='0d', whereas omitting it auto-sets Milestone=True.
                # A scope-exclusion placeholder (demolition under UNKNOWN site
                # condition / excluded trade) is the same: unresolved physical
                # activity, zero committed duration, never a diamond.
                if unresolved or task_dict.get("scope_exclusion"):
                    t_obj.Milestone = False
            except Exception as e:
                logger.debug(f"[_common] 工期设置失败 {t_id}: {e}")

    # 4. 施工日历指派
    is_construction_summary = (
        target_level <= 2
        and any(kw in name for kw in keywords)
        and not any(kw in name for kw in _GOV_APPROVAL_EXCLUDE_KEYWORDS)
    )
    # FIX-01: an explicitly resolved ACTIVITY CALENDAR wins over name/phase
    # inference — this is what stops the site 7D calendar propagating to
    # design / tender / authority / handover activities in the physical MPP.
    explicit_cal = task_dict.get('mpp_calendar_name')
    if explicit_cal:
        try:
            t_obj.Calendar = explicit_cal
        except Exception as e:
            logger.debug(f"[_common] 赋予活动日历失败 {t_id}: {e}")
    elif work_weekend or (is_construction_summary and 'use_construction_cal' not in task_dict):
        try:
            t_obj.Calendar = construction_cal_name
        except Exception as e:
            logger.debug(f"[_common] 赋予施工日历失败 {t_id}: {e}")
    elif 'use_construction_cal' in task_dict and target_level >= 3:
        # Explicit resolved leaf calendar also prevents summary-calendar inference.
        t_obj.Calendar = project.Calendar.Name

    return t_obj


# ---------------------- COM 会话 re-export ----------------------
# mpp_renderer 可直接 from core._common import MSProjectSession
try:
    from core.msp_session import MSProjectSession
except ImportError:
    try:
        from msp_session import MSProjectSession
    except ImportError:
        MSProjectSession = None  # type: ignore
