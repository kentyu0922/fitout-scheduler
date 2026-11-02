"""Client names; technical scope stays in the source work and notes."""
LIFE_NAMES={
'A02':{'assessment':'结构现状与荷载评估','consent':'结构作业条件确认','supply':'加固材料与资源落实','works':'结构改造及过程检验','inspection':'养护检测及荷载核查','release':'结构使用放行'},
'A03':{'capacity':'供电容量与分界核查','consent':'供电方案与实施授权','supply':'供电设备采购','works':'外电及配电设施安装','inspection':'供电检验与保护整定','energize':'停送电窗口与操作授权','release':'供电容量移交'},
'A06':{'performance':'动捕标定与场景验收','release':'动捕使用交接'},
'A08':{'supply':'研发配套设备采购','performance':'研发末端接入与带载验证','release':'研发工位配套交接'},
'A09':{'release':'交易业务验收交接'},'A11':{'supply':'保管设施采购','release':'保管空间使用交接'},
'A12':{'release':'实验通风运行交接'},'A13':{'release':'受控环境使用交接'},
'A14':{'performance':'气体质量及安全联锁验证','release':'工艺气体使用交接'},
'A15':{'performance':'压缩空气质量与带载验证','release':'压缩空气使用交接'},
'A16':{'source':'工艺水取排水条件核验','supply':'工艺水设备采购','works':'工艺水设备及管网安装','cleaning':'工艺水清洗与循环调试','sampling':'运行取样与水质验证','release':'工艺水使用放行'},
'A17':{'supply':'隔振设施采购','release':'微振测试与使用交接'},
'A18':{'supply':'静电防护材料采购','operation':'静电防护培训与点检交接','release':'静电防护使用交接'},
'A19':{'applicability':'环境与EHS适用事项确认','early_release':'专项实施条件核验','controls':'环境与安全控制设施安装','safe_introduction':'介质引入安全条件核验','monitor':'环境监测与验收证据整理','release':'环境与EHS运行条件确认'},
'A20':{'strategy':'确认对象与验证策略批准','dq':'设计确认与方案批准','iq':'安装确认及偏差记录','oq':'运行确认与偏差关闭','pq':'适用性能确认与质量评审','external_facility':'确认对象资料核验','release':'质量放行'},
'A21':{'organization':'运维组织与接管标准确认','plan':'维护计划与服务资源准备','assets':'资产数据与维护计划装载','training':'运维服务演练与接管验证','release':'运维服务接管'},
'A22':{'supply':'专业设备采购与到货','logistics':'设备运输与吊装方案确认','placement':'设备就位与调平','hookup':'公用介质核验与设备接入','performance':'设备联锁与性能验收','utility_scope':'设备介质及既有能力核验','release':'设备使用放行'},
}
STEP_NAMES={('lab_hvac',1):'专用送排风管网安装',('lab_hvac',2):'排风末端与处理设施安装',
('cleanroom_iso',1):'受控围护与空气末端安装',('cleanroom_iso',2):'受控区域地面与密封',
('anti_vibration',1):'隔振基础及设施施工',('anti_vibration',2):'隔振设施就位条件检查',
('esd_grounding',1):'静电防护接地及连接点',('esd_grounding',2):'静电防护面层与工位连接'}
def specialist_name(task,preferred):
    if task.get('master_group_key'):return preferred
    i=task.get('module_id');key=task.get('lifecycle_step')
    # Preserve aggregated requirements/design names chosen by the master layer.
    if key not in {'requirements','design'} and key in LIFE_NAMES.get(i,{}):return LIFE_NAMES[i][key]
    if preferred==task.get('name'):return STEP_NAMES.get((i,task.get('module_step')),preferred)
    return preferred
