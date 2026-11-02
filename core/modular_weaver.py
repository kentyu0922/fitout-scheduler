"""Compose declared modules through explicit stage interfaces.

Detailed legacy activity durations are retained as planning parameters. Industry
requirements and unverified external releases are disclosed, never site facts.
"""
from __future__ import annotations
from core.module_catalog import resolve_modules, CATALOG
from core.module_graph import add_pred, remove_nodes, dependency_order
from core.sector_weaver import weave_sector_modules as legacy_weave, LONGLEAD_SPEC


def weave_sector_modules(tasks, sector='office', area_sqm=3000, facts=None):
    facts=dict(facts or {},sector=sector)
    plan=resolve_modules(facts)
    if plan['blockers']: raise ValueError('MODULE_SCOPE_BLOCKED: '+str(plan['blockers']))
    selected=set(plan['selected'])
    # Office services and basic handover are common to all five fit-out routes.
    result=legacy_weave(tasks,sector='office',area_sqm=area_sqm,facts=facts,
                        pack_override=['office_ff_move','fm_handover']+plan['packs'])
    new_id=max(t['id'] for t in result)+1
    def find(needle,required=True):
        found=next((t for t in result if needle in t['name']),None)
        if not found and required: raise ValueError('MODULE_INTERFACE_MISSING: '+needle)
        return found
    def step(module,n):
        return next(t for t in result if t.get('module_id')==module and t.get('module_step')==n)
    def group(module): return [t for t in result if t.get('module_id')==module]
    def gate(name,preds,module,condition='planning_release'):
        nonlocal new_id
        node=dict(id=new_id,name=name,outline_level=3,duration_days=0,duration=0,
                  milestone=True,calendar='5D',predecessors=','.join(str(t['id']) for t in preds),
                  module_id=module,activity_uid='pmp.interface.'+module,
                  interface_status=condition,phase='项目接口与条件确认')
        new_id+=1
        insert_at=max((result.index(t) for t in preds),default=0)+1
        result.insert(insert_at,node)
        return node
    kickoff=find('Kick Off')
    site=find('测量放线与标高复核')
    physical=find('物理竣工',False) or find('硬装施工完成')
    brief=find('(Brief)',False) or find('任务书')
    handover=step('core_handover',3)
    reloc=step('core_handover',4)
    endpoint=step('core_handover',5)
    endpoint['name']='搬迁全部完成与业务启用确认 (Day 1)'
    endpoint['project_target']=True
    handover['name']='业主搬迁入驻条件确认（内部放行）'
    # Industry-specific requirements are confirmation deliverables, not systems.
    industry=gate('行业需求与接口确认：'+plan['industry_requirements'],[brief],'industry_'+sector)
    for t in result:
        if t is industry: continue
        parts=str(t.get('predecessors','') or '').split(',')
        import re
        t['predecessors']=','.join(re.sub(r'^'+str(brief['id'])+r'(?=\D|$)',str(industry['id']),p) for p in parts)
    # Move planning starts with the brief; it actually gates the replacement move.
    moveplan=step('office_ff_move',4)
    moveplan['predecessors']=str(industry['id'])
    add_pred(reloc,moveplan)
    # IT/AV terminal installation uses workfront readiness, not all furniture.
    av=step('office_ff_move',2)
    av['predecessors']=str(physical['id'])

    # Do not hard-code unusually high engineering criteria into generic scopes.
    if 'A01' in selected:
        step('tdd',2)['name']='大楼多专业现状勘测与项目容量条件核查'
        # PEP and requirement gathering remain available during TDD.
        lease=step('tdd',5)
        for t in result:
            if t is industry or t.get('module_id')=='tdd': continue
            if 'PEP' in t['name'] or '物业装修申请' in t['name'] or t is brief:
                t['predecessors']=str(kickoff['id'])
        add_pred(industry,lease)
    if 'A04' in selected:
        step('datacenter',1)['name']='核心机房区域基础、围护与防尘条件施工'
        step('datacenter',2)['name']='机房冷却系统安装与检查（配置按批准设计）'
        step('datacenter',3)['name']='机房适用消防设施安装与接口检查'
        step('datacenter',4)['name']='机房配电及UPS安装（容量与冗余按批准设计）'
        # Independent installation workfronts; coordination happens at integrated testing.
        step('datacenter',4)['predecessors']=str(step('datacenter',1)['id'])
        for n in (2,3,4,5,6):add_pred(step('datacenter',7),step('datacenter',n))
        step('datacenter',7)['name']='机房负载与接口性能测试（工况按批准方案）'
        step('datacenter',8)['name']='核心机房系统验收与部署条件就绪 (DC Ready)'
        add_pred(handover,step('datacenter',8))
    if 'A05' in selected:
        step('it_cutover',1)['predecessors']=str(industry['id'])
        target=step('datacenter',8) if 'A04' in selected else gate(
            '目标IT环境与恢复条件核验（既有／他方，待确认）',[industry],'external_it_environment','unverified_external')
        step('it_cutover',2)['predecessors']=str(step('it_cutover',1)['id'])+','+str(target['id'])
    if 'A06' in selected or 'A07' in selected:
        drop=set()
        if 'A06' not in selected:
            drop.update(step('mocap',i)['id'] for i in (3,4))
        if 'A07' not in selected and 'A06' not in selected:
            drop.update(step('mocap',i)['id'] for i in (1,2,5,6))
        if 'A06' in selected and 'A07' in selected:
            step('mocap',5)['predecessors']=str(step('mocap',2)['id'])
            add_pred(step('mocap',4),step('mocap',5))
            step('mocap',6)['predecessors']=str(step('mocap',4)['id'])
        result=remove_nodes(result,drop)
        label='动捕与声学' if {'A06','A07'}<=selected else '动捕' if 'A06' in selected else '专业声学'
        step('mocap',0)['name']=label+'专项工程'
        step('mocap',7)['name']=label+'功能验收与使用条件就绪'
        if 'A07' in selected:
            step('mocap',6)['name']='专业声学性能检测与复核（指标按批准设计）'
    if 'A08' in selected:
        dev=step('dev_zone',0)
        dev['name']='高密研发区供电冷却与工位设施安装（按已确认负荷）'
        dev['predecessors']=str(site['id'])
        add_pred(physical,dev)
        for t in result:
            if '隐蔽工程验收' in t['name']: add_pred(t,dev)
    if 'A10' in selected or 'A11' in selected:
        if 'A11' not in selected:
            result=remove_nodes(result,{step('vault_security',1)['id']})
            step('vault_security',0)['name']='强化物理安防系统'
        step('vault_security',4)['name']='选定安防范围测试与接管验收'
    if 'A14' in selected or 'A15' in selected:
        if 'A14' not in selected:
            result=remove_nodes(result,{step('cda_piping',1)['id']})
        elif 'A15' not in selected:
            result=remove_nodes(result,{step('cda_piping',2)['id']})
        else:
            step('cda_piping',2)['predecessors']=str(step('cda_piping',0)['id'])
            add_pred(step('cda_piping',3),step('cda_piping',1))
        label='工艺气体与CDA' if {'A14','A15'}<=selected else '工艺气体' if 'A14' in selected else 'CDA'
        step('cda_piping',0)['name']=label+'系统'
        step('cda_piping',3)['name']=label+'质量与安全性能测试（按批准方案）'
        step('cda_piping',4)['name']=label+'系统验收'
    if 'A13' in selected:
        step('cleanroom_iso',3)['name']='洁净环境性能检测（等级与状态按批准方案）'
        step('cleanroom_iso',4)['name']='洁净环境测试通过与使用条件确认'
        hvac=step('lab_hvac',4) if 'A12' in selected else gate(
            '洁净区HVAC及关联设施运行条件核验（待确认）',[industry],'external_cleanroom_hvac','unverified_external')
        add_pred(step('cleanroom_iso',3),hvac)
    if 'A17' in selected:
        step('anti_vibration',3)['name']='设备区微振动测试（限值按设备要求）'
        step('anti_vibration',0)['name']='设备区微振动控制'

    # Every enabled specialist output controls the declared project handover.
    for module in ('trading_desk','vault_security','lab_hvac','cleanroom_iso','anti_vibration','cda_piping','esd_grounding'):
        members=group(module)
        if members: add_pred(handover,members[-1])

    # Equipment receipts are individual releases, not one all-equipment barrier.
    design=find('施工图全套确认',False) or find('全专业施工图深化出图',False)
    if design is None: raise ValueError('MODULE_INTERFACE_MISSING: design release')
    # Specialist equipment is ordered when the specialist design is frozen; waiting for the
    # GC contract only delays delivery (specialist suppliers are separate orders).
    def purchase(label,days,target,source):
        nonlocal new_id
        t=dict(id=new_id,name='专项设备采购与到货验收：'+label,outline_level=3,
               duration_days=days,duration=days,calendar='5D',
               predecessors=f'{design["id"]}',module_id='module_procurement',
               activity_uid='pmp.procurement.'+source,phase='专项设备采购',
               duration_basis='legacy LONGLEAD_SPEC.'+source+'; unverified supplier planning allowance')
        new_id+=1;result.insert(result.index(design)+1,t);add_pred(target,t)
    # UPS/精密空调/气灭/AHU/FFU/高效/排风柜/金库门/纯化/隔振台/专业设备：交期改由 intake 字段驱动（默认待合同回填）。
    if 'A06' in selected:
        purchase('动捕相机（交货周数不完整，待项目确认）', 0, step('mocap',4), 'gaming.1')
        rec = next(t for t in result if t.get('activity_uid')=='pmp.procurement.gaming.1')
        rec.update(milestone=True, duration_days=0, duration=0,
                   duration_basis='〔不完整〕动捕无官方交货周数，待合同回填。')
    if 'A07' in selected: purchase('专业声学材料（交货周数不完整）', 0, step('mocap',1), 'gaming.2')
    if 'A09' in selected: purchase('交易终端（交货周数不完整）', 0, step('trading_desk',3), 'finance.1')
    if 'A10' in selected or 'A11' in selected: purchase('安防设备（交货周数不完整）', 0, step('vault_security',2), 'finance.2')
    if 'A14' in selected: purchase('工艺气体设备（交货周数不完整）', 0, step('cda_piping',1),'semiconductor.0')
    if 'A15' in selected: purchase('CDA机组（交货周数不完整）', 0, step('cda_piping',2),'semiconductor.1')

    for i in plan['external']:
        external=gate(CATALOG[i]['label']+'外部交付／复用核验（待确认）',[industry],'external_'+i,'unverified_external')
        add_pred(handover,external)
    from core.module_lifecycle import attach_lifecycle
    result=attach_lifecycle(result,facts,plan)
    from core.scope_interfaces import attach_scope_interfaces
    result=attach_scope_interfaces(result,plan)
    from core.industry_base import attach_industry_base
    result=attach_industry_base(result,facts,plan)
    from core.modular_procurement import apply_modular_procurement
    result=apply_modular_procurement(result,facts)
    from core.module_integration import integrate_modules
    result=integrate_modules(result,facts,plan)
    from core.specialist_integration import complete_specialist_interfaces
    result=complete_specialist_interfaces(result,facts,plan)
    from core.process_closure import close_process_interfaces
    result=close_process_interfaces(result,facts)
    from core.work_duration_review import apply_work_duration_review
    result=apply_work_duration_review(result)
    from core.iaq_boundary import apply_iaq_boundary
    result=apply_iaq_boundary(result)
    from core.specialist_sequence import refine_mocap
    result=refine_mocap(result,facts,selected)
    from core.acoustic_finish import apply_acoustic_finish
    result=apply_acoustic_finish(result,facts,selected)
    dependency_order(result) # fail on missing interfaces or cycles
    if result:
        result[0]['module_plan']=plan
        result[0]['module_conditions']=[dict(name=t['name'],status=t['interface_status']) for t in result if t.get('interface_status')=='unverified_external']
    return result
