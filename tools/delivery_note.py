"""One plain-language delivery note per run, written for the person who ran the skill.

Built from the run's own audit files after every delivery check, so it never
states a date or a file that this run did not produce. Internal codes and the
technical release reasoning stay in the audit folder.
"""
from __future__ import annotations
import datetime
import json
from pathlib import Path

# Defaults the user should actually check; engine controls stay in the input workbook.
CHECK_DEFAULTS = ('site_condition', 'calendar_profile', 'space_use', 'furniture_in_scope', 'fire_release_policy')
NUMERALS = '一二三四五六七八九'

FILES = [
    ('.mpp', 'Microsoft Project 原生计划（已做保存后重开日期核对）'),
    ('.pdf', 'A3 甘特图，可直接打印或发给客户'),
    ('_Milestones.pptx', 'PM/高管阶段视图：真实阶段跨度与条件'),
    ('_BD_KeyMilestones.pptx', 'BD投标用单页 Key Milestones；与PM阶段视图共用排程日期'),
]


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _days(a: str, b: str) -> int:
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days + 1


def build_sections(output: Path, payload: dict, route: dict, changes: dict | None = None) -> tuple[str, list[tuple[str, list[str]]]]:
    from core.programme_delivery import plain_field, plain_value
    from core.draft_presentation import PREFIX
    stem = output.stem
    schedule = _load(output.with_name(stem + '_schedule.json'))
    release = schedule.get('release_controls') or {}
    decision = schedule.get('target_decision') or {}
    project = payload.get('project', {})
    draft = bool(project.get('authority_draft'))
    name = project.get('project_name', '项目')
    title = ('【诊断草案·报审路径待核实】' if draft else '') + name + ' — 进度计划交付说明'
    sections = []

    from core.authority_reference import _key_dates
    start, finish = schedule.get('start_date') or project.get('start_date'), schedule.get('finish_date')
    key = _key_dates(schedule.get('tasks', [])) if schedule.get('tasks') else {}
    overview = []
    if start and finish:
        dates = [f'启动 {start}']
        if key.get('permit'):
            dates.append(f'取得施工许可 {key["permit"]}')
        if release.get('main_move_in') or key.get('move_in'):
            dates.append(f'搬迁开始 {release.get("main_move_in") or key["move_in"]}')
        dates.append(f'全部完成 {finish}（共 {_days(start, finish)} 个日历天）')
        overview.append('结果：' + '；'.join(dates) + '。')
    if decision.get('status') == 'TARGET_MISSED':
        overview.append(f'目标：{decision["target"]} 前搬迁，按正常工序 {decision.get("calculated_move_in", decision["calculated_finish"])} 开始搬迁，'
                        f'晚 {decision["variance_calendar_days"]} 个日历天，需要你选择处理方式（见下文“目标日期”）。')
    # Where the time goes: three spans between dates the PM already knows.
    physical = next((t.get('finish') for t in schedule.get('tasks', []) if '物理竣工' in t.get('name', '')), None)
    if start and finish and key.get('permit') and physical:
        overview.append(f'工期构成：启动至取得施工许可 {_days(start, key["permit"])} 天（设计、招采与报审）；'
                        f'其后至物理竣工 {_days(key["permit"], physical) - 1} 天（施工）；'
                        f'其后至全部完成 {_days(physical, finish) - 1} 天（检测、验收、家具与搬迁）。均为日历天，三段合计等于总工期。')
    overview.append('报审路径：诊断草案；参考时限用于推演，尚未核实项目适用类别及属地办理要求。' if draft else '报审路径：按本次提供的项目分类和匹配到的属地规则排；具体范围见本次条件。')
    from tools.condition_sheet import summary
    overview.append(summary(payload))
    overview.append('施工阶段按PM工作包汇报，展开MPP可查详细前置；工作包摘要不承担逻辑关系。零天仅表示独立成果或放行，不表示执行检查无需时间。')
    if changes:
        from tools.pm_brief import change_line
        overview.append(change_line(changes))
    overview.append('日期经 PM 签认后，再作为对客户的承诺。')
    sections.append(('结论', overview))
    if release:
        release_lines=[]
        if release.get('main_move_in'):release_lines.append('本次主计划搬迁开始：'+release['main_move_in']+'。')
        if release.get('scenario_move_in'):
            release_lines.append('抽查情境搬迁开始：'+release['scenario_move_in']+'；较主计划变化 '+str(release.get('delta_calendar_days'))+' 个日历天；固定其他条件重算。')
        release_lines.extend(w['message'] for w in release.get('warnings',[]) if w.get('message'))
        if release.get('policy_note'):release_lines.append(release['policy_note'])
        if release_lines:sections.append(('放行与外部交付条件',release_lines))
    from tools.condition_sheet import rows as condition_rows
    conditions=condition_rows(payload)
    sections.append(('本次条件与待核实项',
        [x['label']+'：'+x['value']+'（'+x['status']+'）' for x in conditions
         if x['field'] in {'site_condition','calendar_profile','zoning_mode','furniture_tracking','authority_basis'}
         or (x['field'].startswith('addon_') and x['value']!='明确排除')]))

    if decision.get('status') == 'TARGET_MISSED':
        lines = [f'目标搬迁日 {decision["target"]}，按正常工序 {decision.get("calculated_move_in", decision["calculated_finish"])} 开始搬迁，'
                 f'晚 {decision["variance_calendar_days"]} 个日历天。计划没有为了达标而缩短工期。']
        if decision.get('decision_required'):
            lines.append('请选择下一步：① 保留推演计划，之后由 PM 调整；② 评估压缩方案（另行重算，不覆盖原计划，不保证达标）。')
        result = decision.get('compression_result')
        if result:
            lines.append(f'压缩评估：最早 {result["finish"]}，可节省 {result["saving_calendar_days"]} 个日历天，'
                         + ('仍未达到目标。' if result['remaining_gap_calendar_days'] > 0 else '模型日期在目标内，采用前须确认实施条件。'))
        critical = [w['name'] for w in decision.get('critical_work', [])][-5:]
        if critical:
            lines.append('影响完成日的关键工作（节选）：' + ' → '.join(critical) + '。')
        sections.append(('目标日期', lines))

    review = schedule.get('authority_draft_review')
    if review:
        lines = ['计划中已包含：']
        sequence = {('review', 'authority'): 0, ('review', 'applicant'): 1, ('review', 'buffer'): 2,
                    ('permit', 'applicant'): 3, ('permit', 'authority'): 4, ('permit', 'buffer'): 5}
        for part in sorted(review['included'], key=lambda p: sequence.get((p['step'], p['kind']), 9)):
            if part['kind'] == 'authority':
                lines.append(f'· {part["name"]}：{part["days"]} 个工作日（行政办理时限；{part["basis"]}）')
            elif part['kind'] == 'applicant' and part.get('planning_days') is not None:
                lines.append(f'· {part["name"]}：{part["days"]} 个工作日（规划 {part["planning_days"]} 天＋缓冲 {part["buffer_days"]} 天，'
                             f'缓冲原因：{part["buffer_reason"]}；可调整）')
            elif part['kind'] == 'applicant':
                lines.append(f'· {part["name"]}：{part["days"]} 个工作日（{part["basis"]}）')
            else:
                lines.append(f'· {part["name"]}：{part["days"]} 个工作日（用户设定缓冲）')
        sections.append(('报审时间组成', lines))

    present = [output.with_name(stem + suffix) for suffix, _ in FILES]
    files = [f'{p.name}：{text}' for p, (_, text) in zip(present, FILES) if p.is_file()]
    if route.get('route') == 'pdf':
        files.append('本次未生成 MPP：' + route.get('reason', '') + '。PDF 与 Timeline 的日期与 MPP 路线相同。')
    files.append('审计数据 文件夹：计算过程与核对记录，供复核使用，一般无需打开。')
    sections.append(('本次交付的文件', files))

    # Only what the PM really has to chase: supplies by others and declared special cases.
    # The approval route and the default conditions are not listed (2026-10-14).
    pending = []
    pending.extend(w['reason'] for w in payload.get('warnings', []) if w.get('field') == 'permit_waived')
    from tools.pm_brief import supply_notes
    supplied = supply_notes(project)
    if supplied:
        pending.append('以下专项由他方提供或沿用现有，请确认交付和可用日期：')
        pending.extend(supplied)
    if project.get('fire_classification') == 'special':
        # Office fit-outs are planned as ordinary construction; say plainly what this route lacks.
        pending.append('· 已声明为特殊建设工程：本版对该路径未完整支持（例如消防设计审查及其前后工序），'
                       '计划中的消防工序请按项目实际人工核对。')
    if pending:
        sections.append(('需要你确认的事项', pending))
        asks = sum(1 for line in pending if line.startswith('· '))
        overview.insert(len(overview) - 1, f'需要你确认：共 {asks} 项（见最后一节），确认后重新运行即可更新计划。')
    return title, [(NUMERALS[i] + '、' + head, lines) for i, (head, lines) in enumerate(sections)]


def write_delivery_note(output: Path, payload: dict, route: dict, changes: dict | None = None) -> Path:
    title, sections = build_sections(output, payload, route, changes)
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
    except ImportError:
        path = output.with_name(output.stem + '_交付说明.txt')
        body = [title, ''] + [line for head, lines in sections for line in ['', head, *lines]]
        path.write_text('\n'.join(body) + '\n', encoding='utf-8-sig')
        return path
    doc = Document()
    
    # 文档调色板
    COLOR_SPACE = RGBColor(0x1F, 0x1F, 0x1F)      # #1F1F1F charcoal (标题与核心要素)
    COLOR_INK = RGBColor(0x1A, 0x1A, 0x1A)        # #1A1A1A Ink (正文)
    COLOR_RED = RGBColor(0xE6, 0xB8, 0x4D)        # #E6B84D attention gold (警示/待处理)
    
    style = doc.styles['Normal']
    style.font.name = 'Arial'
    style.element.rPr.rFonts.set('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia', 'DengXian')
    style.font.size = Pt(10.5)
    style.font.color.rgb = COLOR_INK
    
    heading = doc.add_heading(title, level=1)
    for run in heading.runs:
        run.font.name = 'Arial'
        run.font.size = Pt(16)
        run.font.bold = True
        run.font.color.rgb = COLOR_SPACE
        run.element.rPr.rFonts.set('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia', 'DengXian')
        
    from tools.pm_brief import stamp_line
    p_stamp = doc.add_paragraph(stamp_line())
    p_stamp.runs[0].font.size = Pt(9)
    p_stamp.runs[0].font.color.rgb = RGBColor(0x59, 0x59, 0x59)

    for head, lines in sections:
        h2 = doc.add_heading(head, level=2)
        for run in h2.runs:
            run.font.name = 'Arial'
            run.font.size = Pt(12)
            run.font.bold = True
            run.font.color.rgb = COLOR_SPACE
            run.element.rPr.rFonts.set('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia', 'DengXian')
        for line in lines:
            p_line = doc.add_paragraph()
            p_line.paragraph_format.line_spacing = 1.2
            p_line.paragraph_format.space_after = Pt(3)
            r = p_line.add_run(line)
            r.font.name = 'Arial'
            r.font.size = Pt(10)
            r.font.color.rgb = COLOR_INK
            r.element.rPr.rFonts.set('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia', 'DengXian')
            if '晚' in line or '警示' in line or 'TARGET_MISSED' in line:
                if '晚 ' in line:
                    r.font.color.rgb = COLOR_SPACE
                    r.font.bold = True
    from tools.office_document import apply
    apply(doc,'进度计划交付说明')
    path = output.with_name(output.stem + '_交付说明.docx')
    doc.save(path)
    return path
