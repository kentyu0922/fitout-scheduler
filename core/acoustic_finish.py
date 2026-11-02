"""Separate permanent acoustic finishes from government fire release.

The inspection is real work, not a substitute for government acceptance.
Durations are scoped PM allowances, not curing periods or industry norms.
"""
from core.module_graph import add_pred
from core._common import parse_predecessor


def apply_acoustic_finish(tasks, facts, selected):
    if not selected.intersection({'A06', 'A07'}):
        return tasks
    if any(t.get('activity_uid') == 'pmp.acoustic.finish.check' for t in tasks):
        return tasks
    steps = {t.get('module_step'): t for t in tasks if t.get('module_id') == 'mocap'}
    room_interfaces = next((t for t in tasks if t.get('mocap_subtask') == 'mocap_optical_finishes'), None)
    if room_interfaces is None:
        room_interfaces = steps.get(5) or steps.get(2)
    if room_interfaces is None:
        return tasks
    if 'A06' not in selected:
        room_interfaces.update(name='声学门体与机电穿透接口收口',
                               client_task_name='声学门体与机电穿透接口收口')
    next_id = max(t['id'] for t in tasks) + 1
    definitions = [
        ('check', '声学饰面施工条件检查', 1, 'CONSULTANT_REVIEW',
         '相关隐蔽检查记录齐备；本区域湿尘作业结束；基层干燥、清洁、稳定；'
         '材料和安装构造符合批准设计及厂家要求。未满足不得放行，1天是检查工作量，不是干燥养护时间。'),
        ('install', '最终固定吸声饰面安装', 5, 'SITE_INSTALLATION',
         '固定墙顶吸声构件、软包及必要收口按本次批准范围完成；材料防火证明与实装一致，'
         '不得遮挡探测、喷淋、疏散及检修设施；不默认增加特殊声学等级或设备。'),
        ('clean', '声学房间清洁与完成面移交', 1, 'SITE_CONSTRUCTION',
         '安装残留与粉尘清除，完成面保护到位；本区域不再安排污染性返工作业。'),
    ]
    previous = room_interfaces
    nodes = []
    for key, name, days, cls, acceptance in definitions:
        node = dict(id=next_id, name=name, client_task_name=name, outline_level=4,
                    duration_days=days, duration=days, predecessors=f'{previous["id"]}FS',
                    module_id='mocap', activity_uid='pmp.acoustic.finish.'+key,
                    activity_class=cls, phase=room_interfaces.get('phase', '实体施工'),
                    specialist_core_step=True, acoustic_finish_role=key,
                    duration_scope='single_room_allowance',
                    duration_authority='scoped_pm_allowance',
                    duration_basis='限定范围PM暂估：检查1日、饰面安装5日、清洁1日；须按面积、构造和班组校准',
                    acceptance=acceptance, government_fire_acceptance=False,
                    evidence_ref='references/acoustic_finish_basis.md')
        nodes.append(node)
        previous = node
        next_id += 1
    tasks[tasks.index(room_interfaces)+1:tasks.index(room_interfaces)+1] = nodes
    # Preserve existing equipment/test work and supply conditions. Only replace
    # its room-interface release with the completed clean-room workface.
    consumer = next((t for t in tasks if t.get('mocap_subtask') in ('gate_fire_inspection', 'mocap_camera_mount')), None)
    if consumer is None:
        consumer = steps[4] if 'A06' in selected else steps[6]
    edges = []
    for pid, relation, lag in parse_predecessor(str(consumer.get('predecessors', ''))):
        if pid == room_interfaces['id'] and relation == 'FS' and lag == 0:
            pid = previous['id']
        edges.append(str(pid)+relation+(f'{lag:+d}' if lag else ''))
    consumer['predecessors'] = ','.join(edges)
    add_pred(consumer, previous)
    # The final acoustic test also requires its fixed finish state, even where
    # the existing path contains additional environmental checks.
    add_pred(steps[6], previous)
    physical = next((t for t in tasks if "物理竣工" in t["name"]), None)
    if physical is not None and "A06" not in selected:
        from core.master_graph import _links, _tokens
        physical["predecessors"] = _tokens([edge for edge in _links(physical.get("predecessors")) if edge[0] != steps[7]["id"]])
        add_pred(physical, previous)  # Functional acceptance remains on the handover network.
    return tasks
