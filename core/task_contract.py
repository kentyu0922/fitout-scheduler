"""Freeze task semantics and duration provenance before display/export."""
from __future__ import annotations
import uuid
from collections import Counter
from core.activity_calendar import classify_activity


def annotate_task_contract(tasks):
    occurrences = Counter()
    phase = ''
    for task in tasks:
        if int(task.get('outline_level', 3)) <= 2:
            phase = str(task.get('name', ''))
        cls, basis = classify_activity(task, phase)
        task.setdefault('activity_class', cls)
        task.setdefault('classification_basis', basis)
        identity = str(task.get('phase', phase)) + '/' + str(task.get('activity_type') or task.get('name'))
        occurrences[identity] += 1
        task.setdefault('activity_uid', str(uuid.uuid5(uuid.NAMESPACE_URL, identity + '/' + str(occurrences[identity]))))
        task['duration_record'] = {
            'adopted_days': task.get('duration_days', task.get('duration')),
            'diagnostic_days': task.get('diagnostic_days'),
            'authority': task.get('duration_authority', 'template_or_programme_policy'),
            'basis': (task.get('duration_basis') if task.get('duration_authority') == 'master_work_package'
                      else task.get('l2_duration') or task.get('programme_basis') or 'template'),
            'diagnostic_changes_baseline': False,
        }
    return tasks
