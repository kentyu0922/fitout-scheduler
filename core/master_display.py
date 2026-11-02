"""Arrange an already solved master network for client / PM Lead review.

The caller removes old outline summaries and projects virtual anchors before
calling this module. Real activities, IDs, durations and links are preserved.
Only display fields, outline levels and new phase summaries are introduced.
"""
from __future__ import annotations

import copy
import heapq
import re

from core.client_presentation import sanitize_task_name
from core.mpp_roundtrip import expected_links
from core.master_reporting import PHASES, DISPLAY_PHASES, SUPPLY_TRACK, reporting_phase, procurement_caption
from core.responsibility import annotate_tasks


LEGACY_PHASES = (
    "前期策划", "设计采购", "设计与审批", "施工采购", "开工许可",
    "设备采购", "实体施工", "家具与空气", "IT实施", "验收审批", "交付搬迁",
)

_INDUSTRY_NAMES = {
    "office": ("办公需求确认", "办公功能与设施审查", "办公接口核验", "办公使用验收", "行政与设施接管"),
    "gaming": ("研发协作与保密需求确认", "研发工位与IT接口审查", "研发岗位接口核验", "研发协作场景验收", "研发启用与支持交接"),
    "finance": ("业务连续性与保密需求确认", "保密分区与业务接口审查", "访问边界与信息接口核验", "权限与办公通信验收", "业务启用与风险移交"),
    "biopharma": ("办公用途与EHS边界确认", "办公用途与EHS设计审查", "办公用途及现场接口核验", "办公功能与EHS条件验收", "办公用途与运行责任移交"),
    "semiconductor": ("研发办公与设备用途确认", "工作站容量与设备接口审查", "研发点位与设备接口核验", "研发办公及设施验收", "研发启用与设备边界移交"),
}
_INDUSTRY_STAGES = ("requirements", "design", "inspection", "acceptance", "release")
_NAME_REPLACEMENTS = {
    "Kick Off Meeting 项目启动会": "项目启动会",
    "用户需求调研与空间需求任务书编制 (Brief)": "用户需求与空间任务书",
    "大楼物业装修申请 & CI导则/现状机电图获取": "物业条件与现状资料确认",
    "项目执行计划 PEP 编制与各方确认": "项目执行计划确认",
    "前期规划与项目任务书确认": "项目任务书确认",
    "概念方案深化与空间规划调整 (Test Fit)": "概念方案与空间规划",
    "效果图 3D Rendering 出图与材质选型确认": "效果图与材料确认",
    "初步设计 SD 出图与业主方案签字确认": "初步设计与业主确认",
    "全专业施工图深化出图 (装饰/机电/消防/弱电)": "施工图编制与专业协调",
    "公共工程与专项系统范围／空间分界及唯一施工责任确认": "公共工程与专项界面确认",
    "配合出图 LDI 本地化翻图、规范复核及正式盖章": "本地设计复核与盖章",
    "递交第三方施工图审查机构审查 (数字化审图)": "施工图送审",
    "第三方专项图审意见答复与修改通过": "图审意见修改与复核",
    "取得《施工图审查合格书》 / 施工图全套确认": "施工图确认",
    "住建局：消防设计审查申报": "消防设计审查申报",
    "取得《建设工程消防设计审查合格意见书》": "消防设计审查通过",
    "物业装修图纸送审与进场安全协议签署": "物业图审与进场协议",
    "质安监报监及政府施工许可证申报": "报监与施工许可申报",
    "正式取得施工许可证": "施工许可取得",
    "场地移交与动工准备 Site Takeover": "场地移交与进场准备",
    "物理竣工 / 硬装施工完成 (Physical Completion)": "物理竣工验收",
    "家具与 IT/AV 安装就绪 (Furniture & AV Ready)": "家具与办公终端就绪",
    "搬迁方案编制、资产标签与分批搬迁计划 (Move Management)": "搬迁方案与资产清单",
    "搬迁全部完成与业务启用确认 (Day 1)": "搬迁完成与业务启用",
    "业主搬迁入驻条件确认（内部放行）": "业主入驻条件确认",
    "第三方消防设施检测、电气防火检测与出具检测报告": "消防设施与电气防火检测",
    "住建主管部门消防验收/竣工消防备案申报与实地查验": "消防验收或备案查验",
    "消防查验意见整改复查与正式批文办理": "消防查验整改与复查",
    "取得《建设工程消防验收合格意见书》/ 消防备案凭证": "消防验收通过或备案完成",
    "住建局联合竣工验收与备案": "竣工验收与备案",
    "深度精保洁与场地最终移交准备": "精保洁与移交准备",
    "FM 运维图纸、技术资料与设备资产台账移交": "运维资料与资产台账移交",
    "物业运维团队现场实操培训与带载应急演练": "运维培训与应急演练",
    "物业 FM 正式接管签认": "设施接管确认",
    "各业务线分批搬迁入驻与桌面技术支持": "分批搬迁与现场支持",
}
_PROCUREMENT_PREFIX = re.compile(r"^(?:设计服务|施工总包|DB总承包|设计施工一体(?:化)?|设计施工总包|总承包)\s*[：:]\s*")
_ENGLISH_PARENTHETICAL = re.compile(r"[（(][A-Za-z0-9\s/&.,+\-]+[）)]")
_PHASE_PREFIX = re.compile(r"^Phase\s*\d+\s*[:：.\-]?\s*", re.I)


def _uid(task):
    return str(task.get("activity_uid", ""))


def _is_day_one(task):
    return (_uid(task) == "pmp.module.core_handover.5"
            or (task.get("module_id") == "core_handover" and task.get("module_step") == 5)
            or "Day 1" in str(task.get("name", "")))


def _phase(task):
    """Keep early planning out of construction / move-in phase envelopes."""
    uid = _uid(task)
    if (task.get("industry_stage") == "requirements"
            or re.match(r"pmp\.integration\.industry\.[^.]+\.requirements\d+$", uid)
            or uid.startswith("pmp.interface.industry_")
            or re.match(r"pmp\.lifecycle\.A\d+\.requirements$", uid)
            or uid in {"pmp.module.it_cutover.1", "pmp.module.office_ff_move.4",
                       "pmp.lifecycle.A05.retirement_scope", "pmp.integration.it.backup"}
            or (task.get("module_id") == "it_cutover" and task.get("module_step") == 1)
            or (task.get("module_id") == "office_ff_move" and task.get("module_step") == 4)):
        return "前期策划"
    if _is_day_one(task):
        return "交付搬迁"
    phase = task.get("master_phase")
    if phase not in LEGACY_PHASES:
        raise ValueError(f"Master task {task.get('id')} has no supported master_phase: {phase!r}")
    return phase


def _short_name(task):
    if task.get('completion_scope_caption'):
        return task['completion_scope_caption']
    source = str(task.get("name", ""))
    preferred = str(task.get("master_task_name") or source)
    industry, stage = task.get("industry_profile") or task.get("industry") or task.get("sector"), task.get("industry_stage")
    gate = re.fullmatch(r"pmp\.industry\.([^.]+)\.([^.]+)", _uid(task))
    requirement = re.fullmatch(r"pmp\.interface\.industry_(.+)", _uid(task))
    if gate:
        industry, stage = industry or gate[1], stage or gate[2]
    elif requirement:
        industry, stage = industry or requirement[1], stage or "requirements"
    if preferred == source and industry in _INDUSTRY_NAMES and stage in _INDUSTRY_STAGES:
        preferred = _INDUSTRY_NAMES[industry][_INDUSTRY_STAGES.index(stage)]
    from core.specialist_names import specialist_name
    preferred = specialist_name(task, preferred)
    out = _NAME_REPLACEMENTS.get(preferred, preferred)
    out = sanitize_task_name(out)
    previous = None
    while previous != out:
        previous = out
        out = _PHASE_PREFIX.sub("", out)
        out = re.sub(r"^A\d{2}\s*[:：.\-]?\s*", "", out)
        out = _PROCUREMENT_PREFIX.sub("", out)
        out = re.sub(r"^(?:施工|设计)\s*[：:]\s*", "", out)
    out = _ENGLISH_PARENTHETICAL.sub("", out)
    out = out.replace("验收资料移交及使用批准", "验收与使用交接")
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out or source


def _date_key(task, field):
    return str(task.get(field) or task.get(field + "_date") or "9999-12-31")


def _topological_ranks(tasks, project_id):
    by_id = {task["id"]: task for task in tasks}
    incoming = {tid: set() for tid in by_id}
    successors = {tid: set() for tid in by_id}
    for task in tasks:
        for pred, _kind, _lag in expected_links(task.get("predecessors")):
            if pred == project_id:
                raise ValueError(f"Task {task['id']} still depends on the project summary")
            if pred not in by_id:
                raise ValueError(f"Task {task['id']} refers to missing master predecessor {pred}")
            incoming[task["id"]].add(pred)
            successors[pred].add(task["id"])
    heap = [(_date_key(by_id[tid], "start"), _date_key(by_id[tid], "finish"), tid)
            for tid, predecessors in incoming.items() if not predecessors]
    heapq.heapify(heap)
    ranks = {}
    while heap:
        _start, _finish, tid = heapq.heappop(heap)
        ranks[tid] = len(ranks)
        for successor in sorted(successors[tid]):
            incoming[successor].remove(tid)
            if not incoming[successor]:
                task = by_id[successor]
                heapq.heappush(heap, (_date_key(task, "start"), _date_key(task, "finish"), successor))
    if len(ranks) != len(tasks):
        raise ValueError("Master display received a cyclic predecessor network")
    return ranks


def arrange_master(tasks):
    """Return Project / phase / work rows without editing the solved network.

    ``tasks`` contains one level-1 Project and otherwise flat real activities
    with ``master_phase``. ``master_task_name`` takes priority for client names.
    Apply the general client-presentation pass *before* this function, because
    that pass otherwise regenerates client names from the internal names.
    """
    # Display arrangement writes only top-level task keys and new headings.
    result = [dict(task) for task in tasks]
    if not result:
        return []
    if len({task["id"] for task in result}) != len(result):
        raise ValueError("Master display requires unique task IDs")
    roots = [task for task in result if int(task.get("outline_level", task.get("level", 3))) == 1]
    if len(roots) != 1:
        raise ValueError("Master display requires exactly one Project row")
    root = roots[0]
    leaves = [task for task in result if task is not root]
    if any(task.get("summary") for task in leaves):
        raise ValueError("Old summaries must be projected and removed before master display")
    ranks = _topological_ranks(leaves, root["id"])
    groups = {phase: [] for phase in DISPLAY_PHASES}
    for task in leaves:
        legacy = _phase(task)
        phase = reporting_phase(task, legacy)
        task.update(master_phase=legacy, reporting_phase=phase, outline_level=3, level=3,
                    summary=False, client_task_name=procurement_caption(task, _short_name(task)), client_presentation=True)
        groups[phase].append(task)
    root.update(outline_level=1, level=1, summary=True, client_presentation=True)
    root["client_task_name"] = str(root.get("name", ""))
    ordered = [root]
    seq = max(task["id"] for task in result) + 1
    for number, phase in enumerate(DISPLAY_PHASES, 1):
        members = groups[phase]
        if not members:
            continue
        members.sort(key=lambda task: (_is_day_one(task), _date_key(task, "start"),
                                      ranks[task["id"]], _date_key(task, "finish"), task["id"]))
        starts = [task.get("start") or task.get("start_date") for task in members]
        finishes = [task.get("finish") or task.get("finish_date") for task in members]
        starts = [value for value in starts if value]
        finishes = [value for value in finishes if value]
        if not starts or not finishes:
            raise ValueError(f"Phase {phase} has no solved start / finish dates")
        title = 'IT迁移与切换（选配）' if phase == 'IT实施' else phase
        heading = dict(id=seq, name=title, client_task_name=title,
                       activity_uid=f"pmp.master.phase.{number}", master_phase=phase,
                       outline_level=2, level=2, summary=True, milestone=False,
                       duration=0, duration_days=0, predecessors="",
                       start=min(starts), finish=max(finishes), client_visible=True,
                       client_presentation=True)
        ordered.append(heading)
        seq += 1
        if phase==SUPPLY_TRACK:
            heading['input_notes']='并行供货控制线，不是连续招标；逐项技术释放、订货、制造运输及到货条件保留在计算网络，不逐件向客户展开。专项到货若影响安装，仍如实反映风险。'
            for task in members:task.update(outline_level=3,level=3,pm_view_detail=True)
            ordered.extend(members)
        elif phase == '招采阶段':
            from core.master_boundaries import nest_procurement
            nested, seq = nest_procurement(members, seq)
            ordered.extend(nested)
        elif phase == '施工阶段':
            from core.pm_workpackages import nest_construction
            nested, seq = nest_construction(members, seq)
            ordered.extend(nested)
        elif phase in {'前期规划','竣工验收阶段'}:
            from core.pm_stage_view import arrange
            nested,seq=arrange(members,seq,phase)
            ordered.extend(nested)
        else:
            ordered.extend(members)
    annotate_tasks(ordered)
    return ordered
