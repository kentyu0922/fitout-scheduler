import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def solve_case(*, delivery, area=3000, sector="finance", bidding="invite",
               project_name="测试项目", extra=None, pdf=False, start="2027-03-01"):
    extra = extra or {}
    out = ROOT / "output" / f"pytest_{delivery}_{area}_{sector}"
    out.mkdir(parents=True, exist_ok=True)
    mpp = out / "programme.mpp"
    cmd = [sys.executable, "-X", "utf8", str(ROOT / "main.py"),
           "--city", extra.get("city", "上海"), "--area", str(area),
           "--sector", sector, "--delivery", delivery, "--bidding", bidding,
           "--start_date", start, "--project_name", project_name,
           "--output", str(mpp), "--no_mpp"]
    if not pdf:
        cmd.append("--mpp_only")
    for k, v in extra.items():
        if k == "city":
            continue
        if isinstance(v, bool):
            cmd.extend(["--" + k, "true" if v else "false"])
        else:
            cmd.extend(["--" + k, str(v)])
    proc = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace")
    sched = mpp.with_name("programme_schedule.json")
    if proc.returncode != 0 or not sched.exists():
        raise AssertionError(f"solve failed rc={proc.returncode}\n{proc.stdout[-8000:]}")
    payload = json.loads(sched.read_text(encoding="utf-8"))
    return payload, proc.stdout, out
