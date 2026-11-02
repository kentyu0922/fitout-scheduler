# 项目输入表（3.11统一入口）

客户可填写同目录 PROJECT_INTAKE_TEMPLATE.xlsx，也可直接提交 prompt。字段验证唯一来源为 core/project_input.py。

必填 city / area_sqm / sector / delivery / bidding；start_date（项目启动）与 target_date（搬迁目标）至少一项。其他已有资料直接使用；不猜城市、日期、交付或邀约方式。

默认现场 full_strip、施工7D只作为已披露假设。客户已提供的毛坯、未知状态或日历优先。默认不问造价、货梯、层数；有造价信息时可保留用于审批判定。

所有输入运行 tools/run_contract.py --input <project.json或client.xlsx>；plan/review/scenario共用判定。详细来源与冲突规则见 ../references/input_contract.md。
