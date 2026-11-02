# -*- coding: utf-8 -*-
"""
core/sector_weaver.py — Senior PM Native Sector & Workstream Weaver

Natively weaves industry sector-specific engineering packages, critical paths,
and Capex payment milestones into the comprehensive fit-out WBS template.
Preserves all original design, procurement, permitting, and trade tasks while
introducing sector-defining milestones and strict topological dependencies.
"""
import copy
import json
import logging
import os
import math
import re
from typing import List, Dict, Any, Optional

from core.task_utils import renumber_tasks_contiguously

logger = logging.getLogger("sector_weaver")


def _dur_kind_of(name: str) -> str:
    """按工序语义判定工期类型，决定面积缩放口径（复用 core/calibration 的分类型思路）。

    - statutory：法定审批时钟（环评批复/排污许可/消防验收/施工许可/图审/备案）—— 固定，不随面积缩放
    - test：第三方测试/认证/联调/演练 —— 以固定底限为主，极大面积才略增
    - build：实体物理施工 —— 工作面并发，0.32 次幂（与 SCALE_EXP_BUILD 同口径）
    """
    n = name or ""
    if any(k in n for k in ("环评批复", "排污许可", "环保竣工验收", "消防验收", "施工许可", "图审", "备案")):
        return "statutory"
    if any(k in n for k in ("测试", "检测", "达标", "检漏", "认证", "演练", "联调", "衰减",
                            "露点", "纯度", "面风速", "Failover", "回退", "割接", "第三方")):
        return "test"
    return "build"


def _scale(area, base_dur, name: str = "", base_area: float = 8000.0) -> int:
    """面积呼应（分类型），替代原来对'所有'模块一刀切的 0.32 单曲线。

    - statutory：法定时钟固定返回基准值，绝不按面积压缩（环评法 报告表30日/报告书60日 批复窗口）。
    - test：固定底限 = 基准，仅当面积远大于基准时按 0.15 次幂缓慢上浮（极小面积也不压缩）。
    - build：物理施工，工作面并发 0.32 次幂（与 core/calibration SCALE_EXP_BUILD 同口径）。
    里程碑 (base_dur<=0) 原样返回。
    """
    a = float(area or 0)
    base = int(round(float(base_dur)))
    if base <= 0 or a <= 0:
        return base
    ratio = max(0.05, a / base_area)
    kind = _dur_kind_of(name)
    if kind == "statutory":
        return base
    if kind == "test":
        scaled = base * math.pow(ratio, 0.15)
        return max(base, int(round(scaled)))
    return max(1, int(round(base * math.pow(ratio, 0.32))))


def _specialist_rooms() -> Dict[str, Any]:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "specialist_rooms.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ============================================================
# 长周期关键设备采购规格（按行业）
# lead_days = 日历采购提前期（下单→到货），属供应链属性，不随面积缩放（固定）
# 行业主系统包的专业安装由"到货里程碑"驱动上关键路径
# ============================================================
LONGLEAD_SPEC: Dict[str, Dict[str, Any]] = {
    "gaming": {
        "summary": "长周期关键设备采购 (Long-lead Procurement)",
        "items": [
            ("双冷源精密空调机组 (CRAC N+1) 进口订货与厂验", 84),
            ("3D 红外光学动捕相机阵列进口订货", 70),
            ("专业声学隔声材料与阻抗复合消声器定制", 56),
        ],
    },
    "finance": {
        "summary": "长周期关键设备采购 (Long-lead Procurement)",
        "items": [
            ("高压 UPS 动力电池组与配电柜订货", 84),
            ("交易终端多屏与 KVM 集中部署设备订货", 56),
            ("生物识别门禁与视频安防矩阵订货", 56),
        ],
    },
    "biopharma": {
        "summary": "长周期关键设备采购 (Long-lead Procurement)",
        "items": [
            ("工艺冷水机组 / 全新风 AHU 机组订货", 112),
            ("生物安全柜与隔离器订货", 98),
            ("洁净室彩钢板围护与 HEPA 高效过滤器订货", 70),
        ],
    },
    "semiconductor": {
        "summary": "长周期关键设备采购 (Long-lead Procurement)",
        "items": [
            ("高纯特气柜 / 干泵 / 特气面板订货", 140),
            ("高压压缩空气 (CDA) 干燥机与空压机订货", 98),
            ("超纯水模块与微振动隔振平台订货", 140),
        ],
    },
    "office": {
        "summary": "长周期关键设备采购 (Long-lead Procurement)",
        "items": [
            ("行政班台与定制办公家具订货", 70),
            ("IT / AV 智能会议系统设备订货", 56),
        ],
    },
}


# ============================================================
# 历史城市规划预留（保留旧内部名称以兼容，不是当前法定时限库）。
# 这些数值未经完整属地核实，不能标成全国法定或具体项目承诺。
# 当前公开入口由 local_authority 按适用路径重新组装审批任务；
# 适用上海既有建筑装修的核发窗口为沪建规范〔2025〕12号第八条2工作日。
# 未适配城市仅明确授权的诊断草案可继续；不能凭本表认定政策已适配。
# ============================================================
CITY_STATUTORY: Dict[str, Dict[str, int]] = {
    # 节点关键词 -> 历史规划工作日（default不是全国法定基准）
    "施工许可":     {"default": 7,  "上海": 7, "杭州": 15, "苏州": 10, "苏州工业园": 10},
    "消防设计审查": {"default": 15, "上海": 10, "杭州": 15, "苏州": 12, "苏州工业园": 12},
    "消防验收":     {"default": 15, "上海": 10, "杭州": 15, "苏州": 12, "苏州工业园": 12},
}


def _city_statutory_key(name: str) -> Optional[str]:
    """匹配历史规划预留节点；法律适用性由local_authority决定。"""
    n = name or ""
    if "施工许可" in n:
        return "施工许可"
    if "消防设计审查" in n:
        return "消防设计审查"
    if any(k in n for k in ("消防验收", "消防备案", "竣工消防")):
        return "消防验收"
    return None


def _apply_city_statutory(tasks: List[Dict[str, Any]], city: Optional[str]) -> List[Dict[str, Any]]:
    """应用旧规划预留，后续必须经过属地判定；0天成果不动。

    仅作用于 duration_days>0 的审批活动，里程碑(0天)与实体施工不受影响。
    """
    if not city:
        return tasks
    tbl = CITY_STATUTORY
    for t in tasks:
        key = _city_statutory_key(t.get("name", "") or "")
        if not key:
            continue
        dur = t.get("duration_days", t.get("duration", 0)) or 0
        if dur <= 0:
            continue
        days = tbl[key].get(city, tbl[key]["default"])
        if days != dur:
            t["duration_days"] = days
            t["duration"] = days
            t["statutory_city_clock"] = {"city": city, "node": key, "days": days}
    return tasks


# ============================================================
# 顶层「关键里程碑总控」汇总（Project Control Milestones）
# 将跨阶段的项目级客户里程碑（Kick Off / LOA / 合同签约 / 图审合格证 / 施工许可证 /
# 场地移交 / 物理竣工 / 消防验收 / 竣工备案 / 空气质量 / 搬迁营业）提升为 L2 汇总条，
# 置于 L1 项目根之后，使业主 / Steering Committee 一眼扫关键日期——符合总控 Master schedule 形态。
# 行业专属的「系统就绪」类里程碑（Lease Gate / 长周期到货 / 各系统验收）保留在各自 Phase 内。
# 采用标准 MS Project 大纲模型：汇总条由紧随其后的更高层级任务（此处为 L3 里程碑）聚合得出，
# 其下一位 L2 兄弟（Phase 1）自动收束其子任务范围。
# ============================================================
KEY_MILESTONE_KEYWORDS = (
    "项目启动会", "Kick Off", "中标通知书", "LOA", "总承包合同签订完成",
    "合同签订完成", "图审合格证", "施工图全套确认", "正式取得施工许可证",
    "取得施工许可证", "场地移交", "Site Takeover", "动工准备",
    "物理竣工", "硬装施工完成", "消防验收合格", "消防备案凭证",
    "竣工备案", "联合验收通过", "室内空气质量检测合格", "空气质量检测合格",
    "场地正式移交", "Site Handover", "搬迁全部完成", "正式营业", "正式开业",
)


def _is_key_milestone(name: str) -> bool:
    n = (name or "").lower()
    return any(k.lower() in n for k in KEY_MILESTONE_KEYWORDS)


def _build_control_milestone_rollup(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """在 L1 项目根之后插入一条顶部 L2 汇总条「关键里程碑总控」，作为业主 / Steering
    Committee 一眼扫关键日期的 Master schedule 形态。

    v3.10.2 重写（修复真实 Bug，非校验器误报）：
    - 关键里程碑 **保留在原阶段时间线位置**，不重定位。旧实现把里程碑提到列表顶部，
      使其排在其前置任务之前 → 产生「列表顺序前向引用」；而 main.py 在情景重解
      （evaluate_conditional_exposure / solve_schedule_with_target）会用这份列表重算，
      前向引用会被求解器静默忽略 → 里程碑日期失真。保留原位从根上消除该隐患，且不影响
      任何已算定的日期（CPM 解算发生在本函数之前，本函数仅改呈现顺序）。
    - 汇总条通过 child_ids 引用关键里程碑，并显式写入日期跨度
      （min(child.start) → max(child.finish)），使其在甘特上是一条有意义的顶部控制条。
    - rollup_id 取 max(id)+1，恒不与任何既有任务冲突——不再有「重插入导致 id 撞车」
      （旧实现把恰为 max id 的关键里程碑移出 remaining 后又重插入，与 rollup 同 id，
      触发重复的 id=108 → PRED_CYCLE）。
    """
    key_ms = [t for t in tasks if t.get("milestone") and _is_key_milestone(t.get("name", ""))]
    if not key_ms:
        return tasks

    max_id = 0
    for t in tasks:
        try:
            max_id = max(max_id, int(t["id"]))
        except (TypeError, ValueError):
            pass
    rollup_id = max_id + 1

    starts = [str(t.get("start")) for t in key_ms if t.get("start")]
    finishes = [str(t.get("finish")) for t in key_ms if t.get("finish")]
    span = {}
    if starts and finishes:
        span = {"start": min(starts), "finish": max(finishes)}

    rollup = {
        "id": rollup_id,
        "name": "关键里程碑总控 (Project Control Milestones)",
        "outline_level": 2,
        "duration_days": 0,
        "duration": 0,
        "summary": True,
        "milestone": True,
        "phase": "Project Control",
        "child_ids": [],
        "control_rollup": True,
    }
    rollup.update(span)

    out = list(tasks)
    root_idx = 0
    for i, t in enumerate(out):
        if int(t.get("outline_level", 3)) <= 1:
            root_idx = i
            break
    out.insert(root_idx + 1, rollup)
    # 插入后重编号，使 id 在列表顺序中连续 1..N（满足校验器的 id 连续性约定，
    # 并同步改写所有前置/检验门引用）；随后据重编号后的新 id 回填 child_ids。
    out = renumber_tasks_contiguously(out)
    rollup = next(t for t in out if t.get("control_rollup"))
    rollup["child_ids"] = [
        t["id"] for t in out
        if t.get("milestone") and _is_key_milestone(t.get("name", ""))
        and not t.get("control_rollup")
    ]
    return out


def _add_pred(node, pid):
    """安全地把 pid 追加为 node 的前置（避免重复）。"""
    if not node:
        return
    existing = [p for p in str(node.get("predecessors", "")).split(",") if p]
    if str(pid) not in existing:
        existing.append(str(pid))
    node["predecessors"] = ",".join(existing)


def weave_sector_modules(
    tasks: List[Dict[str, Any]],
    sector: str = "office",
    area_sqm: float = 25000.0,
    facts: Optional[Dict[str, Any]] = None,
    pack_override=None
) -> List[Dict[str, Any]]:
    if not sector:
        return tasks

    from core.sector_router import sector_pack_plan
    plan = sector_pack_plan(sector, facts)
    packs = set(plan["packs"] if pack_override is None else pack_override)
    sec = plan["sector_id"]
    logger.info("  -> [Sector Weaver] sector=%s packs=%s status=%s",
                sec, sorted(packs), plan["scope_status"])
    if plan.get("disclosure"):
        logger.warning("  -> [Sector Weaver] %s", plan["disclosure"])
    if not packs:
        return tasks

    result = [copy.deepcopy(t) for t in tasks]
    facts = facts or {}

    max_id = max((t.get("id", 0) for t in result), default=100)

    def next_id():
        nonlocal max_id
        max_id += 1
        return max_id

    # -------------------------------------------------------------
    # 1. 寻找关键挂接锚点 (Anchor Tasks)
    # -------------------------------------------------------------
    kickoff_task = None
    for t in result:
        name = t.get("name", "")
        if "Kick Off" in name or "项目启动会" in name:
            kickoff_task = t
            break
    if not kickoff_task and result:
        kickoff_task = result[0]

    brief_tasks = []
    for t in result:
        preds = str(t.get("predecessors", "")).split(",")
        if kickoff_task and str(kickoff_task["id"]) in preds:
            brief_tasks.append(t)

    dd_finish_task = None
    permit_finish_task = None
    contract_sign_task = None
    site_prep_task = None
    concealed_inspect_task = None
    fitout_finish_milestone = None
    fire_joint_milestone = None
    iaq_final_milestone = None
    clean_task = None

    for t in result:
        name = t.get("name", "")
        if "全专业施工图深化出图" in name or "施工图全套确认" in name or "施工图审查合格书" in name:
            if not dd_finish_task:
                dd_finish_task = t
        if "正式取得施工许可证" in name or "施工许可" in name:
            if t.get("milestone"):
                permit_finish_task = t
        if any(kw in name for kw in ["合同签订完成", "合同签约", "合同签订", "总承包合同"]):
            if not contract_sign_task:
                contract_sign_task = t
        if "测量放线与标高复核" in name or "原有装修拆除" in name:
            if not site_prep_task and "放线" in name:
                site_prep_task = t
        if "隐蔽工程验收" in name:
            # 基线 WBS 中隐蔽工程验收是带工期的检查任务（非 milestone），取最晚一个
            # （天花隐蔽晚于隔墙隐蔽）作为高密研发测试专区与进度款支付节点的前置锚点；
            # 不能要求 milestone，否则漏匹配掉入硬编码兜底 id 造成前向引用。
            concealed_inspect_task = t
        if "物理竣工" in name or "硬装施工完成" in name:
            fitout_finish_milestone = t
        if "竣工备案" in name or "联合验收通过" in name:
            fire_joint_milestone = t
        if "室内空气质量检测合格" in name:
            iaq_final_milestone = t
        if "深度精保洁" in name:
            clean_task = t

    # -------------------------------------------------------------
    # 2. 编织 TDD 租前技术尽调模块 (Phase 1 尾部，进入设计前)
    # -------------------------------------------------------------
    tdd_nodes = []
    if "tdd" in packs:
        id_tdd_summary = next_id()
        id_tdd_expert = next_id()
        id_tdd_survey = next_id()
        id_tdd_report = next_id()
        id_tdd_review = next_id()
        id_lease_gate = next_id()

        tdd_summary = {
            "id": id_tdd_summary,
            "name": "租前技术尽职调查与租赁决策 (TDD & Lease Gate)",
            "outline_level": 3,
            "duration_days": 0,
            "duration": 0,
            "predecessors": "",
            "phase": "Phase 1  前期项目规划与需求定义"
        }
        tdd_expert = {
            "id": id_tdd_expert,
            "name": "组建 TDD 顾问技术专家组并进场踏勘准备",
            "outline_level": 4,
            "duration_days": 3,
            "duration": 3,
            "calendar": "5D",
            "predecessors": str(kickoff_task["id"]) if kickoff_task else "",
            "phase": "Phase 1  前期项目规划与需求定义"
        }
        tdd_survey = {
            "id": id_tdd_survey,
            "name": "大楼现场多专业工程勘测 (结构承重≥1000kg/㎡、供电密度≥120VA/㎡、双路光纤进线)",
            "outline_level": 4,
            "duration_days": 10,
            "duration": 10,
            "calendar": "5D",
            "predecessors": str(id_tdd_expert),
            "phase": "Phase 1  前期项目规划与需求定义"
        }
        tdd_report = {
            "id": id_tdd_report,
            "name": "编制大楼 TDD 技术尽职调查报告 (技术/机电负荷/造价前置评估)",
            "outline_level": 4,
            "duration_days": 12,
            "duration": 12,
            "calendar": "5D",
            "predecessors": str(id_tdd_survey),
            "phase": "Phase 1  前期项目规划与需求定义"
        }
        tdd_review = {
            "id": id_tdd_review,
            "name": "TDD 尽调报告业主与顾问联合技术评审闭合",
            "outline_level": 4,
            "duration_days": 5,
            "duration": 5,
            "calendar": "5D",
            "predecessors": str(id_tdd_report),
            "phase": "Phase 1  前期项目规划与需求定义"
        }
        lease_gate = {
            "id": id_lease_gate,
            "name": "租前技术尽调合格与租赁决策放行 (Lease Gate)",
            "outline_level": 4,
            "duration_days": 0,
            "duration": 0,
            "milestone": True,
            "calendar": "5D",
            "predecessors": str(id_tdd_review),
            "phase": "Phase 1  前期项目规划与需求定义"
        }
        tdd_nodes = [tdd_summary, tdd_expert, tdd_survey, tdd_report, tdd_review, lease_gate]

        # 将原本紧跟 Kickoff 的前期规划任务改接到 lease_gate 之后
        for bt in brief_tasks:
            preds = str(bt.get("predecessors", "")).split(",")
            new_preds = [str(id_lease_gate) if p == str(kickoff_task["id"]) else p for p in preds if p]
            bt["predecessors"] = ",".join(new_preds)

    # -------------------------------------------------------------
    # 3. 编织 分行业长周期关键设备采购（下单于设计冻结、到货驱动专业安装上关键路径）
    # -------------------------------------------------------------
    id_pay1 = next_id()

    pred_dd = str(dd_finish_task["id"]) if dd_finish_task else "22"
    pred_contract = str(contract_sign_task["id"]) if contract_sign_task else "39"

    # 长周期采购：按行业建模真实提前期，作为关键路径完工驱动（office 也织入其家具/IT 采购）
    longlead_pkg = []
    longlead_delivery_id = None
    if "longlead" in packs:
        _ll_spec = LONGLEAD_SPEC.get(sec, LONGLEAD_SPEC["office"])
        id_ll_summary = next_id()
        longlead_pkg = [{
            "id": id_ll_summary,
            "name": _ll_spec["summary"],
            "outline_level": 3,
            "duration_days": 0,
            "duration": 0,
            "predecessors": pred_dd,
            "phase": "Phase 3  设计深化、第三方审图与施工图确认",
        }]
        _ll_item_ids = []
        for _ll_nm, _ll_lead in _ll_spec["items"]:
            _iid = next_id()
            longlead_pkg.append({
                "id": _iid,
                "name": f"长周期采购: {_ll_nm}",
                "outline_level": 4,
                "duration_days": _ll_lead,
                "duration": _ll_lead,
                "calendar": "5D",
                "predecessors": str(id_ll_summary),
                "phase": "Phase 3  设计深化、第三方审图与施工图确认",
            })
            _ll_item_ids.append(_iid)
        id_ll_delivery = next_id()
        longlead_pkg.append({
            "id": id_ll_delivery,
            "name": "关键长周期设备到货验收与进场放行 (Long-lead Equipment Delivered)",
            "outline_level": 4,
            "duration_days": 0,
            "duration": 0,
            "milestone": True,
            "calendar": "5D",
            "predecessors": ",".join(str(_i) for _i in _ll_item_ids),
            "phase": "Phase 3  设计深化、第三方审图与施工图确认",
        })
        longlead_delivery_id = id_ll_delivery
    pay1_milestone = {
        "id": id_pay1,
        "name": "💰 商务里程碑：总包合同签订与首期预付款支付 (10%)",
        "outline_level": 3,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "5D",
        "predecessors": pred_contract,
        "phase": "Phase 4  施工总包公开招标"
    }

    # -------------------------------------------------------------
    # 4. 编织 核心机房先行交付岛 (Fast-track Data Center)
    # -------------------------------------------------------------
    id_dc_summary = next_id()
    id_dc_start = str(site_prep_task["id"]) if site_prep_task else "50"
    id_dc_struct = next_id()
    id_dc_crac = next_id()
    id_dc_fire = next_id()
    id_dc_ups = next_id()
    id_dc_micro = next_id()
    id_dc_cable = next_id()
    id_dc_loadbank = next_id()
    id_infra_ready = next_id()
    id_pay_dc = next_id()

    dc_summary = {
        "id": id_dc_summary,
        "name": "核心机房先行交付岛 (Fast-track Data Center)",
        "outline_level": 3,
        "duration_days": 0,
        "duration": 0,
        "predecessors": "",
        "phase": "Phase 6  实体施工阶段"
    }
    dc_struct = {
        "id": id_dc_struct,
        "name": "核心机房区域结构局部碳纤维加固与微孔防尘地坪处理",
        "outline_level": 4,
        "duration_days": 8,
        "duration": 8,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": id_dc_start,
        "phase": "Phase 6  实体施工阶段"
    }
    dc_crac = {
        "id": id_dc_crac,
        "name": "双冷源精密空调 (CRAC N+1) 室内外机安装与制冷铜管充氮保压",
        "outline_level": 4,
        "duration_days": 10,
        "duration": 10,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": str(id_dc_struct),
        "phase": "Phase 6  实体施工阶段"
    }
    dc_fire = {
        "id": id_dc_fire,
        "name": "七氟丙烷气体灭火管网预埋、泄压口与防排烟防火阀联动调试",
        "outline_level": 4,
        "duration_days": 8,
        "duration": 8,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": str(id_dc_struct),
        "phase": "Phase 6  实体施工阶段"
    }
    dc_ups = {
        "id": id_dc_ups,
        "name": "双路高压 UPS 动力电池组、配电柜安装与主干电缆敷设",
        "outline_level": 4,
        "duration_days": 10,
        "duration": 10,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": f"{id_dc_crac},{id_dc_fire}",
        "phase": "Phase 6  实体施工阶段"
    }
    dc_micro = {
        "id": id_dc_micro,
        "name": "微模块冷热通道封闭系统与服务器机柜阵列组装",
        "outline_level": 4,
        "duration_days": 8,
        "duration": 8,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": str(id_dc_ups),
        "phase": "Phase 6  实体施工阶段"
    }
    dc_cable = {
        "id": id_dc_cable,
        "name": "核心网络主干万兆单模光纤与超六类铜缆综合布线",
        "outline_level": 4,
        "duration_days": 8,
        "duration": 8,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": str(id_dc_micro),
        "phase": "Phase 6  实体施工阶段"
    }
    dc_loadbank = {
        "id": id_dc_loadbank,
        "name": "72小时全负荷假负载测试 (Load Bank) 与机房动环监控调测",
        "outline_level": 4,
        "duration_days": 5,
        "duration": 5,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": str(id_dc_cable),
        "phase": "Phase 6  实体施工阶段"
    }
    infra_ready = {
        "id": id_infra_ready,
        "name": "核心机房先行交付与提前45天上电就绪 (Fast-track DC Ready)",
        "outline_level": 4,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "7D",
        "predecessors": str(id_dc_loadbank),
        "phase": "Phase 6  实体施工阶段"
    }
    pay_dc = {
        "id": id_pay_dc,
        "name": "💰 商务里程碑：核心机房先行上电验收付款 (15%)",
        "outline_level": 4,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "5D",
        "predecessors": str(id_infra_ready),
        "phase": "Phase 6  实体施工阶段"
    }
    dc_nodes = [dc_summary, dc_struct, dc_crac, dc_fire, dc_ups, dc_micro, dc_cable, dc_loadbank, infra_ready]
    if "datacenter" not in packs:
        dc_nodes = []
        id_infra_ready = None

    # -------------------------------------------------------------
    # 5. 编织 动捕棚与专业声学录音配音室 (Mocap & Acoustic Studio)
    # -------------------------------------------------------------
    id_mocap_summary = next_id()
    id_mocap_start = str(site_prep_task["id"]) if site_prep_task else "50"
    id_mocap_floor = next_id()
    id_mocap_wall = next_id()
    id_mocap_truss = next_id()
    id_mocap_cam = next_id()
    id_mocap_doors = next_id()
    id_mocap_test = next_id()
    id_mocap_ready = next_id()

    mocap_summary = {
        "id": id_mocap_summary,
        "name": "游戏特种工程 B：动捕棚与专业声学录音配音室 (Mocap & Acoustic Studio)",
        "outline_level": 3,
        "duration_days": 0,
        "duration": 0,
        "predecessors": "",
        "phase": "Phase 6  实体施工阶段"
    }
    mocap_floor = {
        "id": id_mocap_floor,
        "name": "建筑声学隔声浮筑地面 (弹簧减震垫+配筋细石混凝土隔振层) 施工",
        "outline_level": 4,
        "duration_days": 12,
        "duration": 12,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": id_mocap_start,
        "phase": "Phase 6  实体施工阶段"
    }
    mocap_wall = {
        "id": id_mocap_wall,
        "name": "房中房双层轻钢吸声隔音墙、穿孔吸声铝板与低频陷阱安装",
        "outline_level": 4,
        "duration_days": 10,
        "duration": 10,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": str(id_mocap_floor),
        "phase": "Phase 6  实体施工阶段"
    }
    mocap_truss = {
        "id": id_mocap_truss,
        "name": "天花动捕刚性钢结构桁架导轨 (Truss Rail) 安装与空间水平校准",
        "outline_level": 4,
        "duration_days": 8,
        "duration": 8,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": str(id_mocap_wall),
        "phase": "Phase 6  实体施工阶段"
    }
    mocap_cam = {
        "id": id_mocap_cam,
        "name": "3D 红外光学动捕相机阵列安装与空间光学校正",
        "outline_level": 4,
        "duration_days": 7,
        "duration": 7,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": str(id_mocap_truss),
        "phase": "Phase 6  实体施工阶段"
    }
    mocap_doors = {
        "id": id_mocap_doors,
        "name": "专业隔声门、双层隔音观察窗与消声风管阻抗复合消声器安装",
        "outline_level": 4,
        "duration_days": 8,
        "duration": 8,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": str(id_mocap_cam),
        "phase": "Phase 6  实体施工阶段"
    }
    mocap_test = {
        "id": id_mocap_test,
        "name": "第三方建筑声学检测: 本底噪声(NC≤20)与混响时间(RT60≤0.25s)达标",
        "outline_level": 4,
        "duration_days": 3,
        "duration": 3,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": str(id_mocap_doors),
        "phase": "Phase 6  实体施工阶段"
    }
    mocap_ready = {
        "id": id_mocap_ready,
        "name": "专业动捕棚与声学录音工程就绪 (Mocap & Acoustic Studio Ready)",
        "outline_level": 4,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "7D",
        "predecessors": str(id_mocap_test),
        "phase": "Phase 6  实体施工阶段"
    }
    mocap_nodes = [mocap_summary, mocap_floor, mocap_wall, mocap_truss, mocap_cam, mocap_doors, mocap_test, mocap_ready]
    if "mocap" not in packs:
        mocap_nodes = []
        id_mocap_ready = None

    # -------------------------------------------------------------
    # 6. 编织 高密研发测试专区 & 施工中付款节点
    # -------------------------------------------------------------
    id_dev_zone = next_id()
    id_pay_concealed = next_id()
    id_pay_fitout = next_id()

    dev_zone = {
        "id": id_dev_zone,
        "name": "高密研发测试专区大功率工位配电 (0.12~0.15 kVA/㎡) 与独立 VRV 空调安装",
        "outline_level": 4,
        "duration_days": 12,
        "duration": 12,
        "work_weekend": True,
        "use_construction_cal": True,
        "calendar": "7D",
        "predecessors": str(concealed_inspect_task["id"]) if concealed_inspect_task else "56",
        "phase": "Phase 6  实体施工阶段"
    }
    pay_concealed = {
        "id": id_pay_concealed,
        "name": "💰 商务里程碑：全楼隐蔽工程联合验收与进度款支付 (30%)",
        "outline_level": 4,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "5D",
        "predecessors": str(concealed_inspect_task["id"]) if concealed_inspect_task else "56",
        "phase": "Phase 6  实体施工阶段"
    }
    pred_fitout = str(fitout_finish_milestone["id"]) if fitout_finish_milestone else "88"
    pay_fitout = {
        "id": id_pay_fitout,
        "name": "💰 商务里程碑：现场硬装物理竣工与进度款支付 (25%)",
        "outline_level": 3,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "5D",
        "predecessors": pred_fitout,
        "phase": "Phase 6  实体施工阶段"
    }

    # 动捕棚就绪挂接到物理竣工
    if "mocap" in packs and fitout_finish_milestone and id_mocap_ready:
        cur_preds = str(fitout_finish_milestone.get("predecessors", "")).split(",")
        if str(id_mocap_ready) not in cur_preds:
            cur_preds.append(str(id_mocap_ready))
        fitout_finish_milestone["predecessors"] = ",".join([p for p in cur_preds if p])

    # -------------------------------------------------------------
    # 7. 编织 竣工验收与空气质量付款里程碑 (15%)
    # -------------------------------------------------------------
    id_pay_accept = next_id()
    pred_accept_parts = []
    if fire_joint_milestone:
        pred_accept_parts.append(str(fire_joint_milestone["id"]))
    if iaq_final_milestone:
        pred_accept_parts.append(str(iaq_final_milestone["id"]))
    if not pred_accept_parts:
        pred_accept_parts = ["99", "101"]

    pay_accept = {
        "id": id_pay_accept,
        "name": "💰 商务里程碑：消防验收通过、竣工备案与空气合格节点款 (15%)",
        "outline_level": 3,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "5D",
        "predecessors": ",".join(pred_accept_parts),
        "phase": "Phase 7  竣工验收与合规审批"
    }

    # -------------------------------------------------------------
    # 8. 编织 IT 核心迁移与周末割接模块 (Phase 7.5)
    # -------------------------------------------------------------
    id_it_phase = next_id()
    id_it_plan = next_id()
    id_it_server = next_id()
    id_it_cutover = next_id()
    id_business_ready = next_id()

    it_summary = {
        "id": id_it_phase,
        "name": "Phase 7.5  总部 IT 核心网络与服务器迁移割接 (IT Migration & Cutover)",
        "outline_level": 2,
        "duration_days": 0,
        "duration": 0,
        "predecessors": "",
        "phase": "IT Migration"
    }
    it_plan = {
        "id": id_it_plan,
        "name": "IT 迁移方案深化、双轨专线打通与备件锁定",
        "outline_level": 3,
        "duration_days": 15,
        "duration": 15,
        "use_construction_cal": False,
        "calendar": "5D",
        "predecessors": str(id_infra_ready) if id_infra_ready else pred_fitout,
        "phase": "IT Migration"
    }
    it_server = {
        "id": id_it_server,
        "name": "核心服务器设备上架、内网打通与仿真试迁移演练",
        "outline_level": 3,
        "duration_days": 15,
        "duration": 15,
        "use_construction_cal": False,
        "calendar": "5D",
        "predecessors": f"{id_it_plan},{pred_fitout}",
        "phase": "IT Migration"
    }
    it_cutover = {
        "id": id_it_cutover,
        "name": "核心业务系统与数据库周末全量割接与回退验证",
        "outline_level": 3,
        "duration_days": 2,
        "duration": 2,
        "use_construction_cal": False,
        "calendar": "WEEKEND",
        "calendar_window": "weekend_only",
        "work_weekend": True,
        "predecessors": str(id_it_server),
        "phase": "IT Migration"
    }
    business_ready = {
        "id": id_business_ready,
        "name": "核心 IT 业务系统全量割接就绪 (Business Ready)",
        "outline_level": 3,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "5D",
        "predecessors": str(id_it_cutover),
        "phase": "IT Migration"
    }
    it_nodes = [it_summary, it_plan, it_server, it_cutover, business_ready]

    # -------------------------------------------------------------
    # 9. 编织 物业 FM 接管演练、搬迁放行与正式开业 (Phase 8)
    # -------------------------------------------------------------
    id_fm_handover = next_id()
    id_fm_training = next_id()
    id_fm_accept = next_id()
    id_occupancy_permit = next_id()
    id_day1_opening = next_id()
    id_pay_retention = next_id()

    pred_clean = str(clean_task["id"]) if clean_task else "103"
    pred_fm_train = f"{id_fm_handover},{','.join(pred_accept_parts)}"

    fm_handover = {
        "id": id_fm_handover,
        "name": "FM 运维图纸、技术资料与设备资产台账移交",
        "outline_level": 3,
        "duration_days": 8,
        "duration": 8,
        "calendar": "5D",
        "predecessors": pred_clean,
        "phase": "Phase 8  客户搬迁入驻与正式交付"
    }
    fm_training = {
        "id": id_fm_training,
        "name": "物业运维团队现场实操培训与带载应急演练",
        "outline_level": 3,
        "duration_days": 8,
        "duration": 8,
        "calendar": "5D",
        "predecessors": pred_fm_train,
        "phase": "Phase 8  客户搬迁入驻与正式交付"
    }
    fm_accept = {
        "id": id_fm_accept,
        "name": "物业 FM 正式接管签认",
        "outline_level": 3,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "5D",
        "predecessors": str(id_fm_training),
        "phase": "Phase 8  客户搬迁入驻与正式交付"
    }
    occ_preds = [str(id_fm_accept)]
    if "it_cutover" in packs:
        occ_preds.append(str(id_business_ready))
    elif iaq_final_milestone:
        occ_preds.append(str(iaq_final_milestone["id"]))
    occupancy_permit = {
        "id": id_occupancy_permit,
        "name": "签发全员搬迁入驻许可证 (Occupancy Permit)",
        "outline_level": 3,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "5D",
        "predecessors": ",".join(occ_preds),
        "phase": "Phase 8  客户搬迁入驻与正式交付"
    }

    # 移除旧的粗糙搬迁与移交节点，用严谨的总控流程挂接
    old_end_tasks = [t for t in result if any(k in t.get("name", "") for k in ["分批次搬迁入驻", "搬迁全部完成", "Site Handover", "场地正式移交"])]
    for rt in old_end_tasks:
        result.remove(rt)

    id_reloc = next_id()
    reloc_task = {
        "id": id_reloc,
        "name": "各业务线分批搬迁入驻与桌面技术支持",
        "outline_level": 3,
        "duration_days": 15,
        "duration": 15,
        "calendar": "5D",
        "predecessors": str(id_occupancy_permit),
        "phase": "Phase 8  客户搬迁入驻与正式交付"
    }
    day1_opening = {
        "id": id_day1_opening,
        "name": "Day 1 正式开业运营",
        "outline_level": 3,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "5D",
        "predecessors": str(id_reloc),
        "phase": "Phase 8  客户搬迁入驻与正式交付"
    }
    pay_retention = {
        "id": id_pay_retention,
        "name": "💰 商务里程碑：工程最终竣工结算完成与质保金封存 (5% 质保金满一年返还)",
        "outline_level": 3,
        "duration_days": 0,
        "duration": 0,
        "milestone": True,
        "calendar": "5D",
        "predecessors": str(id_day1_opening),
        "phase": "Phase 8  客户搬迁入驻与正式交付"
    }
    fm_nodes = [fm_handover, fm_training, fm_accept, occupancy_permit, reloc_task, day1_opening]
    if "fm_handover" not in packs:
        fm_nodes = []
    if "it_cutover" not in packs:
        it_nodes = []
    if "tdd" not in packs:
        tdd_nodes = []
    # office 专属包（家具安装 + 标准 IT/AV 安装 + 搬迁管理）承载变量，默认空/None
    office_ff_nodes = []
    furniture_deliver = None
    itav_install = None
    ff_move_ready = None

    # ============================================================
    # 9.1 金融：交易大厅高可用双电双网 / 金融级安防 (finance)
    # ============================================================
    trading_nodes, vault_nodes = [], []
    if "trading_desk" in packs:
        id_tr_a = next_id(); id_tr_b = next_id(); id_tr_c = next_id(); id_tr_d = next_id(); id_tr_r = next_id()
        trading_nodes = [
            {"id": id_tr_a, "name": "交易大厅双回路配电柜与隔离变压器冗余安装", "outline_level": 4, "duration_days": 12, "duration": 12, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": (str(site_prep_task["id"]) if site_prep_task else "50"), "phase": "Phase 6  实体施工阶段"},
            {"id": id_tr_b, "name": "交易工位双网口双路由综合布线 (不同物理管井进线)", "outline_level": 4, "duration_days": 10, "duration": 10, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_tr_a), "phase": "Phase 6  实体施工阶段"},
            {"id": id_tr_c, "name": "交易终端多屏支架与 KVM 集中部署", "outline_level": 4, "duration_days": 6, "duration": 6, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_tr_b), "phase": "Phase 6  实体施工阶段"},
            {"id": id_tr_d, "name": "双路 UPS 与网络主备自动切换 Failover 演练", "outline_level": 4, "duration_days": 2, "duration": 2, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_tr_c), "phase": "Phase 6  实体施工阶段"},
            {"id": id_tr_r, "name": "交易大厅高可用 (双电双网) 就绪", "outline_level": 4, "duration_days": 0, "duration": 0, "milestone": True, "calendar": "7D", "predecessors": str(id_tr_d), "phase": "Phase 6  实体施工阶段"},
        ]
    if "vault_security" in packs:
        id_va_a = next_id(); id_va_b = next_id(); id_va_c = next_id(); id_va_r = next_id()
        vault_nodes = [
            {"id": id_va_a, "name": "金库级防撬门与钢筋混凝土墙体重构加固", "outline_level": 4, "duration_days": 10, "duration": 10, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": (str(site_prep_task["id"]) if site_prep_task else "50"), "phase": "Phase 6  实体施工阶段"},
            {"id": id_va_b, "name": "生物识别门禁与视频安防矩阵部署", "outline_level": 4, "duration_days": 8, "duration": 8, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_va_a), "phase": "Phase 6  实体施工阶段"},
            {"id": id_va_c, "name": "安防联动与入侵报警系统全功能测试", "outline_level": 4, "duration_days": 3, "duration": 3, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_va_b), "phase": "Phase 6  实体施工阶段"},
            {"id": id_va_r, "name": "金融级物理安防与门禁验收", "outline_level": 4, "duration_days": 0, "duration": 0, "milestone": True, "calendar": "7D", "predecessors": str(id_va_c), "phase": "Phase 6  实体施工阶段"},
        ]

    # ============================================================
    # 9.2 生物医药：实验室通风 / 洁净室 ISO / 环评验收 (biopharma)
    # ============================================================
    lab_hvac_nodes, cleanroom_nodes, eia_nodes = [], [], []
    if "lab_hvac" in packs:
        id_lh_a = next_id(); id_lh_b = next_id(); id_lh_c = next_id(); id_lh_r = next_id()
        lab_hvac_nodes = [
            {"id": id_lh_a, "name": "实验室全新风 VAV 机组与补风管路安装", "outline_level": 4, "duration_days": 14, "duration": 14, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": (str(site_prep_task["id"]) if site_prep_task else "50"), "phase": "Phase 6  实体施工阶段"},
            {"id": id_lh_b, "name": "排风高效过滤与废气处理塔安装", "outline_level": 4, "duration_days": 10, "duration": 10, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_lh_a), "phase": "Phase 6  实体施工阶段"},
            {"id": id_lh_c, "name": "通风柜变风量面风速联调与压差梯度调试", "outline_level": 4, "duration_days": 4, "duration": 4, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_lh_b), "phase": "Phase 6  实体施工阶段"},
            {"id": id_lh_r, "name": "实验室通风与废气处理系统验收", "outline_level": 4, "duration_days": 0, "duration": 0, "milestone": True, "calendar": "7D", "predecessors": str(id_lh_c), "phase": "Phase 6  实体施工阶段"},
        ]
    if "cleanroom_iso" in packs:
        id_cr_a = next_id(); id_cr_b = next_id(); id_cr_c = next_id(); id_cr_r = next_id()
        cleanroom_nodes = [
            {"id": id_cr_a, "name": "洁净室彩钢板围护与 FFU 满布安装", "outline_level": 4, "duration_days": 16, "duration": 16, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": (str(site_prep_task["id"]) if site_prep_task else "50"), "phase": "Phase 6  实体施工阶段"},
            {"id": id_cr_b, "name": "洁净室环氧自流平地面与架空层施工", "outline_level": 4, "duration_days": 8, "duration": 8, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_cr_a), "phase": "Phase 6  实体施工阶段"},
            {"id": id_cr_c, "name": "HEPA 检漏与洁净度 (ISO 7/8) 第三方检测", "outline_level": 4, "duration_days": 4, "duration": 4, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_cr_b), "phase": "Phase 6  实体施工阶段"},
            {"id": id_cr_r, "name": "洁净室 ISO 环境认证通过", "outline_level": 4, "duration_days": 0, "duration": 0, "milestone": True, "calendar": "7D", "predecessors": str(id_cr_c), "phase": "Phase 6  实体施工阶段"},
        ]
    if "eia_approval" in packs:
        id_ei_a = next_id(); id_ei_b = next_id(); id_ei_c = next_id(); id_ei_r = next_id()
        eia_nodes = [
            {"id": id_ei_a, "name": "环保验收监测与三同时报建（竣工阶段）", "outline_level": 4, "duration_days": 12, "duration": 12, "calendar": "5D", "predecessors": (str(iaq_final_milestone["id"]) if iaq_final_milestone else "101"), "phase": "Phase 7  竣工验收与合规审批"},
            {"id": id_ei_b, "name": "废水/废气处理设施竣工调试与在线监测", "outline_level": 4, "duration_days": 10, "duration": 10, "calendar": "5D", "predecessors": str(id_ei_a), "phase": "Phase 7  竣工验收与合规审批"},
            {"id": id_ei_c, "name": "环保竣工验收与排污许可证核发", "outline_level": 4, "duration_days": 20, "duration": 20, "calendar": "5D", "predecessors": str(id_ei_b), "phase": "Phase 7  竣工验收与合规审批"},
            {"id": id_ei_r, "name": "环保环评验收通过 (法定前置放行)", "outline_level": 4, "duration_days": 0, "duration": 0, "milestone": True, "calendar": "5D", "predecessors": str(id_ei_c), "phase": "Phase 7  竣工验收与合规审批"},
        ]
        if id_ei_r:
            occ_preds.append(str(id_ei_r))
            occupancy_permit["predecessors"] = ",".join([p for p in occ_preds if p])

    # office：深度精保洁须在家具与 IT/AV 安装就绪后进行（家具摆场产尘），
    # 使 Closeout 链（精保洁→FM 接管→搬迁许可）正确等待家具/IT 就绪。
    if sec == "office" and clean_task and ff_move_ready is not None:
        _add_pred(clean_task, ff_move_ready["id"])

    # ============================================================
    # 9.3 半导体：微振基座 / 高纯特气 CDA / 防静电接地 (semiconductor)
    # ============================================================
    antivib_nodes, cda_nodes, esd_nodes = [], [], []
    if "anti_vibration" in packs:
        id_av_a = next_id(); id_av_b = next_id(); id_av_c = next_id(); id_av_r = next_id()
        antivib_nodes = [
            {"id": id_av_a, "name": "微振动独立筏板与弹簧隔振基座施工", "outline_level": 4, "duration_days": 16, "duration": 16, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": (str(site_prep_task["id"]) if site_prep_task else "50"), "phase": "Phase 6  实体施工阶段"},
            {"id": id_av_b, "name": "大型设备减振安装与激光调平", "outline_level": 4, "duration_days": 8, "duration": 8, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_av_a), "phase": "Phase 6  实体施工阶段"},
            {"id": id_av_c, "name": "微振动第三方测试达标（〔惯〕IEST VC；设计规范〔法〕GB 50463-2019，不是VC标准）", "outline_level": 4, "duration_days": 3, "duration": 3, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_av_b), "phase": "Phase 6  实体施工阶段"},
            {"id": id_av_r, "name": "微振动控制达标", "outline_level": 4, "duration_days": 0, "duration": 0, "milestone": True, "calendar": "7D", "predecessors": str(id_av_c), "phase": "Phase 6  实体施工阶段"},
        ]
    if "cda_piping" in packs:
        id_cd_a = next_id(); id_cd_b = next_id(); id_cd_c = next_id(); id_cd_r = next_id()
        cda_nodes = [
            {"id": id_cd_a, "name": "高纯特气 (Specialty Gas) 二次配管与惰性吹扫", "outline_level": 4, "duration_days": 14, "duration": 14, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": (str(site_prep_task["id"]) if site_prep_task else "50"), "phase": "Phase 6  实体施工阶段"},
            {"id": id_cd_b, "name": "高压压缩空气 (CDA) 管路与干燥机安装", "outline_level": 4, "duration_days": 8, "duration": 8, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_cd_a), "phase": "Phase 6  实体施工阶段"},
            {"id": id_cd_c, "name": "管路纯度与露点 (Particle/Dew Point) 测试", "outline_level": 4, "duration_days": 4, "duration": 4, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_cd_b), "phase": "Phase 6  实体施工阶段"},
            {"id": id_cd_r, "name": "高纯特气与 CDA 系统合格", "outline_level": 4, "duration_days": 0, "duration": 0, "milestone": True, "calendar": "7D", "predecessors": str(id_cd_c), "phase": "Phase 6  实体施工阶段"},
        ]
    if "esd_grounding" in packs:
        id_es_a = next_id(); id_es_b = next_id(); id_es_c = next_id(); id_es_r = next_id()
        esd_nodes = [
            {"id": id_es_a, "name": "整体微电子防静电等电位接地网敷设", "outline_level": 4, "duration_days": 10, "duration": 10, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": (str(site_prep_task["id"]) if site_prep_task else "50"), "phase": "Phase 6  实体施工阶段"},
            {"id": id_es_b, "name": "防静电架空地板与工作台接地", "outline_level": 4, "duration_days": 6, "duration": 6, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_es_a), "phase": "Phase 6  实体施工阶段"},
            {"id": id_es_c, "name": "接地电阻与静电衰减时间测试", "outline_level": 4, "duration_days": 3, "duration": 3, "work_weekend": True, "use_construction_cal": True, "calendar": "7D", "predecessors": str(id_es_b), "phase": "Phase 6  实体施工阶段"},
            {"id": id_es_r, "name": "防静电接地网验收", "outline_level": 4, "duration_days": 0, "duration": 0, "milestone": True, "calendar": "7D", "predecessors": str(id_es_c), "phase": "Phase 6  实体施工阶段"},
        ]

    # -------------------------------------------------------------
    # 9.4 通用办公：家具到货+摆场安装 + 标准 IT/AV 安装 + 搬迁方案 (office_ff_move)
    # 就地替换模板里单薄的「办公家具及软装进场就位安装」(furniture 包) 与「会议室AV安装」
    # (av 包)，保留其在两次空检之间的位置；补家具到货(长周期采购驱动)、FF&AV 就绪里程碑，
    # 并把深度精保洁 / 分批搬迁正确挂在家具与 IT 安装就绪之后（安装产尘）。
    # 仅对 office 行业且含 office_ff_move 包时织入。
    # -------------------------------------------------------------
    if "office_ff_move" in packs and sec == "office":
        first_iaq = next((t for t in result if '首次室内空气盲测' in t['name']), None)
        vent = next((t for t in result if '强排通风' in t['name']), None)
        clean_task_office = next((t for t in result if t['name'].startswith('深度精保洁')), None)
        move_in = next((t for t in result if '业主分批次搬迁入驻' in t['name']), None)
        furn_old = next((t for t in result if '办公家具及软装进场就位安装' in t['name']), None)
        av_old = next((t for t in result if '会议室AV安装' in t['name']), None)

        id_ff_deliver = next_id()
        id_ff_install = next_id()
        id_ff_itav = next_id()
        id_ff_ready = next_id()
        id_ff_moveplan = next_id()
        first_iaq_id = str(first_iaq['id']) if first_iaq else "101"

        furniture_deliver = {
            "id": id_ff_deliver,
            "name": "定制办公家具到货开箱、清点与验收 (Furniture Delivery & QA)",
            "outline_level": 4,
            "duration_days": 5, "duration": 5, "calendar": "5D",
            "predecessors": first_iaq_id,
            "phase": "Phase 6 物理竣工、收尾与空气治理",
            "activity_type": "procurement_receipt",
        }
        ff_install = {
            "id": id_ff_install,
            "name": "办公家具与软装摆场安装 (工位 / 会议 / 休闲区)",
            "outline_level": 4,
            "duration_days": 15, "duration": 15, "calendar": "5D",
            "predecessors": str(id_ff_deliver),
            "phase": "Phase 6 物理竣工、收尾与空气治理",
            "activity_type": "furniture",
        }
        itav_install = {
            "id": id_ff_itav,
            "name": "标准会议 AV 系统、网络点位与门禁安装调试",
            "outline_level": 4,
            "duration_days": 10, "duration": 10, "calendar": "5D",
            "predecessors": str(id_ff_install),
            "phase": "Phase 6 物理竣工、收尾与空气治理",
            "activity_type": "av",
        }
        ff_move_ready = {
            "id": id_ff_ready,
            "name": "家具与 IT/AV 安装就绪 (Furniture & AV Ready)",
            "outline_level": 4, "duration_days": 0, "duration": 0,
            "milestone": True, "calendar": "5D",
            "predecessors": f"{id_ff_install},{id_ff_itav}",
            "phase": "Phase 6 物理竣工、收尾与空气治理",
        }
        move_plan = {
            "id": id_ff_moveplan,
            "name": "搬迁方案编制、资产标签与分批搬迁计划 (Move Management)",
            "outline_level": 4,
            "duration_days": 7, "duration": 7, "calendar": "5D",
            "predecessors": str(id_ff_ready),
            "phase": "Phase 6 物理竣工、收尾与空气治理",
            "activity_type": "move_planning",
        }
        new_office = [furniture_deliver, ff_install, itav_install, ff_move_ready, move_plan]

        # 移除旧的薄任务（furniture 包 / av 包），并在家具原位置就地替换为整段 office 工序
        _remove_ids = {t['id'] for t in (furn_old, av_old) if t}
        if av_old is not None:
            try:
                result.remove(av_old)
            except ValueError:
                pass
        if furn_old is not None:
            _idx = result.index(furn_old)
            result[_idx:_idx + 1] = new_office
        else:
            result.extend(new_office)

        # 清理仍指向被删旧节点的悬挂前驱
        for _t in result:
            _np = []
            for _p in str(_t.get("predecessors", "")).split(","):
                _p = _p.strip()
                if not _p:
                    continue
                _m = re.match(r"(\d+)", _p)
                if _m and int(_m.group(1)) in _remove_ids:
                    continue
                _np.append(_p)
            _t["predecessors"] = ",".join(_np)

        # 散味（强排通风）等待家具+IT 安装就绪（安装产尘），再进入二次复测
        if vent is not None:
            _vp = [p for p in str(vent.get("predecessors", "")).split(",") if p.strip()]
            if str(id_ff_itav) not in _vp:
                _vp.append(str(id_ff_itav))
            vent["predecessors"] = ",".join(_vp)
        # 深度精保洁等待 FF&AV 就绪（安装产尘后才精保洁）
        if clean_task_office is not None:
            _cp = [p for p in str(clean_task_office.get("predecessors", "")).split(",") if p.strip()]
            if str(id_ff_ready) not in _cp:
                _cp.append(str(id_ff_ready))
            clean_task_office["predecessors"] = ",".join(_cp)
        # 分批搬迁入驻等待搬迁方案编制
        if move_in is not None:
            _mp = [p for p in str(move_in.get("predecessors", "")).split(",") if p.strip()]
            if str(id_ff_moveplan) not in _mp:
                _mp.append(str(id_ff_moveplan))
            move_in["predecessors"] = ",".join(_mp)

        office_ff_nodes = new_office

    # -------------------------------------------------------------
    # 10. 组装：行业专业系统包（level-3 汇总条 + level-4 工序 + 里程碑）
    # -------------------------------------------------------------
    def _system_package(summary_name, nodes, anchor_id, phase, sum_level=3):
        """专业系统包 = level-3 汇总条(0d) + level-4 工序 + 里程碑；首道正工期工序挂汇总条，汇总条挂锚点。

        让行业专业模块以'系统包'而非散细节呈现，符合总控 Master schedule 的层级（阶段L2→系统L3→工序L4）。
        """
        if not nodes:
            return []
        sid = next_id()
        first_real = next((n for n in nodes if n.get("duration_days", 0) > 0), None)
        if first_real is not None:
            first_real["predecessors"] = str(sid)
        summary = {
            "id": sid,
            "name": summary_name,
            "outline_level": sum_level,
            "duration_days": 0,
            "duration": 0,
            "predecessors": str(anchor_id) if anchor_id else (str(site_prep_task["id"]) if site_prep_task else "50"),
            "phase": phase,
        }
        return [summary] + nodes

    _anchor = str(site_prep_task["id"]) if site_prep_task else "50"
    trading_pkg = _system_package("金融交易系统（双电双网） Trading Floor Systems", trading_nodes, _anchor, "Phase 6  实体施工阶段")
    vault_pkg = _system_package("金融级物理安防 Vault & Security", vault_nodes, _anchor, "Phase 6  实体施工阶段")
    lab_hvac_pkg = _system_package("实验室通风与废气 Lab HVAC & Exhaust", lab_hvac_nodes, _anchor, "Phase 6  实体施工阶段")
    cleanroom_pkg = _system_package("洁净室与微环境 Cleanroom & Micro-environment", cleanroom_nodes, _anchor, "Phase 6  实体施工阶段")
    antivib_pkg = _system_package("微振动控制（GB 50463 隔振设计）", antivib_nodes, _anchor, "Phase 6  实体施工阶段")
    cda_pkg = _system_package("高纯特气与 CDA Specialty Gas & CDA", cda_nodes, _anchor, "Phase 6  实体施工阶段")
    esd_pkg = _system_package("防静电接地 ESD Grounding", esd_nodes, _anchor, "Phase 6  实体施工阶段")

    # 把长周期设备到货里程碑拉上关键路径：作为行业主系统包首道安装工序的前置（与 site_prep 并行 FS，取较晚者）。
    # 这才是 Master schedule 的核心——长周期采购是完工驱动，而非孤立的一条 15d 活动。
    if longlead_delivery_id:
        if sec in ("gaming", "finance"):
            _add_pred(dc_struct, longlead_delivery_id)          # 机房先行岛首道工序
        elif sec == "biopharma":
            _add_pred(cleanroom_nodes[0], longlead_delivery_id)  # 洁净室首道工序
        elif sec == "semiconductor":
            _add_pred(cda_nodes[0], longlead_delivery_id)        # 高纯特气/CDA 首道工序
        elif sec == "office" and furniture_deliver is not None:
            _add_pred(furniture_deliver, longlead_delivery_id)   # 办公家具安装首道工序

    final_tasks = []

    for t in result:
        final_tasks.append(t)
        if t is kickoff_task and tdd_nodes:
            final_tasks.extend(tdd_nodes)
        elif t is dd_finish_task and longlead_pkg:
            final_tasks.extend(longlead_pkg)
        elif t is site_prep_task:
            for _pkg in (trading_pkg, vault_pkg, lab_hvac_pkg, cleanroom_pkg, antivib_pkg, cda_pkg, esd_pkg, dc_nodes, mocap_nodes):
                final_tasks.extend(_pkg)
        elif t is concealed_inspect_task and "dev_zone" in packs:
            # 高密研发测试专区须在全部隐蔽工程验收（天花隐蔽，最晚一个）通过后方可
            # 进场；将其列于隐蔽验收之后，使前置关系在列表中为后向引用，避免前向引用
            # 被求解器忽略（dev_zone 前置=concealed_inspect_task）。
            final_tasks.append(dev_zone)
        elif t is iaq_final_milestone:
            final_tasks.extend(it_nodes)
            final_tasks.extend(eia_nodes)
        elif t is clean_task:
            final_tasks.extend(fm_nodes)

    # 面积呼应（分类型）：实体施工按 0.32 次幂，测试按固定底限，法定按固定时钟，不再一刀切
    _all_module_groups = [tdd_nodes, dc_nodes, mocap_nodes, it_nodes, fm_nodes,
                          trading_pkg, vault_pkg, lab_hvac_pkg, cleanroom_pkg,
                          eia_nodes, antivib_pkg, cda_pkg, esd_pkg, office_ff_nodes]
    # 注：longlead_pkg 不进入 _all_module_groups —— 采购提前期属供应链属性，固定不随面积缩放
    if "dev_zone" in packs:
        _all_module_groups.append([dev_zone])
    # Specialist rooms are sized by their own room area, once; the office area never stretches them.
    _rooms = _specialist_rooms()
    _room_groups = {id(g): k for k, g in [("datacenter", dc_nodes), ("mocap", mocap_nodes),
                    ("trading_desk", trading_pkg), ("lab_hvac", lab_hvac_pkg), ("cleanroom_iso", cleanroom_pkg),
                    ("anti_vibration", antivib_pkg), ("cda_piping", cda_pkg), ("esd_grounding", esd_pkg)]}
    if "dev_zone" in packs:
        _room_groups[id(_all_module_groups[-1])] = "dev_zone"
    _room_area = (facts or {}).get("specialist_area_sqm")
    for _grp in _all_module_groups:
        _room = _room_groups.get(id(_grp))
        for _n in _grp:
            if _n.get("duration_days", 0) > 0:
                if _room:
                    _default = _rooms["default_sqm"][_room]
                    room_field = {"datacenter":"server_room_area_sqm", "mocap":"acoustic_room_area_sqm"}.get(_room)
                    room_area = (facts or {}).get(room_field, _room_area)
                    _sd = _scale(room_area, _n["duration_days"], _n.get("name", ""), base_area=_default) if room_area else int(_n["duration_days"])
                    _n["specialist_room_scale"] = dict(room=_room, default_sqm=_default, room_sqm=room_area or _default,
                                                       base_days=int(_n["duration_days"]), basis="specialist_room_area")
                else:
                    _sd = _scale(area_sqm, _n["duration_days"], _n.get("name", ""))
                _n["duration_days"] = _sd
                _n["duration"] = _sd

    # Stable module identity survives renumbering and display names.
    for module_id, group in [
        ('tdd',tdd_nodes),('datacenter',dc_nodes),('mocap',mocap_nodes),
        ('it_cutover',it_nodes),('core_handover',fm_nodes),
        ('trading_desk',trading_pkg),('vault_security',vault_pkg),
        ('lab_hvac',lab_hvac_pkg),('cleanroom_iso',cleanroom_pkg),
        ('eia_approval',eia_nodes),('anti_vibration',antivib_pkg),
        ('cda_piping',cda_pkg),('esd_grounding',esd_pkg),('office_ff_move',office_ff_nodes)]:
        for position, node in enumerate(group):
            node['module_id'] = module_id
            node['module_step'] = position
            node['activity_uid'] = 'pmp.module.'+module_id+'.'+str(position)
    if 'dev_zone' in packs:
        dev_zone.update(module_id='dev_zone',module_step=0,activity_uid='pmp.module.dev_zone.0')

    existing_ids = {t["id"] for t in final_tasks}
    leftover = [tdd_nodes, dc_nodes, mocap_nodes, it_nodes, fm_nodes,
                trading_pkg, vault_pkg, lab_hvac_pkg, cleanroom_pkg,
                eia_nodes, antivib_pkg, cda_pkg, esd_pkg, office_ff_nodes]
    if longlead_pkg:
        leftover.insert(1, longlead_pkg)
    for group in leftover:
        for n in group:
            if n["id"] not in existing_ids:
                final_tasks.append(n)
                existing_ids.add(n["id"])

    logger.info("  -> [Sector Weaver] 织入 %s packs=%s (总任务数: %d)",
                sec, sorted(packs), len(final_tasks))

    # 城市法定审批时钟（仅 facts["city"] 提供时覆盖；默认不改动）
    _apply_city_statutory(final_tasks, (facts or {}).get("city"))

    # 顶层「关键里程碑总控」汇总（关键里程碑提升为 L2 顶部汇总条）移至 CPM 解算之后
    # 再做（见 main.py 解算后调用 _build_control_milestone_rollup）：求解器按列表顺序解算、
    # 会忽略晚于自身的「前置」，若把末尾阶段的收尾里程碑提前到顶部会丢失其真实依赖、
    # 令里程碑漂到项目起点。解算完成、日期已定后重排仅影响呈现，不改变任何工期与网络。
    return final_tasks
