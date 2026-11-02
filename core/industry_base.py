"""Industry-specific owner requirements, reviews and releases, even for offices.

These are PM workflow requirements, not universal regulatory or physical scope
mandates. Stage work is included in existing brief/design/inspection/acceptance
activities; zero-day nodes expose evidence gates without inventing durations.
"""
from core.module_graph import add_pred
from core._common import parse_predecessor

PROFILES={
 'office':dict(label='通用办公',owner='业主行政／IT／设施及用户代表',
  requirements=('办公人数与使用模式确认','岗位、访客、会议协作、物业供给和搬迁限制形成需求表'),
  design=('办公功能与设施可用性设计审查','核对空间、容量、无障碍及会议IT接口；采用项目适用要求'),
  inspection=('办公空间及机电接口施工核对','按已批准点位、机电与空间接口检查施工记录'),
  acceptance=('办公典型使用场景验证','工位连接、会议协作、门禁访客和设施操作按批准需求验收'),
  release=('行政与设施接管、办公启用确认','需求偏差关闭，用户与设施方确认办公可用和维护责任')),
 'gaming':dict(label='游戏办公',owner='业主研发／制作业务代表、IT、行政及设施方',
  requirements=('研发协作、测试岗位与内容保密需求确认','区分普通办公、软件研发测试、内容制作用途；记录设备负荷、协作及集中/延时使用需求，未提供的保持待确认'),
  design=('研发工位、协作空间及IT设施接口审查','用岗位设备表核对电源、数据、散热、协作与安静工作分区；保密边界和运行时段由用户确认，不预设高密度或专用机房'),
  inspection=('研发岗位点位与设备服务接口施工核对','核对普通研发工位电源/数据标识、空间与机电接口；专项测试区未选时不扩大施工范围'),
  acceptance=('研发与协作典型场景验证','由业务/IT验证获批工作场景的接入、会议/协作、访问权限和设施条件；不把测试变成未委托的软件部署'),
  release=('研发业务启用与支持响应确认','批准范围内岗位可用，保密权限及故障响应责任明确；业务迁移仅在另选A05时执行')),
 'finance':dict(label='金融办公',owner='业主业务代表、IT／信息安全、安保、行政及设施方',
  requirements=('办公业务关键性、保密与访问需求确认','区分后台办公与交易/现金保管等特殊用途；确认访问权限、资料存放、会议保密及业务中断容忍要求'),
  design=('办公保密分区与业务连续性接口审查','按批准风险需求检查访客/员工边界、机电IT依赖及恢复责任；不预设双电双网、金库或金融监管等级'),
  inspection=('办公访问边界与信息设施施工核对','按获批图纸检查点位、线缆标识、门禁/疏散接口与资料存放空间；不自动提高设备安防等级'),
  acceptance=('角色访问、办公通信与恢复责任验证','由业务/IT/安保核对访问权限、会议通信和现有恢复安排；只有明确冗余配置才测试故障切换'),
  release=('金融办公业务启用与风险遗留项签认','办公用途与批准范围一致，风险遗留项具名处置；交易上线/金库运营另按所选专项交付')),
 'biopharma':dict(label='生物医药企业办公',owner='业主用户、EHS、行政／设施；质量方按适用性参加',
  requirements=('办公与实验/样品活动边界确认','确认是否仅办公，是否存在实验操作、样品存放、危险物料或受监管用途；未提及不当作已排除'),
  design=('办公用途边界与EHS适用性设计审查','确认设计未把办公场地用于未经评估的实验/样品活动；有相关活动则明确专项范围，纯办公室不自动加载GMP、洁净或实验通风'),
  inspection=('办公空间与已声明用途一致性核对','核对空间标识、机电接口和批准用途，记录现场变更是否新增实验或物料需求；普通办公按办公验收'),
  acceptance=('办公功能与EHS范围条件核验','验证办公设施和用户协作场景；核实涉及样品/物料的用途声明及条件，未选实验模块不作实验室性能验收'),
  release=('办公使用边界与运行责任移交','用户及适用EHS责任方确认获准用途、禁止/限制活动及变更流程；不把办公接管称为GMP或生物安全认证')),
 'semiconductor':dict(label='半导体企业办公',owner='业主研发／设备代表、IT、设施及适用EHS方',
  requirements=('设计办公与设备实验用途确认','区分芯片设计办公、验证测试及设备实验活动；收集工作站负荷、数据保密和设备要求，介质/洁净/ESD不由行业推定'),
  design=('研发工作站容量与设备边界设计审查','按实际工作站/设备表确认电力、散热、数据、保密和空间接口；无特殊设备时按普通研发办公设计'),
  inspection=('研发点位、设备预留与范围标识核对','核对批准普通研发工位接口；专项未选时不增加特气、CDA、ESD地坪或微振设施'),
  acceptance=('设计研发办公场景与设施接口验证','用户/IT验证获批设计工作场景的接入和支持条件；设备、介质及ESD验收仅在对应范围确认后开展'),
  release=('研发办公启用与设备活动边界移交','确认办公研发可用、支持责任及后续设备引入的变更流程；不将办公室接管视为晶圆制造或设备工艺验收')),
}
STAGES=('requirements','design','inspection','acceptance','release')

def profile_summary(sector, profile=None):
 p=PROFILES.get('office' if profile=='general_office' else sector)
 if not p:return None
 return dict(project_industry=sector,selected_profile=profile or 'sector_review',label=p['label'],owner=p['owner'],mandatory_in_workflow=True,regulatory_mandate=False,
             physical_addons_automatic=False,stages=[dict(stage=s,name=p[s][0],acceptance=p[s][1]) for s in STAGES])

def attach_industry_base(tasks,facts,plan):
 sector=facts['sector'];profile_sector='office' if facts.get('industry_work_profile')=='general_office' else sector;p=PROFILES[profile_sector];seq=max(t['id'] for t in tasks)+1
 requirement=next(t for t in tasks if t.get('module_id')=='industry_'+sector)
 def find(text):
  t=next((t for t in tasks if text in t['name']),None)
  if t is None:raise ValueError('INDUSTRY_INTERFACE_MISSING: '+text)
  return t
 def step(module,n):return next(t for t in tasks if t.get('module_id')==module and t.get('module_step')==n)
 drawing=find('全专业施工图');design_release=find('施工图全套确认')
 inspection=find('隐蔽工程验收');office_ready=step('office_ff_move',3);handover=step('core_handover',3)
 def decorate(t,s):
  t.update(name=p['label']+'：'+p[s][0],industry_stage=s,industry=sector,industry_profile=profile_sector,owner=p['owner'],
           acceptance=p[s][1],output_code=sector.upper()+'_'+s.upper()+'_REVIEWED',
           evidence_required=True,evidence_status='pending_project_confirmation',
           mandatory_in_workflow=True,regulatory_mandate=False,physical_scope_added=False,
           scope_boundary='仅核查批准用途；不因行业推定专项工程，适用性可经责任方确认不适用',
           duration_basis='核查工作纳入既有需求/设计/检验/验收活动；本节点只标示放行，不表示核查零工时',
           activity_class='CLIENT_APPROVAL',duration_authority='evidence_release_gate')
  return t
 decorate(requirement,'requirements')
 requirement['space_use']=facts.get('space_use','unknown')
 def gate(s,preds):
  nonlocal seq
  t=dict(id=seq,outline_level=3,duration_days=0,duration=0,milestone=True,calendar='5D',
         predecessors=','.join(str(n['id']) for n in preds),module_id='industry_base_'+sector,
         activity_uid='pmp.industry.'+sector+'.'+s,phase=p['label']+'基础工序与证据放行')
  seq+=1;return decorate(t,s)
 design=gate('design',[drawing,requirement]);add_pred(design_release,design)
 check=gate('inspection',[inspection,design])
 # Require the industry checklist before works released by the first concealment inspection.
 import re
 for t in tasks:
  if t is inspection:continue
  t['predecessors']=','.join(re.sub(r'^'+str(inspection['id'])+r'(?=\D|$)',str(check['id']),token) for token in str(t.get('predecessors','') or '').split(','))
 acceptance=gate('acceptance',[office_ready,check])
 release=gate('release',[acceptance,step('core_handover',2)])
 for card in plan.get('module_cards',{}).values():
  out=next((t for t in tasks if t.get('activity_uid')==card['release_uid']),None)
  if out:add_pred(release,out)
 add_pred(handover,release)
 plan['industry_base']=profile_summary(sector,facts.get('industry_work_profile'))
 plan['industry_base'].update(space_use=facts.get('space_use','unknown'),
                              gate_uids=[requirement['activity_uid']]+[n['activity_uid'] for n in (design,check,acceptance,release)],
                              evidence_status='pending_project_confirmation')
 summary=dict(id=seq,name=p['label']+'基础流程（无Addon也保留）',outline_level=2,duration_days=0,duration=0,predecessors='')
 return tasks+[summary,design,check,acceptance,release]

# Names describe actual work already defined in INDUSTRY_WORK, not extra scope.
WORK_PACKAGE_NAMES = {
 'office':dict(zip(STAGES,('人数席位与办公配套核对','工位会议室点位与访客流线会审','办公点位与标识核查','工位会议与访客场景验证','使用指引与报修流程移交'))),
 'gaming':dict(zip(STAGES,('开发测试岗位与协作需求核对','研发工位供电散热与协作分区会审','研发工位回路与数据端口核查','研发接入与评审协作场景验证','研发设施支持与权限流程移交'))),
 'finance':dict(zip(STAGES,('业务岗位保密与中断要求核对','访客保密分区与业务通信会审','受限门点与资料区接口核查','角色访问与办公通信验证','权限维护与恢复安排移交'))),
 'biopharma':dict(zip(STAGES,('房间用途与样品实验边界核对','房间用途与办公设备接口会审','房间用途点位与变更核查','办公功能与入驻物品核验','用途限制与变更流程移交'))),
 'semiconductor':dict(zip(STAGES,('研发工作站与设备测试边界核对','工作站负荷散热与设备预留会审','工作站回路与设备预留核查','工作站接入与代表负载验证','研发接口与设备变更流程移交'))),
}

WORK_PRODUCTS = {
 'office':('人数席位及设施需求表','办公点位与流线审查记录','点位及标识检查记录','典型办公场景测试记录','使用指引及报修联系表'),
 'gaming':('研发岗位设备及使用时段需求表','岗位供电散热及协作分区审查记录','研发岗位回路与端口核对记录','接入、评审及延时使用测试记录','设施支持与权限申请流程'),
 'finance':('业务岗位及保密中断要求表','访问分区与业务通信接口审查记录','门表、资料区及通信点位核对记录','角色访问及通信测试记录','权限维护、恢复安排及遗留风险表'),
 'biopharma':('房间用途及样品物料活动清单','房间用途与办公设备接口审查记录','用途点位及范围变更检查记录','办公使用与入驻物品核验记录','允许用途、限制活动及变更流程'),
 'semiconductor':('研发工作站及设备活动边界表','负荷散热、数据及设备预留审查记录','研发点位与设备预留核对记录','接入及代表负载使用验证记录','设备接口清单及变更流程'),
}
