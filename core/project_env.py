"""Detect whether this computer can produce a native MPP, without starting Project.

Native MPP output needs Windows, pywin32 and an installed Microsoft Project
whose COM server points to an existing executable. The check reads the
registry only; it never launches Project and never imports win32com.

PMP_MSPROJECT=present|absent overrides detection for tests and for users who
want to force the PDF route on a machine that has Project.
"""
from __future__ import annotations
import importlib.util
import os
import sys

PROGID = 'MSProject.Application'


def _registered_executable():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, PROGID + r'\CLSID') as key:
        clsid = winreg.QueryValue(key, None)
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, 'CLSID\\' + clsid + r'\LocalServer32') as key:
        command = winreg.QueryValue(key, None)
    command = command.strip()
    if command.startswith('"'):
        return command[1:].split('"', 1)[0]
    return command.split(' /', 1)[0].strip()


def ms_project_status() -> dict:
    forced = os.environ.get('PMP_MSPROJECT', '').strip().lower()
    if forced in ('present', 'absent'):
        return dict(available=forced == 'present', reason='PMP_MSPROJECT=' + forced)
    if sys.platform != 'win32':
        return dict(available=False, reason='非Windows系统，无法生成原生MPP')
    if importlib.util.find_spec('win32com') is None:
        return dict(available=False, reason='未安装pywin32（pip install pywin32）')
    try:
        exe = _registered_executable()
    except OSError:
        return dict(available=False, reason='本机未检测到Microsoft Project')
    if not os.path.isfile(exe):
        return dict(available=False, reason='Microsoft Project注册信息存在，但程序文件不存在：' + exe)
    return dict(available=True, reason='已检测到Microsoft Project：' + exe)
