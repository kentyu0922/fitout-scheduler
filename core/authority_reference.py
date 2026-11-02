"""Approval steps for cities whose local route is not verified yet (and Shanghai drafts).

Rules recorded on 2026-10-13:

* An approval is split into what is actually done, each part labelled:
  applicant work (PM planning days plus a stated buffer, adjustable), the
  authority's time limit (sourced), and correction / rework / waiting, which is
  NOT in the plan by default and enters only as a user buffer with a reason.
* Draft time windows use national reference values subject to project applicability; the city
  table (config/city_permit.json) is shown as an unverified local reference
  the user may adopt with a basis.
* Fire: when the classification is unknown the main plan follows the
  "other construction" filing route; the special-construction acceptance is
  shown as an impact scenario. Post-filing spot checks are a scenario too.
* Nothing here claims a local rule is verified, and buffers are never hidden.
"""
from __future__ import annotations
import copy
from datetime import date

from core.master_graph import _links, _tokens

NATIONAL = {
    'review': (10, '《房屋建筑和市政基础设施工程施工图设计文件审查管理办法》（2018修订）第十二条：中型及以下房屋建筑工程审查原则上10个工作日，不含施工图修改及复审时间'),
    'permit': (7, '《建筑工程施工许可管理办法》（住建部令第52号修正）第五条：自收到申请之日起7日内发证；资料不全须补正时审批时间顺延；本计划按工作日排程（依《行政许可法》期限以工作日计算的规定，条号待核）'),
    'fire_special': (15, '不完整（依据待补）：消防验收15日历天为本比赛规划假设；未确认全国统一日历天解释，不作为法定办结承诺；整改复查另计。'),
}
SHANGHAI_EXISTING_FITOUT = {
    'review': (5, '沪建规范〔2025〕12号：既有建筑装修须审图者审图5个工作日（2025-09-15公布、2025-12-01施行）；法定时限不随面积延长'),
    'permit': (2, '沪建规范〔2025〕12号：既有建筑装修施工许可发证2个工作日；法定时限不随面积延长'),
}
FIRE_DESIGN_REVIEW = (15, '住建部令第58号修正后第二十三条：受理消防设计审查申请后15个工作日内出具书面意见；依法专家评审另核')
SPOT_CHECK = (15, '同规定：其他建设工程备案后按“双随机”抽查，被确定为检查对象之日起15个工作日内完成检查；仅对被抽中项目，整改复查另计')
APPLICANT = {
    'review': ('pmp.authority.review_rework', '图审意见修改与一次复审', 3, 0, '审查意见轮次与修改范围事前不确定；不含在法定5个工作日审查时限内。合格书在本次复审之后，施工许可在合格书之后'),
    'permit': (None, '施工许可资料整理签章', 1, 0, '前期收集可与设计并行；齐备后整理签章1工作日，非行政发证时限'),
}
FIELDS = {
    'review': ('authority_review_days', '施工图审查', '第三方图审机构审查'),
    'permit': ('authority_permit_days', '施工许可行政审批', '施工许可证申报'),
    'fire': ('authority_fire_days', '特殊建设工程消防验收', '消防验收/竣工消防备案申报'),
}
DAY_FIELDS = tuple(f for f, _, _ in FIELDS.values())
BUFFER_FIELDS = {'review': 'authority_review_buffer_days', 'permit': 'authority_permit_buffer_days',
                 'fire': 'authority_fire_buffer_days'}
BUFFER_LABEL = {'review': '图审补正', 'permit': '施工许可补正', 'fire': '消防整改复查'}
SENSITIVITY_DAYS = 5


def fire_route(facts: dict) -> str:
    value = facts.get('fire_classification')
    return value if value in ('special', 'other', 'not_applicable') else 'unknown'


def reference_days(facts: dict) -> dict:
    """Draft days per step: user value with basis, else a national reference requiring applicability review.
    The city table value is carried alongside as an unverified local reference."""
    from experts.permit_expert import query_city_permit_rule
    table = query_city_permit_rule(str(facts.get('city') or ''))
    doc = table.get('policy_doc') or 'config/city_permit.json'
    city = {'review': table.get('review_days_min'), 'permit': table.get('permit_days_min'), 'fire': None}
    basis = facts.get('authority_days_basis')
    out = {}
    for key, (field, label, _) in FIELDS.items():
        city_name = str(facts.get('city') or '').strip().lower().removesuffix('市')
        if key in ('review', 'permit') and city_name in {'上海', 'shanghai'}:
            days, source = SHANGHAI_EXISTING_FITOUT[key]
        else:
            days, source = NATIONAL['fire_special' if key == 'fire' else key]
        item = dict(label=label, days=days, source=source, user_provided=False, field=field)
        if city[key] is not None:
            item['city_reference'] = dict(days=int(city[key]), source=f'城市参考表：{doc}（当地承诺时限，未核实现行有效性）')
        if facts.get(field) is not None and basis:
            item.update(days=int(facts[field]), source='用户提供：' + basis, user_provided=True)
        out[key] = item
    return out


def apply_reference_days(tasks: list, facts: dict) -> dict:
    """Set the authority-time tasks to the reference (or user) days."""
    ref = reference_days(facts)
    for key, (_, label, needle) in FIELDS.items():
        item = ref[key]
        # After restructuring, authority-time tasks are marked; the name is only a fallback.
        task = next((t for t in tasks if t.get('authority_component', {}).get('step') == key
                     and t['authority_component']['kind'] == 'authority'
                     and not t['authority_component'].get('design')), None)
        if task is None and not any(t.get('authority_component', {}).get('step') == key for t in tasks):
            task = next((t for t in tasks if needle in t.get('name', '') and t.get('duration_days', 0) > 0), None)
        if task is None:
            item['applied'] = False  # e.g. fire on the filing route has no authority-time task
            continue
        task.update(duration_days=item['days'], duration=item['days'])
        task.pop('statutory_city_clock', None)
        task['duration_basis'] = item['source']
        task['authority_reference'] = dict(item)
        task.setdefault('authority_component', dict(step=key, kind='authority'))['days'] = item['days']
        if key == 'fire':
            task.update(calendar='7D', ignore_holidays=True, elapsed_window=True, duration_unit='calendar_days',
                        activity_class='AUTHORITY_APPROVAL')
            unit_label = '个日历天'
        else:
            task.update(calendar='5D', ignore_holidays=False, duration_unit='working_days',
                        activity_class='AUTHORITY_APPROVAL')
            task.pop('elapsed_window', None)
            unit_label = '个工作日'
        task['input_notes'] = (str(task.get('input_notes', '')) + f'\n{label}按{item["days"]}{unit_label}排程（行政办理时限）；依据：{item["source"]}。'
                               + ('' if item['user_provided'] else '与当地实际不同时，请提供实际天数及依据后重新运行。'))
        item['applied'] = True
    return ref


def _insert_after(tasks: list, anchor: dict, node: dict) -> dict:
    """Place node directly after anchor: anchor's successors now follow node."""
    node['id'] = max(t['id'] for t in tasks) + 1
    node['predecessors'] = f"{anchor['id']}FS"
    for t in tasks:
        links = _links(t.get('predecessors'))
        if any(pid == anchor['id'] for pid, _, _ in links):
            t['predecessors'] = _tokens([(node['id'] if pid == anchor['id'] else pid, kind, lag) for pid, kind, lag in links])
    tasks.append(node)
    return node


STEP_PHASE = {'review': '设计与审批', 'permit': '开工许可', 'fire': '验收审批'}


def _work_node(anchor: dict, uid: str, name: str, days: int, component: dict, notes: str) -> dict:
    node = dict(activity_uid=uid, name=name, duration_days=days, duration=days, milestone=False,
                outline_level=anchor.get('outline_level', 3),
                process_phase=anchor.get('process_phase') or STEP_PHASE[component['step']],
                master_phase=anchor.get('master_phase') or STEP_PHASE[component['step']],
                activity_class='PROJECT_MANAGEMENT', duration_basis='PM规划量（含缓冲），可调整',
                authority_component=component, input_notes=notes)
    # The phase must be explicit: inserted nodes sit at the end of the list, and the
    # master view would otherwise take the phase of whatever heading precedes them.
    if anchor.get('reporting_phase_override'):
        node['reporting_phase_override'] = anchor['reporting_phase_override']
    return node


def _drop_implied(tasks: list, task: dict) -> None:
    """Remove plain FS links already implied through another predecessor."""
    byid = {t['id']: t for t in tasks}

    def ancestors(tid, seen):
        for pid, _, _ in _links(byid[tid].get('predecessors')):
            if pid in byid and pid not in seen:
                seen.add(pid)
                ancestors(pid, seen)
        return seen
    links = _links(task.get('predecessors'))
    implied = set()
    for pid, _, _ in links:
        implied |= ancestors(pid, set()) if pid in byid else set()
    task['predecessors'] = _tokens([l for l in links if not (l[0] in implied and l[1] == 'FS' and not l[2])])


def _applicant(key: str) -> dict:
    uid, name, plan, buffer, reason = APPLICANT[key]
    return dict(step=key, kind='applicant', planning_days=plan, buffer_days=buffer, buffer_reason=reason,
                days=plan + buffer)


def _user_buffer(tasks, facts, key, anchor):
    days = facts.get(BUFFER_FIELDS[key])
    reason = facts.get('authority_buffer_reason')
    if not days or not reason:
        return None
    days = int(days)
    label = BUFFER_LABEL[key]
    return _insert_after(tasks, anchor, _work_node(
        anchor, f'pmp.authority.{key}_buffer', f'{label}缓冲（原因：{reason}）', days,
        dict(step=key, kind='buffer', days=days, buffer_reason=reason),
        f'用户设定的{label}缓冲{days}个工作日；原因：{reason}。可调整或删除。'))


def restructure_draft_authority(tasks: list, facts: dict) -> list:
    """Split each approval into applicant work, authority time and optional buffers."""
    from core.module_graph import add_pred, remove_nodes
    from core.release_controls import split_permit
    removed = set()

    comp = _applicant('review')
    rework_note = (f'图审意见修改与一次复审：规划{comp["planning_days"]}个工作日＋缓冲{comp["buffer_days"]}个工作日'
                   f'（{comp["buffer_reason"]}）。含申请人按意见修改、重新提交及审查机构一次复审的周转；'
                   '法定审查时限不含修改与复审；多轮复审超出部分不在主计划内，可设图审补正缓冲。')
    # DB templates hold review and rework in one task; DBB templates already have two.
    review = next((t for t in tasks if t.get('duration_days', 0) > 0 and
                   ('第三方图审机构审查' in t.get('name', '') or '递交第三方施工图审查机构审查' in t.get('name', ''))), None)
    existing_rework = next((t for t in tasks if '图审意见答复与修改' in t.get('name', '')), None)
    if review:
        review.update(name='第三方图审机构审查', authority_component=dict(step='review', kind='authority'))
        if existing_rework:
            existing_rework.update(name=APPLICANT['review'][1], duration_days=comp['days'], duration=comp['days'],
                                   authority_component=comp, duration_basis='PM规划量（含缓冲），可调整', input_notes=rework_note)
            rework = existing_rework
        else:
            rework = _insert_after(tasks, review, _work_node(
                review, APPLICANT['review'][0], APPLICANT['review'][1], comp['days'], comp, rework_note))
        rework.update(calendar='7D', ignore_holidays=True, elapsed_window=True, duration_unit='calendar_days',
                      duration_basis=(str(rework.get('duration_basis') or '') + ' 〔惯〕修改与一次复审按连续日历日规划，不占用法定审图5工作日。'))
        _user_buffer(tasks, facts, 'review', rework)

    route = fire_route(facts)
    design_review = next((t for t in tasks if '消防设计审查申报' in t.get('name', '')), None)
    design_ok = next((t for t in tasks if '消防设计审查合格意见书' in t.get('name', '')), None)
    if design_review:
        if route == 'special':
            days, source = FIRE_DESIGN_REVIEW
            design_review.update(name='特殊建设工程消防设计审查', duration_days=days, duration=days, duration_basis=source,
                                 authority_component=dict(step='fire', kind='authority', route='special', design=True, days=days))
        else:
            # Other construction: fire content is reviewed within the plan review, no separate design review.
            removed.update({design_review['id'], *( [design_ok['id']] if design_ok else [])})

    work = next((t for t in tasks if '施工许可证申报' in t.get('name', '') and t.get('duration_days', 0) > 0), None)
    result = next((t for t in tasks if t.get('name') == '正式取得施工许可证'), None)
    if work and result:
        permit = [work, result]
        shanghai = str(facts.get('city') or '').strip().lower().removesuffix('市') in {'上海', 'shanghai'}
        split_permit(tasks, permit, handling_days=2 if shanghai else None, final_compilation_after_ready=True)
        prep = next(t for t in permit if t.get('permit_role') == 'prepare')
        comp = _applicant('permit')
        prep.update(name=APPLICANT['permit'][1], duration_days=comp['days'], duration=comp['days'],
                    authority_component=comp, duration_basis='PM规划量（含缓冲），可调整')
        prep['input_notes'] = (f'资料齐备后整理签章、录入与核对：规划{comp["planning_days"]}个工作日＋缓冲{comp["buffer_days"]}个工作日'
                               f'（{comp["buffer_reason"]}）；前期收集可提前开展。行政审批时限不含此项。')
        work.update(name='施工许可行政审批', authority_component=dict(step='permit', kind='authority'))
        work.pop('permit_duration_scope', None)
        _drop_implied(tasks, work)
        _user_buffer(tasks, facts, 'permit', work)

    acc = next((t for t in tasks if '消防验收/竣工消防备案申报' in t.get('name', '')), None)
    rework = next((t for t in tasks if '消防查验意见整改复查' in t.get('name', '')), None)
    milestone = next((t for t in tasks if '消防验收合格意见书' in t.get('name', '')), None)
    if rework:
        removed.add(rework['id'])  # correction is never a default; it enters only as a stated buffer
    if acc:
        acc.pop('statutory_city_clock', None)  # historical city planning value, not a filing time
        if route == 'special':
            acc.update(name='特殊建设工程消防验收申请与现场评定',
                       duration_days=15, duration=15, calendar='7D', ignore_holidays=True,
                       elapsed_window=True, duration_unit='calendar_days',
                       duration_basis='不完整（依据待补）：消防验收15日历天为本比赛规划假设；未确认全国统一日历天解释，不作为法定办结承诺；整改复查另计。',
                       authority_component=dict(step='fire', kind='authority', route='special'))
            _user_buffer(tasks, facts, 'fire', acc)
        elif route == 'not_applicable':
            removed.add(acc['id'])
            if milestone:
                removed.add(milestone['id'])
        else:
            acc.update(name='其他建设工程消防验收备案（材料提交与备案凭证）', duration_days=1, duration=1,
                       duration_basis='PM规划量：提交备案材料；备案凭证按告知承诺即时或当场出具',
                       authority_component=dict(step='fire', kind='applicant', route=route, days=1))
            acc['input_notes'] = (str(acc.get('input_notes', '')) + '\n按其他建设工程备案排程。'
                                  + '备案后可能被抽查，抽查及整改不在主计划内，影响见交付说明。')
            if milestone:
                milestone['name'] = '取得消防验收备案凭证'
            _user_buffer(tasks, facts, 'fire', acc)
    # Filing / acceptance and the completion filing follow the owner's completion inspection.
    from core.local_authority import add_owner_completion
    owner = add_owner_completion(tasks)
    if owner:
        fire_test = next((t for t in tasks if '第三方消防设施检测' in t.get('name', '')), None)
        if fire_test:
            add_pred(owner, fire_test)
        if acc and acc['id'] not in removed:
            add_pred(acc, owner)
        for t in tasks:
            if '住建局联合竣工' in t.get('name', '') and t.get('duration_days', 0) > 0:
                add_pred(t, owner)
    if removed:
        tasks = remove_nodes(tasks, removed)
    tasks[0]['authority_fire_route'] = route
    return tasks


def _key_dates(solved: list) -> dict:
    from core.release_controls import _move_date
    permit = next((t for t in solved if t.get('permit_role') == 'result' or t.get('name') == '正式取得施工许可证'), None)
    end = next((t for t in solved if t.get('project_target')), None)
    return dict(permit=permit and permit.get('finish'), move_in=_move_date(solved), finish=end and end.get('finish'))


def _delta(a: str, b: str):
    return None if not a or not b else (date.fromisoformat(b) - date.fromisoformat(a)).days


def authority_review(prepared: list, solved: list, kickoff: str, holidays, calendar) -> dict | None:
    """What the plan includes, what it leaves out, and how a delay moves start and move-in."""
    parts = [t for t in prepared if t.get('authority_component')]
    if not parts:
        return None
    from core.solver_engine import solve_schedule
    base = _key_dates(solved)
    included = [dict(name=t['name'], step=t['authority_component']['step'], kind=t['authority_component']['kind'],
                     days=t.get('duration_days'), basis=t.get('duration_basis'),
                     planning_days=t['authority_component'].get('planning_days'),
                     buffer_days=t['authority_component'].get('buffer_days'),
                     buffer_reason=t['authority_component'].get('buffer_reason')) for t in parts]
    route = prepared[0].get('authority_fire_route')
    excluded = [dict(item='图审补正与多轮复审超出规划部分', handling='不默认计入；可设图审补正缓冲并写明原因'),
                dict(item='施工许可资料补正（审批时间顺延）', handling='不默认计入；可设施工许可补正缓冲并写明原因'),
                dict(item='消防整改与复查', handling='不默认计入；可设消防整改复查缓冲并写明原因')]
    if route in ('other', 'unknown'):
        excluded.append(dict(item='备案后被抽中检查（15个工作日）', handling='情境说明，见下方影响测算', source=SPOT_CHECK[1]))

    def rerun(change):
        variant = [dict(t) for t in prepared]  # Only top-level durations/dates are changed.
        change(variant)
        return _key_dates(solve_schedule(variant, kickoff, custom_holidays=holidays,
                                         construction_calendar_mode=calendar)['tasks'])

    scenarios = []
    for t in parts:
        comp = t['authority_component']
        if comp['kind'] != 'authority':
            continue
        def longer(v, tid=t['id']):
            node = next(x for x in v if x['id'] == tid)
            node['duration_days'] = node['duration'] = node['duration_days'] + SENSITIVITY_DAYS
        scenarios.append((f'{t["name"]}多用{SENSITIVITY_DAYS}个工作日', longer))
    filing = next((t for t in parts if t['authority_component']['step'] == 'fire'
                   and t['authority_component'].get('route') in ('other', 'unknown')), None)
    if filing:
        def spot(v, tid=filing['id']):
            node = next(x for x in v if x['id'] == tid)
            node['duration_days'] = node['duration'] = node['duration_days'] + SPOT_CHECK[0]
        # Office fit-outs are planned as ordinary construction; no special-construction scenario (2026-10-13).
        scenarios.append(('备案后被抽中检查并等待结果（15个工作日，未含整改）', spot))
    impacts = []
    for label, change in scenarios:
        dates = rerun(change)
        impacts.append(dict(scenario=label, permit=dates['permit'], move_in=dates['move_in'], finish=dates['finish'],
                            permit_delay_calendar_days=_delta(base['permit'], dates['permit']),
                            move_in_delay_calendar_days=_delta(base['move_in'], dates['move_in'])))
    return dict(fire_route=route, base=base, included=included, excluded=excluded, impacts=impacts,
                note='影响按整张网络重新计算；时差可吸收的部分不会照加到搬迁日。主计划未加入任何隐藏缓冲。')
