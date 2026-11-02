"""Traceable PM buffers on resolved scoped local routes; not statutory time limits."""
from core.authority_reference import BUFFER_FIELDS, _user_buffer
from core.module_graph import dependency_order
POLICY={'review':2,'permit':2,'fire':2}
REASON='PM暂估风险余量；不替代已列资料准备、意见修改、行政审批、抽查或整改复查；项目可改为0，风险兑现后须重算'

def apply_resolved_buffers(tasks,facts,decision):
    report=[]
    if decision.get('status')!='READY':return report
    def active(t):return int(t.get('duration_days',0))>0
    anchors={
      'review':next((t for t in tasks if active(t) and '图审意见答复' in t.get('name','')),None) or next((t for t in tasks if active(t) and t.get('authority_stage')=='preconstruction' and '施工图送审' in t.get('name','')),None),
      'permit':next((t for t in tasks if t.get('permit_role')=='submit'),None),
      'fire':next((t for t in tasks if active(t) and t.get('authority_stage')=='completion' and ('消防验收申报' in t.get('name','') or '消防验收备案申报' in t.get('name',''))),None),
    }
    for key,anchor in anchors.items():
        if anchor is None:
            report.append(dict(step=key,status='not_applicable',applied_days=0));continue
        if key=='permit' and not decision.get('permit_required'):continue
        field=BUFFER_FIELDS[key];provided=field in facts and facts[field] is not None
        days=int(facts[field]) if provided else POLICY[key]
        reason=facts.get('authority_buffer_reason') if provided and days else REASON
        if days and not reason:raise ValueError('AUTHORITY_BUFFER_REASON_REQUIRED')
        if any(t.get('activity_uid')==f'pmp.authority.{key}_buffer' for t in tasks):raise ValueError('DUPLICATE_AUTHORITY_BUFFER')
        local={**facts,field:days,'authority_buffer_reason':reason}
        node=_user_buffer(tasks,local,key,anchor) if days else None
        if node:
            node.update(authority_stage=anchor['authority_stage'],authority_route=decision.get('route'),authority_path_resolved=True)
            if not provided:
                node['name']={'review':'图审衔接预留','permit':'许可衔接预留','fire':'消防报验衔接预留'}[key]
            node['buffer_origin']='provided' if provided else 'pm_provisional_policy'
            node['duration_basis']=reason
            if not provided:node['input_notes']=f'PM默认暂估缓冲{days}工作日；{REASON}。不代表主管部门办理时限。'
        report.append(dict(step=key,status='applied' if node else 'disabled',applied_days=days,origin='provided' if provided else 'pm_provisional_policy',reason=reason,anchor_uid=anchor.get('activity_uid'),buffer_uid=node.get('activity_uid') if node else None))
    decision['pm_buffers']=report
    dependency_order(tasks)
    return report


def buffer_disclosure(facts):
    from core.local_authority import resolve_local_authority
    d=resolve_local_authority(facts)
    if d.get('status')!='READY':return None
    applicable={'review':d.get('drawing_review'),'permit':d.get('permit_required'),'fire':d.get('fire_route')!='not_applicable'}
    labels={'review':'图审','permit':'施工许可','fire':'消防'}
    parts=[labels[k]+str(int(facts.get(BUFFER_FIELDS[k],POLICY[k])))+'工作日' for k in POLICY if applicable[k]]
    return 'PM缓冲：'+ '、'.join(parts)+'；未自填者为默认暂估，可改为0；与行政时限分开，非法律或行业定额，不包含未声明的多轮整改。'
