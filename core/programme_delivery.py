"""Read-only execution readiness and portable input disclosures."""
from __future__ import annotations
from core.resource_governance import resource_readiness


DISPLAY = {
    'site_condition': {'full_strip': '全部拆除后重新装修', 'shell': '毛坯', 'cat_b_existing': '既有精装需拆改',
                       'renovation_partial': '局部改造', 'unknown': '待确认'},
    'calendar_profile': {'7D': '每周7天施工', '6D': '每周6天施工', '5D': '每周5天施工'},
    'delivery': {'DB': '设计施工一体化（DB）', 'DBB': '设计与施工分别发包（DBB）'},
    'bidding': {'invite': '邀请招标', 'public': '公开招标'},
    'delivery_profile': {'lean': '精简（只交付MPP）', 'full': '完整（MPP或PDF，另含Timeline）'},
    'mode': {'plan': '编制计划', 'review': '计划＋PM审查', 'scenario': '计划＋快轨情境比较'},
}
ORIGIN = {'default_policy': '默认', 'provided': '客户提供', 'mode_policy': '按所选模式'}
VERIFICATION = {'national_general_limit':'属地规则待核实，仅为参考排程','not_a_site_fact': '非现场事实，待核实', 'unverified_statement': '客户陈述，未独立核实',
                'output_preference': '输出方式选择', 'planning_control': '规划控制项'}


def plain_value(key, value):
    """Client-facing wording for an input value; the code stays in the audit data."""
    if isinstance(value, bool):
        return '是' if value else '否'
    if key in DISPLAY and value in DISPLAY[key]:
        return DISPLAY[key][value]
    from core.project_input import VALUE_ALIASES
    for label, code in VALUE_ALIASES.get(key, {}).items():
        if code == value and any('一' <= ch <= '鿿' for ch in label):
            return label
    if key.startswith('addon_'):
        return {'required': '需要', 'excluded': '不需要', 'unknown': '待确认',
                'existing_claimed': '利用既有（待核验）', 'provided_by_others': '他方提供（待核验）'}.get(value, value)
    return str(value)


def plain_field(key):
    from tools.generate_intake_template import LABELS
    if key.startswith('addon_'):
        from core.module_catalog import CATALOG
        item = CATALOG.get(key[6:].upper())
        if item:
            return '专项：' + item['label']
    return LABELS.get(key, key)


def execution_readiness(input_contract, authority, target_feasibility, quality):
    """Date computation is not proof of available labour or approved scope."""
    pending = []
    if input_contract:
        if input_contract['project'].get('site_condition') == 'unknown':
            pending.append({'field': 'site_condition', 'reason': 'Client explicitly reports site condition unknown; demolition scope unresolved.'})
        for item in input_contract.get('assumptions', []):
            if item['field'] in {'site_condition', 'calendar_profile'}:
                pending.append({'field': item['field'], 'reason': item['basis']})
        for warning in input_contract.get('warnings', []):
            pending.append({'field': warning['field'], 'reason': warning['reason']})
    for key, value in authority['requirements'].items():
        if value.get('legal_status', value['status']) == 'UNKNOWN':
            pending.append({'field': key, 'reason': 'Legal applicability is unverified; planning inclusion is independent.'})
    resources = resource_readiness()
    if resources.get('overall') != 'confirmed':
        pending.append({'field': 'resources', 'reason': 'Available crews, shifts and workfront capacity are not confirmed.'})
    impossible = any((target_feasibility or {}).get(k) == 'TARGET_INFEASIBLE'
                     for k in ('date_feasibility_status', 'conditional_scenario_status'))
    return {'status': 'NOT_EXECUTABLE' if impossible else 'PM_REVIEW_REQUIRED' if pending else 'PM_SIGNOFF_REQUIRED',
            'date_calculated': True, 'execution_confirmed': False, 'pending_conditions': pending,
            'resource_readiness': resources.get('overall'),
            'quality_warnings': quality.get('warnings', []),
            'wording': 'Calculated programme draft. Conditions and named PM approval remain separate from the dates.'}


def disclosure_lines(contract):
    if not contract:
        return []
    fields = contract.get('field_evidence', {})
    p = contract['project']
    lines = []
    for key, label in [('site_condition', '现场状态'), ('calendar_profile', '施工日历')]:
        origin = fields[key]['origin']
        lines.append(f"{label}：{plain_value(key, p[key])}（{'默认' if origin == 'default_policy' else '客户提供，未独立核实'}）")
    from core.module_catalog import resolve_modules, CATALOG
    plan = resolve_modules(p)
    names = lambda ids: '、'.join(CATALOG[i]['label'] if i in CATALOG else i for i in ids)
    lines.append('已选专项：' + (names(plan['selected']) or '无'))
    lines.append('行业基础流程：需求确认→设计审查→施工核对→功能验证→业务启用；未选专项时仍保留，属PM管理流程，不代表法定专项工程。')
    if plan['procurement']['negotiation_allowed']:
        lines.append('企业采购：允许谈判；三轮报价；技术／商务分析、定标审批、合同闭合。流程已选，工期及评分权重待确认。')
    if plan['selected']:
        lines.append('专项工期含未校准规划占位；单设施共享接口假设；外部条件待核验，尚未获得项目专业签收。')
    if plan['recommended_pending']:
        lines.append('行业建议但未确认的专项未计入：'+names(plan['recommended_pending'])+'；如需要请补充。')
    if p.get('furniture_in_scope',True):
        from core.furniture_installation import disclosure
        lines.append(disclosure(p))
    from core.authority_registry import resolve as resolve_jurisdiction
    loc=resolve_jurisdiction(p)
    lines.append('属地规则：'+loc['jurisdiction']['name']+'；'+('按已提供项目分类加载' if loc.get('status')=='READY' else '关键条件或当地规则待核实，使用诊断草案')+'；规则版本 '+loc.get('rule_version','待核实'))
    for conflict in loc.get('service_conflicts',[]):lines.append('办理窗口待核对：'+conflict)
    from core.authority_buffers import buffer_disclosure
    buffer_note=buffer_disclosure(p)
    if buffer_note:lines.append(buffer_note)
    lines.append('审批链为保守规划预留；法律适用性需核实。')
    return lines


def write_input_workbook(contract, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    ws = wb.active
    ws.title = '输入与默认假设'
    ws.append(['项目条件', '采用值', '来源类型', '来源', '依据或摘录', '核实状态', '字段代码'])
    for key, value in contract['project'].items():
        ev = contract['field_evidence'].get(key, {})
        ws.append([plain_field(key), plain_value(key, value), ORIGIN.get(ev.get('origin'), ev.get('origin')),
                   ev.get('source'), ev.get('basis') or ev.get('quote'),
                   VERIFICATION.get(ev.get('verification'), ev.get('verification')), key])
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1F497D')
    for col, width in zip('ABCDEFG', [30, 28, 18, 30, 70, 22, 24]):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical='top')
    ws.freeze_panes = 'C2'
    ws.auto_filter.ref = ws.dimensions
    from core.draft_presentation import draft_notice, PREFIX
    notice = draft_notice(contract.get('authority_plan'))
    if notice:
        ws.insert_rows(1, 2)
        ws.cell(1, 1, PREFIX + str(contract['project'].get('project_name', '总控进度计划')))
        ws.cell(2, 1, notice)
        for row in (1, 2):
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
            ws.cell(row, 1).font = Font(bold=True, color='B00020', size=14)
            ws.cell(row, 1).alignment = Alignment(wrap_text=True)
            ws.row_dimensions[row].height = 30
        ws.freeze_panes = 'C4'
        ws.auto_filter.ref = f'A3:G{ws.max_row}'
    wb.save(path)
    wb.close()
