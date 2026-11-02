# -*- coding: utf-8 -*-
"""
core/msp_automation.py — 三大操作：任务导入 / 进度更新 / 报表导出
================================================================
依赖本机已安装 MS Project（COM 自动化）。所有日期/工期读取均按
core.msp_session 提供的抗坑助手实现。

输入/输出格式说明见 docs_and_sops/common/ms_project_com_guide.md
"""

import os
import json
import csv
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from core.msp_session import (
    MSProjectSession, com_available, field_const,
    read_local_date, read_field_str, parse_duration_days,
)
# 化繁为简：从 _common 导入共享常量与任务创建函数
from core._common import CONSTRUCTION_CAL_NAME, create_task_with_outline

logger = logging.getLogger(__name__)

WORK_WEEKEND_KEYWORDS = ["施工", "Phase 4", "Phase 6", "动工", "苏州DBB", "洁净", "冷却"]

# 报表默认字段 (标签, 属性名)；属性名用于从 Task 对象取值
DEFAULT_FIELDS: List[Tuple[str, str]] = [
    ("ID", "ID"),
    ("名称", "Name"),
    ("大纲级别", "OutlineLevel"),
    ("开始", "Start"),
    ("完成", "Finish"),
    ("工期", "Duration"),
    ("前置任务", "Predecessors"),
    ("完成百分比", "PercentComplete"),
    ("实际开始", "ActualStart"),
    ("实际完成", "ActualFinish"),
    ("总时差", "TotalSlack"),
    ("关键", "Critical"),
    ("执行单位", "Text1"),
    ("责任人", "Text2"),
    ("责任标识", "Text3"),
]


# ============================ 任务导入 ============================

def _load_tasks(json_path: str) -> List[Dict[str, Any]]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("tasks", "Tasks", "wbs", "WBS"):
            if k in data and isinstance(data[k], list):
                return data[k]
    raise ValueError("JSON 中未找到任务列表（期望顶层 list，或含 tasks/Tasks 字段）")


def import_tasks(json_path: str, output_mpp: str,
                 project_title: Optional[str] = None,
                 project_start: Optional[str] = None,
                 calendar_exceptions: Optional[List[Dict]] = None,
                 work_weekend_keywords: Optional[List[str]] = None,
                 append: bool = False) -> str:
    """从任务 JSON 导入到 .mpp。

    Args:
        json_path: 任务 JSON（schema 见文档）。
        output_mpp: 输出 .mpp 路径。
        project_title: 项目标题（新建时）。
        project_start: 项目开工日 "YYYY-MM-DD"（新建时，可省略则用首个任务 start）。
        calendar_exceptions: 节假日 exceptions 列表（新建时注入）。
        work_weekend_keywords: 触发施工7天日历的关键词。
        append: True 时向已有 .mpp 追加任务；False 且文件已存在则覆盖重建。

    Returns:
        实际落盘的 .mpp 绝对路径。
    """
    tasks = _load_tasks(json_path)
    work_weekend_keywords = work_weekend_keywords or WORK_WEEKEND_KEYWORDS
    output_mpp = os.path.abspath(output_mpp)
    out_dir = os.path.dirname(output_mpp)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    existed = os.path.exists(output_mpp)
    if existed and not append:
        logger.warning(f"目标文件已存在且未指定 --append，将覆盖重建: {output_mpp}")
        os.remove(output_mpp)
        existed = False

    if not existed:
        # 新建：复用经真机验证的渲染器（含7天日历 + 节假日 + 大纲 + 前置）
        from core.mpp_renderer import build_mpp
        ps = project_start or (tasks[0].get("start") if tasks and tasks[0].get("start") else None)
        proj_start_dt = None
        if ps:
            try:
                proj_start_dt = datetime.datetime.strptime(ps, "%Y-%m-%d")
            except Exception as exc:

                logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

        title = project_title or "Imported Project"
        path, _ = build_mpp(
            project_title=title,
            project_start=proj_start_dt,
            tasks=tasks,
            calendar_exceptions=calendar_exceptions or [],
            output_mpp_path=output_mpp,
            # 原始 JSON 导入：任务来源就是调用方给的文件，"未带已解算日期"是正常输入，
            # 不该按「对客呈现」规则隐藏 —— 否则整份 JSON 会被过滤成空 .mpp
            # （2026-09-24 实测 4~25 个任务全部被 missing_start_or_finish 丢弃）。
            apply_client_visibility=False,
        )
        logger.info(f"[import] 新建项目并写入 {len(tasks)} 个任务 -> {path}")
        return path

    # 追加模式：打开已有文件，追加任务
    with MSProjectSession() as sess:
        project = sess.open(output_mpp)
        max_lvl = 1
        try:
            for i in range(1, project.Tasks.Count + 1):
                lv = project.Tasks(i).OutlineLevel
                if lv > max_lvl:
                    max_lvl = lv
        except Exception as exc:

            logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

        current_depth = [max_lvl]
        objs = {}
        for t in tasks:
            objs[t.get("id")] = create_task_with_outline(
                project, t, current_depth,
                construction_cal_name=CONSTRUCTION_CAL_NAME,
                work_weekend_keywords=work_weekend_keywords,
                logger=logger,
            )
        for t in tasks:
            preds = str(t.get("predecessors", "")).strip()
            if preds and int(t.get("outline_level", t.get("level", 3))) >= 3:
                obj = objs.get(t.get("id"))
                if obj:
                    try:
                        obj.Predecessors = preds
                    except Exception as e:
                        logger.warning(f"前置写入失败 [{t.get('id')} -> {preds}]: {e}")
        sess.save(project, output_mpp)
    logger.info(f"[import] 向已有文件追加 {len(tasks)} 个任务 -> {output_mpp}")
    return output_mpp


# ============================ 进度更新 ============================

def _load_progress(json_path: str) -> List[Dict[str, Any]]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("updates", "progress", "tasks"):
            if k in data and isinstance(data[k], list):
                return data[k]
    raise ValueError("进度 JSON 未找到更新列表（期望 list，或含 updates 字段）")


def update_progress(mpp_path: str, progress_json: str) -> Dict[str, int]:
    """用进度 JSON 更新 .mpp 中任务的完成度与实际日期。

    Args:
        mpp_path: 目标 .mpp。
        progress_json: 进度更新 JSON（schema 见文档）。

    Returns:
        {"matched": 命中并更新数, "skipped": 未匹配数}
    """
    entries = _load_progress(progress_json)
    mpp_path = os.path.abspath(mpp_path)
    if not os.path.exists(mpp_path):
        raise FileNotFoundError(mpp_path)

    stats = {"matched": 0, "skipped": 0}
    with MSProjectSession() as sess:
        project = sess.open(mpp_path)
        by_id: Dict[int, Any] = {}
        by_name: Dict[str, Any] = {}
        for i in range(1, project.Tasks.Count + 1):
            tk = project.Tasks(i)
            try:
                by_id[int(tk.ID)] = tk
            except Exception as exc:

                logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

            try:
                by_name[tk.Name] = tk
            except Exception as exc:

                logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)


        for e in entries:
            tk = None
            if "id" in e:
                try:
                    tk = by_id.get(int(e["id"]))
                except Exception as exc:

                    logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

            if tk is None and "name" in e:
                tk = by_name.get(e["name"])
            if tk is None:
                logger.warning(f"[update] 未匹配任务，已跳过: {e}")
                stats["skipped"] += 1
                continue

            # 完成百分比
            if e.get("mark_complete"):
                pc = 100
            elif "percent_complete" in e:
                pc = int(e["percent_complete"])
            else:
                pc = None
            if pc is not None:
                try:
                    tk.PercentComplete = pc
                except Exception as ex:
                    logger.warning(f"[update] 写入 %Complete 失败: {ex}")

            # 实际开始/完成
            for fld, key in (("ActualStart", "actual_start"), ("ActualFinish", "actual_finish")):
                if key in e and e[key]:
                    try:
                        d = datetime.datetime.strptime(e[key], "%Y-%m-%d")
                        setattr(tk, fld, d)
                    except Exception as ex:
                        logger.warning(f"[update] 写入 {fld} 失败: {ex}")
            stats["matched"] += 1
        sess.save(project, mpp_path)
    logger.info(f"[update] 完成：命中 {stats['matched']} / 跳过 {stats['skipped']}")
    return stats


# ============================ 报表导出 ============================

def _extract_rows(app, project, fields) -> List[Dict[str, str]]:
    rows = []
    for i in range(1, project.Tasks.Count + 1):
        tk = project.Tasks(i)
        row: Dict[str, str] = {}
        for label, attr in fields:
            try:
                if attr in ("Start", "Finish", "ActualStart", "ActualFinish"):
                    row[label] = read_local_date(getattr(tk, attr, None))
                elif attr == "Duration":
                    row[label] = read_field_str(app, tk, "Duration")
                elif attr == "TotalSlack":
                    row[label] = read_field_str(app, tk, "Total Slack")
                elif attr == "Critical":
                    row[label] = "是" if getattr(tk, "Critical", False) else "否"
                elif attr in ("Text1", "Text2", "Text3"):
                    row[label] = read_field_str(app, tk, attr)
                else:
                    row[label] = str(getattr(tk, attr, ""))
            except Exception:
                row[label] = ""
        rows.append(row)
    return rows


def export_report(mpp_path: str, out_path: str, fmt: str = "csv",
                  fields=None) -> str:
    """从 .mpp 导出任务报表。

    Args:
        mpp_path: 源 .mpp。
        out_path: 输出路径（扩展名随意，按 fmt 决定实质格式）。
        fmt: "csv" | "json" | "pdf"。
        fields: 自定义字段列表 [(标签, 属性)]，默认 DEFAULT_FIELDS。

    Returns:
        实际落盘的报告路径。
    """
    fields = fields or DEFAULT_FIELDS
    mpp_path = os.path.abspath(mpp_path)
    out_path = os.path.abspath(out_path)
    if not os.path.exists(mpp_path):
        raise FileNotFoundError(mpp_path)

    with MSProjectSession() as sess:
        project = sess.open(mpp_path)
        rows = _extract_rows(sess.app, project, fields)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    labels = [l for l, _ in fields]

    if fmt == "csv":
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=labels)
            w.writeheader()
            for r in rows:
                w.writerow(r)
    elif fmt == "json":
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    elif fmt == "pdf":
        _export_pdf(rows, labels, out_path)
    else:
        raise ValueError(f"不支持的格式: {fmt}（支持 csv / json / pdf）")
    logger.info(f"[export] 报表已导出({fmt}) -> {out_path}（{len(rows)} 行）")
    return out_path


def _export_pdf(rows, labels, out_path):
    """PDF 导出：依赖 reportlab，并使用内置中文 CID 字体避免乱码。"""
    try:
        from reportlab.lib.pagesizes import landscape, A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    except Exception as e:
        raise RuntimeError(
            "PDF 导出需要 reportlab。请在运行环境执行 `pip install reportlab` 后重试。原始错误：" + str(e)
        )
    # 注册内置中文字体（无需额外字体文件）
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    font_name = "STSong-Light"

    data = [labels] + [[r.get(c, "") for c in labels] for r in rows]
    doc = SimpleDocTemplate(out_path, pagesize=landscape(A4))
    table = Table(data, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f6")]),
    ]
    table.setStyle(TableStyle(style))
    doc.build([table])

