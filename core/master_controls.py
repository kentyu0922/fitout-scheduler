"""Combine explicitly selected inspection work and its duplicate signoffs.

These recipes preserve an existing control task ID. A real inspection is not
removed: its execution, evidence and release share one master control activity.
Only same-calendar FS0 work is supported; all outside conditions are retained.
"""
import copy
from core._common import parse_predecessor
from core.module_graph import dependency_order


def combine_controls(tasks):
    rows=copy.deepcopy(tasks)
    reports=[]
    def uid(key):return next((t for t in rows if t.get('activity_uid')==key),None)
    wall=next((t for t in rows if '隔墙及主管管线隐蔽工程验收' in t['name']),None)
    inspection=next((t for t in rows if t.get('industry_stage')=='inspection'),None)
    work=[t for t in rows if t.get('industry_work_stage')=='inspection']
    mep=next((t for t in rows if '机电安装完成及调试前安全验收' in t['name']),None)
    power=uid('pmp.integration.power_ready');hvac=uid('pmp.integration.hvac_ready')
    selfcheck=next((t for t in rows if '施工全部完成及总包自检通过' in t['name']),None)
    clean=next((t for t in rows if t.get('activity_type')=='construction_cleaning'),None)
    recipes=[('wall_control','隔墙隐蔽验收',wall,[wall,inspection,*work]),
             ('mep_control','送电与机电运行检查',mep,[power,hvac,mep]),
             ('closeout_control','总包自检及清场',selfcheck,[selfcheck,clean])]
    for key,name,representative,members in recipes:
        if representative is None or any(t is None for t in members) or len(members)<2:continue
        membership={t['id'] for t in members}
        positive=[t for t in members if t.get('duration_days',0)>0]
        calendars={(t.get('activity_calendar',{}).get('calendar_id'),t.get('mpp_calendar_name'),t.get('calendar_window')) for t in positive}
        if len(calendars)!=1 or not positive:continue
        links={t['id']:parse_predecessor(str(t.get('predecessors','') or '')) for t in rows}
        affected=[(p,r,l) for t in rows for p,r,l in links[t['id']] if t['id'] in membership or p in membership]
        if any(r!='FS' or l for p,r,l in affected):continue
        finish={}
        for task in dependency_order(rows):
            if task['id'] in membership:
                finish[task['id']]=task.get('duration_days',0)+max((finish[p] for p,_,_ in links[task['id']] if p in membership),default=0)
        rep=copy.deepcopy(representative);rep_id=rep['id']
        for field in ('activity_calendar','mpp_calendar_name','activity_class','calendar','calendar_window','work_weekend','use_construction_cal'):
            if field in positive[0]:rep[field]=copy.deepcopy(positive[0][field])
        duration=max(finish.values())
        sources=[]
        for t in members:
            sources.extend(t.get('master_source_work') or [dict(id=t['id'],activity_uid=t.get('activity_uid'),name=t['name'],
                duration_days=t.get('duration_days',0),acceptance=t.get('acceptance',''),notes=t.get('input_notes',''))])
        basis=f'执行检查与确认共用一个总控交接点，按原同日历FS工序最长路径计 {duration} 个工作日；保留所有外部条件。'
        rep.update(master_task_name=name,master_phase='实体施工',duration_days=duration,duration=duration,
            milestone=False,summary=False,duration_authority='master_work_package',duration_basis=basis,
            master_group_key=key,source_task_ids=[s['id'] for s in sources],
            source_activity_uids=[s['activity_uid'] for s in sources],master_source_work=sources)
        rep['acceptance']='；'.join(dict.fromkeys(t.get('acceptance') for t in members if t.get('acceptance')))
        if key=='wall_control':
            rep.update(industry_stage='inspection',industry=inspection.get('industry'),inspection_gate=True)
        rep['input_notes']='工作范围：'+'；'.join(s['name'] for s in sources)+'\n完成条件：各检查记录与原批准条件全部满足后移交。\n工期依据：'+basis+'\n'+'\n'.join(s['notes'] for s in sources if s['notes'])
        incoming=[]
        for t in members:
            incoming.extend((p,r,l) for p,r,l in links[t['id']] if p not in membership)
        rep['predecessors']=','.join(str(p)+'FS' for p,_,_ in dict.fromkeys(incoming))
        candidate=[]
        for t in rows:
            if t['id']==rep_id:candidate.append(rep)
            elif t['id'] not in membership:
                t=copy.deepcopy(t)
                replacement=[(rep_id if p in membership else p,r,l) for p,r,l in links[t['id']]]
                t['predecessors']=','.join(str(p)+r+(f'{l:+d}' if l else '') for p,r,l in dict.fromkeys(replacement))
                candidate.append(t)
        try:dependency_order(candidate)
        except ValueError:continue
        rows=candidate
        reports.append(dict(key=key,name=name,representative_id=rep_id,source_task_ids=rep['source_task_ids'],
                            source_activity_uids=rep['source_activity_uids'],duration_days=duration,duration_basis=basis))
    return rows,reports
