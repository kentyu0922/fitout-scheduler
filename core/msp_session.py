# -*- coding: utf-8 -*-
"""
core/msp_session.py — MS Project COM 会话管理与读取助手
========================================================
本模块负责与本机已安装的 Microsoft Project 通过 COM 自动化互操作。

设计要点（已针对 MS Project COM 已知坑位加固）：
  * 连接/启动本机 MS Project（DispatchEx 创建独立隐藏实例，不干扰用户已打开的文件）。
  * 读取工期一律用 GetField(FieldNameToFieldConstant("Duration"))，
    拿到的是项目原生单位串（如 "3 个工作日"），永不自己换算分钟数。
  * Project OLE dates are wall-clock values. Ignore pywin32's attached timezone;
    converting it shifts a 17:00 finish into the following date in China.
  * 上下文管理器自动 CoInitialize / CoUninitialize，避免 COM 套间泄漏。
  * pywin32 为 Windows-only 依赖：模块级 import 受保护，非 Windows 仍可 import 本模块
    （main.py --no_mpp 路径），真正触碰 COM 时才通过 require_win32() 明确报错。
"""

import os
import re
import sys
import datetime
from typing import Any, Dict, List, Optional
import logging

# pywin32 只在 Windows 存在；这里只记录不可用原因，不在 import 阶段抛错。
try:
    import win32com.client  # type: ignore
    import pythoncom  # type: ignore
    WIN32_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as _e:  # ModuleNotFoundError 亦为 ImportError 子类
    win32com = None  # type: ignore
    pythoncom = None  # type: ignore
    WIN32_IMPORT_ERROR = _e

PROGID = "MSProject.Application"

WIN32_AVAILABLE: bool = WIN32_IMPORT_ERROR is None


class MSProjectUnavailableError(RuntimeError):
    """MS Project COM 在当前环境不可用（非 Windows / 未装 pywin32 / 未装 Project）。

    只在真正调用 COM 路径（build_mpp / MSProjectSession / read_tasks）时抛出，
    绝不在模块 import 阶段抛出。调用方应改走 `--no_mpp`，并且不得宣称已写出 .mpp。
    """


def require_win32() -> None:
    """COM 路径入口守卫：pywin32 不可用时立刻、明确地失败。"""
    if WIN32_AVAILABLE:
        return
    raise MSProjectUnavailableError(
        "MS Project COM 不可用：需要 Windows + 桌面版 Microsoft Project + pywin32 "
        f"(当前平台 sys.platform={sys.platform!r}，import 失败: {WIN32_IMPORT_ERROR})。"
        "非 Windows 环境请使用 `python main.py --no_mpp`，仅交付 .pdf/.pptx，不得宣称已生成 .mpp。"
    )


def com_available() -> bool:
    """探测本机是否注册并可用 MS Project COM 对象。

    Returns:
        True 表示本机可驱动 MS Project（后续 import/update/export 才可用）。
        非 Windows / 无 pywin32 时直接返回 False（不抛错）。
    """
    if not WIN32_AVAILABLE:
        return False
    try:
        pythoncom.CoInitialize()
        app = win32com.client.DispatchEx(PROGID)
        _ = app.Version  # 触发真实连接
        try:
            app.Quit()
        except Exception as exc:

            logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

        pythoncom.CoUninitialize()
        return True
    except Exception:
        try:
            pythoncom.CoUninitialize()
        except Exception as exc:

            logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

        return False


class MSProjectSession:
    """上下文管理器：连接/启动本机 MS Project，提供打开/新建/保存/关闭。

    用法：
        with MSProjectSession() as sess:
            project = sess.open("x.mpp")
            ...
            sess.save(project, "x.mpp")
    """

    def __init__(self, visible: bool = False, display_alerts: bool = False):
        require_win32()
        pythoncom.CoInitialize()
        try:
            self.app = win32com.client.DispatchEx(PROGID)
        except Exception as e:
            pythoncom.CoUninitialize()
            raise RuntimeError(
                "无法启动 MS Project COM 对象。请确认：\n"
                "  (1) 本机已安装 Microsoft Project；\n"
                "  (2) Project 的 32/64 位与 Python 解释器位数一致；\n"
                "  (3) 已安装 pywin32（pip install pywin32）。\n"
                f"原始错误：{e}"
            )
        # 全程静默弹窗 + 隐藏窗口，保证自动化不被交互阻塞
        self.app.Visible = visible
        self.app.DisplayAlerts = display_alerts
        # 使用自动计算模式（pjAutomatic=0），确保约束和前置依赖在写入时即时重算。
        # 之前使用手动模式（pjManual=1）导致 MFO 约束无法通过全量 CPM 重算应用到任务日期。
        # 虽然 ScreenUpdating=False 可提升性能，但 Calculation 必须保持自动。
        try:
            self.app.Calculation = 0  # pjAutomatic
            self.app.ScreenUpdating = False
        except Exception as exc:

            logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)


    def open(self, path: str):
        """打开已有 .mpp / .mpt 文件，返回 ActiveProject。"""
        from core.com_retry import retry_busy
        retry_busy(lambda:self.app.FileOpenEx(os.path.abspath(path)))
        return retry_busy(lambda:self.app.ActiveProject)

    def new(self):
        """新建空白项目，返回 ActiveProject。"""
        self.app.FileNew()
        return self.app.ActiveProject

    def save(self, project, path: str):
        """保存到指定路径（已存在则覆盖）。"""
        project.SaveAs(os.path.abspath(path))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 恢复自动计算与刷新
        try:
            self.app.Calculation = 0  # pjAutomatic
            self.app.ScreenUpdating = True
        except Exception as exc:

            logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

        try:
            self.app.FileCloseEx(0)
        except Exception as exc:

            logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

        try:
            self.app.Quit()
        except Exception as exc:

            logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

        pythoncom.CoUninitialize()


# ---------------------- 读取助手（对抗已知 COM 坑） ----------------------

def field_const(app, name: str):
    """安全取字段常量（替代已失效的 CustomFieldRename 思路）。"""
    return app.FieldNameToFieldConstant(name)


def read_local_date(dt) -> str:
    """Read Project's OLE wall-clock date, without timezone conversion.

    Verified against GetField(Start/Finish) on native Project: 17:00+00:00
    represents the displayed local 17:00, not an instant to shift by eight hours.
    """
    if dt is None:
        return ""
    if hasattr(dt, "date"):
        try:
            return dt.date().isoformat()
        except Exception as exc:

            logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)

    return str(dt).split(" ")[0]


def read_field_str(app, task, name: str) -> str:
    """用 GetField 读取本地化显示串（工期/日期等），最稳。"""
    try:
        return task.GetField(field_const(app, name))
    except Exception:
        return ""


_DUR_RE = re.compile(r"(\d+)")

def parse_duration_days(field_str: str) -> Optional[int]:
    """从原生工期串（如 '3 个工作日'）提取工作日天数。"""
    if not field_str:
        return None
    m = _DUR_RE.search(field_str)
    return int(m.group(1)) if m else None

