"""Versioned Shanghai fit-out routes from declared project classifications.

No classification is inferred from industry. Unsupported/missing facts return
questions. Missing facts proceed as a marked diagnostic draft unless the user
declined the draft (authority_draft explicitly false); conflicts always stop.
"""
from core.authority_boundary import SH_RULE, SH_SMALL, NATIONAL_FIRE
from core.module_graph import add_pred, dependency_order, remove_nodes
from core.master_graph import _links, _tokens

SH_FIRE_RULE='https://zjw.sh.gov.cn/gfxwj/20240329/515ff61a630f42a9a85a95139b7733c0.html'
BUSINESS_RULE='https://ghzyj.sh.gov.cn/yhyshjxd/zcwj/new/20250807/b636dcb114474d1c88d94ae2fc940f07.html'
FIELDS={
    'building_context': {'existing_nonresidential','other','unknown'},
    'renovation_category': {'simple','structural_or_facade','fire_changes','out_of_scope','unknown'},
    'authority_special_policy': {'none','business_simple','other','unknown'},
    'fire_classification': {'special','other','not_applicable','unknown'},
    'fire_sampling': {'not_selected','selected','pending'},
    'building_safety_assessment': {'required','not_required','unknown'},
}
QUESTIONS={
    'building_context':'是否为已投入使用的合法非居住类既有建筑？',
    'renovation_category':'设计单位核定为简易装修、局部结构/外立面变动、消防变动，还是用途/主要结构改变等另行审批工程？',
    'authority_special_policy':'是否适用商务楼宇更新简易装修等专项政策？如没有，请选择“无其他专项政策（已核对）”。',
    'fire_classification':'消防主管部门/设计单位核定为特殊建设工程、其他建设工程，还是本次无消防审验事项？',
    'authority_basis':'请提供工程分类依据，例如设计单位确认、申报指导或既有审批文件名称。',
    'building_safety_assessment':'结合房龄、结构、使用年限和历次改造，设计单位是否已核定安全性及适用抗震鉴定要求？',
}


def draft_declined(facts):
    value = facts.get('authority_draft')
    return value is False or str(value).strip().lower() in ('false', 'no', '0', '否')


def draft_allowed(facts, decision):
    """One rule for entry and engine: draft on missing facts, never on conflict or refusal."""
    return decision['status'] != 'CONFLICT' and not draft_declined(facts)


def _resolve_shanghai(facts):
    result=dict(status='NEEDS_FACTS',questions=[],sources=[SH_RULE,NATIONAL_FIRE,SH_FIRE_RULE],rule_version='SH_2025_12',
                verified_on='2026-10-12',classification_source=facts.get('authority_basis'),classification_independently_verified=False)
    def missing(key):result['questions'].append(dict(field=key,question=QUESTIONS.get(key,key)))
    if str(facts.get('city','')).strip().lower() not in ('上海','上海市','shanghai'):
        result.update(status='NEEDS_LOCAL_RULE',questions=[dict(field='city',question='本版自动报审分支已核实上海；此城市需核实当地规则：许可限额与适用性、图审及消防路径、申报主体及承包单位资料、办理时限与官方依据有效期；不能套上海结论。')]);return result
    for key in ('building_context','renovation_category','authority_special_policy','building_safety_assessment','authority_basis'):
        if facts.get(key) in (None,'','unknown'):missing(key)
    if facts.get('renovation_category') not in ('simple',) and facts.get('fire_classification') in (None,'','unknown'):
        missing('fire_classification')
    if result['questions']:return result
    if facts['building_context']!='existing_nonresidential' or facts['renovation_category']=='out_of_scope' or facts['authority_special_policy']=='other':
        result.update(status='NEEDS_LOCAL_RULE',questions=[dict(field='renovation_category',question='项目超出本版既有非居住装修规则；需核实用途、结构或专项审批路径。')]);return result
    when=facts.get('authority_rule_date') or facts.get('start_date') or '2026-10-12'
    if not '2025-12-01' <= when <= '2030-06-30':
        result.update(status='NEEDS_LOCAL_RULE',questions=[dict(field='authority_rule_date',question='拟适用日期不在已核实上海规则有效期内，请更新官方依据。')]);return result
    if facts.get('fire_classification') in ('other','special') and not '2024-01-01' <= when <= '2028-12-31':
        result.update(status='NEEDS_LOCAL_RULE', questions=[dict(field='authority_rule_date', question='拟适用日期超出已核实沪住建规范〔2023〕18号有效期，消防路径及窗口须更新依据。')]);return result
    result['applicability_date']=when
    simple=facts['renovation_category']=='simple'
    if simple and facts.get('addon_a02') in ('required','需要'):
        result.update(status='CONFLICT',questions=[dict(field='renovation_category',question='简易装修与已选结构改造/加固冲突，请核定工程分类。')]);return result
    if simple and facts.get('site_condition') in ('shell','full_strip'):
        result.update(status='CONFLICT',questions=[dict(field='site_condition',question='毛坯/全拆的完整装修范围与仅翻新的简易装修分类不一致，请核定现场与审批范围。')]);return result
    if simple and any(facts.get('addon_a'+str(i).zfill(2)) in ('required','需要') for i in range(2,23) if i not in (19,21)):
        result.update(status='NEEDS_LOCAL_RULE',questions=[dict(field='renovation_category',question='已选专项超出本版简易翻新模板，需要核实专项范围和审批分类。')]);return result
    area=float(facts.get('area_sqm',0));cost=facts.get('cost_10k_rmb')
    business=facts['authority_special_policy']=='business_simple'
    if business and not simple:
        result.update(status='CONFLICT',questions=[dict(field='authority_special_policy',question='商务楼宇简易装修豁免与非简易装修分类冲突。')]);return result
    if business and cost is None:
        result['questions']=[dict(field='cost_10k_rmb',question='核定商务楼宇简易装修许可豁免需要总投资额。')];return result
    exempt=business and area<500 and float(cost)<=100
    if not exempt and area<300 and (cost is None or float(cost)<100):
        result.update(status='NEEDS_LOCAL_RULE',sources=[SH_SMALL],questions=[dict(field='area_sqm',question='限额以下项目需另核实街镇信息报送及一般项目消防条件；不套用常规施工许可链。')]);return result
    fire=facts.get('fire_classification','unknown')
    if simple:
        if fire in ('special','other'):
            result.update(status='CONFLICT',questions=[dict(field='fire_classification',question='简易装修无本次消防变动，与声明消防审验路径冲突，请设计单位核定。')]);return result
        fire='not_applicable'
    elif fire=='unknown':
        missing('fire_classification');return result
    if facts['renovation_category']=='fire_changes' and fire=='not_applicable':
        result.update(status='CONFLICT',questions=[dict(field='fire_classification',question='消防设计变动不能同时声明无消防审验事项。')]);return result
    if fire!='other' and facts.get('fire_sampling')=='selected':
        result.update(status='CONFLICT',questions=[dict(field='fire_sampling',question='备案抽查仅用于其他建设工程备案路径。')]);return result
    result.update(status='READY',route='sh_business_simple' if exempt else 'sh_simple' if simple else 'sh_reviewed_'+fire,
                  drawing_review=not simple,permit_required=not exempt,fire_route=fire,
                  building_assessment_required=facts['building_safety_assessment']=='required',
                  completion_filing=not simple,sampling=facts.get('fire_sampling','pending'))
    if business:result['sources'].append(BUSINESS_RULE)
    return result


def resolve_local_authority(facts):
    from core.authority_registry import resolve
    return resolve(facts)


FILING_DAYS=(1,'PM规划量：提交备案材料；备案凭证按告知承诺即时或当场出具（见 references/authority_reference_verification.md）')


def add_owner_completion(tasks):
    """Owner's completion inspection: the whole site is finished and the client walks it.

    Punch-list items raised there are fixed on site in parallel and get no duration.
    The first indoor-air result is a precondition (GB 50325 acceptance testing)."""
    physical=next((t for t in tasks if '物理竣工' in t.get('name','')),None)
    if physical is None:return None
    documents=next((t for t in tasks if t.get('module_id')=='core_handover' and t.get('module_step')==0),None)
    report=next((t for t in tasks if t.get('iaq_role')=='first_report'),None)
    preds=[physical,*([documents] if documents else []),*([report] if report else [])]
    owner=dict(id=max(t['id'] for t in tasks)+1,name='业主组织工程竣工验收',duration_days=2,duration=2,
               predecessors=','.join(f'{t["id"]}FS' for t in preds),outline_level=3,
               process_phase='验收审批',activity_class='CLIENT_APPROVAL',
               activity_uid='pmp.authority.owner_completion',duration_basis='planning_allowance_pending_project_confirmation',
               input_notes='全场完工后，建设单位约业主/客户到场组织竣工验收；需实体完工、竣工资料、适用测试记录及首次室内空气检测结果。'
                           '验收提出的整改消项（punch list）现场并行修复，不另列工期。家具、入住检测与搬迁另有条件，不能将本节点视为全部入驻条件具备。')
    tasks.append(owner)
    return owner


def apply_local_authority(tasks, facts):
    decision=resolve_local_authority(facts)
    if decision['status']!='READY':
        if not draft_allowed(facts, decision):
            import json
            raise ValueError('AUTHORITY_INPUT_REQUIRED: '+json.dumps(decision,ensure_ascii=False))
        for t in tasks:
            if any(s in t['name'] for s in ('图审','消防设计审查','许可证','消防验收','消防备案','联合验收','联合竣工')):
                t['input_notes']=str(t.get('input_notes',''))+'\n诊断草案：属地报审路径未核实，不代表适用或已批准。'
        from core.authority_reference import apply_reference_days, restructure_draft_authority
        tasks=restructure_draft_authority(tasks,facts)
        decision['reference_days']=apply_reference_days(tasks,facts)
        decision['status']='EXPLICIT_DIAGNOSTIC_DRAFT';tasks[0]['local_authority']=decision
        return tasks,decision
    if decision.get('jurisdiction',{}).get('adapter')=='sip':
        from core.sip_authority import apply_sip
        return apply_sip(tasks,facts,decision)
    def find(needle):return next(t for t in tasks if needle in t['name'])
    drawings=find('全专业施工图')
    release=find('施工图全套确认')
    submit=next(t for t in tasks if '递交第三方施工图审查' in t['name'] or '第三方图审机构审查与意见修改' in t['name'])
    reply=next((t for t in tasks if '第三方专项图审意见' in t['name']), None)
    fd=[t for t in tasks if '消防设计审查' in t['name']]
    permit=[t for t in tasks if t.get('outline_level',3)>=3 and ('政府施工许可证' in t['name'] or '正式取得施工许可证' in t['name'])]
    fire=[t for t in tasks if any(s in t['name'] for s in ('第三方消防设施检测','住建主管部门消防验收','消防查验意见整改','取得《建设工程消防验收'))]
    completion=[t for t in tasks if t.get('outline_level',3)>=3 and any(s in t['name'] for s in ('住建局联合竣工','竣工备案 / 联合验收通过'))]
    physical=find('物理竣工')
    physical['completion_scope_caption']='物理竣工（装修机电实体完工）'
    physical['input_notes']=str(physical.get('input_notes',''))+'\n本节点仅为装修机电实体完成；业主组织验收、适用报审、家具和入驻条件分别控制。'
    # Construction completion alone is not the owner's statutory completion inspection.
    owner=add_owner_completion(tasks)
    removed=set()
    if facts['renovation_category']=='simple':
        # A simple refresh cannot keep the full-fitout partition/fire installation scope.
        forbidden={'partition_framing','gypsum_boarding','glass_partition','sprinkler','fire_alarm'}
        removed.update(t['id'] for t in tasks if t.get('activity_type') in forbidden)
        for t in tasks:
            t['simple_renovation']=True
            if '隔墙及主管管线隐蔽' in t['name']:t['name']='翻新管线隐蔽验收'
        decision['physical_scope_basis']='简易翻新模板：不新增隔墙布局、消防喷淋或报警安装；保留饰面、天花、门窗及管线末端翻新，具体范围仍须按批准图纸核实。'
    if facts['building_safety_assessment']=='required':
        seq=max(t['id'] for t in tasks)+1
        kickoff=find('Kick Off')
        assessment=dict(id=seq,name='原建筑资料核查、安全性及适用抗震鉴定',duration_days=5,duration=5,
                        predecessors=f'{kickoff["id"]}FS',outline_level=3,process_phase='设计与审批',
                        activity_uid='pmp.authority.building_assessment',activity_class='CONSULTANT_REVIEW',
                        duration_basis='provisional_assessor_lead_to_confirm',
                        input_notes='鉴定范围按专业核定；报告作为施工图设计和审查依据。5天为未校准规划预留，不是法定时限。')
        tasks.append(assessment);add_pred(drawings,assessment)
    if not decision['drawing_review']:
        removed.add(submit['id'])
        if reply:removed.add(reply['id'])
        release['name']='施工图内部审查完成'
        release['milestone']=True
    else:
        submit['name']='施工图送审与审查' if reply else '施工图送审、意见答复与复核'
        if reply:reply['name']='图审意见答复、修改与复核'
        # 5 working days is the authority service limit, NOT this package's total duration.
        if reply:
            submit.update(duration_days=5,duration=5,duration_basis='沪建规范〔2025〕12号第七条：适用施工图审查5工作日；修改与复核另列')
        else:
            submit['duration_basis']='合并工作包暂排8工作日：受理后审查5工作日＋申请人修改复核3工作日PM暂估；不把8天称为法定时限'
            submit.update(duration_days=8,duration=8)
            submit['authority_component']={'kind':'combined','step':'review','authority_workdays':5,'applicant_provisional_workdays':3}
        submit['source_url']=SH_RULE
        submit['authority_service_limit_days']=5
        submit['authority_service_limit_basis']='受理后的办理时限；资料准备、修改不包含在此数值内。'
    if decision['fire_route']!='special':removed.update(t['id'] for t in fd)
    elif not fd:
        seq=max(t['id'] for t in tasks)+1
        work=dict(id=seq,name='消防设计审查申报与意见答复',duration_days=15,duration=15,
                  predecessors=f'{release["id"]}FS',outline_level=3,process_phase='开工许可',
                  activity_class='AUTHORITY_APPROVAL',activity_uid='pmp.authority.fire_design_work',
                  duration_basis='planning_allowance_pending_project_confirmation')
        approved=dict(id=seq+1,name='取得消防设计审查合格意见',duration_days=0,duration=0,milestone=True,
                      predecessors=f'{seq}FS',outline_level=3,process_phase='开工许可',
                      activity_class='AUTHORITY_APPROVAL',activity_uid='pmp.authority.fire_design_result')
        tasks.extend([work,approved]);fd=[work,approved]
        for t in permit:add_pred(t,approved)
    if decision['fire_route']=='special':
        for t in fd:
            if t.get('duration_days',0)>0:t.update(duration_days=15,duration=15,duration_basis='住建部令第58号修正后第二十三条：受理后15工作日；特殊消防设计专家评审等另行核定',source_url=NATIONAL_FIRE)
    if decision['fire_route']=='not_applicable':removed.update(t['id'] for t in fire)
    elif decision['fire_route']=='other':
        for t in fire:
            if '第三方消防' in t['name']:
                t['name']='消防设施自查、功能测试与备案资料整理'
            elif '住建主管部门' in t['name']:
                # Filing is not acceptance: no inspection time, no historical city clock.
                t.update(name='消防验收备案申报',duration_days=FILING_DAYS[0],duration=FILING_DAYS[0],duration_basis=FILING_DAYS[1])
                t.pop('statutory_city_clock',None)
            elif '消防查验意见整改' in t['name']:
                removed.add(t['id'])
            elif '取得《' in t['name']:
                t['name']='消防备案完成'
                t['fire_role']='filing_result'
        decision['sampling_note']='pending输出双情境；抽查、整改与内部入驻放行分开。未默认增加整改；不把内部保守政策当法律结论。'
    else:
        for t in fire:
            if '住建主管部门' in t['name']:t['name']='消防验收申报与现场评定'
            elif '取得《' in t['name']:t['name']='取得消防验收合格意见'
            if '消防验收申报与现场评定'==t['name']:
                t.update(duration_days=15,duration=15,calendar='7D',ignore_holidays=True,elapsed_window=True,
                         duration_unit='calendar_days',
                         duration_basis='不完整（依据待补）：消防验收15日历天为本比赛规划假设；未确认全国统一日历天解释，不作为法定办结承诺；整改复查另计。',source_url=SH_FIRE_RULE)
            if '消防查验意见整改' in t['name']:
                days=int(facts.get('fire_rectification_days',0))+int(facts.get('fire_recheck_days',0))
                if not days:removed.add(t['id'])
                else:t.update(duration_days=days,duration=days,duration_basis=facts.get('fire_duration_basis','项目声明的整改复查规划量'))
        decision['third_party_note']='设施测试作为验收准备，不据此推断所有项目均须独立第三方电气防火检测。'
        for t in fire:
            if '第三方消防' in t['name']:t['name']='消防设施功能测试与验收资料整理'
    if not decision['completion_filing']:
        for t in completion:
            if t.get('duration_days',0)>0:t['name']='完工信息在线报送'
            else:t['name']='完工信息报送完成'
    else:
        for t in completion:
            t['name']='工程竣工验收备案' if t.get('duration_days',0)>0 else '取得竣工验收备案结果'
            if t.get('duration_days',0)>0:t.update(duration_days=2,duration=2,duration_basis='沪建规范〔2025〕12号第十条，受理后2工作日；前置竣工及适用消防手续须完成',source_url=SH_RULE)
    for t in fire:
        if t['id'] in removed:continue
        if '功能测试' in t['name']:add_pred(owner,t)
        if '申报' in t['name']:add_pred(t,owner)
    for t in completion:add_pred(t,owner)
    if not decision['permit_required']:
        for t in permit:
            t['name']='属地备案与开工信息报送' if t.get('duration_days',0)>0 else '开工信息报送完成'
    tasks=remove_nodes(tasks,removed)
    if decision['permit_required']:
        from core.release_controls import split_permit
        tasks=split_permit(tasks,permit,handling_days=2,final_compilation_after_ready=True)
    for t in [submit,*([reply] if reply else []),*fd,*permit,*fire,*completion]:
        if t['id'] in removed:continue
        t['authority_route']=decision['route']
        t['authority_path_resolved']=True
        t['authority_stage']='completion' if t in fire or t in completion else 'preconstruction'
        t['authority_requirements'] = (
            ['fire_design_review_required'] if t in fd else
            ['construction_permit_required'] if t in permit and decision['permit_required'] else
            ['fire_acceptance_required'] if t in fire and decision['fire_route']=='special' else
            ['fire_filing_required'] if t in fire and decision['fire_route']=='other' else
            ['joint_completion_required'] if t in completion and decision['completion_filing'] else [])
        t['legal_applicability']='declared_project_classification_with_official_rule'
        t['input_notes']=str(t.get('input_notes',''))+'\n属地路径：'+decision['route']+'；分类依据：'+facts['authority_basis']+'；工作包工期仍为规划预留，非受理后法定办理时限。'
    # Every completed/omitted route retains the upstream design/physical conditions.
    tasks=remove_nodes(tasks,removed)
    # Permit exemption folding in the legacy path is bypassed for these routes.
    if not permit:raise ValueError('LOCAL_AUTHORITY_PERMIT_ANCHOR_MISSING')
    if decision['fire_route']=='other' and (decision['sampling']=='selected' or
            (decision['sampling']=='pending' and facts.get('fire_release_policy')=='inspection')):
        from core.release_controls import attach_fire_inspection
        tasks=attach_fire_inspection(tasks,facts,gate_move=facts.get('fire_release_policy','pending')!='filing')
    from core.authority_buffers import apply_resolved_buffers
    apply_resolved_buffers(tasks,facts,decision)
    dependency_order(tasks)
    decision['removed_source_ids_before_renumber']=sorted(removed)
    tasks[0]['local_authority']=decision
    return tasks,decision
