"""Complete the declared single-facility specialist interfaces at Master level.

Reuse existing positive-duration work. New zero-day nodes are inspection or
external-evidence releases whose work is allocated to an existing work package.
No technical threshold or project approval is inferred from an industry label.
"""
from core.module_graph import add_pred, dependency_order
from core._common import parse_predecessor
from core.module_catalog import CATALOG
from core.module_lifecycle import LEGACY, CARDS
from core.specialist_contracts import CONTRACTS, PAIR_BOUNDARIES


def complete_specialist_interfaces(tasks, facts, plan):
    selected=set(plan['selected']); external=set(plan['external'])
    for t in tasks:
        if t.get('activity_type') and not t.get('activity_uid'):t['activity_uid']='pmp.core.'+t['activity_type']
    seq=max(t['id'] for t in tasks)+1; edges=[]; gates=[]
    def uid(key):return next(t for t in tasks if t.get('activity_uid')==key)
    def life(i,key):return uid('pmp.lifecycle.'+i+'.'+key)
    def step(pack,n):return next(t for t in tasks if t.get('module_id')==pack and t.get('module_step')==n)
    def activity(kind):return next(t for t in tasks if t.get('activity_type')==kind)
    def find(text):return next(t for t in tasks if text in t['name'])
    def output(i):return step('tdd',5) if i=='A01' else life(i,'release') if i in selected else next(t for t in tasks if t.get('module_id')=='external_'+i)
    def wire(after,before,why):
        add_pred(after,before)
        edges.append(dict(predecessor_uid=before.get('activity_uid'),predecessor_name=before['name'],successor_uid=after.get('activity_uid'),successor_name=after['name'],reason=why))
    def evidence(i,key,name,preds,acceptance,unverified=False):
        nonlocal seq
        n=dict(id=seq,name=name,outline_level=3,duration_days=0,duration=0,milestone=True,calendar='5D',
               predecessors=','.join(str(t['id'])+'FS' for t in preds),module_id=i,
               activity_uid='pmp.specialist.'+i+'.'+key,specialist_phase='实体施工',
               activity_class='CONSULTANT_REVIEW',owner=CARDS[i]['主责与确认'],
               responsible_unit=CARDS[i]['主责与确认'],responsible_person='具名负责人待项目指定',
               acceptance=acceptance,interface_status='unverified_external' if unverified else 'planned_not_verified',
               evidence_required=True,duration_authority='release_gate',
               input_notes='完成依据：'+acceptance+'；核查工作纳入关联工作包，零天仅表示签认放行；不表示现场已完成。',
               duration_basis='核查已包含在关联工作包；此节点记录结果放行，无新增持续时间')
        seq+=1;tasks.append(n);gates.append(n)
        for p in preds:edges.append(dict(predecessor_uid=p.get('activity_uid'),predecessor_name=p['name'],successor_uid=n['activity_uid'],successor_name=name,reason=acceptance))
        return n
    wall=find('隔墙及主管管线隐蔽');ceiling=find('天花隐蔽工程验收');physical=find('物理竣工')
    power=uid('pmp.integration.power_ready');hvac=uid('pmp.integration.hvac_ready')
    rough=activity('partition_framing');path=activity('containment');paint=activity('painting')
    floor=activity('raised_floor');tc=activity('testing_commissioning')
    contracts={}
    for i in sorted(selected & CONTRACTS.keys()):
        label,work,acceptance,boundary=CONTRACTS[i]
        owned=[t for t in tasks if t.get('module_id')==i or t.get('activity_uid','').startswith('pmp.lifecycle.'+i+'.')]
        if i in LEGACY:
            pack,first,last=LEGACY[i]
            owned += [t for t in tasks if t.get('module_id')==pack and not t.get('outline_level')==2]
        for t in owned:
            t['input_notes']='\n'.join([t.get('input_notes',''),'总控工作范围：'+work,'完成证据：'+acceptance,'唯一责任边界：'+boundary])
        release=output(i);release['acceptance']=acceptance
        contracts[i]=dict(label=label,scope=work,evidence=acceptance,boundary=boundary,output_uid=release.get('activity_uid'),
                          status='master_interfaces_implemented',project_approval='pending',duration_calibration='not_performed')
    if 'A01' in selected:
        names={1:'尽调资料与使用需求核对',2:'现状勘测与容量核查',3:'缺口、风险及责任界面评估',4:'整改条件与技术尽调报告',5:'租赁技术条件及决策确认'}
        for n,name in names.items():step('tdd',n)['name']=name
        # Confirmed TDD evidence controls the industry brief through the existing
        # lease decision edge; no demand to undertake construction in this module.
    if 'A02' in selected:
        wire(rough,output('A02'),'同一受影响工作面结构使用放行后才能进行后续隔墙及机电粗装')
        life('A02','works')['name']='结构改造及过程检验'
        life('A02','inspection')['name']='养护检测及荷载条件核查'
    if 'A03' in selected:
        wire(life('A03','supply'),find('施工图全套确认'),'供电设备采购须与批准施工图和保护配合一致')
        life('A03','inspection')['name']='供电设施检验与保护整定'
        life('A03','energize')['name']='停送电窗口与操作授权'
    if 'A06' in selected:
        # Rigging goes in before this room's ceiling is closed; cameras mount after the room's finishes.
        wire(step('mocap',4),paint,'相机安装与光学校正使用收口完成的房间')
        if 'A07' in selected:wire(life('A06','performance'),output('A07'),'共同房间声学整改关闭后再进行最终标定与代表动作验收')
        wire(life('A06','performance'),step('mocap',4),'设备安装与控制网络完成后标定')
        # 相机上架在项目消防验收之后；物理竣工不得等待相机，否则与消防验收形成环路。
        if optical := next((t for t in tasks if t.get('mocap_subtask')=='mocap_optical_finishes'), None):
            wire(physical, optical, '动捕房间构造及完成面计入实体完工；光学相机在消防验收后上架')
    if 'A08' in selected:
        step('dev_zone',0)['name']='研发工位增量管线与冷却预留'
        life('A08','performance')['name']='研发工位末端接入与带载验证'
        for p in (activity('power'),activity('structured_cabling')):wire(life('A08','performance'),p,'末端接入及验证消费公共回路与网络端口；隐蔽预留保持在封闭前')
        wire(life('A08','performance'),hvac,'代表负载测试使用已平衡的实际冷却条件')
        wire(physical,step('dev_zone',0),'高密工位配套安装计入实体完工')
    if 'A09' in selected:
        for n in (1,2):wire(step('trading_desk',n),path,'交易岗位专用支线接入共用桥架路径')
        wire(step('trading_desk',4),hvac,'业务及恢复场景测试采用实际运行环境')
        if 'A05' in selected:wire(step('trading_desk',4),output('A05'),'迁移业务就绪后验证交易岗位；切换执行仅由IT迁移包承担')
        wire(physical,step('trading_desk',2),'交易岗位线路安装计入实体完工；桌面部署仍等待家具')
    if 'A11' in selected:
        gate=evidence('A11','enclosure','保管空间围护检查',[step('vault_security',1)],'核对专用围护、门框锚固及设备穿透记录；允许封闭')
        wire(step('vault_security',2),gate,'保管空间围护隐蔽部位先检查再封闭并安装设备')
        wire(physical,step('vault_security',1),'保管空间围护及专用门体纳入实体完工')
    if 'A12' in selected:
        gate=evidence('A12','concealed','专用通风隐蔽检查',[step('lab_hvac',2)],'支吊架、管路严密性及穿透处理记录；排风与公共回风分界确认')
        wire(step('lab_hvac',3),gate,'本房间专用通风隐蔽检查完成后封闭并调试')
        wire(step('lab_hvac',3),hvac,'在补风及公共空调运行状态下完成平衡和安全联锁验证')
    if 'A13' in selected:
        wire(step('cleanroom_iso',1),rough,'受控围护使用已协调的房间定位和骨架接口')
        gate=evidence('A13','concealed','受控围护隐蔽检查',[step('cleanroom_iso',1)],'穿透密封、末端和不可达部位检查；按批准范围允许封闭')
        wire(step('cleanroom_iso',2),gate,'受控环境不可达接口检查后再封闭本房间')
        env=evidence('A13','test_state','受控环境检测状态确认',[step('cleanroom_iso',2),hvac],
                     '记录清洁、过滤器、人员设备及运行状态；参数和检测范围按批准方案')
        if 'A12' in external:wire(env,output('A12'),'复用或他方通风必须交付已核验运行证据')
        wire(step('cleanroom_iso',3),env,'环境检测不能只等待设备通电，应具备清洁和运行状态')
        wire(physical,step('cleanroom_iso',2),'受控空间围护及地面安装纳入实体完工')
    for i,n in [('A14',1),('A15',2)]:
        if i not in selected:continue
        gate=evidence(i,'concealed',('工艺气体' if i=='A14' else '压缩空气')+'管网隐蔽检查',[step('cda_piping',n)],
                      '管材与焊接记录、适用压力检漏和隐蔽检查完成；不以最终带载测试代替封闭前检查')
        wire(ceiling,gate,'封闭前验明介质管网安装与检验记录')
        wire(life(i,'performance'),gate,'投入介质和带载验证以前置检验为依据')
        wire(physical,step('cda_piping',n),'介质设施及管网安装纳入实体完工')
    if 'A16' in selected:
        gate=evidence('A16','concealed','工艺水管网隐蔽检查',[life('A16','works')],
                      '材料、连接、适用试压、排水去向与取样点检查记录')
        wire(ceiling,gate,'隐蔽水管网须检查后封闭')
        wire(life('A16','cleaning'),gate,'以安装检验记录作为清洗循环输入')
    if 'A17' in selected:
        wire(step('anti_vibration',1),activity('setting_out'),'隔振构造按实际设备位置及标高实施')
        if 'A22' in external:
            wire(step('anti_vibration',3),output('A22'),'他方设备就位与实际运行条件作为振动测试输入')
        elif 'A22' not in selected:
            gate=evidence('A17','equipment_state','振动测试设备工况核验',[step('anti_vibration',2)],
                          '确认既有或他方设备位置、运行状态和允许测试窗口',True)
            wire(step('anti_vibration',3),gate,'不能以仅完成隔振基础代表设备运行振动合格')
        wire(physical,step('anti_vibration',2),'隔振设施安装属于物理完工范围')
    if 'A18' in selected:
        step('esd_grounding',1)['name']='静电防护接地及连接点施工'
        step('esd_grounding',3)['name']='静电防护连续性及适用电阻检测'
        gate=evidence('A18','concealed','静电防护接地点隐蔽检查',[step('esd_grounding',1)],
                      '封闭前核对接地点、连续性与标识；保留可测和可维护界面')
        wire(step('esd_grounding',2),gate,'本区域覆盖前确认地面下的防护连接点')
        wire(step('esd_grounding',2),floor,'静电工位与适用面层接口使用已交付基础地面')
        wire(physical,step('esd_grounding',2),'静电防护设施安装纳入物理完工')
    if 'A19' in selected:
        life('A19','early_release')['acceptance']='按活动和责任主体核验实施前条件；需取得的外部文件不得用计划日期代替'
        life('A19','monitor')['acceptance']='按适用范围整理监测记录、设施性能及偏差整改证据；不推定法定办理时长'
        for p in (power,):wire(life('A19','safe_introduction'),p,'控制设施带电检查后才确认介质引入安全条件')
    if 'A20' in selected:
        # IQ consumes installation evidence; OQ consumes commissioned technical
        # outputs. Unrelated IT, business or FM service release is not a blanket
        # prerequisite of facility qualification.
        iq=life('A20','iq');all_releases={output(i)['id'] for i in selected-{'A01','A05','A20','A21'}}
        iq['predecessors']=','.join(str(p)+r+(f'{lag:+d}' if lag else '') for p,r,lag in parse_predecessor(iq.get('predecessors','')) if p not in all_releases)
        installed={'A02':lambda:life('A02','inspection'),'A03':lambda:life('A03','works'),
          'A04':lambda:step('datacenter',6),'A12':lambda:step('lab_hvac',2),
          'A13':lambda:step('cleanroom_iso',2),'A14':lambda:step('cda_piping',1),
          'A15':lambda:step('cda_piping',2),'A16':lambda:life('A16','works'),
          'A17':lambda:step('anti_vibration',2),'A18':lambda:step('esd_grounding',2),
          'A22':lambda:life('A22','hookup')}
        candidates=sorted((selected|external)&installed.keys())
        for i in candidates:
            wire(iq,installed[i]() if i in selected else output(i),'候选确认对象的安装证据进入安装确认；具体对象由质量策略批准')
            wire(life('A20','oq'),output(i),'运行确认复用已完成的技术测试证据，不重复安排同一调试')
        if not candidates:
            evidence_gate=next((t for t in tasks if t.get('activity_uid')=='pmp.lifecycle.A20.external_facility'),None)
            if evidence_gate is None:evidence_gate=evidence('A20','object_records','确认对象安装与调试资料核验',[life('A20','strategy'),physical],'质量方确认实际对象及其安装调试证据，缺失资料保持待核验',True)
            wire(iq,evidence_gate,'无已选设施候选对象时不得把质量确认范围视为空或已完成')
        plan['qualification_scope']=dict(candidate_modules=candidates,status='pending_quality_owner_approval',
             rule='只将所选设施和公用系统列为候选对象；业务切换、动捕、交易岗位和FM服务不自动纳入')
        life('A20','strategy')['name']='确认对象与验证策略批准'
        life('A20','iq')['name']='安装确认及偏差记录'
        life('A20','oq')['name']='运行确认与偏差关闭'
        life('A20','pq')['name']='适用性能确认与质量评审'
        life('A20','strategy')['acceptance']='质量负责人批准对象、追溯矩阵、适用确认阶段与可复用调试证据；不因行业标签自动要求所有阶段'
        life('A20','pq')['input_notes']+='\n适用性：是否需要独立性能确认及其范围由质量方在验证策略中确定；未确认时保留条件节点。'
    if 'A21' in selected:
        # Separate the extended service from the common contractor training:
        # consume its records, rather than scheduling the same training twice.
        life('A21','training')['name']='运维服务演练与接管验证'
        wire(life('A21','assets'),step('core_handover',0),'CMMS与维护计划使用共同交付包的竣工资产资料')
        wire(life('A21','training'),step('core_handover',1),'扩展服务验证复用承包商操作维护培训成果')
    if 'A22' in selected:
        for i in ('A13','A17'):
            if i in external:wire(life('A22','performance'),output(i),'设备性能验收须取得他方环境或振动条件记录')
        wire(life('A22','performance'),hvac,'设备带载性能测试在可用环境条件下进行')
    # External facility releases do not silently satisfy downstream conditions.
    # Existing selected-provider edges remain authoritative; missing upstream
    # construction never triggers an automatic scope addition.
    for provider,consumer,target in [('A02','A13',('cleanroom_iso',1)),('A02','A18',('esd_grounding',1))]:
        if provider in selected|external and consumer in selected:wire(step(*target),output(provider),'受影响工作面按已确认结构使用条件释放')
    external_hooks={
      'A07':[('A06',lambda:life('A06','performance'))],
      'A19':[('A12',lambda:step('lab_hvac',3)),('A13',lambda:step('cleanroom_iso',3)),
             ('A14',lambda:life('A14','performance')),('A15',lambda:life('A15','performance')),
             ('A16',lambda:life('A16','cleaning')),('A22',lambda:life('A22','performance'))],
      'A10':[('A11',lambda:step('vault_security',3))],
    }
    for provider,consumers in external_hooks.items():
        if provider not in external:continue
        for consumer,target in consumers:
            if consumer in selected:wire(target(),output(provider),'复用或他方专项的条件证据须核验后才能完成下游测试；不自动增建上游系统')
    for i,contract in contracts.items():
        owned=[t for t in tasks if t.get('module_id')==i]
        if i in LEGACY:owned += [t for t in tasks if t.get('module_id')==LEGACY[i][0]]
        contract['activity_uids']=sorted({t['activity_uid'] for t in owned if t.get('activity_uid') and t.get('outline_level')!=2})
        contract['interfaces']=[e for e in edges if e['successor_uid'] in contract['activity_uids'] or e['predecessor_uid'] in contract['activity_uids']]
    plan['specialist_contracts']=contracts
    plan['combination_rules']=[dict(modules=list(pair),rule=rule,status='applied_to_declared_shared_facility') for pair,rule in PAIR_BOUNDARIES if set(pair)<=selected]
    plan.setdefault('process_interfaces',[]).extend(edges)
    plan['integration_revision']='rc7_specialist_master_interfaces'
    plan['interface_assumptions'].append('范围分界按单设施共享工作面协调；具体分区、资产和检验适用性须经项目确认。未选上游系统不自动新建。')
    dependency_order(tasks)
    return tasks
