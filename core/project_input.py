"""One intake contract for conversational facts, JSON, spreadsheets and CLI.

Natural language is extracted by the agent, never guessed by this module.
Source statements are not independently verified facts. Defaults are policies.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.6"
from core.furniture_installation import NUMBER_FIELDS as FURNITURE_NUMBERS, TEXT_FIELDS as FURNITURE_TEXT, input_issues as furniture_install_issues
from core.module_catalog import CATALOG, STATES, normalize_state, resolve_modules
REQUIRED_CHOICES = {
    "contracting_strategy": ["DB", "DBB", "GC", "EPC", "CM", "SPLIT"],
    "sector": ["office", "gaming", "finance", "biopharma", "semiconductor", "datacenter", "commercial"],
    "procurement_method": ["invite", "public"],
}
REQUIRED = ("city", "area_sqm", "sector", "delivery", "bidding")
ALIASES = {
    "area": "area_sqm", "delivery_mode": "delivery", "contracting_strategy": "delivery", "procurement_method": "bidding", "target_movein_date": "target_date",
    "site_calendar": "calendar_profile", "phasing_zones": "workfronts",
    "cost": "cost_10k_rmb", "tdd": "prelease_tdd",
}
CHOICES = {
    'construction_handover': {'unconfirmed','whole','sectional'},
    "target_response": {"ask", "keep", "assess"},
    "industry_work_profile": {"sector_review", "general_office"},
    "delivery_profile": {"lean", "full"},
    "space_use": {"unknown", "office_only", "office_with_specialist"},
    "sector": set(REQUIRED_CHOICES["sector"]),
    "delivery": set(REQUIRED_CHOICES["contracting_strategy"]), "bidding": set(REQUIRED_CHOICES["procurement_method"]),
    "site_condition": {"full_strip", "shell", "cat_b_existing", "renovation_partial", "unknown"},
    "calendar_profile": {"5D", "6D", "7D"}, "authority_profile": {"STANDARD", "UNKNOWN"},
    "mode": {"plan", "review", "scenario"}, "zoning_mode": {"off", "auto", "manual"},
    "sectional": {"auto", "on", "off"},
    "building_restriction": {"none", "no_weekend_work"},
    "external_power_status": {"sufficient", "upgrade_needed", "needs_confirmation"},
}
VALUE_ALIASES = {
    "target_response": {"先提示再选择":"ask", "保留推演计划":"keep", "评估压缩方案":"assess"},
    "industry_work_profile": {"按行业核对":"sector_review", "仅通用办公核对":"general_office"},
    "space_use": {"待确认":"unknown", "纯办公室":"office_only", "纯办公":"office_only", "办公含专项空间":"office_with_specialist"},
    "delivery_profile": {"精简": "lean", "精简交付": "lean", "快速": "lean",
                         "完整": "full", "完整交付": "full"},
    "sector": {"办公": "office", "普通办公": "office", "general_office": "office",
               "游戏": "gaming", "金融": "finance", "financial": "finance",
               "半导体": "semiconductor", "生物医药": "biopharma"},
    "delivery": {"db": "DB", "d&b": "DB", "dbb": "DBB", "gc": "GC", "epc": "EPC", "cm": "CM", "split": "SPLIT", "设计施工一体化": "DB"},
    "bidding": {"邀请": "invite", "邀约": "invite", "邀请招标": "invite", "公开": "public", "公开招标": "public"},
    "site_condition": {"毛坯": "shell", "毛坯交付": "shell", "cat_a": "shell", "标准cat-a": "shell",
                       "全拆": "full_strip", "fullstrip": "full_strip", "既有cat-b改造": "cat_b_existing",
                       "存量精装需拆": "cat_b_existing", "待定": "unknown", "待确认": "unknown",
                       "partial": "renovation_partial", "局部改造": "renovation_partial"},
    "calendar_profile": {"5d": "5D", "6d": "6D", "7d": "7D", "5天": "5D", "6天": "6D", "7天": "7D"},
    "authority_profile": {"standard": "STANDARD", "unknown": "UNKNOWN"},
    "external_power_status": {"充足": "sufficient", "无需增容": "sufficient", "需外线增容": "upgrade_needed", "待定": "needs_confirmation", "待确认": "needs_confirmation"},
}
BOOLEANS = {"fasttrack", "has_server_room", "prelease_tdd", "it_migration", "fm_transition", "laboratory",
           "statutory_tender_required", "hazardous_chemical_project"}
from core.equipment_leads import FIELDS as EQUIPMENT_LEAD_FIELDS
NUMBERS = {"area_sqm", "cost_10k_rmb", "demolition_fraction", "workfronts",
           "power_kva", "mocap_camera_count", "mocap_stage_area_sqm",
           "fume_hood_count", *EQUIPMENT_LEAD_FIELDS}
SUPPORTED = set(REQUIRED) | set(CHOICES) | BOOLEANS | NUMBERS | {"project_name", "start_date", "target_date",
    "cleanroom_class", "nc_rating", "esd_level"}
DEFAULTS = {
    'construction_handover': 'unconfirmed',
    "target_response": "ask",
    "project_name": "室内装修排程项目", "site_condition": "full_strip", "calendar_profile": "7D",
    "authority_profile": "STANDARD", "mode": "plan", "zoning_mode": "off", "sectional": "auto",
    "building_restriction": "none", "fasttrack": False, "delivery_profile": "full",
    "statutory_tender_required": False,
}
DEFAULT_BASIS = {
    "site_condition": "默认全部拆除后重新装修；客户说明毛坯或局部改造时按客户说明。",
    "calendar_profile": "默认每周7天施工；客户要求5天或6天时按客户要求。",
    "authority_profile": "保守预留审批链；不代表每项法律义务均已核实。",
    "statutory_tender_required": "私营/外资自有资金办公装修通常非必须招标（必须同时满足范围+规模：16号令2018-03-27；843号文2018-06-06；770号文2020-10-23）。未声明依法必须招标时不锁20日发标窗口。",
}

CHOICES.update({'addon_'+i.lower(): set(STATES) for i in CATALOG})
CHOICES['procurement_regime'] = {'unknown', 'formal_tender', 'enterprise_selection'}
CHOICES['procurement_policy'] = {'unspecified','enterprise_3round_tech_commercial'}
VALUE_ALIASES['procurement_policy'] = {'企业三轮报价及技术商务评审':'enterprise_3round_tech_commercial','待确认':'unspecified'}
from core.local_authority import FIELDS as AUTHORITY_FIELDS
CHOICES.update(AUTHORITY_FIELDS)
CHOICES['authority_size_class']={'unknown','medium_or_below','large'}
CHOICES['authority_local_route']={'unknown','sip_standard_direct'}
SUPPORTED.update({'province','district','authority_size_class','authority_local_route'})
VALUE_ALIASES['authority_size_class']={'中小型':'medium_or_below','大型':'large','待确认':'unknown'}
VALUE_ALIASES['authority_local_route']={'园区常规专业装修直接发包核准路径':'sip_standard_direct','待确认':'unknown'}
CHOICES['furniture_supply_state']={'new_order','ordered','delivered','reuse'}
CHOICES['furniture_lead_profile']={'STANDARD_LOCAL','CUSTOM_LOCAL','IMPORTED','SYSTEM_FURNITURE','LOOSE_FURNITURE','CUSTOM_JOINERY'}
BOOLEANS.update({'authority_draft','furniture_in_scope','furniture_procurement','furniture_tracking','permit_waived'})
DEFAULTS.update(furniture_in_scope=True, furniture_procurement=False, furniture_tracking=False)
CHOICES['fire_release_policy']={'pending','filing','inspection'}
CHOICES['power_supply_type']={'unknown','lv','hv_single','hv_dual'}
CHOICES['eia_document_type']={'unknown','report_book','report_form','registration'}
VALUE_ALIASES['power_supply_type']={'未知':'unknown','低压':'lv','高压单电源':'hv_single','高压双电源':'hv_dual'}
VALUE_ALIASES['eia_document_type']={'未知':'unknown','报告书':'report_book','报告表':'report_form','登记表':'registration'}
VALUE_ALIASES['statutory_tender_required']={'依法必须招标':'true','非必须招标':'false','必须招标':'true','不是必须招标':'false'}
ALIASES['依法必须招标']='statutory_tender_required'
DEFAULTS['power_supply_type']='unknown'
DEFAULTS['eia_document_type']='unknown'
DEFAULT_BASIS['power_supply_type']='〔沪〕供电报装时限随电压等级而变；未提供时按高压单电源22工作日占位并标不完整。'
DEFAULT_BASIS['eia_document_type']='〔法〕环评报告书60日、报告表30日（《环境影响评价法》第22条）；未提供文件类型时暂按报告表并标不完整。'
DEFAULTS['fire_release_policy']='pending'
DEFAULT_BASIS.update(furniture_in_scope='暂保留办公家具安装；明确排除时移除。', furniture_procurement='默认不展开独立家具招采。', furniture_tracking='默认不展开供货跟踪；供货为外部待确认条件。', fire_release_policy='入驻放行政策待项目确认，非法律结论。')
from core.authority_reference import DAY_FIELDS as AUTHORITY_DAY_FIELDS, BUFFER_FIELDS as _AUTHORITY_BUFFERS
AUTHORITY_BUFFER_FIELDS = tuple(_AUTHORITY_BUFFERS.values())
NUMBERS.update({'specialist_area_sqm','furniture_remaining_days','fire_inspection_days','fire_rectification_days','fire_recheck_days','flushout_days',*AUTHORITY_DAY_FIELDS,*AUTHORITY_BUFFER_FIELDS})
NUMBERS.update(FURNITURE_NUMBERS)
SUPPORTED.update(FURNITURE_NUMBERS | FURNITURE_TEXT | {"segmented_review","segmented_permit"})
SUPPORTED.update({'authority_basis','authority_rule_date','furniture_supply_basis','fire_duration_basis','fire_release_basis','occupancy_basis','authority_days_basis','authority_buffer_reason'} | BOOLEANS | NUMBERS)
VALUE_ALIASES.update({
 'building_safety_assessment':{'需要鉴定':'required','已核定无需鉴定':'not_required','待确认':'unknown'},
 'building_context':{'既有非居住建筑':'existing_nonresidential','其他建筑':'other','待确认':'unknown'},
 'renovation_category':{'简易装修':'simple','局部结构或外立面变动':'structural_or_facade','消防设计变动':'fire_changes','用途或主要结构等其他改造':'out_of_scope','待确认':'unknown'},
 'authority_special_policy':{'无其他专项政策（已核对）':'none','商务楼宇简易装修政策':'business_simple','其他专项政策':'other','待确认':'unknown'},
 'fire_classification':{'特殊建设工程':'special','其他建设工程备案':'other','本次无消防审验事项（已核定）':'not_applicable','待确认':'unknown'},
 'fire_sampling':{'尚待抽查结果':'pending','已通知抽查':'selected','已确认未抽中':'not_selected'},
 'fire_release_policy':{'待确认':'pending','按备案条件放行（需依据）':'filing','等待抽查结果（内部保守政策）':'inspection'},
 'furniture_supply_state':{'新购未下单':'new_order','已下单':'ordered','已到货':'delivered','既有家具复用':'reuse'},
})
NUMBERS.update({'server_room_area_sqm','acoustic_room_area_sqm'})
SUPPORTED.update(NUMBERS | {'server_room_early_order_basis'})
SUPPORTED.update(CHOICES)

@dataclass
class InputResult:
    project: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    blockers: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def status(self):
        return "NEEDS_CLARIFICATION" if self.blockers else "READY"

    def payload(self):
        from core.local_authority import resolve_local_authority
        return {"schema_version": SCHEMA_VERSION, "status": self.status,
                "project": self.project, "field_evidence": self.evidence,
                "blockers": self.blockers, "warnings": self.warnings,
                "module_plan": resolve_modules(self.project),
                "authority_plan": resolve_local_authority(self.project),
                "assumptions": [{"field": k, "value": self.project[k], **v}
                                for k, v in self.evidence.items() if v["origin"] == "default_policy"]}


def _clean(key: str, value: Any):
    if key in ('segmented_review','segmented_permit'):
        if str(value).lower() not in ('false','0','no','否'):
            raise ValueError('需项目所在地主管部门确认及批准的分段范围；当前保持一次图审、一次发证')
        return False
    if key.startswith("addon_"):
        return normalize_state(value)
    if isinstance(value, str):
        value = value.strip()
    if key in EQUIPMENT_LEAD_FIELDS:
        from core.equipment_leads import DEFAULT_LABEL
        if value in (None, "", DEFAULT_LABEL):
            return None
    if key in NUMBERS:
        if isinstance(value, bool):
            raise ValueError("must be a number, not a boolean")
        n = float(value)
        if key == "mocap_camera_count" and n <= 0: raise ValueError("camera count must be positive")
        if n < 0: raise ValueError("must not be negative")
        if key in {"mocap_camera_count", "fume_hood_count", *EQUIPMENT_LEAD_FIELDS} and not n.is_integer(): raise ValueError("must be a whole number")
        if not math.isfinite(n):
            raise ValueError("must be finite")
        if key in {"area_sqm", "workfronts", "specialist_area_sqm", "server_room_area_sqm", "acoustic_room_area_sqm", "mocap_stage_area_sqm"} | FURNITURE_NUMBERS and n <= 0:
            raise ValueError("must be positive")
        if key in {"area_sqm", "workfronts", "furniture_install_crews"} and not n.is_integer():
            raise ValueError("must be a whole number; no silent rounding")
        if key in {"cost_10k_rmb", "furniture_remaining_days", "fire_inspection_days", "fire_rectification_days", "fire_recheck_days", "flushout_days", *AUTHORITY_DAY_FIELDS, *AUTHORITY_BUFFER_FIELDS} and n < 0:
            raise ValueError("must not be negative")
        if key == "demolition_fraction" and not 0 <= n <= 1:
            raise ValueError("must be between 0 and 1")
        return int(n) if key in {"area_sqm", "workfronts", "mocap_camera_count", "fume_hood_count", *EQUIPMENT_LEAD_FIELDS} else n
    if key in BOOLEANS:
        if isinstance(value, bool):
            return value
        token = str(value).lower()
        if token in {"true", "yes", "1", "是", "依法必须招标", "必须招标"}:
            return True
        if token in {"false", "no", "0", "否", "非必须招标", "不是必须招标"}:
            return False
        raise ValueError("must be explicit yes/no; unknown is not false")
    if key in {"start_date", "target_date", "authority_rule_date"}:
        if isinstance(value, (dt.date, dt.datetime)):
            return value.strftime("%Y-%m-%d")
        return dt.date.fromisoformat(str(value)).isoformat()
    if key in CHOICES:
        token = str(value)
        token = VALUE_ALIASES.get(key, {}).get(token.lower(), token)
        if token not in CHOICES[key]:
            raise ValueError("choose " + ", ".join(sorted(CHOICES[key])))
        return token
    if not isinstance(value, str) or not value:
        raise ValueError("must be non-empty text")
    return value


def normalize_project_inputs(sources: list[tuple[str, dict]]) -> InputResult:
    """Merge supplied facts, reject disagreement, then disclose defaults.

    A source can wrap fields in `project` and provide field_evidence. Re-loading
    a normalized document preserves default origin instead of laundering it
    into a client assertion. Explicit contradictory sources require clarification.
    """
    result = InputResult()
    for source_name, payload in sources:
        if not isinstance(payload, dict):
            result.blockers.append({"field": "input", "reason": "input must be an object", "source": source_name})
            continue
        raw = payload.get("project", payload)
        if not isinstance(raw, dict):
            result.blockers.append({"field": "project", "reason": "project must be an object"})
            continue
        supplied_evidence = payload.get("field_evidence", {})
        for raw_key, value in raw.items():
            key = ALIASES.get(raw_key, raw_key)
            if value is None or value == "":
                continue
            if key not in SUPPORTED:
                result.warnings.append({"field": raw_key, "source": source_name,
                                        "reason": "not scheduled; retain in source, resolve scope separately"})
                continue
            try:
                value = _clean(key, value)
            except (ValueError, TypeError, OverflowError) as exc:
                result.blockers.append({"field": key, "source": source_name, "reason": str(exc)})
                continue
            ev = supplied_evidence.get(key, {})
            defaulted = ev.get("origin") == "default_policy"
            evidence = {"origin": "default_policy" if defaulted else "provided",
                        "source": source_name, "quote": ev.get("quote", f"{raw_key}={value}"),
                        "verification": "unverified_statement" if not defaulted else "not_a_site_fact"}
            if defaulted:
                evidence["basis"] = ev.get("basis", DEFAULT_BASIS.get(key, "planning default"))
            if key in result.project:
                previous = result.evidence[key]
                if previous["origin"] == "default_policy" and not defaulted:
                    result.project[key], result.evidence[key] = value, evidence
                elif previous["origin"] != "default_policy" and defaulted:
                    continue
                elif result.project[key] != value:
                    result.blockers.append({"field": key, "reason": "conflicting supplied values",
                                            "values": [result.project[key], value],
                                            "sources": [previous["source"], source_name]})
                else:
                    previous.setdefault("corroborating_sources", []).append(source_name)
            else:
                result.project[key], result.evidence[key] = value, evidence
    # A recognized enum is not proof that a corresponding validated engine exists.
    if result.project.get("delivery") in {"GC", "EPC", "CM", "SPLIT"}:
        result.blockers.append({"field": "delivery", "reason": "recognized contracting strategy; production topology not implemented or verified"})
    if result.project.get("sector") in {"datacenter", "commercial"}:
        result.blockers.append({"field": "sector", "reason": "recognized sector; production scope model not implemented or verified"})
    for key in REQUIRED:
        if key not in result.project:
            result.blockers.append({"field": key, "reason": "required; reuse existing material or ask once"})
    if not (result.project.get("start_date") or result.project.get("target_date")):
        result.blockers.append({"field": "start_date_or_target_date", "reason": "provide project kickoff or move-in date; do not substitute site entry"})
    for key, value in DEFAULTS.items():
        if key not in result.project:
            result.project[key] = value
            result.evidence[key] = {"origin": "default_policy", "source": "project_input.DEFAULTS",
                                    "basis": DEFAULT_BASIS.get(key, "planning interface default"),
                                    "verification": "not_a_site_fact"}
    p = result.project
    if not str(p.get('project_name') or '').strip() or p.get('project_name') == '未命名项目' or result.evidence['project_name']['origin']=='default_policy':
        p['project_name']='室内装修排程项目'
        result.warnings.append({'field':'project_name','reason':'项目名称未提供或为占位名，采用通用名称；正式汇报前请回填。'})
    if p['target_response'] == 'assess':
        if (p['mode'] != 'scenario' and result.evidence['mode']['origin'] != 'default_policy') or (p['fasttrack'] is False and result.evidence['fasttrack']['origin'] != 'default_policy'):
            result.blockers.append({'field':'target_response','reason':'压缩评估与明确指定的模式冲突，请统一选择'})
        else:
            p['mode'] = 'scenario'
    # A ventilation wait is never a default; it enters the plan only with project days and a basis.
    if p.get('flushout_days') is not None and not p.get('occupancy_basis'):
        result.blockers.append({'field':'occupancy_basis','reason':'已填写通风散味天数，请同时说明依据（如业主书面要求、当地规定），否则不加入计划。'})
    if p.get('occupancy_basis') and p.get('flushout_days') is None:
        result.blockers.append({'field':'flushout_days','reason':'已填写通风散味依据，请同时填写天数（工作日），否则不加入计划。'})
    if p.get('flushout_days') is not None and not float(p['flushout_days']).is_integer():
        result.blockers.append({'field':'flushout_days','reason':'通风散味天数须为整数天。'})
    given_days = [f for f in AUTHORITY_DAY_FIELDS if p.get(f) is not None]
    if given_days and not p.get('authority_days_basis'):
        result.blockers.append({'field':'authority_days_basis','reason':'已填写报审天数，请同时说明依据（如当地住建窗口答复、近期同类项目），否则不采用。'})
    if p.get('authority_days_basis') and not given_days:
        result.blockers.append({'field':'authority_review_days','reason':'已填写报审天数依据，请同时填写图审、许可或消防天数（工作日）。'})
    given_buffers = [f for f in AUTHORITY_BUFFER_FIELDS if p.get(f)]
    if given_buffers and not p.get('authority_buffer_reason'):
        result.blockers.append({'field':'authority_buffer_reason','reason':'已设报审缓冲天数，请写明原因（如当地补正常见、业主内部审批），否则不加入计划。'})
    if p.get('authority_buffer_reason') and not any(p.get(f) is not None for f in AUTHORITY_BUFFER_FIELDS):
        result.blockers.append({'field':'authority_permit_buffer_days','reason':'已写缓冲原因，请同时填写图审、许可或消防整改的缓冲天数（工作日）。'})
    for f in given_days + given_buffers:
        if not float(p[f]).is_integer():
            result.blockers.append({'field':f,'reason':'报审天数须为整数工作日。'})
    if p["site_condition"] == "renovation_partial" and "demolition_fraction" not in p:
        result.blockers.append({"field": "demolition_fraction", "reason": "partial renovation needs an explicit demolition proportion"})
    if p["zoning_mode"] == "manual":
        result.blockers.append({"field": "zoning_mode", "reason": "manual geometry needs detailed zone input; use auto or off in this L2 entry"})
    if p['mode'] == 'scenario' and p['fasttrack'] is False and result.evidence['fasttrack']['origin'] != 'default_policy':
        result.blockers.append({'field': 'fasttrack', 'reason': 'scenario mode conflicts with explicit fasttrack=False; confirm the intended mode'})
    elif p["mode"] == "scenario" or p["fasttrack"]:
        p["fasttrack"] = True
        if result.evidence['fasttrack']['origin'] == 'default_policy':
            result.evidence['fasttrack'] = {'origin': 'mode_policy', 'source': 'mode=scenario',
                                           'basis': 'explicit scenario request', 'verification': 'planning_control'}
        if not p.get("start_date"):
            result.blockers.append({"field": "start_date", "reason": "fast-track comparison currently needs a supplied kickoff; backward-only mode is not implemented"})
    if p.get("start_date") and p.get("target_date") and p["start_date"] > p["target_date"]:
        result.blockers.append({"field": "target_date", "reason": "move-in target precedes kickoff"})
    result.blockers.extend(furniture_install_issues(p))
    if p.get('furniture_in_scope',True) and not any(p.get(k) is not None for k in FURNITURE_NUMBERS | FURNITURE_TEXT):
        result.warnings.append({'field':'furniture_installation','reason':'家具数量、类型与班组及运输能力未提供；安装工期沿用面积代理暂估，待供应商核定，不视为承诺。'})
    from core.procurement_policy import policy
    procurement=policy(p)
    result.blockers.extend(procurement['blockers'])
    plan = resolve_modules(p)
    result.blockers.extend(plan['blockers'])
    if procurement['negotiation_allowed']:
        result.warnings.append({'field':'procurement_policy','reason':'采用已选企业三轮报价流程：允许谈判、技术与商务分析后定标；应用于设计服务及DB/GC发包。评分权重、每轮时长与最终授权人仍待项目确认。'})
    if plan['selected']:
        result.warnings.append({'field':'module_durations','reason':'专项为规划工序定义；既有时长及新增未校准占位需项目确认，不能作为工期承诺。'})
        result.warnings.append({'field':'module_interfaces','reason':'本版按单设施共享系统接口组装；分区独立放行、外部批准及供应商承诺需项目级核实。'})
    for question in plan.get('scope_questions', []):
        result.warnings.append({'field':'scope_question','reason':question})
    if plan['recommended_pending']:
        result.warnings.append({'field':'addons','reason':'行业推荐专项未确认，不自动加载: '+', '.join(plan['recommended_pending'])})
    if p.get('space_use','unknown')=='unknown':
        result.warnings.append({'field':'space_use','reason':'用途待确认；未选专项不等于已确认纯办公室。保留行业基础核查，不自动增加实体专项。'})
    if p.get('space_use')=='office_only' and 'A12' in plan['selected']:
        result.blockers.append({'field':'space_use','reason':'明确纯办公室与需要实验室设施冲突；请核对实际用途或实验室选项。'})
    for i in plan['external']:
        result.warnings.append({'field':'addon_'+i.lower(),'reason':'复用／他方提供仅为陈述；计划保留待核验外部接口，不表示条件已满足。'})
    if procurement['effective_regime'] == 'unknown':
        result.warnings.append({'field':'procurement_regime','reason':'采购制度待确认；不自动启用议价／BAFO，适用法定窗口仍须核实。'})
    if p.get('fm_transition') is False:
        result.warnings.append({'field':'fm_transition','reason':'排除扩展FM服务；基本接管与搬迁终点仍保留。'})
    from core.release_controls import input_issues
    if result.evidence.get('furniture_tracking',{}).get('origin')=='default_policy' and any(p.get(k) is not None for k in ('furniture_supply_state','furniture_remaining_days','furniture_lead_profile','furniture_supply_basis')):
        result.blockers.append({'field':'furniture_tracking','reason':'已提供家具供货信息，请确认是否开启跟踪；不凭状态自动加载。'})
    issues, notices = input_issues(p)
    result.blockers.extend(issues)
    result.warnings.extend(notices)
    from core.local_authority import resolve_local_authority
    authority_plan=resolve_local_authority(p)
    if authority_plan['status']!='READY':
        from core.local_authority import draft_declined
        refused = draft_declined(p) and result.evidence.get('authority_draft',{}).get('origin')=='provided'
        questions = [dict(field=q['field'], question=q['question'],
                          options=[label for label in VALUE_ALIASES.get(q['field'],{}) if label!='待确认'])
                     for q in authority_plan['questions']]
        if authority_plan['status']=='CONFLICT' or refused:
            # Contradictory facts, or the user declined a draft: ask before planning.
            result.blockers.extend({'field':q['field'],'reason':q['question'],
                                    **({'choices':q['options']} if q['options'] else {})} for q in questions)
        else:
            outside_shanghai = authority_plan['status'] == 'NEEDS_LOCAL_RULE' and authority_plan.get('jurisdiction',{}).get('adapter')=='unverified'
            if not p.get('authority_draft'):
                p['authority_draft'] = True
                if outside_shanghai:
                    # 未匹配属地规则时仅出诊断草案，不冒称当地正式审批路径。
                    result.evidence['authority_draft'] = {'origin':'default_policy','source':'national_general_limit',
                        'basis':'属地报审规则未逐城核实；按已披露参考时限出诊断草案，不能视为当地适用审批结论；补充当地规则后重新判断路径与工期。',
                        'verification':'national_general_limit'}
                else:
                    # 上海分类信息缺失：仍按诊断草案出计划并醒目标注，列出待补问题。
                    result.evidence['authority_draft'] = {'origin':'default_policy','source':'missing_authority_facts',
                        'basis':'报审分类信息未提供或当地规则未核实，先按诊断草案出计划并醒目标注；补充后重新运行可得正式报审路径。',
                        'verification':'not_a_site_fact'}
            from core.authority_reference import reference_days
            try:
                reference = reference_days(p)
            except Exception:  # the reference table is advisory; never block a draft on it
                reference = {}
            if outside_shanghai:
                # 未接入地区披露规则缺口及待核事项。
                result.warnings.append({'field':'authority_draft',
                    'reason':'报审路径为诊断草案：未逐城核实属地规则，按已披露的参考路径与规划参数推演；'
                              '规划预留工期非法定时限，补充当地规则后重新运行即按当地口径重算。',
                    'reference_days':reference})
            else:
                result.warnings.append({'field':'authority_draft',
                    'reason':'报审路径未核实（诊断草案）：报审分类信息还差下面几项；在此之前图审、许可、消防工序按已披露的参考路径与规划参数出草案，'
                              '计划可用于沟通，但不作为报审或开工放行依据。补充后重新判断适用路径，符合当前支持条件时按对应规则重算。',
                    'questions':questions, 'reference_days':reference})
        if p.get('permit_waived') is not True:
            try:
                from experts.permit_expert import query_city_permit_rule
                table = query_city_permit_rule(str(p.get('city', '')), area_sqm=p.get('area_sqm'), cost_10k_rmb=p.get('cost_10k_rmb'))
            except Exception:
                table = {}
            if table.get('is_exempt'):
                result.warnings.append({'field':'permit_waived','reason':'按城市参考表，本项目可能低于施工许可免办限额（'+str(table.get('exempt_desc',''))+'，未核实）。计划默认仍排施工许可；如确认不办理，请说明，计划将改为开工信息报送或备案。'})
        if p.get('permit_waived') is True:
            result.warnings.append({'field':'permit_waived','reason':'已按用户说明不办理施工许可，计划不排许可工序；请向当地住建部门办理开工信息报送或备案。'})
    elif any(p.get(f) is not None for f in AUTHORITY_DAY_FIELDS):
        result.warnings.append({'field':'authority_review_days','reason':'本城市已按核实的报审规则排程，自填的报审天数未采用。'})
    return result


def load_input(path: str | Path) -> dict:
    path = Path(path)
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if path.suffix.lower() == ".xlsx":
        from tools.parse_intake_sheet import parse_intake_xlsx
        data = parse_intake_xlsx(path)
        return {'project': {k: v[0] for k, v in data.items()},
                'field_evidence': {ALIASES.get(k, k): {'quote': f'{k}={v[0]}; {v[1]}'} for k, v in data.items()}}
    raise ValueError("intake must be .json or .xlsx; prompts are extracted to project JSON by the agent")


def engine_arguments(project: dict) -> list[str]:
    """Compile the sole normalized contract to the existing L2 engine surface."""
    mapping = {"area_sqm": "area", "calendar_profile": "calendar_profile", "cost_10k_rmb": "cost"}
    fields = ("city", "area_sqm", "sector", "delivery", "bidding", "start_date", "target_date",
              "project_name", "site_condition", "calendar_profile", "authority_profile",
              "zoning_mode", "sectional", "workfronts", "building_restriction", "demolition_fraction", "construction_handover",
              "cleanroom_class", "nc_rating", "esd_level", "power_kva", "mocap_camera_count", "mocap_stage_area_sqm",
              "statutory_tender_required", "power_supply_type", "eia_document_type", "fume_hood_count",
              "space_use", *sorted(EQUIPMENT_LEAD_FIELDS),
              *["addon_" + i.lower() for i in CATALOG])
    out = []
    for key in fields:
        if project.get(key) is not None:
            value = project[key].lower() if key == "calendar_profile" else project[key]
            if isinstance(value, bool):
                out.extend(["--" + key, "true" if value else "false"])
                continue
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            out.extend(["--" + mapping.get(key, key), str(value)])
    if project.get("flushout_days") is not None and project.get("occupancy_basis"):
        out.extend(["--flushout_days", str(int(project["flushout_days"])), "--occupancy_basis", project["occupancy_basis"]])
    if project.get("fasttrack"):
        out.append("--fasttrack")
    if project.get("mode") == "review":
        out.append("--senior_pm_review")
    return out
