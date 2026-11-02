"""Evidence-linked project decisions. No NLP keyword guessing or date mutation.

The Skill extracts facts; this boundary validates records and evaluates rules.
Evidence substring matching proves traceability, not truth or semantic correctness.
"""
import argparse
import json
from pathlib import Path

FIELDS={'sector':str,'space_types':list,'relocation':bool,'prelease':bool,
        'external_power_upgrade':bool,'it_migration':bool,'server_move':bool,'has_server_room':bool,
        'fm_transition':bool,'use_change':bool,'laboratory':bool,'area_sqm':(int,float),'headcount':int,
        'city':str,'target_date':str}
RULES=[('external_power','external_power_upgrade',100,'是否已确认需要外线增容，还是仅需楼内配电调整？'),
       ('use_change','use_change',95,'拟用功能是否与既有批准用途一致？'),
       ('tdd','prelease',90,'是否仍处于租赁签约前，且需要技术尽调？'),
       ('it_migration','it_migration',85,'需要迁移哪些系统或数据，允许的业务中断窗口是什么？'),
       ('fm_transition','fm_transition',70,'是否需要新址FM接管及分批培训？')]


def evaluate_project(payload):
    if not isinstance(payload,dict):raise ValueError('Decision input must be an object')
    project=payload.get('project_id')
    if not isinstance(project,str) or not project.strip():raise ValueError('project_id required')
    sources=payload.get('sources',{})
    facts=payload.get('facts',[])
    if not isinstance(sources,dict) or not isinstance(facts,list):raise ValueError('Invalid sources/facts')
    ids=set();validated=[]
    for f in facts:
        if not isinstance(f,dict):raise ValueError('Fact must be an object')
        fid=f.get('id')
        if not isinstance(fid,str) or not fid or fid in ids:raise ValueError('Unique fact IDs required')
        ids.add(fid)
        field=f.get('field')
        if field not in FIELDS:raise ValueError(f'Unsupported fact field {field}')
        assertion=f.get('assertion')
        if assertion not in ('stated','denied','conditional','unknown','estimate'):raise ValueError('Invalid assertion')
        if f.get('context') not in ('current','historical','future'):raise ValueError('Explicit temporal context required')
        if not isinstance(f.get('scope'),str) or not f['scope']:raise ValueError('Explicit scope required')
        source=sources.get(f.get('source_id'))
        quote=f.get('quote')
        if not isinstance(source,str) or not isinstance(quote,str) or not quote.strip() or quote not in source:
            raise ValueError('Fact must cite an exact nonempty source excerpt')
        value=f.get('value')
        if assertion not in ('unknown','conditional'):
            allowed=FIELDS[field]; allowed=allowed if isinstance(allowed,tuple) else (allowed,)
            if type(value) not in allowed:raise ValueError(f'Invalid value type for {field}')
            if field in ('area_sqm','headcount'):
                import math
                if not math.isfinite(value) or value<=0:raise ValueError('Positive finite quantity required')
            if field=='space_types' and any(type(v) is not str for v in value):raise ValueError('Space names must be strings')
            if assertion=='denied' and allowed != (bool,):raise ValueError('Denial requires a boolean proposition')
            if assertion=='denied' and value is not False:raise ValueError('Denied proposition must be false')
        validated.append(f)
    by_id={f['id']:f for f in validated};replaced=set()
    for f in validated:
        for old_id in f.get('supersedes',[]):
            old=by_id.get(old_id)
            if old is None or old_id==f['id'] or old['field']!=f['field'] or old['scope']!=f['scope']:
                raise ValueError('Invalid supersession')
            if f['context']!='current' or old['context']!='current':raise ValueError('Historical/future facts cannot supersede current inputs')
            # Only earlier records can be replaced; this also prevents cycles.
            if validated.index(old)>=validated.index(f):raise ValueError('Supersession must reference an earlier record')
            replaced.add(old_id)
    current={};excluded=[]
    for f in validated:
        if f['id'] in replaced or f['context']!='current' or f['scope']!=project:
            excluded.append(f['id']);continue
        current.setdefault(f['field'],[]).append(f)
    resolved={}
    for field,records in current.items():
        certain=[r for r in records if r['assertion'] in ('stated','denied')]
        values={json.dumps(r['value'],ensure_ascii=False,sort_keys=True) for r in certain}
        state='CONFLICT' if len(values)>1 else ('STATED' if certain else 'UNRESOLVED')
        resolved[field]=dict(state=state,value=certain[-1]['value'] if state=='STATED' else None,
                             evidence_ids=[r['id'] for r in records],verification='SOURCE_STATEMENT_NOT_INDEPENDENTLY_VERIFIED')
    decisions=[];questions=[]
    for workflow,field,priority,question in RULES:
        fact=resolved.get(field,{})
        if fact.get('state')=='CONFLICT':status='CONFLICT'
        elif fact.get('state')=='STATED':status='INCLUDE_CANDIDATE' if fact['value'] else 'EXCLUDED_BY_INPUT'
        else:status='NEEDS_CONFIRMATION'
        decisions.append(dict(rule_id='PM.'+workflow+'.v1',workflow=workflow,status=status,
                              evidence_ids=fact.get('evidence_ids',[]),applied=False,duration_days=None))
        if status in ('CONFLICT','NEEDS_CONFIRMATION'):
            questions.append(dict(priority=priority,field=field,question=question,status=status))
    # Scope conflicts are more urgent than routine workflow questions.
    for field,record in resolved.items():
        if record['state']=='CONFLICT' and field not in {r[1] for r in RULES}:
            questions.append(dict(priority=110,field=field,question=f'{field}存在相互冲突的当前输入，请确认适用值。',status='CONFLICT'))
    questions.sort(key=lambda q:-q['priority'])
    return dict(schema_version='decision-layer-0.1',project_id=project,resolved_facts=resolved,
                excluded_from_current_decisions=excluded,fact_ledger=validated,sources=sources,
                workflow_decisions=decisions,priority_questions=questions[:3],remaining_question_count=max(0,len(questions)-3),
                schedule_changed=False,customer_approved=False,
                limitations=['No legal applicability decision','No automatic duration or graph changes',
                             'Extraction semantics require review; quote matching is not semantic proof',
                             'Only project-scope facts evaluated; subspace facts retained but not aggregated'])


def run_cli(argv):
    parser=argparse.ArgumentParser(description='Evaluate evidence-linked project facts')
    parser.add_argument('--decision_input',required=True)
    parser.add_argument('--decision_output',required=True)
    args=parser.parse_args(argv)
    result=evaluate_project(json.loads(Path(args.decision_input).read_text(encoding='utf-8-sig')))
    output=Path(args.decision_output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Decision record saved: {output}; no schedule changed.')


if __name__ == '__main__':
    import sys
    run_cli(sys.argv[1:])

