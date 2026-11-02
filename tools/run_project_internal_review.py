from __future__ import annotations
import argparse, copy, hashlib, json, math, re, shutil, sys, zipfile
from datetime import date, timedelta
from pathlib import Path

def _detect_paths():
    here = Path(__file__).resolve().parent
    if here.name == 'tools' and (here.parent / 'core' / 'solver_engine.py').exists():
        engine = here.parent
        workspace = engine.parent
        package = engine / 'output_mpp/Hangzhou_520sqm_Internal_Review'
    elif (here / 'inputs').exists():
        package = here
        workspace = here.parents[1]
        engine = workspace / 'outputs/AI_Lead_PM_Cursor_Spec_Correction/engine/office-programme'
        if not (engine / 'core' / 'solver_engine.py').exists():
            engine = package / 'engine'
    else:
        workspace = here.parents[1] if here.name != 'tools' else here.parents[3]
        engine = workspace / 'outputs/AI_Lead_PM_Cursor_Spec_Correction/engine/office-programme'
        package = workspace / 'outputs/Hangzhou_520sqm_Final_Review_Package'
    source = workspace / 'outputs/AI_Lead_PM_Skill_Trial_Ready/office-programme/output_mpp/run_20260916_065943/audit/Output_Schedule_schedule.json'
    bundled_source = engine / 'projects/hangzhou_520sqm/reference_540_schedule.json'
    if bundled_source.exists():
        source = bundled_source
    return workspace, package, engine, source

WORKSPACE, PACKAGE, ENGINE, SOURCE = _detect_paths()

def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def write(p, data):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def D(s): return date.fromisoformat(s)

def prepare():
    PACKAGE.mkdir(parents=True, exist_ok=True)
    for folder in ('core','config','exporters'):
        shutil.copytree(ENGINE/folder, PACKAGE/'engine'/folder, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (PACKAGE/'inputs').mkdir(exist_ok=True)
    shutil.copy2(SOURCE,PACKAGE/'inputs/reference_540_schedule.json')
    shutil.copy2(ENGINE/'config/holidays.json',PACKAGE/'inputs/reference_holidays.json')
    plan = dict(project_name='杭州520㎡ D&B办公室项目主进度计划', area_sqm=520,
        delivery='DB', bidding='invite', floors=1, cost=None,
        project_start='2026-09-30', target='2027-04-30',
        source_status='用户已确认项目条件；启动日及以下工期为PM规划假设',
        source_schedule_sha256=sha(SOURCE),
        spring_shutdown=['2027-02-06','2027-02-12'],
        durations={'101':10,'40':6,'41':2,'82':3,'83':4,'84':5,'86':10,'92':3,'102':3},
        testing_calendar='SITE_7D',
        assumptions=[
          ['A01','启动日','2026-09-30','沿用草案的规划锚点，非实际已启动记录','启动延后将影响后续计划'],
          ['A02','局部拆除与清运','6+2现场日','比例未知；PM规划范围拆除4–10日、清运1–3日','高值情景单独正算'],
          ['A03','简易报备','10办公日','沿用草案PM估计5–15日；暂按与施工图并行','路径确认后替换；不表示主管部门承诺'],
          ['A04','现场停工日历','每周7个现场日；春节2/6–2/12停工','一现场日代表一个允许夜间窗口；不表达8h实际产能','本包日历是规划假设，不是官方放假或物业许可证明'],
          ['A05','实体施工与调试','沿用L0工序时长，T&C 8现场日','旧540网络作为520规划参考，未重新声称数量标定','94历日是计算包络，不等于已验证的保守缓冲'],
          ['A06','家具生产交货','35工厂工作日（六日周）','继承ASSUMED交期及工厂周日休息日历','不是35历日，不是供应商承诺'],
          ['A07','家具安装','4现场日：安装3+缓冲1','105件面积估算；团队合计50件/窗口×0.70，非每人速率','数量和供应商计划改变时重新估计，不局限于4或8日'],
          ['A08','IAQ首次报告','3连续规划日','家具前检测作为内部计划假设，非已裁定法规','采样、出报告周末可执行尚未核实'],
          ['A09','通风与复测','5+3连续规划日','沿用草案；复测周末可执行为显式规划假设','检测改为办公日的情景另算；不保证检测一次合格'],
          ['A10','消防/备案手续规划包','10办公日','将旧86–91聚合为管理工作包；具体法定路径未裁定','联合验收若另需办理，新增5办公日仅作测试情景'],
          ['A11','业主/物业竣工验收','3办公日','沿用草案2–5日；硬装完成后开展，尾项须在移交前释放','不表示实际验收已通过'],
          ['A12','精保洁与移交','保洁7现场日；移交等四门','保洁跟家具；移交等保洁、IAQ、手续、业主验收','检测与保洁并行须满足检测条件；重检风险未预置为已发生'],
          ['A13','办公与工厂假期','源配置规划日历快照','所有节假日为计划计算口径，不作法规核验结论','物业、客户及供应商日历确认后替换']])
    write(PACKAGE/'inputs/project_plan.json',plan)
    protected = [SOURCE]+list((ENGINE/'output_mpp/PENDING08_replay').rglob('*_schedule.json'))+list((ENGINE/'config').glob('*.json'))
    write(PACKAGE/'inputs/protected_hashes.json',[{'path':str(p),'sha256':sha(p)} for p in protected])
    write(PACKAGE/'inputs/solver_provenance.json',{'core/solver_engine.py':sha(ENGINE/'core/solver_engine.py'),'core/holidays.py':sha(ENGINE/'core/holidays.py')})
    shutil.copy2(__file__,PACKAGE/'run_review.py')

def clean(s):
    return re.sub(r'\s+',' ',str(s).replace('[M]','').replace('⭐️','').replace('⭐','')).strip()

def calendar_for(t):
    ac=t.get('activity_calendar') or {}
    mode=ac.get('calendar_mode','5D')
    return {'7D':'现场日','6D':'工厂工作日','5D':'办公日'}[mode]

def setcal(t, mode, cls):
    t['activity_calendar']={'calendar_mode':mode,'working_days_per_week':int(mode[0]),'activity_class':cls,'calendar_id':{'7D':'SITE_7D','6D':'FACTORY_6D','5D':'BUSINESS_5D'}[mode], 'basis':'PM_INTERNAL_PLANNING_ASSUMPTION'}
    t['work_weekend']=mode!='5D'
    t['ignore_holidays']=False

def build_tasks(plan):
    src=read(PACKAGE/'inputs/reference_540_schedule.json')['tasks']
    tasks=[]; removed={87,88,90,91}
    for t0 in src:
        i=t0['id']
        if i in removed: continue
        # Only explicit input fields survive. Never carry old dates, float or approval flags.
        t={k:copy.deepcopy(t0[k]) for k in ('id','name','outline_level','predecessors','activity_calendar') if k in t0}
        t['duration_days']=int(t0.get('duration_days') or 0)
        t['name']=clean(t['name']);t['basis']='继承网络工期作为PM规划参考；未视为实测承诺'
        if i==1:t['name']=plan['project_name']
        if i==4:t['name']='物业装修指引、现状图纸获取与进场要求确认'
        if i==10:t['name']='发布D&B邀请招标文件及设计任务书'
        if 39<=i<=79:setcal(t,'7D','SITE_CONSTRUCTION')
        if i==35:t['basis']='ASSUMED 35工厂工作日；周日休息；非35历日'
        if str(i) in plan['durations']:
            t['duration_days']=plan['durations'][str(i)];t['basis']='项目PM规划假设，详见假设表'
        if i in (82,92):setcal(t,'7D' if plan['testing_calendar']=='SITE_7D' else '5D','TESTING_COMMISSIONING')
        if i==84:setcal(t,'7D','SITE_INSTALLATION')
        if i==82:t['name']='首次室内空气检测与报告（家具前规划门）'
        if i==84:t['name']='家具安装后通风散味（规划周期）'
        if i==86:
            t['name']='消防检测与竣工手续办理规划包（具体路径待核）';setcal(t,'5D','AUTHORITY_APPROVAL');t['predecessors']='80FS'
        if i==89:t['name']='竣工手续放行（规划里程碑）';t['predecessors']='86FS'
        if i==37:t['predecessors']='31FS,32FS,101FS'
        if i==95:t['predecessors']='83FS';t['name']='家具安装后精保洁与移交准备'
        if i==96:t['predecessors']='95FS,93FS,89FS,102FS'
        t['duration']=t['duration_days'];t['level']=t['outline_level'];t['client_task_name']=t['name']
        tasks.append(t)
        if i==32:
            tasks.append(dict(id=100,name='Phase 4 开工手续与简易报备',outline_level=2,level=2,duration_days=0,duration=0,predecessors='',basis='管理工作包补全'))
            f=dict(id=101,name='简易施工报备（路径与周期待核）',outline_level=3,level=3,duration_days=plan['durations']['101'],duration=plan['durations']['101'],predecessors='28FS',basis='PM规划10办公日，与施工图并行，非受理承诺')
            setcal(f,'5D','AUTHORITY_APPROVAL');tasks.append(f)
        if i==89:
            own=dict(id=102,name='业主及物业竣工验收（规划）',outline_level=3,level=3,duration_days=plan['durations']['102'],duration=plan['durations']['102'],predecessors='80FS',basis='PM规划3办公日；以尾项释放为移交条件')
            setcal(own,'5D','CLIENT_APPROVAL');tasks.append(own)
    for j,t in enumerate(tasks):
        t['is_summary']=j+1<len(tasks) and tasks[j+1]['outline_level']>t['outline_level']
        t['is_milestone']=not t['is_summary'] and t['duration_days']==0
        t['calendar_label']=calendar_for(t) if not t['is_summary'] else '汇总跨度'
        if t['id'] in (82,92):t['calendar_label']='连续规划日' if plan['testing_calendar']=='SITE_7D' else '办公日'
        t['client_task_name']=t['name']
    return tasks

def effective_holidays(plan):
    hs=read(PACKAGE/'inputs/reference_holidays.json')
    for h in hs:
        if '春节' in h['name'] and h['start'].startswith('2027'):
            h.update(name='春节项目规划停工（未作官方日历认定）',start=plan['spring_shutdown'][0],finish=plan['spring_shutdown'][1])
    return hs

def solve(plan):
    holidays=effective_holidays(plan)
    snap=PACKAGE/'engine'/'config'/'holidays.json'
    live=ENGINE/'config'/'holidays.json'
    if snap.resolve()!=live.resolve():
        write(snap,holidays)
    snap_root=str(PACKAGE/'engine')
    if sys.path[0]!=snap_root:
        sys.path.insert(0,snap_root)
    for key in [k for k in sys.modules if k=='core' or k.startswith('core.')]:
        del sys.modules[key]
    from core.solver_engine import solve_schedule
    return solve_schedule(build_tasks(plan),plan['project_start'],custom_holidays=holidays,construction_calendar_mode='7D')

def check_schedule(result,plan):
    from core.solver_engine import parse_predecessor_token
    tasks=result['tasks']; by={t['id']:t for t in tasks}; hs=effective_holidays(plan)
    alloff=set();spring=set()
    for h in hs:
        dd=D(h['start'])
        while dd<=D(h['finish']):
            alloff.add(dd)
            if '春节' in h['name']:spring.add(dd)
            dd+=timedelta(days=1)
    def working(dd,t):
        mode=(t.get('activity_calendar') or {}).get('calendar_mode','5D')
        return dd not in (alloff if mode=='5D' else spring) and (dd.weekday()<5 if mode=='5D' else dd.weekday()!=6 if mode=='6D' else True)
    def nextwork(dd,t):
        while not working(dd,t):dd+=timedelta(days=1)
        return dd
    def lagdate(dd,lag,t):
        for _ in range(abs(lag)):
            dd+=timedelta(days=1 if lag>0 else -1)
            while not working(dd,t):dd+=timedelta(days=1 if lag>0 else -1)
        return dd
    findings=[]; edge_count=0; seen=set()
    for t in tasks:
        if not t.get('start') or not t.get('finish'):findings.append(f"{t['id']}:日期缺失")
        if t['is_summary']:seen.add(t['id']);continue
        ds,df=D(t['start']),D(t['finish']);n=t['duration_days']
        if df<ds:findings.append(f"{t['id']}:日期倒置")
        if n:
            cnt=sum(working(ds+timedelta(days=k),t) for k in range((df-ds).days+1))
            if cnt!=n:findings.append(f"{t['id']}:工期与日历不一致 {cnt}!={n}")
            if not working(ds,t) or not working(df,t):findings.append(f"{t['id']}:起止落在非工作日")
        for part in t['predecessors'].split(','):
            if not part:continue
            parsed=parse_predecessor_token(part);edge_count+=1
            if not parsed:findings.append(f"{t['id']}:无效依赖 {part}");continue
            pid,rel,lag=parsed
            if pid not in seen or pid not in by or by[pid]['is_summary']:
                findings.append(f"{t['id']}:前置缺失/后向引用/汇总依赖 {part}");continue
            p=by[pid];a=D(p['start'] if rel=='SS' else p['finish'])
            endboundary=p.get('start_boundary' if rel=='SS' else 'finish_boundary')=='end_of_day'
            if n==0:
                bound=lagdate(a,lag,t)
            else:
                if rel in ('FS','SS') and endboundary:a+=timedelta(days=1)
                bound=lagdate(nextwork(a,t),lag,t)
            actual=df if rel=='FF' else ds
            if actual<bound:findings.append(f"{t['id']}:依赖不满足 {part}")
        seen.add(t['id'])
    if len(by)!=len(tasks):findings.append('重复任务ID')
    if set(int(re.match(r'\d+',p)[0]) for p in by[96]['predecessors'].split(','))!={95,93,89,102}:findings.append('移交四门缺失')
    if by[95]['predecessors']!='83FS':findings.append('保洁仍错误等待手续')
    for i in (48,57,75):
        if i not in by:findings.append('隐蔽/调试门丢失')
    coverage={t['id']:('保留' if t['id'] in by else '合并至86竣工手续规划包，非判定不适用') for t in read(PACKAGE/'inputs/reference_540_schedule.json')['tasks']}
    assert all(i in by or i in {87,88,90,91} for i in coverage)
    return dict(status='PASS' if not findings else 'FAIL',task_count=len(tasks),leaf_count=sum(not t['is_summary'] for t in tasks),dependency_count=edge_count,errors=findings,
        checks=['任务ID与拓扑顺序','叶任务工期与自身日历独立计数','FS/SS/FF及滞后独立校验','隐蔽与调试检查门保留','移交四门齐备','保洁施工顺序','源节点覆盖映射'],source_coverage=coverage)

GROUPS=[
 ('启动与项目任务书',[3,4,5,6]),('邀请招标准备与发标',[8,9,10]),('投标、评审与商务澄清',list(range(11,19))),('定标与D&B合同签订',[19,20,21,22]),
 ('方案深化与设计确认',[24,25,26,27,28]),('全专业施工图深化',[29]),('施工图审查与确认',[30,31]),('物业送审与安全协议',[32]),('简易施工报备',[101]),
 ('家具供应商定标',[34]),('家具生产交货',[35]),('场地移交与允许动工',[37]),('场地保护与临设',[39]),('局部拆除与清运',[40,41]),('测量放线',[42]),
 ('隐蔽工程与墙内验收',[44,45,46,47,48,64,67]),('封板、二次机电与天花验收',list(range(50,58))),('饰面、地坪及门木收口',[59,60,61,62,63,65,66,68,69,70]),
 ('机电末端与调试前检查',[71,72,73,74,75]),('全系统联动调试',[76]),('整改、自检与粗保洁',[77,78,79]),('物理竣工／硬装完成',[80]),
 ('首次空气检测与报告',[82]),('办公家具与软装安装',[83]),('通风散味与复测放行',[84,92,93]),('消防/备案手续规划包',[86,89]),('业主／物业竣工验收',[102]),
 ('精保洁与移交准备',[95]),('正式移交（四门齐）',[96]),('搬迁入驻与完成',[97,98])]

def master_rows(tasks):
    by={t['id']:t for t in tasks}; rows=[]
    for k,(name,ids) in enumerate(GROUPS,1):
        members=[by[i] for i in ids]
        s=min(t['start'] for t in members);f=max(t['finish'] for t in members)
        rows.append(dict(id=f'M{k:02}',name=name,start=s,finish=f,duration_days=(D(f)-D(s)).days+1 if any(t['duration_days'] for t in members) else 0,
            calendar_label='历日包络',is_summary=False,is_milestone=not any(t['duration_days'] for t in members),outline_level=3,
            predecessors=' / '.join(f"{t['id']}←{t['predecessors']}" for t in members if t['predecessors']),basis='覆盖明细ID '+','.join(map(str,ids)),ids=ids))
    return rows

def exports(result,plan,validation,scenarios):
    from openpyxl import Workbook,load_workbook
    from openpyxl.styles import Font,PatternFill,Alignment,Border,Side
    from openpyxl.utils import get_column_letter
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib import colors
    tasks=result['tasks'];masters=master_rows(tasks);by={t['id']:t for t in tasks}
    margin=(D(plan['target'])-D(by[98]['finish'])).days
    wb=Workbook();wb.remove(wb.active)
    blue='142A43';teal='087E8B';orange='C97024'
    sheets={}
    for title,rows in [('管理层主表',masters),('完整节点表',tasks)]:
        ws=wb.create_sheet(title);sheets[title]=ws
        ws.append([plan['project_name']+'｜内部审阅版']);ws.merge_cells('A1:J1')
        ws.append(['规划搬迁',by[98]['finish'],'目标',plan['target'],'目标日期余量（历日）',margin,'不是CPM总浮时'])
        ws.append(['ID','任务／工作包','工期','单位','开始','完成','前置／映射','依据','PM意见','处理状态'])
        for t in rows:
            ws.append([t['id'],t['name'],t['duration_days'] if not t['is_summary'] else None,t['calendar_label'],D(t['start']),D(t['finish']),t['predecessors'],t['basis'],'','待审阅'])
            ws.cell(ws.max_row,5).number_format=ws.cell(ws.max_row,6).number_format='yyyy-mm-dd'
        ws.freeze_panes='C4';ws.auto_filter.ref=f'A3:J{ws.max_row}'
        widths=[10,48,10,14,14,14,40,57,40,14]
        for i,w in enumerate(widths,1):ws.column_dimensions[get_column_letter(i)].width=w
        ws.print_title_rows='1:3';ws.sheet_properties.pageSetUpPr.fitToPage=True;ws.page_setup.orientation='landscape';ws.page_setup.paperSize=ws.PAPERSIZE_A3;ws.page_setup.fitToWidth=1;ws.page_setup.fitToHeight=0
    ws=wb.create_sheet('全节点甘特图');ws.append([plan['project_name']+'｜全节点周视图']);ws.append(['ID','任务','工期','开始','完成'])
    lo=D(plan['project_start']);hi=max(D(t['finish']) for t in tasks);weeks=[];dd=lo-timedelta(days=lo.weekday())
    while dd<=hi+timedelta(days=7):weeks.append(dd);dd+=timedelta(days=7)
    for i,w in enumerate(weeks,6):ws.cell(2,i,w).number_format='mm-dd';ws.column_dimensions[get_column_letter(i)].width=4.5
    for row,t in enumerate(tasks,3):
        for col,v in enumerate([t['id'],t['name'],t['duration_days'] if not t['is_summary'] else '',D(t['start']),D(t['finish'])],1):ws.cell(row,col,v)
        ws.cell(row,4).number_format=ws.cell(row,5).number_format='yyyy-mm-dd'
        for col,w in enumerate(weeks,6):
            if D(t['start'])<=w+timedelta(days=6) and D(t['finish'])>=w:
                ws.cell(row,col,'◆' if t['is_milestone'] else '').fill=PatternFill('solid',fgColor=orange if t['is_milestone'] else blue if t['is_summary'] else teal)
                ws.cell(row,col).font=Font(color='FFFFFF')
    for c,w in [('A',8),('B',50),('C',9),('D',14),('E',14)]:ws.column_dimensions[c].width=w
    ws.freeze_panes='F3';ws.auto_filter.ref=f'A2:E{ws.max_row}'
    ws=wb.create_sheet('假设与风险');ws.append(['编号','项目','规划值','来源与含义','风险／替换条件'])
    for a in plan['assumptions']:ws.append(a)
    for i,w in enumerate([10,28,45,78,72],1):ws.column_dimensions[get_column_letter(i)].width=w
    ws=wb.create_sheet('敏感性正算');ws.append(['情景','硬装完成','移交','搬迁','目标日期余量（历日）'])
    for s in scenarios:ws.append([s['name'],s['physical_finish'],s['handover'],s['move'],s['margin_days']])
    for col in 'ABCDE':ws.column_dimensions[col].width=38
    ws=wb.create_sheet('验证结果');ws.append(['检查','结果']);ws.append(['整网独立验证',validation['status']]);ws.append(['节点数',len(tasks)]);ws.append(['依赖数',validation['dependency_count']]);ws.append(['客户批准','未批准；本包为可重复生成的内部审阅计划']);ws.append(['工期合理性','需PM结合实际范围审阅，测试不证明工程估计准确'])
    ws.column_dimensions['A'].width=30;ws.column_dimensions['B'].width=90
    for ws in wb:
        for row in ws:
            for cell in row:
                cell.alignment=Alignment(vertical='center',wrap_text=True)
                if cell.fill.patternType is None:cell.font=Font(name='Microsoft YaHei',size=10,color=blue)
        headers=[1,3] if ws.title in sheets else [1,2] if ws.title=='全节点甘特图' else [1]
        for rr in headers:
            for cell in ws[rr]:cell.fill=PatternFill('solid',fgColor=blue);cell.font=Font(name='Microsoft YaHei',size=10,bold=True,color='FFFFFF')
        for rr in range(1,ws.max_row+1):ws.row_dimensions[rr].height=35 if ws.title!='全节点甘特图' else 25
    xlsx=PACKAGE/'杭州520㎡_完整主进度计划.xlsx';wb.save(xlsx)
    chk=load_workbook(xlsx,data_only=True); sheet=chk['完整节点表']
    assert sheet.max_row-3==len(tasks)
    for rr,t in enumerate(tasks,4):assert sheet.cell(rr,5).value.date().isoformat()==t['start'] and sheet.cell(rr,6).value.date().isoformat()==t['finish']
    chk.close()
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    W,H=1191,842;c=canvas.Canvas(str(PACKAGE/'杭州520㎡_完整主进度计划.pdf'),pagesize=(W,H));c.setTitle(plan['project_name']);page=0
    def text(x,y,s,size=9,color=blue):
        c.setFillColor(colors.HexColor('#'+color));c.setFont('STSong-Light',size);c.drawString(x,y,str(s))
    def header(sub):
        nonlocal page
        page+=1;c.setFillColor(colors.HexColor('#'+blue));c.rect(0,H-102,W,102,fill=1,stroke=0)
        text(30,H-40,plan['project_name'],23,'FFFFFF');text(30,H-69,sub,12,'DBE8F1')
        text(30,22,f'内部审阅｜依据显式PM规划假设｜2027-04-30目标｜第{page}页',9)
    def gantt(rows,title):
        header(title);left=30;y=H-134;rh=20 if len(rows)>26 else 23
        chartx=660;chartw=W-chartx-30;axis0=D(plan['project_start']);axis1=max(hi,D(plan['target']))+timedelta(days=7);span=(axis1-axis0).days+1
        xp=lambda ds:chartx+(D(ds)-axis0).days/span*chartw
        text(30,y,'ID',9);text(65,y,'工作包 / 活动',9);text(374,y,'工期 / 单位',9);text(465,y,'开始',9);text(552,y,'完成',9)
        dd=axis0.replace(day=1)
        while dd<=axis1:
            if dd>=axis0:
                x=xp(dd.isoformat());c.setStrokeColor(colors.HexColor('#DDE5EB'));c.line(x,75,x,y-7);text(x+2,y,dd.strftime('%Y-%m'),8)
            dd=(dd.replace(day=28)+timedelta(days=4)).replace(day=1)
        tx=xp(plan['target']);c.setStrokeColor(colors.HexColor('#B94A35'));c.setDash(3,3);c.line(tx,75,tx,y-7);c.setDash()
        for k,t in enumerate(rows):
            yy=y-15-(k+1)*rh
            if k%2==0:c.setFillColor(colors.HexColor('#F1F5F8'));c.rect(25,yy-5,1140,rh,fill=1,stroke=0)
            text(30,yy,t['id'],8);name=t['name']
            while pdfmetrics.stringWidth(name,'STSong-Light',8.5)>294:name=name[:-2]+'…'
            text(65,yy,name,8.5)
            duration='汇总' if t['is_summary'] else '里程碑' if t['is_milestone'] else f"{t['duration_days']} {t['calendar_label']}"
            text(374,yy,duration,8);text(465,yy,t['start'],9);text(552,yy,t['finish'],9)
            x=xp(t['start']);x2=xp(t['finish'])+chartw/span
            c.setFillColor(colors.HexColor('#'+(orange if t['is_milestone'] else blue if t['is_summary'] else teal)))
            if t['is_milestone']:
                p=c.beginPath();p.moveTo(x,yy+8);p.lineTo(x+4,yy+3);p.lineTo(x,yy-2);p.lineTo(x-4,yy+3);p.close();c.drawPath(p,fill=1,stroke=0)
            else:c.roundRect(x,yy,max(x2-x,2),7,2,fill=1,stroke=0)
        text(30,48,'图示：蓝绿色＝计划活动；深蓝＝汇总；橙色＝规划里程碑；红虚线＝搬迁目标。主表工期为包络，明细为各自日历工期。',9)
        c.showPage()
    gantt(masters,'管理层全周期总览｜启动—招标—设计—报备—采购—施工—验收—搬迁')
    for k in range(0,len(tasks),29):gantt(tasks[k:k+29],f'完整节点甘特图｜节点 {k+1}–{min(k+29,len(tasks))} / {len(tasks)}')
    header('主要假设、交付风险与敏感性正算');yy=H-135
    for a in plan['assumptions']:
        text(30,yy,f'{a[0]}  {a[1]}：{a[2]}',11);text(50,yy-18,a[3]+'；'+a[4],9);yy-=43
    c.showPage();header('最终验证与交付解释');yy=H-135
    lines=[f"计算完成：{len(tasks)}个节点、{validation['leaf_count']}个叶任务、{validation['dependency_count']}条依赖；独立检查 {validation['status']}。",
        f"主情景：硬装{by[80]['finish']}；移交{by[96]['finish']}；搬迁{by[98]['finish']}；目标日期余量{margin}历日。",
        '日期余量不是CPM总浮时。图中未宣称全网总浮时或正式关键路径已经由MS Project复核。',
        '94历日仅是当前规划包络，不等于已证明偏保守。现场估计与手续周期仍需PM审阅。',
        '家具35日使用工厂六日周；安装4现场日基于105件PM估计；旧110件是总量，不是团队夜产能。',
        'IAQ检测暂按连续日出报告，包含周末假设；不能把它表述为检测机构或物业已承诺。',
        '联合验收适用性仍待核；如另需办理，使用额外手续情景评估，不能把未设独立活动理解为免办。',
        '主表、明细Excel、PDF、计划JSON来自同一份求解结果。保护文件哈希及导出逐节点对照见验证报告。']
    for line in lines:text(30,yy,line,11);yy-=30
    yy-=15;text(30,yy,'敏感性：每个情景均独立正算，不把各情景影响简单相加。',12);yy-=30
    for s in scenarios:
        text(30,yy,f"{s['name']}：硬装 {s['physical_finish']}；移交 {s['handover']}；搬迁 {s['move']}；目标余量 {s['margin_days']} 历日",10);yy-=29
    c.showPage();c.save()
    return {'xlsx':xlsx.name,'pdf':'杭州520㎡_完整主进度计划.pdf','pdf_pages':page,'xlsx_date_parity':'PASS'}

def write_zip(folder):
    zpath=folder.parent/(folder.name+'.zip')
    with zipfile.ZipFile(zpath,'w',zipfile.ZIP_DEFLATED) as zf:
        for p in folder.rglob('*'):
            if p.is_dir() or '__pycache__' in p.parts or p.suffix=='.pyc':
                continue
            zf.write(p,p.relative_to(folder.parent))
    return zpath

def main(argv=None):
    global PACKAGE,ENGINE,SOURCE,WORKSPACE
    ap=argparse.ArgumentParser(description='Project-level internal PM review overlay. Does not change engine defaults.')
    ap.add_argument('--prepare',action='store_true')
    ap.add_argument('--mpp',action='store_true')
    ap.add_argument('--plan',default=None,help='project_plan.json; Hangzhou 520 values live here, not in solver defaults')
    ap.add_argument('--output',default=None,help='review package directory')
    ap.add_argument('--engine',default=None,help='live engine root used only as copy source')
    ap.add_argument('--source-schedule',default=None)
    args=ap.parse_args(argv)
    if args.output: PACKAGE=Path(args.output).resolve()
    if args.engine: ENGINE=Path(args.engine).resolve()
    if args.source_schedule: SOURCE=Path(args.source_schedule).resolve()
    PACKAGE.mkdir(parents=True,exist_ok=True)
    if args.prepare or not (PACKAGE/'engine'/'core'/'solver_engine.py').exists():
        prepare()
    sys.path.insert(0,str(PACKAGE/'engine'))
    plan_path=Path(args.plan) if args.plan else PACKAGE/'inputs'/'project_plan.json'
    if args.plan:
        shutil.copy2(plan_path,PACKAGE/'inputs'/'project_plan.json')
    plan=read(PACKAGE/'inputs/project_plan.json');result=solve(plan);v=check_schedule(result,plan)
    if v['errors']:raise RuntimeError(v['errors'])
    repeat=solve(plan);assert result==repeat,'Non-deterministic solve'
    write(PACKAGE/'schedule.json',dict(result,project_name=plan['project_name'],status='INTERNAL_PM_REVIEW',applied=False,planning_inputs='inputs/project_plan.json'))
    scenario_defs=[('当前规划',{}),('检测改为办公日',{'testing_calendar':'BUSINESS_5D'}),('家具安装8现场日',{'durations':{'83':8}}),('拆除及清运高值',{'durations':{'40':10,'41':3}}),('手续办理20办公日',{'durations':{'86':20}}),('春节停工延长至2月20日',{'spring_shutdown':['2027-02-06','2027-02-20']}),('另需联合验收：手续包增加5办公日',{'durations':{'86':15}})]
    scenarios=[]
    for name,changes in scenario_defs:
        pp=copy.deepcopy(plan)
        for key,value in changes.items():
            if key=='durations':pp[key].update(value)
            else:pp[key]=value
        sr=solve(pp);sv=check_schedule(sr,pp);assert not sv['errors'],sv
        sb={t['id']:t for t in sr['tasks']};scenarios.append(dict(name=name,physical_finish=sb[80]['finish'],handover=sb[96]['finish'],move=sb[98]['finish'],margin_days=(D(pp['target'])-D(sb[98]['finish'])).days))
        write(PACKAGE/'audit'/('scenario_'+str(len(scenarios))+'.json'),{'input_changes':changes,'validation':sv,'schedule':sr})
    write(PACKAGE/'engine/config/holidays.json',effective_holidays(plan))
    write(PACKAGE/'sensitivity.json',scenarios)
    before=read(PACKAGE/'inputs/protected_hashes.json');checks=[]
    for p in before:
        path=Path(p['path'])
        checks.append(dict(path=str(path),status='PASS' if path.exists() and sha(path)==p['sha256'] else 'UNAVAILABLE' if not path.exists() else 'FAIL'))
    assert all(x['status']!='FAIL' for x in checks),'Protected source mutated'
    v.update(repeatability='PASS',protected_files=checks,scenarios_recalculated=len(scenarios),engine_solver_sha256=sha(PACKAGE/'engine/core/solver_engine.py'))
    v['exports']=exports(result,plan,v,scenarios)
    from exporters.export_pptx import generate_pptx_milestones
    pptx_name='杭州520㎡_管理层里程碑.pptx'
    generate_pptx_milestones(
        plan['project_name']+'（内部PM规划假设版）',
        {'city':'杭州','area':plan['area_sqm'],'delivery':plan['delivery'],'bidding':plan['bidding'],'cost':'UNKNOWN'},
        result['tasks'],str(PACKAGE/pptx_name))
    v['exports']['pptx']=pptx_name
    v['native_mpp']={'status':'NOT_ATTEMPTED'}
    attempt_file=PACKAGE/'native/attempt.json'
    if attempt_file.exists():
        previous=read(attempt_file)
        if previous.get('schedule_sha256')==sha(PACKAGE/'schedule.json'):
            v['native_mpp']=previous['result']
    if args.mpp:
        try:
            from exporters.mpp_renderer import build_mpp
            path,parity=build_mpp(plan['project_name']+'（内部PM规划）',D(plan['project_start']),result['tasks'],effective_holidays(plan),str(PACKAGE/'native/杭州520㎡_内部主进度.mpp'),construction_calendar_mode='7D')
            v['native_mpp']={'status':'PASS','path':path,'date_parity':parity}
        except Exception as exc:v['native_mpp']={'status':'NOT_DELIVERED','reason':str(exc)}
        write(attempt_file,{'schedule_sha256':sha(PACKAGE/'schedule.json'),'result':v['native_mpp']})
    write(PACKAGE/'validation.json',v)
    live_holidays=ENGINE/'config'/'holidays.json'
    write(PACKAGE/'THIS_RUN.json',{
        'command':'python -X utf8 run_review.py --prepare',
        'engine_entry':'python -X utf8 main.py --internal_review_plan projects/hangzhou_520sqm/project_plan.json --internal_review_output <dir>',
        'solver_engine_sha256':sha(PACKAGE/'engine/core/solver_engine.py'),
        'schedule_sha256':sha(PACKAGE/'schedule.json'),
        'live_holidays_sha256':sha(live_holidays) if live_holidays.exists() else None,
        'native_mpp':v['native_mpp'],
        'task_count':len(result['tasks']),
        'status':'INTERNAL_PM_REVIEW','applied':False,
    })
    zpath=write_zip(PACKAGE)
    write(PACKAGE/'THIS_RUN.json',{**read(PACKAGE/'THIS_RUN.json'),'zip':str(zpath)})
    by={t['id']:t for t in result['tasks']}
    md=['# 杭州520㎡完整主进度计划｜最终内部审阅包','',f"主情景：硬装 **{by[80]['finish']}**；移交 **{by[96]['finish']}**；搬迁 **{by[98]['finish']}**。",'',
        '本包完成内部计划生成与一致性验证，不替代客户批准或现场事实确认。先打开PDF看全周期，再打开Excel逐节点写意见。',
        '',f"完整节点 {v['task_count']}；叶任务 {v['leaf_count']}；依赖 {v['dependency_count']}；独立校验 **{v['status']}**。",'',
        '## 文件','', '- 杭州520㎡_完整主进度计划.pdf：管理层总览、全部节点甘特图、假设与验证。','- 杭州520㎡_完整主进度计划.xlsx：完整节点、周甘特、PM意见栏、假设与敏感性。','- schedule.json：唯一日期数据源；sensitivity.json：七个实际正算情景。','- validation.json：检查结果；audit/：逐情景求解证据。','- CURSOR_CLOSEOUT_TASK.md：交给Cursor的一次性收尾任务。','',
        '## 一键重现','', '在本目录执行：`python -X utf8 run_review.py`。需要 openpyxl、reportlab；核心求解代码随包快照。可加 `--mpp` 通过原生导出器尝试 Project；原生日期不一致时不得交付MPP。', '',
        '## 计算边界','', '这是使用现有求解器的项目级规划覆盖，不是修改正式默认引擎。没有另写夜班小时求解器。现场日仅作PM管理计划单位，非8小时实际施工承诺。',
        '手续规划包合并旧86–91以便管理展示，未裁定法定适用性；额外联合验收情景已列出。',
        f"原生MPP状态：{v['native_mpp']['status']}。详情见validation.json；没有PASS文件就不称已交付MPP。",'',
        '主表的日期余量与工程估计可信度分开；完整生成通过不等于估计准确或商业发布批准。']
    (PACKAGE/'README.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    print(json.dumps({'package':str(PACKAGE),'tasks':len(result['tasks']),'validation':v['status'],'scenarios':scenarios,'mpp':v['native_mpp']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
