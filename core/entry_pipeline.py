"""Adapt the shared input contract to the existing engine Namespace."""
from __future__ import annotations

import json
from pathlib import Path
from core.project_input import load_input, normalize_project_inputs


def resolve_engine_input(args, argv):
    """All public entries converge here; legacy regression CLI stays supported."""
    if not (args.project_input or args.intake_excel):
        return {}, None
    filename = args.project_input or args.intake_excel
    try:
        sources = [(str(Path(filename).resolve()), load_input(filename))]
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        raise SystemExit(f"INTAKE_BLOCKED: 输入文件无法解析：{exc}") from None
    if not args.project_input:
        used = {token.split("=", 1)[0].lstrip("-") for token in argv if token.startswith("--")}
        fields = ("city", "area", "sector", "delivery", "bidding", "start_date", "target_date",
                  "project_name", "site_condition", "calendar_profile", "authority_profile",
                  "workfronts", "zoning_mode", "sectional", "building_restriction", "demolition_fraction", "fasttrack")
        sources.append(("explicit_engine_cli", {k: getattr(args, k) for k in fields if k in used}))
    result = normalize_project_inputs(sources)
    if result.blockers:
        raise SystemExit("INTAKE_BLOCKED: " + json.dumps(result.blockers, ensure_ascii=False))
    p = result.project
    names = {"area_sqm": "area", "cost_10k_rmb": "cost"}
    for key, value in p.items():
        if key == 'mode':
            # Engine --mode is a legacy template key, not the public use mode.
            continue
        target = names.get(key, key)
        if hasattr(args, target):
            setattr(args, target, value.lower() if key == "calendar_profile" else value)
    if p["mode"] == "review":
        args.senior_pm_review = True
    return dict(p), result.payload()
