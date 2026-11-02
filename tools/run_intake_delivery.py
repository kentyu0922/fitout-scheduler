"""Legacy delivery name, routed through the shared L2 intake entry."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.run_intake_project import main
from tools.run_contract import main as contract_main

def run_delivery(intake_file: Path, output_dir: Path, generate_mpp: bool = False):
    return contract_main(["--input", str(intake_file), "--output",
                          str(output_dir.resolve() / "programme.mpp"),
                          "--mpp" if generate_mpp else "--no_mpp"])

if __name__ == "__main__":
    raise SystemExit(main())

