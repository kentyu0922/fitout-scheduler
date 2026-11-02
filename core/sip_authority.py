"""Scoped SIP office-decoration adapter using sourced portal interfaces.
Not a complete Jiangsu / Suzhou-citywide legal classification engine.
"""
from core.module_graph import add_pred,remove_nodes,dependency_order

def resolve_sip(facts,profile):
 d=dict(status='NEEDS_FACTS',questions=[],sources=profile['sources'],rule_version='SIP_PORTAL_PILOT_20261016',classification_source=facts.get('authority_basis'),classification_independently_verified=False,service_conflicts=profile['time_conflicts'])
 required={'building_context':'是否为合法既有非居住建筑？','renovation_category':'是否仅内部装修改造、不改变原规划用途或结构体系？','authority_basis':'请提供设计/项目窗口核定路径依据。','fire_classification':'消防工程分类是特殊工程还是其他工程？','authority_size_class':'审图机构核定为中小型还是大型工程？不直接用装修面积猜测。','authority_local_route':'是否已核定适用园区常规专业装修直接发包核准路径？','building_safety_assessment':'结构安全鉴定要求是否已核定？'}
 for field,question in required.items():
  if facts.get(field) in (None,'','unknown'):d['questions'].append(dict(field=field,question=question))
 if d['questions']:return d
 if facts.get('authority_local_route')!='sip_standard_direct' or facts.get('building_context')!='existing_nonresidential' or facts.get('renovation_category') not in ('fire_changes',) or facts.get('procurement_regime')!='enterprise_selection' or facts.get('building_safety_assessment')!='not_required':
  d.update(status='NEEDS_LOCAL_RULE',questions=[dict(field='authority_local_route',question='超出当前园区办公室常规专业装修试点路径；用途/结构变化、依法招标或需鉴定的工程须核实独立接口，不自动套此流程。')]);return d
 if any(facts.get(f'addon_a{i:02}') in ('required','需要') for i in [2,12,13,14,15,16,17,19,20]):
  d.update(status='NEEDS_LOCAL_RULE',questions=[dict(field='addons',question='实验/工艺或结构专项超出园区常规办公室试点，须核定专项报审路径。')]);return d
 if float(facts.get('area_sqm',0))<1000 or float(facts.get('cost_10k_rmb') or 0)<100:
  d.update(status='NEEDS_LOCAL_RULE',questions=[dict(field='cost_10k_rmb',question='本试点只覆盖面积≥1000㎡且投资≥100万元的常规办公室，避免在限额政策不明时误判免办；这不是法定限额结论。')]);return d
 if facts['fire_classification'] not in ('other','special'):
  d.update(status='CONFLICT',questions=[dict(field='fire_classification',question='申报消防变动与无消防审验事项不一致，请核定。')]);return d
 if facts['authority_size_class'] not in ('medium_or_below','large'):
  d['questions']=[dict(field='authority_size_class',question='请提供审图机构核定的规模档位。')];return d
 d.update(status='READY',route='sip_decoration_'+facts['fire_classification'],drawing_review=True,permit_required=True,fire_route=facts['fire_classification'],completion_filing=True,sampling=facts.get('fire_sampling','pending'),building_assessment_required=False,rule_parameters=profile)
 return d

def apply_sip(tasks,facts,d):
 from core.local_authority import add_owner_completion
 from core.release_controls import split_permit,_node,attach_fire_inspection
 from core.authority_buffers import apply_resolved_buffers
 p=d['rule_parameters'];source=p['sources'][0];find=lambda q:next((t for t in tasks if q in t['name']),None)
 review=next(t for t in tasks if '递交第三方施工图审查' in t['name'] or '第三方图审机构审查与意见修改' in t['name'])
 reply=next((t for t in tasks if '第三方专项图审意见' in t['name']),None)
 release=find('施工图全套确认');kickoff=find('Kick Off')
 permit=[t for t in tasks if t.get('outline_level',3)>=3 and ('政府施工许可证' in t['name'] or '正式取得施工许可证' in t['name'])]
 fire=[t for t in tasks if any(s in t['name'] for s in ('第三方消防设施检测','住建主管部门消防验收','消防查验意见整改','取得《建设工程消防验收'))]
 close=[t for t in tasks if t.get('outline_level',3)>=3 and any(s in t['name'] for s in ('住建局联合竣工','竣工备案 / 联合验收通过'))]
 fd=[t for t in tasks if '消防设计审查' in t['name']]
 removed=set();physical=find('物理竣工');physical['completion_scope_caption']='物理竣工（装修机电实体完工）';owner=add_owner_completion(tasks)
 days=p['review_days'][facts['authority_size_class']]
 review.update(name='施工图送审与审查' if reply else '施工图送审、意见答复与复核',duration_days=days if reply else days+3,duration=days if reply else days+3,authority_service_limit_days=days,duration_basis=f'园区数字化审图公开承诺窗口{days}工作日；修改复核另列，合并工作包另含3工作日PM暂估',source_url=p['sources'][1])
 if reply:reply['name']='图审意见答复、修改与复核'
 reg=_node(tasks,'pmp.authority.sip.project_registration','装修项目登记与原建筑资料核对',1,[kickoff],'开工许可','PROJECT_MANAGEMENT');reg['duration_basis']='园区装修一件事要求登记项目编码；1工作日为PM整理暂估，无行政承诺时限';add_pred(review,reg)
 contract=next((t for t in tasks if t.get('module_id') in ('procurement_gc','procurement_db') and t.get('procurement_step')=='contract'),None)
 if contract is None:contract=next(t for t in tasks if any(x in t['name'] for x in ('总包','总承包')) and any(x in t['name'] for x in ('合同签','合同谈判','签约')) and t.get('outline_level',3)>=3)
 collection=_node(tasks,'pmp.authority.sip.contract_collection','施工合同归集与质安监资料准备',1,[contract],'开工许可','PROJECT_MANAGEMENT');collection['duration_basis']='合同归集为主动申报、不单独审查；1工作日为PM录入及核对量，不冒称行政时限'
 award=_node(tasks,'pmp.authority.sip.direct_award','建设工程直接发包核准',p['direct_award_days'],[contract,release],'开工许可','AUTHORITY_APPROVAL');award['duration_basis']='园区装修一件事直接发包核准承诺3工作日；项目声明符合适用条件'
 work=next(t for t in permit if t.get('duration_days',0)>0);add_pred(work,collection);add_pred(work,award)
 if d['fire_route']=='other':removed.update(t['id'] for t in fd)
 else:
  if not fd:
   fw=_node(tasks,'pmp.authority.sip.fire_design','消防设计审查',15,[release],'开工许可','AUTHORITY_APPROVAL');fr=_node(tasks,'pmp.authority.sip.fire_design_result','取得消防设计审查意见',0,[fw],'开工许可','AUTHORITY_APPROVAL');fr['milestone']=True;fd=[fw,fr]
  for t in fd:
   if t.get('duration_days',0)>0:t.update(duration_days=15,duration=15,duration_basis='园区非工业项目消防设计审查公开窗口15工作日；专家程序另核',source_url=source)
  add_pred(work,fd[-1])
 for t in fire:
  name=t['name']
  if '第三方消防' in name:t['name']='消防设施功能测试与报验资料整理';add_pred(owner,t)
  elif '住建主管部门' in name:
   t.update(name='消防验收备案申报' if d['fire_route']=='other' else '消防验收申报与现场评定',duration_days=1 if d['fire_route']=='other' else 15,duration=1 if d['fire_route']=='other' else 15,duration_basis='备案提交暂估1工作日；特殊消防验收保守按15工作日；公开页面单位差异待项目窗口确认',source_url=source);add_pred(t,owner);t.pop('statutory_city_clock',None)
  elif '消防查验意见整改' in name:
   days=int(facts.get('fire_rectification_days',0))+int(facts.get('fire_recheck_days',0))
   if not days:removed.add(t['id'])
   else:t.update(duration_days=days,duration=days,duration_basis=facts.get('fire_duration_basis','项目声明'))
  elif '取得《' in name:
   t['name']='消防备案完成' if d['fire_route']=='other' else '取得消防验收合格意见'
   if d['fire_route']=='other':t['fire_role']='filing_result'
 for t in close:
  t['name']='工程竣工验收备案' if t.get('duration_days',0)>0 else '取得竣工验收备案结果'
  if t.get('duration_days',0)>0:t.update(duration_days=2,duration=2,duration_basis='园区公开流程备案窗口2工作日，适用消防及竣工资料须完成',source_url=p['sources'][2])
  add_pred(t,owner)
 tasks=remove_nodes(tasks,removed);split_permit(tasks,permit,handling_days=p['permit_days'],final_compilation_after_ready=True)
 for t in permit:
  if t.get('permit_role')=='submit':t.update(name='施工许可及质安监合并申报',duration_basis='园区页面存在1/2工作日差异，本计划保守按2工作日，不冒称当地唯一承诺；须窗口核对',source_url=source)
  if t.get('permit_role')=='prepare':t['input_notes']='合同、直接发包核准、图审等条件齐备后整理签章；3工作日PM暂估，非行政时限。'
 for t in [review,*([reply] if reply else []),*fd,*permit,*fire,*close,reg,collection,award]:
  if t['id'] in removed:continue
  t.update(authority_route=d['route'],authority_path_resolved=True,authority_stage='completion' if t in fire or t in close else 'preconstruction',legal_applicability='provided_project_confirmation_with_scoped_portal_reference')
  t['authority_requirements']=(['fire_design_review_required'] if t in fd else ['construction_permit_required'] if t in permit else ['fire_acceptance_required'] if t in fire and d['fire_route']=='special' else ['fire_filing_required'] if t in fire and d['fire_route']=='other' else ['joint_completion_required'] if t in close else [])
  t['input_notes']=(t.get('input_notes') or '')+'\n属地：苏州工业园区；公开门户参考与项目声明结合；不代表江苏或苏州全市任意项目适用。'
 tasks[0]['local_authority']=d
 if d['fire_route']=='other' and (d['sampling']=='selected' or d['sampling']=='pending' and facts.get('fire_release_policy')=='inspection'):attach_fire_inspection(tasks,facts,gate_move=facts.get('fire_release_policy')!='filing')
 apply_resolved_buffers(tasks,facts,d);dependency_order(tasks)
 return tasks,d
