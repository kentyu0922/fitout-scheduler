"""Internal move planning is managed in PEP, not a scheduled delivery gate."""
from core.module_graph import remove_nodes

def remove_internal_move_planning(tasks):
    ids={t['id'] for t in tasks if t.get('module_id')=='office_ff_move' and t.get('module_step')==4}
    removed=[dict(id=t['id'],name=t['name'],reason='内部搬迁策划取消独立计时；相关组织事项归PEP/交接清单，不作为搬迁实施的额外前置') for t in tasks if t['id'] in ids]
    return remove_nodes(tasks,ids),removed
