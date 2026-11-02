"""Reproducible 67-case acceptance manifest and black-box runner."""
import json, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def cases():
    out=[]
    base=dict(city='上海',area=3000,sector='finance',bidding='invite',start_date='2027-03-01',statutory_tender_required=False)
    for stat in (False,True):
        for area in (800,3000,6000,10000):
            for delivery in ('DBB','DB'):
                out.append((f'matrix_{int(stat)}_{area}_{delivery}',dict(base,area=area,delivery=delivery,statutory_tender_required=stat)))
    for i in range(1,23):
        sector='gaming' if i in (6,7) else 'biopharma' if i in (12,19,20) else 'semiconductor' if i in (13,14,15,16) else 'finance'
        for delivery in ('DBB','DB'):
            out.append((f'a{i:02}_{delivery}',dict(base,delivery=delivery,sector=sector,space_use='office_with_specialist',**{f'addon_a{i:02}':'required'})))
    for delivery in ('DBB','DB'):
        out.append((f'all22_{delivery}',dict(base,delivery=delivery,sector='gaming',**{f'addon_a{i:02}':'required' for i in range(1,23)})))
    for label,filename in [('EX_MOCAP','aaa_gaming_mocap_idc.json'),('EX_SEMI','semiconductor_lab_suzhou.json')]:
        out.append((label,{'project_input':str(ROOT/'examples'/filename)}))
    for label in ('SH_REVIEWED','SHANGHAI_GAMING_6000_LEAN'):
        out.append((label,{'intake_excel':str(ROOT/'templates'/f'PROJECT_INTAKE_EXAMPLE_{label}.xlsx')}))
    out.append(('mocap90',dict(base,delivery='DB',sector='gaming',addon_a06='required',lead_mocap_camera_days=90)))
    assert len(out)==67
    return out

def run_case(name,facts,out,lean):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    cmd=[sys.executable,str(ROOT/'main.py'),'--output',str(out/'p.mpp'),'--no_mpp']
    if lean:cmd+=['--mpp_only']
    if not ('project_input' in facts or 'intake_excel' in facts):cmd+=['--project_name',name]
    for k,v in facts.items():cmd += ['--'+k,str(v).lower() if isinstance(v,bool) else str(v)]
    start=time.perf_counter();r=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,encoding='utf8');elapsed=time.perf_counter()-start
    (out/'run.log').write_text(r.stdout+r.stderr,encoding='utf8')
    assert r.returncode==0,(name,lean,r.returncode,(r.stdout+r.stderr)[-3000:])
    return json.loads((out/'p_schedule.json').read_text()),elapsed
