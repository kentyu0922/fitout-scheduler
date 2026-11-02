"""Shared scope meanings for prompt extraction, intake guidance and audit.

These are project selection rules, not legal or industry-wide mandates.
Natural language is interpreted by the agent with quoted evidence, never by
keyword matching. Industry alone cannot select a physical system.
"""
TRIGGERS = {
 'A01':'委托租前技术尽调', 'A02':'经确认需要结构改造或加固',
 'A03':'确认现有供电不能满足项目负荷、需要外电增容',
 'A04':'本项目需要建设或改造独立机房',
 'A05':'现有业务或IT设备需要迁移、割接或回退安排',
 'A06':'委托建设动捕系统', 'A07':'明确专业隔声、混响或录音性能要求',
 'A08':'已确认普通办公配套不能满足的高密研发测试负荷',
 'A09':'明确建设交易席位及其业务系统接口',
 'A10':'明确超出普通办公门禁安防的防护范围',
 'A11':'明确建设金库或特殊保管空间',
 'A12':'明确实验活动需要本项目提供实验设施或专用排风补风',
 'A13':'明确受控环境指标和建设范围',
 'A14':'设备或活动要求本项目提供工艺气体',
 'A15':'设备要求本项目提供洁净干燥压缩空气',
 'A16':'设备或过程要求本项目提供工艺用水',
 'A17':'明确设备振动限值且本项目承担微振控制',
 'A18':'明确静电敏感作业及ESD防护工作区',
 'A19':'经适用性判断，需要专项环境设施、手续或EHS条件落实',
 'A20':'质量方确认存在受监管设施及本项目确认验证范围',
 'A21':'明确委托超出基本资料培训接管的扩展FM服务',
 'A22':'明确专业设备进场、就位与公用工程接驳范围',
}
QUESTIONS = {
 'office':'是否有普通办公配套以外的设施、设备或业务迁移需求？已有明确选项不再询问。',
 'gaming':'除日常办公外，是否涉及高密开发测试设备、专业录音或动捕空间？仅说明游戏行业不能视为需要。',
 'finance':'本项目仅后台办公，还是涉及交易席位、特殊保管或强化防护？现有系统是否需要迁移？',
 'biopharma':'是否实际进行实验、处理或存放样品及特殊物料？若有，请确认活动、设备及设施提供方。',
 'semiconductor':'仅设计办公，还是有硬件测试或专业设备？若有，请提供设备及电力、介质、环境接口要求。',
}

def selection_decisions(project, states):
    return {i:dict(state=state, trigger=TRIGGERS[i],
                   basis='explicit_scope_or_existing_fact' if state!='unknown' else 'not_confirmed',
                   industry_is_evidence=False,
                   action={'required':'load', 'excluded':'omit', 'unknown':'clarify_if_material',
                           'existing_claimed':'verify_external_interface',
                           'provided_by_others':'retain_external_interface'}[state])
            for i,state in states.items()}
