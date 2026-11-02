# -*- coding: utf-8 -*-
"""Unified PDF export for .mpp schedule plans.

Two output formats:
  --format=gantt  : A3 landscape executive gantt chart (WBS + task bars + milestone diamonds + holiday bands)
  --format=table  : A3 landscape detailed data table (序号/任务名称/工期/开始/完成/前置)

Usage:
  python export_pdf.py                          # default: gantt
  python export_pdf.py --format=table
  python export_pdf.py --format=gantt --mpp path/to/file.mpp --out path/to/out.pdf
"""
import os
from pathlib import Path
import sys
import argparse
import datetime
import math
import re

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception as exc:

        logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception as exc:

        logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)


from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Table,
                                TableStyle, Paragraph)
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import logging

# ---- paths ----
BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MPP = os.path.join(BASE, "output_mpp", "Shanghai_Xuhui_1000_DnB.mpp")
DEFAULT_TITLE = "上海徐汇区 1000㎡ D&B 办公工装项目"

# ---- P1-01: explicit milestone semantics (shared with mpp_renderer) ----
try:
    from core._common import (UNRESOLVED_MARK, is_true_milestone,
                              is_unresolved_placeholder)
except ImportError:  # 兼容独立运行 exporters/
    sys.path.insert(0, os.path.dirname(BASE))
    from core._common import (UNRESOLVED_MARK, is_true_milestone,
                              is_unresolved_placeholder)

# ---- shared font registration (lazy) ----
FONT = r"C:\Windows\Fonts\msyh.ttc"          # 保留旧常量名（Windows 首选：微软雅黑）
FONT_CN = "CN"                                # 实际注册的常规字体名
FONT_CN_BOLD = "CN-Bold"                      # 实际注册的粗体字体名
FONT_SOURCE = None                            # 解析结果说明（用于日志/测试）
_FONTS_READY = False

# (路径, 常规 subfontIndex, 粗体 subfontIndex)
_FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\Deng.ttf", 0, 0),
    (FONT, 0, 1),                                                   # Windows 微软雅黑
    (r"C:\Windows\Fonts\simhei.ttf", 0, 0),                          # Windows 黑体
    ("/System/Library/Fonts/PingFang.ttc", 0, 0),                    # macOS
    ("/System/Library/Fonts/STHeiti Light.ttc", 0, 0),               # macOS (older)
    ("/Library/Fonts/Arial Unicode.ttf", 0, 0),                      # macOS (Office)
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0, 0),        # Debian/Ubuntu fonts-wqy-microhei
    ("/usr/share/fonts/wqy-microhei/wqy-microhei.ttc", 0, 0),        # Fedora/Arch
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0, 0),
    ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 0, 0),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0, 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0, 0),
]


def _try_register_ttf(path, idx_regular, idx_bold):
    """尝试用一个字体文件注册 CN / CN-Bold；成功返回 True，失败返回 False。"""
    if not path or not os.path.exists(path):
        return False
    try:
        pdfmetrics.registerFont(TTFont("CN", path, subfontIndex=idx_regular))
        try:
            pdfmetrics.registerFont(TTFont("CN-Bold", str(Path(path).with_name("Dengb.ttf")) if os.path.basename(path).lower()=="deng.ttf" else path, subfontIndex=idx_bold))
        except Exception:
            pdfmetrics.registerFont(TTFont("CN-Bold", path, subfontIndex=idx_regular))
        return True
    except Exception:
        return False


def _ensure_fonts():
    """惰性注册中文字体。幂等；只在真正构建 PDF 时调用，import 阶段零副作用。"""
    global _FONTS_READY, FONT_CN, FONT_CN_BOLD, FONT_SOURCE
    if _FONTS_READY:
        return FONT_CN, FONT_CN_BOLD

    candidates = []
    env_font = os.environ.get("PMP_PDF_FONT")
    if env_font:
        candidates.append((env_font, 0, 0))
    candidates.extend(_FONT_CANDIDATES)

    for path, i_reg, i_bold in candidates:
        if _try_register_ttf(path, i_reg, i_bold):
            FONT_CN, FONT_CN_BOLD = "CN", "CN-Bold"
            FONT_SOURCE = path
            break
    else:
        # 兜底：reportlab 内建 CJK CID 字体（Identity-H）
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        FONT_CN = FONT_CN_BOLD = "STSong-Light"
        FONT_SOURCE = "reportlab builtin CID font STSong-Light (no CJK TTF found)"

    _FONTS_READY = True
    return FONT_CN, FONT_CN_BOLD


# ---- text sanitization for PDF safety (no missing glyph boxes) ----
# STSong-Light (UniGB-UCS2-H) 与 DroidSansFallbackFull 均不含 U+00B7「·」、
# U+2757「❗」、U+25C6「◆」等字形；副标题分隔与里程碑前缀会渲染成缺字方框。
# 替换为上述字体集合里确实有的字符（全角「｜」、全角「！」、ASCII「[M]」/「m2」）。
_EMOJI_RE = re.compile(r"[\U00010000-\U0010ffff\u2600-\u2604\u2606-\u27bf\ufe00-\ufe0f]+")
_PDF_UNSAFE_REPLACEMENTS = (
    ("⭐️", "★"),
    ("⭐", "★"),
    ("✔️", "√"),
    ("✔", "√"),
    ("❌", "×"),
    ("▶️", ">"),
    ("◀️", "<"),
    ("⚠️", "！"),
    ("⚠", "！"),
    ("❗", "！"),
    ("◆", "[M]"),
    ("·", "｜"),
    ("・", "｜"),
    ("･", "｜"),
    ("㎡", "m2"),
    ("…", "..."),
)


def _sanitize_text(text: str, *, strip: bool = True) -> str:
    """清洗文本，避免特殊字符因当前 PDF 字体缺乏 glyph 渲染为方块 (□)。"""
    if not text:
        return ""
    s = str(text)
    for src, dst in _PDF_UNSAFE_REPLACEMENTS:
        if src in s:
            s = s.replace(src, dst)
    s = _EMOJI_RE.sub("", s)
    return s.strip() if strip else s


def _patch_canvas_font_text(canvas_obj):
    """所有落在 PDF 上的 draw*String 都走同一套缺字替换。"""
    def _wrap(fn):
        def wrapped(x, y, text, *args, **kwargs):
            return fn(x, y, _sanitize_text(text, strip=False), *args, **kwargs)
        return wrapped
    canvas_obj.drawString = _wrap(canvas_obj.drawString)
    canvas_obj.drawCentredString = _wrap(canvas_obj.drawCentredString)
    canvas_obj.drawRightString = _wrap(canvas_obj.drawRightString)
    return canvas_obj


# ============================================================
# Shared: read tasks from .mpp via COM
# ============================================================
def read_tasks(mpp_path):
    """Read all tasks (including summary) from an .mpp file.

    Returns (title, tasks) where each task dict carries fields needed by
    both the table and gantt builders.

    Windows-only（MS Project COM）。非 Windows 调用会抛 MSProjectUnavailableError；
    pywin32 只在这里延迟 import，保证本模块可在任意平台被 import。
    """
    try:
        from core.msp_session import require_win32
    except ImportError:  # 独立运行 exporters/ 时的兜底
        sys.path.insert(0, os.path.dirname(BASE))
        from core.msp_session import require_win32
    require_win32()
    import pythoncom  # noqa: WPS433 — lazy, Windows-only
    import win32com.client  # noqa: WPS433 — lazy, Windows-only

    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("MSProject.Application")
    app.Visible = False
    app.DisplayAlerts = False
    # ReadOnly = True 打开，避免并发锁与模态对话框
    app.FileOpenEx(os.path.abspath(mpp_path), True)
    proj = app.ActiveProject
    title = (getattr(proj, "Title", "") or "").strip()

    # 高速构建 ID -> WBS (OutlineNumber) 映射字典，避免逐个查询 PredecessorTasks 的巨量 COM RPC 开销
    id_to_wbs = {}
    for t in proj.Tasks:
        if t is not None:
            id_to_wbs[t.ID] = (getattr(t, "OutlineNumber", "") or "").strip() or str(t.ID)

    tasks = []
    for t in proj.Tasks:
        if t is None:
            continue
        try:
            lvl = int(t.OutlineLevel)
        except Exception:
            lvl = 1
        onum = (getattr(t, "OutlineNumber", "") or "").strip() or str(t.ID)
        raw_name = t.Name or ""
        clean_name = _sanitize_text(raw_name)

        dur_min = t.Duration if t.Duration is not None else 0
        dur_days = int(round(dur_min / 480)) if dur_min else 0

        # 安全提取本地化日期，避免时区偏差
        st_val = t.Start
        fn_val = t.Finish
        sd = fd = None
        st_str = fn_str = ""
        if st_val:
            try:
                sd = st_val.date() if hasattr(st_val, "date") else datetime.date.fromisoformat(str(st_val)[:10])
                st_str = sd.isoformat()
            except Exception as exc:

                logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

        if fn_val:
            try:
                fd = fn_val.date() if hasattr(fn_val, "date") else datetime.date.fromisoformat(str(fn_val)[:10])
                fn_str = fd.isoformat()
            except Exception as exc:

                logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)


        # 高速解析前置依赖项 (Predecessors 字符串映射为 WBS 编号)
        p_raw = str(getattr(t, "Predecessors", "") or "").strip()
        preds = []
        if p_raw:
            for part in p_raw.split(","):
                part = part.strip()
                m = re.match(r"^(\d+)", part)
                if m:
                    tid = int(m.group(1))
                    wbs_mapped = id_to_wbs.get(tid, str(tid))
                    suffix = part[len(m.group(1)):]
                    preds.append(f"{wbs_mapped}{suffix}")
                else:
                    preds.append(part)

        is_ms = bool(getattr(t, "Milestone", False))
        is_sum = bool(getattr(t, "Summary", False))

        # P1-01: an UNRESOLVED conditional activity is written to MPP with a
        # "[未决] " name prefix and Milestone=False (see core/_common). Never
        # re-infer a milestone from duration==0 for such a placeholder; only
        # genuine zero-duration non-summary tasks become milestones.
        unresolved = clean_name.startswith(UNRESOLVED_MARK)
        if unresolved:
            is_ms = False
        elif not is_sum and dur_days == 0 and sd and fd:
            is_ms = True

        tasks.append({
            "level": lvl,
            "no": onum,
            "name": clean_name,
            "dur": dur_days,
            "start": st_str,
            "finish": fn_str,
            "sd": sd,
            "fd": fd,
            "pred": ", ".join(preds),
            "milestone": is_ms,
            "summary": is_sum,
        })

    app.FileCloseEx(0)
    app.Quit()
    pythoncom.CoUninitialize()
    return title, tasks


# ============================================================
# Format 1: Table PDF (Detailed Print Matrix)
# ============================================================
def build_table_pdf(tasks, out_path, title=DEFAULT_TITLE):
    _ensure_fonts()
    doc = BaseDocTemplate(out_path, pagesize=landscape(A3),
                          leftMargin=36, rightMargin=36, topMargin=70, bottomMargin=58,
                          title=title)
    usable = doc.width
    fixed = 46 + 64 + 80 + 80 + 90
    name_w = usable - fixed
    colWidths = [46, name_w, 64, 80, 80, 90]

    header_style = ParagraphStyle("hdr", fontName=FONT_CN_BOLD, fontSize=9,
                                   leading=11, textColor=colors.HexColor("#1A1A1A"), alignment=TA_CENTER)
    name_base = ParagraphStyle("nb", fontName=FONT_CN, fontSize=8.5, leading=11,
                               textColor=colors.HexColor("#1F1F1F"), alignment=TA_LEFT)
    name_bold = ParagraphStyle("nb_b", parent=name_base, fontName=FONT_CN_BOLD)
    name_ms = ParagraphStyle("nb_ms", parent=name_bold, textColor=colors.HexColor("#1F1F1F"))
    cell_center = ParagraphStyle("cc", fontName=FONT_CN, fontSize=8.5, leading=11,
                                 textColor=colors.HexColor("#1F1F1F"), alignment=TA_CENTER)
    cell_ms = ParagraphStyle("cc_ms", parent=cell_center, fontName=FONT_CN_BOLD, textColor=colors.HexColor("#1F1F1F"))

    def name_cell(task):
        indent = (task["level"] - 1) * 12
        if task["milestone"]:
            p_style = name_ms
        elif task["summary"]:
            p_style = name_bold
        else:
            p_style = name_base
        st = ParagraphStyle("n_%s" % task["no"], parent=p_style, leftIndent=indent)
        nm = _sanitize_text(task["name"])
        return Paragraph(escape(nm), st)

    header = [Paragraph("序号", header_style), Paragraph("任务与里程碑名称", header_style),
              Paragraph("工期", header_style), Paragraph("开始时间", header_style),
              Paragraph("完成时间", header_style), Paragraph("前置任务", header_style)]
    data = [header]
    for t in tasks:
        # 工期天数明确标识：里程碑显示【◆ 里程碑】，杜绝 0 天歧义
        if t["milestone"]:
            dur_p = Paragraph(_sanitize_text("◆ 里程碑"), cell_ms)
        elif t["summary"]:
            dur_p = Paragraph(f"<b>{t['dur']} 天</b>" if t["dur"] else "—", cell_center)
        elif t["dur"]:
            dur_p = Paragraph(f"{t['dur']} 天", cell_center)
        else:
            dur_p = Paragraph("—", cell_center)

        data.append([
            Paragraph(t["no"], cell_center),
            name_cell(t),
            dur_p,
            Paragraph(t["start"], cell_center),
            Paragraph(t["finish"], cell_center),
            Paragraph(t["pred"], cell_center),
        ])

    ts = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DEDFE1")),
        ("FONTNAME", (0, 0), (-1, 0), FONT_CN_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9C9C9")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFBFB")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ])
    table = Table(data, colWidths=colWidths, repeatRows=1)
    table.setStyle(ts)

    def on_page(canvas_obj, d):
        canvas_obj.saveState()
        _patch_canvas_font_text(canvas_obj)
        w, h = d.pagesize
        canvas_obj.setFont(FONT_CN_BOLD, 12)
        canvas_obj.setFillColor(colors.HexColor("#1A1A1A"))
        canvas_obj.drawString(36, h - 38, f"{title} — 全周期进度控制计划表")
        canvas_obj.setFont(FONT_CN, 8)
        canvas_obj.drawRightString(w - 36, h - 38,
                                   "导出基准：%s    第 %d 页" % (datetime.date.today().isoformat(), d.page))
        canvas_obj.setStrokeColor(colors.HexColor("#D9D6F0"))
        canvas_obj.setLineWidth(1.2)
        canvas_obj.line(36, h - 45, w - 36, h - 45)
        canvas_obj.setFont(FONT_CN, 7.5)
        canvas_obj.setFillColor(colors.HexColor("#64748B"))
        legend = ("图例说明：任务名称缩进表示 WBS 层级；里程碑工期明确标示【◆ 里程碑】（非普通零工期任务）；"
                  "工期按各任务日历计；日期取本次排程源数据，法定/物业事项须确认。")
        canvas_obj.drawString(36, 30, _sanitize_text(legend, strip=False))
        canvas_obj.setStrokeColor(colors.HexColor("#C9C9C9"))
        canvas_obj.line(36, 42, w - 36, 42)
        canvas_obj.restoreState()

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=on_page)])
    doc.build([table])


# ============================================================
# Format 2: Executive A3 Gantt PDF (High-End Visual Dashboard)
# ============================================================
# Professional Palette
BG_DARK = HexColor("#1F1F1F")          # charcoal
BG_HEADER_CARD = HexColor("#1F1F1F")
ACCENT_BLUE = HexColor("#1F1F1F")
ACCENT_SKY = HexColor("#D9D6F0")
HEADER_LINE = ACCENT_SKY
ACCENT_GOLD = HexColor("#1F1F1F")
TEXT_WHITE = colors.white
TEXT_MUTED = HexColor("#888888")       # Slate 400
TEXT_MAIN = HexColor("#1A1A1A")        # Slate 900
TEXT_SUB = HexColor("#888888")         # Slate 600

BORDER_LIGHT = HexColor("#DEDFE1")     # Slate 200
BORDER_DARK = HexColor("#888888")      # Slate 400
BORDER_HEADER = HexColor("#1F1F1F")    # Slate 700
GRID_MONTH = HexColor("#C9C9C9")       # Month vertical line

ZEBRA_EVEN = colors.white
ZEBRA_ODD = HexColor("#FAFBFB")        # Slate 50
ROW_SUM_BG = HexColor("#F7F5FB")       # summary row
ROW_MS_BG = HexColor("#FFFFFF")        # Soft Amber 50 for milestone

BAR_PHASE = HexColor("#1F1F1F")        # Dark Slate summary bracket
BAR_TASK_DEFAULT = HexColor("#1F1F1F")
BAR_PROCURE = HexColor("#D9D6F0")      # lavender gray

MS_STATUTORY = HexColor("#1F1F1F")
MS_GATE = HexColor("#1F1F1F")
MS_TENDER = HexColor("#1F1F1F")

HOLIDAY_BG = HexColor("#F7F5FB")       # holiday window
HOLIDAY_BORDER = HexColor("#C4B8E8")   # lavender


def build_gantt_pdf(title, tasks, out_path, meta=None):
    font_cn, font_cn_bold = _ensure_fonts()

    # 自动回滚补全缺少起止日期的 summary 节点
    for t in tasks:
        if t.get("summary") and (not t.get("sd") or not t.get("fd")):
            pfx = str(t.get("no", "")) + "."
            child_sd = [c["sd"] for c in tasks if str(c.get("no", "")).startswith(pfx) and c.get("sd")]
            child_fd = [c["fd"] for c in tasks if str(c.get("no", "")).startswith(pfx) and c.get("fd")]
            if child_sd and child_fd:
                t["sd"] = min(child_sd)
                t["fd"] = max(child_fd)
                t["start"] = t["sd"].isoformat()
                t["finish"] = t["fd"].isoformat()

    # 过滤具备有效日期的任务
    gantt_tasks = [t for t in tasks if t.get("sd") and t.get("fd")]
    if not gantt_tasks:
        print("[WARN] No valid tasks with start/finish dates for gantt chart")
        return

    # 项目全生命周期边界与宏观统计
    min_proj = min(t["sd"] for t in gantt_tasks)
    max_proj = max(t["fd"] for t in gantt_tasks)
    total_calendar_days = (max_proj.date() if hasattr(max_proj, "date") else max_proj).toordinal() - (min_proj.date() if hasattr(min_proj, "date") else min_proj).toordinal() + 1  # 起止日均计入，与汇总行一致
    total_workdays = total_calendar_days
    total_milestones = sum(1 for t in gantt_tasks if t["milestone"])

    W, H = landscape(A3)
    c = canvas.Canvas(out_path, pagesize=(W, H))
    _patch_canvas_font_text(c)

    margin_x = 28
    header_h = 46
    footer_h = 18
    axis_h = 24
    row_h = 16.0

    # 左侧 WBS 任务表格列定义 (总宽 446 pt)
    LEFT_COLS = [
        ("WBS", 0, 36, "C"),
        ("任务与里程碑控制项", 36, 214, "L"),
        ("工期", 250, 48, "C"),
        ("起始日", 298, 52, "C"),
        ("截止日", 350, 52, "C"),
        ("前置", 402, 44, "C"),
    ]
    LW = 446
    chart_x0 = margin_x + LW
    GW = W - margin_x - chart_x0

    # 甘特图右侧时间轴标尺：按整月对齐（从月初对齐到月末）
    ruler_start = datetime.date(min_proj.year, min_proj.month, 1)
    if max_proj.month == 12:
        ruler_end = datetime.date(max_proj.year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        ruler_end = datetime.date(max_proj.year, max_proj.month + 1, 1) - datetime.timedelta(days=1)
    total_cal_days = (ruler_end - ruler_start).days + 1
    pxd = GW / total_cal_days

    y_top = H - header_h - axis_h
    y_bot = footer_h + 8
    max_rows = int((y_top - y_bot) / row_h)

    today_str = datetime.date.today().isoformat()

    # 智能分页：确保行高充足（16.0 pt），平均分布在 3 页中，杜绝突兀截断
    if 70 < len(gantt_tasks) <= 120:
        target_pages = 3
        chunk_size = (len(gantt_tasks) + target_pages - 1) // target_pages
        chunk_size = min(chunk_size, max_rows)
        pages = [gantt_tasks[i:i + chunk_size] for i in range(0, len(gantt_tasks), chunk_size)]
    else:
        pages = [gantt_tasks[i:i + max_rows] for i in range(0, len(gantt_tasks), max_rows)]

    for pi, chunk in enumerate(pages, 1):
        # ------------------------------------------------------------
        # 1. 顶部高管看板 (Executive Header Banner)
        # ------------------------------------------------------------
        c.setFillColor(colors.white)
        c.rect(0, H - header_h, W, header_h, fill=1, stroke=0)

        c.setStrokeColor(HEADER_LINE)
        c.setLineWidth(1.8)
        c.line(0, H - header_h, W, H - header_h)

        c.setFillColor(BG_DARK)
        c.setFont(font_cn_bold, 14)
        display_title = title if "进度计划" in title else f"{title} — 全周期控制进度计划甘特图"
        if ((meta or {}).get('input_contract') or {}).get('project',{}).get('authority_draft') and not display_title.startswith('【诊断草案'):
            display_title = '【诊断草案·报审路径待核实】' + display_title
        c.drawString(margin_x, H - 22, display_title)
        meta = meta or {}
        bits = []
        if meta.get("project_name"):
            bits.append(str(meta["project_name"]))
        gfa = meta.get("gfa_sqm") or meta.get("area")
        if gfa:
            bits.append(f"GFA {gfa}㎡")
        if meta.get("cleanroom_class"):
            bits.append("洁净 " + str(meta["cleanroom_class"]))
        if meta.get("nc_rating"):
            bits.append("NC " + str(meta["nc_rating"]))
        if meta.get("esd_level"):
            bits.append("ESD " + str(meta["esd_level"]))
        if meta.get("power_kva") not in (None, ""):
            bits.append(str(meta["power_kva"]) + " kVA")
        if meta.get("delivery"):
            bits.append(str(meta["delivery"]))
        if bits:
            c.setFillColor(TEXT_SUB)
            c.setFont(font_cn, 8)
            c.drawString(margin_x, H - 36, " ｜ ".join(bits))
        if meta.get("schedule_deficit"):
            c.setFillColor(colors.HexColor("#1F1F1F"))
            c.setFont(font_cn_bold, 8)
            c.drawString(margin_x + 320, H - 36, str(meta["schedule_deficit"].get("banner") or ""))
        if meta.get("equipment_lead_warnings") or meta.get("incomplete_leads"):
            c.setFillColor(colors.HexColor("#1F1F1F"))
            c.setFont(font_cn_bold, 8)
            c.drawRightString(W - margin_x - 70, H - 36, "交货期未填，不完整")
        logo = os.path.join(os.path.dirname(BASE), "assets", "brand-mark.png")
        if os.path.exists(logo):
            c.drawImage(logo, W - margin_x - 50, H - 40, width=50, height=30, preserveAspectRatio=True, mask="auto")

        # Client chart: keep conditions, assumptions and engine diagnostics in the
        # separate delivery brief. No dashboard cards or internal-source labels.
        source_label = "进度计划"
        c.setFillColor(TEXT_SUB)
        c.setFont(font_cn, 7)
        c.drawRightString(W - margin_x - 65, H - 24, f"第 {pi} 页 / 共 {len(pages)} 页")

        # ------------------------------------------------------------
        # 2. 左侧数据表头
        # ------------------------------------------------------------
        c.setFillColor(ACCENT_SKY)
        c.rect(margin_x, y_top, LW, axis_h, fill=1, stroke=0)
        c.setStrokeColor(BORDER_HEADER)
        c.setLineWidth(0.8)
        c.rect(margin_x, y_top, LW, axis_h, fill=0, stroke=1)

        c.setFillColor(BG_DARK)
        c.setFont(font_cn_bold, 8)
        for col_name, cx, cw, align in LEFT_COLS:
            if align == "C":
                c.drawCentredString(margin_x + cx + cw / 2, y_top + 7, col_name)
            else:
                c.drawString(margin_x + cx + 4, y_top + 7, col_name)

        # 表格与甘特图纵向分割线
        c.setStrokeColor(BORDER_DARK)
        c.setLineWidth(1.0)
        c.line(chart_x0, y_bot, chart_x0, y_top + axis_h)

        # ------------------------------------------------------------
        # 3. 右侧时间轴标尺 (Timeline Ruler)
        # ------------------------------------------------------------
        c.setFillColor(HexColor("#EAF3F5"))
        c.rect(chart_x0, y_top, GW, axis_h, fill=1, stroke=0)
        c.setStrokeColor(BORDER_DARK)
        c.setLineWidth(0.8)
        c.line(chart_x0, y_top + axis_h, chart_x0 + GW, y_top + axis_h)
        c.line(chart_x0, y_top, chart_x0 + GW, y_top)
        c.line(chart_x0, y_top + axis_h / 2, chart_x0 + GW, y_top + axis_h / 2)

        curr = ruler_start
        while curr <= ruler_end:
            if curr.month == 12:
                next_m = datetime.date(curr.year + 1, 1, 1)
            else:
                next_m = datetime.date(curr.year, curr.month + 1, 1)
            m_end = next_m - datetime.timedelta(days=1)

            mx0 = chart_x0 + (curr - ruler_start).days * pxd
            mx1 = chart_x0 + (next_m - ruler_start).days * pxd
            mw = mx1 - mx0

            # 上层：年-月标签
            c.setFillColor(BG_DARK)
            c.setFont(font_cn_bold, 7.5)
            c.drawCentredString(mx0 + mw / 2, y_top + axis_h / 2 + 3.5, f"{curr.year}年{curr.month:02d}月")

            # 下层：上/中/下旬刻度 (01, 11, 21)
            c.setFont(font_cn, 6.5)
            c.setFillColor(TEXT_SUB)
            for day_val in [1, 11, 21]:
                if day_val <= m_end.day:
                    tx = chart_x0 + (datetime.date(curr.year, curr.month, day_val) - ruler_start).days * pxd
                    c.setStrokeColor(BORDER_LIGHT)
                    c.setLineWidth(0.4)
                    c.line(tx, y_top, tx, y_top + axis_h / 2)
                    c.drawCentredString(tx + 4, y_top + 2.5, f"{day_val:02d}")

            # 贯穿全图的月份基准网格线
            c.setStrokeColor(GRID_MONTH)
            c.setLineWidth(0.6)
            c.line(mx0, y_bot, mx0, y_top)

            curr = next_m

        c.line(chart_x0 + GW, y_bot, chart_x0 + GW, y_top + axis_h)

        # ------------------------------------------------------------
        # 4. 法定节假日/春节停工带 (CNY Shading Window)
        # ------------------------------------------------------------
        cny_start = datetime.date(2027, 2, 6)
        cny_end = datetime.date(2027, 2, 20)
        if ruler_start <= cny_end and ruler_end >= cny_start:
            hx0 = chart_x0 + (max(cny_start, ruler_start) - ruler_start).days * pxd
            hx1 = chart_x0 + (min(cny_end, ruler_end) - ruler_start).days * pxd
            hw = max(hx1 - hx0, 2)
            c.setFillColor(HOLIDAY_BG)
            c.rect(hx0, y_bot, hw, y_top - y_bot, fill=1, stroke=0)
            c.setStrokeColor(HOLIDAY_BORDER)
            c.setLineWidth(0.5)
            c.line(hx0, y_bot, hx0, y_top)
            c.line(hx1, y_bot, hx1, y_top)

            # 标尺上的清晰停工标签，完全不侵占任务区域
            c.setFillColor(BG_DARK)
            c.setFont(font_cn_bold, 6.5)
            c.drawCentredString((hx0 + hx1) / 2, y_top + 3.0, "春节休假停工带")

        # ------------------------------------------------------------
        # 5. 任务与里程碑条目渲染 (Task Rows)
        # ------------------------------------------------------------
        y = y_top
        for ri, t in enumerate(chunk):
            cy = y - row_h / 2

            is_phase = t["level"] <= 2 and t["summary"]
            is_stage = t["level"] == 3 and t["summary"]
            is_ms = t["milestone"]

            # 行背景条纹
            if is_phase:
                c.setFillColor(ROW_SUM_BG)
                c.rect(margin_x, y - row_h, LW, row_h, fill=1, stroke=0)
            elif is_ms:
                c.setFillColor(ROW_MS_BG)
                c.rect(margin_x, y - row_h, LW, row_h, fill=1, stroke=0)
            elif ri % 2 == 1:
                c.setFillColor(ZEBRA_ODD)
                c.rect(margin_x, y - row_h, LW, row_h, fill=1, stroke=0)
            else:
                c.setFillColor(ZEBRA_EVEN)
                c.rect(margin_x, y - row_h, LW, row_h, fill=1, stroke=0)

            # 行分割横线
            c.setStrokeColor(BORDER_LIGHT)
            c.setLineWidth(0.4)
            c.line(margin_x, y - row_h, margin_x + LW + GW, y - row_h)

            # 左表 1: WBS 序号
            c.setFont(font_cn_bold if is_phase else font_cn, 7.5)
            c.setFillColor(TEXT_MAIN if not is_ms else ACCENT_GOLD)
            c.drawCentredString(margin_x + LEFT_COLS[0][1] + LEFT_COLS[0][2] / 2, cy - 2.5, t["no"])

            # 左表 2: 任务与里程碑名称 (层级缩进)
            indent = min(28, (t["level"] - 1) * 7.5)
            avail_w = LEFT_COLS[1][2] - indent - 4
            name_str = t["name"]

            if is_ms and not name_str.startswith("[M]") and not name_str.startswith("◆"):
                name_str = "◆ " + name_str

            max_chars = max(6, int(avail_w / 7.2))
            if len(name_str) > max_chars:
                name_display = name_str[:max_chars - 1] + "…"
            else:
                name_display = name_str

            c.setFont(font_cn_bold if is_phase else font_cn, 7.5)
            c.setFillColor(TEXT_MAIN if not is_ms else HexColor("#1F1F1F"))
            c.drawString(margin_x + LEFT_COLS[1][1] + indent + 2, cy - 2.5, name_display)

            # 左表 3: 工期天数 (0天彻底修复：里程碑专用标识，阶段加粗显示汇总天数)
            if is_ms:
                dur_text = "◆ 里程碑"
                c.setFont(font_cn_bold, 7.0)
                c.setFillColor(ACCENT_GOLD)
            elif is_phase:
                dur_text = f"{t['dur']} {t.get('dur_unit','天')}"
                c.setFont(font_cn_bold, 7.5)
                c.setFillColor(TEXT_MAIN)
            else:
                dur_text = f"{t['dur']} {t.get('dur_unit','天')}" if t["dur"] else "—"
                c.setFont(font_cn, 7.5)
                c.setFillColor(TEXT_SUB)
            c.drawCentredString(margin_x + LEFT_COLS[2][1] + LEFT_COLS[2][2] / 2, cy - 2.5, dur_text)

            # 左表 4: 起始日 (关键里程碑明确展示起始日)
            c.setFont(font_cn_bold if is_phase else font_cn, 7.0)
            c.setFillColor(TEXT_MAIN if not is_ms else HexColor("#1F1F1F"))
            c.drawCentredString(margin_x + LEFT_COLS[3][1] + LEFT_COLS[3][2] / 2, cy - 2.5, t["start"])

            # 左表 5: 截止日 (关键里程碑明确展示截止日)
            c.drawCentredString(margin_x + LEFT_COLS[4][1] + LEFT_COLS[4][2] / 2, cy - 2.5, t["finish"])

            # 左表 6: 前置任务
            pred_txt = t["pred"]
            if len(pred_txt) > 8:
                pred_txt = pred_txt[:7] + "…"
            c.setFont(font_cn, 6.5)
            c.setFillColor(TEXT_SUB)
            c.drawCentredString(margin_x + LEFT_COLS[5][1] + LEFT_COLS[5][2] / 2, cy - 2.5, pred_txt)

            # ------------------------------------------------------------
            # 右侧甘特图图形元素 (Bars & Diamonds)
            # ------------------------------------------------------------
            sx = chart_x0 + (t["sd"] - ruler_start).days * pxd
            ex = chart_x0 + (t["fd"] - ruler_start).days * pxd

            if is_ms:
                # 关键里程碑菱形节点 (Milestone Diamond)
                mx = sx
                dd = 4.5  # 半径 4.5 pt

                nm = t["name"]
                if any(k in nm for k in ["消防", "施工许可", "图审", "质安监", "LOA", "合同"]):
                    ms_color = MS_STATUTORY
                elif any(k in nm for k in ["启动", "Takeover", "进场", "物理竣工", "交付", "营业", "搬迁"]):
                    ms_color = MS_GATE
                else:
                    ms_color = MS_TENDER

                p = c.beginPath()
                p.moveTo(mx, cy - dd)
                p.lineTo(mx + dd, cy)
                p.lineTo(mx, cy + dd)
                p.lineTo(mx - dd, cy)
                p.close()
                c.setFillColor(ms_color)
                c.setStrokeColor(HexColor("#1F1F1F") if ms_color == MS_TENDER else HexColor("#1F1F1F"))
                c.setLineWidth(0.6)
                c.drawPath(p, fill=1, stroke=1)

                # 里程碑日期胶囊徽章 (Milestone Date Badge)
                date_label = t["sd"].isoformat()
                c.setFont(font_cn_bold, 6.5)
                tw = c.stringWidth(date_label, font_cn_bold, 6.5)
                badge_w = tw + 6
                badge_h = 9.5
                badge_y = cy - badge_h / 2

                if mx + 8 + badge_w <= chart_x0 + GW:
                    bx = mx + 7
                else:
                    bx = mx - 7 - badge_w

                c.setFillColor(colors.white)
                c.setStrokeColor(HexColor("#C9C9C9"))
                c.setLineWidth(0.4)
                c.roundRect(bx, badge_y, badge_w, badge_h, 2, fill=1, stroke=1)
                c.setFillColor(TEXT_MAIN)
                c.drawCentredString(bx + badge_w / 2, cy - 2.2, date_label)

            elif is_phase or is_stage:
                # 阶段汇总条 (Sleek Bracket Bar)
                bw = max(ex - sx, 3)
                bh = 4.0
                c.setFillColor(BAR_PHASE)
                c.rect(sx, cy - bh / 2 + 1, bw, bh, fill=1, stroke=0)

                # 下垂支架挂钩 (Downward Hooks)
                hook_h = 4.5
                c.setStrokeColor(BAR_PHASE)
                c.setLineWidth(1.4)
                c.line(sx, cy - bh / 2 + 1 + bh, sx, cy - bh / 2 + 1 - hook_h)
                c.line(ex, cy - bh / 2 + 1 + bh, ex, cy - bh / 2 + 1 - hook_h)

            else:
                # 常规施工/深化任务条 (Rounded Task Bar)
                bw = max(ex - sx, 2.5)
                bh = 6.0
                bar_color = BAR_TASK_DEFAULT
                if "采购" in t["name"] or "Lead Time" in t["name"]:
                    bar_color = BAR_PROCURE

                c.setFillColor(bar_color)
                c.roundRect(sx, cy - bh / 2, bw, bh, 1.8, fill=1, stroke=0)

                # 若宽度充裕，在任务条内嵌标注工期天数
                if bw >= 26:
                    c.setFillColor(TEXT_WHITE)
                    c.setFont(font_cn, 5.5)
                    c.drawCentredString((sx + ex) / 2, cy - 2.0, f"{t['dur']}d")

            y -= row_h

        # Clean client footer. Assumptions and legal-path limitations remain in
        # the independent delivery conditions, not a coloured warning banner.
        c.setFillColor(TEXT_SUB)
        c.setFont(font_cn, 7)
        c.drawRightString(W - margin_x, 8, f"第 {pi} 页 / 共 {len(pages)} 页")
        c.showPage()

    c.save()


def _normalize_tasks_from_dict(task_list):
    """Normalize raw solver task dicts into the format expected by PDF builders."""
    tasks = []
    from core.native_graph import prepare_native_graph
    task_list, _ = prepare_native_graph(task_list)
    from core.client_visibility import work_item_hide_reason
    visible = [t for t in task_list if t.get('client_visible') is not False and not work_item_hide_reason(t)]
    display_wbs = {}
    levels = [0,0,0,0,0,0]
    for t in visible:
        if t.get("client_visible") is False or work_item_hide_reason(t):
            continue
        lvl = int(t.get("outline_level", t.get("level", 3)))
        levels[lvl-1] += 1
        for level in range(lvl,len(levels)): levels[level]=0
        onum = str(t.get('wbs') or '.'.join(str(n) for n in levels[:lvl]))
        display_wbs[t['id']] = onum
        name = _sanitize_text(str(t.get("client_task_name") or t.get("name", "")))
        dur = t.get("duration_days", t.get("duration", 0))
        try:
            dur_days = int(round(float(dur))) if dur else 0
        except Exception:
            dur_days = 0

        start_val = t.get("start", "")
        finish_val = t.get("finish", "")
        start_str = str(start_val)[:10] if start_val else ""
        finish_str = str(finish_val)[:10] if finish_val else ""
        sd = fd = None
        if start_str and len(start_str) >= 10:
            try:
                sd = datetime.date.fromisoformat(start_str)
            except Exception as exc:

                logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

        if finish_str and len(finish_str) >= 10:
            try:
                fd = datetime.date.fromisoformat(finish_str)
            except Exception as exc:

                logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)


        preds = str(t.get('predecessors', ''))
        import re
        is_sum = lvl <= 2 or bool(t.get("summary", False))
        # P1-01: explicit milestone semantics. A summary is never a milestone;
        # a leaf is a milestone only per is_true_milestone() (explicit flag or
        # genuine zero duration AND not an UNRESOLVED conditional placeholder).
        is_ms = (not is_sum) and is_true_milestone(t)

        # 汇总行在引擎里是 0 天。这里用起止日的日历跨度，不再乘 5/7 冒充工作日。
        if is_sum and sd and fd:
            dur_days = max(1, (fd - sd).days + 1)

        tasks.append({
            "level": lvl, "no": onum, "name": name,
            "dur": dur_days, "dur_unit": "日历天" if is_sum or t.get("elapsed_window") else "工作日", "start": start_str, "finish": finish_str,
            "sd": sd, "fd": fd,
            "pred": preds,
            "milestone": is_ms,
            "summary": is_sum,
        })
    for row in tasks:
        row['pred'] = re.sub(r'(^|,)(\d+)', lambda m: m[1]+display_wbs.get(int(m[2]), '详见明细'), row['pred'])
        if '详见明细' in row['pred']:row['pred']='详见明细'
    return tasks


def export_pdf(mpp_or_tasks=DEFAULT_MPP, out_path: str = None, format: str = "gantt", title: str = None, meta: dict = None) -> str:
    """Export schedule to PDF format ('gantt' or 'table').
    Accepts either an .mpp file path (str) or a list of task dicts (list).
    Default format is 'gantt' (A3 Executive Gantt Chart).
    """
    if isinstance(mpp_or_tasks, (list, tuple)):
        tasks = _normalize_tasks_from_dict(mpp_or_tasks)
        if not title:
            title = DEFAULT_TITLE
        if not out_path:
            suffix = "_gantt" if format == "gantt" else ""
            out_path = os.path.join(BASE, "output_mpp", f"Schedule_Report{suffix}.pdf")
    else:
        mpp_path = str(mpp_or_tasks)
        if not out_path:
            base, _ = os.path.splitext(mpp_path)
            suffix = "_gantt" if format == "gantt" else ""
            out_path = base + suffix + ".pdf"

        mpp_title, tasks = read_tasks(mpp_path)
        if not title:
            title = mpp_title or DEFAULT_TITLE

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    if format == "table":
        build_table_pdf(tasks, out_path, title=title)
    else:
        build_gantt_pdf(title, tasks, out_path, meta=meta)
    return out_path


# ============================================================
# CLI entry point
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Export .mpp schedule to executive PDF")
    parser.add_argument("--format", choices=["gantt", "table"], default="gantt",
                        help="PDF output format (default: gantt)")
    parser.add_argument("--mpp", default=DEFAULT_MPP,
                        help="Path to .mpp file (default: Shanghai_Xuhui_1000_DnB.mpp)")
    parser.add_argument("--out", default=None,
                        help="Output PDF path (default: auto-derived from --mpp and --format)")
    parser.add_argument("--city", default="杭州市", help="City for metadata card")
    parser.add_argument("--area", type=float, default=860.0, help="Area (sqm) for metadata card")
    args = parser.parse_args()

    if args.out:
        out_path = args.out
    else:
        base, _ = os.path.splitext(args.mpp)
        suffix = "_gantt" if args.format == "gantt" else ""
        out_path = base + suffix + ".pdf"

    print("Reading MPP:", args.mpp)
    title, tasks = read_tasks(args.mpp)
    if not title:
        title = DEFAULT_TITLE
    print("Project title:", title, "| tasks:", len(tasks))

    meta = {"city": args.city, "area": args.area}
    if args.format == "table":
        build_table_pdf(tasks, out_path, title=title)
    else:
        build_gantt_pdf(title, tasks, out_path, meta=meta)

    print("PDF written:", out_path, "| size:", os.path.getsize(out_path), "bytes")


if __name__ == "__main__":
    main()

