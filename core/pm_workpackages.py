"""PM reading hierarchy only. Never rewrite a live activity or dependency."""
import copy

MODULES = {'datacenter':'机房建设与接口移交','mocap':'动捕棚专项工程',
 'lab_hvac':'实验室通风安装','vault_security':'安防安装与接口',
 'trading_desk':'交易岗位设施安装','it_cutover':'IT环境部署与接入',
 'cleanroom_iso':'受控空间施工','anti_vibration':'隔振设施施工',
 'esd_grounding':'静电防护施工','cda_piping':'工艺介质安装'}

def package_name(t):
    name=t.get('client_task_name') or t['name']
    if any(x in name for x in ('放线','隐蔽验收','物理竣工')):
        return None  # Independent PM hold points remain visible.
    module=str(t.get('module_id') or '')
    if module in MODULES:return MODULES[module]
    if '声学测试环境' in name:return '动捕与声学空间施工'
    uid=str(t.get('activity_uid') or '')
    if any(x in name for x in ('拆除','垃圾清运')):
        return '进场与场地准备'
    if t.get('industry_stage')=='inspection' or t.get('industry_work_stage')=='inspection':
        return '隔墙与机电粗装'
    if any(x in name for x in ('送电','机电运行','调试前安全','供电条件','空调运行')):
        return '天花与机电安装'
    if any(x in name for x in ('自检','清场','安装区域移交')):
        return '装饰、地面与末端收口'
    for prefix,label in [('dc.','机房建设与接口移交'),('security.','安防安装与接口'),
                         ('acoustic.','动捕与声学空间施工'),('equipment.','专业设备进场与接驳')]:
        if uid.startswith('pmp.integration.'+prefix):return label
    if module.startswith('A'):
        from core.module_catalog import CATALOG
        return CATALOG.get(module,{}).get('label','专项安装与移交')
    kind=t.get('activity_type','')
    if kind in {'site_setup'} or '场地移交' in name:return '进场与场地准备'
    if kind in {'partition_framing','hvac_duct','chw_pipe','containment'} or t.get('master_group_key')=='trade.rough':return '隔墙与机电粗装'
    if kind in {'gypsum_boarding','ceiling_grid','fcu','sprinkler','fire_alarm','structured_cabling','power'} or t.get('master_group_key')=='trade.second':return '天花与机电安装'
    if kind in {'gypsum_ceiling','feature_ceiling','wall_prep','painting','wallcovering','raised_floor','carpet','vinyl','tile_stone','glass_partition','doors','joinery','diffuser','lighting'} or t.get('master_group_key') in {'trade.finishing','trade.floor_doors'}:return '装饰、地面与末端收口'
    if kind in {'furniture','av'}:return None  # Distinct quantities, crews and release conditions.
    if kind=='security':return '天花与机电安装'
    return '综合检查与施工移交'

def nest_construction(members, seq):
    groups={}; standalone=[]
    for t in members:
        label=package_name(t)
        if label is None:standalone.append(t)
        else:groups.setdefault(label,[]).append(t)
    entries=[(t['start'],t['id'],[t]) for t in standalone]
    for label,work in groups.items():
        if len(work)==1:
            entries.append((work[0]['start'],work[0]['id'],work));continue
        head=dict(id=seq,name=label,client_task_name=label,activity_uid=f'pmp.pmview.{seq}',
            outline_level=3,level=3,summary=True,milestone=False,duration_days=0,duration=0,
            predecessors='',start=min(t['start'] for t in work),finish=max(t['finish'] for t in work),
            master_phase='实体施工',reporting_phase='施工阶段',client_visible=True,
            pm_view_package=True,input_notes='PM汇报工作包；展开查看实际工序、日历和前置。汇总行不参与排程。')
        seq+=1
        for t in work:t.update(outline_level=4,level=4,pm_view_detail=not (t.get('module_id') in {'mocap','datacenter','cleanroom_iso','lab_hvac'} or t.get('specialist_core_step')))
        entries.append((head['start'],head['id'],[head,*work]))
    entries.sort(key=lambda e:(e[0],e[1]))
    return [t for _,__,rows in entries for t in rows],seq

def overview(tasks):
    """Report view: same dates, with construction detail explicitly rolled up."""
    result = []
    for task in tasks:
        if task.get('pm_view_detail') and not task.get('specialist_core_step'):
            continue
        # Projection edits only top-level visibility/name fields.
        visible = dict(task)
        if task.get('specialist_core_step'):
            visible['pm_view_detail'] = False
            visible['client_task_name'] = visible['name']
        result.append(visible)
    return result
