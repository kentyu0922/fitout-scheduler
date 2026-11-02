"""One scope registry for prompt facts, intake, selection and composition.

All 22 entries provide Master-level interface definitions; industry never selects
physical systems. Values are scope statements, not proof of site readiness.
"""
from __future__ import annotations

STATES = {'required', 'excluded', 'unknown', 'existing_claimed', 'provided_by_others'}
STATE_ALIASES = {'需要':'required','不需要':'excluded','待确认':'unknown',
                 '既有拟复用':'existing_claimed','他方提供':'provided_by_others',
                 'true':'required','false':'excluded','yes':'required','no':'excluded'}
# Existing detailed activity definitions are reused without inventing durations.
DEFINITIONS = [
 ('A01','租前技术尽调','tdd'), ('A02','结构改造／加固',None),
 ('A03','外电／供电增容',None), ('A04','专项机房','datacenter'),
 ('A05','IT迁移与业务切换','it_cutover'), ('A06','动捕系统','mocap'),
 ('A07','专业声学／录音空间','mocap'), ('A08','高密研发测试区','dev_zone'),
 ('A09','交易业务系统','trading_desk'), ('A10','强化物理安防','vault_security'),
 ('A11','金库／特殊保管空间','vault_security'), ('A12','实验室基础设施与通风','lab_hvac'),
 ('A13','洁净室／受控环境','cleanroom_iso'), ('A14','工艺气体系统','cda_piping'),
 ('A15','CDA洁净干燥压缩空气','cda_piping'), ('A16','工艺用水／UPW',None),
 ('A17','微振控制','anti_vibration'), ('A18','ESD防护工作区','esd_grounding'),
 ('A19','专项环境／EHS条件落实',None), ('A20','受监管设施确认／验证',None),
 ('A21','扩展FM导入',None), ('A22','专业设备进场与接入',None),
]
CATALOG = {i:dict(id=i,label=label,pack=pack,executable=True,
                  status='master_interfaces_implemented_project_review_pending')
           for i,label,pack in DEFINITIONS}
RECOMMENDATIONS = {
 'office':['A01','A03','A04','A05','A07','A10','A21'],
 'gaming':['A04','A05','A06','A07','A08','A21'],
 'finance':['A04','A05','A09','A10','A11','A21'],
 'biopharma':['A12','A13','A14','A15','A16','A19','A20','A22'],
 'semiconductor':['A08','A13','A14','A15','A16','A17','A18','A19','A22'],
}
INDUSTRIES = {
 'office':('OFF','人数工位、会议协作、ITAV和物业复用、搬迁需求'),
 'gaming':('GAM','研发与测试岗位、算力网络、业务连续性及内容制作需求'),
 'finance':('FIN','办公或交易用途、业务关键性、数据与物理安全、恢复要求'),
 'biopharma':('BIO','办公或实验用途、物料危害、EHS及质量体系适用性'),
 'semiconductor':('SEM','设计办公或设备实验用途、设备及介质、环境条件'),
}
LEGACY_FIELDS={'prelease_tdd':'A01','has_server_room':'A04','it_migration':'A05','laboratory':'A12'}

def normalize_state(value):
    if isinstance(value,bool): return 'required' if value else 'excluded'
    token=str(value).strip()
    token=STATE_ALIASES.get(token.lower(),token.lower())
    if token not in STATES:
        raise ValueError('choose '+', '.join(sorted(STATES))+'; reuse is unverified until project evidence is reviewed')
    return token

def resolve_modules(project):
    sector=project.get('sector','')
    issues=[]
    states={i:project.get('addon_'+i.lower(),'unknown') for i in CATALOG}
    for key,i in LEGACY_FIELDS.items():
        if key in project:
            state=normalize_state(project[key])
            if states[i]!='unknown' and states[i]!=state:
                issues.append(dict(field='addon_'+i.lower(),reason=f'conflicts with {key}'))
            else: states[i]=state
    # A confirmed capacity gap is an actual need, not an industry recommendation.
    if project.get('external_power_status') == 'upgrade_needed':
        if states['A03'] == 'excluded':
            issues.append(dict(field='addon_a03', reason='已声明需外电增容，却排除增容范围；请确认由谁实施，不能取消必要供电接口。'))
        elif states['A03'] == 'unknown':
            states['A03'] = 'required'
    if project.get('fm_transition') is True:
        # Compatibility: old flag meant basic handover, never the expanded service.
        pass
    selected=[i for i,s in states.items() if s=='required']
    for i in selected:
        if not CATALOG[i]['executable']:
            issues.append(dict(field='addon_'+i.lower(),reason=f'{CATALOG[i]["label"]}: specification only; a reviewed project execution definition is required'))
    if project.get('space_use')=='office_only':
        conflicting=set(selected)&{'A12','A13','A14','A15','A16'}
        if conflicting:issues.append(dict(field='space_use',reason='纯办公用途与所选实验、受控环境或工艺设施冲突：'+ '、'.join(CATALOG[i]['label'] for i in sorted(conflicting))))
    route={('DB','invite'):'R01',('DB','public'):'R02',('DBB','invite'):'R03',('DBB','public'):'R04'}.get((project.get('delivery'),project.get('bidding')))
    prefix=INDUSTRIES.get(sector,('UNKNOWN',''))[0]
    from core.procurement_policy import policy
    from core.industry_base import profile_summary
    from core.scope_selection import selection_decisions, QUESTIONS
    return dict(version='3.0',procurement=policy(project),industry_base=profile_summary(sector,project.get('industry_work_profile')),
                engineering_status='PLANNING_MODULES_NOT_PROJECT_SIGNOFF',
                duration_status='LEGACY_AND_UNCALIBRATED_PLANNING_ALLOWANCES',combination=f'{prefix}-{route}' if route else None,
                industry_requirements=INDUSTRIES.get(sector,('',''))[1],
                states=states,selected=selected,
                selection_decisions=selection_decisions(project, states),
                scope_questions=([QUESTIONS[sector]] if sector in QUESTIONS and project.get('space_use','unknown')=='unknown' and any(v=='unknown' for v in states.values()) else []),
                pending=[i for i,s in states.items() if s=='unknown'],
                recommended_pending=[i for i in RECOMMENDATIONS.get(sector,[]) if states[i]=='unknown'],
                external=[i for i,s in states.items() if s in {'existing_claimed','provided_by_others'}],
                packs=sorted({CATALOG[i]['pack'] for i in selected if CATALOG[i]['pack']}),
                blockers=issues,scope_status='DECLARED_SCOPE_DRAFT',
                core_handover_required=True,physical_scope_from_industry=False)
