"""Blank client XLSX generated from the shared prompt/JSON contract."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.project_input import REQUIRED, CHOICES, SUPPORTED, DEFAULTS, VALUE_ALIASES, BOOLEANS
from core.module_catalog import CATALOG
from core.scope_selection import TRIGGERS
LABELS = {
    "target_response": "目标无法达到时如何处理",
    "industry_work_profile":"基础核对范围（留空按行业核对；仅在确认不需要行业特定核对时选通用办公）",
    "delivery_profile": "交付方式（精简／完整）",
    "city": "项目城市", "area_sqm": "建筑面积（㎡，正整数）", "sector": "行业",
    "delivery": "交付模式", "bidding": "邀约方式", "project_name": "项目名称",
    "start_date": "项目启动日 Kick-off（不是施工进场日）", "target_date": "目标搬迁日（客户要求的搬家日期）",
    "site_condition": "现场状态", "calendar_profile": "施工日历",
    "space_use": "实际用途：纯办公室／办公含专项空间／待确认",
    "cost_10k_rmb": "已提供的估算造价（万元，可留空）",
    "mode": "使用模式：plan/review/scenario", "demolition_fraction": "局部拆除比例（0..1）",
}
LABELS.update(server_room_area_sqm="机房面积（㎡）", acoustic_room_area_sqm="声学房间面积（㎡）", server_room_early_order_basis="机房设备独立提前订货授权依据（留空暂按总包签约后下单）")
LABELS.update({
 'province':'省份（属地识别，选填）',
 'district':'区或园区（苏州请选择具体管辖区）',
 'authority_size_class':'审图机构核定规模档位（中小型／大型；不按装修面积猜测）',
 'authority_local_route':'属地适用路径确认（园区试点须设计或窗口核定）',
 'furniture_install_quantity':'家具安装同口径数量（可选，须与类型和能力一起填写）',
 'furniture_install_type':'家具类型及计量单位（同口径；混合家具须供应商折算）',
 'furniture_install_rate':'每班组每日安装量（与数量同单位，含物流制约）',
 'furniture_install_crews':'可同时作业安装班组数（正整数）',
 'furniture_install_basis':'家具安装能力依据（含组装、楼层运输及电梯限制）',
 'furniture_in_scope':'是否包含家具安装（明确不需要则移除）',
 'furniture_procurement':'是否单独跟踪家具招采（默认否）',
 'furniture_tracking':'是否跟踪家具供货交期（默认否，与招采分开）',
 'fire_release_policy':'其他建设工程入驻放行政策（待确认／按备案及其他条件／等待抽查）',
 'fire_release_basis':'放行政策依据（业主批准或属地明确要求）',
 'fire_inspection_days':'抽查工作情境天数（留空15工作日窗口假设，非保证耗时）',
 'fire_rectification_days':'已明确的整改天数（不默认发生）',
 'fire_recheck_days':'整改后的复核天数（须单独确认）',
 'fire_duration_basis':'抽查、整改、复核工期依据',
 'building_safety_assessment':'房屋安全性及适用抗震鉴定（按房龄、结构与历次改造核定）',
 'building_context':'建筑背景（以产权、现状及设计核实为准）',
 'renovation_category':'装修工程分类（由业主与设计单位共同核定）',
 'authority_special_policy':'专项审批政策适用性（须核对，非随意选择）',
 'fire_classification':'本次工程消防审验分类',
 'fire_sampling':'其他建设工程备案抽查状态',
 'authority_basis':'工程分类依据（设计确认/主管部门指引/文件名称）',
 'authority_rule_date':'拟申报适用日期（留空按项目启动日核对有效期）',
 'authority_review_days':'施工图审查行政时限（工作日；未核实城市选填，留空按全国规定10）',
 'authority_permit_days':'施工许可行政审批时限（工作日；未核实城市选填，留空按全国规定7）',
 'authority_fire_days':'特殊建设工程消防验收时限（工作日；留空按全国规定15）',
 'authority_review_buffer_days':'图审衔接余量（工作日；已接入适用路径留空暂估2，可填0；非行政时限）',
 'authority_permit_buffer_days':'许可衔接余量（工作日；已接入适用路径留空暂估2，可填0；非行政时限）',
 'authority_fire_buffer_days':'消防报验衔接余量（工作日；已接入适用路径留空暂估2，可填0；不替代整改复查）',
 'permit_waived':'不办理施工许可（默认否；仅在确认免办时选“是”，计划改为报送或备案）',
 'authority_buffer_reason':'报审缓冲原因（填写缓冲天数时必填）',
 'authority_days_basis':'报审天数依据（如当地住建窗口答复；填写天数时必填）',
 'flushout_days':'通风散味天数（工作日；仅在项目明确要求时填写，默认不加通风等待）',
 'occupancy_basis':'通风散味天数的依据（如业主书面要求、当地规定；与天数同时填写）',
 'authority_draft':'报审信息不全时先出诊断草案（留空默认先出草案并醒目标注；选“否”则须先补齐报审信息）',
 'furniture_supply_state':'家具供货现状（仅启用跟踪时填写；留空不推断新购）',
 'furniture_supply_basis':'家具订单、供应商承诺或库存清单依据',
 'furniture_remaining_days':'已下单家具剩余交期（整数工作日）',
 'furniture_lead_profile':'新购家具供货档位（须供应商复核，不按面积缩放）',
})
LABELS.update({"addon_"+i.lower(): m["label"]+("（需专项定义，暂不自动排程）" if not m["executable"] else "") for i,m in CATALOG.items()})
LABELS["procurement_regime"]="适用采购制度（未知可留空）"
LABELS["procurement_policy"]="采购流程：企业三轮报价、允许谈判、技术商务评审"
LABELS["statutory_tender_required"]="是否依法必须招标（是/否；私营自有资金通常否）"
LABELS["power_supply_type"]="供电报装类型（低压／高压单电源／高压双电源）"
LABELS["eia_document_type"]="环评文件类型（报告书／报告表／登记表）"
LABELS["fume_hood_count"]="排风柜数量（柜位驱动，可留空）"
from core.equipment_leads import LEAD_ITEMS, DEFAULT_LABEL
for _field, _addon, _label, _crit in LEAD_ITEMS:
    LABELS[_field] = f"{_label}日历日（{_addon}；留空={DEFAULT_LABEL}）"
EXAMPLES = {"city": "上海", "area_sqm": 3000, "sector": "office", "delivery": "DB",
            "bidding": "invite", "start_date": "2026-11-02", "target_date": "2028-01-31"}
def generate(path):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "项目准入与事实采集表"
    ws.merge_cells("B1:F1")
    ws["B1"] = "项目总控进度事实采集与决策需求表"
    ws["B1"].font = Font(name="Microsoft YaHei", size=14, bold=True, color="003E51")
    ws.merge_cells("B2:F2")
    ws["B2"] = "请填写项目实际工程条件及来源依据；未提供参数按披露的规划暂估处理，不代表已核实的定额。启动日、目标搬迁日至少填写一项。"
    ws["B2"].font = Font(name="Microsoft YaHei", size=10, color="555555")

    thin_gray = Side(style='thin', color='E0E0E0')
    top_space = Side(style='medium', color='003E51')
    bottom_red = Side(style='thin', color='E30613')
    header_border = Border(top=top_space, bottom=bottom_red, left=None, right=None)
    row_border = Border(bottom=thin_gray, left=None, right=None)

    for i, text in enumerate(["字段键", "字段说明", "填写要求", "项目值", "来源/备注", "示例或合法选项"], 1):
        cell = ws.cell(4, i, text)
        cell.fill = PatternFill("solid", fgColor="BCDEE6")
        cell.font = Font(name="Microsoft YaHei", size=11, bold=True, color="003E51")
        cell.border = header_border
        cell.alignment = Alignment(vertical="center", horizontal="center" if i in (1, 4) else "left")
    ws.row_dimensions[4].height = 28
    # Engine-only switches (usage mode, scenario workfronts and zoning) are not questions for the client.
    order = ["project_name", "delivery_profile", *REQUIRED, "start_date", "target_date", "target_response", "site_condition", "calendar_profile"]
    order += sorted(SUPPORTED - set(order) - {"mode", "workfronts", "zoning_mode", "sectional"})
    for row, key in enumerate(order, 5):
        req = "必填" if key in REQUIRED else "日期至少一项" if key in {"start_date", "target_date"} else "选填"
        if key in DEFAULTS:
            req += f"；留空默认 {DEFAULTS[key]}"
        example = ", ".join(sorted(CHOICES[key])) if key in CHOICES else EXAMPLES.get(key, "")
        if key.startswith('addon_'):
            req = '明确需要则加载；复用/他方提供保留接口；空白待确认'
            example = TRIGGERS[key.removeprefix('addon_').upper()] + '。行业本身不触发；若与用途或已提供需求矛盾，先确认。'
        if key in ('building_safety_assessment','building_context','renovation_category','authority_special_policy','authority_basis'):
            req='生成已选报审路径须提供；先利用已有文件，不能凭行业推断'
        if key == 'fire_classification':
            req='非简易装修须核定；简易装修有消防事项声明时先解决分类冲突'
        if key == 'delivery_profile':
            req = '请选择；旧表留空沿用原交付规则'
            example = '精简：MPP＋简要结果，目标未达时标注诊断草案；完整：MPP＋PDF＋PPTX＋输入假设表。两者均保留MPP保存重开核对。'
        if key == 'target_response':
            req = '留空先提示差距并询问；不自动改短工期'
            example = '保留推演计划供PM后改；或评估独立压缩情境，需提供启动日并确认资源条件，不能保证达标。目标按搬迁开始比较。'
        for col, value in enumerate([key, LABELS.get(key, key), req, None, None, example], 1):
            cell = ws.cell(row, col, value)
            cell.font = Font(name="Microsoft YaHei", size=10, color="1A1A1A")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = row_border
        ws.row_dimensions[row].height = 32
        if key in BOOLEANS:
            dv = DataValidation(type='list', formula1='"是,否"', allow_blank=True)
            dv.showErrorMessage=True; ws.add_data_validation(dv); dv.add(ws.cell(row,4))
        if key in CHOICES:
            options = sorted(CHOICES[key] - ({"manual"} if key == "zoning_mode" else set()))
            if key in ('building_safety_assessment','building_context','renovation_category','authority_special_policy','fire_classification','fire_sampling','furniture_supply_state','fire_release_policy'):
                options=list(VALUE_ALIASES[key])
            if key.startswith('addon_'):
                options = ['需要','不需要','待确认','既有拟复用','他方提供']
            if key == 'procurement_policy':
                options = ['待确认','企业三轮报价及技术商务评审']
            if key == 'industry_work_profile':
                options = ['按行业核对','仅通用办公核对']
            if key == 'space_use':
                options = ['待确认','纯办公室','办公含专项空间']
            if key == 'delivery_profile':
                options = ['精简', '完整']
            if key == 'target_response':
                options = list(VALUE_ALIASES[key])
            dv = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=True)
            dv.showErrorMessage = True
            ws.add_data_validation(dv)
            dv.add(ws.cell(row, 4))
        if key in {"start_date", "target_date", "authority_rule_date"}:
            ws.cell(row, 4).number_format = "yyyy-mm-dd"
    for col, width in zip("ABCDEF", [26, 45, 38, 25, 35, 55]):
        ws.column_dimensions[col].width = width
    ws.column_dimensions['A'].hidden = True  # Stable machine keys remain readable by the intake parser.
    ws.freeze_panes = "D5"
    ws.auto_filter.ref = f"A4:F{ws.max_row}"
    ws.print_title_rows = "1:4"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    wb.close()
    return Path(path)
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default=str(Path(__file__).resolve().parents[1] / "templates/PROJECT_INTAKE_TEMPLATE.xlsx"))
    print(generate(p.parse_args().output))
if __name__ == "__main__":
    main()
