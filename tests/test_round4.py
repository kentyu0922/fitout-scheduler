"""Round 4 behavioural regressions. Legacy band assertions stay untouched."""
import copy
import json
from datetime import date
from pathlib import Path
import pytest
from tests.conftest import solve_case, ROOT
from core.milestones import snapshot, drawing_review_task
from core._common import parse_predecessor

@pytest.mark.parametrize('statutory', [False, True])
def test_db_signed_contract_design_review_permit_chain(statutory):
    leads=[]
    for area in (800,3000,6000,10000):
        p,_,_=solve_case(delivery='DB',area=area,extra={'statutory_tender_required':statutory})
        ts=p['tasks'];s=snapshot(ts)
        contract=next(t for t in ts if t.get('procurement_step')=='contract' and t.get('module_id')=='procurement_gc')
        sd=next(t for t in ts if '初步设计' in t['name'] and t.get('duration_days',0)>0)
        cd=next(t for t in ts if '全专业施工图' in t['name'] and t.get('duration_days',0)>0)
        # 第五轮追加明确授权：以 A1 替换冲突的第四轮串行断言。
        award=next(t for t in ts if t.get('procurement_step')=='award' and t.get('module_id')=='procurement_gc')
        assert award['finish'] <= cd['start'] < s['review_start'] < s['certificate_finish'] < s['permit'] < s['demolition_start']
        assert contract['finish'] < s['review_start']
        assert 3 <= sd['duration_days'] <= 5 and sd['duration_unit']=='working_days'
        assert (award['id'],'SS',0) in parse_predecessor(cd['predecessors'])
        leads.append(s['permit_lead_calendar_days'])
        assert s['concealed_gap_working_days']=={800:10,3000:14,6000:18,10000:18}[area]
        assert s['drawings_days']=={800:14,3000:21,6000:28,10000:35}[area]
    assert leads==sorted(leads) and leads[0]!=leads[-1]

def test_mocap_one_contract_lead_and_receipt_gate():
    p,_,_=solve_case(delivery='DB',sector='gaming',extra={'addon_a06':'required','lead_mocap_camera_days':90})
    ts=p['tasks'];buys=[t for t in ts if t.get('module_id')=='module_procurement' and '动捕相机' in t['name']]
    assert len(buys)==1
    buy=buys[0];assert buy['duration_days']==90
    assert (date.fromisoformat(buy['finish'])-date.fromisoformat(buy['start'])).days+1==90
    receipt=next(t for t in ts if '动捕相机到货开箱' in t['name'])
    assert receipt['start']>buy['finish']
    assert (buy['id'],'FS',0) in parse_predecessor(receipt['predecessors'])
    assert '不完整' not in receipt['name']
    cam=next(t for t in ts if t.get('mocap_subtask')=='mocap_camera_mount')
    assert cam['start']>receipt['finish']

def test_a20_document_review_not_site_work():
    p,_,_=solve_case(delivery='DB',extra={'addon_a20':'required'})
    t=next(t for t in p['tasks'] if t.get('activity_uid')=='pmp.lifecycle.A20.external_facility')
    assert t['activity_class']=='CONSULTANT_REVIEW'
    assert t['activity_type']=='document_review'
    assert t['duration_days']>0

def test_review_alias_and_semantic_role():
    for name in ('第三方图审机构审查','施工图送审与审查','第三方施工图审查'):
        t={'name':name};assert drawing_review_task([t]) is t
    t={'name':'任意显示名称','control_role':'drawing_review'}
    assert drawing_review_task([t]) is t

def test_optimizer_copy_does_not_mutate_nested_evidence():
    from core.optimizer import analyze_schedule
    p,_,_=solve_case(delivery='DB')
    ts=p['tasks'];before=copy.deepcopy(ts)
    analyze_schedule(ts,start_date='2027-03-01')
    assert ts==before

@pytest.mark.parametrize('delivery',['DB','DBB'])
def test_full_lean_all_dates_match(delivery):
    p,_,_=solve_case(delivery=delivery,area=6000)
    q,_,_=solve_case(delivery=delivery,area=6000,pdf=True)
    key=lambda x:{t['id']:(t.get('start'),t.get('finish')) for t in x['tasks']}
    assert key(p)==key(q)
