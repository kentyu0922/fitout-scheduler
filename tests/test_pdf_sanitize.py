# -*- coding: utf-8 -*-
"""PDF 渲染缺字替换：不改排期结果，只保证画到页面上的字符当前字体有字形。"""
import datetime
from pathlib import Path

from exporters.export_pdf import _sanitize_text, build_gantt_pdf


def test_sanitize_replaces_middle_dot_exclaim_and_diamond():
    sub = _sanitize_text("示例办公室装修2000㎡  ·  GFA 2000㎡  ·  DB")
    assert "·" not in sub
    assert "｜" in sub
    assert "㎡" not in sub
    assert "m2" in sub
    assert "❗" not in _sanitize_text("❗ 产能覆盖提示")
    assert "！" in _sanitize_text("❗ 产能覆盖提示")
    cleaned = _sanitize_text("◆ 正式取得施工许可证")
    assert "◆" not in cleaned
    assert cleaned.startswith("[M]")
    assert "正式取得施工许可证" in cleaned


def test_gantt_pdf_subtitle_and_milestone_have_no_missing_glyphs(tmp_path):
    import fitz

    out = tmp_path / "glyph_check.pdf"
    tasks = [
        {
            "level": 2, "no": "1.1", "name": "开工许可",
            "dur": 1, "dur_unit": "日历天",
            "start": "2027-03-01", "finish": "2027-03-01",
            "sd": datetime.date(2027, 3, 1), "fd": datetime.date(2027, 3, 1),
            "pred": "", "milestone": False, "summary": True,
        },
        {
            "level": 3, "no": "1.1.1", "name": "正式取得施工许可证",
            "dur": 0, "dur_unit": "工作日",
            "start": "2027-03-01", "finish": "2027-03-01",
            "sd": datetime.date(2027, 3, 1), "fd": datetime.date(2027, 3, 1),
            "pred": "", "milestone": True, "summary": False,
        },
    ]
    build_gantt_pdf(
        "示例办公室装修2000㎡",
        tasks,
        str(out),
        meta={"project_name": "示例办公室装修2000㎡", "gfa_sqm": 2000, "delivery": "DB"},
    )
    doc = fitz.open(out)
    text = doc[0].get_text()
    doc.close()
    assert "·" not in text
    assert "❗" not in text
    assert "◆" not in text
    assert "｜" in text
    assert "正式取得施工许可证" in text
    assert "[M]" in text
