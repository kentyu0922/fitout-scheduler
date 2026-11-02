# -*- coding: utf-8 -*-
"""
permit_expert.py - 政务与法门合规 SOP 库核心规则引擎 (专家 A)

v3 收敛说明：
- 城市免办限额数据已外置至 config/city_permit.json（单源，随仓库发布），
  本模块仅负责加载与查询，不再硬编码年表。
- 未知城市回退内置「全国通用标准」静态兜底；【不触发任何网络调用】。
包含：
- ASAP-0: 开工日期三级优先级判定 (显式指定 > 实时日期 > 常规推演)
- SOP-A1: 全国城市施工许可证免办限额查询（静态规则库）
- SOP-A3: 《招标投标法》400万红线与发包模式判定
- SOP-A4: 两次室内空气采样检测与季节强排通风工期对齐
"""

import os
import json
import datetime
from typing import Dict, Any, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "..", "config", "city_permit.json")

# 加载失败时的内置兜底（与 config/city_permit.json 的 default 保持一致）
_FALLBACK_DEFAULT = {
    "city": "全国通用", "province": "全国通用", "policy_doc": "住建部国家通用标准",
    "area_threshold": 300, "cost_threshold_10k": 100, "rule_type": "AND_EXEMPT",
    "exempt_desc": "建筑面积 < 300平米 且 工程造价 < 100万元",
    "mandatory_desc": "建筑面积 >= 300平米 或 工程造价 >= 100万元",
    "review_days_min": 7, "permit_days_min": 5, "joint_completion_days_min": 18
}


def _load_city_db() -> Dict[str, Any]:
    """加载 config/city_permit.json；失败则回退内置 default（静态，不联网）。

    查询优先级：cities(城市名子串) -> provinces(省份名子串) -> default(全国通用)。
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        cities = data.get("cities", {})
        provinces = data.get("provinces", {})
        default = data.get("default", _FALLBACK_DEFAULT)
        return {"cities": cities, "provinces": provinces, "default": default}
    except Exception:
        return {"cities": {}, "provinces": {}, "default": _FALLBACK_DEFAULT}


_CITY_DB = _load_city_db()


def _match_province(city_name: str) -> Optional[str]:
    """按省份名子串匹配（含'省/市/自治区/维吾尔/回族/壮族'等后缀容错）。

    优先查 city_province_map（常见地级市 -> 省份），再回退省份名子串匹配。
    """
    provinces = _CITY_DB.get("provinces", {})
    bare = (city_name.replace("市", "").replace("省", "")
            .replace("自治区", "").replace("维吾尔", "").replace("回族", "")
            .replace("壮族", "").replace("特别行政区", "").strip())
    # 1) 常见地级市 -> 省份 直查
    if bare in _CITY_PROVINCE_MAP:
        prov = _CITY_PROVINCE_MAP[bare]
        if prov in provinces:
            return prov
    # 2) 省份名子串匹配（如'宁夏银川'->'宁夏回族自治区'）
    for k in provinces.keys():
        if k in city_name or city_name in k:
            return k
    # 3) 去后缀再比（如'内蒙古呼和浩特'->'内蒙古'）
    for k in provinces.keys():
        kb = (k.replace("市", "").replace("省", "")
              .replace("自治区", "").replace("维吾尔", "").replace("回族", "")
              .replace("壮族", "").replace("特别行政区", "").strip())
        if kb and (kb in bare or bare in kb):
            return k
    return None


# 常见地级市 -> 省级行政区（用于裸城市名回退省份基线；未列出的地级市最终回退全国 default）
_CITY_PROVINCE_MAP = {
    "无锡": "江苏省", "常州": "江苏省", "南通": "江苏省", "徐州": "江苏省", "苏州": "江苏省", "南京": "江苏省",
    "温州": "浙江省", "绍兴": "浙江省", "嘉兴": "浙江省", "金华": "浙江省", "台州": "浙江省", "宁波": "浙江省", "杭州": "浙江省",
    "佛山": "广东省", "珠海": "广东省", "惠州": "广东省", "中山": "广东省", "东莞": "广东省", "广州": "广东省", "深圳": "广东省",
    "泉州": "福建省", "厦门": "福建省", "福州": "福建省",
    "烟台": "山东省", "潍坊": "山东省", "济南": "山东省", "青岛": "山东省",
    "唐山": "河北省", "保定": "河北省", "石家庄": "河北省",
    "洛阳": "河南省", "郑州": "河南省",
    "襄阳": "湖北省", "武汉": "湖北省",
    "株洲": "湖南省", "岳阳": "湖南省", "长沙": "湖南省",
    "芜湖": "安徽省", "合肥": "安徽省",
    "赣州": "江西省", "南昌": "江西省",
    "唐山": "河北省",
    "洛阳": "河南省",
    "绵阳": "四川省", "成都": "四川省",
    "遵义": "贵州省", "贵阳": "贵州省",
    "大理": "云南省", "昆明": "云南省",
    "桂林": "广西壮族自治区", "南宁": "广西壮族自治区",
    "三亚": "海南省", "海口": "海南省",
    "宝鸡": "陕西省", "西安": "陕西省",
    "天水": "甘肃省", "兰州": "甘肃省",
    "克拉玛依": "新疆维吾尔自治区", "乌鲁木齐": "新疆维吾尔自治区",
    "包头": "内蒙古自治区", "呼和浩特": "内蒙古自治区",
    "大同": "山西省", "太原": "山西省",
    "鞍山": "辽宁省", "大连": "辽宁省", "沈阳": "辽宁省",
    "吉林": "吉林省", "长春": "吉林省",
    "大庆": "黑龙江省", "哈尔滨": "黑龙江省",
    "银川": "宁夏回族自治区",
    "西宁": "青海省",
    "拉萨": "西藏自治区",
}



def resolve_project_start_date(user_start_date: Optional[str] = None, is_urgent: bool = False) -> str:
    """
    开工日期三级优先级判定函数：
    1. 优先级 1 (最高): 用户显式指定日期 (如 '2026-10-01') ➔ 100% 优先遵从指定日
    2. 优先级 2: 用户未指定 + 强调紧急/赶工 (is_urgent=True) ➔ 强扣当前实时日期
    3. 优先级 3: 未指定且常规推演 ➔ 默认今天
    """
    if user_start_date:
        return user_start_date
    today = datetime.date.today()
    return today.strftime("%Y-%m-%d")


def query_city_permit_rule(city_name: str,
                           area_sqm: Optional[float] = None,
                           cost_10k_rmb: Optional[float] = None) -> Dict[str, Any]:
    """SOP-A1: 动态查询城市施工许可证办理条件与免办限额（静态规则库，无网络调用）"""
    matched_city = None
    for k in _CITY_DB["cities"].keys():
        if k in city_name or city_name in k:
            matched_city = k
            break

    if not matched_city:
        # 城市未收录：尝试按省份名匹配省级基线
        prov = _match_province(city_name)
        if prov:
            info = dict(_CITY_DB["provinces"][prov])
            info["city"] = city_name
            info["province"] = prov
            info["policy_doc"] = info.get("policy_doc", "") + "(省份基线兜底)"
        else:
            # 省份也未收录：回退全国通用标准（静态兜底），绝不触发 search_web
            info = dict(_CITY_DB["default"])
            info["city"] = city_name
            info["province"] = "全国通用(兜底)"
            info["policy_doc"] = "住建部国家通用标准(兜底)"
    else:
        info = dict(_CITY_DB["cities"][matched_city])

    if area_sqm is not None and cost_10k_rmb is not None:
        if info["rule_type"] == "OR_EXEMPT":
            is_exempt = (area_sqm < info["area_threshold"]) or (cost_10k_rmb < info["cost_threshold_10k"])
        else:
            is_exempt = (area_sqm < info["area_threshold"]) and (cost_10k_rmb < info["cost_threshold_10k"])

        info["input_area"] = area_sqm
        info["input_cost"] = cost_10k_rmb
        info["is_exempt"] = is_exempt
        info["is_mandatory"] = not is_exempt
    elif area_sqm is not None and cost_10k_rmb is None:
        # P2-15: cost UNKNOWN must never become a fake default number.
        # OR_EXEMPT cities: area below the threshold alone legally exempts the
        # permit, so an area-only fallback is valid (disclosed as such).
        # AND cities: both conditions are required, so the requirement itself
        # is UNKNOWN — reported as None, never guessed.
        info["input_area"] = area_sqm
        info["input_cost"] = None
        if info["rule_type"] == "OR_EXEMPT":
            info["is_exempt"] = area_sqm < info["area_threshold"]
            info["cost_basis"] = ("area_only — OR rule: area below threshold alone exempts; "
                                  "cost UNKNOWN, no default value invented")
        else:
            info["is_exempt"] = None
            info["cost_basis"] = ("UNKNOWN — AND rule requires both area and cost; "
                                  "cost not provided, requirement undetermined (conservative)")
        info["is_mandatory"] = None if info["is_exempt"] is None else (not info["is_exempt"])

    return info


def calc_tender_duration(bidding: str = None, project_type: str = None, cost_10k_rmb: int = 500) -> Dict[str, Any]:
    """SOP-A3: 《招标投标法》招标方式判定。

    新契约: 由显式 `bidding` 决定 (public/invite)，不再从模板键里解析 "SOE"。
    保留 `project_type` 兼容旧调用 (SOE/GOV/国企/政府 -> public)。
    """
    if bidding is None:
        # 旧版兼容: 资金来源推导
        bidding = "public" if (project_type or "").upper() in ["SOE", "GOV", "STATE_OWNED", "国企", "政府", "公募"] else "invite"

    is_public = str(bidding).lower() in ["public", "open", "公开"]

    if is_public:
        return {
            "procurement_mode": "公开招标 (Open Public Tendering, 法定流程)",
            "bidding_prep_days": 15,
            "bid_submission_days": 20,
            "eval_days": 5,
            "total_procurement_days": 25,
            "legal_basis": ("《招标投标法》: 公告/招标文件发售≥5日, 投标截止≥20日(第24条), "
                            "评标委员会5人以上单数且技术经济专家≥2/3, 中标候选人公示≥3日, 中标通知书30日内签约")
        }
    else:
        return {
            "procurement_mode": "邀请招标 (Invited Tendering / D&B 邀请比选, 敏捷多轮流程)",
            "bidding_prep_days": 9,
            "bid_submission_days": 10,
            "eval_days": 14,
            "total_procurement_days": 33,
            "legal_basis": ("非依法必须公开招标项目（外企/私企自主发包）：采用国际商业邀请比选全流程（30~35 工作日）"
                            "（RFP编制审批/正式发标/现场踏勘答疑/10d概念深化与报价备标/R1清标分析/方案述标面试与答辩/R2商务谈判/R3 BAFO/综合定标/发出LOI/合同谈判签署），专业从容。")
        }


def calc_ventilation_duration(completion_month: int = 7) -> Dict[str, Any]:
    """SOP-A4: 竣工通风阶段季节气温动态工期计算 (无口语化词汇)"""
    if completion_month in [6, 7, 8]:
        return {
            "season": "夏季",
            "ventilation_workdays": 45,
            "ventilation_calendar_days": 60,
            "sop_name": "竣工强排通风与空气净化散味阶段 (夏季散味加速)",
            "desc": "利用夏季自然较高气温加速释放，安排 45 工作日 (60日历日) 强排风与新风全负荷运行。"
        }
    elif completion_month in [11, 12, 1, 2]:
        return {
            "season": "冬季",
            "ventilation_workdays": 75,
            "ventilation_calendar_days": 110,
            "sop_name": "竣工强排通风与空气净化散味阶段 (冬季长效强排)",
            "desc": "针对冬季低温分子活性较低特点，动态拉长强排通风工期至 75 工作日 (110日历日)，确保采样完全达标。"
        }
    else:
        return {
            "season": "春秋季",
            "ventilation_workdays": 60,
            "ventilation_calendar_days": 90,
            "sop_name": "竣工强排通风与空气净化散味阶段 (3个月标准强排)",
            "desc": "标准常温状态，执行刚性 60 工作日 (90日历日 / 3个月) 全场新风及强排风强排。"
        }
