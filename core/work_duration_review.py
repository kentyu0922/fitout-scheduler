"""Explicit PM work allowances; never infer elapsed work from zero-day captions.

These are reviewable planning allowances for a single office project/workfront,
not statutory clocks or calibrated engineering productivity.
"""
from core.module_graph import dependency_order

WORK = {
 'pmp.scope.boundaries': (2, '公共与专项范围、空间及施工接口会审', '会前范围表齐备；组织专业协调、登记冲突并闭合接口意见'),
 'pmp.integration.brief_release': (1, '项目任务书评审与意见闭合', '已完成需求编制，组织业主评审和意见关闭'),
 'pmp.lifecycle.A05.retirement_scope': (1, '旧环境保留与退出责任核对', '核对资产、保留期限及退出批准责任'),
 'pmp.lifecycle.A07.test_environment': (1, '声学测试环境核查', '核查门窗、空调运行状态与背景噪声，形成采样条件记录'),
 'pmp.lifecycle.A16.source': (2, '工艺用水外部条件核查', '核对原水、排水和废液去向资料及现场接口；不包含外部工程等待'),
 'pmp.lifecycle.A20.external_facility': (2, '验证对象及安装调试资料核查', '核对设施范围、安装调试记录和验证可用性；不代替外部设施交付'),
 'pmp.lifecycle.A22.utility_scope': (2, '设备介质与既有公用系统能力核查', '核对设备清单、容量资料及现场接口，不代表未确认容量已满足'),
 'pmp.interface.external_it_environment': (2, '既有IT环境与恢复条件核查', '检查目标环境、恢复证据及未决项，不包含他方建设工期'),
 'pmp.interface.external_cleanroom_hvac': (2, '洁净区既有HVAC运行条件核查', '检查运行记录、控制接口及测试条件，不代表条件已确认'),
 'pmp.module.vault_security.4': (2, '安防复测与管理权限移交', '复核测试记录、关闭已列缺陷、完成管理权限交接；重大整改另列'),
 'pmp.module.lab_hvac.4': (1, '通风系统联合验收', '见证检查测试记录与安全联锁，形成验收意见'),
 'pmp.module.esd_grounding.4': (1, '静电防护联合验收', '核对测试记录、接地点标识及使用边界'),
 'pmp.module.cda_piping.4': (1, '所选介质系统联合验收', '系统试验完成后见证核对记录、质量指标及使用边界；仅核对已选介质，不扩展未选系统'),
 'pmp.lifecycle.A19.early_release': (1, '专项环境与EHS实施条件核验', '核对已声明专项要求、文件与现场条件；不替代行政审批，不表示未取得的批准已完成'),
}
NAME_WORK = {
 'Kick Off Meeting 项目启动会': (1, '项目启动会', '准备材料已具备；召开启动会并形成行动项'),
 '前期规划与项目任务书确认': (1, '项目任务书评审与意见闭合', '需求编制已完成，组织决策评审并关闭意见'),
 '场地移交与动工准备 Site Takeover': (1, '现场联合查验与移交', '联合检查场地、记录现状和进场边界，不包含临设施工'),
}

def apply_work_duration_review(tasks):
    changes=[]
    for t in tasks:
        if t.get('outline_level',3)<3 or t.get('duration_days',0)!=0:
            continue
        uid=t.get('activity_uid','');stage=t.get('industry_stage')
        rule=WORK.get(uid) or NAME_WORK.get(t['name'])
        if stage=='design':
            rule=(2, t['name'], '施工图完成后的业主/使用部门设计复核与意见闭合；前序岗位接口核对负责输入，本任务负责最终图纸审查')
        elif uid.startswith('pmp.specialist.'):
            rule=(1,t['name'],'独立现场见证检查、记录及放行意见；前序安装工期不包含本次检查，重大整改另列')
        if rule:
            days,label,scope=rule
            old=t['name']
            # Preserve semantic names used by existing interfaces; client names are explicit.
            t.update(duration_days=days,duration=days,milestone=False,
                     duration_authority='pm_work_allowance',duration_basis='provisional_pm_allowance_not_calibrated',
                     work_duration_review=dict(previous_days=0,adopted_days=days,scope=scope,confirmation='pending_project_confirmation'))
            if uid in WORK:t['name']=label
            if uid == 'pmp.lifecycle.A20.external_facility':
                t.update(activity_class='CONSULTANT_REVIEW', activity_type='document_review', physical_work=False)
            t['input_notes']=str(t.get('input_notes',''))+'\n工期复核：'+scope+f'；暂留{days}工作日，单一项目/工作面规划值，须按参与方及实际工作量校准。原零天放行口径由本工作包替代。'
            changes.append(dict(uid=uid,name=old,days=days,scope=scope))
        elif stage in ('requirements','acceptance','release'):
            # Positive industry work already exists upstream; do not count it twice.
            t['completion_scope_caption']={'requirements':'行业需求批准','acceptance':'行业使用验证通过','release':'行业运行条件放行'}[stage]
            t['duration_basis']='前序行业工作包承担实际核对/验证/移交；此处仅记录成果批准，不重复计时'
            t['name']={'requirements':'行业需求批准','acceptance':'行业使用验证通过','release':'行业运行条件放行'}[stage]
            t['input_notes']=str(t.get('input_notes',''))+'\n此零天节点只记录前序实际工作成果，不是零天开展核对或测试。'
    if tasks:tasks[0]['work_duration_review']=dict(changes=changes,basis='明确工作范围后的PM规划预留，未取得项目校准或工程签认')
    dependency_order(tasks)
    return tasks
