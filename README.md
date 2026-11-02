# 办公室装修总控排程 · v4.0.0

默认一次图审、一次发证；原生MPP仅在Windows Project环境另行核验。

Office fit-out master scheduling for PMs. PDF-first, with optional native Microsoft Project export.

一句话或一张需求表，生成可审阅的总控计划。工序与日期由同一排程数据导出，现场事实、规划假设和待确认条件分开呈现。

```mermaid
flowchart LR
  A[Prompt 经 Agent 提取 / JSON / Excel] --> B[统一输入与来源核对]
  B --> C[行业与专项模块 / 工序依赖 / 日历排程]
  C --> D[PDF + PM/BD Timeline + 条件与交付说明]
  D --> E[成果可读性验证 / JSON交付结果]
  E --> F{显式 --mpp?}
  F -->|是| G[Project 原生导出与重开核验]
  F -->|否| H[完成 PDF 路线]
  G --> I[成功追加MPP；失败保留完整套件]
```

## Installation

Python 3.10+。安装依赖：`python -m pip install -r requirements.txt`。检查：`python -X utf8 tools/check_env.py`。
Word库为可选依赖，缺失时给TXT。PDF路线不需要Microsoft Project，Windows/Linux/macOS可使用兼容Python依赖；本版实际测试的平台以验证报告为准。原生MPP需要Windows、Microsoft Project及pywin32。

## Quickstart

### A · 自然语言，经Agent提取字段

对AI说：“用这个技能，上海3000平金融办公室，设计施工一体、邀请招标，2027年3月1日启动。”Agent调用：

```bash
python -X utf8 tools/run_contract.py --city 上海 --area 3000 --sector finance --delivery DB --bidding invite --start_date 2027-03-01 --output output/finance --project_name 上海金融办公
```

对照发包或竞赛现场改约束：

```bash
python -X utf8 tools/run_contract.py diff --base=examples/intake_dbb_public.json --target=examples/intake_db_invite.json
```

竞赛示例（项目名与洁净/NC/ESD/kVA 来自 intake，不是引擎默认值）：`examples/semiconductor_lab_suzhou.json`、`examples/aaa_gaming_mocap_idc.json`。

CLI本身接受明确字段，不把任意自然语言字符串当参数。

### B · Excel Intake

```bash
python -X utf8 tools/run_contract.py --input templates/PROJECT_INTAKE_EXAMPLE_SH_REVIEWED.xlsx --output output/excel
```

空表：`templates/PROJECT_INTAKE_TEMPLATE.xlsx`。输入中的full/lean不会自动启动Project。

### C · Delta re-solve

```bash
python -X utf8 tools/run_contract.py --base output/finance --addon_server_room required --specialist_area_sqm 150
```

仅修改提供的项，保留原结果，并在简要结论说明变更。别名`addon_server_room`映射`addon_a04`。

### D · Optional native MPP

```bash
python -X utf8 tools/run_contract.py --base output/finance --mpp
```

完整PDF套件先生成，MPP作为追加增强；状态由`output_route.mpp_status`给出。MPP失败不丢失已验证套件。`--output foo.pdf`或`foo.mpp`只改变文件stem，只有`--mpp`触发原生导出。

## Deliverables

|文件|用途|
|---|---|
|`<stem>.pdf`|A3横版总控甘特图|
|`<stem>_Milestones.pptx`|PM阶段汇报|
|`<stem>_BD_KeyMilestones.pptx`|BD关键日期时间轴|
|`<stem>_本次排程条件.docx`|有效输入、来源及假设；缺Word库用TXT|
|`<stem>_交付说明.docx`|结果、控制工作、目标与待确认项；缺Word库用TXT|
|`<stem>.mpp`|显式请求且保存重开核验通过后追加|
|`审计数据/`|计算、输入来源和验证记录|

stdout是单个JSON结果（含 `benchmark` 实测秒数）；过程日志与 `[BENCHMARK] PMP Scheduler Engine v4.0.0` 在 stderr。`DELIVERED`指套件已生成且通过文件检查，不代表属地批准或工程签认。`pdf_suite_seconds`与`elapsed_seconds`为本次实测，性能不作固定秒数承诺。典型案例「排程+PDF」目标 <1.5s；达不到时保留完整成果并如实报告实测。

施工许可前置（启动到许可，含设计）按 intake「是否依法必须招标」分两套：非必须招标 D&B 40–85 / DBB 55–95；必须招标 D&B 55–100 / DBB 80–115；差值均为 −15 至 +45（本轮第五轮修订，非法规）。法定环节固定：发标至截标≥20日、公示≥3日、上海审图5工作日、发证2工作日。隔墙到天花隐蔽按面积分档工作日；动捕相机在消防联调与消防验收之后上架。工期按面积与专项从 config 推导，法定窗口不压缩。

## Repository

```text
SKILL.md / README.md / VERSION.md
main.py                 排程执行
core/ experts/          工程规则、模块与计算
config/ profiles/       参数及适用条件
exporters/              PDF、PPTX与原生MPP
tools/                  唯一入口、环境、交付、打包及复验
templates/              Intake及WBS模板
references/ assets/     业务依据与文档样式
examples/               最小运行示例
```

## Scope and verification

支持办公室及已接入附属专项，五行业、DB/DBB、邀请/公开。全国入口与属地规则适配范围分开；不因行业自动添加实验设施。当地规则、供货承诺、资源配置与工作面仍由PM确认。未校准的工期不称行业定额。

参见[SKILL.md](SKILL.md)五条专业规则与[本版验证记录](RELEASE_VALIDATION.md)。当前没有自动学习平台、通用MPP任意转换或跨平台原生MPP承诺。

## Build

```bash
python -X utf8 tools/build_pack.py --out dist --kind full
python -X utf8 tools/verify_pack.py
# 或先打包再复验：
# python -X utf8 tools/build_pack.py --out dist --kind full
# python -X utf8 tools/verify_pack.py dist/office-programme-4.0.0-full.zip
```

打包按路径排序，排除dist、输出和缓存；内容摘要生成REVISION，PACK_MANIFEST与归档一致。逐文件对账请将包解压到独立清洁目录再用`--tree`。


## 工程接口修订（2026-10-31）

四链路采购按准入、RFP、投标准备、技术商务评审与合同连接；企业三轮须明确授权，formal_tender单列适用法定窗口，邀请也不自动豁免20日。DBB默认完整图审成果后正式发标；许可等待合同与适用图审资料。日期由真实网络重算，不能强制所有项目四链路始终同样排序。

动捕A06保留原施工与设备接口；本次补齐施工条件检查、最终固定饰面、清洁移交。房间工作面与政府消防结果分开，具体顺序及待核依据见references/acoustic_finish_basis.md。

具体四链路日期与验证范围见RELEASE_VALIDATION.md。本修订不改变属地未核实的草案身份，不能作为施工批准。


## 声学房间完成面接口（2026-11-01）

默认仍交完整PDF、两份Timeline及条件/交付说明，耗时按实测报告。固定声学饰面在隐蔽记录齐备、本区域湿尘作业结束、基层及材料条件满足后安装，再进行清洁移交。固定饰面属于申报范围时须纳入竣工消防查验，不统一安排在政府验收之后。施工条件检查是1日的真实检查工作，不是政府验收或0天占位；安装5日与清洁1日是待校准PM规划量。既有光学设备与到货条件保留，本轮不扩展设备。依据见references/acoustic_finish_basis.md。
