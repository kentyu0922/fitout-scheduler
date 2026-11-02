# -*- coding: utf-8 -*-
"""
exporters/ - 通用排程多端渲染与导出引擎 (Universal Schedule Exporters Component)
================================================================================
本包完全解耦于特定行业领域知识（不含办公室、厂房或零售的业务规则），
仅接收标准求解后的任务列表字典，提供多端物理文件渲染与报告导出能力：

1. MS Project COM 原生落盘 (.mpp + .xml)  -> build_mpp
2. executive 交互式决策看板 (.html + SVG) -> generate_html_report
3. 战略里程碑汇报演示文稿 (.pptx)          -> generate_pptx_milestones
4. 打印级甘特图与任务报表 (.pdf)           -> export_pdf
"""

from .mpp_renderer import build_mpp
from .export_html import generate_html_report
from .export_pptx import generate_pptx_milestones
from .export_pdf import export_pdf

__all__ = [
    "build_mpp",
    "generate_html_report",
    "generate_pptx_milestones",
    "export_pdf",
]
