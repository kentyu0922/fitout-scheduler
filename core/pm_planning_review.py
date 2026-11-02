"""Explicit provisional PM planning quantities; never statutory durations."""
def apply_planning_review(tasks,handover):
    changes=[]
    physical=next((t for t in tasks if '物理竣工' in t.get('name','')),None)
    for t in tasks:
        name=t.get('name','');days=None;basis=None
        if 'PEP' in name:
            days=5;basis='PM总控规划量：PEP编制与主要方确认暂留5工作日，非规范时限，待项目确认。'
        elif '二次室内空气质量复测' in name:
            days=5;basis='采样、实验室分析及报告核对暂留5工作日；须检测机构按点位与批次确认，不包含整改或两次检测之间的家具安装间隔。'
        elif handover=='whole' and t.get('activity_type')=='gypsum_boarding':
            diagnostic=(t.get('duration_record') or {}).get('diagnostic_days')
            if isinstance(diagnostic,(int,float)) and diagnostic>t.get('duration_days',0):
                days=int(diagnostic);basis='整体移交规划复核：不采用低于本次工程量/产能诊断的面积节拍；暂按诊断值，工程量、班组和工作面仍待PM确认，非行业定额。'
        if days is None:continue
        before=t.get('duration_days');t.update(duration_days=days,duration=days,duration_basis=basis,
            duration_authority='provisional_pm_review',planning_review_required=True)
        t['input_notes']=str(t.get('input_notes',''))+'\n'+basis
        record=t.setdefault('duration_record',{})
        record.update(adopted_days=days,authority='provisional_pm_review',basis=basis)
        changes.append(dict(id=t['id'],name=name,before=before,after=days,basis=basis))
    return changes
