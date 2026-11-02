"""Compatibility alias: client XLSX now runs the sole L2 contract entry."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.run_contract import main as contract_main

def main(argv=None):
    p = argparse.ArgumentParser(description="Client intake to unified L2 programme")
    p.add_argument("--intake", "-i", required=True)
    p.add_argument("--output_dir", "-o", required=True)
    exports = p.add_mutually_exclusive_group()
    exports.add_argument("--mpp", action="store_true")
    exports.add_argument("--no-mpp", "--no_mpp", action="store_true")
    args = p.parse_args(argv)
    return contract_main(["--input", args.intake, "--output",
                          str(Path(args.output_dir).resolve() / "programme.mpp"),
                          "--mpp" if args.mpp else "--no_mpp"])

if __name__ == "__main__":
    raise SystemExit(main())
