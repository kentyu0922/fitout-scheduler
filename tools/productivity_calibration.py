"""Preview evidence-selected rates without changing the rate library or a schedule."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.productivity import load_productivity_rates
from core.productivity_evidence import load_evidence, resolve_rates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--city', required=True)
    parser.add_argument('--project_id')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    protected = [Path(args.evidence).resolve(), Path(__file__).resolve().parents[1] / 'config' / 'productivity_rates.json']
    if output in protected:
        parser.error('Proposal output must not overwrite evidence or the baseline rate library')
    proposed = resolve_rates(load_productivity_rates(), args.city, load_evidence(args.evidence), args.project_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'proposal_only': True, 'baseline_modified': False,
                                 'note': 'Review numeric evidence and re-solve a separate schedule before adoption.',
                                 'effective_rates': proposed}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
