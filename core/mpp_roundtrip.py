"""Fail-closed native readback; COM enum values follow Microsoft Project VBA.

Checks calendar assignments, not full calendar exception equivalence to Python.
That separate semantic check remains required for release.
"""
import re
from core.export_audit import compare_native_schedule
from core._common import is_true_milestone

LINK_TYPES = {0: 'FF', 1: 'FS', 2: 'SF', 3: 'SS'}


def read_native_rows(project, id_map):
    from core.com_retry import retry_busy
    reverse = {native: source for source, native in id_map.items()}
    rows = []
    objects=retry_busy(lambda:project.Tasks)
    for position in range(1,int(retry_busy(lambda:objects.Count))+1):
        obj=retry_busy(lambda:objects.Item(position))
        if obj is None:
            continue
        native_id = int(retry_busy(lambda:obj.ID))
        if native_id not in reverse:
            raise ValueError(f'Unexpected native task ID {native_id}')
        links = []
        # Project's COM enumerator can return an untyped transient wrapper
        # after reopen ("<unknown>.To"). Indexed access binds each actual
        # dependency and still fails closed if it cannot be read.
        dependencies=retry_busy(lambda:obj.TaskDependencies)
        for index in range(1,int(retry_busy(lambda:dependencies.Count))+1):
            dep=retry_busy(lambda:dependencies.Item(index))
            if int(retry_busy(lambda:dep.To.ID)) != native_id:
                continue  # Collection includes successor links too.
            links.append((reverse[int(retry_busy(lambda:dep.From.ID))], LINK_TYPES[int(retry_busy(lambda:dep.Type))], float(retry_busy(lambda:dep.Lag))))
        rows.append(dict(id=reverse[native_id], start=str(retry_busy(lambda:obj.Start)), finish=str(retry_busy(lambda:obj.Finish)),
                         calendar=str(retry_busy(lambda:obj.Calendar)), milestone=bool(retry_busy(lambda:obj.Milestone)),
                         summary=bool(retry_busy(lambda:obj.Summary)), duration_minutes=float(retry_busy(lambda:obj.Duration)),
                         links=sorted(links)))
    return rows


def expected_links(value):
    result = []
    seen_preds = set()
    for part in str(value or '').split(','):
        if not part.strip():
            continue
        match = re.fullmatch(r'\s*(\d+)(FS|SS|FF|SF)?([+-]\d+)?(?:d)?\s*', part.upper(), re.I)
        if not match:
            raise ValueError(f'Unsupported predecessor syntax: {part!r}')
        pid = int(match[1])
        if pid in seen_preds:
            continue
        seen_preds.add(pid)
        # Existing exporter writes integer working days with 8h/day.
        result.append((pid, match[2] or 'FS', int(match[3] or 0) * 480.0))
    return sorted(result)


def audit_roundtrip(tasks, before, after):
    """重开落盘文件后的逐项核验。

    2026-09-24 修正：日期对账只在**输入本身带日期**时才适用。原始 JSON 导入路径
    （msp_automation.import_tasks）的日期是交给 MS Project 现排的，输入没有
    start/finish，compare_native_schedule 按设计返回 NOT_APPLICABLE。此前 status 一律
    要求 dates['status']=='PASS'，等于把「没有日期基线可对」判成 FAIL；而同一份 JSON
    导入又确实需要重开核验（任务清单/前置/里程碑/日历），两条需求会互相顶死。
    现在按适用性区分，并把结论显式写进 `date_parity`，绝不假装验过。
    """
    dates = compare_native_schedule(tasks, after)
    differences = list(dates['differences'])
    date_parity_applicable = any(t.get('start') and t.get('finish') for t in tasks)
    dates_ok = (dates['status'] == 'PASS' if date_parity_applicable
                else dates['status'] == 'NOT_APPLICABLE' and not dates['differences'])
    old = {r['id']: r for r in before}
    new = {r['id']: r for r in after}
    expected_ids = {t['id'] for t in tasks}
    if len(old) != len(before) or len(new) != len(after) or set(old) != expected_ids or set(new) != expected_ids:
        differences.append({'field': 'task_inventory', 'reason': 'Missing, extra or duplicate task IDs'})
    for index, task in enumerate(tasks):
        tid = task['id']
        if tid not in old or tid not in new:
            continue
        a, b = old[tid], new[tid]
        for field in ('start', 'finish', 'calendar', 'milestone', 'summary', 'duration_minutes', 'links'):
            if a[field] != b[field]:
                differences.append(dict(id=tid, field=field, before=a[field], after=b[field]))
        level = task.get('outline_level', task.get('level', 3))
        summary = index+1 < len(tasks) and tasks[index+1].get('outline_level', tasks[index+1].get('level', 3)) > level
        if b['summary'] != summary:
            differences.append(dict(id=tid, field='summary_source'))
        if summary:
            continue
        if b['milestone'] != is_true_milestone(task):
            differences.append(dict(id=tid, field='milestone_source'))
        try:
            links = expected_links(task.get('predecessors'))
            if links != b['links']:
                differences.append(dict(id=tid, field='links_source', expected=links, actual=b['links']))
        except ValueError as exc:
            differences.append(dict(id=tid, field='unsupported_links', reason=str(exc)))
        if task.get('mpp_calendar_name') and task['mpp_calendar_name'] != b['calendar']:
            differences.append(dict(id=tid, field='calendar_source'))
    return dict(status='PASS' if tasks and dates_ok and not differences else 'FAIL',
                difference_count=len(differences), differences=differences,
                checked_leaf_tasks=dates['checked_leaf_tasks'], checked_tasks=len(tasks),
                date_parity=('CHECKED' if date_parity_applicable
                             else 'NOT_APPLICABLE_NO_INPUT_DATES'),
                scope='Reopened/recalculated task dates, links, lag, milestones and calendar assignments',
                calendar_exception_equivalence='NOT_CHECKED')
