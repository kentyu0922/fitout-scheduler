"""Connect specialist work to the office trades that supply and consume it.

The contract is a single fit-out facility. Workface/asset allocation still needs
project drawings; readiness is scheduled work, never a claim of completion.
IDs stay internal. Display order follows the original project phases.
"""
from core.module_graph import add_pred, remove_nodes, dependency_order
from core.module_catalog import CATALOG
from core._common import parse_predecessor
import re

# Brief and design deliverables differ by scope. These replace generic captions.
DELIVERABLES = {
 'A02':('复核现状结构、设备荷载与加固范围','完成加固节点、施工顺序与临时支撑设计','结构检测记录、荷载限制与使用移交单'),
 'A03':('核算新增用电负荷并核实上级电源余量','确认供电分界、保护配合与停送电方案','试验报告、保护整定记录和送电操作移交单'),
 'A04':('编制机柜功率、冷量及机房运行要求表','协调机柜布置、配电冷却、消防及网络接口','供配电试验、冷却测试、消防接口记录和机房接收单'),
 'A05':('盘点应用、网络、设备及业务中断限制','编制迁移批次、割接步骤与回退方案','业务测试记录、割接日志、遗留问题及支持交接单'),
 'A06':('确认采集空间、设备清单与动作测试场景','协调相机视线、安装支架和控制网络点位','相机标定结果、动作采集样片与用户验收记录'),
 'A07':('确认房间用途、隔声、混响及背景噪声指标','完成墙顶地、声学门及机电穿透节点设计','隔声、混响和运行背景噪声测量报告'),
 'A08':('统计研发测试工位的电力、散热与网络需求','复核高密工位布置、供电回路和冷却分区','代表负载下的供电、温度和网络验证记录'),
 'A09':('盘点交易岗位、业务系统与恢复要求','协调终端布局、配电网络及业务系统接口','岗位连通性、权限与约定恢复场景测试记录'),
 'A10':('编制人员权限、监控覆盖及报警分区表','协调门表、设备点位、布线路由和消防释放逻辑','逐点测试、权限矩阵、报警记录和消防释放测试单'),
 'A11':('确认保管对象、出入流程与空间防护要求','完成围护、专用门体、荷载及安防接口设计','门体与围护验收、报警测试和保管责任移交记录'),
 'A12':('盘点实验操作、设备排风和补风需求','完成排风补风、排放路径与控制联锁设计','风量平衡、压差及通风联锁测试报告'),
 'A13':('确定受控区域、洁净等级及测试状态','协调围护气密、过滤末端与送回风路径','过滤器检漏、洁净度和压差检测记录'),
 'A14':('确认气体种类、用量、终端压力与危害','完成管路、供气柜、排风及报警切断设计','压力检漏、吹扫纯度与报警切断试验记录'),
 'A15':('核实用气量、压力、露点和空气质量要求','完成机组、干燥过滤及管网末端设计','压力、露点、颗粒和含油测试记录'),
 'A16':('核实设备用水量、水质和排水去向','完成水处理、循环管网和取样点设计','清洗记录、运行取样结果及水质放行单'),
 'A17':('测量背景振动并核对设备振动限值','完成隔振基础、连接软接与传振路径设计','安装检查及设备运行工况振动测试报告'),
 'A18':('确定静电敏感器件、作业区与防护边界','完成接地点、地面、工位及人员接地设计','接地及电阻测试、人员防护培训和点检记录'),
 'A19':('识别项目活动、危害源及需办理的环境手续','落实排放、收集处理、监测与应急设施设计','设施检查、监测及适用手续和运行条件记录'),
 'A20':('确定受监管对象、质量风险和验证责任','审查用户需求追溯矩阵及设计确认文件','已批准的确认方案、原始记录、偏差和质量放行文件'),
 'A21':('确认设施运维服务、人员及资产接管范围','编制维护计划、备件和运维数据接收要求','资产数据、培训考核和服务接管记录'),
 'A22':('核实设备外形、重量、功耗及公用介质清单','完成设备布置、运输路径及接驳条件设计','到货检查、就位接驳、联锁及设备性能验收记录'),
}

# Actual owner/consultant work, distributed through the project, not a detached
# set of five milestone labels. Office use never implies laboratory fabrication.
INDUSTRY_WORK = {
 'office':[
  ('逐部门核定人数、席位与会议室需求','核对访客、收纳和行政服务清单'),
  ('核对工位与会议室的电源、数据及设备点位','会审访客流线、家具布局与设施维护空间'),
  ('现场核对工位、会议室及公共区点位和标识',),
  ('测试工位接入、会议设备及访客门禁场景','组织行政与使用部门巡检并关闭使用缺陷'),
  ('移交办公使用指引、保修联系人与报修流程',)],
 'gaming':[
  ('逐团队盘点开发、测试工作站和协作工位','确认项目保密分区、评审会议及延时使用需求'),
  ('核对开发测试工作站的供电、数据与散热条件','会审制作协作区、安静工作区及项目访问边界'),
  ('按研发工位表核对回路、数据端口和区域标识',),
  ('验证开发测试工作站接入和项目区访问权限','验证版本评审会议及延时使用的设施服务场景'),
  ('移交研发办公设施支持、权限申请与故障响应流程',)],
 'finance':[
  ('区分后台办公、客户接待与限制访问岗位','确认资料存放、会议保密及可容忍中断要求'),
  ('会审员工访客流线、受限门点及保密会议空间','核对业务岗位通信设施和现有恢复安排的接口'),
  ('按门表和岗位表核对门点、数据端口及资料区施工',),
  ('按员工、访客和维护角色测试门禁与接待流程','验证办公通信、保密会议及故障报告流程'),
  ('移交权限维护、风险遗留事项和办公恢复责任',)],
 'biopharma':[
  ('逐房间确认行政、研发文档及协作办公用途','与用户及EHS核对样品、化学品和实验活动清单'),
  ('会审办公布局、房间用途表与获批活动的一致性','核对办公设备及资料存放的机电和访客访问需求'),
  ('对照房间用途表核查现场点位、标识与范围变更',),
  ('验证办公终端、会议协作和访客访问流程','由用户与EHS核查入驻物品及房间用途执行情况'),
  ('移交办公用途清单、受限活动及后续变更申请流程',)],
 'semiconductor':[
  ('盘点芯片设计工作站、显示设备及研发协作岗位','确认设计办公与硬件测试活动边界及数据访问需求'),
  ('核算设计工作站负荷并复核回路、散热和数据端口','会审研发区域权限、协作空间及未来设备预留'),
  ('按工作站配置表核对回路、数据端口和设备预留',),
  ('验证设计工作站接入、权限与代表负载使用场景','核查办公热环境、协作会议和研发支持响应流程'),
  ('移交研发工作站接口清单及新增设备变更流程',)],
}

def integrate_modules(tasks, facts, plan):
    selected=set(plan['selected']); external=set(plan['external'])
    seq=max(t['id'] for t in tasks)+1
    scheduled=[]; placements={}; edges=[]
    def find(text):
        return next(t for t in tasks if text in t['name'])
    def step(module,n):
        return next(t for t in tasks if t.get('module_id')==module and t.get('module_step')==n)
    def life(i,key):
        return next(t for t in tasks if t.get('activity_uid')=='pmp.lifecycle.'+i+'.'+key)
    def activity(key):
        return next(t for t in tasks if t.get('activity_type')==key)
    def wire(target,pred,reason):
        add_pred(target,pred)
        edges.append(dict(predecessor=pred['id'],successor=target['id'],reason=reason))
    def place(task,anchor,before=True,level=3):
        placements[task['id']]=(anchor['id'],before,level)
    def new(key,name,preds,days,kind,owner,acceptance,anchor,before=True,level=3,module='integration'):
        nonlocal seq
        t=dict(id=seq,name=name,outline_level=level,duration_days=days,duration=days,
               milestone=days==0,calendar='5D',predecessors=','.join(str(p['id'])+'FS' for p in preds),
               activity_uid='pmp.integration.'+key,module_id=module,activity_class=kind,
               owner=owner,acceptance=acceptance,evidence_status='pending_project_confirmation',
               duration_authority='uncalibrated_module_planning' if days else 'release_gate',
               duration_basis='新补充工序的规划暂估，未校准；项目审定后调整' if days else '成果放行节点，实际核查由其前置工作承担')
        seq+=1;scheduled.append(t);place(t,anchor,before,level)
        for p in preds:edges.append(dict(predecessor=p['id'],successor=t['id'],reason=acceptance))
        return t
    concept=find('Test Fit');drawing=find('全专业施工图'); finaldesign=find('施工图全套确认')
    site=activity('setting_out');physical=find('物理竣工')
    wallcheck=find('隔墙及主管管线隐蔽工程验收');ceilingcheck=find('天花隐蔽工程验收')
    tc=activity('testing_commissioning'); power=activity('power'); cables=activity('structured_cabling')
    doors=activity('doors');fire=activity('fire_alarm');containment=activity('containment')
    fcu=activity('fcu');diffuser=activity('diffuser');painting=activity('painting')
    office_ready=step('office_ff_move',3);handover=step('core_handover',3)
    brief=find('(Brief)');sector=facts['sector']
    profile_sector='office' if facts.get('industry_work_profile')=='general_office' else sector
    industry={t['industry_stage']:t for t in tasks if t.get('industry_stage')}
    requirements=industry['requirements']
    planning=next((t for t in tasks if '前期规划与项目任务书确认' in t['name']),None)
    if planning is None:
        header=next(t for t in tasks if t.get('outline_level')==2 and '总承包' in t['name'])
        planning=new('brief_release','项目需求任务书确认',[requirements],0,'CLIENT_APPROVAL',
                     '业主项目经理、使用部门','行业及专项需求纳入发包人任务书，未决项注明处理责任',header)
        first_procurement=tasks[tasks.index(header)+1]
        wire(first_procurement,planning,'设计施工一体发包也须使用完整需求任务书')
    # Replace detached generic management captions, and feed the drawings rather
    # than only a later final approval milestone.
    for i in sorted(selected-{'A01'}):
        req=life(i,'requirements');req['name']=DELIVERABLES[i][0];req['acceptance']=DELIVERABLES[i][0]+'；用户签认并登记未决项'
        place(req,planning)
        wire(planning,req,'专项使用需求先进入项目任务书和后续发包范围')
        if i not in {'A05','A21'}:
            des=life(i,'design');des['name']=DELIVERABLES[i][1];des['acceptance']='对应专业图纸、计算书与接口表已完成协调'
            wire(drawing,des,'专项设计必须进入施工图，不在出图后补签')
            place(des,drawing)
        close=life(i,'release');close['name']=CATALOG[i]['label']+'验收资料移交及使用批准';close['acceptance']=DELIVERABLES[i][2]
        # Records needed by the operator are available for the core FM training.
        if i not in {'A05','A21'}:wire(step('core_handover',1),close,'运维培训须使用已核验的专项系统及记录')

    # Real industry work is inserted alongside the native brief, design,
    # inspections and occupancy work. The five existing gates remain traceable.
    for index,stage in enumerate(('requirements','design','inspection','acceptance','release')):
        # Industry work is added to the general office work, never instead of it:
        # a biopharma office still has workstation points and a warranty handover.
        general=INDUSTRY_WORK['office'][index]
        # Industry items lead (they name the merged package); general items stay in its scope.
        names=(tuple(INDUSTRY_WORK[profile_sector][index]) if profile_sector!='office' else ())+tuple(general);gate=industry[stage]
        if stage=='requirements':preds=[brief];anchor=requirements;kind='CLIENT_APPROVAL'
        elif stage=='design':preds=[concept,requirements];anchor=drawing;kind='DESIGN'
        elif stage=='inspection':preds=[activity('partition_framing'),containment];anchor=wallcheck;kind='CONSULTANT_REVIEW'
        elif stage=='acceptance':preds=[office_ready,tc];anchor=handover;kind='TESTING_COMMISSIONING'
        else:preds=[industry['acceptance'],step('core_handover',2)];anchor=handover;kind='PROJECT_MANAGEMENT'
        for number,name in enumerate(names,1):
            work=new('industry.'+sector+'.'+stage+str(number),name,preds,2 if stage in {'requirements','design'} else 1,
                     kind,gate['owner'],gate['acceptance'],anchor,level=4 if stage=='inspection' else 3,module='industry_work_'+sector)
            work.update(industry_work_stage=stage,industry=sector,industry_profile=profile_sector,physical_scope_added=False,
                        industry_work_layer='general' if name in general else 'industry')
            from core.industry_base import WORK_PRODUCTS, STAGES
            product=WORK_PRODUCTS[profile_sector][STAGES.index(stage)]
            work['industry_work_contract']=dict(deliverable=product,
                input_task_ids=[p['id'] for p in preds], release_gate_uid=gate.get('activity_uid'),
                applicability='行业基础核对清单；以项目实际岗位、用途及设备为准，不适用项记录原因，不自动增加设施',
                acceptance=gate['acceptance'])
            work['input_notes']='成果：'+product+'\n完成依据：'+gate['acceptance']+'\n适用范围：按实际用途核对；未确认的活动及设备不得当作已选专项。'

            wire(gate,work,'行业审查有实际执行工作和成果，不以空门槛代替')
            if stage=='design':wire(drawing,work,'岗位与使用要求纳入施工图')
            if stage=='inspection':wire(wallcheck,work,'封闭前核对点位及现场范围变更')
        if stage=='design':place(gate,finaldesign)
        elif stage=='inspection':place(gate,wallcheck,False,4)
        else:place(gate,handover)

    # Native shared utilities have explicit readiness checks independent of the
    # overall T&C finish, avoiding the usual specialist-test/T&C dependency loop.
    energize=new('power_ready','配电回路绝缘、接地连续性检查与送电',
                  [power,containment],2,'TESTING_COMMISSIONING','电气承包商、调试负责人',
                  '回路标识、绝缘及接地测试合格；送电操作批准并记录',tc,level=4)
    hvac=new('hvac_ready','空调试运转、风水量平衡及运行参数记录',
             [fcu,diffuser,energize],3,'TESTING_COMMISSIONING','暖通承包商、调试负责人',
             '设备启动及风水量平衡记录完成，可提供设计运行工况',tc,level=4)
    wire(tc,energize,'系统联调前具备安全电源');wire(tc,hvac,'联调使用已调试的暖通工况')
    if 'A03' in selected|external:
        p=life('A03','release') if 'A03' in selected else next(t for t in tasks if t.get('module_id')=='external_A03')
        wire(energize,p,'上级供电能力和送电责任落实后才操作下游回路')

    if 'A04' in selected:
        d=lambda n:step('datacenter',n)
        d(0)['name']='机房配套工程与调试'
        d(1)['name']='机房基础、围护、防水及防尘施工'
        d(2)['name']='机房冷却设备、管路及凝结水排水安装'
        d(3)['name']='机房消防设备、报警接口及穿墙防火封堵'
        d(4)['name']='机房配电、UPS及等电位连接安装'
        d(5)['name']='机柜固定、接地与气流组织设施安装'
        d(6)['name']='机柜配线、主干上联及链路标识测试'
        d(7)['name']='机房带载、供电切换及冷却报警联调'
        d(8)['name']='机房调试通过、具备IT设备部署条件'
        # Cooling, fire equipment, power and cabinets are separate installation
        # branches. They join for testing; a cooling finish is not a prerequisite
        # for installing fire devices, nor UPS installation for fixing cabinets.
        d(3)['predecessors']=str(d(1)['id'])+'FS'
        d(5)['predecessors']=str(d(1)['id'])+'FS'
        sub=new('dc.submittal','机房设备技术资料报审与机电接口复核',[finaldesign],3,'DESIGN',
                '机房承包商、机电设计、业主IT','设备容量、尺寸、维护距离及电源冷源参数获批',drawing,False)
        for t in tasks:
            if t.get('activity_uid') in {'pmp.procurement.gaming.0','pmp.procurement.finance.0'}:
                wire(t,sub,'订货前批准设备技术资料')
                if not facts.get('server_room_early_order_basis'):
                    contract = next(n for n in tasks if n.get('activity_uid') == 'pmp.gc.contract')
                    wire(t,contract,'采购授权未单列，暂按总包合同签署后下单')
                    t['input_notes'] = '机房设备默认由总包下单；业主提前订货须提供授权依据。'
        pre=new('dc.prestart','机房单机启动、保护整定及泄漏报警测试',[d(2),d(4),energize],3,'TESTING_COMMISSIONING',
                '机房承包商、设备厂家','UPS启动、电池检查、冷却机组启动和漏水报警记录完整',d(7),level=4)
        firetest=new('dc.fire_interface','机房消防报警、设备停机及门禁接口测试',[d(3),fire,energize],2,'TESTING_COMMISSIONING',
                     '消防承包商、机房承包商','按照批准因果表测试报警传递和相关设备动作',d(7),level=4)
        clean=new('dc.clean','机房设备清洁、环境检查与测试负载准备',[d(1),d(5),d(6)],2,'TESTING_COMMISSIONING',
                  '机房承包商、业主IT','工作区无扬尘作业，通道、防水与测试方案满足上电条件',d(7),level=4)
        for p in (pre,firetest,clean):wire(d(7),p,'带载和接口联调以前置检查、运行工况及安全条件为依据')
        wire(d(4),containment,'配电设备接入原生桥架和电缆路径');wire(d(6),cables,'机房上联接入已测试的办公网络干线')
        for n in (2,3,4,5,6):
            wire(physical,d(n),'范围内机房固定安装完成后方可记录物理竣工')
        place(life('A04','release'),d(8),False,4)

    if 'A05' in selected:
        it=lambda n:step('it_cutover',n)
        it(0)['name']='IT迁移、业务验证与支持交接'
        it(1)['name']='编制应用依赖、迁移批次与回退实施方案'
        it(2)['name']='部署目标设备、网络配置并验证链路连通'
        it(3)['name']='执行批准批次的数据迁移与业务切换'
        it(4)['name']='各应用负责人确认业务验证结果'
        scope=life('A05','retirement_scope');scope['name']='登记旧环境保留期限和退出审批责任'
        wire(it(1),scope,'迁移方案需明确旧环境保留和回退责任')
        circuits=new('it.circuits','开通运营商线路并测试办公区至机房的网络路径',
                     [life('A05','requirements'),cables],3,'TESTING_COMMISSIONING','业主IT、运营商、网络供应商',
                     '线路交付、路由、地址及访问测试记录完成',it(2))
        backup=new('it.backup','核验备份可恢复性并完成迁移数据校验方案',[it(1)],2,'TESTING_COMMISSIONING','业主IT、应用负责人',
                   '恢复样本、校验方法和回退触发条件获业务负责人确认',it(2))
        wire(it(2),circuits,'设备部署需要已交付网络路径');wire(life('A05','rehearsal'),backup,'回退演练必须有恢复验证')
        life('A05','rehearsal')['name']='演练数据恢复、业务切换和回退步骤'
        life('A05','go_no_go')['name']='批准停机窗口、业务通知与切换执行'
        uat=new('it.uat','在实际办公终端执行应用、权限与打印通信验证',[it(3),office_ready],2,
                'TESTING_COMMISSIONING','业务负责人、业主IT','逐应用、逐岗位记录访问、数据一致性及业务操作结果',it(4))
        wire(it(4),uat,'业务就绪由用户验证支撑');life('A05','observe')['name']='运行观察、处理业务故障并交接服务支持'
        wire(industry['acceptance'],life('A05','release'),'行业办公场景验收采用迁移后的实际业务环境')
        for key in ('retirement_scope','rehearsal','go_no_go','observe','release'):
            anchor={'retirement_scope':it(1),'rehearsal':it(3),'go_no_go':it(3),'observe':it(4),'release':it(4)}[key]
            place(life('A05',key),anchor,key not in {'observe','release'})

    if 'A10' in selected or 'A11' in selected:
        sec=lambda n:step('vault_security',n)
        sec(0)['name']='门禁、视频监控与入侵报警工程'
        # One owner per physical endpoint: the ordinary security work package is
        # absorbed into this integrated package rather than installed a second time.
        base_security=activity('security')
        for pred,rel,lag in parse_predecessor(base_security.get('predecessors','')):add_pred(sec(2),pred)
        tasks=remove_nodes(tasks,{base_security['id']})
        step('office_ff_move',2)['name']='会议AV设备安装与音视频联调'
        scope_i='A10' if 'A10' in selected else 'A11'
        rough=new('security.rough','门禁、监控及报警管线敷设与隐蔽检查',
                  [containment,activity('partition_framing'),life(scope_i,'design')],4,'SITE_INSTALLATION',
                  '弱电承包商、安保代表','线缆编号和测试记录齐全；穿墙、门框预留完成后允许封闭',ceilingcheck,level=4)
        wire(ceilingcheck,rough,'安防隐蔽管线须在吊顶封闭前验收')
        sec(2)['name']='安装门禁锁具、监控摄像机及报警探测器'
        for p in (rough,doors,power,cables,painting):wire(sec(2),p,'末端设备安装依赖门体、饰面、电源和通信条件')
        point=new('security.point','逐点测试门锁、门磁、录像及报警信号',[sec(2),energize],2,
                  'TESTING_COMMISSIONING','弱电承包商、安保代表','设备点表逐项签认，故障点整改并复测',sec(3),level=4)
        rights=new('security.rights','配置人员权限、访客规则和报警处置流程',[point],2,
                   'TESTING_COMMISSIONING','业主安保、IT、弱电承包商','角色权限与报警流程经业主确认',sec(3),level=4)
        sec(3)['name']='测试门禁消防释放、断电状态及入侵报警联动'
        for p in (rights,fire):wire(sec(3),p,'联动必须接入已安装的消防报警及已测试门禁设备')
        sec(4)['name']='安防缺陷复测及管理权限移交'
        wire(tc,sec(3),'全系统调试必须包含消防与安防联动')
        for i in selected&{'A10','A11'}:place(life(i,'release'),sec(4),False,4)
        plan['scope_allocation']['entries']=[e for e in plan['scope_allocation']['entries'] if e['key'] not in {'CORE_OFFICE_ENDPOINTS','ENHANCED_SECURITY'}]
        plan['scope_allocation']['entries'].append(dict(key='OFFICE_AV_ENDPOINTS',owner_module='office_ff_move',
            scope='公共会议AV设备与音视频联调',excludes='门禁设备由统一安防包负责',tasks=[step('office_ff_move',2)['id']]))
        plan['scope_allocation']['entries'].append(dict(key='SECURITY_SINGLE_PACKAGE',owner_module='vault_security',
            scope='本项目普通及强化安防设备统一安装和测试；门表区分权限与设备级别',
            excludes='会议AV另包；同一门锁、摄像机或探测器不得重复计列',tasks=[sec(2)['id']]))

    if 'A07' in selected:
        a=lambda n:step('mocap',n)
        a(1)['name']='声学房间地面基层及隔振构造施工'
        a(2)['name']='声学隔墙骨架、吸声填充及围护施工'
        a(5)['name']='安装声学门、密封五金及室内吸声饰面'
        a(6)['name']='测量隔声、混响时间及空调运行背景噪声'
        wire(a(2),activity('partition_framing'),'声学围护与原生隔墙定位及骨架协调')
        seal=new('acoustic.concealed','检查声学隔墙、风管消声段及机电穿透密封',
                 [a(2),activity('hvac_duct'),containment],2,'CONSULTANT_REVIEW',
                 '声学顾问、装修及机电承包商','封板前照片、密封和传声路径检查表签认',ceilingcheck,level=4)
        # Room-local closure: the acoustic check releases this room's own doors and finishes,
        # never the whole floor's ceiling or boarding.
        wire(a(5),seal,'本房间声学围护及穿透检查后再封闭并安装声学门与吸声饰面')
        wire(a(5),painting,'吸声饰面及门体收口安排在湿作业和饰面基层完成后')
        env=life('A07','test_environment');env['name']='确认测试房间门窗关闭、空调工况及噪声背景'
        env['predecessors']=','.join(str(p['id'])+'FS' for p in (a(5),hvac))
        env['interface_status']='planned_not_verified';env['acceptance']='暖通平衡完成；风机按使用工况运行，房间收口完成并记录测试状态'
        place(env,a(6),True,4)
        for p in (a(5),env):wire(a(6),p,'声学测试使用实际已完成房间和暖通运行条件')
        retest=new('acoustic.retest','整改声学漏声与设备噪声问题并复测',[a(6)],2,
                   'TESTING_COMMISSIONING','声学顾问、装修及机电承包商','实测结果与批准指标逐项对比，超差项关闭',a(7),level=4)
        wire(a(7),retest,'专业空间交付须完成超差处理');wire(life('A07','release'),retest,'验收资料采用整改后的测量结果')
        place(life('A07','release'),a(7),False,4)

    # Remaining specialist packages consume real common-utility conditions at
    # testing/connection stages, not at the initial concept-design milestone.
    test_hooks={
      'A06':('mocap',4,(cables,energize)),
      'A08':('dev_zone',0,(containment,)),
      'A09':('trading_desk',4,(cables,energize)),
      'A12':('lab_hvac',3,(energize,)),
      'A13':('cleanroom_iso',3,(energize,)),
      'A14':('cda_piping',1,(containment,)),
      'A15':('cda_piping',2,(containment,)),
      'A17':('anti_vibration',3,(energize,)),
      'A18':('esd_grounding',3,(energize,)),
    }
    for i,(pack,n,preds) in test_hooks.items():
        if i in selected:
            for p in preds:wire(step(pack,n),p,'专项接入和测试须使用已完成的公共工程接口')
    for i in selected&{'A08','A14','A15'}:wire(life(i,'performance'),energize,'带载或联锁测试以前置送电检查为条件')
    if 'A16' in selected:wire(life('A16','cleaning'),energize,'水处理循环调试以前置电源和设备启动条件为依据')
    if 'A20' in selected:wire(life('A20','iq'),physical,'安装确认需建筑及安装完成证据，不把设计批准当成建成')
    if 'A22' in selected:wire(life('A22','hookup'),energize,'设备电源接驳以前置回路测试和送电为条件')
    if 'A09' in selected:
        wire(step('trading_desk',3),step('office_ff_move',1),'交易终端部署需工位家具及桌面安装条件')
    if 'A12' in selected:
        wire(step('lab_hvac',1),activity('hvac_duct'),'专项通风接入已完成的公共送排风路径')
    for i in selected&{'A14','A15'}:
        if i=='A14':wire(life(i,'performance'),fire,'气体报警切断测试接入项目消防报警接口')
    if 'A22' in selected:
        floor=new('equipment.workface','检查设备搬运路线、安装地面及吊装作业条件',
                  [activity('raised_floor'),doors,life('A22','logistics')],2,'CONSULTANT_REVIEW',
                  '设备供应商、GC、结构顾问','现场路线尺寸与荷载条件符合已批准搬运方案，地面保护完成',life('A22','placement'),level=4)
        wire(life('A22','placement'),floor,'设备进场使用实际完成的运输与安装条件')
    for i in selected&{'A02','A16','A19','A22'}:
        key={'A02':'inspection','A16':'works','A19':'controls','A22':'placement'}[i]
        wire(physical,life(i,key),'本次委托的实体专项工程属于物理完工范围')

    # Procurement has its own readable WBS phase. Delivery acceptance is a real
    # predecessor of installation rather than buried inside an order allowance.
    buys=[t for t in tasks if t.get('module_id')=='module_procurement']
    if buys:
        construction=next(t for t in tasks if t.get('outline_level')==2 and '实体施工' in t['name'])
        heading=new('procurement.heading','专项设备采购与到货',[ ],0,'PROJECT_MANAGEMENT',
                    '采购负责人','设备报审、供货与到货验收按包独立跟踪',construction,level=2)
        heading['milestone']=False
        if 'A04' in selected:place(sub,heading,False)
        for buy in buys:
            label=buy['name'].split('：')[-1]
            buy['name']=label+'订货、制造与运输'
            original_consumers=[t for t in tasks if buy['id'] in {p for p,_,_ in parse_predecessor(str(t.get('predecessors','') or ''))}]
            receipt=new('receipt.'+str(buy['activity_uid']),label+'到货开箱与技术文件验收',[buy],1,
                        'CONSULTANT_REVIEW','采购、专业承包商及监理','核对型号、数量、损伤、出厂资料和附件；不合格项关闭',heading,False)
            place(buy,heading,False)
            # Keep order then receipt in the physical WBS as well as in the graph.
            after_map_order=placements.pop(receipt['id']);placements[receipt['id']]=after_map_order
            for consumer in original_consumers:wire(consumer,receipt,'安装以前置设备到货验收合格为条件')

    # No isolated early/DC milestone may masquerade as a whole-project release.
    # The original programme's physical completion and safety gates are retained.
    for i in sorted(selected-{'A01'}):
        gate=life(i,'release')
        if i not in {'A04','A05','A07','A10','A11'}:place(gate,handover)
    for t in tasks:
        i=t.get('module_id');key=t.get('lifecycle_step')
        if i in CATALOG and t['id'] not in placements:
            if key in {'requirements','assessment','capacity','applicability','strategy','organization'}:place(t,concept)
            elif key in {'design','dq','logistics','plan'}:place(t,drawing)
            elif key in {'supply','consent','early_release','source','utility_scope'}:place(t,site,level=4)
            elif i=='A21':place(t,handover)
            else:place(t,physical,level=4)
    # Move legacy requirement gates out of an end-of-file industry appendix.
    summaries={t['id'] for t in tasks if t.get('module_id','').endswith('_summary') or '基础流程（无Addon也保留）' in t['name']}
    base=[t for t in tasks if t['id'] not in summaries]
    all_tasks=base+scheduled
    byid={t['id']:t for t in all_tasks}
    before_map={};after_map={}
    for tid,(anchor,before,level) in placements.items():
        if tid not in byid:continue
        byid[tid]['outline_level']=level
        (before_map if before else after_map).setdefault(anchor,[]).append(tid)
    ordered=[];emitted=set();visiting=set()
    def emit(tid):
        if tid in emitted:return
        if tid in visiting:raise ValueError('MODULE_DISPLAY_CYCLE')
        visiting.add(tid)
        for child in before_map.get(tid,[]):emit(child)
        ordered.append(byid[tid]);emitted.add(tid)
        for child in after_map.get(tid,[]):emit(child)
        visiting.remove(tid)
    for t in base:
        if t['id'] not in placements:emit(t['id'])
    for t in all_tasks:emit(t['id'])
    for t in ordered:
        original=t['name']
        original=re.sub(r'^A(?:0[1-9]|1[0-9]|2[0-2])\s+','',original)
        t['name']=re.sub(r'\bA(?:0[1-9]|1[0-9]|2[0-2])\b',lambda m:CATALOG[m.group()]['label'],original)
        t['name']=t['name'].replace('生命周期与放行','工程实施与验收').replace('Go/No-Go','切换执行')
        if t.get('module_id') in CATALOG and ':' not in t['name']:
            t['phase']='专项工程'
        if t.get('acceptance') or t.get('scope_boundary'):
            if t.get('owner'):
                t.setdefault('responsible_unit',t['owner'])
                t.setdefault('responsible_person','具名负责人待项目指定')
            t['input_notes']='\n'.join(x for x in [t.get('input_notes',''),
              '执行责任：'+str(t.get('owner','待项目确认')),
              '完成依据：'+str(t.get('acceptance','检查记录与批准图纸')),
              '范围边界：'+str(t.get('scope_boundary','按批准房间、资产与系统接口清单执行')),
              '工期依据：'+str(t.get('duration_basis','沿用现有规划参数；本轮未校准')),
              '状态：计划工作，完成成果须在实际执行时核验。'] if x)
    dependency_order(ordered)
    # Use stable identifiers and names; subsequent ID renumbering must not stale
    # this public trace. The runtime graph remains the authoritative dependency.
    plan['process_interfaces']=[dict(predecessor_uid=byid[e['predecessor']].get('activity_uid'),
        predecessor_name=byid[e['predecessor']]['name'],successor_uid=byid[e['successor']].get('activity_uid'),
        successor_name=byid[e['successor']]['name'],reason=e['reason']) for e in edges]
    plan['integration_revision']='rc4_trade_interfaces'
    plan['industry_base']['work_uids']=[t['activity_uid'] for t in scheduled if t.get('industry_work_stage')]
    return ordered
