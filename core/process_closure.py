"""Supply and handover interfaces, shared by every industry/route assembly."""
from core.module_graph import add_pred, dependency_order
from core.master_graph import _links, _tokens


def close_process_interfaces(tasks, facts):
    report = dict(furniture={}, handover={}, absorbed_inspections=[], id_basis='Historical IDs refer to the module graph before renumbering; stable UIDs identify current tasks.')
    # Explicit bindings for the four maintained source templates. These are
    # deliverables consumed by design/procurement, not independent end branches.
    sd = next(t for t in tasks if t['name'] in ('初步设计SD出图与确认', '初步设计 SD 出图与业主方案签字确认'))
    visuals = [t for t in tasks if t['name'] in ('效果图/3D渲染出图', '材料选型确认', '效果图 3D Rendering 出图与材质选型确认')]
    for provider in visuals:
        add_pred(sd, provider)
    if facts.get('delivery') == 'DB':
        criteria = next(t for t in tasks if t['name'] == '大楼物业装修申请 & CI导则/机电图获取')
        pep = next(t for t in tasks if t['name'] == '项目执行计划 PEP 与各方确认')
        # Before brief release: property constraints and agreed procurement / execution plan.
        brief = next(t for t in tasks if t.get('activity_uid') == 'pmp.integration.brief_release')
        add_pred(brief, criteria)
        add_pred(brief, pep)
    report['design_inputs'] = {'consumer': sd['name'], 'providers': [t['name'] for t in visuals],
                               'basis': '方案签字前完成效果及材料选择；DB任务书放行纳入物业限制与PEP'}
    seq = max(t['id'] for t in tasks) + 1
    def find(needle):
        return next(t for t in tasks if needle in t['name'])
    def step(module, number):
        return next(t for t in tasks if t.get('module_id') == module and t.get('module_step') == number)
    def new(key, name, days, preds, phase, kind='PROJECT_MANAGEMENT'):
        nonlocal seq
        t = dict(id=seq, name=name, duration_days=days, duration=days, outline_level=3,
                 predecessors=_tokens([(p['id'],'FS',0) for p in preds]),
                 activity_uid='pmp.process.'+key, module_id='procurement_furniture' if phase=='设备采购' else 'handover_preparation',
                 process_phase=phase, activity_class=kind, calendar='5D',
                 duration_basis='internal_planning_allowance_pending_project_confirmation')
        seq += 1; tasks.append(t); return t
    from core.release_controls import configure_furniture
    tasks, report['furniture'] = configure_furniture(tasks, facts)
    seq = max(t['id'] for t in tasks) + 1
    # Compile in parallel with T&C; final signed records require physical completion.
    tc = next(t for t in tasks if t.get('activity_type')=='testing_commissioning')
    physical = find('物理竣工')
    cleaning = next(t for t in tasks if t.get('activity_type')=='final_cleaning')
    documents = step('core_handover',0)
    old_days = int(documents.get('duration_days',7))
    prep_days = max(1, old_days-2)
    prep = new('handover.prepare','竣工资料编制与运维培训准备',prep_days,[], '验收审批')
    prep['predecessors'] = f'{tc["id"]}SS'
    prep['input_notes'] = '整理竣工图、设备清单、操作维护资料及培训方案；可随调试推进。完成不代表最终资料已签收。'
    documents.update(name='竣工资料定稿与签收', duration_days=max(1,old_days-prep_days), duration=max(1,old_days-prep_days),
                     predecessors=f'{prep["id"]}FS,{physical["id"]}FS', process_phase='验收审批')
    training = step('core_handover',1)
    training['name'] = '运维实操培训与应急演练'
    add_pred(training,tc)
    # Cleanliness remains a condition of final takeover, not document preparation.
    add_pred(step('core_handover',2),cleaning)
    report['handover'] = dict(preparation_uid=prep['activity_uid'], documents_task_uid=documents.get('activity_uid'),
                             basis='Prepare alongside commissioning; sign final records after physical completion; cleaning gates takeover.')
    # The base industry inspection is the checklist of the same concealment release.
    for gate in list(tasks):
        if gate.get('industry_stage')!='inspection': continue
        preds = _links(gate.get('predecessors'))
        wall = next((t for t in tasks if t['id'] in {p for p,_,_ in preds} and '隐蔽工程验收' in t['name']), None)
        if wall is None or gate.get('duration_days',0) or any(k!='FS' or l for _,k,l in preds):
            raise ValueError('INDUSTRY_INSPECTION_BOUNDARY_REVIEW_REQUIRED')
        for p,_,_ in preds:
            if p!=wall['id']: add_pred(wall,p)
        wall.setdefault('embedded_industry_checks',[]).append(dict(industry=gate.get('industry'), name=gate['name'], acceptance=gate.get('acceptance')))
        wall['input_notes'] = str(wall.get('input_notes',''))+'\n同次隐蔽验收包含行业点位核对：'+str(gate.get('acceptance') or gate['name'])
        for t in tasks:
            links=_links(t.get('predecessors'))
            if any(p==gate['id'] and (k!='FS' or l) for p,k,l in links):
                raise ValueError('INDUSTRY_INSPECTION_NON_FS_CONSUMER')
            t['predecessors']=_tokens(list(dict.fromkeys((wall['id'] if p==gate['id'] else p,k,l) for p,k,l in links)))
            contract=t.get('industry_work_contract')
            if contract and contract.get('release_gate_uid')==gate.get('activity_uid'):
                contract['release_gate_uid']=wall.get('activity_uid')
        tasks.remove(gate)
        report['absorbed_inspections'].append(dict(gate_uid=gate.get('activity_uid'), into_task_uid=wall.get('activity_uid'), acceptance=gate.get('acceptance')))
    dependency_order(tasks)
    tasks[0]['process_closure']=report
    return tasks
