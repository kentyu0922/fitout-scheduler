"""Remove an undeclared ventilation wait; retain actual installation interfaces."""
from core.master_graph import _links, _tokens
from core.module_graph import dependency_order

# GB 50325-2020: indoor pollutant testing at least 7 days after completion, before use.
COMPLETION_REST = (7, 'GB 50325-2020《民用建筑工程室内环境污染控制标准》：验收检测应在工程完工至少7d以后、交付使用前进行（条号待核）')


def apply_iaq_boundary(tasks):
    from core.module_graph import add_pred
    first = next((t for t in tasks if '首次室内空气盲测' in t.get('name','')),None)
    if first:
        first.update(duration_days=1,duration=1,milestone=False,iaq_role='first_sampling',
                     duration_basis='现场采样暂留1工作日；检测点位与进场窗口待检测机构确认')
        first['input_notes']=str(first.get('input_notes',''))+'\n本工序只含现场采样；采样条件须检测机构确认。活动家具可在采样完成后进场，不等报告；固定家具按装修检测范围处理。'
        # Sampling condition, not a ventilation wait: test at least 7 calendar days after completion.
        # A visible 7-day rest on a 7-day calendar, then sampling on the testing (working-day) calendar.
        physical = next((t for t in tasks if '物理竣工' in t.get('name', '')), None)
        if physical:
            rest = dict(id=max(t['id'] for t in tasks) + 1, activity_uid='pmp.iaq.completion_rest',
                        name='完工静置期（GB 50325：完工至少7日后检测）', duration_days=COMPLETION_REST[0],
                        duration=COMPLETION_REST[0], milestone=False, outline_level=3,  # appended: a deeper level would turn its neighbour into a summary
                        predecessors=f'{physical["id"]}FS', activity_class='LOGISTICS_DELIVERY',
                        process_phase=first.get('process_phase') or '验收审批', iaq_role='completion_rest',
                        duration_basis=COMPLETION_REST[1],
                        input_notes='按日历天计算的采样条件，不是通风等待；不占用施工或检测资源。')
            for key in ('master_phase', 'reporting_phase_override'):
                if first.get(key):
                    rest[key] = first[key]
            tasks.append(rest)
            links = [l for l in _links(first.get('predecessors')) if l[0] != physical['id']]
            links.append((rest['id'], 'FS', 0))
            first['predecessors'] = _tokens(links)
            first['input_notes'] += '\n采样排在“完工静置期”（完工后7个日历天）之后，再对齐检测机构工作日。'
        receipt=next((t for t in tasks if t.get('module_id')=='office_ff_move' and t.get('module_step')==0),None)
        if receipt:add_pred(receipt,first)
        report_node=next((t for t in tasks if t.get('iaq_role')=='first_report'),None)
        if report_node is None:
            report_node=dict(id=max(t['id'] for t in tasks)+1,activity_uid='pmp.iaq.first_report',name='首次空气检测报告与结果核对',
                duration_days=3,duration=3,milestone=False,outline_level=3,predecessors=f'{first["id"]}FS',
                module_id='iaq_results',process_phase='验收审批',master_phase='验收审批',reporting_phase_override='竣工验收阶段',
                activity_class='CONSULTANT_REVIEW',iaq_role='first_report',
                duration_basis='报告周转及结果核对暂留3工作日，非规范规定，须检测机构确认',
                input_notes='与家具进场安装并行；不合格须另行评估整改复检，不将报告生成等同检测合格。')
            tasks.append(report_node)
    installations = [t for t in tasks if t.get('module_id') == 'office_ff_move'
                     and t.get('module_step') in (1, 2)]
    flush = [t for t in tasks if '强排通风与空气净化散味' in t.get('name', '')]
    removed = []
    for node in flush:
        # Only project-specific days AND a stated basis can create this activity.
        if node.get('explicit_ventilation_window'):
            links = _links(node.get('predecessors'))
            links.extend((t['id'], 'FS', 0) for t in installations)
            node['predecessors'] = _tokens(list(dict.fromkeys(links)))
            continue
        incoming = _links(node.get('predecessors'))
        if any(kind != 'FS' or lag for _, kind, lag in incoming):
            raise ValueError('IAQ_BOUNDARY: ventilation has non-FS/lagged incoming logic; review required')
        for task in tasks:
            links = _links(task.get('predecessors'))
            replacement = []
            for pid, kind, lag in links:
                if pid == node['id']:
                    if kind != 'FS' or lag:
                        raise ValueError('IAQ_BOUNDARY: ventilation has non-FS/lagged outgoing logic; review required')
                    replacement.extend(incoming)
                else:
                    replacement.append((pid, kind, lag))
            task['predecessors'] = _tokens(list(dict.fromkeys(replacement)))
        tasks.remove(node)
        removed.append(dict(id=node['id'], name=node['name'], duration_days=node.get('duration_days')))
    second = next((t for t in tasks if '二次室内空气质量复测' in t.get('name', '')), None)
    if second:
        first = next((t for t in tasks if '首次室内空气盲测' in t.get('name','')),None)
        if first:
            from core.module_graph import add_pred
            add_pred(second,first)
            add_pred(second,report_node)
        links = _links(second.get('predecessors'))
        links.extend((t['id'], 'FS', 0) for t in installations)
        second['predecessors'] = _tokens(list(dict.fromkeys(links)))
        second['input_notes'] = str(second.get('input_notes', '')) + '\n检测前应核实装修、家具及现场采样条件；不预设通风等待天数，不代表安装结束即可检测合格。'
        second['iaq_sampling_conditions_status'] = 'pending_project_confirmation'
    report = dict(removed_default_waits=removed, fixed_ventilation_wait_default=False,
                  basis='用户确认：通风为验收到搬迁期间的现场管理事项，不单列默认工序或等待约束。')
    dependency_order(tasks)
    if tasks:
        tasks[0]['iaq_boundary_review'] = report
    return tasks
