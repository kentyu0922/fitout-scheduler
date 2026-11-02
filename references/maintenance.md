# 当前3.12.0-rc8-master-refined验证

以根目录 VALIDATION_RESULT.json 和 modular_release.md 为准；本版未运行完整测试集。以下旧版本统计仅作历史记录。

# 维护与复验

统一输入规则唯一来源：`core/project_input.py`。旧intake运行工具是正式入口的兼容别名，不再运行另一套动态WBS。研究/回归工具保留，但不属于客户常规入口。

原签收L2参数仍在config/l2_master_duration.json；不得通过改测试快照使变更通过。原签收表的包内副本 DERIVATION_SIGNOFF_PM_filled_20260923.xlsx 与快照原文件字节指纹一致；回归闸门核对指纹后允许文件名别名，再逐字段复算，不重新签署或伪造原件。

按修改范围选择定向测试；完整回归才执行 `python -X utf8 tools/run_regressions.py`，普通案例交付不自动运行全套。结构复验用 tools/verify_pack.py。新测试同时覆盖输入合流、真实产物一致、缺项冲突、来源保留、审批未知状态、旧表布局和情景隔离。程序回归不能证明每个工程假设适用，也不能证明自然语言在所有模型上都解析正确。

requirements.txt 给最低依赖；constraints-validated.txt 记录本机验证版本。仅Windows+桌面MS Project可验证原生MPP。默认不启动Project；显式 --mpp 会要求本次真的生成MPP，否则正式入口返回非零。未验证的输出不能标成已验证。

交付方式在prompt与表格中共用delivery_profile。明确选精简或完整会请求MPP，精简跳过额外报表；旧表未选时保留原默认。打包时同步程序、空白模板与填写示例，逐文件核对指纹，不将历史全量测试结果标成新版本已跑。3.11.1的完整74脚本未重跑，当前验证范围记录在交付包VALIDATION_RESULT.json。

原生Project只核对计划中按可见性规则预定导出的任务，不能按读回结果删掉缺失任务来通过。core/native_graph.py 将零工期包起点的父汇总引用投影到其上游起点，保留source_predecessors和native_projection；只支持已验证的零lag FS情形，复杂父子关系明确拒绝。原始CPM网络与日期不变，保存重开后的日期/依赖/任务清单仍必须通过。

历史裁决保存在 docs_and_sops/heritage/CONTRACT_3_10_PRE_UNIFICATION.md，仅在追溯时读取。现行流程以 SKILL.md 与共享输入契约为准；历史文档内“禁止intake/分区/日历选择”等限制不覆盖当前入口。
