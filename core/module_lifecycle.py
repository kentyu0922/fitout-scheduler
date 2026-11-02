"""Planning-level lifecycle interfaces, not approved engineering methods.

One facility/workfront model. Interface assumptions remain visible. All newly
estimated activity durations are uncalibrated; zero-day nodes are release gates.
"""
import json
from pathlib import Path
from core.module_graph import add_pred
from core.module_catalog import CATALOG

CARDS=json.loads((Path(__file__).resolve().parents[1]/'references/modular_design/module_cards.json').read_text(encoding='utf-8'))
LEGACY={
 'A01':('tdd',1,5),'A04':('datacenter',1,8),'A05':('it_cutover',1,4),
 'A06':('mocap',3,7),'A07':('mocap',1,7),'A08':('dev_zone',0,0),
 'A09':('trading_desk',1,5),'A10':('vault_security',2,4),'A11':('vault_security',1,4),
 'A12':('lab_hvac',1,4),'A13':('cleanroom_iso',1,4),'A14':('cda_piping',1,4),
 'A15':('cda_piping',2,4),'A17':('anti_vibration',1,4),'A18':('esd_grounding',1,4)}

def attach_lifecycle(tasks,facts,plan):
    selected=set(plan['selected']);seq=max(t['id'] for t in tasks)+1
    additions=[];groups={};releases={};phases={};requirements={};designs={}
    def find(text):
        t=next((t for t in tasks if text in t['name']),None)
        if t is None:raise ValueError('LIFECYCLE_INTERFACE_MISSING: '+text)
        return t
    def step(pack,n):
        t=next((t for t in tasks if t.get('module_id')==pack and t.get('module_step')==n),None)
        if t is None:raise ValueError(f'LIFECYCLE_STEP_MISSING: {pack}.{n}')
        return t
    industry=next(t for t in tasks if t.get('module_id')=='industry_'+facts['sector'])
    site=find('测量放线与标高复核')
    designstart=find('Test Fit')
    finaldesign=find('施工图全套确认')
    contract=next(t for t in tasks if '施工总包合同签订完成' in t['name'] or 'D&B 总承包合同签订完成' in t['name'])
    physical=next(t for t in tasks if '物理竣工' in t['name'] or '硬装施工完成' in t['name'])
    handover=step('core_handover',3)
    # Delivery inspection is not factory fabrication merely because it is custom furniture.
    step('office_ff_move',0)['activity_class']='TESTING_COMMISSIONING'
    for i in plan['external']:
        releases[i]=next(t for t in tasks if t.get('module_id')=='external_'+i)
    available=selected|set(plan['external'])
    # Technical solutions belong to approved project design, not the industry tag.
    neutral={
      ('datacenter',5):'机房机柜及适用气流组织设施安装（按批准设计）',
      ('datacenter',6):'机房网络主干与综合布线安装测试（规格按批准设计）',
      ('mocap',1):'声学地面及适用隔振构造施工（按批准设计）',
      ('mocap',2):'声学围护及穿透部位处理（按批准设计）',
      ('mocap',3):'动捕安装支架与空间定位校准（按设备及结构要求）',
      ('mocap',5):'声学门窗与机电消声设施安装（按批准设计）',
      ('trading_desk',1):'交易岗位供配电设施安装（冗余策略按批准需求）',
      ('trading_desk',2):'交易岗位网络布线（路径及冗余按批准设计）',
      ('trading_desk',4):'交易系统功能、恢复与适用切换场景验证',
      ('trading_desk',5):'交易业务系统就绪确认',
      ('vault_security',1):'特殊保管空间围护与门体安装（按专业设计）',
      ('vault_security',2):'批准范围内门禁、监控及报警系统安装',
      ('lab_hvac',1):'实验室送排风与补风设施安装（按风险评估及设计）',
      ('lab_hvac',2):'适用排风末端与处理设施安装（按批准范围）',
      ('lab_hvac',3):'通风平衡、控制及安全联锁功能验证',
      ('cleanroom_iso',1):'受控环境围护与空气末端设施安装（按批准设计）',
      ('cleanroom_iso',2):'受控环境地面、接缝与可清洁表面完成',
      ('anti_vibration',1):'按治理设计实施微振控制基础与隔振设施',
      ('esd_grounding',2):'批准范围内ESD地面、工位及连接设施安装',
      ('cda_piping',1):'工艺气体管路及供给设施安装与适用检查',
      ('cda_piping',2):'CDA供给设施及管网安装与适用检查',
      ('it_cutover',2):'目标IT环境预部署、设备上架与链路连通检查',
      ('it_cutover',3):'按批准窗口实施业务切换及即时验证',
    }
    for t in tasks:
        key=(t.get('module_id'),t.get('module_step'))
        if key in neutral:t['name']=neutral[key]
    def node(i,key,label,preds=(),days=0,external=False):
        nonlocal seq
        card=CARDS[i]
        n=dict(id=seq,name=i+' '+CATALOG[i]['label']+'：'+label,outline_level=3,
               duration_days=days,duration=days,milestone=days==0,calendar='5D',
               predecessors=','.join(str(t['id']) for t in preds),module_id=i,
               activity_uid='pmp.lifecycle.'+i+'.'+key,lifecycle_step=key,
               phase=i+' 专项需求、实施与交付接口',owner=card['主责与确认'],
               acceptance=card['完成成果'],scope_boundary=card['排除与边界'],
               interface_status='unverified_external' if external else 'planned_not_verified',
               duration_basis='未校准规划占位；非客户确认工期、供应商承诺或法定办理期限' if days else '成果/条件放行里程碑；不表示现场已完成')
        classes={'design':'DESIGN','requirements':'CLIENT_APPROVAL','assessment':'CONSULTANT_REVIEW',
                 'supply':'TENDER_COMMERCIAL','works':'SITE_CONSTRUCTION','controls':'SITE_INSTALLATION',
                 'placement':'SITE_INSTALLATION','hookup':'SITE_INSTALLATION','performance':'TESTING_COMMISSIONING',
                 'inspection':'TESTING_COMMISSIONING','cleaning':'TESTING_COMMISSIONING','sampling':'TESTING_COMMISSIONING',
                 'monitor':'TESTING_COMMISSIONING','rehearsal':'TESTING_COMMISSIONING','iq':'TESTING_COMMISSIONING',
                 'oq':'TESTING_COMMISSIONING','pq':'TESTING_COMMISSIONING','release':'CLIENT_APPROVAL'}
        n['activity_class']=classes.get(key,'PROJECT_MANAGEMENT')
        n['duration_authority']='uncalibrated_module_planning' if days else 'release_gate'
        seq+=1;groups.setdefault(i,[]).append(n);return n
    # Industry use and requirements must feed design, not merely change a title.
    for i in sorted(selected):
        if i=='A01':
            for n in tasks:
                if n.get('module_id')=='tdd':n.update(owner=CARDS[i]['主责与确认'],scope_boundary=CARDS[i]['排除与边界'])
            releases[i]=step('tdd',5);continue
        req=node(i,'requirements','需求、适用范围及验收准则确认',[industry],2)
        requirements[i]=req;add_pred(designstart,req)
        if i in {'A05','A21'}:continue
        des=node(i,'design','专业设计、责任界面与测试要求协调确认',[designstart,req],3)
        designs[i]=des;add_pred(finaldesign,des)
    # Existing detailed works receive explicit design/authority and acceptance.
    for i in sorted(selected & LEGACY.keys()-{'A01'}):
        pack,first,last=LEGACY[i];entry=step(pack,first);end=step(pack,last)
        add_pred(entry,requirements[i])
        if i!='A05':add_pred(entry,finaldesign);add_pred(entry,contract)
        if i in {'A08','A11','A17','A18'}:
            buy=node(i,'supply','本专项材料／设备采购与到货确认',[finaldesign,contract],10)
            add_pred(entry,buy)
        for n in tasks:
            if n.get('module_id')==pack:
                n.setdefault('addon_ids',[]).append(i)
                n['owner']=CARDS[i]['主责与确认'];n['scope_boundary']=CARDS[i]['排除与边界']
        if i=='A05':
            step(pack,3).update(calendar='5D',calendar_window='unconfirmed',work_weekend=False,
                                duration_basis='切换窗口未确认，暂用5D规划占位；不能据此认定只能周末或已获业务授权')
            rehearsal=node(i,'rehearsal','切换演练、恢复与回退验证',[step(pack,2)],2)
            window=node(i,'go_no_go','业务窗口与Go/No-Go授权（待确认）',[rehearsal],external=True)
            add_pred(step(pack,3),window)
            end=node(i,'observe','业务验证后观察与遗留问题关闭',[end],2)
            node(i,'retirement_scope','旧环境退出范围与另行授权要求登记',[requirements[i]])
        elif i=='A06':
            test=node(i,'performance','设备标定与代表动作场景验收',[step(pack,4)],2)
            add_pred(end,test)
            end=test
        elif i=='A07':
            hvac=node(i,'test_environment','声学测试所需HVAC及背景条件核验（待确认）',[finaldesign],external=True)
            add_pred(step(pack,6),hvac)
            if 'A06' in selected:step(pack,6)['predecessors']=str(step(pack,5)['id'])+','+str(hvac['id'])
            end=step(pack,6)
        elif i=='A08':end=node(i,'performance','供电冷却与网络检查、带载场景验证',[end],3)
        elif i in {'A14','A15'}:
            label='适用压力与泄漏试验、清洁纯度验证及安全联锁检查' if i=='A14' else '适用检验、清洁与带载露点及空气质量验证'
            end=node(i,'performance',label,[entry],4)
        elif i=='A18':end=node(i,'operation','人员／工位ESD控制制度培训与接管',[end],2)
        close=node(i,'release','测试偏差关闭、记录移交与使用放行',[end],1)
        releases[i]=close;add_pred(handover,close)
    if selected & {'A14','A15'}:
        aggregate=step('cda_piping',3)
        aggregate.update(name='所选介质系统专项试验完成汇合',duration_days=0,duration=0,milestone=True)
        aggregate['predecessors']=','.join(str(releases[i]['id']) for i in ('A14','A15') if i in selected)
    # New modules: explicit stages, with no invented technical design thresholds.
    if 'A02' in selected:
        i='A02';assessment=node(i,'assessment','现状、荷载评估及加固设计依据确认',[requirements[i]],3)
        add_pred(designs[i],assessment)
        consent=node(i,'consent','方案、必要审批与作业条件确认',[finaldesign,contract],external=True)
        supply=node(i,'supply','材料与专项施工资源落实',[consent],5)
        work=node(i,'works','按批准方案实施结构改造及过程检验',[supply,site],10)
        cure=node(i,'inspection','适用养护、检测及荷载使用条件核查',[work],5)
        releases[i]=node(i,'release','受影响区域结构使用放行',[cure])
    if 'A03' in selected:
        i='A03';capacity=node(i,'capacity','容量与产权分界核查、供电责任确认',[requirements[i]],3)
        add_pred(designs[i],capacity)
        consent=node(i,'consent','供电方案、手续及实施授权（待确认）',[designs[i]],external=True)
        supply=node(i,'supply','设备采购及交期核实',[consent,contract],20)
        work=node(i,'works','外线、配电及相关设施施工',[supply],10)
        inspect=node(i,'inspection','检验试验及送电条件检查',[work],3)
        energize=node(i,'energize','送电窗口批准与操作责任确认（待确认）',[inspect],external=True)
        releases[i]=node(i,'release','批准容量范围内电源可用确认',[energize])
    if 'A16' in selected:
        i='A16';source=node(i,'source','原水、排水／废液去向及公用条件核验（待确认）',[requirements[i]],external=True)
        buy=node(i,'supply','工艺水处理与分配设备采购到货',[finaldesign,contract],20)
        work=node(i,'works','设备及管网安装、连接和适用检验',[buy,site,source],10)
        clean=node(i,'cleaning','按批准方案清洗与循环调试',[work],5)
        sample=node(i,'sampling','在约定运行条件下取样与水质验证',[clean],5)
        releases[i]=node(i,'release','偏差关闭及工艺水使用放行',[sample],1)
        phases['water_start']=clean
    if 'A19' in selected:
        i='A19';app=node(i,'applicability','事项适用性、法定主体与前期手续清单确认',[requirements[i]],3)
        early=node(i,'early_release','适用开工／实施条件核验（待确认）',[app],external=True)
        add_pred(designs[i],app)
        controls=node(i,'controls','批准范围内环境与EHS控制设施落实',[finaldesign,contract,site,early],10)
        safe=node(i,'safe_introduction','控制设施检查与介质引入安全条件核验',[controls],2)
        monitor=node(i,'monitor','适用调试、监测及验收证据整理',[safe],5)
        releases[i]=node(i,'release','适用验收／许可／登记及运行条件核验（待确认）',[monitor],external=True)
        phases.update(ehs_early=early,ehs_safe=safe,ehs_monitor=monitor)
        # Only selected specialist work is held; no automatic whole-floor EIA.
    if 'A22' in selected:
        i='A22';buy=node(i,'supply','专业设备采购与到货检查（厂验按合同适用）',[finaldesign,contract],20)
        logistics=node(i,'logistics','运输路径、荷载、吊装及洁净搬入方案确认',[designs[i]],3)
        place=node(i,'placement','安装区域交接与设备就位调平',[buy,site,logistics],3)
        hookup=node(i,'hookup','所需公用工程接口核验及设备接入',[place],3)
        test=node(i,'performance','联锁及设备功能／性能验收',[hookup],3)
        releases[i]=node(i,'release','缺陷关闭与设备使用放行',[test],1)
        phases.update(equipment_placement=place,equipment_hookup=hookup,equipment_test=test)
    if 'A20' in selected:
        i='A20';strategy=node(i,'strategy','质量方批准验证范围、计划与证据复用策略',[requirements[i]],3)
        dq=node(i,'dq','设计确认及执行方案批准',[designs[i],strategy],2)
        add_pred(finaldesign,dq)
        iq=node(i,'iq','按批准方案安装确认及偏差记录',[dq],3)
        oq=node(i,'oq','运行确认与偏差关闭',[iq],3)
        pq=node(i,'pq','性能确认与质量评审',[oq],5)
        releases[i]=node(i,'release','质量负责人批准相关设施／系统使用',[pq])
        phases['qualification_start']=iq
    if 'A21' in selected:
        i='A21';org=node(i,'organization','运维组织、服务边界及接管标准确认',[requirements[i]],3)
        planfm=node(i,'plan','维护策略、CMMS结构及服务资源准备',[org],5)
        asset=node(i,'assets','竣工资产台账、CMMS数据与维护计划装载',[planfm,physical],3)
        training=node(i,'training','运行培训、应急演练与服务接管验证',[asset],3)
        releases[i]=node(i,'release','扩展FM服务上线与责任交接',[training])
        phases['fm_training']=training
    # Cross-system consumption is explicit. Shared facility assumption is disclosed.
    if 'A02' in available:
        for i in ('A04','A06','A07','A11','A17'):
            if i in selected:add_pred(step(LEGACY[i][0],LEGACY[i][1]),releases['A02'])
        if 'A22' in selected:add_pred(phases['equipment_placement'],releases['A02'])
    if 'A03' in available:
        for i,pack,n in [('A04','datacenter',7),('A09','trading_desk',4),('A12','lab_hvac',3)]:
            if i in selected:add_pred(step(pack,n),releases['A03'])
        if 'A15' in selected:add_pred(next(n for n in groups['A15'] if n['lifecycle_step']=='performance'),releases['A03'])
        if 'A08' in selected:add_pred(next(n for n in groups['A08'] if n['lifecycle_step']=='performance'),releases['A03'])
        if 'A16' in selected:add_pred(phases['water_start'],releases['A03'])
        if 'A22' in selected:add_pred(phases['equipment_hookup'],releases['A03'])
    if 'A19' in selected:
        for i in ('A12','A13','A14','A15','A16','A22'):
            if i not in selected:continue
            if i in LEGACY:add_pred(step(LEGACY[i][0],LEGACY[i][1]),phases['ehs_early'])
            else:
                work=next(n for n in groups[i] if n['lifecycle_step'] in ('works','placement'))
                add_pred(work,phases['ehs_early'])
        if 'A14' in selected:add_pred(next(n for n in groups['A14'] if n['lifecycle_step']=='performance'),phases['ehs_safe'])
        if 'A16' in selected:add_pred(phases['water_start'],phases['ehs_safe'])
        for i in ('A12','A14','A16'):
            if i in selected:add_pred(phases['ehs_monitor'],releases[i])
    if 'A22' in selected:
        for i in ('A12','A14','A15','A16','A18'):
            if i in available:add_pred(phases['equipment_hookup'],releases[i])
        if 'A17' in selected:
            # Foundation precedes placement; vibration performance follows it.
            add_pred(phases['equipment_placement'],step('anti_vibration',2))
            step('anti_vibration',2)['name']='隔振设施安装检查与设备就位前条件确认（设备搬入归A22）'
            add_pred(step('anti_vibration',3),phases['equipment_placement'])
            add_pred(phases['equipment_test'],releases['A17'])
        if 'A13' in selected:
            add_pred(phases['equipment_placement'],step('cleanroom_iso',2))
            add_pred(step('cleanroom_iso',3),phases['equipment_placement'])
            add_pred(phases['equipment_test'],releases['A13'])
        external=node('A22','utility_scope','设备介质清单及未新建公用系统能力核验（待确认）',[requirements['A22']],external=True)
        add_pred(phases['equipment_hookup'],external)
    if 'A20' in selected:
        related=[releases[i] for i in sorted(selected-{'A01','A05','A20','A21'})]
        if not related:related=[node('A20','external_facility','验证对象安装与调试资料核验（待确认）',[requirements['A20']],external=True)]
        for t in related:add_pred(phases['qualification_start'],t)
    if 'A21' in selected:
        for i in sorted(selected-{'A01','A05','A20','A21'}):add_pred(phases['fm_training'],releases[i])
    # IT deployment consumes the completed facility handover, not just raw test end.
    if 'A04' in available and 'A05' in selected:add_pred(step('it_cutover',2),releases['A04'])
    if 'A04' in available and 'A09' in selected:add_pred(step('trading_desk',4),releases['A04'])
    if 'A12' in available and 'A13' in selected:add_pred(step('cleanroom_iso',3),releases['A12'])
    for i in sorted(selected-{'A01'}):add_pred(handover,releases[i])
    for i,members in groups.items():
        summary=dict(id=seq,name=i+' '+CATALOG[i]['label']+' 生命周期与放行',outline_level=2,
                     duration_days=0,duration=0,predecessors='',module_id=i+'_summary')
        seq+=1;additions.extend([summary]+members)
    plan['module_cards']={i:dict(owner=CARDS[i]['主责与确认'],inputs=CARDS[i]['启动资料与前置'],output=CARDS[i]['完成成果'],
                                    boundary=CARDS[i]['排除与边界'],release_uid=releases[i].get('activity_uid')) for i in sorted(selected)}
    plan['interface_assumptions']=['单设施总控：同选专项按共享供电、工作面和设备服务接口连接；分区独立启用须另行拆分。',
                                   '新增持续时间为未校准占位；外部批准、运行许可、技术指标与供应商交期尚未获得项目确认。',
                                   'IT切换窗口未确认时暂按5D规划占位，保留业务Go/No-Go确认条件，不默认已获周末停机授权。',
                                   '质量确认暂保守等待相关设施调试证据；不代表验证范围或阶段组合已由质量负责人批准。']
    return tasks+additions
