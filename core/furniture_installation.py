"""Furniture installation capacity: project evidence overrides provisional area proxy."""
import math
NUMBER_FIELDS={'furniture_install_quantity','furniture_install_rate','furniture_install_crews'}
TEXT_FIELDS={'furniture_install_type','furniture_install_basis'}
FIELDS=NUMBER_FIELDS|TEXT_FIELDS

def input_issues(facts):
    if not any(facts.get(k) is not None for k in FIELDS):return []
    errors=[]
    for k in sorted(FIELDS):
        value=facts.get(k)
        if value is None or isinstance(value,str) and not value.strip():
            errors.append(dict(field=k,reason='家具安装能力推算须一起提供数量、类型与计量单位、每班组每日安装量、班组数及项目依据；可一次补齐，不沿用未知默认。'))
        elif k in NUMBER_FIELDS:
            try:
                n=float(value)
                if isinstance(value,bool) or not math.isfinite(n) or n<=0 or k=='furniture_install_crews' and not n.is_integer():raise ValueError()
            except (ValueError,TypeError):errors.append(dict(field=k,reason='须为有效正数；班组数须为正整数。'))
    if facts.get('furniture_in_scope') is False:errors.append(dict(field='furniture_in_scope',reason='排除家具范围与提供安装参数冲突，请确认。'))
    return errors

def disclosure(facts):
    if not any(facts.get(k) is not None for k in FIELDS):
        return '家具安装：缺少实际数量、组装类型及运输与班组能力，本次保留面积代理暂估；不是供应商承诺，须按项目安装方案核定。会议影音系统另列，不合并解释为家具安装工期。'
    return ('家具安装：按项目提供的同口径数量÷（每班组每日安装量×班组数）向上取整；'
            '能力须包含组装、搬运与电梯限制，不将不同家具品类简单相加；依据：'+str(facts.get('furniture_install_basis','待补齐')))

def apply_installation(tasks,facts):
    issues=input_issues(facts)
    if issues:raise ValueError(str(issues))
    configured=any(facts.get(k) is not None for k in FIELDS)
    report=dict(mode='project_capacity' if configured else 'provisional_area_proxy',tasks=[])
    for t in tasks:
        if t.get('activity_type')!='furniture':continue
        t['client_task_name']='家具安装';t['master_task_name']='家具安装'
        if configured:
            q=float(facts['furniture_install_quantity']);rate=float(facts['furniture_install_rate']);crews=int(float(facts['furniture_install_crews']))
            days=math.ceil(q/(rate*crews))
            basis=dict(quantity=q,unit_and_type=facts['furniture_install_type'],rate_per_crew_day=rate,crews=crews,evidence=facts['furniture_install_basis'],formula='ceil(quantity / (rate_per_crew_day * crews))',calendar='task_working_days',status='user_provided_not_independently_verified')
            t.update(duration_days=days,duration=days,duration_authority='project_furniture_capacity')
            t.pop('l2_duration',None)
            t['duration_record']=dict(adopted_days=days,authority='project_furniture_capacity',basis=basis)
        else:
            days=t['duration_days'];basis=dict(status='provisional_area_proxy',area_scaling=t.get('l2_duration'),missing=['actual_quantity','assembly_type','logistics_constrained_crew_capacity'])
            t['duration_record']=dict(adopted_days=days,authority='provisional_area_proxy',basis=basis)
            t['duration_authority']='provisional_area_proxy'
        t['furniture_installation_basis']=basis
        t['duration_basis']=disclosure(facts)
        t['input_notes']=(t.get('input_notes') or '')+'\n'+disclosure(facts)
        report['tasks'].append(dict(uid=t.get('activity_uid'),duration_days=days,basis=basis))
    return report
