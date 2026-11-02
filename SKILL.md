---
name: office-programme
description: 为中国大陆办公室及已支持附属专项生成PM总控进度计划。接收自然语言提取的项目条件、JSON或Excel，核对工序接口、报审条件与搬迁目标，默认交付PDF、PM/BD Timeline和Word条件说明；显式--mpp追加原生Project文件。
metadata:
  version: "4.0.0-round5"
  default_route: "pdf_executive_suite"
  agent_entry: "tools/run_contract.py"
---

# 办公室装修总控排程

30秒发起项目需求 → L2/L3工作包与工序依赖计算 → PDF总控计划、PM/BD里程碑PPTX、条件与交付说明。30秒是发起请求，不是生成承诺；实际耗时见本版实测。

## 运行入口

收到需求先利用已有资料，agent将自然语言提取为入口参数。CLI不直接解析任意自由文本。默认PDF套件不依赖Project；不得因JSON、Excel或沿用条件中的full/lean启动COM。只有用户明确要MPP且调用包含`--mpp`时追加原生导出。

```bash
python -X utf8 tools/run_contract.py --city 上海 --area 3000 --sector finance --delivery DB --bidding invite --start_date 2027-03-01 --project_name 上海金融办公
python -X utf8 tools/run_contract.py --input templates/PROJECT_INTAKE_EXAMPLE_SH_REVIEWED.xlsx
python -X utf8 tools/run_contract.py --base <上次输出目录> --addon_server_room required --specialist_area_sqm 150
python -X utf8 tools/run_contract.py --base <上次输出目录> --mpp
python -X utf8 tools/run_contract.py diff --base=examples/intake_dbb_public.json --target=examples/intake_db_invite.json
python -X utf8 tools/run_contract.py --input examples/semiconductor_lab_suzhou.json --output output/semiconductor
python -X utf8 tools/run_contract.py --input examples/aaa_gaming_mocap_idc.json --output output/mocap
```

默认 PDF 套件（ReportLab 矢量甘特，不启动浏览器）。stdout 仅为一个 JSON（含 `benchmark` 实测）；`[BENCHMARK] PMP Scheduler Engine v4.0.0` 打在 stderr。业主品牌不得写进引擎默认项目名，须由 intake/`--project_name` 提供。

**发包与隐蔽（v4.0.0 硬约束）**
- DBB：设计聘用与业主概念可并行；施工图完成后正式发标，图审与施工采购并行，许可等待审查合格和合同。
- D&B：招标版Test-fit发布→正式发标→中标通知启动施工图；合同签署、3–5工作日设计确认与施工图并行，送审前完成。施工图→图审→修改复审→合格书→许可→现场均为硬FS。分段图审/许可默认关闭，需项目所在地主管部门确认。
- 本轮第五轮验收区间（日历天）：非必招 DBB55–95、DB40–85；必招 DBB80–115、DB55–100；差值DBB−DB均为−15至45。8组工况DB总工期不长于DBB；这些是验收目标，不是法规。目标日不够仍披露差距。
- 隔墙到天花隐蔽工作日：约800㎡为 8–15；中型 12–18；约10000㎡为 15–25。天花前须喷淋强度 1.4MPa/30min + 24h 严密性、风管严密性、电气导管隐蔽检查。
- 动捕 A06 无国标（工期不完整）；相机上架在消防联调与项目消防验收之后；防火门框随隔墙、门扇在完成面及地面后、联调前。
- 设计/招采/施工工期由面积、专项房间、产能与班组从既有 config 推导（L2 指数缩放 + 工程量×生产率÷班组），法定窗口锁定。

输出可指定目录、.pdf或.mpp路径，后两者只指定文件名，不决定是否调用Project。`--lean`与delivery_profile保留为兼容输入，均不取消PDF成果套件。默认新目录留存旧结果。缺依赖给安装指引，不自行假装安装成功。

## 统一输入与事实边界

Prompt、JSON和Intake使用core/project_input.py。先运行`tools/check_env.py`，再走唯一入口`tools/run_contract.py`。城市、面积、行业、DB/DBB、邀请/公开与启动或目标日期按入口支持的字段解析；既有资料优先，冲突与阻断项一次汇总。用户提供事实provided、默认规划default_policy、未知unknown与属地待核authority_draft明确区分，均体现在《本次排程条件》。不凭行业猜测实验室/机房，不把表格示例当事实。

支持office/gaming/finance/biopharma/semiconductor五行业，DB/DBB×invite/public四链路。22项addon有required/excluded/unknown/existing_claimed/provided_by_others状态；别名addon_server_room等价addon_a04。既有或甲供保留核验接口。specialist_area_sqm仅表示已输入专项面积，不承诺每种专项工期均已按面积校准。

L2为前期规划、设计、招采、报审、施工、竣工验收、搬迁；阶段可以重叠，不把汇总阶段串行。施工视图为PM总控颗粒度，保留物理竣工和关键放行，详细计算关系不因简化展示被删。企业三轮谈判采购须有明确制度依据，邀请/公开本身不授权谈判。

## 4. 资深PM Lead的五条核心专业判断规则

按以下条件检查实际工序网络，结论附输入、前置关系与计算依据。项目PM确认前保持计划草案状态。

### 规则1：进场准备与依法可施工分开判断

问清进场做什么：现场踏勘、准备、拆改、动火的放行条件可能不同。实体施工作业须等待适用的许可或备案、施工主体资料及物业条件。质安监手续可能合并办理，动火许可针对相关作业；不得全国统一增设固定串行手续，也不把动火批准当所有准备工作的前置。DBB图审不统一等待GC定标；施工许可另核实承包主体及申请资料。输出“作业—适用条件—依据/待确认项—受影响节点”。

### 规则2：长周期设备从需求日期往前检查

识别精密空调、冷水机组、变压器、开关柜等实际在范围内的设备；取得规格、采购授权、供货状态、承诺交期及现场接收条件。技术提资和采购准备可与深化设计并行；技术冻结、合同和授权满足后才锁单，不默认未定技术就采购或一律独立发包。12–16周、10周、提前45天都只能是明确来源的项目参数，不能当通用事实。把供货完成与工作面就绪共同接到安装，再接调试验收；已有/甲供仍保留外部接口。若未跟踪供货，披露未计交期风险。查不到实际连接就列缺口，不能声称已消除延期。

### 规则3：报审时间分清工作、窗口与风险准备

按属地、工程分类与适用日期核实程序，分别说明申请人资料准备签章、行政受理/审查窗口、条件性补正整改及PM缓冲。现场踏勘仅在适用时列入。法定上限、服务承诺、经验规划值和供应商估计不得互换；每个数字标来源、工作日/日历日、适用条件。PM缓冲写理由，不重复计入资料准备或办理时间。沿用已配置路径也须核对适用性；材料不足只能给带条件的草案，不能承诺合法开工或入驻。

### 规则4：目标差距、CPM时差与压缩方案分开证明

正常计划先正推。目标差距不等于CPM负总时差；只有同一网络、完成约束、日历及前后推计算给出ES/EF/LS/LF和总时差时，才能报告Negative Float。没有经验证的负时差结果时，不能编造“-12工作日”。用户选择评估压缩后，检验可分区范围、设计释放、施工资源、工作面、保护措施和审批条件，才生成独立情境；SS+Lag不得凭空指定。报告可节省量、剩余差距、投入与风险；没有资源或费用依据不报定量赶工代价。现有scenario可节省0天，方案须PM批准后采用。

### 规则5：覆盖隐蔽工程与地面安装要有实体放行条件

封板前对应区域适用隐蔽工程须验收通过，并完成必要测试与整改。地面后续工序依据具体系统、基层强度/含水率、养护及厂家要求判断；不能统一认为所有防静电活动地板都必须先做自流平。整体移交不擅自改为分区流水；分区获批后才允许按区放行。将放行接到实际消费者工序，合并显示不能删除依赖。实际作业给有依据的工期，独立放行成果可为0天。无检查依据时，写明未核实条件及其影响。


## 目标与客户决策

不为达标手改天数。默认target_date比较搬迁开始；明确搬完/正式办公须对齐实际控制节点。目标未达仍交计划、差距及target_decision，供PM选择保留或评估独立压缩情境。压缩不自动覆盖原计划，可能节省0天；不把目标差距叫负总时差。

## 成果与回复契约

每次非dry_run成功返回一个stdout JSON，含delivery_status、pm_brief、output_route、target_decision和files。日志与输入诊断写stderr。仅当PDF、两份PPTX及两份DOCX（或TXT替代）可读性检查完成才报DELIVERED。

先贴本次pm_brief关键数字，再列实际文件。完整套件在MPP操作前落盘并验证。显式MPP失败或超时，套件仍有效，output_route.mpp_status说明失败；不得将未通过核验的MPP列作可用。MPP需Windows+桌面Project+pywin32，成功后仍核验保存重开。PDF路线不启动COM。

用途：MPP供PM编辑；PDF供项目审阅；Milestones供PM阶段汇报；BD_KeyMilestones供投标关键日期沟通；条件说明核对输入；交付说明列结果、卡点及待决事项。放行清单从本次任务与证据整理为说明附表，不伪称已核完当地所有手续。

## 专业边界与维护

全国统一入口，属地按已接入范围适配；未核实规则须由项目团队确认，不承诺大中华区全部法规覆盖。适用办公室及已有附属专项，不覆盖整厂、整座数据中心、医院诊疗、EPC/CM/SPLIT新模型、造价比价或每日班组派工。工期规划值待项目校准，测试通过不等于工程批准。

家具首次采样后可安装，不必等首次报告；仍核对工作面及到货，最终空气合格参与入驻放行。无明确依据不默认通风35天。实际工作不能0天代替，独立放行成果可0天。

用户回传前后版本、原条件与修改原因，经PM核实再决定项目修正或共性升级；没有自动上传学习。修改后做对应回归、输入一致性与导出核对。复验与实际性能见[RELEASE_VALIDATION.md](RELEASE_VALIDATION.md)，不能引用旧版测试数冒充本版。


## 工程接口修订（2026-10-31）

四链路采购按准入、RFP、投标准备、技术商务评审与合同连接；企业三轮须明确授权，formal_tender单列适用法定窗口，邀请也不自动豁免20日。DBB默认完整图审成果后正式发标；许可等待合同与适用图审资料。日期由真实网络重算，不能强制所有项目四链路始终同样排序。

动捕A06保留原施工与设备接口；本次补齐施工条件检查、最终固定饰面、清洁移交。房间工作面与政府消防结果分开，具体顺序及待核依据见references/acoustic_finish_basis.md。

具体四链路日期与验证范围见RELEASE_VALIDATION.md。本修订不改变属地未核实的草案身份，不能作为施工批准。


## 声学房间完成面接口（2026-11-01）

默认仍交完整PDF、两份Timeline及条件/交付说明，耗时按实测报告。固定声学饰面在隐蔽记录齐备、本区域湿尘作业结束、基层及材料条件满足后安装，再进行清洁移交。固定饰面属于申报范围时须纳入竣工消防查验，不统一安排在政府验收之后。施工条件检查是1日的真实检查工作，不是政府验收或0天占位；安装5日与清洁1日是待校准PM规划量。既有光学设备与到货条件保留，本轮不扩展设备。依据见references/acoustic_finish_basis.md。
