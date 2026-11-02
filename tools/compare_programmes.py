"""Read-only baseline/current variance review with an explicit status date."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.programme_compare import compare_programmes


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--baseline', required=True)
    p.add_argument('--current', required=True)
    p.add_argument('--status_date', required=True)
    p.add_argument('--reasons')
    p.add_argument('--output', required=True)
    args = p.parse_args()
    read = lambda f: json.loads(Path(f).read_text(encoding='utf-8-sig'))
    result = compare_programmes(read(args.baseline), read(args.current), status_date=args.status_date,
                                reasons=read(args.reasons) if args.reasons else None)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
