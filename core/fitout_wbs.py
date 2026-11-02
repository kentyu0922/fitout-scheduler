"""Expand configured commercial-office work packages before quantity calculation."""
import copy
import json
from pathlib import Path
from core._common import split_predecessor_id_suffix
from core.task_utils import renumber_tasks_contiguously


def expand_fitout_wbs(tasks):
    config = json.loads((Path(__file__).resolve().parents[1] / 'config' / 'fitout_wbs.json').read_text(encoding='utf-8'))
    if not tasks:
        return []
    result, replacements = [], {}
    source_by_id = {t['id']: t for t in tasks}
    package_ids = {t['id'] for t in tasks if t['name'] in config['packages']}
    next_id = max(t['id'] for t in tasks) + 1
    in_construction = False
    for source in tasks:
        task = copy.deepcopy(source)
        if task.get('outline_level', task.get('level', 3)) == 2:
            in_construction = '实体施工' in task['name']
        package = config['packages'].get(task['name'])
        if package is None:
            if in_construction and task.get('duration_days', task.get('duration', 0)) > 0 and not task.get('activity_type'):
                if not any(k in task['name'] for k in ('验收', '检测', '盲测', '空气', '通风')):
                    raise ValueError(f"Unmapped construction task: {task['name']}")
            result.append(task)
            continue
        children = []
        for activity in package:
            child = copy.deepcopy(task)
            child.update(id=next_id, activity_type=activity, name=config['labels'][activity],
                         duration_days=0, duration=0, source_package=task['name'],
                         workfronts_source='project', milestone=False)
            next_id += 1
            child.pop('constraint', None)
            # A legacy package is a bundle, not a finish-to-start requirement
            # for every child of the next bundle. Trade edges are configured below.
            # Keep external releases (permits, completion, IAQ), while concealed
            # inspection releases are assigned to the actual covered trade.
            child['predecessors'] = ','.join(
                f'{pid}{suffix}'
                for pid, suffix in split_predecessor_id_suffix(task.get('predecessors', ''))
                if pid not in package_ids and
                '隐蔽工程验收' not in source_by_id.get(pid, {}).get('name', '')
            )
            children.append(child)
        replacements[task['id']] = [c['id'] for c in children]
        result.extend(children)
    for task in result:
        tokens = []
        for pid, suffix in split_predecessor_id_suffix(task.get('predecessors', '')):
            for replacement in replacements.get(pid, [pid]):
                tokens.append(f'{replacement}{suffix}')
        task['predecessors'] = ','.join(tokens)
    # Template inspection tasks are explicit releases, even when represented as milestones.
    for task in result:
        if any(k in task['name'] for k in ('隐蔽工程验收', '首次室内空气盲测')):
            task['inspection_gate'] = True
    by_activity = {}
    for task in result:
        if task.get('activity_type'):
            by_activity.setdefault(task['activity_type'], []).append(task)
    for task in list(result):
        activity = task.get('activity_type')
        if not activity:
            continue
        for pred_type in config['predecessors'].get(activity, []):
            for pred in by_activity.get(pred_type, []):
                if pred['id'] == task['id']:
                    continue
                task['predecessors'] += (',' if task['predecessors'] else '') + f"{pred['id']}FS"
        gate_keyword = config['inspection_requirements'].get(activity)
        if gate_keyword:
            gates = [t for t in result[:result.index(task)] if gate_keyword in t['name'] and t.get('inspection_gate')]
            if not gates:
                raise ValueError(f'Missing inspection release for {activity}')
            task['inspection_gate_ids'] = [gates[-1]['id']]
            task['predecessors'] += (',' if task['predecessors'] else '') + f"{gates[-1]['id']}FS"
        if activity == 'testing_commissioning':
            # Readiness inspection is a real scheduled hold point, not an approval flag.
            gate = dict(id=next_id, name='机电安装完成及调试前安全验收', inspection_gate=True,
                        milestone=True, duration_days=0, predecessors=task['predecessors'],
                        outline_level=task.get('outline_level', 4))
            next_id += 1
            result.insert(result.index(task), gate)
            task['inspection_gate_ids'] = [gate['id']]
            task['predecessors'] = f"{gate['id']}FS"
    # Furniture delivery must also be complete before installation.
    for task in by_activity.get('furniture', []):
        for delivery in result:
            if '家具生产交货' in delivery['name']:
                task['predecessors'] += f",{delivery['id']}FS"
    return renumber_tasks_contiguously(result)
