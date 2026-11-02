import argparse,json,hashlib,sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv):
    p=argparse.ArgumentParser();p.add_argument('--engine_project',required=True);p.add_argument('--engine_output',required=True)
    p.add_argument('--engine_mpp',action='store_true')
    args=p.parse_args(argv)
    from core.workflow_engine import compile_project
    source=Path(args.engine_project);project=json.loads(source.read_text(encoding='utf-8-sig'))
    result=compile_project(project)
    out=Path(args.engine_output);out.mkdir(parents=True,exist_ok=True)
    result['input_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    (out/'engine_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    tasks=result['schedule']['tasks']
    from exporters import export_pdf,generate_pptx_milestones
    level_label = '【L2 管理级综合总控计划】' if result.get('plan_level') == 'L2_MACRO_CONTROL' else '【L3 施工级工序进度计划】'
    nature_label = '【演示情景·非承诺】' if result.get('scenario_nature') == 'ILLUSTRATIVE_SCENARIO' else '【已核实工程事实】'
    suffix = '（程序逻辑测试·工期不可用于项目）' if result['planning_purpose'] == 'logic_test' else f'{level_label}{nature_label}'
    if project.get('clean_title'):
        title = project['clean_title']
    else:
        title = project.get('title', project['project_id']) + '｜' + suffix
        if result['planning_purpose'] == 'planning_review':
            title += '｜未建模重叠'
            if any(e.get('status') == 'ACCEPTED_WITH_DEVIATION' for e in result['experience_floor_assessments']):
                title += '·含工期偏差契约'
            elif result['experience_floor_not_enforced']:
                title += '·经验下限未校核'
    export_pdf(tasks,str(out/'master_programme.pdf'),title=title,meta={'baseline_format':'json'})
    city_val = project.get('city') or project.get('planning', {}).get('city') or '杭州'
    area_val = project.get('area') or project.get('planning', {}).get('area') or 25000
    delivery_val = project.get('delivery') or 'DBB'
    bidding_val = project.get('bidding') or 'public'
    generate_pptx_milestones(title,{'city':city_val,'area':area_val,'delivery':delivery_val,'bidding':bidding_val,'cost':'按实结算'},tasks,str(out/'milestones.pptx'))
    from openpyxl import Workbook
    wb=Workbook();ws=wb.active;ws.title='完整节点表';ws.append(['ID','稳定标识','节点','工期','开始','完成','前置','工作流'])
    for t in tasks:ws.append([t['id'],t.get('semantic_id',''),t['name'],t.get('duration_days'),t.get('start'),t.get('finish'),t.get('predecessors',''),t.get('workflow','')])
    ws.freeze_panes='D2'
    for col in ('B','C'):ws.column_dimensions[col].width=45
    for col in ('E','F'):ws.column_dimensions[col].width=16
    reasoning=wb.create_sheet('工期判断')
    reasoning.append(['节点','工作范围','阶段与交付','规模及状态','采用工作日','区间工作日','日历及依据','为何采用','依据类型','来源','原文','前提','缺失资料','复核状态'])
    for j in result['duration_judgements']:
        reasoning.append([j['name'],j['scope'],j['stage'],j['scale']['description']+' / '+j['scale']['status'],
                          j['selected_workdays'],f"{j['range']['low']}–{j['range']['high']}",
                          j['calendar']['mode']+' / '+j['calendar']['basis'],j['selection_reason'],j['basis'],
                          j['source_id'],j['quote'],'；'.join(j['assumptions']),'；'.join(j['missing_inputs']),j['review']['status']])
    reasoning.freeze_panes='B2';reasoning.auto_filter.ref=reasoning.dimensions
    from openpyxl.styles import Alignment
    for col in 'ABCDEFGHIJKLMN':reasoning.column_dimensions[col].width=30
    for row in reasoning.iter_rows(min_row=2):
        for cell in row:cell.alignment=Alignment(wrap_text=True,vertical='top')
    limits=wb.create_sheet('条件与依据');limits.append(['类型','内容'])
    limits.append(['计划管理层级', result.get('plan_level', 'UNKNOWN')])
    limits.append(['数据依据性质', result.get('scenario_nature', 'UNKNOWN')])
    limits.append(['工期可用性',result['duration_acceptance']])
    limits.append(['承诺排程许可',False])
    limits.append(['契约适用范围',result['contract_scope']])
    limits.append(['包络参考日历（不覆盖任务日历）',json.dumps(result['summary_calendar'],ensure_ascii=False)])
    limits.append(['工作流内重叠', '各阶段FS串行；未建模桌面审查与现场等阶段的部分重叠'])
    for item in result['experience_floor_assessments']:
        if item.get('status') == 'ACCEPTED_WITH_DEVIATION':
            dev = item['deviation']
            limits.append([f"工期偏差契约 ({item['workflow']})", f"ACCEPTED_WITH_DEVIATION: 偏差{dev['deviation_days']}工作日 | 路径: {dev['justification_path']} | 失效条件: {dev['invalidation_condition']}"])
        else:
            limits.append(['整体经验下限未校核',json.dumps(item,ensure_ascii=False)])
    limits.append(['规划日历来源',json.dumps(result['calendar_provenance'],ensure_ascii=False)])
    if result['duration_sensitivity']:limits.append(['工期区间情景（非概率）',json.dumps(result['duration_sensitivity'],ensure_ascii=False)])
    for item in result['unscheduled_conditions']:limits.append(['未计工期条件',json.dumps(item,ensure_ascii=False)])
    for n in result['assembled_nodes']:
        if n.get('estimate_basis'):limits.append([n['key'],json.dumps(n['estimate_basis'],ensure_ascii=False)])
    limits.column_dimensions['B'].width=120
    wb.save(out/'master_programme.xlsx')
    notes=f"# 本次规划范围\n\n【{result.get('plan_level','L2')}｜{result.get('scenario_nature','ILLUSTRATIVE')}】\n\n{result['plan_state']}。仅内部草案，不是客户批准。\n\n工期可用性：{result['duration_acceptance']}。\n\n"
    notes+='已装配：'+', '.join(x['workflow'] for x in result['applied_workflows'])+'。\n\n'
    notes+='未计工期条件（不能理解为零天或无需办理）：\n'+''.join('- '+json.dumps(x,ensure_ascii=False)+'\n' for x in result['unscheduled_conditions'])
    notes+='\n原生MPP未在本入口导出或验证。行业及经验月份不用于自动赋值。\n'
    boundary_notes='\n本工期判断契约仅适用于 --engine_project + planning_review；普通办公CLI未接此契约。\n'
    boundary_notes+=f"\n包络采用 {result['summary_calendar']['mode']} 参考日历：{result['summary_calendar']['basis']}。任务保留各自日历，包络不是任务工期求和。\n"
    boundary_notes+='\n工作流内部各阶段采用FS串行，未建模桌面审查与现场等阶段的部分重叠，不代表已优化的最短周期。\n'
    for item in result['experience_floor_assessments']:
        if 'threshold' in item:
            units={'calendar_month':'日历月','calendar_week':'日历周','calendar_day':'历日','working_day':'工作日'}
            th=item['threshold']
            if item.get('status') == 'ACCEPTED_WITH_DEVIATION':
                dev = item['deviation']
                boundary_notes+=f"\n[工期偏差披露] {item['workflow']} 经验下限至少{th['value']}{units[th['unit']]}；状态：ACCEPTED_WITH_DEVIATION（偏差{dev['deviation_days']}工作日）。采纳路径：{dev['justification_path']}。理由：{dev['rationale']}。失效条件：{dev['invalidation_condition']}。\n"
            else:
                boundary_notes+=f"\n{item['workflow']} 经验下限：至少{th['value']}{units[th['unit']]}；整体经验下限未校核（EXPERIENCE_FLOOR_NOT_ENFORCED）。未自动换算或判为通过。覆盖阶段：{', '.join(item['stages'])}。原文：{item['quote']}\n"
        else:
            boundary_notes+=f"\n{item['workflow']} 未提供整体经验下限评估，不计为通过。\n"
    notes+=boundary_notes
    if result['duration_sensitivity']:
        s=result['duration_sensitivity']
        notes+=f"\n全低值情景完成：{s['low_finish']}；选定情景：{s['selected_finish']}；全高值情景：{s['high_finish']}。仅计算已纳入范围，非概率区间或承诺。\n"
    reasoning_notes=f"# 工期判断审阅\n\n【管理层级：{result.get('plan_level','L2')}｜数据性质：{result.get('scenario_nature','ILLUSTRATIVE')}】\n\n阶段、数值及引用的一致性检查不自动证明原文语义正确、专业合理性、实际完成或客户批准。\n{boundary_notes}"
    for j in result['duration_judgements']:
        reasoning_notes+=f"\n## {j['name']}\n\n范围：{j['scope']}\n\n阶段：{j['stage']}\n\n规模：{j['scale']['description']}（{j['scale']['status']}）\n\n采用：{j['selected_workdays']}工作日；区间：{j['range']['low']}–{j['range']['high']}；日历：{j['calendar']['mode']}，{j['calendar']['basis']}\n\n判断：{j['selection_reason']}\n\n来源：{j['source_id']}（{j['basis']}）；{j['quote']}\n\n前提：{'；'.join(j['assumptions'])}\n\n缺失：{'；'.join(j['missing_inputs']) or '未声明缺项（不表示已核实完整）'}\n\n复核：{j['review']['status']}\n"
    (out/'DURATION_REVIEW.md').write_text(reasoning_notes,encoding='utf-8')
    (out/'READ_ME.md').write_text(notes,encoding='utf-8')
    export_status={'pdf':'GENERATED','pptx':'GENERATED','xlsx':'GENERATED','mpp':'NOT_REQUESTED'}
    if args.engine_mpp:
        try:
            from datetime import date
            from exporters import build_mpp
            from core.holidays import load_holiday_raw
            from core.msp_session import MSProjectSession
            from core.mpp_calendar_audit import audit_native_calendars
            native,audit=build_mpp(title,date.fromisoformat(result['schedule']['start_date']),tasks,load_holiday_raw(),str(out/'master_programme.mpp'))
            with MSProjectSession() as session:
                native_project=session.open(native)
                calendars=audit_native_calendars(session.app,native_project,result['schedule']['start_date'],result['schedule']['finish_date'],load_holiday_raw())
            (out/'native_calendar_audit.json').write_text(json.dumps(calendars,ensure_ascii=False,indent=2),encoding='utf-8')
            if audit['status']!='PASS' or calendars['status']!='PASS':raise RuntimeError('Native readback/calendar mismatch')
            export_status['mpp']='READBACK_AND_CALENDAR_PASS'
            notes=notes.replace('原生MPP未在本入口导出或验证。','原生MPP已保存、重新打开并通过本次回读及日历检查。')
            (out/'READ_ME.md').write_text(notes,encoding='utf-8')
        except Exception as exc:
            export_status.update(mpp='FAILED',error=str(exc))
            (out/'export_status.json').write_text(json.dumps(export_status,ensure_ascii=False,indent=2),encoding='utf-8')
            raise
    (out/'export_status.json').write_text(json.dumps(export_status,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Workflow engine completed: {out}')


if __name__ == '__main__':
    import sys
    main(sys.argv[1:])
