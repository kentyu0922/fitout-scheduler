# 项目输入与来源规则

交付方式字段 delivery_profile：表格下拉选“精简／完整”，分别标准化为lean/full，与prompt及CLI --delivery_profile共用同一判定。明确选精简自动生成MPP，略去PDF/PPTX/XLSX/XML；明确选完整自动生成MPP及报表。--lean是同一字段的兼容别名；若与表格的明确“完整”冲突则阻断。精简与--no_mpp也冲突。未填写交付方式时按本机环境自动选择：检测到Microsoft Project则生成MPP及报表，未检测到则自动改用PDF＋Timeline并在结果中说明；默认值不标成客户已选。明确要求MPP（精简、完整或--mpp）但本机没有Project属于冲突，停下询问用户：改用PDF路线，或到装有Project的电脑运行；入口同时生成“改用PDF路线”输入文件，用户选择后无需重新填写。

客户可以填写 `templates/PROJECT_INTAKE_TEMPLATE.xlsx`，也可以直接说需求。自然语言由 agent 提取为 JSON，不采用固定正则猜测工程意思。JSON、Excel、CLI 均由 `core/project_input.py` 标准化并经 `tools/run_contract.py` 进入同一个 L2 引擎。

必填：city、area_sqm（正整数）、sector、delivery、bidding。start_date（项目启动 Kick-off）与 target_date（目标搬迁）至少一项；都有时正排并检查目标。不能把施工进场日替代项目启动日；用户只说“开工”且含义不明时需问清。

行业：office / gaming / finance / biopharma / semiconductor；交付：DB / DBB；邀约：invite / public。不得猜城市、行业、交付方式、邀约方式或日期。

现场状态缺失：full_strip，仅为默认假设；明确毛坯：shell；明确未确认：unknown；局部改造：renovation_partial，必须提供 demolition_fraction（0..1）。默认 calendar_profile=7D（沿用原规划），可提供5D/6D。authority_profile是历史诊断控制；正式路径还须通过属地工程分类检查。未提供分类时返回缺口，仅明确authority_draft=true允许诊断模板，不能表示法律适用性已核实。

已有材料优先；CLI 和文件之间不存在暗中覆盖。同一字段的两个明确值冲突时生成澄清单，只有确认后的新输入才能解决。系统默认值可以由客户明确值替代。JSON 回读保持默认来源，不能把默认假设变成客户确认。

Prompt 提取示例（值仅为示例，不作为用户项目默认）：

```json
{
  "project": {
    "project_name": "办公规划示例",
    "city": "上海", "area_sqm": 3000, "sector": "office",
    "delivery": "DB", "bidding": "invite", "start_date": "2026-11-02"
  },
  "field_evidence": {
    "start_date": {"quote": "项目11月2日正式启动"}
  }
}
```

现场日期来源可放 quote；客户陈述标记为 provided/unverified_statement，不等于外部独立验证。保留缺项，不编写签收、审批完成或现场事实。

运行：`python -X utf8 tools/run_contract.py --input project.json --no_mpp`；表格同命令，只换为 .xlsx。补充项目资料用 --reference，可重复；先检查用 --dry_run。原四列表格按“项目填值/确认状态”表头识别，五列无表头旧表回退D列；新表D列为值，F列示例永不读取。表格公式无缓存结果时要求在Excel重算或填写实值。历史表的自由文本/未知字段只披露，不假装已织入计划。

cost_10k_rmb 仅在客户已有造价信息时使用；不作为常规缺项问题，不默认100万。机房/实验室与标准行业包冲突会阻断，避免生成违背明确排除范围的计划；外电状态upgrade_needed按共享模块规则触发增容专项；与明确排除冲突则澄清，既有拟复用或他方提供保留外部接口。

输出 programme_input.json、programme_Input_Assumptions.xlsx、schedule JSON 和 RUN_SUMMARY 保存每个采用值的来源；PDF/PPTX显示关键现场与日历假设。每次默认运行使用独立目录；显式 --output 可以选择位置。

22项选择含义见core/scope_selection.py；表格说明与Prompt提取使用相同规则。行业建议不自动加载；详细判断见[主表与行业判断](master_refinement.md)。

## rc10工序输入

[工序与接口规则](process_closure.md)列出上海分支和家具状态字段；Prompt、JSON、CLI和中文intake采用同一校验。上面的最小JSON仅展示来源记录，缺少报审分类时会返回NEEDS_CLARIFICATION，不能当作完整可出表输入。完整假设案例见templates/PROJECT_INTAKE_EXAMPLE_SH_REVIEWED.xlsx。

家具留空不展开供货链；仅开启跟踪后，已下单须剩余交期和来源、已到货/复用须清单或状态依据。参数与审批分类都不自动从行业推断。


## rc11可选范围与放行字段（schema 1.5）

家具安装furniture_in_scope默认true（规划范围，可明确false）；家具独立招采furniture_procurement和供货跟踪furniture_tracking默认false，两个开关相互独立。只有供货状态没有开关时返回意图澄清；不自动启用。新购跟踪须明确采购下单链，已下单须剩余交期及来源，已到货/利旧须状态来源。排除范围与跟踪、采购信息冲突时阻断。

fire_release_policy为pending/filing/inspection，默认pending。后两者须fire_release_basis。fire_inspection_days为明确窗口工作日，否则比较情境暂用15；整改与复核须成对提供fire_rectification_days、fire_recheck_days（非负整数），所有自定义天数须fire_duration_basis。Prompt、JSON、Excel和CLI共享校验；CLI布尔字段用`--furniture_tracking true`或`false`，authority_draft仍为独立flag。

旧版样例中的供货状态不再自动启动生产链。完整新例见PROJECT_INTAKE_EXAMPLE_SH_REVIEWED.xlsx（默认无家具招采）；实际工程分类仍为演示假设，不能迁移为其他客户事实。


## rc12入口校验

放行政策下拉：待确认→pending；按备案条件放行（需依据）→filing；等待抽查结果（内部保守政策）→inspection。Prompt与表格采用同一别名映射，后两项须提供依据。未增加另一套“乐观/保守”开关。内层校验对非数字、非有限数字、布尔冒充天数和未规范化布尔值返回字段错误；统一入口负责将“是/否”解析为布尔值。

非上海城市保留人工适配入口，明确核对许可适用限额、图审消防路径、主体及资料、时限和有效依据；填写文字本身不会自动认证新城市。诊断授权仅允许未核实模板，不能作为执行承诺。


rc13未增加资料齐备后另加N天的入口，也不将此自由文本静默转换为额外等待；须先划清与既有办理预留的工作范围，避免重复计时。
