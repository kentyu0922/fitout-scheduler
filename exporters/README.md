# 📦 exporters/ (通用排程多端渲染与导出引擎)

> **设计哲学**：**「稳定通用展示能力 vs 易变领域业务逻辑」彻底解耦**。  
> 本组件为纯粹的展示与落盘通用能力，**不含任何「中国办公室装修」或特定项目的业务假设**。  
> 任何排程系统（如未来开发的**零售门店开业排期、工业厂房建置排程、数据中心机电工程、甚至软件敏捷发布流水线**），只要输出标准任务数据结构，均可直接无缝挂载本项目完成五大格式导出。

---

## 1. 包含的核心渲染器

| 模块文件 | 产出格式 | 依赖技术栈 | 核心能力说明 |
| :--- | :--- | :--- | :--- |
| **`mpp_renderer.py`** | `.mpp` / `.xml` | pywin32, MS Project COM | 调用 MS Project 原生 COM 接口，精准设置项目开工起点、多层大纲级别 (OutlineLevel)、FS/SS/FF 前置关联关系、7天或工作日基准日历、节假日 Exceptions、注入 Text1/2/3 责任列，安全 CalculateProject 并落盘。 |
| **`msp_automation.py`** | COM 脚本控制 / `.pdf` | pywin32, win32com | 提供底层的 MS Project Session 会话生命周期管理、任务导入/更新、批量属性安全提取及打印控制。 |
| **`export_html.py`** | `.html` (单文件) | 纯 Python, 内嵌 SVG/JS/CSS | 遵循 executive 高管看板标准，渲染包含 Executive KPI 卡片、方案比选矩阵、战略里程碑时间轴、Level 2 甘特图、关键路径全景导览链及合规雷达的交互式看板。 |
| **`export_pptx.py`** | `.pptx` | python-pptx, lxml | 纯代码自动生成 16:9 极简暖白风格高层汇报级单页 Key Milestones Timeline 演示文稿，实现卡片防挤压排版与关键卡口高亮。 |
| **`export_pdf.py`** | `.pdf` | reportlab, pythoncom | 支持 A3 横向工程任务明细报表 (`--format=table`) 与带里程碑菱形的印刷级甘特图 (`--format=gantt`) 输出。 |

---

## 2. 通用数据输入契约 (Standard Task Schema)

本组件仅要求输入以下标准字典列表（不依赖任何专有类或实体对象）：

```json
[
  {
    "id": 1,
    "name": "任务或阶段名称",
    "outline_level": 2,
    "duration_days": 5,
    "start": "2026-09-01",
    "finish": "2026-09-05",
    "predecessors": "2FS,3SS+2d",
    "milestone": false,
    "critical": true,
    "total_slack_days": 0,
    "responsibility": {
      "unit": "机电总包",
      "person": "暖通工程师",
      "flag": "RESP"
    }
  }
]
```

---

## 3. 跨项目复用接入示例 (Plug & Play)

当开发新的排程应用时（如 `retail-store-scheduler` 或 `datacenter-scheduler`）：

```python
from exporters import build_mpp, generate_html_report, generate_pptx_milestones

# 1. 业务端独立进行 CPM 计算或拓扑排序，得到 tasks_solved 列表
# 2. 直接调用 exporters 组件完成多端渲染
build_mpp(
    project_title="某品牌全国旗舰店建置工程",
    project_start=datetime.date(2026, 10, 1),
    tasks=tasks_solved,
    output_mpp_path="output/flagship_store.mpp"
)

generate_html_report(
    project_title="某品牌全国旗舰店建置工程",
    project_meta={"city": "成都", "area": 3000, "delivery": "DB"},
    tasks=tasks_solved,
    output_html_path="output/flagship_store.html"
)
```
