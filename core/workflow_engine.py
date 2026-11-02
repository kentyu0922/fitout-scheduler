"""Decision-to-network adapter for internal PM planning.

Explicit project planning estimates are required. Industry experience is never
converted into default durations. Uses the existing solver in topological order.
"""
import copy
import heapq
import hashlib
from datetime import date, timedelta
from pathlib import Path
from core.project_decisions import evaluate_project
from core.duration_judgement import validate_judgement, validate_milestone, experience_disclosures

CONTRACTS={
 'tdd': [('appoint','TDD顾问招采与委任',None),('desk','资料收集与桌面审查',None),
         ('survey','各专业现场勘查',None),('report','分析、报告编制与顾问内审',None),
         ('close','报告答疑与缺陷责任/租赁条件确认',None)],
 'it_migration':[('prepare','IT迁移方案与准备',None),('pilot','IT试迁移与回退验证','infra_ready'),('cutover','业务切换与验证',None)],
 'fm_transition':[('prepare','FM资料与接管准备',None),('training','FM现场培训与运行演练','systems_ready'),('accept','FM接管签认',None)]}
RELEASES={'tdd':'lease_decision','it_migration':'business_ready','fm_transition':'occupancy_ready'}


def solve_graph(nodes,start_date,summary_mode='5D'):
    from core.solver_engine import solve_schedule
    date.fromisoformat(start_date)
    if summary_mode not in ('5D','6D','7D'):raise ValueError('Unsupported summary reference calendar')
    ids=[n['key'] for n in nodes]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate semantic node keys')
    by={n['key']:n for n in nodes};out={k:[] for k in ids};degree={k:0 for k in ids}
    for n in nodes:
        if not isinstance(n['key'],str) or not n['key']:raise ValueError('Node key must be nonempty')
        if type(n.get('days')) is not int or n['days']<0:raise ValueError('Nonnegative integer duration required')
        if n.get('calendar','5D') not in ('5D','6D','7D'):raise ValueError('Unsupported calendar')
        if not isinstance(n.get('after',[]),list):raise ValueError('after must be a list')
        for pred in set(n.get('after',[])):
            if pred not in by:raise ValueError(f'Missing predecessor: {pred}')
            out[pred].append(n['key']);degree[n['key']]+=1
    ready=[k for k in ids if degree[k]==0];heapq.heapify(ready);ordered=[]
    while ready:
        key=heapq.heappop(ready);ordered.append(key)
        for child in out[key]:
            degree[child]-=1
            if degree[child]==0:heapq.heappush(ready,child)
    if len(ordered)!=len(nodes):raise ValueError('Cycle in composed schedule')
    # Numeric IDs are an export mapping, never the semantic identity.
    mapping={key:i+2 for i,key in enumerate(ordered)}
    tasks=[dict(id=1,name='项目主进度（内部规划）',outline_level=1,duration_days=0)]
    for key in ordered:
        n=by[key];mode=n.get('calendar','5D')
        tasks.append(dict(id=mapping[key],semantic_id=key,name=n['name'],outline_level=3,
                          duration_days=n['days'],duration=n['days'],milestone=n['days']==0,
                          predecessors=','.join(f'{mapping[p]}FS' for p in dict.fromkeys(n.get('after',[]))),
                          activity_calendar=dict(calendar_mode=mode,working_days_per_week=int(mode[0])),
                          estimate_basis=n.get('estimate_basis'),duration_judgement=n.get('duration_judgement'),
                          workflow=n.get('workflow','base')))
    solved=solve_schedule(tasks,start_date)
    # Supply an explicit summary duration so PDF never approximates days * 5/7.
    # This is the declared reference-calendar envelope, not a sum of parallel tasks.
    from core.solver_engine import build_calendar_bitmaps, _is_working_day
    summary=solved['tasks'][0]
    first,last=date.fromisoformat(summary['start']),date.fromisoformat(summary['finish'])
    holidays,spring=build_calendar_bitmaps(first.year)
    span=sum(_is_working_day(first+timedelta(days=i),False,summary_mode!='5D',holidays,spring,summary_mode)
             for i in range((last-first).days+1)) if any(n['days'] for n in nodes) else 0
    summary.update(name=f'项目包络（{summary_mode}参考日历）',duration_days=span,duration=span,
                   activity_calendar=dict(calendar_mode=summary_mode,working_days_per_week=int(summary_mode[0])),summary=True,milestone=False)
    actual={t.get('semantic_id'):t for t in solved['tasks'] if t.get('semantic_id')}
    for n in nodes:
        for pred in n.get('after',[]):
            if actual[n['key']]['start'] < actual[pred]['finish']:
                raise RuntimeError('Dependency violated after solve')
    return dict(solved,semantic_id_map=mapping,network_order=ordered,dependency_contract='FS only; no lag')


def compile_project(project):
    decisions=evaluate_project(project)
    sector_recommendation=None
    sector=decisions['resolved_facts'].get('sector',{})
    if sector.get('state')=='STATED':
        from core.sector_profile import recommend_profile
        spaces=decisions['resolved_facts'].get('space_types',{})
        sector_recommendation=recommend_profile(dict(sector=sector['value'],space_types=spaces['value'] if spaces.get('state')=='STATED' else []))
    planning=project.get('planning',{})
    if planning.get('mode')!='internal_draft':raise ValueError('Only explicit internal_draft planning supported')
    purpose=planning.get('purpose','planning_review')
    if purpose not in ('logic_test','planning_review'):raise ValueError('Explicit valid planning purpose required')
    summary_calendar=planning.get('summary_calendar',{'mode':'5D','basis':'Logic-test reference only'} if purpose=='logic_test' else None)
    if not isinstance(summary_calendar,dict) or summary_calendar.get('mode') not in ('5D','6D','7D') or not isinstance(summary_calendar.get('basis'),str) or not summary_calendar['basis'].strip():
        raise ValueError('Explicit summary reference calendar and basis required')
    nodes=copy.deepcopy(planning.get('base_nodes',[]))
    if not nodes:raise ValueError('Base management network required')
    if purpose=='planning_review':
        for n in nodes:
            if type(n.get('days')) is not int or n['days']<0:raise ValueError('Nonnegative integer duration required')
            if n['days']==0:
                validate_milestone(n,project['sources'])
            else:
                n['duration_judgement']=validate_judgement(n.get('duration_judgement'),n['days'],n.get('calendar'),project['sources'],n['key'],n['name'])
    plans=planning.get('workflow_plans',{})
    if not isinstance(plans,dict):raise ValueError('workflow_plans must be an object')
    by={n['key']:n for n in nodes}
    selected={d['workflow']:d for d in decisions['workflow_decisions']}
    applied=[];pending=[]
    for workflow,spec in plans.items():
        if workflow not in CONTRACTS:raise ValueError(f'Workflow not implemented: {workflow}')
        if selected[workflow]['status']!='INCLUDE_CANDIDATE':raise ValueError(f'Workflow lacks applicable current input: {workflow}')
        if spec.get('adopted_for_internal_planning') is not True:raise ValueError('Planning estimate not adopted')
        scope=spec.get('scope_key')
        if not isinstance(scope,str) or not scope:raise ValueError('Workflow scope key required')
        if any(scope in n.get('scope_keys',[]) or n.get('workflow')==workflow for n in nodes):
            raise ValueError('Workflow overlaps existing scope')
        anchors=spec.get('anchors',{})
        needed={'start',RELEASES[workflow]}|{a for _,_,a in CONTRACTS[workflow] if a}
        if any(anchors.get(a) not in by for a in needed):raise ValueError(f'Missing workflow anchors: {workflow}')
        estimates=spec.get('estimates',{})
        previous=anchors['start']
        for stage,label,extra in CONTRACTS[workflow]:
            e=estimates.get(stage,{})
            if type(e.get('days')) is not int or e['days']<=0:raise ValueError('Positive working-day estimate required')
            if e.get('unit')!='working_day':raise ValueError('Explicit working_day unit required; months not converted')
            if e.get('basis') not in ('PM_ESTIMATE','HISTORICAL','SUPPLIER_QUOTE','SYNTHETIC_TEST'):raise ValueError('Estimate basis required')
            if e.get('basis')=='SYNTHETIC_TEST' and purpose!='logic_test':raise ValueError('Synthetic durations cannot be used for planning review')
            minimum=e.get('minimum_workdays')
            if minimum is not None:
                if type(minimum) is not int or minimum<0:raise ValueError('Invalid project duration floor')
                if not isinstance(e.get('minimum_basis'),str) or not e['minimum_basis'].strip():raise ValueError('Project duration floor needs explicit basis')
                if e['days']<minimum:raise ValueError(f'Project duration floor violated: {workflow}.{stage}')
            source=project['sources'].get(e.get('source_id'));quote=e.get('quote')
            if not isinstance(source,str) or not isinstance(quote,str) or not quote.strip() or quote not in source:
                raise ValueError('Traceable estimate source required')
            key=f'{workflow}.{scope}.{stage}'
            judgement=None
            calendar=e.get('calendar','5D') if purpose=='logic_test' else e.get('calendar')
            if purpose=='planning_review':
                judgement=validate_judgement(e.get('judgement'),e['days'],calendar,project['sources'],key,label)
                for field in ('basis','source_id','quote'):
                    if judgement[field]!=e[field]:raise ValueError('Estimate and judgement evidence conflict')
                if minimum is not None and judgement['range']['low']<minimum:
                    raise ValueError('Duration range violates project floor')
            after=[previous]+([anchors[extra]] if extra else [])
            nodes.append(dict(key=key,name=label,days=e['days'],calendar=calendar,after=after,
                              duration_judgement=judgement,
                              workflow=workflow,scope_keys=[scope],estimate_basis=copy.deepcopy(e)))
            previous=key
        release=by[anchors[RELEASES[workflow]]]
        release['after']=list(dict.fromkeys(release.get('after',[])+[previous]))
        applied.append(dict(workflow=workflow,scope_key=scope,release=release['key'],evidence_ids=selected[workflow]['evidence_ids']))
    for d in decisions['workflow_decisions']:
        if d['status']!='EXCLUDED_BY_INPUT' and d['workflow'] not in plans:
            pending.append(dict(workflow=d['workflow'],status=d['status'],reason='Not included in solved scope; not assumed zero duration'))
    judgements=[dict(key=n['key'],name=n['name'],**n['duration_judgement']) for n in nodes if n.get('duration_judgement')]
    if len({j['source_id'] for j in judgements})!=len(judgements) or len({j['quote'] for j in judgements})!=len(judgements):
        raise ValueError('Each work package requires a distinct stage-specific estimate citation')
    experience=experience_disclosures(plans,project['sources']) if purpose=='planning_review' else []
    solved=solve_graph(nodes,planning['start_date'],summary_calendar['mode'])
    duration_conditions=[dict(key=j['key'],missing_inputs=j['missing_inputs'],assumptions=j['assumptions'],
                              scale_status=j['scale']['status'],review_status=j['review']['status']) for j in judgements]
    sensitivity=None
    if purpose=='planning_review':
        sensitivity={'meaning':'All-low/all-high deterministic scenarios; not probabilities or promised dates',
                     'selected_finish':solved['finish_date']}
        for endpoint in ('low','high'):
            variant=copy.deepcopy(nodes)
            for n in variant:
                if n.get('duration_judgement'):n['days']=n['duration_judgement']['range'][endpoint]
            sensitivity[endpoint+'_finish']=solve_graph(variant,planning['start_date'],summary_calendar['mode'])['finish_date']
    plan_level = project.get('plan_level', 'L2_MACRO_CONTROL' if purpose in ('planning_review', 'logic_test') else 'L3_MICRO_EXECUTION')
    scenario_nature = project.get('scenario_nature', 'ILLUSTRATIVE_SCENARIO' if 'illustrative' in project.get('project_id', '').lower() or any('illustrative' in str(v).lower() for v in project.get('sources', {}).values()) else 'VERIFIED_FACT')
    return dict(project_id=project['project_id'],plan_state='INTERNAL_CONDITIONAL_DRAFT' if pending or judgements else 'INTERNAL_DRAFT',
                plan_level=plan_level,scenario_nature=scenario_nature,
                planning_purpose=purpose,duration_acceptance='NOT_ELIGIBLE_SYNTHETIC' if purpose=='logic_test' else 'PM_REVIEW_REQUIRED',
                duration_judgements=judgements,duration_conditions=duration_conditions,duration_sensitivity=sensitivity,
                contract_scope='--engine_project + planning.purpose=planning_review only; ordinary office CLI is outside this contract',
                summary_calendar=copy.deepcopy(summary_calendar),experience_floor_assessments=experience,
                experience_floor_not_enforced=any(e['experience_floor_not_enforced'] for e in experience),
                overlap_model='WITHIN_WORKFLOW_FS_SEQUENTIAL; PARTIAL_OVERLAP_NOT_MODELLED',
                calendar_provenance=dict(path='config/holidays.json',
                    sha256=hashlib.sha256((Path(__file__).resolve().parents[1]/'config/holidays.json').read_bytes()).hexdigest(),
                    status='EXISTING_PLANNING_CONFIG_NOT_OFFICIAL_VERIFICATION'),
                committed_schedule_allowed=False,
                sector_recommendation=sector_recommendation,
                customer_approved=False,decisions=decisions,applied_workflows=applied,unscheduled_conditions=pending,
                assembled_nodes=nodes,schedule=solved,source_facts_unchanged=True,
                limitations=['FS-only adapter; within-workflow partial overlap not modelled',
                             'Stage/numeric citation checks do not prove source truth or NLP semantic correctness',
                             'No legal applicability validation','No automatic NLP parser',
                             'Dates apply only to solved scope; unscheduled conditions may change outcome'])
