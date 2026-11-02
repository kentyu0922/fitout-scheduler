"""Reviewable document sources and physical release gates; never date offsets."""
from core.module_graph import add_pred

def apply_controls(tasks, facts):
    cd=next(t for t in tasks if '全专业施工图' in t['name'] and t.get('duration_days',0)>0)
    cd['duration_basis']=str(cd.get('duration_basis',''))+'；规划假设：面积锚点14/21/28/35日历天，校准依据不完整（依据待补）。'
    sh='https://www.shanghai.gov.cn/gwk/search/content/ff0c97d945a243cc9133ca6f52d6d326'
    for t in tasks:
        if t.get('control_role')=='drawing_review' or t.get('permit_role')=='submit':
            t['reference_document']='沪建规范〔2025〕12号' if str(facts.get('city','')).startswith('上海') else '属地依据见报审配置；未确认部分为规划假设'
            if str(facts.get('city','')).startswith('上海'):
                t['reference_article']='第七条' if t.get('control_role')=='drawing_review' else '第八条'
                t['source_url']=sh
        if t.get('procurement_step')=='prepare' and t.get('duration_days',0)>=20 and facts.get('statutory_tender_required'):
            t['source_url']='https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/bgt/art/2023/art_1f79dd79321441a0831f3aed697b4535.html'
            t['reference_article']='招标投标法第二十四条'
        if t.get('procurement_step')=='publicity' and facts.get('statutory_tender_required'):
            t['source_url']='https://www.ccgp.gov.cn/zcfg/gjfg/202309/t20230907_20661132.htm'
            t['reference_article']='招标投标法实施条例第五十四条'
    docs={'design' ,'dq','plan','strategy','organization','logistics','retirement_scope','assets','external_facility'}
    for t in list(tasks):
        step=t.get('lifecycle_step')
        if step in docs or t.get('activity_type')=='document_review':
            phase='交付搬迁' if step=='assets' else '设计与审批'
            t.update(document_work=True,physical_work=False,activity_type='document_review',
                     phase=phase,master_phase=phase,specialist_phase=phase,
                     activity_class='CONSULTANT_REVIEW')
            from core._common import parse_predecessor
            t['document_source_ids']=[p for p,_,_ in parse_predecessor(t.get('predecessors',''))]
            if step=='external_facility':
                src=dict(id=max(x['id'] for x in tasks)+1,name='验证对象设备技术资料及安装调试方案提交',
                    activity_uid='pmp.document.A20.source',module_id='A20',outline_level=3,
                    duration_days=2,duration=2,milestone=False,calendar='5D',
                    activity_class='DESIGN',activity_type='document_review',document_work=True,
                    physical_work=False,phase='设计与审批',master_phase='设计与审批',specialist_phase='设计与审批',
                    predecessors=f'{cd["id"]}FS',document_source_ids=[cd['id']],
                    duration_basis='规划假设：供应商基于施工图提交资料；2工作日，不完整（依据待补）')
                tasks.append(src)
                t['predecessors']=f'{src["id"]}FS';t['document_source_ids']=[src['id']]
                t['acceptance']='设备参数、安装调试方案和验证输入文件齐备；不等同于已安装或已调试'
                t.get('work_duration_review', {})['scope']='核对拟用设备技术资料和安装调试方案；实际记录由后续IQ核验'
                t['input_notes']='核对拟用设备技术资料与安装调试方案；实际安装记录由后续IQ核查。'
    # A physical lifecycle installation never inherits DESIGN from an explanatory suffix.
    for t in tasks:
        if t.get('lifecycle_step') in {'installation','install','construction','implementation'} or t.get('activity_uid') in {
            'pmp.module.trading_desk.2','pmp.module.lab_hvac.1','pmp.module.vault_security.1',
            'pmp.module.anti_vibration.1','pmp.module.cleanroom_iso.1'}:
            t.update(activity_class='SITE_INSTALLATION',physical_work=True)
    elec=next((t for t in tasks if t.get('activity_uid')=='pmp.concealed.electrical_conduit'),None)
    for t in tasks:
        if elec and ('隔墙' in t['name'] and '封板' in t['name']):add_pred(t,elec)
    # Preserve the project-level fire ordering even on alternate local templates.
    linkage=next((t for t in tasks if '全系统 T&C 联动调试' in t['name']),None)
    third=next((t for t in tasks if any(s in t['name'] for s in ('第三方消防设施检测','消防设施与电气防火检测','消防设施功能测试','消防自查','消防设施自查'))),None)
    if linkage and third:add_pred(third,linkage)
    # Other D&B order packages also release on award; no unnamed design wait.
    award=next((t for t in tasks if t.get('procurement_step')=='award' and t.get('module_id')=='procurement_gc'),None)
    if facts.get('delivery')=='DB' and award:
        for t in tasks:
            if t.get('module_id')=='module_procurement' and t.get('outline_level',3)>2:
                t['predecessors']=f'{award["id"]}FS'
                t['input_notes']=str(t.get('input_notes',''))+'；规划假设：中标技术规格及下单授权齐备。'
