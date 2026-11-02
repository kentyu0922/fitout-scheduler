"""Explain a computed target gap without moving dates or approving acceleration."""
from datetime import date


def target_decision(tasks, target, finish, response='ask'):
    # A PM's target (搬迁 / 搬家 / move-in) is the move: compare it with the move-in date.
    if not target:
        return None
    from core.solver_engine import move_in_date
    moved = move_in_date(tasks)
    move_in = moved.isoformat() if moved else finish
    gap = (date.fromisoformat(move_in) - date.fromisoformat(target)).days
    critical = [t for t in tasks if t.get('critical') and not t.get('summary')
                and t.get('outline_level', 3) >= 3 and t.get('duration_days', 0) > 0]
    critical.sort(key=lambda t: (t.get('start', ''), t.get('finish', ''), t['id']))
    controls = [dict(uid=t.get('activity_uid'), name=t.get('client_task_name') or t['name'],
                     start=t.get('start'), finish=t.get('finish'), days=t.get('duration_days'),
                     predecessors=t.get('predecessors')) for t in critical]
    lines = [f'目标日期按搬迁开始比较：目标 {target}，正常推演搬迁开始 {move_in}，'
             + (f'晚 {gap} 个日历天。' if gap > 0 else '在目标日期内；仍须确认输入条件。')]
    if gap > 0:
        lines += [('请选择：保留正常推演计划，之后由PM调整；或评估压缩方案，再决定是否采用。' if response == 'ask' else '已按入口选择处理目标差距，原目标与正常推演计划均保留。'),
                  '压缩评估保留原计划，重算可并行工作及资源条件；不自动缩短适用审批窗口、取消必要工序或承诺达标。']
        if controls:
            lines.append('当前模型关键工作（完整日期及前置关系见目标决策JSON）：' + ' → '.join(t['name'] for t in controls[-6:])
                         + '。此为关键工作节选，可能包含并行分支，不能将工期相加作为延误归因。')
        if response == 'keep':
            lines.append('已选择保留正常推演计划；目标日期保留原值，由PM后续调整。')
        elif response == 'assess':
            lines.append('已请求独立压缩情境评估；是否达标以重算结果和条件确认情况为准，原计划不覆盖。')
    return dict(status='TARGET_MISSED' if gap > 0 else 'WITHIN_TARGET',
                target=target, calculated_finish=finish, calculated_move_in=move_in, variance_calendar_days=gap,
                comparison='move_in', selected_response=response,
                decision_required=gap > 0 and response == 'ask',
                critical_work=controls, summary_lines=lines,
                choices=[dict(value='keep', label='保留推演计划'), dict(value='assess', label='评估压缩方案')] if gap > 0 else [],
                baseline_modified=False)


def add_compression_result(decision, scenario):
    """Surface the actual what-if result, including no saving / unmet target."""
    if decision['selected_response'] != 'assess':
        return
    if not scenario or scenario.get('status') != 'OK':
        decision['summary_lines'].append('压缩情境尚未完成有效重算，不能给出可压缩承诺；保留正常推演计划。')
        return
    finish = scenario['fasttrack_finish']
    gap = decision['variance_calendar_days'] - scenario['saving_days']   # the move shifts with the saving
    decision['compression_result'] = dict(finish=finish, remaining_gap_calendar_days=gap,
        saving_calendar_days=scenario['saving_days'], applied_to_baseline=False,
        conditions_confirmed=False, evaluated_scope='existing_allowed_fasttrack_transformations')
    decision['summary_lines'].append(f'压缩评估结果：{finish}，当前规则可节省 {scenario["saving_days"]} 个日历天；'
        + (f'仍晚于目标 {gap} 天，未达标。' if gap > 0 else '模型日期在目标内，采用前仍须确认实施条件。'))
    if scenario['saving_days'] == 0:
        decision['summary_lines'].append('当前已支持且允许的搭接规则未找到节省；保留正常推演计划。')
