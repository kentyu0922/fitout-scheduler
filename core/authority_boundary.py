"""Local applicability review, separate from provisional template scheduling.

This does not decide a project's legal classification from its industry/area.
An official service limit is not the duration of preparing and correcting a submission.
"""
SH_RULE = 'https://zjw.sh.gov.cn/gfxwj/20250925/ff0c97d945a243cc9133ca6f52d6d326.html'
SH_SMALL = 'https://zjw.sh.gov.cn/jsgl/20260302/98bda38e00504ef5a17271e40d4cafd7.html'
NATIONAL_FIRE = 'https://www.beijing.gov.cn/zhengce/zhengcefagui/qtwj/202404/t20240415_3617624.html'


def review_authority_boundary(tasks, city):
    shanghai = str(city).strip() in ('上海', '上海市', 'Shanghai')
    questions = [
        '建筑所在地及主管部门，拟申报日期；是否属于既有非居住建筑装修？',
        '是否改变用途、结构、外立面或消防设施？具体改动范围是什么？',
        '原建筑用途、高度/规模及特殊建设工程分类是否经设计单位核定？',
        '投资额、装修面积、产权/租赁及业主同意文件是否齐备？',
        '是否适用商务楼宇更新等专项政策，已有审批文件是否可沿用？',
    ]
    findings = []
    reviewed = []
    for task in tasks:
        name = task.get('name', '')
        if task.get('summary') or task.get('outline_level', 3) < 3:
            continue
        if not any(k in name for k in ('送审', '图审', '施工图审查', '许可证', '消防设计审查', '消防验收', '消防备案', '竣工备案', '联合竣工', '联合验收')):
            continue
        days = float(task.get('duration_days', task.get('duration', 0)) or 0)
        receipt = any(k in name for k in ('取得', '获取', '通过', '凭证')) or (task.get('milestone') and name.endswith(('完成','闭合')))
        unresolved = task.get('unresolved_placeholder') or task.get('duration_status') == 'UNRESOLVED'
        kind = ('unresolved_activity' if unresolved else 'result_milestone' if days == 0 and receipt
                else 'zero_day_activity_needs_review' if days == 0 else 'submission_work_package')
        row = dict(task_id=task['id'], name=name, kind=kind, duration_days=days,
                   applicability='declared_classification_with_official_rule' if task.get('authority_path_resolved') else 'pending_project_classification', duration_basis='template_planning_allowance_not_verified_statutory_duration')
        task['authority_boundary'] = row
        task['input_notes'] = str(task.get('input_notes', '')) + '\n报审路径须按属地及工程分类确认；当前工作包工期为规划预留，非已核实的法定办理时限。零天仅表示成果取得，不代替申报、审查或整改。'
        if kind in ('unresolved_activity', 'zero_day_activity_needs_review'):
            findings.append(dict(code='AUTHORITY_ZERO_DAY_WORK', task_id=task['id'], name=name,
                                 resolution='核实是否为未定条件或实际工作；不得将待定工期当成零天已完成。'))
        if days == 0 and receipt and not task.get('predecessors'):
            findings.append(dict(code='AUTHORITY_RECEIPT_WITHOUT_WORK', task_id=task['id'], name=name,
                                 resolution='成果里程碑须关联实际办理工作或已取得文件证据。'))
        if '消防验收' in name and '备案' in name and not task.get('authority_path_resolved'):
            findings.append(dict(code='FIRE_ROUTE_UNRESOLVED', task_id=task['id'], name=name,
                                 resolution='核定特殊建设工程/其他建设工程后，选择验收或备案路径；抽查不得一律当成必经步骤。'))
        reviewed.append(row)
    return dict(status='PROJECT_CLASSIFICATION_PENDING', city=city, reviewed_on='2026-10-12',
                local_sources_verified=shanghai, workflow_implemented=False,
                sources=[NATIONAL_FIRE] + ([SH_RULE, SH_SMALL] if shanghai else []),
                missing_project_conditions=questions, tasks=reviewed, findings=findings,
                note='上海已核实相关官方规则，项目适用性及工序分支尚待接入；其他城市不能沿用上海结论。不得将本报告标为报审合规通过。')
