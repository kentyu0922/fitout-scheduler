"""Effective intake: one evidence-backed view for prompt, intake and reruns."""
import json
from pathlib import Path

def rows(payload):
    from core.programme_delivery import plain_field, plain_value
    from core.module_catalog import CATALOG
    p=payload.get('project',{}); evidence=payload.get('field_evidence',{})
    labels={'province':'省份','district':'区／园区','authority_size_class':'审图规模档位','authority_local_route':'属地适用路径','city':'城市','area_sqm':'建筑面积（㎡）','sector':'行业','delivery':'设计施工方式','bidding':'邀约方式',
       'start_date':'项目启动日','target_date':'目标搬迁日','site_condition':'现场状态','calendar_profile':'施工日历',
       'space_use':'空间用途','industry_work_profile':'行业基础核对','zoning_mode':'施工分区组织','workfronts':'并行工作面',
       'sectional':'分段移交','construction_handover':'施工工作面移交','procurement_policy':'报价评审政策','procurement_regime':'采购制度',
       'furniture_in_scope':'家具安装范围','furniture_procurement':'家具独立招采','furniture_tracking':'家具供货跟踪',
       'furniture_supply_state':'家具供货状态','fire_classification':'消防工程分类','fire_release_policy':'消防放行情境',
       'authority_draft':'报审路径状态','authority_basis':'报审分类依据'}
    keys=['city','province','district','authority_size_class','authority_local_route','area_sqm','sector','delivery','bidding','start_date','target_date',
          'site_condition','calendar_profile','space_use','industry_work_profile',
          'zoning_mode','workfronts','sectional','construction_handover','procurement_policy','procurement_regime',
          'furniture_in_scope','furniture_procurement','furniture_tracking','furniture_supply_state',
          'fire_classification','fire_release_policy','authority_draft','authority_basis']
    effects={'delivery':'决定设计与施工招采链及设计交付接口','bidding':'决定供应商进入竞争的方式；报价政策另列',
       'area_sqm':'用于面积相关施工工期；不代表专项范围均为全楼',
       'calendar_profile':'控制现场作业日历；行政、设计和供货使用各自日历',
       'site_condition':'决定是否安排拆除及场地准备','zoning_mode':'决定是否计入分区组织；未提供分区不假设流水收益',
       'furniture_tracking':'关闭时未计未确认供货交期，安装仍受工作面与采样条件约束',
       'authority_draft':'为真时审批路径尚未核实，不能视为项目许可结论'}
    out=[]
    for key in keys:
        value=p.get(key,'未提供'); ev=evidence.get(key,{})
        status='用户提供' if ev.get('origin')=='provided' else '默认／待确认' if key in p else '未提供'
        if str(ev.get('source','')).startswith('上次运行'):status='沿用上次条件'
        shown=str(plain_value(key,value))
        if key=='construction_handover':shown={'whole':'整体移交，不计分区提前释放','sectional':'分区移交，具体工作面与资源仍需核实','unconfirmed':'待确认；未批准分区流水收益'}.get(value,str(value))
        if key=='zoning_mode':shown={'off':'未计分区流水','auto':'按条件判断','manual':'按明确分区'}.get(str(value),shown)
        if key=='sectional':shown={'off':'未计分段交付','auto':'按适用条件判断','on':'按明确分段'}.get(str(value),shown)
        if key=='industry_work_profile' and key not in p:shown='按行业核对（引擎默认）';status='默认／待确认'
        if key=='authority_draft':shown='诊断草案，尚未核实' if value is True else '按已提供分类判断'
        if isinstance(value,bool) and key!='authority_draft':shown='是' if value else '否'
        out.append(dict(field=key,label=labels.get(key,plain_field(key)),value=shown,
                        status=status,source=str(ev.get('source','未记录来源')),effect=effects.get(key,'')))
    for code,card in CATALOG.items():
        key='addon_'+code.lower();value=p.get(key,'unknown');ev=evidence.get(key,{})
        status={'required':'加载','excluded':'明确排除','unknown':'未确认、不自动加载',
                'existing_claimed':'现有复用，需核验','provided_by_others':'他方提供，需确认接口及日期'}.get(value,str(value))
        out.append(dict(field=key,label=card['label'],value=status,status='用户提供' if ev.get('origin')=='provided' else '待确认',
                        source=str(ev.get('source','未提供')),effect='按已选专项接入设计、供货、施工与验收；具体工序以本次模块清单为准' if value=='required' else ''))
    return out

def summary(payload):
    from core.programme_delivery import plain_value
    p=payload.get('project',{})
    return '本次条件：'+ '；'.join(str(plain_value(k,p.get(k,'未提供'))) for k in ('site_condition','calendar_profile','delivery','bidding'))+'。完整输入、来源与未确认项见《本次排程条件》。'

def write_conditions(output,payload):
    data=rows(payload)
    output.with_name(output.stem+'_有效条件.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        from docx import Document
    except ImportError:
        path=output.with_name(output.stem+'_本次排程条件.txt')
        path.write_text('本次排程条件\n'+'\n'.join(x['label']+'：'+x['value']+'（'+x['status']+'）' for x in data),encoding='utf-8-sig')
        return path
    from docx.shared import Inches,Pt,RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc=Document();sec=doc.sections[0];sec.top_margin=Inches(.6);sec.bottom_margin=Inches(.6)
    for name in ['Normal','Heading 1','Heading 2']:
        st=doc.styles[name];st.font.name='Arial';st.font.size=Pt(10 if name=='Normal' else 16 if name=='Heading 1' else 12)
        st.font.color.rgb=RGBColor.from_string('1A1A1A' if name=='Normal' else '003E51')
        st.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'DengXian')
    doc.add_heading('本次排程条件',0)
    doc.add_paragraph(payload.get('project',{}).get('project_name','项目'))
    doc.add_paragraph('本表展示实际传入排程的条件。默认值不是现场事实；未填专项不等于明确排除。修改条件后请重算。')
    for title,items in [('项目与排程条件',[x for x in data if not x['field'].startswith('addon_')]),('专项范围',[x for x in data if x['field'].startswith('addon_') and x['value'] not in {'明确排除','未确认、不自动加载'}])]:
        doc.add_heading(title,2);table=doc.add_table(rows=1+len(items),cols=3)
        flat_cells=table._cells
        for c,t in zip(flat_cells[:3],['条件','本次采用','来源／状态']):c.text=t
        for index,x in enumerate(items,1):
            cells=flat_cells[index*3:index*3+3]
            cells[0].text=x['label'];cells[1].text=x['value'];cells[2].text=x['status']
            if x['status']=='用户提供':cells[2].text+='\n'+('对话' if x['source']=='explicit_cli_or_prompt_facts' else Path(x['source']).name)
        for cell in flat_cells[:3]:
            sh=OxmlElement('w:shd');sh.set(qn('w:fill'),'BCDEE6');cell._tc.get_or_add_tcPr().append(sh)
    excluded=[x['label'] for x in data if x['field'].startswith('addon_') and x['value']=='明确排除']
    unknown=[x['label'] for x in data if x['field'].startswith('addon_') and x['value']=='未确认、不自动加载']
    if excluded:doc.add_paragraph('本次明确排除：'+'、'.join(excluded)+'。')
    if unknown:doc.add_paragraph('未确认、未自动加载：'+'、'.join(unknown)+'。')
    doc.add_heading('条件如何影响本次计划',2)
    for x in data:
        if x['effect']:doc.add_paragraph(x['label']+'：'+x['effect'])
    from tools.pm_brief import stamp_line
    doc.add_paragraph(stamp_line())
    from tools.office_document import apply
    apply(doc,'本次排程条件')
    path=output.with_name(output.stem+'_本次排程条件.docx');doc.save(path);return path
