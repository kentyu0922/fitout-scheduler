"""Round5 release acceptance. Assertions complement, never suppress legacy tests."""
import json
from datetime import date,timedelta
from pathlib import Path
import pytest
from tools.round5_acceptance import cases,run_case,ROOT
from core._common import parse_predecessor
from core.milestones import (snapshot,construction_drawings_task,drawing_review_task,
    drawing_rework_task,drawing_certificate_task,mocap_subtasks)
from core.holidays import load_holiday_pairs,build_holiday_bitmap,is_workday

ROWS={}
@pytest.fixture(scope='session')
def evidence():
    yield ROWS
    out=ROOT/'output'/'round5_acceptance.json';out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(ROWS,ensure_ascii=False,indent=2),encoding='utf8')

def find(ts,*needles):
    return next(t for t in ts if any(x in t['name'] for x in needles))

def fs_path(ts,source,target):
    byid={t['id']:t for t in ts};seen=set()
    def walk(t):
        if t['id']==source['id']:return True
        if t['id'] in seen:return False
        seen.add(t['id'])
        return any(walk(byid[p]) for p,r,l in parse_predecessor(t.get('predecessors','')) if r=='FS' and p in byid)
    return walk(target)

def workcount(t):
    bitmap=build_holiday_bitmap(load_holiday_pairs());start=date.fromisoformat(t['start']);end=date.fromisoformat(t['finish'])
    return sum(is_workday(start+timedelta(days=i),bitmap) for i in range((end-start).days+1))

@pytest.mark.parametrize('name,facts',cases(),ids=[c[0] for c in cases()])
def test_67_cases_order_parity_and_semantics(name,facts,tmp_path,evidence):
    p,lean_s=run_case(name,facts,tmp_path/'lean',True)
    q,full_s=run_case(name,facts,tmp_path/'full',False)
    assert full_s<=1.5 and lean_s<=1.5,(name,full_s,lean_s)
    ts=p['tasks'];s=snapshot(ts)
    dates=lambda z:{t['id']:(t.get('start'),t.get('finish')) for t in z['tasks']}
    assert dates(p)==dates(q),name
    cd,review,rework,cert=(f(ts) for f in (construction_drawings_task,drawing_review_task,drawing_rework_task,drawing_certificate_task))
    permit=next(t for t in ts if t.get('permit_role')=='result')
    assert cd['finish']<review['start']<rework['finish']<=cert['finish']<permit['finish']<s['demolition_start'],(name,s)
    for a,b in ((cd,review),(review,rework),(rework,cert),(cert,permit)):
        assert fs_path(ts,a,b),(name,a['name'],b['name'])
    site=[t for t in ts if t.get('activity_type') in ('demolition','waste_removal','setting_out') or any(x in t['name'] for x in ('场地移交与','原有装修拆除','建筑垃圾清运'))]
    assert len(site)>=3
    for t in site:
        assert permit['finish']<t['start'] and fs_path(ts,permit,t),(name,t['name'])
    for t in ts:
        if not t.get('summary') and (t.get('activity_class') in ('SITE_INSTALLATION','SITE_CONSTRUCTION') or t.get('physical_work')):
            assert permit['finish']<t['start'],(name,t['name'],'physical work before permit')
        if 'total_slack_days' in t:
            assert t['total_slack_days']>=0,(name,t['name'],t['total_slack_days'])
        for pid,rel,lag in parse_predecessor(t.get('predecessors','')):
            assert lag>=0,(name,t['name'],pid,rel,lag)
    if facts.get('city','上海')=='上海' and name!='EX_SEMI':
        assert review['duration_days']==5 and workcount(review)==5,(name,review)
        admin=next(t for t in ts if t.get('permit_role')=='submit')
        assert admin['duration_days']==2 and workcount(admin)==2,(name,admin)
    if facts.get('statutory_tender_required'):
        for t in ts:
            if t.get('procurement_step') in ('prepare','publicity'):
                minimum=20 if t['procurement_step']=='prepare' else 3
                assert t['duration_days']>=minimum and t.get('elapsed_window'),(name,t)
        for package in {t.get('module_id') for t in ts if t.get('procurement_step')=='prepare'}:
            issue=next(t for t in ts if t.get('module_id')==package and t.get('procurement_step')=='issue')
            deadline=next(t for t in ts if t.get('module_id')==package and t.get('procurement_step')=='quote1')
            assert (date.fromisoformat(deadline['finish'])-date.fromisoformat(issue['start'])).days>=20
    ceiling=find(ts,'天花隐蔽工程验收','天花隐蔽验收')
    for uid in ('sprinkler_strength','sprinkler_hydrostatic','duct_leakage','electrical_conduit'):
        t=next(t for t in ts if t.get('activity_uid')=='pmp.concealed.'+uid)
        assert t['finish']<ceiling['start'] and fs_path(ts,t,ceiling),(name,t['name'])
    strength=next(t for t in ts if t.get('activity_uid')=='pmp.concealed.sprinkler_strength')
    assert '1.4MPa' in strength['name'] and '30分钟' in strength['name']
    hydro=next(t for t in ts if t.get('activity_uid')=='pmp.concealed.sprinkler_hydrostatic')
    assert '24小时' in hydro['name'] and hydro['duration_days']>=1 and hydro['elapsed_window']
    elec=next(t for t in ts if t.get('activity_uid')=='pmp.concealed.electrical_conduit')
    for t in ts:
        if '隔墙' in t['name'] and '封板' in t['name']:
            assert (elec['id'],'FS',0) in parse_predecessor(t['predecessors']) and elec['finish']<t['start']
    linkage=find(ts,'全系统 T&C 联动调试')
    third=find(ts,'第三方消防设施检测','消防设施与电气防火检测','消防设施功能测试','消防自查','消防设施自查')
    assert linkage['finish']<third['start'] and fs_path(ts,linkage,third)
    fire=find(ts,'取得消防验收合格意见','取得消防验收备案凭证','消防验收通过或备案完成','消防备案完成','取得《建设工程消防验收合格意见书》')
    assert third['finish']<=fire['finish'] and fs_path(ts,third,fire)
    filing=find(ts,'工程竣工验收备案','竣工验收备案','住建局联合竣工')
    assert fire['finish']<=filing['start'] and fs_path(ts,fire,filing)
    for t in ts:
        if '消防验收' in t['name'] and t.get('duration_days')==15:
            assert t['elapsed_window'] and t['duration_unit']=='calendar_days'
            assert (date.fromisoformat(t['finish'])-date.fromisoformat(t['start'])).days+1==15
    byid={t['id']:t for t in ts}
    for t in ts:
        if t.get('document_work'):
            assert '施工' not in str(t.get('master_phase'))+str(t.get('reporting_phase')),(name,t['name'])
            sources=t['document_source_ids'];assert sources,(name,t['name'])
            assert all(pid in byid and byid[pid]['finish']<=t['start'] for pid in sources),(name,t['name'],sources)
            assert set(sources)=={pid for pid,_,_ in parse_predecessor(t['predecessors'])},(name,t)
    a20=next((t for t in ts if t.get('activity_uid')=='pmp.lifecycle.A20.external_facility'),None)
    if a20:
        src=next(t for t in ts if t.get('activity_uid')=='pmp.document.A20.source')
        assert (src['id'],'FS',0) in parse_predecessor(a20['predecessors']) and cd['finish']<src['start']<a20['start']
    if 'mocap_camera_mount' in mocap_subtasks(ts):
        assert len(set(mocap_subtasks(ts)))==9
        cam=next(t for t in ts if t.get('mocap_subtask')=='mocap_camera_mount')
        leaf=next(t for t in ts if t.get('mocap_subtask')=='mocap_fire_door_leaf')
        assert leaf['finish']<linkage['start'] and fire['finish']<cam['start']
        buys=[t for t in ts if t.get('module_id')=='module_procurement' and '动捕相机' in t['name']]
        assert len(buys)==1
        if name=='mocap90':
            assert buys[0]['duration_days']==90
            assert buys[0]['finish']<find(ts,'动捕相机到货开箱')['start']
    if facts.get('delivery')=='DB':
        award=next(t for t in ts if t.get('procurement_step')=='award' and t.get('module_id')=='procurement_gc')
        contract=next(t for t in ts if t.get('procurement_step')=='contract' and t.get('module_id')=='procurement_gc')
        confirm=find(ts,'初步设计确认/深化')
        assert award['finish']<=cd['start'] and contract['finish']<review['start']
        assert 3<=workcount(confirm)==confirm['duration_days']<=5
        issue=next(t for t in ts if t.get('procurement_step')=='issue' and t.get('module_id')=='procurement_gc')
        release=find(ts,'业主Test-fit招标版')
        assert (release['id'],'FS',0) in parse_predecessor(issue['predecessors'])
        for t in ts:
            if t.get('module_id')=='module_procurement' and t.get('outline_level',3)>2:
                assert (award['id'],'FS',0) in parse_predecessor(t['predecessors'])
    assert p['project_name'] and p['project_name']!='未命名项目'
    assert all('total_slack_days' in t and 'on_driving_chain' in t for t in ts if t.get('outline_level',3)>=3 and not t.get('control_rollup') and not t.get('summary'))
    area=facts.get('area')
    if name.startswith('matrix_'):
        assert s['drawings_days']=={800:14,3000:21,6000:28,10000:35}[area]
        assert s['concealed_gap_working_days']=={800:10,3000:14,6000:18,10000:18}[area]
    evidence[name]=dict(site_durations={t['activity_uid']:t['duration_days'] for t in ts if t.get('activity_uid') and t.get('activity_class') in ('SITE_INSTALLATION','SITE_CONSTRUCTION')},snapshot=s,full_seconds=full_s,lean_seconds=lean_s,dates_different=0,legal_violations=0,finish=p['finish_date'],start=p['start_date'])

def test_eight_db_totals_bands_and_monotonic(evidence):
    assert len(evidence)==67
    for stat in (0,1):
        for delivery in ('DB','DBB'):
            leads=[evidence[f'matrix_{stat}_{a}_{delivery}']['snapshot']['permit_lead_calendar_days'] for a in (800,3000,6000,10000)]
            assert leads==sorted(leads) and leads[0]<leads[-1]
            small=evidence[f'matrix_{stat}_6000_{delivery}']['site_durations'];large=evidence[f'matrix_{stat}_10000_{delivery}']['site_durations']
            assert any(large[k]>v for k,v in small.items() if k in large), '现场时长必须随面积变化' 
            lo,hi=({'DB':(40,85),'DBB':(55,95)} if not stat else {'DB':(55,100),'DBB':(80,115)})[delivery]
            assert all(lo<=v<=hi for v in leads)
        for area in (800,3000,6000,10000):
            a,b=(evidence[f'matrix_{stat}_{area}_{d}'] for d in ('DBB','DB'))
            assert b['finish']<=a['finish']
            assert -15<=a['snapshot']['permit_lead_calendar_days']-b['snapshot']['permit_lead_calendar_days']<=45

@pytest.mark.parametrize('start,expected',[('2026-02-13','2026-02-27'),('2026-09-30','2026-10-13')])
def test_official_long_holidays(start,expected):
    from core.solver_engine import solve_schedule
    ts=[dict(id=1,name='图纸审查',outline_level=3,duration_days=5,activity_calendar=dict(calendar_mode='5D',working_days_per_week=5))]
    solved=solve_schedule(ts,start)
    assert solved['tasks'][0]['finish']==expected

@pytest.mark.parametrize('key,value',[('area_sqm',-1),('area_sqm',0),('area_sqm','oops'),('area_sqm','nan'),('area_sqm','inf'),('mocap_camera_count',0),('mocap_camera_count','36.2'),('mocap_stage_area_sqm',0),('lead_mocap_camera_days',-1)])
def test_numeric_input_blockers(key,value):
    from core.project_input import normalize_project_inputs
    result=normalize_project_inputs([('test',dict(city='上海',area_sqm=3000,sector='finance',delivery='DB',bidding='invite',start_date='2027-03-01',**{key:value}) if key!='area_sqm' else dict(city='上海',area_sqm=value,sector='finance',delivery='DB',bidding='invite',start_date='2027-03-01'))])
    assert any(b['field']==key for b in result.blockers)

def test_numeric_strings_and_blank_defaults():
    from core.project_input import normalize_project_inputs
    p=normalize_project_inputs([('test',dict(city='上海',area_sqm='3000.0',sector='gaming',delivery='DB',bidding='invite',start_date='2027-03-01',mocap_camera_count='36.0',project_name=None))])
    assert not p.blockers and p.project['mocap_camera_count']==36 and p.project['area_sqm']==3000
    assert p.project['project_name']!='未命名项目' and p.evidence['project_name']['origin']=='default_policy'

@pytest.mark.parametrize('field,value',[('area_sqm',-5),('sector','bad_sector'),('start_date','2027-02-30')])
def test_xlsx_invalid_fields(field,value,tmp_path):
    import openpyxl
    from core.project_input import load_input,normalize_project_inputs
    facts=dict(city='上海',area_sqm=3000,sector='finance',delivery='DB',bidding='invite',start_date='2027-03-01');facts[field]=value
    wb=openpyxl.Workbook();ws=wb.active;ws.append(['field','value'])
    for k,v in facts.items():ws.append([k,v])
    p=tmp_path/'bad.xlsx';wb.save(p);wb.close()
    result=normalize_project_inputs([('xlsx',load_input(p))])
    assert any(b['field']==field for b in result.blockers)

def test_segmented_approvals_require_local_confirmation():
    from core.project_input import _clean
    assert _clean('segmented_review','false') is False
    assert _clean('segmented_permit',False) is False
    for key in ('segmented_review','segmented_permit'):
        with pytest.raises(ValueError,match='需项目所在地主管部门确认'):_clean(key,True)

def test_legacy_physical_installations_use_site_calendar():
    from core.round5_controls import apply_controls
    from core.activity_calendar import resolve_activity_calendar
    uids=['trading_desk.2','lab_hvac.1','vault_security.1','anti_vibration.1','cleanroom_iso.1']
    ts=[dict(id=1,name='全专业施工图',duration_days=21)]
    ts += [dict(id=i+2,name='设施安装（按批准设计）',activity_uid='pmp.module.'+uid) for i,uid in enumerate(uids)]
    apply_controls(ts,{'delivery':'DB'})
    for t in ts[1:]:
        cal=resolve_activity_calendar(t,construction_calendar_mode='7D')
        assert t['physical_work'] and cal['site_calendar_bound'] and cal['working_days_per_week']==7

def test_fire_15_is_explicit_calendar_assumption(tmp_path):
    facts=dict(city='上海',area_sqm=3000,sector='finance',delivery='DB',bidding='invite',start_date='2026-12-01',fire_classification='special',project_name='消防十五日规划假设检查')
    f=tmp_path/'special.json';f.write_text(json.dumps(facts,ensure_ascii=False))
    p,_=run_case('special',{'project_input':str(f)},tmp_path/'run',True)
    fire=find(p['tasks'],'特殊建设工程消防验收申请','消防验收申报与现场评定')
    assert fire['duration_days']==15 and fire['elapsed_window'] and fire['duration_unit']=='calendar_days'
    assert (date.fromisoformat(fire['finish'])-date.fromisoformat(fire['start'])).days+1==15
    assert '不完整' in fire['duration_basis'] and '规划假设' in fire['duration_basis']

def test_pdf_missing_lead_banner_and_chinese_render(tmp_path):
    import fitz
    p,_=run_case('中文交货期检查',dict(city='上海',area=800,sector='gaming',delivery='DB',bidding='invite',start_date='2027-03-01',addon_a06='required'),tmp_path,False)
    doc=fitz.open(tmp_path/'p.pdf');page=doc[0]
    assert '交货期未填，不完整' in page.get_text()
    assert '中文交货期检查' in page.get_text()
    words=page.get_text('blocks');assert any('交货期未填，不完整' in b[4] and b[1]<100 for b in words)
    pix=page.get_pixmap(matrix=fitz.Matrix(1,1));pix.save(tmp_path/'中文预览.png')
    assert pix.width>1000 and pix.height>700 and len(set(pix.samples))>20
    doc.close()

@pytest.mark.parametrize('all22',[False,True])
def test_default_delivery_under_one_point_five_seconds(all22,tmp_path):
    import subprocess,sys,time
    cmd=[sys.executable,str(ROOT/'tools/run_contract.py'),'--city','上海','--area','6000','--sector','finance','--delivery','DB','--bidding','invite','--start_date','2027-03-01','--output',str(tmp_path/'p.mpp'),'--no_mpp']
    if all22:
        for i in range(1,23):cmd += [f'--addon_a{i:02}','required']
    started=time.perf_counter();r=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True);elapsed=time.perf_counter()-started
    assert r.returncode==0 and json.loads(r.stdout)['delivery_status']=='DELIVERED',r.stderr[-2000:]
    assert elapsed<=1.5,(all22,elapsed)


def test_float_backward_pass_uses_dependency_order():
    from core.solver_engine import solve_schedule
    from core.optimizer import compute_total_float,_Cal
    tasks=[dict(id=2,name='第二工序',outline_level=3,duration_days=2,predecessors='3FS',calendar='5D'),
           dict(id=1,name='第三工序',outline_level=3,duration_days=1,predecessors='2FS',calendar='5D'),
           dict(id=3,name='第一工序',outline_level=3,duration_days=2,predecessors='',calendar='5D')]
    solved=solve_schedule(tasks,'2027-03-01')['tasks']
    result=compute_total_float(solved,_Cal(2027))
    assert len(result)==3 and all(row['float_days']==0 for row in result.values()),result


@pytest.mark.parametrize('mode',['5D','6D','7D','WEEKEND'])
@pytest.mark.parametrize('ignore',[False,True])
def test_float_calendar_prefix_matches_daily_count(mode,ignore):
    from core.optimizer import _Cal
    cal=_Cal(2026);t=dict(ignore_holidays=ignore,use_construction_cal=mode!='5D',activity_calendar=dict(calendar_mode=mode))
    pairs=[('2026-02-10','2026-03-06'),('2026-09-29','2026-10-15'),('2026-02-20','2026-02-12'),('2024-01-01','2024-01-07'),('2026-03-01','2026-03-01')]
    for a,b in pairs:
        a,b=date.fromisoformat(a),date.fromisoformat(b);lo,hi=sorted([a,b])
        expected=sum(cal.is_working(lo+timedelta(days=i),t) for i in range(1,(hi-lo).days+1))
        assert cal.count_between(a,b,t)==expected*(1 if b>=a else -1)

def test_report_projections_preserve_input_evidence_and_dates():
    import copy
    from core.master_display import arrange_master
    from core.schedule_metrics import synchronize_schedule_metrics
    from core.pm_workpackages import overview
    tasks=[dict(id=1,name='项目',outline_level=1,start='2027-03-01',finish='2027-03-02'),
           dict(id=2,name='设计任务',outline_level=3,start='2027-03-01',finish='2027-03-02',duration_days=2,predecessors='',master_phase='设计与审批',evidence={'records':['input']},specialist_core_step=True)]
    before=copy.deepcopy(tasks)
    arranged=arrange_master(tasks)
    metrics=synchronize_schedule_metrics(tasks,{'float':{'by_task':[dict(id=2,float_days=0,critical=True,on_driving_chain=True)]}})
    shown=overview(tasks)
    assert tasks==before
    for view in (arranged,metrics,shown):
        row=next(t for t in view if t['id']==2)
        assert (row['start'],row['finish'],row['evidence'])==('2027-03-01','2027-03-02',{'records':['input']})


def test_master_network_owns_nested_evidence():
    import copy
    from core.master_schedule import build_master_schedule
    from core.solver_engine import solve_schedule
    tasks=[dict(id=1,name='项目',outline_level=1,duration_days=0,predecessors=''),
           dict(id=2,name='设计任务',outline_level=3,duration_days=2,predecessors='1FS',
                activity_calendar=dict(calendar_id='OFFICE_5D',calendar_mode='5D'),
                evidence={'records':['original']})]
    before=copy.deepcopy(tasks)
    network,_=build_master_schedule(tasks)
    solved=solve_schedule([dict(t) for t in tasks],'2027-03-01')
    assert tasks==before and solved['finish_date']=='2027-03-02'
    next(t for t in network if t['id']==2)['evidence']['records'].append('output only')
    assert tasks==before


def test_document_styling_preserves_text_and_chinese_fonts():
    from docx import Document
    from docx.oxml.ns import qn
    from tools.office_document import apply
    doc=Document();title=doc.add_heading('中文标题',0)
    normal=doc.add_paragraph('资料');normal.add_run('核查')
    table=doc.add_table(rows=3,cols=2);table.cell(0,0).merge(table.cell(0,1)).text='合并表头'
    table.cell(1,0).text='安装方案';table.cell(2,1).text='已提交'
    text=[p.text for p in doc.paragraphs]+[c.text for c in table._cells]
    apply(doc,'交付说明')
    assert text==[p.text for p in doc.paragraphs]+[c.text for c in table._cells]
    for p in list(doc.paragraphs)+[p for c in table._cells for p in c.paragraphs]:
        for run in p.runs:
            assert run.font.name=='Arial' and run._r.rPr.rFonts.get(qn('w:eastAsia'))=='DengXian'
            assert str(run.font.color.rgb)==('003E51' if p._p is title._p else '1A1A1A')


def test_cold_unpacked_full_delivery_under_one_point_five_seconds(tmp_path):
    import shutil,subprocess,sys,time
    from tools.build_pack import files
    root=tmp_path/'source';out=tmp_path/'out';out.mkdir()
    for relative in files('full'):
        target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/relative,target)
    assert not list(root.rglob('*.pyc'))
    cmd=[sys.executable,str(root/'tools/run_contract.py'),'--city','上海','--area','6000','--sector','finance','--delivery','DB','--bidding','invite','--start_date','2027-03-01','--output',str(out/'p.mpp'),'--no_mpp']
    for i in range(1,23):cmd += [f'--addon_a{i:02}','required']
    started=time.perf_counter();r=subprocess.run(cmd,cwd=root,capture_output=True,text=True);elapsed=time.perf_counter()-started
    assert r.returncode==0 and json.loads(r.stdout)['delivery_status']=='DELIVERED',r.stderr[-2000:]
    assert elapsed<=1.5,elapsed
