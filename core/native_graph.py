"""Project-compatible projection of virtual zero-day package-start anchors.

MS Project cannot link a child to its own parent summary. The offline engine
uses some zero-duration package rows as start anchors before rolling them up
for display. Expand only the zero-lag FS start-anchor case to the package's
upstream zero-lag FS links. More complex cases refuse, rather than drop links.
The source graph is unchanged, and native date parity remains mandatory.
"""
from __future__ import annotations
import copy
from datetime import date
from core._common import WEEKEND_CAL_NAME, is_true_milestone
from core.mpp_roundtrip import expected_links


def prepare_native_graph(tasks, apply_client_visibility=True):
    projected = copy.deepcopy(tasks)
    by_id = {t['id']: t for t in projected}
    from core.client_visibility import work_item_hide_reason
    hidden = {t['id'] for t in projected if t.get('client_visible') is False or
              (apply_client_visibility and work_item_hide_reason(t))}
    ancestors = {}
    stack = []
    for task in projected:
        level = int(task.get('outline_level', task.get('level', 3)))
        while stack and stack[-1][0] >= level:
            stack.pop()
        ancestors[task['id']] = {tid for _, tid in stack}
        stack.append((level, task['id']))
    changes = []
    def expand_link(link, successor, seen):
        pid, kind, lag = link
        if pid not in hidden and pid not in ancestors[successor]:
            return [link]
        if pid in seen or pid not in by_id:
            raise ValueError(f'Invalid hidden/summary dependency chain at {pid}')
        parent = by_id[pid]
        if int(parent.get('duration_days', parent.get('duration', 0)) or 0) != 0 or kind != 'FS' or lag != 0:
            raise ValueError(f'Unsupported hidden/summary dependency {pid}->{successor}; cannot drop a live constraint')
        links = expected_links(parent.get('predecessors'))
        if not links or any(k != 'FS' or l != 0 for _, k, l in links):
            raise ValueError(f'Hidden/virtual anchor {pid} needs explicit zero-lag FS upstream links')
        resolved = []
        for upstream in links:
            resolved.extend(expand_link(upstream, successor, seen | {pid}))
        changes.append({'task_id': successor, 'virtual_package_id': pid,
                        'source_link': [pid, kind, lag], 'native_links': [list(x) for x in resolved],
                        'basis': 'hidden zero-duration node or package-start anchor projected to upstream event; not a baseline edit'})
        return resolved
    for task in projected:
        if task['id'] in hidden:
            continue
        expanded = []
        old = expected_links(task.get('predecessors'))
        for pid, kind, lag in old:
            expanded.extend(expand_link((pid, kind, lag), task['id'], set()))
        if expanded != old:
            task['source_predecessors'] = task.get('predecessors')
            tokens = []
            for pid, kind, lag in sorted(set(expanded)):
                days = int(lag / 480)
                tokens.append(f'{pid}{kind}' + (f'{days:+d}d' if days else ''))
            task['predecessors'] = ','.join(tokens)
    weekend_ids = set()
    for task in projected:
        if str(task.get('calendar_window', '')).lower() in {'weekend', 'weekend_only'} or str(task.get('calendar', '')).upper() == 'WEEKEND':
            task['mpp_calendar_name'] = WEEKEND_CAL_NAME
            weekend_ids.add(task['id'])
            changes.append({'task_id': task['id'], 'native_calendar': WEEKEND_CAL_NAME,
                            'basis': 'explicit weekend-only execution window; calendar weekdays are nonworking'})
    for task in projected:
        links = expected_links(task.get('predecessors'))
        if (is_true_milestone(task) and task.get('finish') and date.fromisoformat(task['finish']).weekday() >= 5
                and links and all(pid in weekend_ids and kind == 'FS' and lag == 0 for pid, kind, lag in links)):
            task['mpp_calendar_name'] = WEEKEND_CAL_NAME
            changes.append({'task_id': task['id'], 'native_calendar': WEEKEND_CAL_NAME,
                            'basis': 'zero-day completion event inherits the weekend cutover completion; dates unchanged'})
    return projected, changes
