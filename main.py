# -*- coding: utf-8 -*-
import json
import os
import sys
import argparse
import datetime
import logging
import copy
import time

# 设置日志系统
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("main")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.solver_engine import solve_schedule_with_target, solve_schedule
from exporters import build_mpp, export_pdf, generate_pptx_milestones
from experts.permit_expert import query_city_permit_rule, calc_tender_duration
from core import holidays as _holidays
from core import compliance as _compliance
# 化繁为简：业务逻辑已提取到独立模块
from core.calibration import calibrate_durations
from core.task_utils import clean_procurement_terminology, renumber_tasks_contiguously, fold_exempt_construction_permit
from core.productivity import productivity_summary
from core.fitout_engine import prepare_fitout
from core.fitout_wbs import expand_fitout_wbs
from core.dependency_engine import validate_dependency_graph


def dump_json(value, stream, **kwargs):
    """Write complete audit JSON in one C-accelerated encode/write; no fields omitted."""
    kwargs.pop("indent", None)
    stream.write(json.dumps(value, separators=(",", ":"), **kwargs))


def _parse_floor_breakdown(spec):
    """'F01:3000,F02:3000' → [{'floor_id': 'F01', 'area_sqm': 3000.0}, ...]  (None when empty)."""
    if not spec:
        return None
    out = []
    for item in str(spec).split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise SystemExit(f"--floor_breakdown 格式错误: '{item}'，应为 floor_id:面积，如 F01:3000,F02:3000")
        fid, val = item.split(":", 1)
        fid = fid.strip()
        if not fid:
            raise SystemExit(f"--floor_breakdown 缺少楼层编号: '{item}'")
        try:
            area = float(val)
        except ValueError:
            raise SystemExit(f"--floor_breakdown 面积必须为数值: '{item}'")
        if area <= 0:
            raise SystemExit(f"--floor_breakdown 面积必须 > 0: '{item}'")
        out.append({"floor_id": fid, "area_sqm": area})
    return out or None


def _manual_zones_for(floors, zones_per_floor):
    """Explicit per-floor zone counts → manual zone specs with apportioned areas.

    The count is an explicit planning instruction; the area split is exact (apportion),
    never an invented number.
    """
    from core.zone_model import apportion
    n = int(zones_per_floor)
    if n < 1:
        raise SystemExit("--zones must be >= 1")
    specs = []
    for f in floors:
        fid, farea = f["floor_id"], float(f["area_sqm"])
        parts = apportion(farea, [1.0] * n)
        for i, part in enumerate(parts):
            specs.append({"floor_id": fid, "zone_id": f"Z{i + 1}", "area_sqm": part})
    return specs


def _parse_quantities(spec: str) -> dict:
    """'partition_framing=900,flooring=1300' → {'partition_framing': 900.0, 'flooring': 1300.0}"""
    out = {}
    for item in (spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise SystemExit(f"--quantities 格式错误: '{item}'，应为 activity_type=数值，如 partition_framing=900")
        k, v = item.split("=", 1)
        try:
            out[k.strip()] = float(v)
        except ValueError:
            raise SystemExit(f"--quantities 工程量必须为数值: '{item}'")
    return out

def _run_diff_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog='main.py diff')
    parser.add_argument('--base', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--output', default=None)
    args = parser.parse_args(argv)
    from core.intake_diff import compare_intake_snapshots, format_ascii_table, markdown_card
    from core.project_input import load_input
    from core.milestones import snapshot
    t0 = time.perf_counter()
    base_raw = load_input(args.base)
    target_raw = load_input(args.target)
    if not isinstance(base_raw, dict):
        base_raw = dict(base_raw)
    if not isinstance(target_raw, dict):
        target_raw = dict(target_raw)

    def _solve(path: str) -> dict:
        import tempfile
        out = tempfile.mkdtemp(prefix='pmp_diff_')
        mpp = os.path.join(out, 'programme.mpp')
        cmd = [sys.executable, '-X', 'utf8', os.path.join(BASE_DIR, 'main.py'),
               '--project_input', os.path.abspath(path), '--output', mpp,
               '--no_mpp', '--mpp_only']
        # Intakes are raw facts; engine resolves via --project_input.
        import subprocess
        r = subprocess.run(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding='utf-8', errors='replace')
        if r.returncode:
            raise SystemExit('DIFF_SOLVE_FAILED: ' + (r.stderr or r.stdout)[-4000:])
        sched = os.path.splitext(mpp)[0] + '_schedule.json'
        with open(sched, encoding='utf-8') as f:
            payload = json.load(f)
        return snapshot(payload.get('tasks') or [])

    base_ms = target_ms = None
    try:
        base_ms = _solve(args.base)
        target_ms = _solve(args.target)
    except SystemExit:
        raise
    except Exception as exc:
        logger.warning('diff solved-milestones skipped: %s', exc)
    delta = compare_intake_snapshots(base_raw, target_raw, base_ms, target_ms)
    delta['benchmark'] = {'intake_diff_seconds': round(time.perf_counter() - t0, 3)}
    text = format_ascii_table(delta)
    print(text, file=sys.stderr)
    print(json.dumps({'diff': delta, 'markdown': markdown_card(delta), 'ascii': text,
                      'benchmark': delta['benchmark']}, ensure_ascii=False, indent=2))
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            dump_json(delta, f, ensure_ascii=False, indent=2)


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'diff':
        _run_diff_cli(argv[1:])
        return
    if any(a == '--engine_project' or a.startswith('--engine_project=') for a in argv):
        from tools.run_workflow_engine import main as workflow_main
        workflow_main(argv)
        return
    if any(a == '--decision_input' or a.startswith('--decision_input=') for a in argv):
        from core.project_decisions import run_cli
        run_cli(argv)
        return
    if any(a == '--sector_profile' or a.startswith('--sector_profile=') for a in argv):
        from core.sector_profile import run_cli
        run_cli(argv)
        return
    if any(a == "--internal_review_plan" or a.startswith("--internal_review_plan=") for a in argv):
        import importlib.util
        tool = os.path.join(BASE_DIR, "tools", "run_project_internal_review.py")
        spec = importlib.util.spec_from_file_location("run_project_internal_review", tool)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        overlay = argparse.ArgumentParser(add_help=False)
        overlay.add_argument("--internal_review_plan", required=True)
        overlay.add_argument("--internal_review_output", default=None)
        overlay.add_argument("--mpp", action="store_true")
        ov, _rest = overlay.parse_known_args()
        argv = ["--plan", ov.internal_review_plan, "--prepare"]
        if ov.internal_review_output:
            argv.extend(["--output", ov.internal_review_output])
        argv.extend(["--engine", BASE_DIR])
        if ov.mpp:
            argv.append("--mpp")
        raise SystemExit(mod.main(argv))
    parser = argparse.ArgumentParser(description="PMP Lead PM Scheduler engine. Agent command is tools/run_contract.py, or this file with the SKILL.md flags only. --cost, --floors, --floor_breakdown, --freight_lifts, --zones and --rate_level require --engine_regression and are not for the agent.")
    parser.add_argument("--city", type=str, required=False, default=None, help="项目所在城市，如 '上海', '杭州', '苏州'")
    parser.add_argument("--area", type=float, required=False, default=None, help="项目面积(平方米)")
    parser.add_argument("--sector", type=str, default=None,
                        help="必填行业: office / gaming / finance / biopharma / semiconductor。缺省停机，不猜测。")
    parser.add_argument("--project_input", type=str, default=None, help="Normalized shared intake JSON from tools/run_contract.py")
    parser.add_argument("--intake_excel", type=str, default=None,
                        help="项目准入登记表 (.xlsx) 路径，自动提取行业、面积、特种工程、里程碑与工期约束")
    parser.add_argument("--cost", type=float, default=None,
                        help="[P2-14/15] 项目预估造价(万元)，支持小数如 157.5。缺省=None(UNKNOWN)——"
                             "不默认100万；cost 相关法定判定按 UNKNOWN/仅面积规则处理")
    parser.add_argument('--site_condition',choices=['unknown','shell','cat_a','cat_b_existing','renovation_partial','full_strip','partial'],default='full_strip',help='交付状态：缺省=full_strip 全拆（PM 总控默认要拆，要改由 PM 显式覆盖）/shell|cat_a=毛坯不拆/cat_b_existing=存量精装需拆/renovation_partial|partial=局部改造(需--demolition_fraction)/unknown=未确认不画拆除')
    parser.add_argument('--demolition_fraction',type=float,help='局部改造拆除比例0..1，需显式输入')
    parser.add_argument('--crew_plan',help='Explicit JSON trade headcounts; proposed staffing is not proof of workforce availability')
    # ③ 招采形态 (DB=D&B / DBB) 与 ④ 招标方式 (invite=邀请 / public=公开) 为正交两维
    parser.add_argument("--delivery", type=str, choices=["DB", "DBB"], help="招采形态: DB(D&B 设计施工一体化) / DBB(设计-招标-施工)")
    parser.add_argument("--bidding", type=str, choices=["invite", "public"], help="招标方式: invite(邀请招标) / public(公开招标)")
    parser.add_argument("--mode", type=str, help="(兼容旧版) 直接指定模板键, 如 Standard_Fitout_DB_Invite")
    parser.add_argument("--target_date", type=str, help="硬性要求的项目交付搬迁日 (YYYY-MM-DD)。用户原意保持不变，绝不隐藏提前")
    parser.add_argument("--allow_diagnostic_baseline", action="store_true", default=False,
                        help="倒排不可行时仍写出诊断产物；缺省：不可行则非零退出。")
    parser.add_argument("--management_buffer_days", type=int, default=0,
                        help="[P0-01] 显式管理缓冲(天)：planning_target = user_target - buffer。默认 0 = 不隐藏缓冲；>0 时输出显式显示 User Target / Management Buffer / Planning Target")
    parser.add_argument("--start_date", type=str, help="项目启动 Kick-off 日期 (YYYY-MM-DD)")
    parser.add_argument("--project_name", type=str, default="未命名项目", help="项目名称（由intake/CLI提供，不内置业主品牌）")
    parser.add_argument("--cleanroom_class", type=str, default=None)
    parser.add_argument("--nc_rating", type=str, default=None)
    parser.add_argument("--esd_level", type=str, default=None)
    parser.add_argument("--power_kva", type=float, default=None)
    parser.add_argument("--mocap_camera_count", type=float, default=None)
    parser.add_argument("--statutory_tender_required", default=None)
    parser.add_argument("--power_supply_type", default=None)
    parser.add_argument("--eia_document_type", default=None)
    parser.add_argument("--fume_hood_count", type=float, default=None)
    parser.add_argument("--space_use", default=None)
    from core.module_catalog import CATALOG as _ADDON_CATALOG
    for _aid in _ADDON_CATALOG:
        parser.add_argument("--addon_" + _aid.lower(), default=None)
    from core.equipment_leads import FIELDS as _LEAD_FIELDS, DEFAULT_LABEL as _LEAD_DEFAULT

    def _lead_cli(v, _default=_LEAD_DEFAULT):
        text = str(v).strip()
        if text in {"", _default, "none", "None"}:
            return None
        return float(v)

    for _lf in sorted(_LEAD_FIELDS):
        parser.add_argument("--" + _lf, type=_lead_cli, default=None)
    parser.add_argument("--mocap_stage_area_sqm", type=float, default=None)
    parser.add_argument("--addons", type=str, default="", help="附加的 Sub-WBS 模块名 (如 Datacenter_LoadBank_Module)")
    parser.add_argument("--no_mpp", action="store_true", default=False, help="跳过物理 MS Project COM 渲染，仅生成 CPM 解算数据与 PDF/PPTX 报表")
    parser.add_argument('--mpp_only', action='store_true', help='Lean delivery: native MPP and essential machine audits only')
    parser.add_argument("--senior_pm_review", action="store_true", default=False,
                        help="[V6.5.0] 在冻结的 V6.4.4.1 计算之后运行 Senior PM 智能层：基准挑战/工期驱动诊断/"
                             "分区流水机会/优化登记册 + 客户版任务名净化。不修改任何计算工期。")
    parser.add_argument("--output", type=str, default="Output_Schedule.mpp", help="输出 MPP 文件名称")
    parser.add_argument("--quantities", type=str, default="",
                        help="实测工程量，如 partition_framing=900,flooring=1300,painting=2700。未给出的类型由新工程量引擎推导，标记 provisional")
    parser.add_argument("--rate_level", type=str, default=None, choices=["low", "typical", "high"],
                        help="选用生产率库中的哪一档 (low=悲观/最长工期, typical=默认, high=乐观)。数值只来自 config，不可自定义")
    parser.add_argument("--grade", type=str, default=None,
                        help="[工程量引擎] 楼宇等级 A|B|C (亦接受 '甲级'/'Grade A')。缺省 A")
    parser.add_argument("--layout", type=str, default=None, choices=["open_plan", "standard", "cellular"],
                        help="[工程量引擎] 平面形态，影响隔墙密度。缺省 standard")
    parser.add_argument('--fitout_grade', choices=['basic', 'standard', 'premium'], default='standard')
    parser.add_argument('--shifts', type=float, default=1.0)
    parser.add_argument('--workfront_efficiency', type=float, default=1.0)
    parser.add_argument('--complexity', choices=['standard', 'complex'], default='standard')
    parser.add_argument('--access', choices=['open', 'restricted'], default='open')
    parser.add_argument('--productivity_evidence', default=None, help='Reviewed L1/L2/L3 evidence JSON; defaults to config/productivity_evidence.json')
    parser.add_argument('--calibration_project', default=None, help='Restrict calibration to observations from this project ID')
    parser.add_argument("--sectional", type=str, choices=["auto", "on", "off"], default="auto",
                        help="[Fit-out依赖引擎] 分区穿插 SS+lag 规则: auto=按 config/dependency_rules.json 面积/工作面阈值判定(默认); on=强制启用; off=采用严格 FS 工序顺序")
    parser.add_argument("--workfronts", type=int, default=None,
                        help="[Fit-out依赖引擎] 显式分区/工作面数量。缺省按 --area ÷ 单工作面面积 推导；同时决定 SS 滞后 = ceil(前置工期 ÷ 工作面数)")
    # ---- V6.6.0 generalized spatial & multi-floor orchestration ----
    parser.add_argument("--floors", type=int, default=1,
                        help="已废弃：总控进度表与层数无关，参数忽略")
    parser.add_argument("--floor_breakdown", type=str, default=None,
                        help='显式楼层面积分解，格式 "F01:3000,F02:3000"（合计须等于 --area）。优先于 --floors；仍不对客出分区条')
    parser.add_argument('--construction_handover',default='unconfirmed',choices=['unconfirmed','whole','sectional'],help='整体移交或分区移交；未确认不代表已批准分区流水')
    parser.add_argument("--zoning_mode", type=str, default="off", choices=["auto", "manual", "off"],
                        help="空间分区：PM 总控默认 off（不对客出区域工序）。仅诊断/L3 才用 manual 或 auto")
    parser.add_argument("--freight_lifts", type=int, default=None,
                        help="已废弃：总控进度表不模拟货梯。保留参数仅为兼容旧 CLI，不参与工期")
    parser.add_argument("--zones", type=int, default=None,
                        help="每层分区数。总控默认忽略；仅 --zoning_mode manual 的 L3 诊断使用")
    parser.add_argument("--fasttrack", action="store_true", default=False,
                        help="快轨情景开关(GOV-18 合法搭接/长周期提前/并行招标)：克隆重解，基线不动，"
                             "产出独立 fasttrack_schedule.json（CALCULATED，永不自动套用）")
    parser.add_argument("--calendar_profile", type=str, default=None, choices=["5d", "6d", "7d"],
                        help="[P1-12] 施工日历档位：5d/6d/7d。缺省沿用 7D（激进），但每次运行都会显式披露并推荐 6D")
    parser.add_argument("--building_restriction", type=str, default="none", choices=["none", "no_weekend_work"],
                        help="[P1-12] 大楼限制覆写：no_weekend_work 强制施工回落 5D（周末禁止施工）")
    parser.add_argument("--scope_profile", type=str, default=None,
                        choices=["shell_to_catb", "cat_a_to_catb", "existing_refurb", "light_refresh",
                                 "full_renovation", "custom"],
                        help="[P1-11] 范围档位。缺省不选 = 完整工种清单按 ASSUMED_SCOPE 排入并显式披露")
    parser.add_argument("--procurement_profile", type=str, default="auto",
                        choices=["auto", "STANDARD", "FAST_TRACK_SMALL", "COMPLEX"],
                        help="[P1-06] 招采档位。auto=按面积+招标方式判定（≤1000㎡邀请招标→FAST_TRACK_SMALL，"
                             "公开招标恒为 STANDARD——法定等标/公示窗口不可压缩）")
    parser.add_argument("--segmented_review", default="false", help="分段图审选项，默认关闭；需项目所在地主管部门确认")
    parser.add_argument("--segmented_permit", default="false", help="分段许可选项，默认关闭；需项目所在地主管部门确认")
    parser.add_argument("--design_release", type=str, default="auto", choices=["auto", "on", "off"],
                        help="[P1-07] D&B 设计分批释放（物业送审基于SD包提前启动等）。auto=DB模式启用/DBB保持串行")
    parser.add_argument("--furniture_lead_profile", type=str, default=None,
                        help="[P1-10] 家具交期档位。缺省=SYSTEM_FURNITURE（产品固定 35d 系统家具交期）。")
    parser.add_argument("--authority_profile", type=str, default="STANDARD",
                        choices=["STANDARD", "UNKNOWN"],
                        help="[P0-04] 消防图审/验收备案/第三方检测/联合竣工。缺省=STANDARD 全项目承诺；"
                             "仅 UNKNOWN 为诊断占位（甘特不画）。施工许可仍按城市面积/造价独立判定。")
    parser.add_argument("--occupancy_profile", type=str, default=None,
                        choices=["STANDARD", "CLIENT_STRICT", "CERTIFICATION_REQUIRED", "NO_FORMAL_IAQ"],
                        help="[P1-09] 交付就绪档位。缺省=STANDARD 固定双测："
                             "首次 GB 50325，二次 GB/T 18883。仅 NO_FORMAL_IAQ 为显式不做检测。")
    parser.add_argument("--flushout_days", type=int, default=None,
                        help="[FIX-02 E] 显式散味天数。必须与 --occupancy_basis 同时给出，"
                             "否则拒绝进入 committed programme")
    parser.add_argument("--occupancy_basis", type=str, default=None,
                        help="[FIX-02 E] --flushout_days 的依据 (client_requirement / local_regulation / "
                             "certification_scheme ...)")
    parser.add_argument("--occupancy_confirmed_by", type=str, default=None,
                        help="[FIX-02 E] 确认人 (如 client_pm)")
    parser.add_argument("--activity_calendar_override", type=str, default=None,
                        help="[FIX-01 A] 活动日历项目级覆写，JSON 或 .json 文件路径，如 "
                             '\'{"FACTORY_PRODUCTION": {"working_days_per_week": 7}}\'')
    parser.add_argument("--engine_regression", action="store_true", default=False,
                        help="Regression only. Required before --cost, --floors, --floor_breakdown, "
                             "--freight_lifts, --zones, or --rate_level. The agent must not pass this.")
    # ③ x ④ -> 4 套模板键映射
    TEMPLATE_MAP = {
        ("DB", "invite"):  "Standard_Fitout_DB_Invite",
        ("DB", "public"):  "Standard_Fitout_DB_Public",
        ("DBB", "invite"): "Standard_Fitout_DBB_Invite",
        ("DBB", "public"): "Standard_Fitout_Office_DBB",
    }
    
    args = parser.parse_args(argv)
    from core.project_input import _clean, NUMBERS, ALIASES
    for key, value in vars(args).items():
        field = ALIASES.get(key, key)
        if (field in NUMBERS or field in {"start_date", "target_date"}) and value not in (None, ""):
            try: setattr(args, key, _clean(field, value))
            except (ValueError, TypeError, OverflowError) as exc: parser.error(f"{key}: {exc}")
    if not str(args.project_name or "").strip() or args.project_name == "未命名项目":
        args.project_name = "室内装修排程项目"
        logger.warning("项目名称未提供：使用通用名称，正式汇报前请回填。")

    if any(str(getattr(args,k)).lower() not in {"false","0","no","否"} for k in ("segmented_review","segmented_permit")):
        parser.error("分段图审/分段许可：需项目所在地主管部门确认及分段范围；当前未配置批准的分段方案，保持一次图审、一次发证。")

    _gated = ("--cost", "--floors", "--floor_breakdown", "--freight_lifts", "--zones", "--rate_level")
    _used = []
    for _token in argv:
        _name = _token.split("=", 1)[0]
        if _name in _gated and _name not in _used:
            _used.append(_name)
    if _used and not args.engine_regression:
        raise SystemExit(
            "CONTRACT_REJECTED: "
            + ", ".join(_used)
            + " are regression-only. Pass --engine_regression from a test, not from the agent. "
            + "Do not invent a cost, floor count, lift count, zone count, or rate level."
        )
    from core.entry_pipeline import resolve_engine_input
    intake_facts, input_contract = resolve_engine_input(args, argv)
    for _k in ("cleanroom_class", "nc_rating", "esd_level", "power_kva",
               "mocap_camera_count", "mocap_stage_area_sqm", "project_name",
               "statutory_tender_required", "power_supply_type", "eia_document_type",
               "fume_hood_count", "space_use"):
        if getattr(args, _k, None) not in (None, ""):
            intake_facts[_k] = getattr(args, _k)
    from core.equipment_leads import FIELDS as _LEAD_FIELDS
    for _lf in _LEAD_FIELDS:
        if getattr(args, _lf, None) not in (None, ""):
            intake_facts[_lf] = getattr(args, _lf)
    from core.module_catalog import CATALOG as _ADDON_CATALOG
    for _aid in _ADDON_CATALOG:
        _ak = "addon_" + _aid.lower()
        if getattr(args, _ak, None) not in (None, ""):
            intake_facts[_ak] = getattr(args, _ak)
    if "statutory_tender_required" in intake_facts:
        raw = intake_facts["statutory_tender_required"]
        if isinstance(raw, str):
            intake_facts["statutory_tender_required"] = raw.strip().lower() in {"true", "yes", "1", "是", "依法必须招标"}
    bench = {"cpm": None, "procurement_permit": None, "pdf": None, "diff": None, "total": None}
    _t_run = time.perf_counter()


    if not args.area or args.area <= 0:
        raise SystemExit("Area must be positive. Please specify --area or provide --intake_excel")
    if not args.city:
        raise SystemExit("City must be specified. Please specify --city or provide --intake_excel")
    from core.sector_router import resolve_sector_id, SectorRequiredError, sector_pack_plan
    try:
        args.sector = resolve_sector_id(args.sector)
    except SectorRequiredError as exc:
        raise SystemExit(str(exc))
    intake_facts["sector_pack_plan"] = sector_pack_plan(args.sector, intake_facts)
    # city clock 接线：args.city 必须写回 intake_facts，否则 _apply_city_statutory 收 None 而失效
    intake_facts["city"] = args.city
    if intake_facts["sector_pack_plan"].get("disclosure"):
        logger.warning(intake_facts["sector_pack_plan"]["disclosure"])

    if args.addons:
        raise SystemExit('Specialist add-ons require typed quantity/rate mapping before scheduling')
    if args.area <= 0 or (args.cost is not None and args.cost < 0):
        raise SystemExit('Area must be positive and cost must not be negative')
    if args.management_buffer_days < 0:
        raise SystemExit('management_buffer_days must be a non-negative integer')

    # ---- V3.5: L2 derivation 与工艺包署名校验 ----
    # 只判定来源与署名，不改任何 b0 数值。配置不合规则拒绝出计划（硬失败）。
    from core.l2_derivation import (
        load_signoff_policy, load_productivity_rates,
        validate_derivations, signoff_open_decisions, summary_line,
    )
    _signoff_policy = load_signoff_policy()
    derivation_report = validate_derivations(
        _signoff_policy,
        area_sqm=float(args.area),
        rates=load_productivity_rates(),
    )
    logger.info(summary_line(derivation_report))
    for _err in derivation_report['errors']:
        logger.error('  -> [derivation 硬失败] %s | %s', _err.get('code'), _err.get('detail'))
    for _warn in derivation_report['warnings']:
        if _warn.get('code') != 'PACKAGE_UNCONFIRMED':
            logger.warning('  -> [derivation 警告] %s | %s', _warn.get('code'), _warn.get('detail'))
    if derivation_report['hard_fail']:
        raise SystemExit(
            'DERIVATION_GATE_FAILED: L2 基准工期存在无来源或无署名的条目，拒绝出计划。')
    logger.info('  -> [derivation] 来源与署名校验通过（13 个工艺包署名状态见 audit）。')

    # ---- ③ x ④ 解析为模板键 + 招标方式 ----
    if args.mode:
        # 兼容旧版: 直接给模板键
        mode_key = args.mode
        if mode_key not in TEMPLATE_MAP.values():
            raise SystemExit(f'Unknown template: {mode_key}')
        bidding_val = "public" if mode_key in (TEMPLATE_MAP[('DB', 'public')], TEMPLATE_MAP[('DBB', 'public')]) else "invite"
        delivery_val = "DBB" if "DBB" in args.mode else "DB"
    else:
        # 缺省交互询问 (仅 TTY); 非交互则报错退出, 不伪造排程
        if not args.delivery or not args.bidding:
            try:
                if sys.stdin.isatty():
                    if not args.delivery:
                        args.delivery = input("招采形态 (D&B 输入 DB, DBB 输入 DBB): ").strip().upper()
                    if not args.bidding:
                        args.bidding = input("招标方式 (邀请 invite / 公开 public): ").strip().lower()
                else:
                    raise EOFError
            except EOFError:
                logger.error("缺少 --delivery / --bidding，请显式指定招采形态与招标方式")
                raise SystemExit("用法: --delivery DB|DBB --bidding invite|public")
        if args.delivery not in ("DB", "DBB"):
            raise SystemExit(f"无效招采形态: {args.delivery} (应为 DB 或 DBB)")
        if args.bidding not in ("invite", "public"):
            raise SystemExit(f"无效招标方式: {args.bidding} (应为 invite 或 public)")
        mode_key = TEMPLATE_MAP[(args.delivery, args.bidding)]
        delivery_val, bidding_val = args.delivery, args.bidding

    # 现场交付：总控默认全拆。仅当显式 --site_condition unknown 时才不画拆除。
    if args.site_condition == 'unknown':
        if "site_condition" in intake_facts and intake_facts["site_condition"]:
            args.site_condition = str(intake_facts["site_condition"]).strip().lower()

    logger.info(f"\n[{args.project_name}] 开始编排总控计划...")

    # ---- ⑤ 排期锚点硬化: 必须给 start_date 或 target_date ----
    if not args.start_date and not args.target_date:
        try:
            if sys.stdin.isatty():
                ans = input("未提供排程锚点。选择: 1=正排(输入项目启动日 Kick-off YYYY-MM-DD) 2=倒排(输入目标搬迁日 YYYY-MM-DD): ").strip()
                if ans == "1":
                    args.start_date = input("项目启动日 Kick-off (YYYY-MM-DD): ").strip()
                elif ans == "2":
                    args.target_date = input("目标搬迁日 (YYYY-MM-DD): ").strip()
                else:
                    raise SystemExit("无效选择，请输入 1 或 2")
            else:
                raise EOFError
        except EOFError:
            logger.error("缺少 --start_date / --target_date，请至少提供一个排程锚点 (拒绝生成零日期失真排程)")
            raise SystemExit("用法: --start_date YYYY-MM-DD 或 --target_date YYYY-MM-DD")
    logger.info(f"==================================================")
    
    # 1. 呼叫合规专家 (Compliance Expert)
    logger.info("Step 1: 读取属地报审规则与招采条件...")
    permit_info = query_city_permit_rule(args.city, area_sqm=args.area, cost_10k_rmb=args.cost)
    # 2026-10-13: a permit is always planned unless the user states it will not be applied for.
    # The city table's exemption limits are unverified, so they only produce a note.
    _waived = intake_facts.get('permit_waived') is True or str(intake_facts.get('permit_waived', '')).strip().lower() in ('true', '1', '是', 'yes')
    if permit_info.get('is_exempt') and not _waived:
        logger.info('  -> [免办提示] 城市参考表显示可能低于免办限额（未核实）；未经用户确认，计划保留施工许可。')
        permit_info['exempt_advisory'] = permit_info.get('exempt_desc')
    permit_info['is_exempt'] = bool(_waived)
    from core.local_authority import resolve_local_authority, apply_local_authority, draft_allowed
    intake_facts.setdefault('area_sqm',args.area)
    intake_facts.setdefault('start_date',args.start_date)
    intake_facts.setdefault('cost_10k_rmb',args.cost)
    local_authority_plan=resolve_local_authority(intake_facts)
    if local_authority_plan['status']!='READY' and draft_allowed(intake_facts, local_authority_plan):
        # Same rule as the entry contract: missing facts give a marked diagnostic draft.
        intake_facts['authority_draft']=True
        logger.warning('[报审] 报审路径未核实，按诊断草案继续并醒目标注：%s',
                       '；'.join(q['question'] for q in local_authority_plan['questions']))
    if local_authority_plan['status']!='READY' and not intake_facts.get('authority_draft'):
        os.makedirs(os.path.dirname(os.path.abspath(args.output)),exist_ok=True)
        with open(os.path.splitext(args.output)[0]+'_authority_questions.json','w',encoding='utf-8') as f:
            dump_json(local_authority_plan,f,ensure_ascii=False,indent=2)
        raise SystemExit('AUTHORITY_INPUT_REQUIRED: '+json.dumps(local_authority_plan['questions'],ensure_ascii=False))
    if local_authority_plan['status']=='READY':
        permit_info['is_exempt']=False # Keep anchors; the local adapter replaces the correct route after weaving.
    tender_info = calc_tender_duration(bidding=bidding_val, cost_10k_rmb=args.cost)

    # P0-04: Authority Decision Model —— 施工许可与消防/竣工备案法定闭环
    from core.authority_decision import (decide_authority_requirements, authority_decision_trace,
                                          authority_wbs_consistency)
    statutory_mode = str(getattr(args, "authority_profile", "STANDARD") or "STANDARD").upper() != "UNKNOWN"
    authority = decide_authority_requirements(permit_info, area_sqm=args.area, cost_10k_rmb=args.cost,
                                              statutory_baseline=statutory_mode)
    for line in authority_decision_trace(authority):
        logger.info("  -> [authority] %s", line)
    
    logger.info(f"  -> 报审模板参数 [{args.city}]（项目适用性待核实）: 图审规划 {permit_info.get('review_days_min', 7)}天, 许可规划 {permit_info.get('permit_days_min', 5)}天")
    logger.info('  -> 招采方式=%s；实际周期以CPM阶段汇总为准，邀请方式不等于已确认法律适用。', bidding_val)

    # 2. 呼叫工序专家 (Process Expert) - 加载 WBS
    logger.info(f"Step 2: 加载工序模板 [{mode_key}] 模版 (delivery={delivery_val}, bidding={bidding_val})...")
    template_path = os.path.join(BASE_DIR, "templates", "wbs_templates.json")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            template_data = json.load(f)
            tasks = template_data["templates"][mode_key]["tasks"]
            template_base_area = template_data["templates"][mode_key].get("base_area", 1000)
            logger.info(f"  -> 成功加载 100% 完整标准 WBS 节点树 (共 {len(tasks)} 节点)")
    except Exception as e:
        logger.error(f"ERROR: 读取 WBS 模版失败: {e}")
        raise SystemExit(f'WBS_LOAD_FAILED: {e}') from e

    tasks = expand_fitout_wbs(tasks)

    # 4. 执行严谨的【PM 业务领域 SOP 下限断言与面积非线性缩放】
    logger.info(f"Step 2.8: 执行 PMP 业务常识断言与工期二次校准...")
    tasks = calibrate_durations(
        tasks, permit_info, args.area, template_base_area, args.addons,
        cost_10k_rmb=args.cost, log=logger, bidding=bidding_val)

    if tasks:
        tasks[0]["name"] = args.project_name

    # v4.1: 消费 is_exempt —— 免办则折叠政府施工许可阶段（保留物业送审）
    if permit_info.get("is_exempt"):
        logger.info(
            f"  -> [免办判定] 面积/造价低于属地门槛（{permit_info.get('exempt_desc', '')}），"
            f"折叠《施工许可证》政府节点。豁免仅针对施工许可这一独立法定事项；"
            f"物业送审、图审与消防路径属不同法定事项，保留并另行判定（见 authority_decision_trace）。"
        )
        tasks = fold_exempt_construction_permit(tasks, True, log=logger)
    else:
        logger.info("  -> [免办判定] 需办理施工许可（或未计算豁免），保留许可阶段节点。")

    # P0-04: 无矛盾 WBS 校验 —— 豁免则许可节点必须已折叠；必办则许可里程碑必须在场。
    authority_issues = authority_wbs_consistency(tasks, authority)
    if authority_issues:
        raise SystemExit(f'AUTHORITY_WBS_CONTRADICTION: {authority_issues}')

    tasks = clean_procurement_terminology(tasks, mode_key, log=logger)
    tasks = renumber_tasks_contiguously(tasks)

    # P1-10: 家具交期档位 —— 通用模板工期不再当作事实，交期由档位驱动并留痕
    from core.furniture_lead import furniture_lead_block
    furniture_resolved = dict(profile=None, lead_days=0, lead_time_basis='默认不加载家具供货链', confidence='not_scheduled', evidence_ref=None, assumed=False)
    furniture_report = dict(applied=False,reason='tracking_not_loaded')


    # P1-06: 招采档位 —— 小体量邀请招标允许声明式 overlap，硬门（回标/BAFO/LOA/签约）保持 FS
    from core.procurement_profile import (ProcurementProfileError, apply_procurement_profile,
                                           resolve_procurement_profile, procurement_profile_block)
    proc_profile_key = None if args.procurement_profile == 'auto' else args.procurement_profile
    try:
        procurement_resolved = resolve_procurement_profile(proc_profile_key, area_sqm=args.area, bidding=bidding_val)
    except ProcurementProfileError as ppe:
        raise SystemExit(f'PROCUREMENT_PROFILE_ERROR: {ppe}')
    procurement_report = apply_procurement_profile(tasks, procurement_resolved)
    if procurement_report['applied']:
        logger.info("  -> [P1-06] 招采档位 %s：应用 %d 处声明式 overlap（硬门保持 FS）",
                    procurement_resolved['profile'], len(procurement_report['applied']))
        for a in procurement_report['applied']:
            logger.info("     · %s ← %s %s+%dd (%s)",
                        a['successor']['name'], a['predecessor']['name'],
                        a['relationship'], a['lag'], a['relationship_reason'])
    if procurement_report['skipped']:
        for s in procurement_report['skipped']:
            logger.warning("  -> [P1-06] overlap 跳过（不臆造链接）: %s", s['reason'])
    for p in procurement_report['protected_gates']:
        if not p['all_fs']:
            raise SystemExit(f'PROCUREMENT_GATE_BYPASS: protected gate degraded: {p}')

    # P1-07: D&B 设计分批释放 —— 物业送审基于 SD 包启动，与施工图/图审并行；法定链不放松
    from core.design_release import (DesignReleaseError, apply_design_release, design_release_block)
    design_release_enabled = None if args.design_release == 'auto' else (args.design_release == 'on')
    try:
        design_release_report = apply_design_release(tasks, delivery=delivery_val, enabled=design_release_enabled)
    except DesignReleaseError as dre:
        raise SystemExit(f'DESIGN_RELEASE_ERROR: {dre}')
    if design_release_report['enabled']:
        for a in design_release_report['applied']:
            logger.info("  -> [P1-07] D&B 设计释放: %s 由 %s 改挂 %s (%s)",
                        a['task']['name'], a['from_gate']['name'], a['to_gate']['name'], a['reason'])
        for s in design_release_report['skipped']:
            logger.warning("  -> [P1-07] 释放重挂跳过（不臆造链接）: %s", s['reason'])
        for p in design_release_report['statutory_protections']:
            if p['satisfied'] is False:
                raise SystemExit(f'DESIGN_RELEASE_STATUTORY_BYPASS: {p}')
            if p['satisfied'] is None:
                logger.info("  -> [P1-07] 法定保护 %s: 任务不在 WBS（可能因豁免折叠），标记 N/A: %s",
                            p['gate'], p['reason'])
    else:
        logger.info("  -> [P1-07] 设计分批释放不启用（%s）", design_release_report.get('reason'))

    # P1-09: 交付就绪档位 —— 尾段（首次IAQ/散味/二次复测/保洁）
    from core.occupancy_readiness import (OccupancyProfileError, apply_occupancy_profile,
                                           resolve_occupancy_profile, occupancy_profile_block)
    occ_prof = args.occupancy_profile
    try:
        occupancy_resolved = resolve_occupancy_profile(
            occ_prof, flushout_days=args.flushout_days,
            occupancy_basis=args.occupancy_basis, confirmed_by=args.occupancy_confirmed_by)
    except OccupancyProfileError as ope:
        raise SystemExit(f'OCCUPANCY_PROFILE_ERROR: {ope}')
    if args.occupancy_basis and args.flushout_days is None:
        logger.warning("[FIX-02] 提供了 --occupancy_basis 但没有 --flushout_days；依据不生效。")
    try:
        occupancy_report = apply_occupancy_profile(tasks, occupancy_resolved)
    except OccupancyProfileError as ope:
        raise SystemExit(f'OCCUPANCY_PROFILE_PROTECTION: {ope}')
    for o in occupancy_report.get('duration_overrides', []):
        logger.info("  -> [P1-09] 尾段参数 %s: %s %sd → %sd (profile=%s)",
                    o['anchor'], o['task']['name'], o['old_duration_days'], o['new_duration_days'],
                    occupancy_resolved['profile'])
    for m in occupancy_report.get('missing_anchors', []):
        logger.warning("  -> [P1-09] 尾段锚点缺失（参数未适用，不臆造）: %s (%s)", m['anchor'], m['reason'])
    if occupancy_report.get('folded'):
        for f_ in occupancy_report['folded']:
            logger.warning("  -> [P1-09] 折叠为0天占位: %s —— %s", f_['task']['name'], f_['reason'])
    if occupancy_resolved.get('status') == 'UNKNOWN':
        logger.warning("[FIX-02] OCCUPANCY_IAQ_UNKNOWN: 未输入 occupancy / IAQ / 散味要求 —— "
                       "不做 STANDARD 35d 假设，也不当 0 天。相关活动转为 CONDITIONAL REQUIREMENT "
                       "(Baseline Commitment=UNRESOLVED)，并以情景重解 CPM 给出 Conditional Exposure。")
        for c in occupancy_report.get('conditional_requirements', []):
            logger.warning("  -> [conditional] %s: template %sd → UNRESOLVED (not committed; "
                           "decision required)", c['task']['name'], c['template_duration_days'])
    elif occupancy_resolved.get('confidence') == 'provisional':
        logger.warning("[P1-09] OCCUPANCY_PROFILE_PROVISIONAL: 档位 %s 已显式选择，但参数仍属 provisional —— "
                       "basis=%s；confidence=%s。客户/属地/认证要求确认后复核。",
                       occupancy_resolved['profile'], occupancy_resolved['basis'],
                       occupancy_resolved['confidence'])

    # Single production path: expand packages, calculate quantities/resources/durations,
    # and enforce typed dependencies including FS inspection releases.
    measured = _parse_quantities(args.quantities)
    active = {t['activity_type'] for t in tasks if t.get('activity_type')}
    crew_plan={}
    crew_plan_meta={}
    if args.crew_plan:
        with open(args.crew_plan,encoding='utf-8') as f:
            raw_plan=json.load(f)
        # P2-16: {meta, trades} 新schema（meta 自由注释不炸）+ 旧 flat schema 完全兼容
        from core.crew_plan_schema import parse_crew_plan, CrewPlanSchemaError
        try:
            parsed_plan = parse_crew_plan(raw_plan, frozenset(active))
        except CrewPlanSchemaError as cpe:
            raise SystemExit(f'CREW_PLAN_SCHEMA_ERROR: {cpe}')
        crew_plan = parsed_plan['trades']
        crew_plan_meta = parsed_plan['meta']
        if crew_plan_meta:
            logger.info("  -> [P2-16] crew_plan %s schema；meta: %s",
                        parsed_plan['schema'],
                        '; '.join(f"{k}={v}" for k, v in sorted(crew_plan_meta.items())
                                  if not isinstance(v, (dict, list))) or '(complex)')
    if set(measured) - active:
        raise SystemExit(f'Unknown or inactive measured activity types: {sorted(set(measured) - active)}')
    for task in tasks:
        if task.get('activity_type'):
            if task['activity_type'] in crew_plan:
                task['crew_size']=crew_plan[task['activity_type']]
            task.update(shifts=args.shifts, workfront_efficiency=args.workfront_efficiency)
            task['factors'] = dict(complexity=args.complexity, access=args.access)
            if args.rate_level:
                task['rate_level'] = args.rate_level
            if task['activity_type'] in measured:
                task.update(quantity=measured[task['activity_type']], quantity_source='measured')
    # P1-11: scope profile —— 未输入范围时完整工种按 ASSUMED_SCOPE 排入并披露，不当作事实
    from core.scope_profile import (resolve_scope_profile, scope_profile_exclusions, ScopeProfileError)
    try:
        scope_resolved = resolve_scope_profile(args.scope_profile, site_condition=args.site_condition)
    except ScopeProfileError as se:
        raise SystemExit(f'SCOPE_PROFILE_ERROR: {se}')
    scope_excluded = scope_profile_exclusions(scope_resolved, sorted(active))
    if scope_resolved['assumed_scope']:
        logger.warning("[P1-11] ASSUMED_SCOPE: 仅提供了面积，未提供范围档位 —— 完整工种清单按【假设范围】排入，"
                       "非确认范围。假设含工种=%d 项；未知项：%s。用 --scope_profile 显式声明。",
                       len(active), '; '.join(scope_resolved['unknowns']))
    else:
        logger.info("  -> [P1-11] 范围档位 %s：显式排除 %d 个工种，复用系统：%s",
                    scope_resolved['profile'], len(scope_excluded),
                    '; '.join(scope_resolved['assumed_reuse']) or '无')

    # PM Master Schedule: never explode Floor×Zone trades on the client Gantt.
    # Spatial pipeline is opt-in L3 only (--zoning_mode manual|auto).
    from core.fitout_engine import prepare_spatial_fitout
    from core.logistics_engine import logistics_governance, explain as _logistics_explain

    floor_specs = _parse_floor_breakdown(args.floor_breakdown)
    if args.floors is None or int(args.floors) < 1:
        raise SystemExit('FLOORS_INVALID: --floors must be an integer >= 1')
    floors_n = len(floor_specs) if floor_specs else int(args.floors)
    spatial_requested = args.zoning_mode in ('manual', 'auto')
    spatial_plan = None

    if spatial_requested:
        if args.zoning_mode == 'manual':
            if not args.zones:
                raise SystemExit('ZONING_MANUAL_REQUIRES_ZONES: --zoning_mode manual 需要 --zones N '
                                 '（每层分区数）')
            manual_zones = _manual_zones_for(
                floor_specs or [{"floor_id": "F1", "area_sqm": float(args.area)}], int(args.zones))
            zone_mode = 'manual'
        else:
            manual_zones = None
            zone_mode = 'auto'
        prepared = prepare_spatial_fitout(
            tasks, args.area, office_type=args.layout or 'standard',
            fitout_grade=args.fitout_grade, grade=args.grade,
            zone_mode=zone_mode, manual_zones=manual_zones,
            workfronts=args.workfronts, city=args.city,
            evidence_path=args.productivity_evidence, calibration_project=args.calibration_project,
            site_condition=args.site_condition, demolition_fraction=args.demolition_fraction,
            floors_count=int(args.floors), floor_breakdown=floor_specs,
            scope_excluded_activity_types=scope_excluded,
            freight_lifts=args.freight_lifts)
        spatial_plan = prepared.get('plan')
        logger.info('[V6.6.0] NATIVE SPATIAL PIPELINE: floors=%s zones=%s mode=%s '
                    '→ spatial tasks %s', floors_n,
                    len(spatial_plan['zones']) if spatial_plan else 0,
                    zone_mode, len(prepared['tasks']))
    else:
        prepared = prepare_fitout(tasks, args.area, office_type=args.layout or 'standard',
                                 fitout_grade=args.fitout_grade, grade=args.grade,
                                 workfronts=args.workfronts, sectional=args.sectional, city=args.city,
                                 evidence_path=args.productivity_evidence, calibration_project=args.calibration_project,
                                 site_condition=args.site_condition, demolition_fraction=args.demolition_fraction,
                                 scope_excluded_activity_types=scope_excluded,
                                 floors=1, freight_lifts=None)
    tasks, derived_q = prepared['tasks'], prepared['quantities']
    dep_report = prepared.get('dependencies') or {}
    l2_review = prepared.get('l2_review') or {}
    if l2_review:
        logger.info('[L2] master-beat applied=%s skipped=%s n_serial=%s concurrent=%s (%s) envelope=%s',
                    l2_review.get('applied'), l2_review.get('skipped_unconfigured'),
                    l2_review.get('n_serial'), l2_review.get('concurrent_packages'),
                    l2_review.get('concurrent_reason'),
                    (l2_review.get('envelope') or {}).get('status'))
        for w in (l2_review.get('warnings') or [])[:12]:
            logger.warning('[L2] %s %s: %s', w.get('code'), w.get('name'), w.get('detail'))

    # ---- vertical logistics governance (gate-based; never a throughput model) ----
    logistics = logistics_governance(args.freight_lifts, floors=floors_n,
                                     zones=(len(spatial_plan['zones']) if spatial_plan else None))
    for _line in _logistics_explain(logistics):
        logger.info('[V6.6.0] %s', _line)
    if args.site_condition == 'unknown':
        logger.warning("SITE CONDITION UNCONFIRMED: 显式 unknown，拆除不进对客甘特。")
    logger.info('L2 occupancy authority after diagnostic Q/R/C sidecar; formula_tasks=%s (not client duration)', len(active))
    # 行业专项特种工程与 资本里程碑深度编织
    logger.info("Step 2.9: 行业包织入 (Sector Weaver: %s)...", args.sector)
    from core.sector_weaver import _build_control_milestone_rollup
    from core.modular_weaver import weave_sector_modules
    weave_facts = dict(intake_facts, sector=args.sector, delivery=delivery_val,
                       bidding=bidding_val, area_sqm=args.area, city=args.city)
    tasks = weave_sector_modules(tasks, sector=args.sector, area_sqm=args.area, facts=weave_facts)
    tasks, local_authority_plan = apply_local_authority(tasks, weave_facts)
    if local_authority_plan['status']=='READY':
        from core.authority_decision import _decision
        settings={'construction_permit_required':local_authority_plan['permit_required'],
                  'fire_design_review_required':local_authority_plan['fire_route']=='special',
                  'fire_acceptance_required':local_authority_plan['fire_route']=='special',
                  'fire_filing_required':local_authority_plan['fire_route']=='other',
                  'joint_completion_required':local_authority_plan['completion_filing'],
                  'third_party_testing_required':False}
        for key,required in settings.items():
            authority['requirements'][key]=_decision('TRUE' if required else 'FALSE',
                '按已提供工程分类组装；分类本身未独立鉴证。', '; '.join(local_authority_plan['sources']),'declared_classification')
        authority['unknown_requirements']=[];authority['conflicts']=[];authority['needs_confirmation']=False
    furniture_report = next((t['process_closure']['furniture'].get('lead_report',{}) for t in tasks if t.get('process_closure')),furniture_report)
    furniture_resolved = next((t['process_closure']['furniture']['resolved_lead'] for t in tasks if t.get('process_closure')),furniture_resolved)

    tasks = renumber_tasks_contiguously(tasks)
    from core.l2_master_duration import apply_l2_master_durations, label_reference_plate
    l2_review = apply_l2_master_durations(tasks, area_sqm=float(args.area))
    label_reference_plate(tasks)
    logger.info('[L2] after sector weave applied=%s timed_tests=%s not_scaled=%s',
                l2_review.get("applied"),
                sum(1 for w in (l2_review.get("warnings") or []) if w.get("code") == "TIMED_TEST_NO_AREA_SCALE"),
                sum(1 for t in tasks if t.get("duration_authority") == "not_area_scaled"))
    from core.duration_derivation import apply_preconstruction_durations
    duration_audit = apply_preconstruction_durations(tasks, weave_facts, city_permit=permit_info)
    from core.concealed_works import apply_concealed_works
    concealed_report = apply_concealed_works(tasks, weave_facts)
    from core.delivery_model import apply_delivery_model
    delivery_report = apply_delivery_model(tasks, weave_facts)
    from core.fire_sequence import apply_fire_sequence
    fire_report = apply_fire_sequence(tasks, weave_facts)
    from core.addon_practices import apply_addon_practices
    addon_report = apply_addon_practices(tasks, weave_facts)
    logger.info('[duration] preconstruction applied=%s; concealed=%s; delivery=%s; fire=%s; addons=%s',
                duration_audit.get('applied'), concealed_report.get('applied'), delivery_report.get('changes'),
                fire_report.get('applied'), addon_report.get('selected'))
    from core.round5_controls import apply_controls
    apply_controls(tasks, weave_facts)
    graph_issues = validate_dependency_graph(tasks)
    if graph_issues:
        raise SystemExit(f'DEPENDENCY_GRAPH_INVALID: {graph_issues}')

    # v3 单源：config/holidays.json 一次读取，求解器与 MPP 日历共用同一份
    from core.task_contract import annotate_task_contract
    annotate_task_contract(tasks)
    holidays_raw = _holidays.load_holiday_raw()       # [{"name","start","finish"}] -> MPP 日历 Exceptions
    holidays_pairs = _holidays.load_holiday_pairs()   # [(start,end)] -> 求解器 is_workday

    # P1-12: 施工日历档位 —— 缺省 7D 不再是默默的：每次运行显式披露 + 推荐 6D
    from core.calendar_profile import resolve_calendar_profile, calendar_basis_block, CalendarProfileError
    try:
        calendar_resolved = resolve_calendar_profile(args.calendar_profile, args.building_restriction)
    except CalendarProfileError as ce:
        raise SystemExit(f'CALENDAR_PROFILE_ERROR: {ce}')
    cal_mode = calendar_resolved['calendar_mode']
    if input_contract and input_contract['field_evidence']['calendar_profile']['origin'] == 'default_policy':
        calendar_resolved['defaulted_to_7d'] = True
        calendar_resolved['disclosure'] = input_contract['field_evidence']['calendar_profile']['basis']

    # ---- FIX-03: UNKNOWN authority requirements -> CONDITIONAL PLACEHOLDER ----
    # UNKNOWN 不得作为 confirmed mandatory duration 进入 committed baseline；
    # 活动保留可见性（不删除），工期标记 UNRESOLVED。
    from core.authority_boundary import review_authority_boundary
    authority_boundary_report = review_authority_boundary(tasks, args.city)
    authority_boundary_report['local_route']=local_authority_plan
    authority_boundary_report['workflow_implemented']=local_authority_plan['status']=='READY'
    authority_boundary_report['status']=local_authority_plan['status']
    if local_authority_plan['status']=='READY':
        authority_boundary_report['note']='已按声明的工程分类接入'+local_authority_plan['jurisdiction']['name']+'规则模块；分类未独立鉴证，公开窗口适用性与工作包工期仍须项目核对。'
    from core.authority_decision import map_authority_tasks
    authority_mapping = map_authority_tasks(tasks, authority)
    for t in tasks:
        if t['id'] in authority_mapping.get('planning_allowance_task_ids', []):
            t['programme_basis'] = 'conservative_STANDARD_allowance'
            t['legal_applicability'] = 'UNVERIFIED_PROGRAMME_POLICY'
    for req_name, entry in sorted(authority_mapping['requirements'].items()):
        logger.info("  -> [FIX-03] %s = %s (%s) tasks=%s", req_name, entry['status'],
                    entry['disposition'],
                    ', '.join(str(t['id']) for t in entry['tasks']) or 'none (missing anchor)')
    for miss in authority_mapping['missing_anchors']:
        logger.warning("  -> [FIX-03] 权威事项 %s 在 WBS 中无对应节点 —— 不臆造，仅留决策项", miss)

    # ---- FIX-04: 条件需求登记 + committed baseline（UNKNOWN 不计入承诺工期）----
    from core.conditional_requirements import (build_conditional_registry, apply_committed_baseline,
                                               evaluate_conditional_exposure,
                                               evaluate_backward_conditional_exposure,
                                               conditional_exposure_block, programme_certainty_block)
    conditional_registry = build_conditional_registry(tasks, occupancy_resolved, authority_mapping)
    apply_committed_baseline(tasks, conditional_registry)
    if conditional_registry['conditional_tasks']:
        logger.warning("[FIX-04] CONDITIONAL TASKS (UNRESOLVED, audit only, hidden from client Gantt): %s",
                       '; '.join("%s#%s(%sd→UNRESOLVED)" % (c['name'], c['task_id'],
                                                            c['template_duration_days'])
                                 for c in conditional_registry['conditional_tasks']))

    # ---- FIX-01: ACTIVITY CALENDAR（项目日历 ≠ 活动日历 ≠ 资源日历）----
    from core.activity_calendar import (ActivityCalendarError, apply_activity_calendars,
                                        activity_calendar_block, parse_overrides,
                                        resource_calendar_statement)
    try:
        calendar_overrides = parse_overrides(args.activity_calendar_override)
    except ActivityCalendarError as ace:
        raise SystemExit(f'ACTIVITY_CALENDAR_ERROR: {ace}')
    activity_cal_report = apply_activity_calendars(tasks, cal_mode, calendar_overrides)
    for cls, info in sorted(activity_cal_report['by_class'].items()):
        logger.info("  -> [FIX-01] %-22s %-12s %dD  basis=%s  confidence=%s%s",
                    cls, info['calendar_id'], info['working_days_per_week'], info['basis'],
                    info['confidence'],
                    " (override)" if info['override_source'] else "")
    if not activity_cal_report['no_global_7d_propagation']:
        logger.warning("[FIX-01] 非现场类活动出现扩展工作周: %s —— 需复核是否为项目覆写",
                       ', '.join(activity_cal_report['non_site_classes_on_extended_week']))
    logger.warning("[FIX-01] %s", resource_calendar_statement()['disclosure'])
    # 物理 MPP 日历指派：SITE_* → 施工日历；FACTORY_6D → 工厂日历；其余 → 项目标准日历
    from core._common import CONSTRUCTION_CAL_NAME, FACTORY_CAL_NAME, LOGISTICS_CAL_NAME
    for t in tasks:
        cid = (t.get('activity_calendar') or {}).get('calendar_id')
        if cid is None:
            continue
        if cid.startswith('SITE_'):
            t['mpp_calendar_name'] = CONSTRUCTION_CAL_NAME
        elif cid == 'FACTORY_6D':
            t['mpp_calendar_name'] = FACTORY_CAL_NAME
        elif cid == 'LOGISTICS_7D':
            t['mpp_calendar_name'] = LOGISTICS_CAL_NAME
        else:
            t['mpp_calendar_name'] = None
    logger.info("  -> [calendar] 施工日历档位: %s (working_days_per_week=%s)", cal_mode,
                calendar_resolved['working_days_per_week'])
    if calendar_resolved['defaulted_to_7d']:
        logger.warning("[P1-12] %s", calendar_resolved['disclosure'])
    if calendar_resolved['forced_by_building_restriction']:
        logger.warning("[P1-12] 大楼限制 no_weekend_work 已覆写：施工日历强制 5D。")

    # Client/PM Lead master packages are the actual scheduled network. Retain
    # the configured source only as trace evidence; do not hide live work in MPP.
    from core.master_schedule import build_master_schedule
    from core.design_submission import align_submission_conditions
    align_submission_conditions(tasks)
    from core.requirements_alignment import align_requirements
    requirements_review=align_requirements(tasks)
    from core.move_scope import remove_internal_move_planning
    tasks,move_scope_review=remove_internal_move_planning(tasks)
    from core.construction_handover import align_handover
    handover_review=align_handover(tasks,args.construction_handover)
    from core.pm_planning_review import apply_planning_review
    planning_review=apply_planning_review(tasks,args.construction_handover)
    from core.furniture_installation import apply_installation
    furniture_installation_review=apply_installation(tasks,intake_facts)
    # build_master_schedule owns a deep copy before changing any source data.
    # Retain this untouched input as audit evidence without copying it twice.
    master_source = tasks
    tasks, master_report = build_master_schedule(tasks)
    master_report['furniture_installation']=furniture_installation_review
    master_report['construction_handover']=handover_review
    master_report['provisional_pm_planning_review']=planning_review
    master_report['requirements_alignment']=requirements_review
    master_report['move_scope_review']=move_scope_review
    logger.info('[MASTER] source=%s, master=%s, combined_packages=%s',
                len(master_source), len(tasks), len(master_report['groups']))
    logger.info("Step 3: 计算工期与关键路径...")
    _t_cpm = time.perf_counter()
    start_date_str = ""
    tasks_solved = tasks
    finish_date = ""
    target_semantics = None  # 仅倒排路径赋值；正排+target 走既有 AssertionError 契约

    if args.start_date:
        start_date_str = args.start_date
        logger.info(f"  -> 采用项目启动日正排，Kick-off（非整表施工进场）: {start_date_str}")
        solve_res = solve_schedule(tasks, start_date_str, custom_holidays=holidays_pairs, construction_calendar_mode=cal_mode)
        tasks_solved = solve_res["tasks"]
        finish_date = solve_res["finish_date"]
        logger.info(f"  -> 确定范围基准完工日（未计未确认条件项）: {finish_date}")
        
        if args.target_date:
            # The target is the move-in (搬迁开始), not the end of the move-in support.
            from core.solver_engine import move_in_date
            target_dt = datetime.datetime.strptime(args.target_date, "%Y-%m-%d").date()
            finish_dt = move_in_date(tasks_solved) or datetime.datetime.strptime(finish_date, "%Y-%m-%d").date()
            if finish_dt > target_dt:
                days_late = (finish_dt - target_dt).days
                logger.error(f"[PM排程审计断言] 排程失败！正推搬迁开始 {finish_dt} 晚于硬性目标死线 {args.target_date} 达 {days_late} 天！")
                if not args.allow_diagnostic_baseline:
                    raise AssertionError(f"搬迁开始 {finish_dt} 晚于目标搬迁日 {args.target_date} 达 {days_late} 个日历天；请通过统一入口选择保留推演计划或评估压缩，不能直接改短工期。")
                logger.warning('保留用户启动日与目标日，继续生成明确标注目标未达的诊断草案。')
    elif args.target_date:
        logger.info(f"  -> 采用倒排模式，目标死线: {args.target_date}")
        # P0-01: 用户目标日保持原意。倒排只消费 planning target；唯一允许的扣减是
        # 显式 --management_buffer_days（默认 0）。禁止任何隐藏 buffer。
        from core.target_feasibility import (resolve_planning_target, evaluate_target_feasibility,
                                              format_target_summary, format_infeasible_block)
        target_semantics = resolve_planning_target(args.target_date, args.management_buffer_days)
        for line in format_target_summary(target_semantics).splitlines():
            logger.info("  -> %s", line)
        if target_semantics['management_buffer_days'] > 0:
            logger.info("  -> [P0-01] 倒排仅使用 Planning Target Date；用户原始目标日不被改写。")
        solve_res = solve_schedule_with_target(tasks, target_finish_date_str=target_semantics['planning_target_date'], target_buffer_days=0, active_holidays=holidays_pairs, construction_calendar_mode=cal_mode)
        start_date_str = solve_res["start_date"]
        tasks_solved = solve_res["tasks"]
        finish_date = solve_res["finish_date"]

        if args.target_date:
            target_dt = datetime.datetime.strptime(args.target_date, "%Y-%m-%d").date()
            finish_dt = datetime.datetime.strptime(finish_date, "%Y-%m-%d").date()
            days_early = (target_dt - finish_dt).days
            if days_early > 90:
                logger.warning(f"[PM商业常识断言] 工期失真预警！正推完工日提前了 {days_early} 天（超过3个月！）。")

    # 真实项目终点 = 所有任务最晚 finish（含搬迁/客户入驻尾段），与 MPP 实测对齐。
    # 倒排路径 solve_schedule_with_target 返回的是搬迁锚点日，需以任务最晚finish为准，
    # 否则日志“项目终点”比 MPP 实测少算搬迁尾段（约7天），造成虚假的求解器/MPP 漂移。
    _true_finish = max((t.get("finish") for t in tasks_solved if t.get("finish")), default=finish_date)
    if _true_finish != finish_date:
        logger.info(f"  -> [校准] 项目终点由锚点日 {finish_date} 修正为任务最晚finish: {_true_finish}")
    finish_date = _true_finish

    # Make changed handover granularity reviewable at retained control gates.
    # Compare at the same kickoff, never restore old dates with constraints.
    # The solver only replaces top-level dates/boundaries; nested evidence is read-only.
    source_comparison = solve_schedule([dict(t) for t in master_source], start_date_str,
                                      custom_holidays=holidays_pairs, construction_calendar_mode=cal_mode)
    source_by_uid = {t.get('activity_uid'): t for t in source_comparison['tasks']}
    master_report['date_comparison'] = [dict(activity_uid=t.get('activity_uid'), name=t['name'],
        source_start=source_by_uid[t.get('activity_uid')].get('start'),
        source_finish=source_by_uid[t.get('activity_uid')].get('finish'),
        master_start=t.get('start'), master_finish=t.get('finish'))
        for t in tasks_solved if t.get('activity_uid') in source_by_uid
        and (t.get('milestone') or t.get('project_target') or any(k in t['name'] for k in
             ('隐蔽工程验收','物理竣工','室内空气','搬迁全部完成')))]
    master_report['source_finish_same_kickoff'] = source_comparison['finish_date']
    master_report['master_finish'] = finish_date

    # 顶层「关键里程碑总控」汇总（关键里程碑提升为 L2 顶部汇总条）—— CPM 解算完成、日期已定后
    # 再做，仅改变呈现顺序，不影响任何工期与依赖网络。若放在解算前（weave 内）会把收尾里程碑
    # 提前到顶部，而求解器按列表顺序解算会丢失其真实前置（里程碑漂到项目起点）。
    # Control gates stay in their lifecycle stage in the client master view.

    # ---- FIX-04 / P0-02: CONDITIONAL EXPOSURE —— 情景重解 CPM，绝不相加 ----
    conditional_exposure = None
    if conditional_registry['unresolved_driver_ids']:
        if target_semantics is not None:
            # P0-02: BACKWARD mode. Each scenario is solved BACKWARD independently
            # from the SAME planning target; reachability compares the scenario's
            # required start against the planning reference date. Forward-solving
            # from the optimistic baseline latest start is forbidden here — it
            # structurally biases any scenario with added duration to miss.
            planning_ref = datetime.date.today().isoformat()

            def _backward_start(task_list):
                r = solve_schedule_with_target(
                    task_list, target_finish_date_str=target_semantics['planning_target_date'],
                    target_buffer_days=0, active_holidays=holidays_pairs,
                    construction_calendar_mode=cal_mode)
                return r['start_date']

            conditional_exposure = evaluate_backward_conditional_exposure(
                tasks_solved, conditional_registry, _backward_start,
                planning_reference_date=planning_ref, target_date=args.target_date)
            rng = conditional_exposure['conditional_required_start_range']
            logger.warning("[P0-02] BACKWARD CONDITIONAL EXPOSURE (per-scenario backward re-solve): "
                           "required start range %s … %s (planning reference %s)",
                           rng['earliest'], rng['latest'], planning_ref)
            for s in conditional_exposure['scenarios']:
                logger.info("  -> [scenario] %s | %s | required_start=%s | margin=%sd | meets_target=%s",
                            s['scenario_id'], s['label'], s['scenario_required_start_date'],
                            s['start_margin_working_days'], s['meets_target'])
        else:
            def _solve_finish(task_list):
                r = solve_schedule(task_list, start_date_str, custom_holidays=holidays_pairs,
                                   construction_calendar_mode=cal_mode)
                return max((t.get('finish') for t in r['tasks'] if t.get('finish')), default=r['finish_date'])

            conditional_exposure = evaluate_conditional_exposure(
                tasks_solved, conditional_registry, _solve_finish, finish_date, args.target_date)
            rng = conditional_exposure['conditional_finish_range']
            logger.warning("[FIX-04] CONDITIONAL EXPOSURE (re-solved, not summed): committed %s → "
                           "potential finish range %s … %s",
                           finish_date, rng['earliest'], rng['latest'])
            for s in conditional_exposure['scenarios']:
                logger.info("  -> [scenario] %s | %s | finish=%s | meets_target=%s",
                            s['scenario_id'], s['label'], s['finish_date'], s['meets_target'])
            for d in conditional_exposure['drivers']:
                if not d.get('committed', True):
                    logger.warning("  -> [exposure] %s: +0 … +%sd (UNKNOWN; decision required)",
                                   d['label'], d['max_impact_days'])
        # P1-02: truncation is never silent.
        if conditional_exposure.get('scenarios_truncated'):
            logger.warning("[P1-02] SCENARIO TRUNCATION: %s", conditional_exposure['truncation_reason'])

    # P0-02: TARGET FEASIBILITY CHECK —— 倒排解得的最晚启动日早于规划参考日（今天）
    # 时，本解算只是 DIAGNOSTIC RECOVERY BASELINE，必须标记 NOT EXECUTABLE AS BASELINE，
    # 不得包装成正常可执行排程。正排路径不在此列（用户显式选定开工日）。
    target_feasibility = None
    # The unconfirmed-input list is consumed by BOTH the backward P0-02 date check
    # and the forward scenario classification (FIX-02 D / FIX-03 E), so it is built
    # once here instead of inside the backward-only branch.
    unconfirmed = []
    if args.site_condition == 'unknown':
        unconfirmed.append('site_condition_unconfirmed')
    for d in conditional_registry['unresolved_driver_ids']:
        unconfirmed.append(d.lower() + '_unconfirmed')
    if target_semantics is not None:
        from core.target_feasibility import (evaluate_target_feasibility, format_infeasible_block,
                                             classify_target_from_scenarios, DIAGNOSTIC_LABEL)
        target_feasibility = evaluate_target_feasibility(
            required_start_date=start_date_str,
            planning_reference_date=datetime.date.today(),
            unconfirmed_inputs=unconfirmed)
        target_feasibility['date_status'] = target_feasibility.get('status')
        target_feasibility['latest_feasible_start'] = start_date_str
        target_feasibility['backward_search'] = solve_res.get('backward_search')
        target_feasibility['target_met'] = solve_res.get('target_met')
        if solve_res.get('target_met') is False:
            target_feasibility['date_status'] = 'TARGET_INFEASIBLE'
            target_feasibility['status'] = 'TARGET_INFEASIBLE'
            target_feasibility['executable_as_baseline'] = False
        if target_feasibility['status'] == 'TARGET_INFEASIBLE':
            for line in format_infeasible_block(target_feasibility).splitlines():
                logger.warning(line)
        elif target_feasibility['status'] == 'CONDITIONALLY_FEASIBLE':
            logger.warning("[P0-02] CONDITIONALLY_FEASIBLE: 日期可行，但以下输入未确认，达标以确认为前提: %s",
                           ', '.join(target_feasibility['unconfirmed_inputs']))

    # ---- FIX-02 D / FIX-03 E: 目标可达性取决于 UNKNOWN 时，禁止输出确定结论 ----
    # 只有在所有 relevant states 结论一致时才允许 TARGET_MET / TARGET_INFEASIBLE。
    scenario_target_status = None
    if args.target_date and conditional_exposure:
        # Forward runs never enter the backward branch, so the import must be
        # resolved here as well (it is a local import by design: target_feasibility
        # stays optional for runs with no target date at all).
        from core.target_feasibility import (classify_target_from_scenarios,
                                             compose_target_dimensions, DIAGNOSTIC_LABEL)
        scenario_target_status = classify_target_from_scenarios(
            conditional_exposure['scenarios'], args.target_date,
            unconfirmed_inputs=(target_feasibility or {}).get('unconfirmed_inputs') or unconfirmed,
            committed_finish=finish_date,
            # P1-02: never let truncation silently masquerade as a complete
            # enumeration. enumerate_complete / enumeration_mode / envelope_proof
            # inside the result record the true state.
            truncated=bool(conditional_exposure.get('scenarios_truncated', False)))
        if target_feasibility is None:
            # Forward + target: no backward solve ran, so there is no P0-02 date
            # verdict. scenario_target_status is the only verdict; date_status stays
            # None so nobody reads a date claim that was never computed.
            target_feasibility = dict(status=None, required_start_date=start_date_str,
                                      planning_reference_date=datetime.date.today().isoformat(),
                                      start_gap_days=0, recovery_required_days=0,
                                      unconfirmed_inputs=list(unconfirmed),
                                      executable_as_baseline=True,
                                      diagnostic_label=None)
        target_feasibility['date_status'] = target_feasibility['status']
        target_feasibility['scenario_target_status'] = scenario_target_status
        target_feasibility['meets_target_in_all_scenarios'] = (
            scenario_target_status['scenarios_meeting_target'] == scenario_target_status['scenario_count'])
        target_feasibility['meets_target_in_any_scenario'] = (
            scenario_target_status['scenarios_meeting_target'] > 0)
        if scenario_target_status['status']:
            target_feasibility['depends_on'] = scenario_target_status['depends_on']
            # The P0-02 DIAGNOSTIC label is a *date* statement (required start is in
            # the past). A scenario-driven TARGET_INFEASIBLE is a different claim and
            # must not borrow that label.
            target_feasibility['executable_as_baseline'] = (
                target_feasibility['date_status'] != 'TARGET_INFEASIBLE')
            target_feasibility['diagnostic_label'] = (
                DIAGNOSTIC_LABEL if target_feasibility['date_status'] == 'TARGET_INFEASIBLE' else None)
            # P1-04: legacy `status` is a DEPRECATED summary. It keeps the DATE
            # verdict when one exists (backward mode); only when no date check ran
            # (forward mode) does the scenario verdict fill it — so
            # date_feasibility_status (date) and conditional_scenario_status
            # (scenario) never collapse into one contradictory value.
            if target_feasibility['date_status'] is None:
                target_feasibility['status'] = scenario_target_status['status']
            if scenario_target_status['status'] == 'CONDITIONALLY_FEASIBLE':
                # Backward exposure reports a required-start range, forward reports a
                # finish range. The FIX-02/03 warning must read the correct one.
                _rng = (conditional_exposure.get('conditional_finish_range')
                        or conditional_exposure.get('conditional_required_start_range') or {})
                logger.warning("[FIX-02/03] %s — 目标可达性取决于未确认项 %s；"
                               "不在所有情景一致时拒绝输出确定 TARGET_MET。potential finish range: %s … %s",
                               scenario_target_status.get('detail') or 'CONDITIONALLY_FEASIBLE',
                               ', '.join(scenario_target_status['depends_on']),
                               _rng.get('earliest'), _rng.get('latest'))

    # P1-04: three independent target dimensions (source of truth). The legacy
    # `status` key remains only as a deprecated summary; the three fields below
    # are authoritative and never collapse into one contradictory value.
    if target_feasibility is not None:
        from core.target_feasibility import compose_target_dimensions
        target_feasibility.update(compose_target_dimensions(
            date_feasibility_status=target_feasibility.get('date_status'),
            conditional_scenario_status=(scenario_target_status or {}).get('status'),
            executable_as_baseline=bool(target_feasibility.get('executable_as_baseline'))))
        target_feasibility['status_is_deprecated_summary'] = True

    # The target is the move-in (搬迁开始); the move-in support after it may run past the target.
    bench["cpm"] = round(time.perf_counter() - _t_cpm, 3)
    bench["procurement_permit"] = bench["cpm"]
    from core.solver_engine import move_in_date
    _moved = move_in_date(tasks_solved)
    _moved = _moved.isoformat() if _moved else finish_date
    if args.target_date and _moved > args.target_date:
        if args.allow_diagnostic_baseline:
            logger.warning("[P0-02] 诊断基线搬迁开始 %s 超出用户目标日 %s；仅作诊断输出。",
                           _moved, args.target_date)
        else:
            raise SystemExit(f'INFEASIBLE_TARGET: solved move-in {_moved} exceeds {args.target_date}')

    # One calendar-aware metric source for every offline artifact. Proposals
    # remain read-only; synchronization changes presentation metadata only.
    from core.optimizer import analyze_schedule
    from core.schedule_metrics import synchronize_schedule_metrics
    _lean = bool(args.mpp_only)
    opt_result = analyze_schedule(
        tasks_solved, start_date_str, custom_holidays=holidays_pairs,
        verify=not _lean, compliance_check=not _lean, max_suggestions=0 if _lean else 3,
        construction_calendar_mode=cal_mode)
    tasks_solved = synchronize_schedule_metrics(tasks_solved, opt_result)
    annotate_task_contract(tasks_solved)
    n_crit = sum(1 for t in tasks_solved if t.get("critical"))
    logger.info(f"  -> 离线关键路径节点(估算): {n_crit} 个；项目终点: {finish_date}")

    # v3: 合规红线代码化审计（非阻塞；error 级由调用方决定是否阻断）
    logger.info("Step 3.6: 核对报审与交付条件...")
    compliance_issues = _compliance.run_compliance_checks(tasks_solved, holiday_raw=holidays_raw)
    _compliance.log_compliance(compliance_issues)
    n_err = sum(1 for it in compliance_issues if it.get("level") == "error")
    if n_err:
        logger.error(f"[合规审计] 发现 {n_err} 项强制红线违例，拒绝交付失真排程。请复核 WBS 模板后重跑。")
        raise SystemExit(f"COMPLIANCE_BLOCKED: {n_err} error-level issues")

    # P2-17: run_<timestamp>/ 组织 —— 相对 --output 进 run 目录（baseline/reports/audit/input/scenarios）；
    # 绝对路径 --output 保持 legacy 平铺（兼容既有测试与脚本），两种模式都产出 RUN_SUMMARY.json。
    _legacy_layout = os.path.isabs(args.output)
    if _legacy_layout:
        run_id = os.path.splitext(os.path.basename(args.output))[0]
        run_dir = os.path.dirname(args.output) or "."
        audit_dir = run_dir
        reports_dir = run_dir
        output_path = args.output
    else:
        run_id = "run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join(BASE_DIR, "output_mpp", run_id)
        audit_dir = os.path.join(run_dir, "audit")
        reports_dir = os.path.join(run_dir, "reports")
        for sub in ("baseline", "audit", "reports", "input", "scenarios"):
            os.makedirs(os.path.join(run_dir, sub), exist_ok=True)
        output_path = os.path.join(run_dir, "baseline", args.output)
    _stem = os.path.splitext(os.path.basename(output_path))[0]

    def _audit(name: str) -> str:
        return os.path.join(audit_dir, _stem + name)

    os.makedirs(audit_dir, exist_ok=True)
    _json_indent = None if args.mpp_only else 2
    if not args.mpp_only:
        with open(_audit('_master_source.json'), 'w', encoding='utf-8') as f:
            dump_json(master_source, f, ensure_ascii=False, indent=_json_indent)
        with open(_audit('_master_packages.json'), 'w', encoding='utf-8') as f:
            dump_json(master_report, f, ensure_ascii=False, indent=_json_indent)

    # ---- FAST-TRACK SCENARIO SWITCH (V6.6.1, GOV-18) ----
    # 克隆 prepared 图 -> 套 GOV-18 合法变换 -> 重解 -> 写独立 fasttrack_schedule.json。
    # 基线 tasks_solved 全程不被改动；本块任何异常都不影响基线产出。
    ft = None
    if getattr(args, "fasttrack", False) and args.start_date:
        try:
            from core.fasttrack_scenario import run_fasttrack_scenario, write_fasttrack_audit
            ft = run_fasttrack_scenario(copy.deepcopy(tasks), start_date_str, cal_mode,
                                        holidays_pairs, baseline_finish=finish_date)
            ft_path = _audit("_fasttrack_schedule.json")
            write_fasttrack_audit(ft, ft_path)
            if ft.get("status") == "OK":
                logger.info("[FASTTRACK] 基线 %s -> 快轨 %s（节省 %s 天；套用边 %d，待确认 %d，阻断 %d；按技术: %s）",
                            ft.get("baseline_finish"), ft.get("fasttrack_finish"), ft.get("saving_days"),
                            ft.get("applied_edges"), ft.get("needs_confirmation"), ft.get("blocked"),
                            ft.get("applied_by_technique"))
            else:
                logger.warning("[FASTTRACK] 快轨情景未产出（%s）：%s", ft.get("status"), ft.get("error"))
        except Exception as exc:  # 快轨失败绝不拖垮基线
            logger.error("[FASTTRACK] 快轨情景计算异常（不影响基线）：%s", exc)

    # ---- V6.5.0 SENIOR PM INTELLIGENCE & CLIENT PRESENTATION LAYER (additive, opt-in) ----
    # Runs strictly AFTER the frozen V6.4.4.1 calculation. It challenges / explains /
    # recommends / flags / proposes and never shortens, overwrites, deletes, relaxes a
    # dependency, changes productivity or changes crew. Client-facing task names are
    # sanitised for the MPP; the internal name is never rewritten.
    if getattr(args, "senior_pm_review", False):
        from core import senior_pm_review as _spr
        from core import client_presentation as _cp
        _pres = _cp.apply_client_presentation(tasks_solved)
        _ctx = _spr.build_context(args.area, existing_condition=args.site_condition,
                                  delivery=delivery_val, complexity="normal", location=args.city)
        # V6.6.0.2 P0-01: hand the Senior PM layer the ALREADY-EXISTING canonical spatial
        # allocation, so floor area comes from the spatial engine rather than from an
        # activity-row aggregation. No new spatial computation is performed here.
        _spatial_for_pm = None
        if spatial_plan:
            _spatial_for_pm = dict(
                version="6.6.0.2",
                floors=[dict(floor_id=f.get("floor_id"), area_sqm=f.get("area_sqm"))
                        for f in (spatial_plan.get("floors") or [])],
                zones=[dict(zone_id=z.get("zone_id"), floor_id=z.get("floor_id"),
                            area_sqm=z.get("area_sqm"))
                       for z in (spatial_plan.get("zones") or [])],
            )
        _rv = _spr.build_senior_pm_review(tasks_solved,
                                          {"start": start_date_str, "finish_date": finish_date},
                                          opt_result, _ctx, mode="NORMAL",
                                          logistics=logistics, floor_specs=floor_specs,
                                          spatial_hierarchy=_spatial_for_pm)
        _rv["client_presentation"] = dict(
            audit=_cp.audit_task_names(tasks_solved),
            renamed=_pres["renamed"],
            metadata_destination=_pres["metadata_destination"])
        with open(_audit("_senior_pm_review.json"), "w", encoding="utf-8") as f:
            dump_json(_rv, f, ensure_ascii=False, indent=2)
        with open(_audit("_programme_sanity_check.json"), "w", encoding="utf-8") as f:
            dump_json(_rv["programme_sanity_check"], f, ensure_ascii=False, indent=2)
        with open(_audit("_pm_optimisation_register.json"), "w", encoding="utf-8") as f:
            dump_json(_rv["optimisation_register"], f, ensure_ascii=False, indent=2)
        with open(os.path.join(reports_dir, _stem + "_SENIOR_PM_REVIEW.md"), "w",
                  encoding="utf-8") as f:
            f.write(_spr.render_markdown(_rv))
        _con = _rv["construction_outlier_review"]
        _mob = _rv["mobilisation_review"]
        logger.warning("[V6.5.0] SENIOR PM REVIEW — worst status %s; construction %s wd (%s); "
                       "mobilisation %s wd (%s); sectional opportunities %d; optimisation entries %d; "
                       "MPP task names sanitised (%d renamed, audit pass=%s). Baseline unchanged.",
                       _rv["programme_sanity_check"]["worst_status"],
                       _con.get("calculated"), _con.get("status"),
                       _mob.get("calculated"), _mob.get("status"),
                       _rv["sectional_flow_review"]["opportunity_count"],
                       _rv["optimisation_register"]["count"],
                       len(_pres["renamed"]), _cp.audit_task_names(tasks_solved)["pass_"])

    proj_start_dt = datetime.datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else datetime.date.today()

    # 工程量引擎可解释字段落盘（仅当引擎运行过）：<output>_quantities.json
    if derived_q is not None:
        q_out = _audit("_quantities.json")
        os.makedirs(os.path.dirname(q_out), exist_ok=True)
        with open(q_out, "w", encoding="utf-8") as f:
            dump_json({"project_name": args.project_name, "city": args.city, "area_sqm": args.area,
                       "fitout_scope": scope_resolved,
                       "quantity_engine": derived_q}, f, ensure_ascii=False, indent=2)
        logger.info(f"  -> [quantity] 基准工程量已落盘: {q_out}")

    # 生产率可解释字段落盘（仅当存在公式路径节点）：<output>_productivity.json
    prod_rows = productivity_summary(tasks_solved)
    if prod_rows:
        prod_out = _audit("_productivity.json")
        os.makedirs(os.path.dirname(prod_out), exist_ok=True)
        with open(prod_out, "w", encoding="utf-8") as f:
            dump_json({"project_name": args.project_name, "area_sqm": args.area,
                       "start_date": start_date_str, "finish_date": finish_date,
                       "rate_level": args.rate_level or "default",
                       "rates_version": prod_rows[0].get("rates_version"),
                       "tasks": prod_rows}, f, ensure_ascii=False, indent=2)
        for r in prod_rows:
            logger.info(
                f"  -> [productivity][explain][diagnostic] #{r['id']} {r['name']}: not client occupancy. {r['activity_type']} quantity={r['quantity']:g}{r['unit']} "
                f"({r['quantity_source']}), rate={r['productivity_rate']:g} {r['productivity_unit']} [{r['rate_level']}], crew={r['crew_size']}, "
                f"factors={r['factor_labels']} (×{r['factor_product']:g}), calculated={r['calculated_duration']:g}d, "
                f"final={r['final_duration']}d (template {r['template_duration']}d; by rate level {r['duration_by_rate_level']}), "
                f"confidence={r['confidence']}, {r['start']}→{r['finish']}, critical={r['critical']}"
            )
        logger.info(f"  -> [productivity] 可解释字段已落盘: {prod_out}")

    from core.l2_master_duration import l2_summary_rows
    l2_rows = l2_summary_rows(tasks_solved)
    if l2_rows:
        l2_out = _audit("_l2_duration.json")
        os.makedirs(os.path.dirname(l2_out), exist_ok=True)
        with open(l2_out, "w", encoding="utf-8") as f:
            dump_json({"project_name": args.project_name, "area_sqm": args.area,
                       "floors": floors_n, "freight_lifts": args.freight_lifts,
                       "review": l2_review, "tasks": l2_rows}, f, ensure_ascii=False, indent=2)
        logger.info("  -> [L2] master occupancy windows written: %s", l2_out)

    if not args.mpp_only:
        from core.programme_intelligence import build_programme_judgement
        judgement = build_programme_judgement(tasks_solved, area_sqm=float(args.area))
        judge_out = _audit("_programme_judgement.json")
        os.makedirs(os.path.dirname(judge_out), exist_ok=True)
        with open(judge_out, "w", encoding="utf-8") as f:
            dump_json(judgement, f, ensure_ascii=False, indent=2)
        logger.info("  -> [intelligence] pipeline=%s research=%s lanes=%s → %s",
                    " > ".join(judgement.get("pipeline") or []),
                    judgement.get("research_status"),
                    judgement.get("lane_counts"), judge_out)

    dep_out = _audit('_dependencies.json')
    with open(dep_out, 'w', encoding='utf-8') as f:
        dump_json(dep_report, f, ensure_ascii=False, indent=2)
    from core.schedule_quality import schedule_quality
    quality = schedule_quality(tasks_solved, opt_result)
    if authority.get('needs_confirmation'):
        quality.setdefault('warnings', []).append(
            'AUTHORITY UNKNOWN: %s — fire/landlord/completion applicability not confirmed; '
            'construction-permit exemption does not remove them.'
            % ', '.join(authority['unknown_requirements']))
    if authority.get('conflicts'):
        quality.setdefault('warnings', []).append(
            'AUTHORITY CONFLICTS resolved to NEEDS_CONFIRMATION: %s' % authority['conflicts'])
    if args.cost is None:
        quality.setdefault('warnings', []).append(
            'COST_UNKNOWN: no project cost provided; no default value was invented. '
            'Permit exemption follows area-only rules where legally valid (OR rules) '
            'and stays UNKNOWN otherwise. Do not invent a cost to close this gap.')
    if scope_resolved.get('assumed_scope'):
        quality.setdefault('warnings', []).append(
            'ASSUMED_SCOPE: full trade list scheduled from area alone; not a confirmed scope. '
            'assumed included trades: %d; unknowns: %s'
            % (len(active), '; '.join(scope_resolved['unknowns'])))
    if furniture_report.get('applied') and furniture_resolved.get('assumed'):
        quality.setdefault('warnings', []).append(
            'FURNITURE_LEAD_ASSUMED: supply tracking explicitly enabled; profile %s (%dd lead) is provisional; '
            'basis=%s; confidence=%s; prior template reference=%s'
            % (furniture_resolved['profile'], furniture_resolved['lead_days'],
               furniture_resolved['lead_time_basis'], furniture_resolved['confidence'],
               furniture_report['template_lead_days']))
    if procurement_resolved.get('defaulted') and procurement_report.get('applied'):
        quality.setdefault('warnings', []).append(
            'PROCUREMENT_PROFILE_AUTO: %s auto-selected (%s); %d overlaps applied; '
            'protected gates (bid/BAFO/LOA/contract) remain FS'
            % (procurement_resolved['profile'], procurement_resolved['basis'],
               len(procurement_report['applied'])))
    if design_release_report.get('enabled') and design_release_report.get('applied'):
        quality.setdefault('warnings', []).append(
            'DESIGN_RELEASE: D&B fast-track re-links applied (%s); statutory permit days '
            'are not compressed; DBB remains serial through drawing review'
            % '; '.join(a['task']['name'] for a in design_release_report['applied']))
    if conditional_exposure is not None:
        # Backward exposure reports a required-start range, forward reports a
        # finish range. Read the correct one for the quality warning.
        _rng = (conditional_exposure.get('conditional_finish_range')
                or conditional_exposure.get('conditional_required_start_range') or {})
        _range_kind = ('finish' if 'conditional_finish_range' in conditional_exposure
                       else 'required start')
        quality.setdefault('warnings', []).append(
            'CONDITIONAL EXPOSURE: committed finish %s; re-solved potential %s range '
            '%s … %s across %d scenarios. Conditional durations are NOT summed. '
            'Unresolved drivers: %s'
            % (finish_date, _range_kind, _rng.get('earliest'), _rng.get('latest'),
               len(conditional_exposure['scenarios']),
               ', '.join(conditional_registry['unresolved_driver_ids'])))
    if conditional_registry['conditional_tasks']:
        quality.setdefault('warnings', []).append(
            'UNRESOLVED ACTIVITIES (audit JSON only; hidden from client Gantt): %s'
            % '; '.join('%s#%s (%sd → UNRESOLVED)' % (c['name'], c['task_id'],
                                                      c['template_duration_days'])
                        for c in conditional_registry['conditional_tasks']))
    if not activity_cal_report['no_global_7d_propagation']:
        quality.setdefault('warnings', []).append(
            'ACTIVITY CALENDAR: non-site classes on an extended working week: %s'
            % ', '.join(activity_cal_report['non_site_classes_on_extended_week']))
    if target_feasibility:
        if target_feasibility.get('date_status', target_feasibility['status']) == 'TARGET_INFEASIBLE':
            quality.setdefault('warnings', []).append(
                'TARGET_INFEASIBLE: required start %s precedes planning reference %s (gap %d days). '
                'This schedule is a %s.' % (target_feasibility['required_start_date'],
                                            target_feasibility['planning_reference_date'],
                                            target_feasibility['start_gap_days'],
                                            'DIAGNOSTIC RECOVERY BASELINE - NOT EXECUTABLE AS BASELINE'))
        elif target_feasibility['status'] == 'TARGET_INFEASIBLE':
            quality.setdefault('warnings', []).append(
                'TARGET_INFEASIBLE (scenario-based): the finish date misses the target in ALL '
                '%d modelled UNKNOWN states; the committed forecast excludes unresolved '
                'requirements and must not be read as delivering the target.'
                % (scenario_target_status or {}).get('scenario_count', 0))
        elif target_feasibility['status'] == 'CONDITIONALLY_FEASIBLE':
            quality.setdefault('warnings', []).append(
                'CONDITIONALLY_FEASIBLE: date-feasible but unconfirmed inputs are load-bearing: '
                + ', '.join(target_feasibility['unconfirmed_inputs']))
    from core.procurement_review import procurement_review
    procurement = procurement_review(tasks_solved)
    with open(_audit('_procurement_review.json'),'w',encoding='utf-8') as f:
        dump_json(procurement,f,ensure_ascii=False,indent=2)
    with open(_audit('_authority_decision.json'), 'w', encoding='utf-8') as f:
        dump_json(authority, f, ensure_ascii=False, indent=2)
    logger.warning('招采实际日期=%s；制度适用、内部审批及供应商交期尚未验证，不能仅凭邀请方式确认合规。',procurement['actual_phase'])
    with open(_audit('_quality.json'), 'w', encoding='utf-8') as f:
        dump_json(quality, f, ensure_ascii=False, indent=2)

    # ---- V6.6.0 spatial hierarchy + vertical logistics governance ----
    with open(_audit('_logistics_governance.json'), 'w', encoding='utf-8') as f:
        dump_json(logistics, f, ensure_ascii=False, indent=2)
    if spatial_plan:
        _floors = spatial_plan.get('floors') or []
        _hier = dict(
            version='6.6.0',
            hierarchy='Project -> Floor -> Zone -> Workfront',
            project=dict(area_sqm=args.area, floors=floors_n,
                         zones=len(spatial_plan['zones'])),
            floors=[dict(floor_id=f['floor_id'], area_sqm=f['area_sqm'],
                         zone_ids=f.get('zone_ids') or []) for f in _floors],
            zone_count=len(spatial_plan['zones']),
            zones=[dict(zone_id=z['zone_id'], floor_id=z['floor_id'],
                        label=z['label'], area_sqm=z['area_sqm'])
                   for z in spatial_plan['zones']],
            generation=spatial_plan.get('generation'),
            logistics_governance=logistics,
        )
        with open(_audit('_spatial_hierarchy.json'), 'w', encoding='utf-8') as f:
            dump_json(_hier, f, ensure_ascii=False, indent=2)
        _spatial_hier = _hier
    else:
        _hier = None
    spatial_hierarchy = _hier
    for warning in quality['warnings']:
        logger.warning('[schedule quality] %s', warning)
    opt_out = _audit('_optimizer.json')
    if not args.mpp_only:
        with open(opt_out, 'w', encoding='utf-8') as f:
            dump_json(opt_result, f, ensure_ascii=False, indent=2)
    from core.release_controls import release_report
    release_controls = release_report(tasks, tasks_solved, intake_facts, start_date_str, holidays_pairs, cal_mode)
    from core.authority_reference import authority_review
    authority_draft_review = None if args.mpp_only else authority_review(
        tasks, tasks_solved, start_date_str, holidays_pairs, cal_mode)
    from core.master_display import arrange_master
    tasks_solved = arrange_master(tasks_solved)
    tasks_solved[0]['input_notes'] = str(tasks_solved[0].get('input_notes','')) + '\n排程条件与来源：同目录 '+os.path.splitext(os.path.basename(args.output))[0]+'_本次排程条件.docx。施工工作包展开查看实际前置。'
    from core.client_visibility import apply_client_visibility
    visibility = apply_client_visibility(tasks_solved, site_condition=args.site_condition)
    # V3.5: 工艺包署名未确认 → 合并进待确认清单（汇总 1 条，不淹没其他决策）
    _signoff_decisions = signoff_open_decisions(derivation_report)
    if _signoff_decisions:
        visibility['open_decisions'] = _signoff_decisions + list(visibility.get('open_decisions') or [])
    logger.info("  -> [client Gantt] visible=%s hidden=%s — dated work only; unconfirmed scope is a question",
                visibility['visible'], visibility['hidden'])
    # V3.5: 来源与署名审计落盘
    _deriv_out = _audit('_derivation_signoff.json')
    if not args.mpp_only:
        with open(_deriv_out, 'w', encoding='utf-8') as f:
            dump_json(derivation_report, f, ensure_ascii=False, indent=2)
        logger.info('  -> [derivation] 审计写入 %s（13 个工艺包署名状态）', os.path.basename(_deriv_out))
    for _dec in visibility.get('open_decisions') or []:
        logger.warning("  -> [待确认] %s | %s", _dec.get('id'), _dec.get('ask'))

    # Authoritative offline artifact; exports consume exactly this baseline.
    from core.milestones import snapshot as milestone_snapshot
    _ms = milestone_snapshot(tasks_solved)
    _deficit = None
    if args.target_date and _moved and _moved > args.target_date:
        _gap = (datetime.date.fromisoformat(_moved) - datetime.date.fromisoformat(args.target_date)).days
        _deficit = dict(days=_gap, target=args.target_date, calculated_move_in=_moved,
                        banner=f"[SCHEDULE DEFICIT / 负时差警告: -{_gap} Days]")
        logger.warning(_deficit['banner'] + ' 法定招标/图审/许可窗口未压缩。')
    schedule_payload = dict(engine='fitout', project_name=args.project_name,
                            start_date=start_date_str, finish_date=finish_date, tasks=tasks_solved,
                            project_attributes=dict(
                                project_name=args.project_name, city=args.city, gfa_sqm=args.area,
                                sector=args.sector, delivery=delivery_val, bidding=bidding_val,
                                cleanroom_class=intake_facts.get('cleanroom_class') or args.cleanroom_class,
                                nc_rating=intake_facts.get('nc_rating') or args.nc_rating,
                                esd_level=intake_facts.get('esd_level') or args.esd_level,
                                power_kva=intake_facts.get('power_kva') or args.power_kva,
                                mocap_camera_count=intake_facts.get('mocap_camera_count') or args.mocap_camera_count,
                            ),
                            control_dates=_ms,
                            schedule_deficit=_deficit,
                            duration_derivation=duration_audit,
                            concealed_works=concealed_report,
                            delivery_model=delivery_report,
                            fire_sequence=fire_report,
                            addon_practices=addon_report)
    _lead_warn = ((addon_report or {}).get("leads") or {}).get("warnings") or []
    if _lead_warn:
        schedule_payload["equipment_lead_warnings"] = [
            dict(field=w.get("field"), message="交货期未填，不完整") for w in _lead_warn
        ]
    schedule_payload['calendar_basis'] = calendar_basis_block(calendar_resolved)
    schedule_payload['calendar_basis']['holiday_basis'] = '2025/2026依据国务院通知；5D按委托要求跳过全部周末（含调休补班日）；2027及以后为规划占位，不完整（依据待补），非已发布法定日历。'
    schedule_payload['segmented_approvals'] = dict(review=False,permit=False,note='默认一次图审、一次发证；分段选项需项目所在地主管部门确认。')
    schedule_payload['activity_calendar'] = activity_calendar_block(activity_cal_report)
    schedule_payload['conditional_requirements'] = conditional_registry
    if conditional_exposure is not None:
        schedule_payload['conditional_exposure'] = conditional_exposure_block(conditional_exposure)
    schedule_payload['programme_certainty'] = programme_certainty_block(
        finish_date, args.target_date,
        (scenario_target_status or {}).get('status') or (target_feasibility or {}).get('status'),
        conditional_registry, conditional_exposure)
    schedule_payload['client_visibility'] = visibility
    schedule_payload['master_schedule'] = master_report
    from core.master_boundaries import boundary_review
    boundary_report = boundary_review(tasks_solved)
    schedule_payload['boundary_review'] = boundary_report
    if not args.mpp_only:
        with open(_audit('_boundary_review.json'), 'w', encoding='utf-8') as f:
            dump_json(boundary_report, f, ensure_ascii=False, indent=2)
    for finding in boundary_report['findings']:
        logger.warning('[boundary review] %s: %s', finding['code'], finding['observation'])
    _asks = [d.get('ask') for d in visibility.get('open_decisions') or [] if d.get('ask')]
    _rd = list(schedule_payload['programme_certainty'].get('required_decisions') or [])
    for _ask in reversed(_asks):
        if _ask not in _rd:
            _rd.insert(0, _ask)
    schedule_payload['programme_certainty']['required_decisions'] = _rd
    schedule_payload['programme_certainty']['open_decisions'] = visibility.get('open_decisions') or []
    from core.module_catalog import resolve_modules
    schedule_payload['module_plan'] = next((t['module_plan'] for t in tasks_solved if t.get('module_plan')), resolve_modules(dict(intake_facts, sector=args.sector, delivery=delivery_val, bidding=bidding_val)))
    schedule_payload['procurement_profile'] = procurement_profile_block(procurement_resolved, procurement_report)
    schedule_payload['design_release'] = design_release_block(design_release_report)
    schedule_payload['furniture_lead'] = furniture_lead_block(furniture_resolved, furniture_report)
    schedule_payload['occupancy_readiness'] = occupancy_profile_block(occupancy_resolved, occupancy_report)
    schedule_payload['authority_boundary_review'] = authority_boundary_report
    schedule_payload['release_controls']=release_controls
    if authority_draft_review:
        schedule_payload['authority_draft_review']=authority_draft_review
        with open(_audit('_报审组成与影响.json'), 'w', encoding='utf-8') as f:
            dump_json(authority_draft_review, f, ensure_ascii=False, indent=2)
    if not args.mpp_only:
        with open(_audit('_release_scenarios.json'), 'w', encoding='utf-8') as f:
            dump_json(release_controls,f,ensure_ascii=False,indent=2)
    release_lines = release_controls['summary_lines'] + [w['message'] for w in release_controls['warnings']]
    if release_controls.get('control_chain_explanation'):
        release_lines.append('控制链说明：'+release_controls['control_chain_explanation'])
    sensitivity = release_controls.get('sensitivity_window')
    if sensitivity:
        release_lines.append(f"计划敏感度：固定其他条件时，不推迟主计划搬迁的抽查窗口为 {sensitivity['max_inspection_workdays_without_move_delay']} 个检查工作日；对应检查完成日 {sensitivity['latest_inspection_finish']}。" + sensitivity['basis'])
    if release_controls.get('inspection_source'):
        source = release_controls['inspection_source']
        release_lines.append(f"窗口依据：{source['document']}，{source['articles']}；{source['url']}。检查窗口不包含未声明的通知等待、整改，也不保证通过。")
    if not args.mpp_only:
        with open(_audit('_交付条件与情境.md'), 'w', encoding='utf-8') as f:
            f.write('\n\n'.join(release_lines)+'\n')
    for line in release_lines: logger.warning('[交付条件] %s', line)
    schedule_payload['process_closure']=next((t['process_closure'] for t in tasks_solved if t.get('process_closure')), {})
    if not args.mpp_only:
        with open(_audit('_authority_boundary_review.json'), 'w', encoding='utf-8') as f:
            dump_json(authority_boundary_report, f, ensure_ascii=False, indent=2)
    # V6.6.0: spatial hierarchy + vertical logistics governance travel with every schedule payload
    schedule_payload['logistics_governance'] = logistics
    if spatial_hierarchy is not None:
        schedule_payload['spatial_hierarchy'] = spatial_hierarchy
    # P1-08: 阶段语义拆分 —— 实体施工只算 takeover→物理竣工；家具/IAQ/验收/移交各自成段
    from core.phase_semantics import phase_semantics_block
    phase_sem = phase_semantics_block(tasks_solved)
    schedule_payload['phase_semantics'] = phase_sem
    logger.info("  -> [P1-08] 实体施工真实跨期: %s → %s (%d 天) —— 不含家具/IAQ散味/检测验收/搬迁尾段；"
                "五段 spans: 施工=%s天 家具=%s天 检测验收=%s天 IAQ散味=%s天 移交搬迁=%s天",
                phase_sem['site_construction']['start'], phase_sem['site_construction']['finish'],
                phase_sem['site_construction']['calendar_days'],
                *(str((phase_sem['spans'].get(cat) or {}).get('calendar_days')) for cat in
                  ('SITE_CONSTRUCTION', 'FURNITURE_INSTALLATION', 'TESTING_COMMISSIONING',
                   'IAQ_FLUSHOUT', 'HANDOVER_MOVE')))
    _env = (l2_review or {}).get('envelope') or {}
    logger.info("  -> [L2] 包络基准节拍 %s 天（带 %s–%s，%s）。重叠现场跨度 %s 天。"
                "基准节拍不是现场横道相加。",
                _env.get('implied_site_days'), _env.get('band_low'), _env.get('band_high'),
                _env.get('status'), phase_sem['site_construction']['calendar_days'])
    if target_semantics is not None:
        schedule_payload['target_semantics'] = target_semantics
    if target_feasibility is not None:
        schedule_payload['target_feasibility'] = target_feasibility
        if target_feasibility.get('diagnostic_label'):
            schedule_payload['baseline_status'] = target_feasibility['diagnostic_label']
    schedule_json_path = _audit('_schedule.json')
    from core.programme_delivery import execution_readiness, disclosure_lines
    readiness = execution_readiness(input_contract, authority, target_feasibility, quality)
    forward_target_comparison = None
    if args.start_date and args.target_date:
        from core.solver_engine import move_in_date
        moved = move_in_date(tasks_solved)
        variance = ((moved or datetime.date.fromisoformat(finish_date)) - datetime.date.fromisoformat(args.target_date)).days
        forward_target_comparison = dict(
            status='TARGET_MISSED' if variance > 0 else 'DATE_WITHIN_TARGET',
            kickoff_date=args.start_date, user_target_date=args.target_date,
            calculated_finish_date=finish_date, variance_calendar_days=variance,
            basis='forward CPM under current scope and planning assumptions; not actual completion',
            target_date_modified=False, dates_compressed=False,
            diagnostic_only=variance > 0)
        schedule_payload['forward_target_comparison'] = forward_target_comparison
        if variance > 0:
            schedule_payload['baseline_status'] = 'TARGET_MISSED_DIAGNOSTIC'
            readiness['status'] = 'NOT_EXECUTABLE_FOR_REQUESTED_TARGET'
            readiness['pending_conditions'].append(dict(field='target_date', reason=f'Forecast exceeds the unchanged target by {variance} calendar days.'))
    from core.target_decision import target_decision, add_compression_result
    decision = target_decision(tasks_solved, args.target_date, finish_date, intake_facts.get('target_response', 'ask'))
    if decision:
        add_compression_result(decision, ft)
        schedule_payload['target_decision'] = decision
        with open(_audit('_目标日期决策.json'), 'w', encoding='utf-8') as f:
            dump_json(decision, f, ensure_ascii=False, indent=2)
        with open(_audit('_交付条件与情境.md'), 'a', encoding='utf-8') as f:
            f.write('\n'+'\n\n'.join(decision['summary_lines'])+'\n')
        # Client-facing MPP/PDF notes carry the fact only; the choice is asked of the
        # skill user through the run output and the delivery note.
        release_lines.append(decision['summary_lines'][0] + ('处理方式待PM确认。' if decision['status'] == 'TARGET_MISSED' else ''))
        for line in decision['summary_lines']: logger.warning('[目标日期] %s', line)
    schedule_payload['execution_readiness'] = readiness
    if input_contract and tasks_solved:
        tasks_solved[0]['input_notes'] = '\n'.join(disclosure_lines(input_contract)) + '\n计划草案，具名 PM 签收前不是合同工期。'
        if forward_target_comparison and forward_target_comparison['diagnostic_only']:
            comparison = forward_target_comparison
            tasks_solved[0]['input_notes'] += f"\n目标未达：目标搬迁 {args.target_date}，按正常工序晚 {comparison['variance_calendar_days']} 个日历天。"
    if tasks_solved:
        tasks_solved[0]['input_notes'] = str(tasks_solved[0].get('input_notes',''))+'\n'+'\n'.join(release_lines)
    if release_controls['status']=='PROVISIONAL_DUAL_SCENARIOS' or release_controls['warnings']:
        readiness['pending_conditions'].append(dict(field='release_controls',reason='放行政策或外部供货待确认，详见交付条件与情境摘要。'))
    from core.draft_presentation import draft_notice, PREFIX
    local_notice = draft_notice(next((t.get('local_authority') for t in tasks_solved if t.get('local_authority')), {}))
    if input_contract and input_contract.get('project',{}).get('authority_draft'):
        from core.draft_presentation import NOTICE
        local_notice = NOTICE
    if local_notice and tasks_solved:
        tasks_solved[0]['input_notes'] = local_notice + '\n' + tasks_solved[0].get('input_notes', '')
        tasks_solved[0]['client_task_name'] = PREFIX + tasks_solved[0].get('client_task_name', args.project_name)
    if input_contract:
        schedule_payload['input_contract'] = input_contract
        if not args.mpp_only:
            with open(_audit('_input_contract.json'), 'w', encoding='utf-8') as f:
                dump_json(input_contract, f, ensure_ascii=False, indent=2)

    if not args.mpp_only:
        with open(schedule_json_path, 'w', encoding='utf-8') as f:
            dump_json(schedule_payload, f, ensure_ascii=False, indent=2)
    # Advisory phase attribution only; it does not rewire the project network.
    from core.phase_rollup_audit import audit as audit_phase_rollups
    if args.mpp_only:
        phase_audit = dict(leaf_count=0, issues=[])
    else:
        phase_audit = audit_phase_rollups(tasks_solved)
        with open(_audit('_phase_allocation.json'), 'w', encoding='utf-8') as f:
            dump_json(phase_audit, f, ensure_ascii=False, indent=2)
        logger.info('[阶段归属] %s leaves; %s review items', phase_audit['leaf_count'], len(phase_audit['issues']))
    # Structural review is deliberately narrower than engineering sign-off.
    from tools.pm_health_check import audit as audit_schedule
    if args.mpp_only:
        health = dict(status='LEAN', task_count=len(tasks_solved), findings=[], target={})
    else:
        health = audit_schedule(schedule_payload)
    health['fasttrack'] = {k: (ft or {}).get(k) for k in ('status','saving_days','applied_edges','blocked')}
    health['supply_chain_status'] = 'NOT_VERIFIED: equipment dates and interface readiness require project evidence'
    health['risk_level'] = 'NOT_ASSESSED'
    if not args.mpp_only:
        health_file = _audit('_health.json')
        with open(health_file, 'w', encoding='utf-8') as f:
            dump_json(health, f, ensure_ascii=False, indent=2)
        target = health.get('target') or {}
        lines = ['# PM 主控计划健康审计卡片', '',
                 '状态：' + health['status'] + '；本卡为结构核对，不代表工程、审批或资源签认。',
                 '任务数：' + str(health['task_count']),
                 '结构检查发现：' + str(len(health['findings'])) + ' 项（详情见审计数据）。',
                 '开始搬迁：' + str(target.get('calculated_move_in') or '未计算'),
                 '全部完成：' + str(target.get('calculated_finish') or schedule_payload.get('finish_date') or '未计算'),
                 '目标差距：' + (str(target.get('variance_calendar_days')) + ' 日历天（按开始搬迁，正数为晚）' if target.get('variance_calendar_days') is not None else '未提供可比较目标'),
                 '注意：目标差距不是总浮动；未完成独立倒排与浮动校验。',
                 '设备到货与基础接口：未核实，不能判断是否冲突。',
                 '快轨：' + str(health['fasttrack']) + '；安全风险等级未评定。',
                 '审批、现场、资源、供应商及项目假设须由责任方核实。']
        card_file = os.path.splitext(output_path)[0] + '_PM健康审计卡片.md'
        with open(card_file, 'w', encoding='utf-8') as f: f.write('\n'.join(lines)+'\n')
        logger.warning('[健康检查] %s；审计卡片：%s', health['status'], card_file)
        if health['status'] == 'ERROR':
            raise SystemExit('HEALTH_GATE_FAILED: 发现结构错误，禁止继续导出；请检查健康审计数据。')
    logger.info('Optimizer proposals re-solved; baseline unchanged: %s', opt_out)

    report_title = f'{args.project_name} - 项目管理主总控进度计划'
    if target_feasibility and target_feasibility.get('date_status', target_feasibility['status']) == 'TARGET_INFEASIBLE':
        # P0-02: 诊断基线必须在每一份交付物（MPP/PDF/PPTX）标题上显式可见，禁止包装成正常排程。
        report_title = args.project_name + '（DIAGNOSTIC RECOVERY BASELINE — 目标不可行，不可作为基线执行）'
    elif target_feasibility and target_feasibility['status'] == 'TARGET_INFEASIBLE':
        # FIX-03 E: 情景驱动的不可行 —— 所有 modelled states 均不达标，同样禁止包装成达标排程。
        report_title = args.project_name + '（TARGET NOT ACHIEVABLE IN ANY MODELLED STATE — 详见 Conditional Exposure）'
    if local_notice:
        report_title = PREFIX + report_title
    mpp_written = False
    if args.no_mpp:
        logger.info("Step 4: [已指定 --no_mpp] 跳过物理 MS Project COM 渲染，直接进入 PDF/PPTX 报表导出...")
    else:
        logger.info("Step 4: 生成 MS Project 文件...")
        try:
            build_mpp(
                project_title=report_title,
                project_start=proj_start_dt,
                tasks=tasks_solved,
                calendar_exceptions=holidays_raw,
                output_mpp_path=output_path,
                construction_calendar_mode=cal_mode,
                export_xml=not args.mpp_only
            )
            mpp_written = os.path.exists(output_path)
        except Exception as mpp_err:
            logger.error(f"  -> [MPP COM 渲染] 失败，本次不会产出 .mpp: {mpp_err}")
            logger.error("  -> 原生输出未通过；请查看 .mpp 日期与重开核对报告。无 Project 时可显式 --no_mpp，但不能把核对失败当作已验证交付。")

    if not args.mpp_only:
        # 自动同步导出 A3 打印级任务明细甘特图报表 (.pdf)
        try:
            from exporters.export_pdf import export_pdf
            pdf_out_path = os.path.join(reports_dir, _stem + ".pdf")
            meta_dict = {"city": args.city, "area": args.area, "cost": ("UNKNOWN" if args.cost is None else args.cost), "delivery": delivery_val, "bidding": bidding_val, "sector": getattr(args, 'sector', 'office'),
                         "project_name": args.project_name,
                         "gfa_sqm": args.area,
                         "cleanroom_class": intake_facts.get("cleanroom_class") or args.cleanroom_class,
                         "nc_rating": intake_facts.get("nc_rating") or args.nc_rating,
                         "esd_level": intake_facts.get("esd_level") or args.esd_level,
                         "power_kva": intake_facts.get("power_kva") or args.power_kva,
                         "schedule_deficit": _deficit,
                         "equipment_lead_warnings": schedule_payload.get("equipment_lead_warnings") or []}
            meta_dict['input_disclosures'] = disclosure_lines(input_contract)
            meta_dict['input_contract'] = input_contract or {}
            meta_dict['local_draft_notice'] = local_notice
            meta_dict['baseline_format'] = 'mpp' if mpp_written else 'schedule_json'
            if forward_target_comparison and forward_target_comparison['diagnostic_only']:
                meta_dict['input_disclosures'].append(f'目标 {args.target_date}；测算搬迁完成 {finish_date}；晚 {forward_target_comparison["variance_calendar_days"]} 个日历天。')
            from core.pm_workpackages import overview
            from exporters.export_pptx import generate_pptx_milestones
            pptx_out_path = os.path.join(reports_dir, _stem + "_Milestones.pptx")
            _certainty = dict(schedule_payload.get('programme_certainty') or {})
            _certainty['input_disclosures'] = disclosure_lines(input_contract)
            if conditional_exposure is not None:
                _certainty['scenario_note'] = (
                    "%d scenarios re-solved; exposure is never summed"
                    % len(conditional_exposure['scenarios']))
            _overview = overview(tasks_solved)
            _t_pdf = time.perf_counter()
            from concurrent.futures import ThreadPoolExecutor
            def _pdf():
                export_pdf(mpp_or_tasks=_overview, out_path=pdf_out_path, format="gantt", title=report_title, meta=meta_dict)
            def _pptx():
                generate_pptx_milestones(
                    project_title=report_title,
                    project_meta=meta_dict,
                    tasks=tasks_solved,
                    output_pptx_path=pptx_out_path,
                    programme_certainty=_certainty or None
                )
            with ThreadPoolExecutor(max_workers=2) as pool:
                f_pdf = pool.submit(_pdf)
                f_pptx = pool.submit(_pptx)
                f_pdf.result()
                f_pptx.result()
            bench["pdf"] = round(time.perf_counter() - _t_pdf, 3)
            logger.info(f"  -> [PDF 甘特图] 同步生成 A3 专业全周期控制甘特图: {pdf_out_path}")
            logger.info(f"  -> [PPTX 演示] 并行生成高层汇报文稿: {pptx_out_path}")
        except Exception as ex:
            logger.warning(f"  -> [PDF/PPTX 报表] 自动导出跳过: {ex}")
    else:
        logger.info('LEAN DELIVERY: PDF/PPTX/XLSX/XML omitted; native MPP audit retained.')
    
    # ------------------------------------------------------------------
    # P2-17: input 快照 + RUN_SUMMARY.json —— 每次运行可判读、可追溯、不强选情景
    # ------------------------------------------------------------------
    import hashlib
    # 'output' is an output LOCATION (often a temp path), not a semantic input —
    # excluded so identical inputs hash identically across runs.
    input_echo = {k: v for k, v in vars(args).items() if k not in ('output', 'project_input', 'intake_excel')}
    input_echo['resolved_profiles'] = dict(
        template=mode_key,
        scope_profile=scope_resolved.get('profile'),
        calendar_profile=calendar_resolved.get('calendar_mode'),
        procurement_profile=procurement_resolved.get('profile'),
        design_release_enabled=design_release_report.get('enabled'),
        furniture_lead_profile=furniture_resolved.get('profile'),
        occupancy_profile=occupancy_resolved.get('profile'),
        site_condition=args.site_condition,
    )
    input_hash = hashlib.sha256(
        json.dumps(input_echo, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')
    ).hexdigest()
    if not _legacy_layout:
        with open(os.path.join(run_dir, "input", "input.json"), "w", encoding="utf-8") as f:
            dump_json(dict(run_id=run_id, input_hash=input_hash, args=input_echo),
                      f, ensure_ascii=False, indent=2, default=str)

    assumptions = []
    if input_contract:
        for item in input_contract['assumptions']:
            assumptions.append('%s: %s — %s (default policy, not a confirmed site fact)' %
                               (item['field'], item['value'], item['basis']))
    if args.cost is None:
        assumptions.append("cost: UNKNOWN — no default value invented (P2-15)")
    if args.site_condition == 'unknown':
        assumptions.append("site_condition: UNKNOWN — 未问清是否拆除；对客甘特不画拆除占位，不臆造天数 (P0-03)")
    if scope_resolved.get('assumed_scope'):
        assumptions.append("scope: ASSUMED_SCOPE — full trade list from area alone (P1-11)")
    if calendar_resolved.get('defaulted_to_7d'):
        assumptions.append("calendar: defaulted 7D with disclosure; 6D recommended (P1-12)")
    if not furniture_report.get('applied'):
        assumptions.append('家具生产交期未加载；是否需外部供货确认见交付条件摘要。')
    elif furniture_resolved.get('assumed'):
        assumptions.append(f"furniture lead: ASSUMED {furniture_resolved['profile']} "
                           f"({furniture_resolved['lead_days']}d) (P1-10)")
    else:
        assumptions.append(
            f"furniture lead: {furniture_resolved.get('profile')} "
            f"{furniture_resolved.get('lead_days')}d (programme policy)"
        )
    if occupancy_resolved.get('status') == 'UNKNOWN':
        assumptions.append("occupancy / IAQ: UNKNOWN — diagnostic only; not converted to days")
    elif occupancy_resolved.get('status') in ('CONFIRMED_POLICY', 'CONFIRMED_SELECTION', 'CONFIRMED'):
        assumptions.append(
            "occupancy / IAQ: 内部规划双测，项目适用性待确认；首次 GB 50325 → 二次 GB/T 18883 "
            f"(profile={occupancy_resolved.get('profile')}; 默认不设独立通风等待，项目显式输入窗口={occupancy_resolved.get('flushout_duration')}d)"
        )
    elif occupancy_resolved.get('confidence') == 'provisional':
        assumptions.append(f"occupancy readiness: declared {occupancy_resolved['profile']} "
                           f"(provisional parameters) (P1-09)")
    for d in conditional_registry['unresolved_driver_ids']:
        if d == 'AUTHORITY_ACCEPTANCE_CHAIN':
            assumptions.append("authority acceptance chain: UNKNOWN — conditional placeholder, "
                               "not committed (FIX-03)")
    if statutory_mode and 'AUTHORITY_ACCEPTANCE_CHAIN' not in conditional_registry['unresolved_driver_ids']:
        assumptions.append("authority: 消防图审 / 消防验收备案 / 第三方检测 / 联合竣工 — 保守规划预留，法律适用性未核实；"
                           "施工许可仍按城市面积/造价独立判定")
    if procurement_resolved.get('defaulted'):
        assumptions.append(f"procurement profile: auto-selected {procurement_resolved['profile']} (P1-06)")

    def _rel(path: str) -> str:
        return os.path.relpath(path, run_dir) if not _legacy_layout else path

    if _legacy_layout:
        # legacy flat: this run's artifacts are exactly the <stem>* files in run_dir
        manifest = sorted(name for name in os.listdir(run_dir) if name.startswith(_stem))
    else:
        manifest = sorted(os.path.relpath(os.path.join(root, name), run_dir).replace("\\", "/")
                          for root, _dirs, files in os.walk(run_dir) for name in files)
    run_summary = dict(
        run_id=run_id,
        project_name=args.project_name,
        execution_readiness=readiness,
        input_contract=input_contract,
        input_hash=input_hash,
        selected_mode=("backward (target_date %s)" % args.target_date
                       if target_semantics is not None and not args.start_date
                       else "forward (start_date %s)" % args.start_date),
        # [P2-17] The summary must always name the main result. When no .mpp was
        # written (--no_mpp or COM unavailable) the authoritative result is the
        # CPM schedule JSON — pointing at nothing would leave the run unreadable.
        baseline_output=(_rel(output_path) if mpp_written else _rel(schedule_json_path)),
        baseline_format=("mpp" if mpp_written else "schedule_json"),
        final_selected_scenario=None,  # P2-17: 无正式 recommendation 时绝不强选
        decision_status="PM_DECISION_REQUIRED",
        client_wording="计划草案，具名 PM 签收前不是合同工期。",
        scenario_outputs=[],
        warnings=quality.get('warnings', []),
        assumptions=assumptions,
        open_decisions=visibility.get('open_decisions') or [],
        client_visibility=dict(visible=visibility.get('visible'),
                               hidden=visibility.get('hidden'),
                               rule=visibility.get('rule')),
        target_status=((forward_target_comparison or {}).get('status') or (target_feasibility or {}).get('status')),
        forward_target_comparison=forward_target_comparison,
        duration_readings=dict(
            reference_beat_days=_env.get('implied_site_days'),
            envelope_band=_env.get('band_id'),
            envelope_low=_env.get('band_low'),
            envelope_high=_env.get('band_high'),
            envelope_status=_env.get('status'),
            overlapped_site_calendar_days=(phase_sem.get('site_construction') or {}).get('calendar_days'),
            overlapped_site_start=(phase_sem.get('site_construction') or {}).get('start'),
            overlapped_site_finish=(phase_sem.get('site_construction') or {}).get('finish'),
            note=("reference_beat_days is the area-scaled 105-day envelope check. "
                  "overlapped_site_calendar_days is the CPM span from site takeover to physical completion. "
                  "They are different measures."),
        ),
        main_report_path=(_rel(output_path) if args.mpp_only and mpp_written else _rel(schedule_json_path)
                          if args.mpp_only else _rel(pdf_out_path) if not _legacy_layout else pdf_out_path),
        delivery_profile='mpp_only' if args.mpp_only else 'full',
        manifest=manifest,
        layout=("run_folder" if not _legacy_layout else "legacy_flat"),
    )
    run_summary['release_controls'] = release_controls
    summary_path = os.path.join(run_dir, "RUN_SUMMARY.json") if not _legacy_layout \
        else os.path.join(run_dir, _stem + "_RUN_SUMMARY.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        dump_json(run_summary, f, ensure_ascii=False, indent=2, default=str)
    logger.info("  -> [P2-17] RUN_SUMMARY 已落盘: %s (input_hash=%s; final_selected_scenario=null/"
                "PM_DECISION_REQUIRED — 未强选情景)", summary_path, input_hash[:12])
    from core.typesafe_judgment import build_review_request
    if summary_path.endswith("_RUN_SUMMARY.json"):
        review_path = summary_path[:-len("_RUN_SUMMARY.json")] + "_TYPESAFE_REVIEW.json"
    else:
        review_path = os.path.join(run_dir, "TYPESAFE_REVIEW.json")
    if not args.mpp_only:
        with open(review_path, "w", encoding="utf-8") as f:
            dump_json(build_review_request(run_summary), f, ensure_ascii=False, indent=2, default=str)
        logger.info("  -> closed-question review written, not applied to dates: %s", review_path)

    bench["total"] = round(time.perf_counter() - _t_run, 3)
    n_tasks = sum(1 for t in tasks_solved if not t.get("summary"))
    n_links = 0
    from core._common import parse_predecessor as _pp
    for t in tasks_solved:
        n_links += len(_pp(str(t.get("predecessors") or "")))
    cpm_s = bench.get("cpm") if bench.get("cpm") is not None else 0.0
    perm_s = bench.get("procurement_permit") if bench.get("procurement_permit") is not None else 0.0
    pdf_s = bench.get("pdf") if bench.get("pdf") is not None else 0.0
    diff_s = bench.get("diff") if bench.get("diff") is not None else 0.0
    total_s = bench["total"]
    verdict = "PASS: Sub-second" if total_s < 1.0 else ("PASS: <1.5s" if total_s < 1.5 else "MEASURED: above 1.5s target (outputs not reduced)")
    bench_block = (
        "==============================================================\n"
        "[BENCHMARK] PMP Scheduler Engine v4.0.0\n"
        "--------------------------------------------------------------\n"
        f"- CPM Schedule Network Solved:     {cpm_s:.3f}s ({n_tasks} tasks, {n_links} links)\n"
        f"- Procurement & Permit Logic:      {perm_s:.3f}s\n"
        f"- Vector PDF Gantt Generated:      {pdf_s:.3f}s\n"
        f"- Intake Snapshot Diff Computed:   {diff_s:.3f}s\n"
        f"- TOTAL RUNTIME:                   {total_s:.3f}s ({verdict})\n"
        "=============================================================="
    )
    print(bench_block, file=sys.stderr)
    bench_payload = dict(engine="PMP Scheduler Engine v4.0.0", cpm_seconds=cpm_s,
                         procurement_permit_seconds=perm_s, pdf_seconds=pdf_s,
                         intake_diff_seconds=diff_s, total_seconds=total_s,
                         tasks=n_tasks, links=n_links, verdict=verdict)
    with open(_audit("_benchmark.json"), "w", encoding="utf-8") as f:
        dump_json(bench_payload, f, ensure_ascii=False, indent=2)
    with open(_audit("_duration_derivation.json"), "w", encoding="utf-8") as f:
        dump_json(duration_audit, f, ensure_ascii=False, indent=2)
    schedule_payload["benchmark"] = bench_payload
    with open(schedule_json_path, "w", encoding="utf-8") as f:
        dump_json(schedule_payload, f, ensure_ascii=False, indent=None if args.mpp_only else 2)
    logger.info(f"==================================================")
    if mpp_written:
        logger.info(f"SUCCESS: MPP 已保存并通过逐项日期核验；生产率仍需现场校准: {output_path}")
    elif not args.no_mpp:
        if os.path.exists(output_path):
            logger.error('PARTIAL DELIVERY: .mpp 文件存在但验证未通过；不可作为已验证 MPP 交付。')
        else:
            logger.error('PARTIAL DELIVERY: 未生成 .mpp；保留离线草案，不能交付为已验证 MPP。')
    else:
        # SKILL.md: never claim an .mpp was written when it was not (--no_mpp or COM unavailable)
        logger.info(f"SUCCESS: 调度完成！CPM 解算完成；审批适用性与项目条件仍须核实；未生成 .mpp（--no_mpp 或无 MS Project COM），交付物见 {reports_dir} 下的 .pdf/.pptx")

    if args.target_date and not args.allow_diagnostic_baseline:
        date_st = (target_feasibility or {}).get('date_status') or (target_feasibility or {}).get('status')
        if date_st == 'TARGET_INFEASIBLE':
            raise SystemExit(
                f"TARGET_INFEASIBLE: latest feasible start {start_date_str} vs planning "
                f"reference {(target_feasibility or {}).get('planning_reference_date')}"
            )

if __name__ == "__main__":
    main()
