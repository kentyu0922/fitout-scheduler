"""Jurisdiction registry. Matching a place is not proof that a project qualifies."""
import json
from pathlib import Path

def registry():return json.loads((Path(__file__).resolve().parents[1]/'config/authority_profiles.json').read_text(encoding='utf-8'))
def match(facts):
 city=str(facts.get('city','')).strip().lower();district=str(facts.get('district','')).strip().lower()
 for p in registry()['profiles']:
  if city in p['city_aliases'] or city in p.get('city_parent_aliases',[]) and district in p.get('district_aliases',[]):return p
 return None

def resolve(facts):
 p=match(facts)
 province=str(facts.get('province','')).strip().lower()
 expected={'shanghai':{'上海','上海市','shanghai'},'sip':{'江苏','江苏省','jiangsu'}}
 if p and p['adapter']=='sip' and str(facts.get('district','')).strip() and str(facts['district']).strip().lower() not in p['district_aliases']:
  return dict(status='CONFLICT',questions=[dict(field='district',question='城市名称指向工业园区，但区县不属于该规则范围，请确认；不能跨区套用园区流程。')],sources=p['sources'],rule_version='JURISDICTION_CONFLICT',jurisdiction=dict(profile_id=p['id'],name=p['name'],adapter=p['adapter'],rule_status='conflict'),classification_independently_verified=False)
 if p and province and province not in expected[p['adapter']]:
  return dict(status='CONFLICT',questions=[dict(field='province',question='省份与城市/园区不一致，请确认，不能按默认覆盖。')],sources=p['sources'],rule_version='JURISDICTION_CONFLICT',jurisdiction=dict(profile_id=p['id'],name=p['name'],adapter=p['adapter'],rule_status='conflict'),classification_independently_verified=False)
 if p and p['adapter']=='shanghai':
  from core.local_authority import _resolve_shanghai
  d=_resolve_shanghai(facts)
 elif p:
  from core.sip_authority import resolve_sip
  d=resolve_sip(facts,p)
 else:
  d=dict(status='NEEDS_LOCAL_RULE',questions=[dict(field='district' if str(facts.get('city','')).strip() in ('苏州','苏州市') else 'authority_basis',question='请一次提供具体区/园区、工程分类及属地办理依据；未匹配已接入规则包，先出有来源的诊断草案，不套用上海结论。')],sources=[],rule_version='NATIONAL_ENTRY_LOCAL_RULE_PENDING',classification_independently_verified=False)
 d['jurisdiction']=dict(profile_id=p['id'] if p else None,name=p['name'] if p else str(facts.get('city','')),adapter=p['adapter'] if p else 'unverified',rule_status=p['status'] if p else 'local_rule_missing',reviewed_on=registry()['reviewed_on'],project_confirmation=facts.get('authority_basis'))
 if p:d['sources']=list(dict.fromkeys(d.get('sources',[])+p['sources']))
 return d

def inspection_source(facts):
 p=match(facts)
 if p and p['adapter']=='shanghai':return dict(document='沪住建规范〔2023〕18号',articles='第37条抽查、第38条整改复查',url='https://zjw.sh.gov.cn/gfxwj/20240329/515ff61a630f42a9a85a95139b7733c0.html',valid_until='2028-12-31')
 if p and p['adapter']=='sip':return dict(document='江苏省2026年消防审验工作指南（地方政府公开转发）',articles='备案抽查及整改复查；不采用上海条款',url=p['sources'][-1],valid_until=None)
 return dict(document='住建部令第58号修正后暂行规定',articles='第39、40条全国参考，属地适用性待核',url='https://www.beijing.gov.cn/zhengce/zhengcefagui/qtwj/202404/t20240415_3617624.html',valid_until=None)
