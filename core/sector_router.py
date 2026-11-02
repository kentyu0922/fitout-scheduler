"""Top-Level Industry Sector Router & Orchestration Engine (Level 0 Architecture).

In PM Engineering Architecture, Sector is the governing dimension (L0):
- Sector determines allowed space types and specialized engineering modules.
- Sector activates domain-specific WBS branches (e.g. Data Center island, Lab validation, Trading floor).
- Sector attaches historical experience registries and duration floors.
- Sector enforces scope denial boundaries (e.g. gaming explicitly denies laboratory/cleanroom).
"""

import copy
import json
from pathlib import Path
from typing import Dict, Any, List, Optional


CATALOG_PATH = Path(__file__).resolve().parents[1] / "profiles" / "sectors.json"


SECTOR_ARCHETYPES: Dict[str, Dict[str, Any]] = {
    "gaming": {
        "sector_id": "gaming",
        "label": "游戏科技研发 (Gaming & Tech R&D)",
        "governing_level": "L0_SECTOR_DIRECTIVE",
        "description": "适用于游戏研发公司、数字娱乐及高算力科技总部。核心特征包括高密度服务器机房、双回路高供电力、声学录音动捕棚、高密度研发工位及极短业务割接窗口。",
        "allowed_spaces": ["office", "server_room", "recording", "motion_capture"],
        "core_workflows": ["tdd", "it_migration", "fm_transition"],
        "mandatory_wbs_modules": [
            "fast_track_datacenter",       # 4.0 核心机房先行交付岛
            "dual_network_cutover",        # 7.0 总部IT双轨割接与回退
            "acoustic_soundproofing"       # 声学降噪工程
        ],
        "default_exclusions": ["laboratory", "cleanroom", "production"],
        "experience_register": "experience/gaming_master_programme.json",
        "critical_power_density_kva_per_sqm": 0.12,
        "recommended_ac_system": "大厦冷水末端 + 核心机房精密空调+N+1备用",
        "key_risk_disclosure": "核心机房必须在全楼硬装完工前30~45天提前正式供电，以释放IT服务器部署；外电需提前核实大厦变压器余量。"
    },
    "biopharma": {
        "sector_id": "biopharma",
        "label": "生物医药研发 (Biopharma & Life Sciences)",
        "governing_level": "L0_SECTOR_DIRECTIVE",
        "description": "适用于生物医药、基因技术、药物研发中心。核心特征包括实验区与办公区分区、恒温恒湿洁净室、特气管网、废水分流及严苛的环保与环评审批。",
        "allowed_spaces": ["office", "laboratory", "cleanroom"],
        "core_workflows": ["tdd", "fm_transition"],
        "mandatory_wbs_modules": [
            "lab_hvac",                    # 实验室补风与废气处理系统
            "cleanroom_iso",               # 洁净室ISO环境认证
            "eia_approval"                 # 环保环评验收放行
        ],
        "default_exclusions": ["production", "recording"],
        "experience_register": None,
        "critical_power_density_kva_per_sqm": 0.18,
        "recommended_ac_system": "全新风变风量VAV系统 + 独立直膨式净化空调",
        "key_risk_disclosure": "环评批复与消防图审为法定前置阻断点；实验家具设备到货与公用工程接口必须提前锁定。"
    },
    "semiconductor": {
        "sector_id": "semiconductor",
        "label": "半导体与集成电路 (Semiconductor & Chip R&D)",
        "governing_level": "L0_SECTOR_DIRECTIVE",
        "description": "适用于集成电路设计、半导体封装测试及微电子研发。核心特征包括微振动控制、超纯水系统、高压压缩空气及防静电洁净室。",
        "allowed_spaces": ["office", "cleanroom", "laboratory"],
        "core_workflows": ["tdd", "fm_transition"],
        "mandatory_wbs_modules": [
            "anti_vibration",              # 微振动控制与大型设备基座
            "cda_piping",                  # 高纯特气与高压压缩空气
            "esd_grounding"                # 整体微电子防静电接地网
        ],
        "default_exclusions": ["recording"],
        "experience_register": None,
        "critical_power_density_kva_per_sqm": 0.20,
        "recommended_ac_system": "循环机组FFU + 恒温恒湿精密空调",
        "key_risk_disclosure": "超纯水与特气管路试压钝化周期不可压缩；楼板荷载与微振动指标需在租前TDD中重点复核。"
    },
    "finance": {
        "sector_id": "finance",
        "label": "金融科技与银行交易 (Financial & Trading Floor)",
        "governing_level": "L0_SECTOR_DIRECTIVE",
        "description": "适用于证券公司、基金总部、银行交易大厅。核心特征包括极高可用性弱电网络、双重UPS不间断供电、交易大厅多屏工位及高等级物理安防。",
        "allowed_spaces": ["office", "server_room"],
        "core_workflows": ["tdd", "it_migration", "fm_transition"],
        "mandatory_wbs_modules": [
            "trading_desk",                # 交易工位双回路配电与网络
            "datacenter",                  # Tier 3 高可用机房
            "vault_security"               # 金融级物理安防与门禁
        ],
        "default_exclusions": ["laboratory", "cleanroom", "production"],
        "experience_register": None,
        "critical_power_density_kva_per_sqm": 0.15,
        "recommended_ac_system": "双冷源互备中央空调 + 24小时交易机房恒温空调",
        "key_risk_disclosure": "网络主干双路由由不同物理管井进线；周末全业务割接必须具备完整秒级回退预案。"
    },
    "office": {
        "sector_id": "office",
        "label": "通用商业办公 (General Commercial Office)",
        "governing_level": "L0_SECTOR_DIRECTIVE",
        "description": "标准甲级或乙级写字楼室内装修。以常规开放办公区、会议室、多功能厅为主，机电依托大厦既有公用系统。",
        "allowed_spaces": ["office"],
        "core_workflows": ["tdd", "fm_transition"],
        "mandatory_wbs_modules": [
            "standard_fitout_pipeline",    # 标准分区装饰装修流水
            "office_ff_move"              # 办公家具安装 + 标准 IT/AV 安装 + 搬迁管理
        ],
        "default_exclusions": ["laboratory", "cleanroom", "production"],
        "experience_register": None,
        "critical_power_density_kva_per_sqm": 0.08,
        "recommended_ac_system": "依托大厦既有中央空调末端风机盘管(FCU)或VAV",
        "key_risk_disclosure": "重点控制大厦垂直运输梯位预约及夜间低噪音施工窗口。"
    }
}


class SectorRequiredError(ValueError):
    """CLI/intake must name a supported sector. Do not guess office or gaming."""


# Packs this release actually weaves. Archetype mandatory_wbs_modules may list
# more; unimplemented modules are disclosed, never substituted with the gaming kit.
IMPLEMENTED_PACKS: Dict[str, List[str]] = {
    "office": ["tdd", "longlead", "fm_handover", "office_ff_move"],
    "gaming": ["tdd", "longlead", "datacenter", "mocap", "dev_zone", "it_cutover", "fm_handover"],
    "finance": ["tdd", "longlead", "datacenter", "trading_desk", "vault_security", "it_cutover", "fm_handover"],
    "biopharma": ["tdd", "longlead", "lab_hvac", "cleanroom_iso", "eia_approval", "fm_handover"],
    "semiconductor": ["tdd", "longlead", "anti_vibration", "cda_piping", "esd_grounding", "fm_handover"],
}

UNIMPLEMENTED_MODULES: Dict[str, List[str]] = {
    "office": [],
    "gaming": [],
    "finance": [],
    "biopharma": [],
    "semiconductor": [],
}

SCOPE_STATUS: Dict[str, str] = {
    "office": "implemented",
    "gaming": "implemented",
    "finance": "implemented",
    "biopharma": "implemented",
    "semiconductor": "implemented",
}


def resolve_sector_id(sector: Optional[str]) -> str:
    if sector is None or str(sector).strip() in ("", "none", "None"):
        raise SectorRequiredError(
            "SECTOR_REQUIRED: pass --sector office|gaming|finance|biopharma|semiconductor"
        )
    return get_sector_archetype(sector)["sector_id"]


def sector_pack_plan(sector: str, facts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Industry recommends; only declared scope selects physical modules."""
    from core.module_catalog import resolve_modules
    sid = resolve_sector_id(sector)
    plan = resolve_modules(dict(facts or {}, sector=sid))
    if plan['blockers']:
        raise ValueError('MODULE_SCOPE_BLOCKED: '+str(plan['blockers']))
    return dict(sector_id=sid, packs=['office_ff_move','fm_handover']+plan['packs'],
                scope_status='declared_scope_draft', unimplemented_modules=[],
                inject_commercial_percent_milestones=False,
                disclosure='专项按明确需求加载；基本接管与项目终点保留。',
                module_plan=plan)


def get_sector_archetype(sector: str) -> Dict[str, Any]:
    """Retrieve the top-level sector governing archetype."""
    normalized = str(sector).strip().lower()
    # Normalize synonyms
    if normalized in ("gaming", "游戏", "游戏科技", "game"):
        normalized = "gaming"
    elif normalized in ("biopharma", "生物医药", "医药", "生物", "bio", "life_science", "lifescience", "生命科学"):
        normalized = "biopharma"
    elif normalized in ("semiconductor", "半导体", "集成电路", "芯片", "tmt", "科技互联网"):
        normalized = "semiconductor"
    elif normalized in ("finance", "金融", "金融科技", "证券", "银行"):
        normalized = "finance"
    elif normalized in ("office", "办公", "普通办公", "commercial", "通用办公", "传统办公"):
        normalized = "office"
    
    if normalized not in SECTOR_ARCHETYPES:
        raise ValueError(
            f"Unsupported sector '{sector}'. Supported sector archetypes: "
            f"{', '.join(SECTOR_ARCHETYPES.keys())}"
        )
    return copy.deepcopy(SECTOR_ARCHETYPES[normalized])


def apply_sector_governance(raw_facts: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich and constrain raw project intake facts with Sector-level governance directives."""
    sector_id = resolve_sector_id(raw_facts.get("sector"))
    archetype = get_sector_archetype(sector_id)
    
    enriched = copy.deepcopy(raw_facts)
    enriched["sector"] = archetype["sector_id"]
    enriched["sector_label"] = archetype["label"]
    enriched["governing_level"] = archetype["governing_level"]

    plan = sector_pack_plan(sector_id, enriched)
    # 6. 附加行业元数据
    enriched["sector_metadata"] = {
        "key_risk_disclosure": archetype["key_risk_disclosure"],
        "recommended_ac_system": archetype["recommended_ac_system"],
        "critical_power_density_kva_per_sqm": archetype["critical_power_density_kva_per_sqm"],
        "mandatory_wbs_modules": [],
        "candidate_wbs_modules": archetype["mandatory_wbs_modules"],
        "implemented_packs": plan["packs"],
        "scope_status": plan["scope_status"],
        "unimplemented_modules": plan["unimplemented_modules"],
        "scope_disclosure": plan["disclosure"],
    }
    enriched["sector_pack_plan"] = plan

    return enriched
