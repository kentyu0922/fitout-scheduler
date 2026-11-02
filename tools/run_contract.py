"""Single public entry for prompt facts, JSON and XLSX."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import datetime
import uuid
import time
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.project_input import load_input, normalize_project_inputs, engine_arguments
from core.module_catalog import CATALOG

def parser_for_contract():
    p = argparse.ArgumentParser(description="Unified L2 project intake and programme entry")
    p.add_argument("--input", "--intake", dest="input")
    p.add_argument("--contracting_strategy")
    p.add_argument("--procurement_method")
    p.add_argument("--reference", action="append", default=[])
    p.add_argument("--base", help="上次输出文件夹：沿用上次全部条件，只改本次明确提出的项")
    for key in ("city", "area", "sector", "delivery", "bidding", "start_date", "target_date",
                "project_name", "site_condition", "space_use", "industry_work_profile", "calendar_profile", "authority_profile",
                "workfronts", "zoning_mode", "sectional", "building_restriction", "demolition_fraction", "cost",
                "cleanroom_class", "nc_rating", "esd_level", "power_kva", "mocap_camera_count", "mocap_stage_area_sqm",
                "statutory_tender_required", "power_supply_type", "eia_document_type", "fume_hood_count"):
        p.add_argument("--" + key)
    from core.equipment_leads import FIELDS as _LEAD_FIELDS
    for _lf in sorted(_LEAD_FIELDS):
        p.add_argument("--" + _lf)
    from core.local_authority import FIELDS as AUTHORITY_FIELDS
    for key in (*AUTHORITY_FIELDS,'authority_basis','authority_rule_date','server_room_early_order_basis','server_room_area_sqm','acoustic_room_area_sqm','specialist_area_sqm','furniture_supply_state','furniture_supply_basis','furniture_remaining_days','furniture_lead_profile','furniture_in_scope','furniture_procurement','furniture_tracking','fire_release_policy','fire_release_basis','fire_inspection_days','fire_rectification_days','fire_recheck_days','fire_duration_basis','flushout_days','occupancy_basis','authority_review_days','authority_permit_days','authority_fire_days','authority_days_basis','authority_review_buffer_days','authority_permit_buffer_days','authority_fire_buffer_days','authority_buffer_reason','permit_waived'):
        p.add_argument('--'+key)
    for key in ['province','district','authority_size_class','authority_local_route']:
        p.add_argument('--'+key)
    from core.furniture_installation import NUMBER_FIELDS, TEXT_FIELDS
    for key in sorted(NUMBER_FIELDS | TEXT_FIELDS):
        p.add_argument('--'+key)
    p.add_argument('--authority_draft', action='store_true', default=None)
    for i in CATALOG:
        p.add_argument("--addon_"+i.lower())
    p.add_argument("--addon_server_room", dest="addon_a04")
    p.add_argument("--procurement_policy", choices=["unspecified","enterprise_3round_tech_commercial"])
    p.add_argument("--procurement_regime", choices=["unknown","formal_tender","enterprise_selection"])
    p.add_argument("--target_response", choices=["ask","keep","assess"])
    p.add_argument("--mode", choices=["plan", "review", "scenario"])
    p.add_argument('--delivery_profile', help='精简/完整 (lean/full); same field as client intake')
    p.add_argument("--fasttrack", action="store_true", default=None)
    exports = p.add_mutually_exclusive_group()
    exports.add_argument("--mpp", action="store_true")
    exports.add_argument("--no_mpp", "--no-mpp", dest="no_mpp", action="store_true")
    p.add_argument("--dry_run", action="store_true")
    p.add_argument("--output", default=None)
    p.add_argument("--allow_diagnostic_baseline", action="store_true")
    p.add_argument("--lean", action="store_true", help="Compatibility preference; PDF suite remains mandatory, --mpp is explicit opt-in")
    return p

PDF_CHOICE = '改用PDF与Timeline（不需要Microsoft Project）'
# Project can stall on a dialog (activation, sign-in, recovery); never let the PM wait without end.
MPP_TIMEOUT = int(os.environ.get('PMP_MPP_TIMEOUT', '360'))


def _choose_output_route(args, normalized) -> dict:
    """Only the explicit CLI --mpp authorises desktop automation."""
    requested = bool(args.mpp)
    args.no_mpp = True
    args.lean = False
    return dict(route='pdf', primary='pdf_executive_suite', mpp_requested=requested,
                reason='先交付PDF、Timeline与说明；仅显式--mpp追加原生MPP')


def normalize_output(value: str | None, default_run: Path) -> Path:
    path = Path(value).expanduser().resolve() if value else default_run / 'programme.mpp'
    if path.is_dir() or not path.suffix:
        path = path / 'programme.mpp'
    elif path.suffix.lower() in ('.pdf', '.mpp'):
        path = path.with_suffix('.mpp')
    else:
        raise ValueError('--output accepts a directory, .pdf or .mpp path')
    return path


AUDIT_FOLDER = '审计数据'


def _base_facts(folder: str) -> tuple[str, dict]:
    """Facts the user gave in an earlier run (defaults are re-applied, not inherited)."""
    base = Path(folder).resolve()
    found = sorted([*base.glob(AUDIT_FOLDER + '/*_input.json'), *base.glob('*_input.json')])
    if not found:
        raise ValueError(f'上次输出文件夹里没有找到输入记录：{base}')
    data = json.loads(found[0].read_text(encoding='utf-8'))
    evidence = data.get('field_evidence', {})
    facts = {k: v for k, v in data.get('project', {}).items()
             if evidence.get(k, {}).get('origin') != 'default_policy'}
    return f'上次运行（{base.name}）', facts


def _finish(output: Path, payload: dict, route: dict, status: str = 'DELIVERED', changes: dict | None = None) -> None:
    """Write the plain-language note, tidy, and tell the agent what the user must decide.

    A note failure never hides delivered files."""
    decision = {}
    try:
        decision = json.loads(audit_file(output, '_schedule.json').read_text(encoding='utf-8')).get('target_decision') or {}
    except (OSError, ValueError):
        pass
    brief = []
    try:
        from tools.pm_brief import delivered_brief
        brief = delivered_brief(output, payload, route, changes)
    except Exception as exc:  # the brief is a convenience; files are already verified
        print('PM_BRIEF_SKIPPED: ' + str(exc), file=sys.stderr)
    _tidy_outputs(output)
    if status not in ('DELIVERED', 'MPP_NOT_DELIVERED'):
        return
    files = sorted(str(p) for p in output.parent.glob(output.stem + '*') if p.is_file()
                   and (p.suffix.lower() != '.mpp' or route.get('mpp_status') == 'DELIVERED'))
    benchmark = {}
    try:
        benchmark = json.loads(audit_file(output, '_benchmark.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        if route.get('elapsed_seconds') is not None:
            benchmark = {'total_seconds': route.get('elapsed_seconds'), 'pdf_suite_seconds': route.get('pdf_suite_seconds')}
    print(json.dumps({'agent_instruction': '把 pm_brief 逐行原样发给用户，包括最后的“请你选”；保留原始数字与结论，并可补充必要的核实提示，然后等用户回复。用户回答专项问题后，选中的 addon 设为 required、未明确选择的保留 unknown（仅明确排除的设为 excluded），用 --base <本次输出文件夹> 重算；选 MPP 时用 --base <本次输出文件夹> --mpp；选评估压缩时用 --base <本次输出文件夹> --target_response assess。',
                      'pm_brief': brief, 'delivery_status': status, 'output_route': route, 'files': files,
                      'authority_draft': bool(payload.get('project', {}).get('authority_draft')),
                      'target_decision': {k: decision[k] for k in ('status', 'target', 'calculated_finish', 'calculated_move_in',
                                          'variance_calendar_days', 'decision_required', 'choices') if k in decision},
                      'benchmark': benchmark},
                     ensure_ascii=False, indent=2))


def _tidy_outputs(output: Path) -> Path:
    """Leave only what the user opens at the top level; machine audits go to a subfolder.

    Runs after every delivery check, so the engine and the native audit keep
    their original flat layout while they work.
    """
    keep = {output.name, output.stem + '.pdf', output.stem + '_Milestones.pptx',
            output.stem + '_BD_KeyMilestones.pptx',
            output.stem + '_本次排程条件.docx',
            output.stem + '_本次排程条件.txt',
            output.stem + '_交付说明.docx', output.stem + '_交付说明.txt',
            output.stem + '_改用PDF路线_输入.json'}
    audit = output.parent / AUDIT_FOLDER
    for path in sorted([*output.parent.glob(output.stem + '_*'), output.with_suffix('.xml')]):
        if path.is_file() and path.name not in keep:
            audit.mkdir(exist_ok=True)
            path.replace(audit / path.name)
    return audit


def audit_file(output: Path, suffix: str) -> Path:
    """Locate an audit file of a run, before or after tidying."""
    tidied = output.parent / AUDIT_FOLDER / (output.stem + suffix)
    return tidied if tidied.exists() else output.with_name(output.stem + suffix)


def _run_diff(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog='run_contract.py diff')
    p.add_argument('--base', required=True)
    p.add_argument('--target', required=True)
    p.add_argument('--output', default=None)
    args = p.parse_args(argv)
    cmd = [sys.executable, '-X', 'utf8', str(ROOT / 'main.py'), 'diff',
           '--base', args.base, '--target', args.target]
    if args.output:
        cmd.extend(['--output', args.output])
    result = subprocess.run(cmd, cwd=ROOT)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'diff':
        return _run_diff(argv[1:])
    run_started = time.perf_counter()
    parser = parser_for_contract()
    args, unknown = parser.parse_known_args(argv)
    from tools.check_env import check, report
    env = check(include_mpp=bool(args.mpp))
    if env['status'] != 'READY':
        # Fail before reading any input, with the one command that fixes it.
        print(json.dumps({'env_status': 'MISSING', 'message': report(env), **env}, ensure_ascii=False, indent=2))
        return 4
    if unknown:
        parser.error("CONTRACT_REJECTED: unsupported public parameters: " + " ".join(unknown))
    default_run = ROOT / 'output' / ('run_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
    try:
        output = normalize_output(args.output, default_run)
    except ValueError as exc:
        parser.error(str(exc))
    sources = []
    try:
        for filename in args.reference + ([args.input] if args.input else []):
            sources.append((str(Path(filename).resolve()), load_input(filename)))
        base_name, base_facts = _base_facts(args.base) if args.base else (None, {})
    except (OSError, ValueError) as exc:
        print("INTAKE_READ_FAILED: " + str(exc), file=sys.stderr)
        return 2
    controls = {"input", "reference", "output", "mpp", "no_mpp", "dry_run", "allow_diagnostic_baseline", "lean", "base"}
    sources.append(("explicit_cli_or_prompt_facts", {k: v for k, v in vars(args).items() if k not in controls and v is not None}))
    changes = None
    if args.base:
        # What is said now replaces the earlier value; everything else carries over.
        provided = {}
        for source in sources:
            single = normalize_project_inputs([source])
            provided.update({k: single.project[k] for k, ev in single.evidence.items() if ev.get('origin') != 'default_policy'})
        reset_local = []
        canonical_city = lambda v: str(v or '').strip().lower().removesuffix('市')
        location_changed = ('city' in provided and canonical_city(provided['city']) != canonical_city(base_facts.get('city'))) or ('district' in provided and provided['district'] != base_facts.get('district'))
        if location_changed:
            local_keys = {'province','district','authority_local_route','authority_size_class','authority_basis','authority_rule_date','building_context','renovation_category','authority_special_policy','building_safety_assessment','fire_classification','fire_sampling','authority_review_days','authority_permit_days','authority_fire_days','authority_days_basis'}
            reset_local = [k for k in local_keys if k in base_facts and k not in provided]
            base_facts = {k:v for k,v in base_facts.items() if k not in reset_local}
        kept = {k: v for k, v in base_facts.items() if k not in provided}
        changes = dict(base=base_name, kept=len(kept), reset_local_fields=reset_local,
                       changed={k: (base_facts.get(k, '未提供'), v) for k, v in provided.items()
                                if k != 'project_name' and base_facts.get(k) != v})
        sources.insert(0, (base_name, kept))
        if not args.output:
            base_dir = Path(args.base).resolve()
            # Keep the earlier file name so the PM recognises the same project.
            earlier = sorted(base_dir.glob('*_交付说明.docx'))
            stem = earlier[0].name[:-len('_交付说明.docx')] if earlier else 'programme'
            output = base_dir.parent / f'{base_dir.name}_改_{datetime.datetime.now():%Y%m%d_%H%M%S}' / f'{stem}.mpp'
    if args.lean:
        sources.append(('explicit --lean alias', {'delivery_profile': 'lean'}))
    normalized = normalize_project_inputs(sources)
    args.lean = normalized.project['delivery_profile'] == 'lean'
    route = _choose_output_route(args, normalized)
    if normalized.project.get("start_date") and normalized.project.get("target_date"):
        args.allow_diagnostic_baseline = True
    if args.lean:
        # A quick planning draft discloses a missed target instead of hiding it
        # or forcing a second run; it never changes the requested dates.
        args.allow_diagnostic_baseline = True
    payload = normalized.payload()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = output.parent / (output.stem + "_input.json")
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    from tools.pm_brief import blocked_message
    print(json.dumps({"pm_message": blocked_message(normalized.blockers),
                      "input_status": normalized.status, "input_report": str(report),
                      "output_route": route,
                      "blockers": normalized.blockers, "assumptions": payload["assumptions"],
                      "warnings": normalized.warnings}, ensure_ascii=False, indent=2), file=sys.stderr)
    if normalized.blockers:
        print(json.dumps({'delivery_status':'BLOCKED', 'pm_message':blocked_message(normalized.blockers),
                          'output_route':route, 'blockers':normalized.blockers}, ensure_ascii=False))
        return 2
    if args.dry_run:
        print(json.dumps({'delivery_status':'VALIDATED_INPUT', 'output_route':route,
                          'input_report':str(report)}, ensure_ascii=False))
        return 0
    command = [sys.executable, '-X', 'utf8', str(ROOT / 'main.py'),
               *engine_arguments(normalized.project), '--project_input', str(report), '--output', str(output)]
    if args.allow_diagnostic_baseline:
        command.append('--allow_diagnostic_baseline')
    # Exporter output goes to stderr; stdout is one machine-readable result.
    suite_paths = [output.with_suffix('.pdf'), output.with_name(output.stem+'_Milestones.pptx'),
                   output.with_name(output.stem+'_BD_KeyMilestones.pptx')]
    before = {p:p.stat().st_mtime_ns if p.exists() else None for p in suite_paths}
    # PDF engine is safe in-process: reuse imports/config instead of starting a second
    # interpreter. Native Project remains an isolated timeout-controlled worker below.
    import io
    import contextlib
    from main import main as engine_main
    captured = io.StringIO()
    rc = 0
    try:
        with contextlib.redirect_stdout(captured):
            engine_main(command[4:] + ['--no_mpp'])
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
        if exc.code and not isinstance(exc.code, int):
            print(exc.code, file=sys.stderr)
    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        rc = 1
    result = subprocess.CompletedProcess(command, rc, stdout=captured.getvalue())
    print(result.stdout, file=sys.stderr)
    if result.returncode:
        print(json.dumps({'delivery_status':'FAILED','output_route':route,
                          'error':'PDF scheduling/export failed; see stderr'}, ensure_ascii=False))
        return result.returncode
    from tools.condition_sheet import write_conditions
    from tools.delivery_note import write_delivery_note
    try:
        if any(not p.exists() or p.stat().st_mtime_ns == before[p] for p in suite_paths):
            raise ValueError('Suite exports were not refreshed in this run')
        write_conditions(output, payload)
        write_delivery_note(output, payload, route, changes)
        _verify_suite(output)
    except Exception as exc:
        print(json.dumps({'delivery_status':'INCOMPLETE','error':str(exc),'output_route':route},ensure_ascii=False))
        return 3
    route['pdf_suite_seconds'] = round(time.perf_counter() - run_started, 3)
    route['mpp_status'] = 'NOT_REQUESTED'
    if args.mpp:
        previous_mpp = output.stat().st_mtime_ns if output.exists() else None
        from core.project_env import ms_project_status
        native_env = ms_project_status()
        route['mpp_status'] = 'UNAVAILABLE'
        if native_env['available']:
            route['mpp_status'] = 'FAILED'
            native_audit = output.with_name(output.stem+'_mpp_audit.json')
            previous_mpp = output.stat().st_mtime_ns if output.exists() else None
            previous_audit = native_audit.stat().st_mtime_ns if native_audit.exists() else None
            try:
                # Independent worker: already verified PDF/DOCX/PPTX are untouched.
                native = subprocess.run(command + ['--mpp_only'], cwd=ROOT, timeout=MPP_TIMEOUT,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding='utf-8', errors='replace')
                print(native.stdout, file=sys.stderr)
                audit = json.loads(output.with_name(output.stem+'_mpp_audit.json').read_text(encoding='utf-8'))
                if (native.returncode == 0 and output.is_file() and output.stat().st_mtime_ns != previous_mpp
                        and native_audit.stat().st_mtime_ns != previous_audit
                        and audit.get('status') == 'PASS' and audit.get('date_parity') == 'CHECKED'):
                    route.update(mpp_status='DELIVERED', mpp_ready=True)
                else:
                    route['reason'] = 'MPP保存重开核验未通过；已验证PDF套件可用'
            except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
                # Do not terminate Project by start time: it may be a user-owned instance.
                route['reason'] = 'MPP未完成；PDF套件已保留。' + str(exc)
        else:
            route['reason'] = native_env['reason'] + '；PDF套件已验证'
        if (route['mpp_status'] != 'DELIVERED' and output.exists()
                and output.stat().st_mtime_ns != previous_mpp):
            quarantine = output.parent / AUDIT_FOLDER
            quarantine.mkdir(exist_ok=True)
            output.replace(quarantine / (output.stem + '_未通过核验.mpp'))
    route['elapsed_seconds'] = round(time.perf_counter() - run_started, 3)
    _finish(output, payload, route, changes=changes)
    return 0


def _verify_suite(output: Path) -> None:
    """Check deliverable readability, not merely file existence."""
    pdf = output.with_suffix('.pdf')
    if not pdf.is_file() or not pdf.read_bytes().startswith(b'%PDF-'):
        raise ValueError('Missing/invalid PDF')
    from pptx import Presentation
    for suffix in ('_Milestones.pptx', '_BD_KeyMilestones.pptx'):
        path = output.with_name(output.stem + suffix)
        if not len(Presentation(str(path)).slides):
            raise ValueError('Empty Timeline: ' + str(path))
    for suffix in ('_本次排程条件', '_交付说明'):
        doc = output.with_name(output.stem + suffix + '.docx')
        txt = doc.with_suffix('.txt')
        if doc.is_file():
            with zipfile.ZipFile(doc) as z:
                if z.testzip() or 'word/document.xml' not in z.namelist():
                    raise ValueError('Invalid DOCX: ' + str(doc))
        elif not txt.is_file() or not txt.read_text(encoding='utf-8').strip():
            raise ValueError('Missing condition/delivery document: ' + suffix)


if __name__ == '__main__':
    raise SystemExit(main())
