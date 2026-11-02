"""BD bid milestone board: dated control points, not an artificial serial WBS."""
import datetime as dt
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches,Pt
from pptx.enum.shapes import MSO_SHAPE,MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from exporters.pm_timeline import text,rect,chrome,rgb

def select_points(tasks):
    from core.solver_engine import move_in_date
    real=[t for t in tasks if not t.get('summary') and t.get('client_visible') is not False and t.get('start') and t.get('finish')]
    if not real:
        raise ValueError('BD_TIMELINE_NO_DATED_POINTS')
    points=[dict(label='项目启动',date=min(t['start'][:10] for t in real),basis='项目启动日')]
    rules=[('总包合同签订',lambda t:('施工总包合同签订完成' in t['name'] or '设计施工总包合同' in t['name'] or 'D&B 总承包合同签订完成' in t['name']) and t.get('milestone')),
           ('施工图确认',lambda t:'施工图全套确认' in t['name'] or t.get('client_task_name')=='施工图确认'),
           ('施工许可取得',lambda t:t.get('permit_role')=='result'),
           ('物理竣工',lambda t:'物理竣工' in t['name']),
           ('空气检测合格',lambda t:'室内空气质量检测合格' in t['name'] or t.get('client_task_name')=='室内空气质量检测合格')]
    for label,predicate in rules:
        found=[t for t in real if predicate(t)]
        if found:
            t=max(found,key=lambda t:t['finish']);points.append(dict(label=label,date=t['finish'][:10],uid=t.get('activity_uid'),basis=t['name']))
    moved=move_in_date(tasks)
    if moved:points.append(dict(label='搬迁开始',date=moved.isoformat(),basis='引擎搬迁开始控制点'))
    points.append(dict(label='全部完成',date=max(t['finish'][:10] for t in real),basis='项目最晚完成日'))
    return sorted(points,key=lambda x:(x['date'],x['label']))

def generate(project_title,project_meta,tasks,output_path):
    return generate_axis_board(project_title,project_meta,tasks,output_path)

def mainline_bands(tasks,points):
    """Narrative intervals between REAL control dates, not L2 rollup spans."""
    real=[t for t in tasks if not t.get('summary') and t.get('start') and t.get('finish')]
    start=min(t['start'][:10] for t in real);finish=max(t['finish'][:10] for t in real)
    design=next((t['finish'][:10] for t in real if t.get('milestone') and '设计总承包合同签订完成' in t['name']),None)
    gc=next((p['date'] for p in points if p['label']=='总包合同签订'),None)
    site=min((t['start'][:10] for t in real if '场地移交与动工准备' in t['name'] or t.get('activity_type')=='site_setup'),default=None)
    physical=next((p['date'] for p in points if p['label']=='物理竣工'),None)
    move=next((p['date'] for p in points if p['label']=='搬迁开始'),None)
    anchors=[(start,'前期与发包')]
    if design and start<design:anchors.append((design,'设计与施工发包'))
    if gc and gc>anchors[-1][0]:anchors.append((gc,'开工准备'))
    if site and site>anchors[-1][0]:anchors.append((site,'施工阶段'))
    if physical and physical>anchors[-1][0]:anchors.append((physical,'验收与交付'))
    if move and move>anchors[-1][0]:anchors.append((move,'搬迁入驻'))
    anchors.append((finish,None))
    return [(label,a,b) for (a,label),(b,_) in zip(anchors,anchors[1:]) if b>a]

def _fit_centers(desired,width,left,right,gap=.12):
    centers=[]
    for value in desired:centers.append(max(left+width/2,value,centers[-1]+width+gap if centers else left))
    if centers and centers[-1]>right-width/2:
        centers[-1]=right-width/2
        for i in range(len(centers)-2,-1,-1):centers[i]=min(centers[i],centers[i+1]-width-gap)
    return centers

def generate_axis_board(project_title,project_meta,tasks,output_path):
    points=select_points(tasks)
    if not points:raise ValueError('BD_TIMELINE_NO_DATED_POINTS')
    p=(project_meta.get('input_contract') or {}).get('project',{})
    prs=Presentation();prs.slide_width=Inches(13.333);prs.slide_height=Inches(7.5)
    s=prs.slides.add_slide(prs.slide_layouts[6])
    notice='投标规划草案 · 报审适用条件待核实' if p.get('authority_draft') else '投标规划 · 以项目条件及PM签认为准'
    name=p.get('project_name') or str(project_title)
    if name[:3] in {'01_','02_','03_'}:
        from core.industry_base import PROFILES
        name=f"{project_meta.get('city','')} · {PROFILES.get(p.get('sector'),PROFILES['office'])['label']} · {float(project_meta.get('area') or 0):,.0f}㎡"
    chrome(s,'项目整体进度计划｜关键里程碑',name+' · '+notice,1)
    a=dt.date.fromisoformat(points[0]['date']);b=dt.date.fromisoformat(points[-1]['date'])
    axis_start=dt.date(a.year,a.month,1)
    axis_end=dt.date(b.year+(b.month==12),1 if b.month==12 else b.month+1,1)
    span=max(1,(axis_end-axis_start).days);left=.75;right=12.6;axis_y=3.6;band_h=.62
    def xdate(date):return left+(date-axis_start).days/span*(right-left)
    def centered(x,y,w,h,value,size=16,color='space',bold=False):
        box=text(s,x,y,w,h,value,size,color,bold)
        for par in box.text_frame.paragraphs:par.alignment=PP_ALIGN.CENTER
        return box
    # Callouts are plain text above/below a single month ribbon, like a bid board.
    width=1.95
    for upper in [True,False]:
        lane=points[0::2] if upper else points[1::2]
        centers=_fit_centers([xdate(dt.date.fromisoformat(x['date'])) for x in lane],width,left,right)
        for item,center in zip(lane,centers):
            pin=xdate(dt.date.fromisoformat(item['date']));y=1.91 if upper else 5.22
            leader_y=axis_y if upper else axis_y+band_h
            elbow=2.82 if upper else 4.88
            end=y+.69 if upper else y-.07
            for x1,y1,x2,y2 in [(pin,leader_y,pin,elbow),(pin,elbow,center,elbow),(center,elbow,center,end)]:
                ln=s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,Inches(x1),Inches(y1),Inches(x2),Inches(y2))
                ln.line.color.rgb=rgb('888888');ln.line.width=Pt(.7);ln.line.dash_style=MSO_LINE_DASH_STYLE.DASH
            centered(center-width/2,y,width,.35,item['label'],16,'ink',True)
            day=dt.date.fromisoformat(item['date'])
            centered(center-width/2,y+.37,width,.33,f'{day.year}/{day.month}/{day.day}',15,'grey')
    # Arrow intervals are explicitly an anchor-to-anchor BD narrative. They do
    # not replace the overlapping L2 phases available in the PM companion deck.
    colors=['D9D6F0','D9D6F0','C9C9C9','1F1F1F','D9D6F0','D9D6F0']
    bands=mainline_bands(tasks,points)
    for i,(label,start,end) in enumerate(bands):
        x1=xdate(dt.date.fromisoformat(start));x2=xdate(dt.date.fromisoformat(end))
        sh=s.shapes.add_shape(MSO_SHAPE.LEFT_RIGHT_ARROW,Inches(x1+.015),Inches(3.34),Inches(max(.06,x2-x1-.03)),Inches(.16))
        sh.fill.solid();sh.fill.fore_color.rgb=rgb(colors[min(i,len(colors)-1)]);sh.line.fill.background()
        centered((x1+x2)/2-.95,3.00,1.9,.3,label,13,'space',True)
    # Month boundaries follow their actual calendar widths, not equal-day blocks.
    tick=axis_start
    month_count=(axis_end.year-axis_start.year)*12+axis_end.month-axis_start.month
    while tick<=axis_end:
        x=xdate(tick)
        if tick<axis_end:
            nxt=dt.date(tick.year+(tick.month==12),1 if tick.month==12 else tick.month+1,1)
            w=xdate(nxt)-x
            rect(s,x,axis_y,w,band_h,'F7F5FB' if tick.year==a.year else 'F7F5FB')
            line=s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,Inches(x),Inches(axis_y-.025),Inches(x),Inches(axis_y+band_h+.13));line.line.color.rgb=rgb('C9C9C9');line.line.width=Pt(.7)
            centered(x,axis_y+.04,w,.29,str(tick.month)+'月',16 if month_count<=12 else 13,'space',True)
            centered(x,axis_y+.34,w,.22,str(tick.year),10,'grey')
        tick=dt.date(tick.year+(tick.month==12),1 if tick.month==12 else tick.month+1,1)
    assumptions='；'.join(x for x in [str(project_meta.get('city') or ''),str(project_meta.get('area') or '')+'㎡' if project_meta.get('area') else '',p.get('calendar_profile','')+'现场日历' if p.get('calendar_profile') else ''] if x)
    text(s,.75,6.45,11.9,.55,'阶段箭头为控制点之间的主线汇报区间；设计、采购与报审可并行，详见PM阶段版。\n'+assumptions+'。范围、供货及放行前提见《本次排程条件》。',11,'grey')
    target=p.get('target_date')
    if target:text(s,8.4,1.50,4.2,.25,'客户目标（搬迁开始） '+target,12,'space',True)
    out=Path(output_path);out.parent.mkdir(parents=True,exist_ok=True);prs.save(out);return str(out)
