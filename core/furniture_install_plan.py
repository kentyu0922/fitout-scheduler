# -*- coding: utf-8 -*-
"""Isolated furniture-install night-capacity candidate (Hangzhou 520 trial).

Not wired into main.py. Does not rewrite config/productivity_rates.json or
baseline task dates. applied=false always.

Rate units:
  each_per_team_night   — team output in one night window; do NOT multiply crew
  each_per_worker_night — per-person output in one night window; multiply crew_size

A night window shorter than 8h is the time unit. Do not convert to an 8h day
shift and derate again.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

RATE_UNITS = ("each_per_team_night", "each_per_worker_night")


class FurnitureInstallPlanError(ValueError):
    """Missing or invalid candidate inputs."""


def _require_number(name: str, value: Any, *, positive: bool = True, integer: bool = False) -> float:
    if value is None:
        raise FurnitureInstallPlanError(f"missing {name}")
    if isinstance(value, bool):
        raise FurnitureInstallPlanError(f"invalid {name}: {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FurnitureInstallPlanError(f"invalid {name}: {value!r}") from exc
    if not math.isfinite(number):
        raise FurnitureInstallPlanError(f"{name} must be finite, got {value!r}")
    if integer and not number.is_integer():
        raise FurnitureInstallPlanError(f"{name} must be an integer, got {value!r}")
    if positive and number <= 0:
        raise FurnitureInstallPlanError(f"{name} must be > 0, got {value!r}")
    if not positive and number < 0:
        raise FurnitureInstallPlanError(f"{name} must be >= 0, got {value!r}")
    return int(number) if integer else number


def _effective_crew(rate_unit: str, crew_size: int) -> int:
    if rate_unit == "each_per_worker_night":
        return crew_size
    return 1


def compute_furniture_install_nights(
    *,
    quantity_each: Any,
    quantity_source: str,
    rate_value: Any,
    rate_unit: Optional[str],
    crew_size: Any,
    risk_factor: Any,
    buffer_nights: Any,
    night_window_basis: Optional[str],
    estimated_by: Optional[str],
    categories: Optional[List[Dict[str, Any]]] = None,
    baseline: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a read-only candidate. Never mutates a schedule task."""
    if not rate_unit:
        raise FurnitureInstallPlanError(
            "missing rate_unit; must be each_per_team_night or each_per_worker_night "
            "(do not auto-select 4 vs 2 nights)")
    if rate_unit not in RATE_UNITS:
        raise FurnitureInstallPlanError(
            f"unknown rate_unit {rate_unit!r}; valid: {list(RATE_UNITS)}")
    if not (quantity_source or "").strip():
        raise FurnitureInstallPlanError("missing quantity_source")
    if not (night_window_basis or "").strip():
        raise FurnitureInstallPlanError("missing night_window_basis")
    if not (estimated_by or "").strip():
        raise FurnitureInstallPlanError("missing estimated_by")

    qty = _require_number("quantity_each", quantity_each, positive=True)
    rate = _require_number("rate_value", rate_value, positive=True)
    crew = int(_require_number("crew_size", crew_size, positive=True, integer=True))
    risk = _require_number("risk_factor", risk_factor, positive=True)
    if risk > 1:
        raise FurnitureInstallPlanError(f"risk_factor must be <= 1, got {risk}")
    buffer = int(_require_number("buffer_nights", buffer_nights, positive=False, integer=True))

    if categories:
        mixed = [c.get("category") or "unspecified" for c in categories]
        return dict(
            ok=False,
            applied=False,
            reason="CATEGORY_MIX_NEEDS_CONFIRMATION",
            categories=mixed,
            note="Do not treat all pieces as equal install difficulty until categories are estimated separately.",
        )

    crew_multiplier = _effective_crew(rate_unit, crew)
    raw_team_capacity = rate * crew_multiplier
    derated_team_capacity = raw_team_capacity * risk
    if derated_team_capacity <= 0:
        raise FurnitureInstallPlanError("derated capacity is not positive")
    installation_nights = int(math.ceil(qty / derated_team_capacity - 1e-12))
    candidate_total_nights = installation_nights + buffer

    assumed_qty = str(quantity_source).upper().startswith("ASSUMED") or "DERIVED_FROM_AREA" in str(quantity_source).upper()
    baseline = dict(baseline or {})
    if baseline and not baseline.get("source"):
        raise FurnitureInstallPlanError(
            "baseline.source required when attaching another project's task ids/dates")

    return dict(
        ok=True,
        applied=False,
        pm_approval_required=True,
        product_approved=False,
        commercial_acceptance=False,
        engine_patch=False,
        baseline_task_id=baseline.get("task_id"),
        baseline_duration_days=baseline.get("duration_days"),
        baseline_predecessors=baseline.get("predecessors"),
        baseline_source=baseline.get("source"),
        duration_unit="night_window",
        quantity_each=qty,
        quantity_source=quantity_source,
        quantity_status="ASSUMED_QUANTITY" if assumed_qty else "MEASURED_OR_DECLARED",
        rate_value=rate,
        rate_unit=rate_unit,
        crew_size=crew,
        crew_used_in_rate=crew_multiplier,
        crew_not_double_counted=(crew_multiplier == (crew if rate_unit == "each_per_worker_night" else 1)),
        risk_factor=risk,
        buffer_nights=buffer,
        night_window_basis=night_window_basis.strip(),
        estimated_by=estimated_by.strip(),
        confidence="pm_estimate_not_vendor_po_not_L1",
        raw_stated_capacity_each_per_night=raw_team_capacity,
        derated_capacity_each_per_night=derated_team_capacity,
        installation_nights=installation_nights,
        candidate_total_nights=candidate_total_nights,
        finish_after=None,
        saving_days=None,
        saving_forbidden_until=["night_calendar", "partial_demolition", "simplified_filing", "completion_releases"],
        note=(
            "Candidate nights only. Do not write into a baseline furniture-install task. "
            "Transport/position/install are included in the team rate — no extra tasks. "
            "Do not convert a short night window into an 8h day and derate again."
        ),
    )


def sensitivity_risk_factors(plan_input: Dict[str, Any], factors=(0.70, 0.75)) -> List[Dict[str, Any]]:
    rows = []
    for factor in factors:
        payload = dict(plan_input)
        payload["risk_factor"] = factor
        result = compute_furniture_install_nights(**payload)
        if not result["ok"]:
            rows.append(dict(risk_factor=factor, ok=False, reason=result["reason"]))
            continue
        rows.append(dict(risk_factor=factor,
                         installation_nights=result["installation_nights"],
                         buffer_nights=result["buffer_nights"],
                         candidate_total_nights=result["candidate_total_nights"],
                         derated_capacity_each_per_night=result["derated_capacity_each_per_night"]))
    return rows
