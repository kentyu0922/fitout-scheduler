# -*- coding: utf-8 -*-
"""
core/calibration.py — 工期校准引擎

从 main.py 提取的业务校准逻辑：
  * 面积非线性缩放 (scale_factor = (area / base_area) ^ 0.3)
  * 图审/施工许可/BOQ/RFP 工期下限断言
  * 复杂特种施工最小工期断言
"""
import math
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

COMPLEX_SPECIAL_CONSTRUCTION_MIN_DAYS = 80

# 穿插实体工序（大机电主管/天花吊顶龙骨/二次机电保温等）保底最小工期（防过度压缩，确保 1000㎡ 也有 10~12 天施工期，隔墙隐蔽与天花隐蔽间隔保持至少 2 周）
INTERSPERSED_TRADE_MIN_DAYS = 10
INTERSPERSED_TRADE_KEYWORDS = ["大机电", "主管桥架", "主管预埋", "吊顶龙骨", "天花吊顶龙骨", "天花龙骨", "二次机电", "二次机电保温", "机电保温"]

# 测试与联调(T&C)工期下限：小面积经面积缩放后易被压到 3-4 天，不足以完成系统联动调试。
# 设 ≥6 工作日下限（大面积仍按 scale 自然放大）。
TC_MIN_DAYS = 6
TC_KEYWORDS = ["系统联动", "联调", "T&C", "测试与联调", "全系统联动调试"]

# 邀请招标：从收到 RFP 到回标（工作日）。公开招标仍走法定等标期。
INVITE_BID_RETURN_KWS = (
    "回标", "标书编制", "编制投标文件与投标截止递交", "投标单位施工方案深化",
    "总包编制投标文件与回标", "总包深化概念方案",
    "设计概念方案投标编制", "方案竞标", "概念方案投标",
)


def invite_rfp_to_return_workdays(area: float) -> int:
    """PM Master Schedule: invite RFP receipt → bid return.

    Small jobs ~7wd; typical 10–11wd; ≥10,000㎡ = 12wd.
    """
    a = float(area or 0)
    if a < 1500:
        return 7
    if a < 5000:
        return 10
    if a < 10000:
        return 11
    return 12


def _is_complex_special(area: int, addons_str: str) -> bool:
    """判断是否为复杂特种施工项目（实验室/洁净室/冷却塔/超大面积）。"""
    has_lab = "Lab" in addons_str or "实验室" in str(addons_str)
    has_cleanroom = "Cleanroom" in addons_str or "洁净" in str(addons_str)
    has_cooling_tower = "Cooling_Tower" in addons_str or "冷却塔" in str(addons_str)
    return has_lab or has_cleanroom or has_cooling_tower or area >= 5000


def calibrate_durations(
    tasks: List[Dict[str, Any]],
    permit_info: Dict[str, Any],
    area: int,
    template_base_area: int = 8000,
    addons_str: str = "",
    cost_10k_rmb: float = None,
    log: logging.Logger = None,
    bidding: str = None,
) -> List[Dict[str, Any]]:
    """广义全谱系面积与投资额连续演进工期校准引擎 (500㎡ ~ 50,000㎡ 连续平滑无断层)。

    四维工程物理与管理演进方程：
      1. 实体施工流水线：工作面并发度模型 (指数 0.32)
      2. 设计深化与图纸生产：图纸张数与信息协同模型 (指数 0.38，带工艺成套底线)
      3. 商务招采与标书编制：供应链询价响应模型 (指数 0.25，受招投标法底线保护)
      4. 工程造价算量测算：清单工程量测算模型 (指数 0.35，废除旧版 5 天硬截断)
      5. 资本治理与定标决策：投资额与管理授权阶梯模型 (指数 0.35 / 对数演进)
    """
    if log is None:
        log = logger

    base_area_flt = float(template_base_area) if template_base_area and template_base_area > 0 else 8000.0
    ratio = max(0.05, area / base_area_flt)
    is_complex = _is_complex_special(area, addons_str)

    SCALE_EXP_BUILD = 0.32
    SCALE_EXP_DESIGN = 0.38
    SCALE_EXP_TENDER = 0.25
    SCALE_EXP_ESTIMATE = 0.35
    SCALE_EXP_GOVERNANCE = 0.35

    current_phase_name = ""
    for t in tasks:
        level = t.get("outline_level", t.get("level", 1))
        t["level"] = level
        t["outline_level"] = level
        name = str(t.get("name", ""))
        if level <= 2:
            current_phase_name = name

        if t.get('activity_type'):
            # Physical work is calculated exclusively by the productivity engine.
            continue

        base_dur = int(round(float(t.get("duration_days", t.get("duration", 0)))))
        if base_dur <= 0:
            continue

        # -----------------------------------------------------------------
        # 维度 1: 施工图与深化设计出图 (CD 阶段) —— 图纸张数与协同复杂度
        # -----------------------------------------------------------------
        if any(kw in name for kw in ["全专业施工图深化", "全专业施工图全套深化", "施工图全套深化", "全套施工图", "施工图深化出图", "施工图出图"]):
            # 8000㎡ 基准为 26 工日，随面积按 0.38 次方连续演进，保底 12 工日
            base_dur = max(12, int(round(26 * math.pow(ratio, SCALE_EXP_DESIGN))))
            if is_complex:
                base_dur = max(base_dur, 22)

        elif any(kw in name for kw in ["概念方案深化", "平面布局规划", "空间规划", "主笔方案深化", "概念深化", "功能分区"]):
            base_dur = max(5, int(round(8 * math.pow(ratio, SCALE_EXP_ESTIMATE))))

        elif any(kw in name for kw in ["效果图 3D", "效果图", "材质选型"]):
            base_dur = max(5, int(round(8 * math.pow(ratio, SCALE_EXP_ESTIMATE))))

        elif any(kw in name for kw in ["初步设计 SD", "初设出图"]):
            base_dur = max(4, int(round(6 * math.pow(ratio, SCALE_EXP_ESTIMATE))))

        elif any(kw in name for kw in ["LDI", "本地化翻图", "甲级院"]):
            base_dur = max(3, int(round(5 * math.pow(ratio, 0.30))))

        elif any(kw in name for kw in ["消防设计专篇", "节能专篇", "环保专篇"]):
            base_dur = max(3, int(round(4 * math.pow(ratio, 0.25))))

        # -----------------------------------------------------------------
        # 维度 2: 工程造价算量与控制价 (BOQ) —— 彻底废除旧版硬编码截断
        # -----------------------------------------------------------------
        elif any(kw in name for kw in ["工程量清单", "BOQ", "控制价测算"]):
            # 8000㎡ 基准为 10 工日，平滑演进：1000㎡ 约 5d，20000㎡ 约 14d
            base_dur = max(5, int(round(10 * math.pow(ratio, SCALE_EXP_ESTIMATE))))

        # -----------------------------------------------------------------
        # 维度 3: 招采回标窗口 (RFP 收到 → 回标)
        # 邀请招标按面积分档工作日；公开招标走法定等标期。不得把编制与等标揉成 16 工作日。
        # -----------------------------------------------------------------
        elif any(kw in name for kw in INVITE_BID_RETURN_KWS):
            is_public = (
                (bidding or "").lower() == "public"
                or "法定等标期" in name
                or "公开" in current_phase_name
            )
            if is_public:
                base_dur = max(15, int(round(20 * math.pow(ratio, 0.10))))
            else:
                base_dur = invite_rfp_to_return_workdays(area)

        # 清标质询与澄清回函 (必须置于清标前，防止被“清标”关键词覆盖)
        elif any(kw in name for kw in ["清标质询", "技术与造价质询", "质询(RFI)", "质询函", "澄清回函"]):
            base_dur = max(2, int(round(3 * math.pow(ratio, 0.30))))

        # 商务标清单逐行清标与技术偏离度分析
        elif any(kw in name for kw in ["逐行清标", "总价包干清标", "商务清标", "清标"]):
            base_dur = max(3, int(round(6 * math.pow(ratio, 0.32))))

        # 总包述标答辩 (现场考核 4选2)
        elif any(kw in name for kw in ["述标答辩", "述标面试", "述标汇报", "述标"]):
            base_dur = 2

        # 面对面商务谈判与 VE 价值工程降本研讨
        elif any(kw in name for kw in ["VE价值工程", "VE 降本", "二轮澄清", "二轮方案优化", "二轮谈判", "商务谈判与 VE"]):
            base_dur = max(2, int(round(3 * math.pow(ratio, 0.30))))

        # 总包向分包核价及申请集团特批底价
        elif any(kw in name for kw in ["特批底价", "核价及申请集团特批", "申请集团特批"]):
            base_dur = max(2, int(round(2.5 * math.pow(ratio, 0.25))))

        # 提交终版密封报价 (BAFO一口价)
        elif any(kw in name for kw in ["终版密封报价", "BAFO"]):
            base_dur = 1

        # 定标报告呈批与发放中标通知书 (LOA)
        elif any(kw in name for kw in ["定标报告呈批", "定标呈批", "定标审批", "定标决策", "定标报告备案", "中标通知书 (LOA)", "中标通知书审批发出"]):
            base_dur = max(2, int(round(2.2 * math.pow(ratio, SCALE_EXP_GOVERNANCE))))

        # 施工总承包合同谈判与签约盖章
        elif any(kw in name for kw in ["合同谈判与签约", "合同谈判", "合同条款谈判", "合同签署", "合同签订", "合同签约盖章"]):
            base_dur = max(3, int(round(4 * math.pow(ratio, SCALE_EXP_TENDER))))

        elif any(kw in name for kw in ["设计任务书与设计招标文件", "施工总包招标文件 (RFP) 编制", "招标文件 (RFP) 编制", "招标文件编制与行政备案"]):
            base_dur = max(3, int(round(4 * math.pow(ratio, SCALE_EXP_TENDER))))

        elif any(kw in name for kw in ["资格预审", "Pre-Q", "短名单拟定"]):
            base_dur = max(3, int(round(4 * math.pow(ratio, SCALE_EXP_TENDER))))

        # -----------------------------------------------------------------
        # 维度 4: 法定报批报建底线断言 (Permit & Fire Review)
        # -----------------------------------------------------------------
        elif "图审" in name:
            min_rev = permit_info.get("review_days_min", 7)
            if is_complex or area >= 5000:
                min_rev = max(min_rev, 12 if area < 15000 else 15)
            base_dur = max(base_dur, min_rev)

        elif "施工许可" in name and "免办" not in name:
            # 稳健合规推演：资料网签(3d) + 住建局承诺审批(5d) + 补正领证缓冲(2d) = 稳健基准 10 工作日
            min_p = max(10, permit_info.get("permit_days_min", 5))
            base_dur = max(base_dur, min_p)

        elif ("备案" in name and "入苏" not in name):
            min_p = permit_info.get("permit_days_min", 5)
            base_dur = max(base_dur, min_p)

        # -----------------------------------------------------------------
        # 维度 5: 实体物理施工流水线 (Physical Construction)
        # -----------------------------------------------------------------
        else:
            if not any(kw in name for kw in [
                "Kick Off", "启动会", "移交", "消杀", "保洁", "空气检测", "空气质量", "复测", "盲测", "Brief", "答疑", "踏勘", "面试", "答辩"
            ]):
                base_dur = max(1, int(round(base_dur * math.pow(ratio, SCALE_EXP_BUILD))))

        # 穿插工序保底工期（大机电主管/天花龙骨等）
        if any(kw in name for kw in INTERSPERSED_TRADE_KEYWORDS) and base_dur > 0:
            base_dur = max(base_dur, INTERSPERSED_TRADE_MIN_DAYS)

        # 测试与联调 (T&C) 工期保底
        if any(kw in name for kw in TC_KEYWORDS) and base_dur > 0:
            base_dur = max(base_dur, TC_MIN_DAYS)

        t["duration_days"] = base_dur
        t["duration"] = base_dur

    return tasks


def validate_complex_construction(
    tasks: List[Dict[str, Any]],
    area: int,
    addons_str: str = "",
    log: logging.Logger = None,
) -> bool:
    """复杂特种施工最小工期断言。

    从 main.py L193-202 迁入。返回 True 表示通过，False 表示工期可能失真。
    """
    if log is None:
        log = logger

    if not _is_complex_special(area, addons_str):
        return True

    max_single_construction = max(
        (t.get("duration_days", 0) for t in tasks
         if t.get("outline_level", t.get("level", 3)) >= 3
         and not t.get("milestone", False)
         and t.get("duration_days", 0) > 0),
        default=0
    )
    if max_single_construction < COMPLEX_SPECIAL_CONSTRUCTION_MIN_DAYS:
        log.warning(
            f"  [PM业务校准] [WARNING]: 实体施工最长工期 {max_single_construction} 天 "
            f"< 门槛 {COMPLEX_SPECIAL_CONSTRUCTION_MIN_DAYS} 天，工期复用失真风险！"
        )
        return False
    return True
