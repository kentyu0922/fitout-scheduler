# -*- coding: utf-8 -*-
"""
tools/msp_cli.py — MS Project COM 自动化命令行入口
==================================================
环境前提（运行本工具前必须满足）：
  1. 本机已安装 Microsoft Project（支持 .mpp 的版本，如 Project 2016/2019/2021/365）。
  2. Python 与 Project 位数一致（同为 32 或 64 位）。
  3. 已安装 pywin32：pip install pywin32
  4. PDF 导出额外需要：pip install reportlab

子命令：
  verify   探测本机 MS Project COM 是否可用
  import   从任务 JSON 导入/生成 .mpp
  update   用进度 JSON 更新 .mpp 的完成度与实际日期
  export   从 .mpp 导出任务报表（csv / json / pdf）

示例：
  python tools/msp_cli.py verify
  python tools/msp_cli.py import --json examples/sample_tasks.json --out output_mpp/Demo.mpp --title "演示项目"
  python tools/msp_cli.py update --mpp output_mpp/Demo.mpp --progress examples/sample_progress.json
  python tools/msp_cli.py export --mpp output_mpp/Demo.mpp --out output_mpp/Demo_report.csv --format csv
"""
import argparse
import logging
import os
import sys

# 允许从仓库根目录直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.msp_session import com_available
from core.msp_automation import import_tasks, update_progress, export_report


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="MS Project COM 自动化：任务导入 / 进度更新 / 报表导出")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_v = sub.add_parser("verify", help="探测本机 MS Project COM 可用性")
    p_v.add_argument("--verbose", action="store_true", help="打印版本号")

    p_i = sub.add_parser("import", help="从任务 JSON 导入/生成 .mpp")
    p_i.add_argument("--json", required=True, help="任务 JSON 路径")
    p_i.add_argument("--out", required=True, help="输出 .mpp 路径")
    p_i.add_argument("--title", default=None, help="项目标题（新建时）")
    p_i.add_argument("--start", default=None, help="项目开工日 YYYY-MM-DD")
    p_i.add_argument("--append", action="store_true", help="向已有 .mpp 追加任务")

    p_u = sub.add_parser("update", help="用进度 JSON 更新 .mpp")
    p_u.add_argument("--mpp", required=True, help="目标 .mpp 路径")
    p_u.add_argument("--progress", required=True, help="进度 JSON 路径")

    p_e = sub.add_parser("export", help="从 .mpp 导出报表")
    p_e.add_argument("--mpp", required=True, help="源 .mpp 路径")
    p_e.add_argument("--out", required=True, help="输出文件路径")
    p_e.add_argument("--format", default="csv", choices=["csv", "json", "pdf"],
                     help="导出格式（默认 csv）")
    return ap


def main(argv=None):
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.cmd == "verify":
        ok = com_available()
        if ok:
            print("✅ MS Project COM 可用")
        else:
            print("❌ MS Project COM 不可用（请检查安装 / 位数 / pywin32）")
        return 0 if ok else 1

    if args.cmd == "import":
        path = import_tasks(args.json, args.out, args.title, args.start, append=args.append)
        print(f"✅ 已生成/更新: {path}")
        return 0

    if args.cmd == "update":
        stats = update_progress(args.mpp, args.progress)
        print(f"✅ 进度更新完成: 命中 {stats['matched']} / 跳过 {stats['skipped']}")
        return 0

    if args.cmd == "export":
        path = export_report(args.mpp, args.out, args.format)
        print(f"✅ 报表已导出: {path}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
