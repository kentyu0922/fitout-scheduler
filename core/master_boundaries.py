"""Reporting packages are not inferred contracts or new execution scope."""
from collections import OrderedDict

def procurement_group(task):
    obj=task.get('reporting_procurement_object')
    module=task.get('module_id','')
    if not obj:
        obj={'procurement_design':'设计服务','procurement_gc':'施工总包','procurement_db':'设计施工总包'}.get(module)
    if module=='procurement_gc' and any(s in task.get('name','') for s in ('DB总承包','设计施工一体')):
        obj='设计施工总包'
    if not obj:
        obj={'设计采购':'设计服务','施工采购':'施工总包'}.get(task.get('master_phase'))
    if obj:return obj+'招采','defined_route'
    if task.get('module_id')=='office_ff_move' or '家具' in task.get('name',''):
        return '家具供货','supply_stream_contract_boundary_pending'
    return '其他设备与专项供货','aggregate_supply_stream_not_a_single_contract'

def nest_procurement(members, next_id):
    """Keep packages contiguous; maintain existing date order within each one."""
    groups=OrderedDict()
    for task in members:
        label,basis=procurement_group(task)
        groups.setdefault((label,basis),[]).append(task)
    output=[]
    for index,((label,basis),children) in enumerate(groups.items(),1):
        summary=dict(id=next_id,name=label,client_task_name=label,
                     activity_uid=f'pmp.master.procurement_group.{index}',
                     outline_level=3,level=3,summary=True,milestone=False,
                     duration=0,duration_days=0,predecessors='',
                     start=min(t.get('start') or t['start_date'] for t in children),
                     finish=max(t.get('finish') or t['finish_date'] for t in children),
                     reporting_phase='招采阶段',master_phase='招采阶段',
                     client_visible=True,client_presentation=True,
                     package_boundary_basis=basis,
                     input_notes='采购/供货汇报分组；不表示合同已经划分或签署。其他设备与专项供货可包含多个合同，须按项目发包边界细分。实际依赖只连接执行任务。')
        next_id+=1;output.append(summary)
        if children and all(t.get('procurement_profile')=='enterprise_3round_tech_commercial' for t in children):
            from core.procurement_pm_view import group_rounds
            rounds,next_id=group_rounds(children,next_id,label,basis)
            output.extend(rounds)
            continue
        for child in children:
            child.update(outline_level=4,level=4,reporting_package=label,package_boundary_basis=basis)
            # Object is already visible in the parent; retain meaningful object
            # names inside otherwise ambiguous equipment/aggregate supply rows.
            if basis=='defined_route':
                prefix=label.removesuffix('招采')+'：'
                if child['client_task_name'].startswith(prefix):child['client_task_name']=child['client_task_name'][len(prefix):]
            output.append(child)
    return output,next_id

def boundary_review(tasks):
    """Expose observed gaps without inventing work, durations or approval facts.

    Checks intentionally focus on known master boundaries. No findings does not
    certify the full process network. IDs refer to this schedule, not MPP rows.
    """
    from core.master_graph import _links
    leaves=[t for t in tasks if not t.get('summary') and t.get('outline_level',3)>1]
    byid={t['id']:t for t in leaves};findings=[]
    def ancestors(task):
        found=set();todo=[task['id']]
        while todo:
            for pid,_,_ in _links(byid[todo.pop()].get('predecessors')):
                if pid in byid and pid not in found:found.add(pid);todo.append(pid)
        return found
    def add(code,rows,observation,needed):
        findings.append(dict(code=code,status='open',task_ids=[t['id'] for t in rows],
                             observation=observation,required_resolution=needed))
    furniture=[t for t in leaves if t.get('activity_type')=='furniture']
    receipts=[t for t in leaves if t.get('module_id')=='office_ff_move' and t.get('module_step')==0]
    production=[t for t in leaves if t.get('furniture_supply') or '家具' in t.get('name','') and any(w in t['name'] for w in ('生产','交货周期','Lead Time','状态与适配核查','供货就绪'))]
    for receipt in receipts:
        if not receipt.get('external_supply_condition') and not any(t['id'] in ancestors(receipt) for t in production):
            add('FURNITURE_SUPPLY_RELEASE',[receipt], '家具到货未连接可识别的生产交货完成条件。',
                '确认选型、定标/下单及生产交货链；到货同时具备供货和现场接收条件，不能凭空填工期。')
    flush=[t for t in leaves if '强排通风与空气净化散味' in t.get('name','')]
    for task in flush:
        if furniture and not all(t['id'] in ancestors(task) for t in furniture):
            add('IAQ_TEST_STATE',[task,*furniture], '现有散味窗口未受家具安装完成约束；它不能代表家具完成后的完整散味窗口。',
                '明确检测要求及测试状态，区分可提前通风与家具完成后适用的等待/检测条件；核对最终检测的前置。')
    for task in leaves:
        if task.get('module_id')=='core_handover' and task.get('module_step')==0:
            immediate=[byid[p] for p,_,_ in _links(task.get('predecessors')) if p in byid]
            if any(t.get('activity_type')=='final_cleaning' for t in immediate):
                add('HANDOVER_PREPARATION',[task], '资料移交工作整体排在最终保洁之后，尚未区分提前编制与最终移交。',
                    '核对资料编制、审阅、培训准备和最终签收是否需分开；实际演练仍须运行条件。')
        if task.get('industry_stage')=='inspection':
            add('INSPECTION_RELEASE_PURPOSE',[task], '行业接口核验仍独立占行；程序未证明它有区别于前置检查的独立批准目的。',
                'PM确认其放行对象及证据；同一检查合并完成条件，独立批准则保留。')
    for task in leaves:
        if task.get('reporting_phase')=='招采阶段' and task.get('package_boundary_basis') not in {None,'defined_route'}:
            task['procurement_boundary_status']='pending_project_packaging'
    return dict(status='OPEN_REVIEW_ITEMS' if findings else 'NO_FINDINGS_IN_CHECKED_RULES',
                scope='Known supply, testing-state, handover-preparation and inspection boundaries only; not full professional signoff.',
                process_signoff=False,findings=findings)
