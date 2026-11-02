"""Compare native Project readback with the solved baseline before delivery."""


def compare_native_schedule(tasks, rows):
    by_id = {r['id']: r for r in rows}
    differences = []
    checked = 0
    for index, task in enumerate(tasks):
        level = task.get('outline_level', task.get('level', 3))
        summary = index + 1 < len(tasks) and tasks[index + 1].get('outline_level', tasks[index + 1].get('level', 3)) > level
        if summary or not task.get('start') or not task.get('finish'):
            continue
        checked += 1
        actual = by_id.get(task['id'])
        expected = {'start': task['start'], 'finish': task['finish']}
        if actual is None or any(actual.get(key, '')[:10] != value for key, value in expected.items()):
            differences.append({'id': task['id'], 'name': task.get('name'), 'expected': expected, 'native': actual})
    return {'status': 'PASS' if checked and not differences else ('FAIL' if differences else 'NOT_APPLICABLE'),
            'checked_leaf_tasks': checked, 'difference_count': len(differences), 'differences': differences,
            'scope': 'Native COM start/finish dates of every solved leaf; not a productivity accuracy certification.'}
