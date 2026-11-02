"""Two readable programme slides from actual phase spans and effective intake."""
import datetime as dt
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches,Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.xmlchemy import OxmlElement

COLORS={'space':'1F1F1F','ink':'1A1A1A','ocean':'D9D6F0','grey':'888888','light':'F7F5FB','red':'6B5CE7','white':'FFFFFF'}
def rgb(key):return RGBColor.from_string(COLORS.get(key,key))
def text(s,x,y,w,h,value,size=20,color='ink',bold=False):
    box=s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h));tf=box.text_frame
    tf.word_wrap=True
    for i,line in enumerate(str(value).split('\n')):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph();p.space_after=Pt(4)
        r=p.add_run();r.text=line;r.font.name='Arial';r.font.size=Pt(size);r.font.bold=bold;r.font.color.rgb=rgb(color)
        ea=OxmlElement('a:ea');ea.set('typeface','DengXian');r._r.get_or_add_rPr().append(ea)
    return box
def rect(s,x,y,w,h,color='ocean'):
    sh=s.shapes.add_shape(MSO_SHAPE.RECTANGLE,Inches(x),Inches(y),Inches(w),Inches(h));sh.fill.solid();sh.fill.fore_color.rgb=rgb(color);sh.line.fill.background();return sh
def chrome(s,title,subtitle='',page=1):
    rect(s,.55,.42,.65,.045,'red');text(s,.55,.58,11.8,.55,title,28,'space',True)
    text(s,.55,1.18,12,.5,subtitle,13,'grey')
    logo=Path(__file__).resolve().parents[1]/'assets/brand-mark.png'
    if logo.exists():s.shapes.add_picture(str(logo),Inches(11.65),Inches(.35),width=Inches(.95))
    text(s,.55,7.06,11,.25,'PMP 总控进度｜由本次排程生成；日期及条件以本次交付为准',10,'grey')
    text(s,12.1,7.06,.5,.25,str(page),10,'grey')

def generate(project_title,project_meta,tasks,output_pptx_path,programme_certainty=None):
    prs=Presentation();prs.slide_width=Inches(13.333);prs.slide_height=Inches(7.5)
    visible=[t for t in tasks if t.get('client_visible') is not False and t.get('start') and t.get('finish')]
    from core.master_reporting import SUPPLY_TRACK
    phases=[t for t in visible if t.get('outline_level')==2 and t.get('name')!=SUPPLY_TRACK]
    start=min(t['start'] for t in visible);finish=max(t['finish'] for t in visible)
    a=dt.date.fromisoformat(start[:10]);b=dt.date.fromisoformat(finish[:10]);span=max(1,(b-a).days)
    contract=project_meta.get('input_contract') or {}
    notice='诊断草案·报审路径待核实｜' if contract.get('project',{}).get('authority_draft') and '诊断草案' not in str(project_title) else ''
    s=prs.slides.add_slide(prs.slide_layouts[6]);chrome(s,'一眼看懂项目主线',notice+str(project_title),1)
    text(s,.6,1.7,6,.65,start+'  →  '+finish,24,'space',True)
    text(s,7.4,1.78,5.1,.5,'阶段允许重叠；横条取真实汇总日期',15,'grey')
    x0=3.1;w=9.4
    for i,t in enumerate(phases):
        y=2.55+i*.49;beg=dt.date.fromisoformat(t['start'][:10]);end=dt.date.fromisoformat(t['finish'][:10])
        text(s,.6,y-.04,2.5,.42,t.get('client_task_name',t['name']),17,'space',True)
        left=x0+(beg-a).days/span*w;width=max(.055,((end-beg).days+1)/span*w)
        rect(s,x0,y+.06,w,.20,'light');rect(s,left,y+.06,min(width,x0+w-left),.20,'space' if i==4 else 'ocean')
    picks=[]
    for label,predicate in [('施工许可',lambda t:t.get('permit_role')=='result'),('物理竣工',lambda t:'物理竣工' in t.get('name',''))]:
        t=next((t for t in visible if predicate(t)),None)
        if t:picks.append((label,t['start'] if label=='搬迁开始' else t['finish']))
    from core.solver_engine import move_in_date
    moved=move_in_date(tasks)
    if moved:picks.append(('搬迁开始',moved.isoformat()))
    picks.append(('全部完成',finish))
    for i,(label,date) in enumerate(picks):
        x=.6+i*3.15;rect(s,x,5.85,2.95,.85,'light');text(s,x+.12,5.90,2.7,.25,label,12,'grey');text(s,x+.12,6.19,2.7,.4,date,21,'space',True)
    s=prs.slides.add_slide(prs.slide_layouts[6]);chrome(s,'先看条件，再判断日期',str(project_title),2)
    from tools.condition_sheet import rows
    contract=project_meta.get('input_contract') or {}
    data=rows(contract) if contract else []
    selected=[x for x in data if x['field'] in {'city','area_sqm','delivery','bidding','site_condition','calendar_profile'}]
    for i,x in enumerate(selected):
        col=i%2;row=i//2;px=.6+col*6.3;py=1.95+row*.8
        text(s,px,py,5.8,.3,x['label'],13,'grey');text(s,px,py+.27,5.8,.42,x['value']+' · '+x['status'],19,'space',True)
    addons=[x['label'] for x in data if x['field'].startswith('addon_') and x['value']=='加载']
    text(s,.6,4.55,12,.75,'已加载专项：'+('、'.join(addons) if addons else '无明确加载；未填不等于明确排除'),19,'ink')
    draft=bool(contract.get('project',{}).get('authority_draft'))
    text(s,.6,5.5,12,.9,('报审分类尚未核实，本次为诊断草案。' if draft else '报审按本次已提供条件判断，仍待项目PM确认。')+'\n家具交期、资源及分区条件请核对《本次排程条件》。',17,'space')
    out=Path(output_pptx_path);out.parent.mkdir(parents=True,exist_ok=True);prs.save(out);return str(out)
