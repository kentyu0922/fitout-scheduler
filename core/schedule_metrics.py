"""Synchronize presentation metrics from the calendar-aware optimizer result."""
import copy


def synchronize_schedule_metrics(tasks, optimizer_result):
    """Return a copy with authoritative leaf metrics; never retime the baseline."""
    # Only top-level presentation fields are written; evidence is read-only.
    result = [dict(task) for task in tasks]
    metrics = {row['id']: row for row in optimizer_result['float']['by_task']}
    for index, task in enumerate(result):
        level = task.get('outline_level', task.get('level', 1))
        summary = index + 1 < len(result) and result[index + 1].get('outline_level', result[index + 1].get('level', 1)) > level
        for key in ('total_slack_days', 'critical', 'on_driving_chain', 'float_unit', 'metrics_source'):
            task.pop(key, None)
        if summary:
            continue
        # 顶层「关键里程碑总控」汇总条（control_rollup）由 _build_control_milestone_rollup
        # 在 CPM 解算之后、优化器结果之外插入，不进优化器 float 表；跳过而非报缺指标。
        if task.get('control_rollup'):
            continue
        if task['id'] not in metrics:
            raise ValueError(f"Missing optimizer metrics for leaf task {task['id']}")
        row = metrics[task['id']]
        task.update(total_slack_days=row['float_days'], critical=row['critical'],
                    on_driving_chain=row.get('on_driving_chain', False),
                    float_unit='working days in task calendar', metrics_source='optimizer_calendar_aware')
    return result
