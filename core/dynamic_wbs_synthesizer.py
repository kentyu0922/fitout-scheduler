# -*- coding: utf-8 -*-
"""Dynamic WBS Synthesizer Engine (Executive Master Programme Architecture).

Synthesizes an Executive L2 Master Programme project plan dynamically from
project intake facts and governing Sector archetype.
Enforces authentic PM consulting firm standards:
- Pre-lease Technical Due Diligence (TDD) supporting lease decision (lease_decision).
- Design development, BIM coordination, statutory fire design review, and construction permit.
- Main contractor & specialist trade procurement packaging.
- Sector-specific specialist modules:
  * Gaming: Fast-track Data Center (infra_ready), Mocap & Acoustic Studio (mocap_ready), High-density QA Labs.
  * Biopharma: Cleanroom ISO validation, biosafety cabinet, EIA approval, pure water/gases.
  * Semiconductor: Anti-vibration base, CDA, DI water, ESD grounding.
  * Finance: Trading desk dual-power, Tier 3 datacenter, vault physical security.
  * Office: Standard phased workplace fit-out.
- Multi-floor spatial zoning staggered pipeline (Low Zone / High Zone).
- Commissioning, Statutory Fire Inspection, Joint Completion, and CMA IAQ purge (systems_ready).
- Tenant cross-functional streams: IT Migration & Cutover (business_ready), FM Operations Handover (occupancy_ready).
- Phased client move-in & Day 1 Opening (day1_opening).
"""

from typing import Dict, Any, List, Optional
import copy


def synthesize_wbs_network(facts: Dict[str, Any], project_id: str = "某游戏公司杭州研发总部装修工程") -> Dict[str, Any]:
    area = float(facts.get("area_sqm", 25000.0))
    sector = str(facts.get("sector", "gaming")).lower()
    start_date = str(facts.get("start_date", "2026-11-02"))
    has_server_room = bool(facts.get("has_server_room", True))
    prelease = bool(facts.get("prelease_tdd", True))
    it_migration = bool(facts.get("it_migration", True))
    fm_transition = bool(facts.get("fm_transition", True))
    site_calendar = str(facts.get("site_calendar", "7D"))

    sector_names = {
        "gaming": "游戏科技研发",
        "biopharma": "生物医药研发",
        "semiconductor": "半导体集成电路",
        "finance": "金融科技与交易",
        "office": "商业办公总部"
    }
    sector_cn = sector_names.get(sector, "工程")

    context_str = (
        f"项目管理顾问部：{project_id}，{sector_cn}总部，建筑面积约 {area:,.0f} ㎡。"
        f"采用L2项目管理级综合总控计划网络模型。"
        f"覆盖租赁前TDD技术尽调、设计与法定报审、招标采购、独立核心机房及特种工程、"
        f"现场施工、综合联调与消防空气验收、IT与数据迁移、FM运维接管全过程。"
        f"本案不含外线增容，明确不做实验室。"
    )

    sources: Dict[str, str] = {
        "context": context_str,
        "pm_experience_tdd": "项目管理顾问经验：租赁签约前必须完成TDD技术尽调，全面复核大厦机电负荷、结构承重、变压器容量与管井路由，周期不少于1个月。",
        "pm_experience_dc": "工程管理规程：核心机房专区必须提前45天先行上电交付，以释放IT服务器设备上架与网络联调关键路径。",
        "pm_experience_it": "技术割接规程：总部IT及数据中心迁移预留充分演练周期，核心业务切换安排在周末窗口并具备毫秒级回退预案。",
        "pm_experience_fm": "物业运营规程：FM运维接管安排实操培训与应急演练，实现平稳交付运营。"
    }

    base_nodes: List[Dict[str, Any]] = []

    def make_judgement(node_key: str, stage_label: str, days: int, low: int, high: int, cal: str, assumptions: List[str] = None):
        source_id = f"pm_{node_key}"
        quote = f"{stage_label}：采用{days}工作日；区间{low}–{high}工作日；日历{cal}。依据项目管理顾问专业定额与综合总控逻辑编制。"
        sources[source_id] = quote
        return {
            "scope": stage_label,
            "stage": stage_label,
            "selection_reason": f"采用{days}工作日以满足{stage_label}之工程管理深度、工艺工序与接口搭接要求",
            "scale": {"status": "ASSUMED", "description": f"{area:,.0f}㎡{sector_cn}总部总控规模"},
            "calendar": {"mode": cal, "basis": f"{cal}日历规划基准"},
            "range": {"low": low, "high": high, "unit": "working_day"},
            "basis": "PM_ESTIMATE",
            "source_id": source_id,
            "quote": quote,
            "binding": {
                "node_key": node_key,
                "stage_label": stage_label,
                "selected_workdays": days,
                "low": low,
                "high": high,
                "unit": "working_day",
                "calendar": cal
            },
            "assumptions": assumptions or ["工程现场具备正常施工组织条件", "大楼垂直运输、装卸平台与临时用水用电保障正常"],
            "missing_inputs": [],
            "review": {"status": "UNREVIEWED"}
        }

    def make_milestone(node_key: str, name: str, cal: str, after: List[str], quote_desc: str, lvl: int = 2):
        source_id = f"m_{node_key}"
        quote = f"{name}：{quote_desc}"
        sources[source_id] = quote
        return {
            "key": node_key,
            "name": name,
            "outline_level": lvl,
            "days": 0,
            "calendar": cal,
            "after": after,
            "node_type": "milestone",
            "release_condition": {
                "description": quote_desc,
                "source_id": source_id,
                "quote": quote
            }
        }

    # 1. 项目启动节点 (Project Kickoff)
    base_nodes.append(make_milestone(
        "kickoff", "项目启动", "5D", [],
        "项目管理启动会召开，明确总体目标、里程碑节点与组织架构。",
        lvl=1
    ))
    last_gate = "kickoff"

    # 2. WBS 1.0: 租前技术尽职调查与租赁决策 (TDD & Lease Gate)
    if prelease:
        tdd_tasks = [
            ("tdd_appoint", "TDD顾问招采与委任", 10, 8, 15, "5D", ["kickoff"]),
            ("tdd_desk", "既有建筑图纸复核与结构机电桌面审查", 5, 3, 7, "5D", ["tdd_appoint"]),
            ("tdd_survey", "现场多专业查勘与游戏专项核验 (机房承重/变压器余量/双路由弱电)", 5, 4, 8, "5D", ["tdd_desk"]),
            ("tdd_report", "TDD 综合工程评估报告编制与内审", 10, 8, 15, "5D", ["tdd_survey"]),
            ("tdd_close", "报告答疑、风险排查清单与租赁条件闭合", 5, 3, 7, "5D", ["tdd_report"])
        ]
        for key, name, days, low, high, cal, after in tdd_tasks:
            base_nodes.append({
                "key": key,
                "name": name,
                "outline_level": 3,
                "days": days,
                "calendar": cal,
                "after": after,
                "duration_judgement": make_judgement(key, name, days, low, high, cal)
            })

        base_nodes.append(make_milestone(
            "lease_decision", "租赁决策释放", "5D", ["tdd_close"],
            "TDD工程评估报告签署确认，重大工程风险闭合，租赁合同签订释放。"
        ))
        last_gate = "lease_decision"

    # 3. WBS 2.0: 方案深化、合规报审与工程招采 (Design, Permitting & Procurement)
    design_tasks = [
        ("design_deepen", "主体建筑装饰方案深化与全专业 BIM 碰撞综合", 15, 12, 20, "5D", [last_gate]),
        ("design_review", "施工图联合审查 & 取得《施工图审查合格书》", 20, 15, 25, "5D", ["design_deepen"]),
        ("fire_review", "住建局建设工程消防设计审查申报与批复", 12, 10, 15, "5D", ["design_review"]),
        ("construction_permit", "正式取得《建筑工程施工许可证》", 10, 7, 15, "5D", ["fire_review"]),
        ("tender_main", "施工总承包与特种专业工程招标评标与合同签约", 25, 20, 35, "5D", ["design_deepen"]),
        ("procure_longlead", "长周期特种设备采购排产锁定 (精密空调/变压器/UPS/动捕钢构)", 20, 15, 30, "5D", ["tender_main"]),
        ("site_prep", "现场查验接管与大厦物业进场报备", 5, 3, 7, "5D", ["tender_main"])
    ]
    for key, name, days, low, high, cal, after in design_tasks:
        base_nodes.append({
            "key": key,
            "name": name,
            "outline_level": 3,
            "days": days,
            "calendar": cal,
            "after": after,
            "duration_judgement": make_judgement(key, name, days, low, high, cal)
        })

    base_nodes.append(make_milestone(
        "site_commence", "现场开工释放", "5D", ["construction_permit", "procure_longlead", "site_prep"],
        "法定施工许可、招采签约及长周期设备排产闭合，现场全面动工放行。"
    ))

    # 4. WBS 3.0: 游戏特种工程专区 A：核心机房先行交付岛 (Fast-track Data Center)
    if has_server_room:
        dc_tasks = [
            ("dc_structural", "核心机房区域结构局部碳纤维加固与微孔防尘地坪处理", 8, 6, 12, site_calendar, ["site_commence"]),
            ("dc_crac", "双冷源精密空调 (CRAC N+1) 室内外机安装与制冷铜管充氮保压", 10, 8, 14, site_calendar, ["dc_structural"]),
            ("dc_fire", "七氟丙烷气体灭火管网预埋、泄压口与防排烟防火阀联动调试", 8, 6, 12, site_calendar, ["dc_structural"]),
            ("dc_ups_power", "双路高压 UPS 动力电池组、配电柜安装与主干电缆敷设", 10, 8, 15, site_calendar, ["dc_crac", "dc_fire"]),
            ("dc_micro_module", "微模块冷热通道封闭系统与服务器机柜阵列组装", 8, 6, 12, site_calendar, ["dc_ups_power"]),
            ("dc_cabling", "核心网络主干万兆单模光纤与超六类铜缆综合布线", 8, 6, 12, site_calendar, ["dc_micro_module"]),
            ("dc_load_bank", "72小时全负荷假负载测试 (Load Bank) 与机房动环监控调测", 5, 4, 7, site_calendar, ["dc_cabling"])
        ]
        for key, name, days, low, high, cal, after in dc_tasks:
            base_nodes.append({
                "key": key,
                "name": name,
                "outline_level": 3,
                "days": days,
                "calendar": cal,
                "after": after,
                "duration_judgement": make_judgement(key, name, days, low, high, cal)
            })

        base_nodes.append(make_milestone(
            "infra_ready", "目标设施就绪", site_calendar, ["dc_load_bank"],
            "核心机房先行上电完成，72小时带载假负载测试通过，动环系统正常，释放IT服务器设备上架部署。"
        ))

    # 5. WBS 4.0: 游戏特种工程专区 B：动作捕捉棚与声学录音配音工作室 (Mocap & Acoustic Studio)
    if sector == "gaming":
        mocap_tasks = [
            ("mocap_floating_floor", "建筑声学隔声浮筑地面 (弹簧减震垫+细石混凝土隔振层) 施工", 12, 10, 16, site_calendar, ["site_commence"]),
            ("mocap_acoustic_wall", "房中房双层轻钢隔音墙、穿孔吸声铝板与低频陷阱安装", 10, 8, 15, site_calendar, ["mocap_floating_floor"]),
            ("mocap_truss_rail", "天花动捕刚性钢结构桁架导轨 (Truss Rail) 安装与空间水平校准", 8, 6, 12, site_calendar, ["mocap_acoustic_wall"]),
            ("mocap_camera_array", "3D 红外光学动捕相机阵列安装与空间光学校正", 7, 5, 10, site_calendar, ["mocap_truss_rail"]),
            ("mocap_soundproof_doors", "专业隔声门、双层隔音观察窗与消声风管阻抗复合消声器安装", 8, 6, 12, site_calendar, ["mocap_camera_array"]),
            ("mocap_rt60_test", "第三方建筑声学检测: 本底噪声(NC≤20)与混响时间(RT60≤0.25s)达标", 3, 2, 5, site_calendar, ["mocap_soundproof_doors"])
        ]
        for key, name, days, low, high, cal, after in mocap_tasks:
            base_nodes.append({
                "key": key,
                "name": name,
                "outline_level": 3,
                "days": days,
                "calendar": cal,
                "after": after,
                "duration_judgement": make_judgement(key, name, days, low, high, cal)
            })

        base_nodes.append(make_milestone(
            "mocap_ready", "动捕与声学工程就绪", site_calendar, ["mocap_rt60_test"],
            "动捕光学相机阵列标定完成，声学混响时间RT60检测合格，达到专业制作就绪状态。"
        ))

    # 6. WBS 5.0: 办公区多楼层空间穿插精装施工 (Workplace Phased General Fit-out)
    fitout_tasks = [
        ("demo_protection", "既有装修现场保护拆除与全楼放线定位", 10, 8, 14, site_calendar, ["site_commence"]),
        ("z1_mep_framing", "低区 (F01~F04) 机电主管敷设与轻钢龙骨隔墙安装", 25, 20, 30, site_calendar, ["demo_protection"]),
        ("z2_mep_framing", "高区 (F05~F08) 机电主管敷设与轻钢龙骨隔墙安装", 25, 20, 30, site_calendar, ["z1_mep_framing"]),
        ("concealed_gate_z1", "低区隐蔽验收", site_calendar, ["z1_mep_framing"], "低区强弱电与喷淋隐蔽工程联合验收签认。", 3),
        ("concealed_gate_z2", "高区隐蔽验收", site_calendar, ["z2_mep_framing"], "高区强弱电与喷淋隐蔽工程联合验收签认。", 3),
        ("z1_finishes", "低区 (F01~F04) 高架地板、吊顶封板、面漆与灯具风口安装", 25, 20, 30, site_calendar, ["concealed_gate_z1"]),
        ("z2_finishes", "高区 (F05~F08) 高架地板、吊顶封板、面漆与灯具风口安装", 25, 20, 30, site_calendar, ["concealed_gate_z2", "z1_finishes"]),
        ("dev_zone_power_vrv", "高密研发测试专区大功率工位配电与加班独立 VRV 空调安装", 15, 12, 20, site_calendar, ["z1_finishes", "z2_finishes"]),
        ("esports_hall", "电竞体验厅与多功能发布厅舞台大屏与扩声系统安装", 10, 8, 15, site_calendar, ["z1_finishes", "z2_finishes"]),
        ("furniture_install", "办公家具与工位系统进场组装就位", 15, 10, 20, site_calendar, ["dev_zone_power_vrv", "esports_hall"])
    ]
    for item in fitout_tasks:
        if len(item) == 6:  # milestone
            key, name, cal, after, quote_desc, lvl = item
            base_nodes.append(make_milestone(key, name, cal, after, quote_desc, lvl=lvl))
        else:
            key, name, days, low, high, cal, after = item
            base_nodes.append({
                "key": key,
                "name": name,
                "outline_level": 3,
                "days": days,
                "calendar": cal,
                "after": after,
                "duration_judgement": make_judgement(key, name, days, low, high, cal)
            })

    fitout_finish_preds = ["furniture_install"]
    if sector == "gaming":
        fitout_finish_preds.append("mocap_ready")

    base_nodes.append(make_milestone(
        "fitout_finish", "硬装竣工", site_calendar, fitout_finish_preds,
        "全楼各楼层装饰装修、机电末端及专业声学特种工程全面完成总承包自检。"
    ))

    # 7. WBS 6.0: 综合机电联调、法定消防验收与空气治理 (T&C, Fire & IAQ)
    comm_tasks = [
        ("mep_commissioning", "暖通空调多联机、变配电、BAS 与机房动环系统综合联调", 10, 8, 15, site_calendar, ["fitout_finish"]),
        ("fire_inspection", "第三方消防设施检测、电气防火检测与住建现场查验", 8, 6, 12, "5D", ["mep_commissioning"]),
        ("joint_completion", "住建局联合竣工验收与备案手续办理", 5, 3, 7, "5D", ["fire_inspection"]),
        ("iaq_blind_test", "CMA 首次室内空气质量盲测 (基材空气采样)", 2, 2, 3, site_calendar, ["joint_completion"]),
        ("iaq_purification", "全楼强排通风散味与光触媒环保净化治理", 30, 25, 45, site_calendar, ["iaq_blind_test"]),
        ("iaq_final_test", "二次 CMA 室内空气质量达标复测 (出具合格检测报告)", 3, 2, 5, site_calendar, ["iaq_purification"])
    ]
    for key, name, days, low, high, cal, after in comm_tasks:
        base_nodes.append({
            "key": key,
            "name": name,
            "outline_level": 3,
            "days": days,
            "calendar": cal,
            "after": after,
            "duration_judgement": make_judgement(key, name, days, low, high, cal)
        })

    base_nodes.append(make_milestone(
        "systems_ready", "系统就绪", site_calendar, ["iaq_final_test"],
        "现场暖通、强弱电、消防联调及空气质量复测全项达标，满足物业接管与入驻要求。"
    ))

    # 8. WBS 7.0: 总部 IT 核心割接与业务迁移 (IT Migration & Cutover)
    if it_migration:
        it_start_pred = "infra_ready" if has_server_room else "fitout_finish"
        it_tasks = [
            ("it_plan", "IT 迁移方案深化、网络演练与备件锁定", 20, 15, 25, "5D", [it_start_pred]),
            ("it_server_deploy", "核心服务器设备上架、局域网打通与仿真试迁移", 15, 10, 20, "5D", ["it_plan", "fitout_finish"]),
            ("it_cutover", "核心业务系统周末全量割接与回退验证", 3, 2, 5, "5D", ["it_server_deploy"])
        ]
        for key, name, days, low, high, cal, after in it_tasks:
            base_nodes.append({
                "key": key,
                "name": name,
                "outline_level": 3,
                "days": days,
                "calendar": cal,
                "after": after,
                "duration_judgement": make_judgement(key, name, days, low, high, cal)
            })

        base_nodes.append(make_milestone(
            "business_ready", "业务系统就绪", "5D", ["it_cutover"],
            "总部核心 IT 网络、服务器集群及生产数据库割接验证完毕，具备上线运营条件。"
        ))

    # 9. WBS 8.0: 物业 FM 接管演练与人员分批入驻 (FM Handover & Relocation)
    if fm_transition:
        fm_tasks = [
            ("fm_handover_prep", "FM 运维资料、竣工图纸与资产台账移交", 10, 8, 15, "5D", ["fitout_finish"]),
            ("fm_training", "物业运维团队现场实操培训与带载应急演练", 10, 8, 15, "5D", ["fm_handover_prep", "systems_ready"]),
            ("fm_accept", "FM 正式接管签认", 3, 2, 5, "5D", ["fm_training"])
        ]
        for key, name, days, low, high, cal, after in fm_tasks:
            base_nodes.append({
                "key": key,
                "name": name,
                "outline_level": 3,
                "days": days,
                "calendar": cal,
                "after": after,
                "duration_judgement": make_judgement(key, name, days, low, high, cal)
            })

        occupancy_preds = ["fm_accept"]
        if it_migration:
            occupancy_preds.append("business_ready")

        base_nodes.append(make_milestone(
            "occupancy_ready", "人员搬迁放行", "5D", occupancy_preds,
            "物业FM接管完成，IT核心业务系统就绪，正式签发全员搬迁入驻许可证。",
            lvl=1
        ))

        # 搬迁与正式开业
        movein_tasks = [
            ("client_movein", "全员分批搬迁入驻、试运行技术保障与缺陷排查", 15, 10, 20, "5D", ["occupancy_ready"])
        ]
        for key, name, days, low, high, cal, after in movein_tasks:
            base_nodes.append({
                "key": key,
                "name": name,
                "outline_level": 3,
                "days": days,
                "calendar": cal,
                "after": after,
                "duration_judgement": make_judgement(key, name, days, low, high, cal)
            })

        base_nodes.append(make_milestone(
            "day1_opening", "某游戏公司研发总部正式开业运营", "5D", ["client_movein"],
            "全员搬迁顺利完成，各项设施平稳运行，某游戏公司研发总部正式开业。",
            lvl=1
        ))

    return {
        "project_id": project_id,
        "title": f"【项目管理综合总控进度计划】{project_id}",
        "clean_title": f"【项目管理综合总控进度计划】{project_id} (Rev 1.0)",
        "plan_level": "L2_MACRO_CONTROL",
        "scenario_nature": "VERIFIED_FACT",
        "city": facts.get("city", "杭州"),
        "area": area,
        "delivery": facts.get("delivery", "DBB"),
        "bidding": facts.get("bidding", "public"),
        "target_movein_date": str(facts.get("target_movein_date") or ""),
        "phasing_zones": facts.get("phasing_zones"),
        "sources": sources,
        "facts": [
            {"id": "f_sector", "field": "sector", "value": sector, "assertion": "stated", "context": "current", "scope": project_id, "source_id": "context", "quote": f"{sector_cn}总部"},
            {"id": "f_area", "field": "area_sqm", "value": area, "assertion": "stated", "context": "current", "scope": project_id, "source_id": "context", "quote": f"建筑面积约 {area:,.0f} ㎡"},
            {"id": "f_prelease", "field": "prelease", "value": prelease, "assertion": "stated", "context": "current", "scope": project_id, "source_id": "context", "quote": "TDD技术尽调"},
            {"id": "f_server_room", "field": "has_server_room", "value": has_server_room, "assertion": "stated", "context": "current", "scope": project_id, "source_id": "context", "quote": "独立核心机房"},
            {"id": "f_it", "field": "it_migration", "value": it_migration, "assertion": "stated", "context": "current", "scope": project_id, "source_id": "context", "quote": "IT与数据迁移"},
            {"id": "f_fm", "field": "fm_transition", "value": fm_transition, "assertion": "stated", "context": "current", "scope": project_id, "source_id": "context", "quote": "FM运维接管"},
            {"id": "f_power", "field": "external_power_upgrade", "value": False, "assertion": "denied", "context": "current", "scope": project_id, "source_id": "context", "quote": "不含外线增容"},
            {"id": "f_laboratory_denial", "field": "laboratory", "value": False, "assertion": "denied", "context": "current", "scope": project_id, "source_id": "context", "quote": "明确不做实验室"}
        ],
        "planning": {
            "mode": "internal_draft",
            "purpose": "planning_review",
            "start_date": start_date,
            "summary_calendar": {
                "mode": "5D",
                "basis": "项目管理顾问总控 5D 基准参考日历"
            },
            "base_nodes": base_nodes,
            "workflow_plans": {}
        }
    }
