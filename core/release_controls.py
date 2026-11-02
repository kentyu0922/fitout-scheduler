"""Optional furniture interfaces and explicit fire-release scenarios.

No resource levelling or just-in-time rescheduling. Scenario graphs are copies;
conditional exposure never silently edits the main programme.
"""
import copy
import math
from datetime import date
from core.module_graph import add_pred, remove_nodes, dependency_order
from core.master_graph import _links, _tokens


def input_issues(facts):
    errors, warnings = [], []
    def error(field, reason): errors.append(dict(field=field, reason=reason))
    for key in ('furniture_in_scope', 'furniture_tracking', 'furniture_procurement'):
        if key in facts and not isinstance(facts[key], bool):
            error(key, '内部接口须使用规范化布尔值；请经统一入口解析是/否。')
    tracking = facts.get('furniture_tracking', False)
    procurement = facts.get('furniture_procurement', False)
    supplied = any(facts.get(k) is not None for k in ('furniture_supply_state', 'furniture_remaining_days', 'furniture_lead_profile', 'furniture_supply_basis'))
    if facts.get('furniture_in_scope', True) is False and (tracking or procurement or supplied):
        error('furniture_in_scope', '排除家具与家具招采、跟踪或供货信息冲突，请确认范围。')
    if supplied and 'furniture_tracking' not in facts:
        error('furniture_tracking', '已提供家具供货信息；请确认是否启用供货跟踪，不自动加载采购链。')
    if supplied and not tracking:
        warnings.append(dict(field='furniture_tracking', reason='供货信息已保留，但供货跟踪关闭；这些信息不驱动日期。'))
    state = facts.get('furniture_supply_state')
    if tracking and not state:
        error('furniture_supply_state', '启用供货跟踪须明确新购、已下单、已到货或利旧。')
    if procurement and state in ('ordered', 'delivered', 'reuse'):
        error('furniture_procurement', '已下单/到货/利旧不应重复加载新招采；请核对是否另有新增采购范围。')
    if tracking and state in ('ordered', 'delivered', 'reuse') and not facts.get('furniture_supply_basis'):
        error('furniture_supply_basis', '跟踪已下单/到货/利旧须有订单或清单依据。')
    if tracking and state == 'ordered' and facts.get('furniture_remaining_days') is None:
        error('furniture_remaining_days', '已下单须提供自项目启动会完成时点起的剩余工作日，不重置完整生产周期。')
    if tracking and state == 'new_order' and not procurement:
        error('furniture_procurement', '新购未下单的生产链须有下单条件；请开启独立招采，或改为已下单并提供剩余交期。')
    for key in ('furniture_remaining_days', 'fire_inspection_days', 'fire_rectification_days', 'fire_recheck_days'):
        value = facts.get(key)
        if value is not None:
            try:
                number = float(value)
                valid = not isinstance(value, bool) and math.isfinite(number) and number >= 0 and number.is_integer()
            except (ValueError, TypeError, OverflowError):
                valid = False
            if not valid:
                error(key, '须为非负整数工作日，不静默舍入。')
    if facts.get('fire_inspection_days') == 0:
        error('fire_inspection_days', '抽查工作不能用零天成果代替。')
    if any(facts.get(k) is not None for k in ('fire_inspection_days', 'fire_rectification_days', 'fire_recheck_days')) and not facts.get('fire_duration_basis'):
        error('fire_duration_basis', '项目抽查、整改或复核工期须有依据；不把整改合并进法定检查窗口。')
    if bool(facts.get('fire_rectification_days')) != bool(facts.get('fire_recheck_days')):
        error('fire_recheck_days', '计划包含整改时须同时明确复核预留；不把整改完成当作复核通过。')
    if facts.get('fire_release_policy', 'pending') != 'pending' and not facts.get('fire_release_basis'):
        error('fire_release_basis', '明确入驻放行政策须记录依据，不自动等同于属地法律结论。')
    if facts.get('fire_sampling') == 'not_selected' and facts.get('fire_rectification_days'):
        error('fire_sampling', '已确认未抽中与抽查整改安排冲突。')
    if facts.get('fire_classification') in ('special', 'not_applicable') and (
            facts.get('fire_release_policy', 'pending') != 'pending' or
            any(facts.get(k) is not None for k in ('fire_inspection_days','fire_rectification_days','fire_recheck_days'))):
        error('fire_classification', '备案抽查情境字段仅用于其他建设工程；不能套用特殊工程消防验收。')
    return errors, warnings


def _node(tasks, uid, name, days, preds, phase, kind):
    node = dict(id=max(t['id'] for t in tasks)+1, activity_uid=uid, name=name,
                duration_days=days, duration=days, milestone=days == 0, outline_level=3,
                predecessors=_tokens([(t['id'], 'FS', 0) for t in preds]),
                process_phase=phase, master_phase=phase, activity_class=kind,
                duration_basis='planning_allowance_pending_project_confirmation')
    tasks.append(node)
    return node


def configure_furniture(tasks, facts):
    errors, _ = input_issues(facts)
    if errors:
        raise ValueError('RELEASE_INPUT_REQUIRED: ' + str(errors))
    old = {t['id'] for t in tasks if '家具供应商定标' in t['name'] or '家具生产交货' in t['name']}
    tasks = remove_nodes(tasks, old)
    receipt = next(t for t in tasks if t.get('module_id') == 'office_ff_move' and t.get('module_step') == 0)
    installation = next(t for t in tasks if t.get('module_id') == 'office_ff_move' and t.get('module_step') == 1)
    disabled_lead = dict(profile=None, lead_days=0, lead_time_basis='未加载供货跟踪，不假设生产周期', confidence='not_scheduled', evidence_ref=None, assumed=False)
    report = dict(in_scope=facts.get('furniture_in_scope', True), procurement=facts.get('furniture_procurement', False),
                  tracking=facts.get('furniture_tracking', False), resolved_lead=disabled_lead,
                  lead_report=dict(applied=False, reason='tracking_not_loaded'))
    if not report['in_scope']:
        tasks = remove_nodes(tasks, {receipt['id'], installation['id']})
        for task in tasks:
            if task.get('module_id') == 'office_ff_move' and task.get('module_step') == 3:
                task['name'] = '办公终端安装就绪'
        return tasks, report
    ready_types = {'gypsum_ceiling', 'feature_ceiling', 'painting', 'wallcovering', 'raised_floor', 'carpet', 'vinyl', 'tile_stone', 'construction_cleaning'}
    ready_work = [t for t in tasks if t.get('activity_type') in ready_types]
    if not ready_work:
        raise ValueError('FURNITURE_WORKFACE_ANCHORS_MISSING')
    ready = _node(tasks, 'pmp.process.furniture.workface', '家具安装区域移交', 0, ready_work, '实体施工', 'SITE_INSTALLATION')
    ready['module_id'] = 'furniture_interface'
    ready['reporting_phase_override'] = '施工阶段'
    ready['input_notes'] = '整区移交规划：湿作业和天花饰面完成、地面完成并保护、安装前清洁、运输通道及堆放条件具备；不等于最终交付精保洁。未建模分区提前移交。'
    receipt.update(name='家具到场验收',duration_days=1,duration=1,predecessors=f'{ready["id"]}FS', process_phase='实体施工',
                   reporting_phase_override='竣工验收阶段', activity_class='SITE_INSTALLATION')
    receipt.pop('activity_type', None)
    receipt['activity_type']='receipt_inspection'
    receipt['duration_basis'] = '到货数量、外观及文件检查暂留1工作日；批次及检测范围待供应商/PM确认，不含安装或生产交期，不按全楼面积放大'
    report.update(receipt_task_uid=receipt.get('activity_uid'), workface_task_uid=ready['activity_uid'])
    order = None
    if report['procurement']:
        drawing = next(t for t in tasks if '全专业施工图' in t['name'])
        spec = _node(tasks, 'pmp.process.furniture.spec', '家具范围、规格及评选条件确认', 3, [drawing], '设备采购', 'CLIENT_APPROVAL')
        order = _node(tasks, 'pmp.process.furniture.order', '家具评选、定标与合同下单', 5, [spec], '设备采购', 'TENDER_COMMERCIAL')
        for t in (spec, order):
            t.update(module_id='procurement_furniture', reporting_procurement_object='家具', reporting_phase_override='招采阶段')
            t['input_notes'] = '用户显式选择的独立家具招采；不自动套用GC三轮报价，评选制度和工期待项目确认。'
        add_pred(receipt, order)
    if not report['tracking']:
        receipt['external_supply_condition'] = dict(status='unverified', assumption='家具在现场接收条件具备时可供接收；日期未取得供应商确认，未计未确认交期风险；须核实规格冻结、下单及承诺到货日期。')
        receipt['input_notes'] = '外部供货条件待确认：本次不排生产运输，暂按工作面具备时可接收测算；不得视为到货承诺。'
        report['external_condition'] = receipt['external_supply_condition']
        return tasks, report
    state = facts['furniture_supply_state']
    kickoff = next(t for t in tasks if 'Kick Off' in t['name'])
    if state == 'new_order':
        from core.furniture_lead import resolve_furniture_lead
        lead = resolve_furniture_lead(facts.get('furniture_lead_profile'))
        lead['assumed'] = not bool(facts.get('furniture_lead_profile'))
        supply = _node(tasks, 'pmp.process.furniture.production', '家具生产与供货准备', lead['lead_days'], [order], '实体施工', 'FACTORY_PRODUCTION')
    elif state == 'ordered':
        lead = dict(disabled_lead, lead_days=int(facts['furniture_remaining_days']), lead_time_basis=facts['furniture_supply_basis'], confidence='declared_input')
        supply = _node(tasks, 'pmp.process.furniture.remaining', '已下单家具剩余供货准备', lead['lead_days'], [kickoff], '实体施工', 'FACTORY_PRODUCTION')
    else:
        lead = dict(disabled_lead, lead_time_basis=facts['furniture_supply_basis'], confidence='declared_input')
        supply = _node(tasks, 'pmp.process.furniture.stock', '现有家具清点、状态与适配核查', 2, [kickoff], '实体施工', 'CLIENT_APPROVAL')
    supply.update(module_id='furniture_supply', reporting_phase_override='施工阶段', furniture_supply=True)
    supply['input_notes'] = '显式供货跟踪，供应商可供接收状态不等于现场已收货；提前供货需确认保管、发运及二次搬运安排。交期从项目启动会完成时点起算（新购从下单完成起算）。' + str(lead['lead_time_basis'])
    add_pred(receipt, supply)
    report.update(state=state, supply_task_uid=supply['activity_uid'], resolved_lead=lead,
                  lead_report=dict(applied=state in ('new_order', 'ordered'), template_lead_days=None, reason='explicit_tracking', task=dict(name=supply['name'])))
    return tasks, report


def split_permit(tasks, permit, *, handling_days=None, final_compilation_after_ready=False):
    work = next(t for t in permit if t.get('duration_days', 0) > 0)
    result = next(t for t in permit if t.get('duration_days', 0) == 0)
    contract = next((t for t in tasks if t.get('module_id') in ('procurement_gc', 'procurement_db') and t.get('procurement_step') == 'contract'), None)
    if contract is None:
        contract = next((t for t in tasks if any(k in t['name'] for k in ('总包', '总承包')) and any(k in t['name'] for k in ('合同签', '合同谈判', '签约')) and t.get('outline_level', 3) >= 3), None)
    if contract is None:
        raise ValueError('PERMIT_CONSTRUCTION_CONTRACT_ANCHOR_MISSING')
    add_pred(work, contract)
    kickoff = next(t for t in tasks if 'Kick Off' in t['name'])
    prep = _node(tasks, 'pmp.authority.permit_prepare', '施工许可资料汇齐与核对', 3, [kickoff], '开工许可', 'PROJECT_MANAGEMENT')
    # Start early; final issuer-specific documents cannot complete before their providers.
    original_links = _links(work.get('predecessors'))
    # Final-document providers must have finish-to-start gates on submission.
    # Their lag belongs to formal submission (retained verbatim), not document collection.
    # Do not reinterpret a start/finish constraint as proof of documents being available.
    if any(kind != 'FS' for _, kind, _ in original_links):
        raise ValueError('PERMIT_PROVIDER_RELATION_REQUIRES_REVIEW: expected FS provider gates')
    prep['predecessors'] = _tokens([(kickoff['id'], 'FS', 0)] + [(pid, 'FF', 0) for pid, _, _ in original_links])
    prep['provider_submission_links'] = _tokens(original_links)
    prep['input_notes'] = '最终资料汇齐与核对暂留3工作日，为未校准PM规划值；日常收集可提前开展。完成须取得最终施工企业、资质、安全生产许可、项目人员、合同及适用图审资料。原前置延迟仅保留在正式申报，不解释为资料收集工期。'
    prep['permit_role'] = 'prepare'
    prep['input_notes'] += ' 默认按提前整理、与最终资料同步汇齐测算，与办理预留不作串行相加；不代表已支持资料齐备后另加N天的输入。'
    work['permit_duration_scope'] = dict(status='legacy_scope_unresolved', includes_post_contract_compilation=None, adopted_days=work['duration_days'], additional_post_ready_days=0, basis='沿用既有工作包规划量；历史材料整理、签章、受理、补正范围未拆清，不自动叠加额外等待。')
    work.update(name='施工许可申报与办理', permit_role='submit', duration_days=work['duration_days'])
    work['duration'] = work['duration_days']
    work['input_notes'] = '正式提交等待适用条件全部具备；沿用原申报办理规划预留，未因资料核对而扣减；历史值是否包含合同后整理签章尚未拆清，不代表已核实纯行政时限。项目另报资料齐备后N天时，先核对范围重叠，再决定替换、重新分配或增加；不自动叠加。'
    add_pred(work, prep)
    result.update(name='施工许可取得', permit_role='result', duration_days=0, duration=0, milestone=True)
    add_pred(result, work)
    if final_compilation_after_ready:
        prep['predecessors'] = _tokens([(pid, 'FS', 0) for pid, _, _ in original_links])
        prep['input_notes'] = '最终合同、适用图审及物业资料齐备后，整理签章、录入并核对申报材料，暂留3工作日；前期收集可提前开展。此为PM规划量，非行政办理时限。'
        prep['preparation_mode'] = 'post_provider_compilation'
    if handling_days is not None:
        previous_days = work['duration_days']
        work.update(duration_days=handling_days, duration=handling_days)
        work['permit_duration_scope'] = dict(status='separated_applicant_and_authority', adopted_days=handling_days, legacy_package_days=previous_days, applicant_compilation_days=prep['duration_days'], extra_buffer_days=0)
        work['duration_basis'] = '沪建规范〔2025〕12号第八条，适用既有建筑装修路径核发窗口2工作日；不含申请人准备与未明确补正'
        work['input_notes'] = '资料汇齐整理与行政核发分开；正式申报等待材料准备及全部原适用条件。核发按适用2工作日窗口规划，不保证补正或例外情形仍按期完成；未把原7天差额补成隐含缓冲。'
        work.pop('statutory_city_clock', None)
        work['source_url'] = 'https://zjw.sh.gov.cn/gfxwj/20250925/ff0c97d945a243cc9133ca6f52d6d326.html'
    permit.append(prep)
    return tasks


def attach_fire_inspection(tasks, facts, *, gate_move=True):
    """Other-construction inspection only; independent of completion filing.

The optional gate is an explicit internal planning policy, not a legal claim.
"""
    filing = next(t for t in tasks if t.get('fire_role') == 'filing_result')
    move_release = next(t for t in tasks if t.get('module_id') == 'core_handover' and t.get('module_step') == 3)
    days = int(facts.get('fire_inspection_days', 15))
    check = _node(tasks, 'pmp.authority.fire_inspection', '消防备案抽查配合与结果跟踪', days, [filing], '验收审批', 'AUTHORITY_APPROVAL')
    check['fire_role'] = 'inspection'
    from core.authority_registry import inspection_source
    src=inspection_source(facts)
    check['duration_basis'] = facts.get('fire_duration_basis') or src['document']+'：按检查对象确定后15工作日情境，不含通知等待和未声明整改'
    check['source_url'] = src['url']
    check['input_notes'] = '按备案后即确定检查对象测算；15工作日为本情境的检查窗口假设，不含等待抽中通知和整改，不代表必定用满或必定合格。' if 'fire_inspection_days' not in facts else facts['fire_duration_basis']
    last = check
    if facts.get('fire_rectification_days'):
        last = _node(tasks, 'pmp.authority.fire_rectify', '消防抽查问题整改', int(facts['fire_rectification_days']), [last], '验收审批', 'SITE_CONSTRUCTION')
        last = _node(tasks, 'pmp.authority.fire_recheck', '消防抽查整改复核', int(facts['fire_recheck_days']), [last], '验收审批', 'AUTHORITY_APPROVAL')
        gate_move = True  # a modelled failure cannot release occupancy on a filing assumption
    check['inspection_blocks_move'] = gate_move
    if gate_move:
        add_pred(move_release, last)
    else:
        # A documented post-occupancy follow-up is an intentional terminal branch.
        # Linking it to "Day 1 / move complete" would silently undo the policy.
        last['non_occupancy_closeout'] = True
        last['input_notes'] += '\n按已提供放行政策作为独立跟踪终点；不阻断搬迁。项目总结束可能晚于搬迁完成。抽查不合格须重新核定使用与整改条件。'
    dependency_order(tasks)
    return tasks


def _move_date(tasks):
    move = next(t for t in tasks if t.get('module_id') == 'core_handover' and t.get('module_step') == 4)
    return move.get('start') or move['start_date']


def release_report(prepared, solved, facts, kickoff, holidays, calendar):
    from core.solver_engine import solve_schedule
    from core.activity_calendar import apply_activity_calendars
    decision = next((t['local_authority'] for t in prepared if t.get('local_authority')), {})
    report = dict(version='1.1', delta_unit='calendar_days', approved_baseline=False,
                  status='SINGLE_PLAN', main_move_in=_move_date(solved), scenario_move_in=None,
                  delta_calendar_days=None, release_policy=facts.get('fire_release_policy', 'pending'), warnings=[])
    if decision.get('fire_route') == 'other' and decision.get('sampling') == 'pending' and facts.get('fire_release_policy') != 'inspection':
        variant = copy.deepcopy(prepared)
        original_ids = {t['id'] for t in variant}
        if not any(t.get('fire_role') == 'inspection' for t in variant):
            attach_fire_inspection(variant, facts)
        # Preserve all existing project-specific calendar overrides. Only the
        # newly added scenario work needs calendar assignment.
        apply_activity_calendars([t for t in variant if t['id'] not in original_ids], calendar)
        compared = solve_schedule(variant, kickoff, custom_holidays=holidays, construction_calendar_mode=calendar)
        report.update(status='PROVISIONAL_DUAL_SCENARIOS', scenario_move_in=_move_date(compared['tasks']))
        report['delta_calendar_days'] = (date.fromisoformat(report['scenario_move_in'])-date.fromisoformat(report['main_move_in'])).days
        report['inspection_days'] = int(facts.get('fire_inspection_days', 15))
        report['scenario_basis'] = '假设备案后即确定检查对象；内部放行等待检查结果，按通过测算；未包含未声明的整改或通知等待。'
        report.update(_scenario_evidence(prepared, solved, variant, compared['tasks'], kickoff, holidays, calendar))
    if decision.get('fire_route') == 'other' and decision.get('sampling') == 'pending' and facts.get('fire_release_policy') == 'inspection':
        report['status']='PROVISIONAL_CONSERVATIVE_PLAN'
    report['main_assumption'] = ('内部规划等待抽查及适用整改复核结果' if any(t.get('inspection_blocks_move') for t in prepared)
                                 else '未在入驻链上增加抽查等待；仍须满足其他适用条件') if decision.get('fire_route') == 'other' else '按已选报审路径及项目使用条件'
    from core.authority_registry import inspection_source
    report['inspection_source'] = inspection_source(facts) if decision.get('fire_route')=='other' else None
    report['policy_note'] = '计划草案：核对属地通知、项目适用条件及业主放行政策后，PM才能确定承诺日期。'
    if decision.get('status') != 'READY':
        report['warnings'].append(dict(code='LOCAL_AUTHORITY_UNVERIFIED', message='属地规则未核实；审批工期为旧模板暂估，不能用于开工或入驻承诺。'))
    byuid = {t.get('activity_uid'):t for t in solved}
    furniture = next((t.get('process_closure', {}).get('furniture', {}) for t in solved if t.get('process_closure')), {})
    supply = byuid.get(furniture.get('supply_task_uid'))
    receipt = byuid.get(furniture.get('receipt_task_uid'))
    if furniture.get('external_condition'):
        report['warnings'].append(dict(code='FURNITURE_EXTERNAL_SUPPLY', message=furniture['external_condition']['assumption']))
    if supply and receipt:
        gap = (date.fromisoformat(receipt['start'])-date.fromisoformat(supply['finish'])).days
        if gap > 0:
            report['warnings'].append(dict(code='FURNITURE_EARLY_SUPPLY', gap_calendar_days=gap,
                supply_ready=supply['finish'], planned_receipt=receipt['start'],
                advisory_threshold_calendar_days=90,
                priority='verify_input' if gap > 90 else 'coordinate_delivery',
                message=('供货与接收间隔超过90日历天的PM提示阈值：先核对交期起算日、批次、订单范围与计划是否匹配；再确认发运保管安排。阈值不参与排程，也非行业规范。' if gap > 90 else '供货早于计划接收，需确认保管、发运及二次搬运安排；不代表已发生费用或自动推迟下单。')))
    comparison = ('已按明确的内部保守政策计入主计划' if report['status']=='PROVISIONAL_CONSERVATIVE_PLAN' else '不适用／抽查状态已确定') if report['scenario_move_in'] is None else f"{report['scenario_move_in']}（相对主计划 {report['delta_calendar_days']:+d} 日历天）"
    sensitivity = report.get('sensitivity_window')
    if sensitivity:
        if sensitivity['status'] == 'BOUNDED_THRESHOLD_FOUND':
            comparison += f"；固定其他条件的计划敏感度：保持主计划搬迁日的检查窗口为{sensitivity['max_inspection_workdays_without_move_delay']}个检查工作日（政府工作日历）"
        else:
            comparison += '；检查窗口未取得精确阈值，详见敏感度说明'
        comparison += '；不含未声明的通知等待及整改，超过窗口须重算'
    report['summary_lines'] = [f"主计划：搬迁开始 {report['main_move_in']}；{report['main_assumption']}",
                               f"抽查情境：{comparison}", '承诺条件：'+report['policy_note']]
    return report


def _scenario_evidence(prepared, main, variant, compared, kickoff, holidays, calendar):
    """Use actual solved dependencies and bounded re-solves; never equate overlap with float."""
    from core.solver_engine import solve_schedule
    def release(ts):
        return next(t for t in ts if t.get('module_id') == 'core_handover' and t.get('module_step') == 3)
    original, changed = release(main), release(compared)
    byid = {t['id']: t for t in main}
    edges = _links(original.get('predecessors'))
    plain = all(kind == 'FS' and lag == 0 for _, kind, lag in edges)
    providers = [byid[pid] for pid, _, _ in edges if pid in byid]
    latest = max((t['finish'] for t in providers), default=None)
    governing = [dict(uid=t.get('activity_uid'), name=t['name'], finish=t['finish']) for t in providers if t['finish'] == latest] if plain else []
    check = next(t for t in compared if t.get('fire_role') == 'inspection')
    fire_end = next((t for t in compared if t.get('activity_uid') == 'pmp.authority.fire_recheck'), check)
    names = '、'.join(t['name'] for t in governing)
    explanation = (f"原放行前置最晚完成：{names}（{latest}）；" if governing else '原放行前置含搭接或延迟，须结合关系审阅；')
    explanation += f"原入驻放行{original['finish']}，检查及已声明复核完成{fire_end['finish']}，重算入驻放行{changed['finish']}。净差按整网计算，不把检查工期直接加到搬迁日。"
    # Maximum inspection duration preserving this main move date; other scope,
    # calendars, notice assumption, rectification and policy are held fixed.
    cache = {}
    def evaluate(days):
        if days not in cache:
            trial = copy.deepcopy(variant)
            node = next(t for t in trial if t.get('fire_role') == 'inspection')
            node.update(duration_days=days, duration=days, milestone=days == 0)
            solved = solve_schedule(trial, kickoff, custom_holidays=holidays, construction_calendar_mode=calendar)['tasks']
            c = next(t for t in solved if t.get('fire_role') == 'inspection')
            cache[days] = dict(move_in=_move_date(solved), inspection_finish=c['finish'])
        return cache[days]
    target = _move_date(main)
    bound = 120
    if evaluate(0)['move_in'] > target:
        maximum, status = None, 'OTHER_SCENARIO_CONDITIONS_ALREADY_DELAY_MOVE'
    elif evaluate(bound)['move_in'] <= target:
        maximum, status = bound, 'LOWER_BOUND_ONLY'
    else:
        lo, hi = 0, bound
        while hi-lo > 1:
            mid = (lo+hi)//2
            if evaluate(mid)['move_in'] <= target: lo = mid
            else: hi = mid
        maximum, status = lo, 'BOUNDED_THRESHOLD_FOUND'
    window = dict(status=status, max_inspection_workdays_without_move_delay=maximum,
                  search_limit_workdays=bound, calendar='inspection task calendar',
                  latest_inspection_finish=evaluate(maximum)['inspection_finish'] if maximum is not None else None,
                  next_day_result=evaluate(maximum+1) if status == 'BOUNDED_THRESHOLD_FOUND' else None,
                  basis='固定其他工作、日历、通知假设及已声明整改复核后重算；仅为计划敏感度，不是法定宽限或允许整改天数。')
    return dict(control_chain_explanation=explanation, governing_release_providers=governing,
                original_release=original['finish'], scenario_release=changed['finish'],
                inspection_finish=check['finish'], sensitivity_window=window)
