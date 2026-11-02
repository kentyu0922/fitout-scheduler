"""Build a proposal-only dataset from planned-vs-actual JSON records."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.calibration_dataset import planned_vs_actual, to_observation, build_dataset
from core.productivity import load_productivity_rates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actuals', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source, output = Path(args.actuals).resolve(), Path(args.output).resolve()
    root = Path(__file__).resolve().parents[1]
    if output == source or output.is_relative_to(root / 'config') or output.is_relative_to(root / 'templates') or output.exists():
        parser.error('Use a new output file outside config/templates; existing baselines must not be overwritten')
    records = json.loads(source.read_text(encoding='utf-8'))['records']
    comparisons = [planned_vs_actual(r) for r in records]
    observations = [to_observation(r) for r in records]
    result = build_dataset(load_productivity_rates(), observations)
    result.update(comparisons=comparisons, observations=observations, proposal_only=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    print(output)


if __name__ == '__main__':
    main()
