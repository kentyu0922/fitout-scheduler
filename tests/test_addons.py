"""Each addon loads on DBB and D&B without cycles; record elapsed time."""
import json
import time
from pathlib import Path

from core.module_catalog import CATALOG
from tests.conftest import ROOT, solve_case

SPECIALIST = {"A12", "A13", "A14", "A15", "A16", "A19", "A20"}


def test_each_addon_dbb_and_db_loads():
    rows = []
    for addon in CATALOG:
        extra = {f"addon_{addon.lower()}": "required", "statutory_tender_required": "false"}
        sector = "finance"
        if addon in SPECIALIST:
            extra["space_use"] = "office_with_specialist"
            sector = "biopharma" if addon in {"A12", "A19", "A20"} else "semiconductor"
        if addon in {"A06", "A07"}:
            sector = "gaming"
        extra["sector"] = sector
        for delivery in ("DBB", "DB"):
            t0 = time.perf_counter()
            payload, stdout, _ = solve_case(
                delivery=delivery, area=3000, sector=sector, extra=extra,
                project_name=f"{addon}-{delivery}")
            elapsed = time.perf_counter() - t0
            assert payload.get("tasks"), (addon, delivery)
            assert elapsed < 1.5, (addon, delivery, elapsed)
            selected = (payload.get("module_plan") or {}).get("selected") or []
            assert addon in selected, (addon, selected)
            finish = payload.get("finish_date") or payload.get("control_dates", {}).get("completion")
            start = payload.get("start_date")
            rows.append({
                "addon": addon, "delivery": delivery, "elapsed_s": round(elapsed, 3),
                "tasks": len(payload["tasks"]), "finish_date": finish, "start_date": start,
                "ok": True, "cycle": False,
            })
    out = ROOT / "output" / "addon_matrix.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    assert len(rows) == 44
