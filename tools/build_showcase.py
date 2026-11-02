"""Build the offline interactive architecture page from shipped module rules."""
import copy,json,re,sys,logging
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.module_catalog import CATALOG,RECOMMENDATIONS
from core.industry_base import PROFILES,STAGES
from core.module_integration import INDUSTRY_WORK
from core.module_lifecycle import CARDS
from core.modular_weaver import weave_sector_modules
from core.fitout_wbs import expand_fitout_wbs
from core.master_schedule import build_master_schedule
from core.master_display import _short_name
from core.activity_calendar import apply_activity_calendars
from core.l2_master_duration import apply_l2_master_durations
from core.procurement_policy import PROFILE

logging.disable(logging.CRITICAL)
T=json.loads((ROOT/'templates/wbs_templates.json').read_text(encoding='utf-8'))['templates']
ROUTES=[dict(key='DBB_invite',delivery='DBB',bidding='invite',label='设计施工分开 · 邀请',template='Standard_Fitout_DBB_Invite'),
        dict(key='DBB_public',delivery='DBB',bidding='public',label='设计施工分开 · 公开',template='Standard_Fitout_Office_DBB'),
        dict(key='DB_invite',delivery='DB',bidding='invite',label='设计施工一体 · 邀请',template='Standard_Fitout_DB_Invite'),
        dict(key='DB_public',delivery='DB',bidding='public',label='设计施工一体 · 公开',template='Standard_Fitout_DB_Public')]
CATEGORIES={'项目条件':['A01','A02','A03','A19'],'数字与业务':['A04','A05','A06','A08','A09','A10','A11'],
            '空间与环境':['A07','A12','A13','A17','A18'],'工艺与交付':['A14','A15','A16','A20','A21','A22']}

def human(value):
    text=str(value or '')
    names={'TDD_REPORT_ACCEPTED':'技术尽调成果确认','LEASE_DECISION':'租赁决策','STRUCTURE_RELEASED':'结构使用条件确认','POWER_AVAILABLE':'供电条件确认','DC_READY':'机房部署条件确认','BUSINESS_READY':'业务就绪确认','MOCAP_ACCEPTED':'动捕系统验收','ACOUSTIC_ACCEPTED':'声学性能验收','RND_ZONE_READY':'研发区域就绪','TRADING_READY':'交易区域就绪','SECURITY_ACCEPTED':'安防系统验收','VAULT_READY':'金库交付确认','LAB_FACILITY_READY':'实验设施就绪','CLEANROOM_QUALIFIED':'洁净室性能确认','GAS_READY':'气体系统就绪','CDA_READY':'压缩空气系统就绪','PROCESS_WATER_READY':'工艺用水系统就绪','VIBRATION_ACCEPTED':'振动性能验收','ESD_AREA_ACCEPTED':'防静电区域验收','ENV_EHS_RELEASE':'环境与安全条件放行','QUALITY_RELEASE':'质量放行','FM_SERVICE_READY':'设施运维接管就绪','TOOL_READY':'设备投用就绪','LAB_HVAC':'实验室暖通','C02':'前期策划','C03':'设计','C05':'报批','C07':'设备采购','C08':'实体施工','C09':'测试验收','C11':'交付接管'}
    for key,label in names.items():
        text=re.sub(r'(?<![A-Za-z0-9_])'+key+r'(?![A-Za-z0-9_])(?:\[[^\]]*\])?',label,text)
    for key,item in CATALOG.items():
        text=re.sub(r'(?<![A-Za-z0-9])'+key+r'(?![0-9])',item['label'],text)
    return text

def compose(sector,route,selected=()):
    facts=dict(city='上海',area_sqm=6000,sector=sector,delivery=route['delivery'],bidding=route['bidding'],
               procurement_policy=PROFILE,space_use='office_with_specialist' if selected else 'office_only')
    facts.update({'addon_'+key.lower():'required' if key in selected else 'excluded' for key in CATALOG})
    tasks=weave_sector_modules(expand_fitout_wbs(copy.deepcopy(T[route['template']]['tasks'])),sector,6000,facts)
    plan=tasks[0]['module_plan']
    apply_l2_master_durations(tasks,area_sqm=6000)
    apply_activity_calendars(tasks,'7D')
    master,_=build_master_schedule(tasks)
    return master,plan

def build_data():
    data=dict(version='3.12.0-rc7-modular',date='2026-10-12',routes=ROUTES,categories=CATEGORIES,industries={},addons={},bases={})
    for sector,p in PROFILES.items():
        data['industries'][sector]=dict(label=p['label'],owner=p['owner'],recommended=RECOMMENDATIONS[sector],
            stages=[dict(key=stage,name=human(p[stage][0]),acceptance=human(p[stage][1]),work=list(INDUSTRY_WORK[sector][i])) for i,stage in enumerate(STAGES)])
        for route in ROUTES:
            tasks,_=compose(sector,route)
            grouped={}
            for t in tasks:
                phase=t.get('master_phase')
                if t.get('outline_level')!=1 and phase:
                    grouped.setdefault(phase,[]).append(_short_name(t))
            data['bases'][sector+'_'+route['key']]=grouped
    for key,item in CATALOG.items():
        tasks,plan=compose('biopharma',ROUTES[0],(key,))
        card=CARDS[key]
        data['addons'][key]=dict(label=item['label'],category=next(c for c,keys in CATEGORIES.items() if key in keys),
            depth='总控接口已接入',
            trigger=human(card.get('启用条件')),inputs=human(card.get('启动资料与前置')),
            workflow=human(card.get('内部工序')),parallel=human(card.get('并行与等待规则')),
            outputs=human(card.get('完成成果')),owner=human(card.get('主责与确认')),
            interfaces=human(card.get('下游及共用接口')),boundary=human(card.get('排除与边界')),
            phases=sorted({t.get('master_phase') for t in tasks if t.get('module_id')==key or
                  (item.get('pack') and t.get('module_id')==item.get('pack')) or any(str(u).startswith('pmp.lifecycle.'+key+'.') for u in t.get('source_activity_uids',[]))} - {None}),
            observed_links=[dict(before=human(e['predecessor_name']),after=human(e['successor_name']),why=human(e['reason']))
                for e in plan.get('process_interfaces',[]) if key in str(e.get('predecessor_uid')) or key in str(e.get('successor_uid'))][:12])
    return data

def build():
    data=build_data()
    template=(ROOT/'assets/showcase.html').read_text(encoding='utf-8')
    output=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'模块装配演示.html'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(template.replace('__SHOWCASE_DATA__',json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')),encoding='utf-8')
    print(json.dumps(dict(output=str(output),base_combinations=len(data['bases']),addons=len(data['addons']),bytes=output.stat().st_size),ensure_ascii=False))

if __name__=='__main__':build()
