"""Absorb redundant industry completion gates, retaining their evidence.

Only a gate already implied by one completed industry package is eligible.
Independent approvals, concealment checks and multi-system releases remain.
"""
import copy
from core.master_graph import _links, _tokens, _calendar
from core.module_graph import dependency_order

def absorb_completion_gates(tasks, *, copy_source=True):
    rows=copy.deepcopy(tasks) if copy_source else list(tasks); changes=[]
    for gate in list(rows):
        stage=gate.get('industry_stage')
        if stage not in {'requirements','acceptance','release'}: continue
        if gate.get('duration_days',0) or gate.get('constraint') or gate.get('constraint_date'): continue
        candidates=[t for t in rows if t.get('industry_work_stage')==stage and t.get('industry')==gate.get('industry')]
        if len(candidates)!=1: continue
        work=candidates[0]
        if not work.get('duration_days') or _calendar(work) is None or _calendar(work)!=_calendar(gate): continue
        byid={t['id']:t for t in rows}
        # Only finish-to-start chains with no lag qualify as proven completion.
        ancestors=set(); todo=[work['id']]
        while todo:
            for pid,kind,lag in _links(byid[todo.pop()].get('predecessors')):
                if kind=='FS' and lag==0 and pid not in ancestors:
                    ancestors.add(pid); todo.append(pid)
        preds=_links(gate.get('predecessors'))
        if not preds or any(kind!='FS' or lag or pid not in ancestors|{work['id']} for pid,kind,lag in preds): continue
        if not any(pid==work['id'] for pid,_,_ in preds): continue
        consumers=[(t,p) for t in rows for p in _links(t.get('predecessors')) if p[0]==gate['id']]
        if any(p[1]!='FS' or p[2] for _,p in consumers): continue
        condition=dict(id=gate['id'],activity_uid=gate.get('activity_uid'),name=gate['name'],
                       acceptance=gate.get('acceptance'),evidence_status=gate.get('evidence_status'),
                       predecessors=gate.get('predecessors'))
        work.setdefault('master_source_work',[dict(id=work['id'],activity_uid=work.get('activity_uid'),name=work['name'],duration_days=work['duration_days'],acceptance=work.get('acceptance',''),notes=work.get('input_notes',''))])
        work['master_source_work'].append(dict(id=gate['id'],activity_uid=gate.get('activity_uid'),name=gate['name'],duration_days=0,acceptance=gate.get('acceptance',''),notes=gate.get('input_notes','')))
        work.setdefault('completion_conditions',[]).append(condition)
        work['source_task_ids']=list(dict.fromkeys(work.get('source_task_ids',[work['id']])+gate.get('source_task_ids',[gate['id']])))
        work['source_activity_uids']=list(dict.fromkeys(work.get('source_activity_uids',[work.get('activity_uid')])+[gate.get('activity_uid')]))
        work['input_notes']=str(work.get('input_notes',''))+'\n完成条件（待实际签认）：'+str(gate.get('acceptance') or gate['name'])
        for t,_ in consumers:
            t['predecessors']=_tokens(list(dict.fromkeys((work['id'] if p==gate['id'] else p,k,l) for p,k,l in _links(t.get('predecessors')))))
        rows.remove(gate)
        changes.append(dict(gate_id=gate['id'],package_id=work['id'],condition=condition,basis='Existing zero-lag FS completion already implied by the package; evidence retained.'))
    dependency_order(rows)
    return rows,changes
