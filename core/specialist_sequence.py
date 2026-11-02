"""Room-local MoCap execution chain: seven engineering steps plus fire gate."""
from core.duration_derivation import scale_role, camera_count
from core.module_graph import add_pred


MOCAP_STEPS = (
    (1, 'mocap_acoustic_base', '声学浮筑地台减振弹簧与高阻尼双层隔墙施工'),
    (3, 'mocap_truss_grid', '独立刚性相机马道天轨钢桁架系统吊装与微振动测试'),
    (5, 'mocap_silent_mep', 'NC-20 超静音低风速消音静压箱与恒温恒湿精密空调'),
    (2, 'mocap_optical_finishes', '全黑漫反射阻燃吸音软包与防静电无缝地胶铺设'),
    (4, 'mocap_camera_mount', '光学动捕红外相机天轨定位与万兆PoE+汇聚'),
    (6, 'mocap_calibration_ue5', '动态Wand空间标定、演员动捕联调与虚幻引擎(UE5)实时驱动验收'),
)


def refine_mocap(tasks, facts, selected):
    if 'A06' not in selected:
        return tasks
    bystep = {t.get('module_step'): t for t in tasks if t.get('module_id') == 'mocap'}
    site = next(t for t in tasks if '测量放线与标高复核' in t['name'])
    purchases = [t for t in tasks if t.get('module_id') == 'module_procurement' and '动捕相机' in t['name']]
    receipt = next((t for t in tasks if '动捕相机到货开箱' in t['name']), None)
    cams = camera_count(facts)
    # Sequence: base → truss → silent MEP → optical finishes → FIRE GATE → camera → calibration
    order = [
        (1, 'mocap_acoustic_base', '声学浮筑地台减振弹簧与高阻尼双层隔墙施工', site),
        (3, 'mocap_truss_grid', '独立刚性相机马道天轨钢桁架系统吊装与微振动测试', None),
        (5, 'mocap_silent_mep', 'NC-20 超静音低风速消音静压箱与恒温恒湿精密空调', None),
        (2, 'mocap_optical_finishes', '全黑漫反射阻燃吸音软包与防静电无缝地胶铺设', None),
    ]
    prev = site
    for number, key, name, _anchor in order:
        t = bystep[number]
        scaled = scale_role(key, facts)
        t.update(name=name, client_task_name=name, duration_days=scaled['duration_days'],
                 duration=scaled['duration_days'], predecessors=str(prev['id']) + 'FS',
                 specialist_core_step=True, mocap_step=number, mocap_subtask=key,
                 pm_view_detail=False,                  duration_derivation=scaled,
                 duration_basis='〔不完整〕动捕无对应国家工程建设强制标准；仅排工序顺序，工期待项目确认。',
                 acceptance='构造、容差及性能以批准设计、设备厂家和专项验收方案为准')
        prev = t

    fire = next((t for t in tasks if t.get('mocap_subtask') == 'gate_fire_inspection'), None)
    if fire is None:
        fire = dict(
            id=max(t['id'] for t in tasks) + 1,
            name='全区政府消防联合验收与现场深度保洁清场',
            client_task_name='全区政府消防联合验收与现场深度保洁清场',
            outline_level=3, duration_days=0, duration=0, milestone=True, calendar='5D',
            predecessors=str(prev['id']) + 'FS', module_id='mocap', module_step=8,
            activity_uid='pmp.mocap.gate_fire_inspection', mocap_subtask='gate_fire_inspection',
            specialist_core_step=True, pm_view_detail=False,
            phase=prev.get('phase', '实体施工'),
            acceptance='消防喷淋及联动检测通过、粉尘清场完成前不得安装光学相机与音频设备',
        )
        tasks.insert(tasks.index(prev) + 1, fire)
        bystep[8] = fire
    else:
        fire.update(predecessors=str(prev['id']) + 'FS', specialist_core_step=True, pm_view_detail=False)
    add_pred(fire, prev)
    # Room-local fire/clean gate only. Do not wait for the project-level
    # 消防验收合格意见 — that sits after physical completion and would cycle.

    cam = bystep[4]
    scaled = scale_role('mocap_camera_mount', facts)
    cam.update(name=f'{cams}台光学动捕红外相机天轨定位与万兆PoE+汇聚',
               client_task_name=f'{cams}台光学动捕红外相机天轨定位与万兆PoE+汇聚',
               duration_days=scaled['duration_days'], duration=scaled['duration_days'],
               specialist_core_step=True, mocap_subtask='mocap_camera_mount', pm_view_detail=False,
               duration_derivation=scaled,
               acceptance='相机不得在消防验收与清场完成前上架')
    for p in ([receipt] if receipt else purchases):
        add_pred(cam, p)
    add_pred(cam, fire)
    # Strip any predecessor that would mount cameras before the fire gate.
    from core._common import parse_predecessor
    kept = []
    for pid, rel, lag in parse_predecessor(str(cam.get('predecessors') or '')):
        if pid == fire['id']:
            kept.append(f'{pid}FS')
            continue
        pred = next((t for t in tasks if t['id'] == pid), None)
        if pred and pred.get('mocap_subtask') in {'mocap_acoustic_base', 'mocap_truss_grid', 'mocap_silent_mep', 'mocap_optical_finishes'}:
            continue
        kept.append(str(pid) + rel + (f'{lag:+d}' if lag else ''))
    if str(fire['id']) + 'FS' not in kept:
        kept.append(str(fire['id']) + 'FS')
    cam['predecessors'] = ','.join(dict.fromkeys(kept))

    cal = bystep[6]
    scaled = scale_role('mocap_calibration_ue5', facts)
    cal.update(name='动态Wand空间标定、演员动捕联调与虚幻引擎(UE5)实时驱动验收',
               client_task_name='动态Wand空间标定、演员动捕联调与虚幻引擎(UE5)实时驱动验收',
               duration_days=scaled['duration_days'], duration=scaled['duration_days'],
               predecessors=str(cam['id']) + 'FS', specialist_core_step=True,
               mocap_subtask='mocap_calibration_ue5', pm_view_detail=False,
               duration_derivation=scaled)
    ready = bystep[7]
    ready.update(name='动捕棚验收通过与使用条件就绪', predecessors=str(cal['id']) + 'FS',
                 specialist_core_step=True, mocap_subtask='mocap_ready', pm_view_detail=False)
    if 0 in bystep:
        bystep[0]['pm_view_detail'] = False
        bystep[0]['specialist_core_step'] = True
    return tasks
