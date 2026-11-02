"""Concise front-end and closeout views; live dependencies remain untouched."""
def label(task,phase):
    name=task.get('client_task_name') or task['name']
    uid=str(task.get('activity_uid') or '')
    if phase=='前期规划':
        if '启动会' in name:return None
        if '物业' in name:return None
        if 'PEP' in task.get('name','') or '执行计划' in name:
            task['client_task_name']='项目执行计划（PEP）'
            return None
        return '用户需求、行业要求与专项范围确认'
    if task.get('iaq_role')=='completion_rest':
        task['client_task_name']='首次采样前置条件（完工后7日）'
        return None
    if '物理竣工' in name or '室内空气质量检测合格' in name:return None
    if task.get('authority_stage') or any(w in name for w in ('消防验收','消防查验','消防设施','消防备案','竣工备案','竣工验收与备案')):
        return '消防报验与竣工备案'
    if task.get('iaq_role')=='first_sampling':
        task['client_task_name']='首次空气检测（现场采样）'
        return None
    if task.get('iaq_role')=='first_report':
        task['client_task_name']='首次检测报告与结果核对'
        return None
    if '二次室内空气' in name:
        task['client_task_name']='二次空气检测（采样与报告）'
        return None
    if task.get('module_id') in {'core_handover','handover_preparation'} or 'industry' in uid and any(w in uid for w in ('release','acceptance')):
        return '使用验证、运维培训与接管'
    if task.get('activity_type')=='final_cleaning':return '交付保洁'
    return '系统联调、专项验收与缺陷整改'

def arrange(members,seq,phase):
    groups={};entries=[]
    for task in members:
        key=label(task,phase)
        if key is None:entries.append((task['start'],task['id'],[task]))
        else:groups.setdefault(key,[]).append(task)
    for key,work in groups.items():
        if len(work)==1:
            entries.append((work[0]['start'],work[0]['id'],work));continue
        head=dict(id=seq,name=key,client_task_name=key,activity_uid=f'pmp.pmview.{seq}',
            outline_level=3,level=3,summary=True,milestone=False,duration_days=0,duration=0,
            predecessors='',start=min(t['start'] for t in work),finish=max(t['finish'] for t in work),
            master_phase=phase,reporting_phase=phase,client_visible=True,pm_view_package=True,
            input_notes='PM汇报工作包；原活动、日历、完成条件与前置保留在计算明细。')
        seq+=1
        for task in work:task.update(outline_level=4,level=4,pm_view_detail=True)
        entries.append((head['start'],head['id'],[head,*work]))
    entries.sort(key=lambda x:(x[0],x[1]))
    return [task for _,__,rows in entries for task in rows],seq
