"""Check this computer before a run, and say in one line how to fix what is missing.

Fast: it only looks for installed packages and never imports them or starts
Microsoft Project. tools/run_contract.py calls it first; users can also run
`python -X utf8 tools/check_env.py` on its own.
"""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# (import name, pip package, what it is for)
REQUIRED = [
    ('reportlab', 'reportlab', 'PDF 甘特图'),
    ('pptx', 'python-pptx', 'Timeline 演示文稿'),
    ('openpyxl', 'openpyxl', 'Excel 输入表与输入假设表'),
    ('PIL', 'Pillow', '图片处理（Timeline 依赖）'),
    ('lxml', 'lxml', '文档结构处理（Timeline 依赖）'),
]
OPTIONAL = [
    ('docx', 'python-docx', 'Word 版交付说明（缺少时改出 .txt 说明）'),
]
MIN_PYTHON = (3, 10)


def check(include_mpp: bool = False) -> dict:
    missing = [dict(module=m, package=p, purpose=w) for m, p, w in REQUIRED
               if importlib.util.find_spec(m) is None]
    optional = [dict(module=m, package=p, purpose=w) for m, p, w in OPTIONAL
                if importlib.util.find_spec(m) is None]
    python_ok = sys.version_info[:2] >= MIN_PYTHON
    from core.project_env import ms_project_status
    project = ms_project_status() if include_mpp else dict(available=False, reason="默认PDF路线不检测或启动Project")
    packages = [x['package'] for x in missing + optional]
    mpp_needs = []
    if include_mpp and sys.platform == 'win32' and importlib.util.find_spec('win32com') is None:
        packages.append('pywin32')
        try:
            from core.project_env import _registered_executable
            import os
            if os.path.isfile(_registered_executable()):
                # Project is installed: pywin32 is the only thing between this computer and an MPP.
                mpp_needs.append('pywin32')
        except OSError:
            pass
    return dict(
        mpp_needs=mpp_needs,
        mpp_install_command=(f'"{sys.executable}" -m pip install pywin32') if mpp_needs else '',
        status='READY' if python_ok and not missing else 'MISSING',
        python=dict(version='.'.join(map(str, sys.version_info[:3])), ok=python_ok,
                    required='.'.join(map(str, MIN_PYTHON)) + ' 或以上'),
        missing_required=missing, missing_optional=optional,
        ms_project=project,
        install_command=(f'"{sys.executable}" -m pip install ' + ' '.join(packages)) if packages else '',
        # PowerShell needs the call operator before a quoted program path.
        install_command_powershell=(f'& "{sys.executable}" -m pip install ' + ' '.join(packages)) if packages else '')


def report(result: dict) -> str:
    lines = []
    if not result['python']['ok']:
        lines.append(f"Python 版本 {result['python']['version']} 过低，需要 {result['python']['required']}。")
    for item in result['missing_required']:
        lines.append(f"缺少必需组件 {item['package']}（{item['purpose']}）。")
    for item in result['missing_optional']:
        lines.append(f"缺少可选组件 {item['package']}（{item['purpose']}）。")
    if result.get('mpp_needs'):
        lines.append('本机已装 Microsoft Project，装上 pywin32 就能生成 MPP（征得同意后安装）。')
    lines.append(('可生成 MPP：' if result['ms_project']['available'] else '不能生成 MPP，将输出 PDF 与 Timeline：')
                 + result['ms_project']['reason'] + '。')
    if result['install_command']:
        lines.append('安装命令（复制到命令行运行一次即可）：')
        lines.append('  PowerShell：' + result['install_command_powershell'])
        lines.append('  命令提示符（cmd）：' + result['install_command'])
    lines.append('环境检查通过，可以开始。' if result['status'] == 'READY' else '请先安装缺少的必需组件后再运行。')
    return '\n'.join(lines)


if __name__ == '__main__':
    outcome = check(include_mpp="--mpp" in sys.argv)
    print(report(outcome))
    if '--json' in sys.argv:
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome['status'] == 'READY' else 4)
