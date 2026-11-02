# -*- coding: utf-8 -*-
"""V6.6.0.1 — minimum-viable VERTICAL LOGISTICS GOVERNANCE layer.

Governing principle (V6.6.0.1 governance correction)
---------------------------------------------------
    LOGISTICS FACT        !=  LOGISTICS THROUGHPUT
    UNKNOWN               !=  UNCONSTRAINED

What this module is
-------------------
A GATE. Given how many freight lifts the building provides, it decides whether
material-intensive multi-floor concurrency may be assumed and what must be
confirmed first — nothing more.

What this module deliberately is NOT
------------------------------------
No pallet-per-hour model. No hoist velocity. No tonnage throughput. No material
fluids simulation. Those would require measured lift ratings, loading-bay booking
windows and contractor logistics data that the engine does not have — inventing
them would be exactly the fabricated-certainty defect this codebase exists to
avoid (see LIMITATIONS.md).

Three consequences, all enforced here
-------------------------------------
1. ``freight_lifts is None`` does NOT mean "unconstrained". It means
   ``NOT_SPECIFIED``: the count was never declared, so lift availability,
   loading-bay access and hoisting windows are all UNKNOWN. The CPM still runs
   (no logistics constraint is applied) but every report must state that vertical
   logistics feasibility has NOT been verified.
2. ``freight_lifts == 1`` does NOT auto-serialize every trade across every floor.
   It raises a SCOPED concurrency gate that applies ONLY to material-intensive
   concurrent floor STARTS. Setting out, protection, inspections, T&C, cleaning
   and furniture are not gated — no physical justification exists to block them,
   and this module never emits an FS rule.
3. A declared lift count is a FACT about a count. Capacity stays UNKNOWN in every
   case unless delivery-window / booking / payload / loading-bay evidence exists.
   This module never invents a throughput figure and never invents a delay.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

# --- logistics declaration status (was it declared at all?) ------------------
NOT_SPECIFIED = "NOT_SPECIFIED"
SINGLE_LIFT_DECLARED = "SINGLE_LIFT_DECLARED"
MULTIPLE_LIFTS_DECLARED = "MULTIPLE_LIFTS_DECLARED"

# --- capacity value ----------------------------------------------------------
# A declared COUNT is a fact. CAPACITY is never derived from it.
CAPACITY_UNKNOWN = "UNKNOWN"

# --- concurrency governance gate --------------------------------------------
GATE_NOT_REQUIRED = "NO_LOGISTICS_GATE"
GATE_CONFIRMATION_REQUIRED = "LOGISTICS_CONFIRMATION_REQUIRED"

# --- concurrency status — never "UNCONSTRAINED" / "ASSUMED_ALLOWED" ---------
CONCURRENCY_UNVERIFIED = "UNVERIFIED"
CONCURRENCY_CONDITIONAL = "CONDITIONALLY_ALLOWED"
CONCURRENCY_NOT_APPLICABLE = "NOT_APPLICABLE_SINGLE_FLOOR"

# --- what the gate touches ---------------------------------------------------
GATE_SCOPE = "MATERIAL_INTENSIVE_CONCURRENT_FLOOR_STARTS"

NOT_GATED_SCOPE = ("Setting out, temporary protection, inspections, testing & commissioning, "
                   "cleaning and furniture installation carry negligible or no bulk material "
                   "intake and are NOT gated. No FS dependency is created by this layer.")

# What would be needed before capacity could be stated at all. None of it exists
# in the engine's inputs, which is precisely why capacity stays UNKNOWN.
THROUGHPUT_EVIDENCE_REQUIRED = [
    "Freight lift rating (kg) and cage dimensions",
    "Loading-bay booking window / delivery slot allocation",
    "Night hoisting hours and after-hours access protocol",
    "Materials manifest with delivery phasing",
]

HOLD_POINT = ("LOGISTICS_HOLD_POINT: Multi-floor concurrent heavy trade start requires "
              "Main Contractor vertical logistics plan.")

SINGLE_LIFT_WARNING = ("Single freight lift declared. Concurrent multi-floor heavy material "
                       "delivery (drywall / ducting) is CONDITIONALLY_ALLOWED only — it requires a "
                       "confirmed hoisting protocol. This is a governance gate on "
                       "material-intensive floor starts, not a serialisation of every trade.")

NOT_SPECIFIED_WARNING = ("Freight lift count was NOT declared. Vertical logistics feasibility has "
                         "NOT been verified — lift availability, loading-bay access and hoisting "
                         "windows are all UNKNOWN. UNKNOWN does not mean UNCONSTRAINED: no "
                         "unconstrained concurrency has been established. Declare --freight_lifts to "
                         "have the engine gate material-intensive multi-floor concurrency.")

VERIFICATION_NOT_CONFIRMED = ("Vertical logistics feasibility has NOT been verified. The CPM "
                              "proceeds without a logistics constraint; this is a statement of "
                              "missing evidence, not a statement that vertical transport is "
                              "adequate.")

# trades whose material is bulky enough that a single lift becomes a real choke
HEAVY_TRADES = ("partition_framing", "gypsum_boarding", "hvac_duct", "chw_pipe",
                "containment", "raised_floor", "suspended_ceiling", "gypsum_ceiling",
                "feature_ceiling", "tile_stone")


def validate_freight_lifts(value: Any) -> Optional[int]:
    """None passes through; anything else must be a positive integer."""
    if value is None:
        return None
    if isinstance(value, bool) or int(value) != value or int(value) < 1:
        raise ValueError(f"freight_lifts must be a positive integer or None, got {value!r}")
    return int(value)


def logistics_governance(freight_lifts: Optional[int] = None,
                         floors: int = 1,
                         zones: Optional[int] = None,
                         heavy_trades: Sequence[str] = HEAVY_TRADES,
                         evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the gate-based vertical logistics governance payload.

    ``freight_lifts is None``  -> NOT_SPECIFIED : capacity UNKNOWN, feasibility NOT verified,
                                  no logistics constraint applied to the CPM, concurrency UNVERIFIED.
    ``freight_lifts == 1``     -> SINGLE_LIFT_DECLARED : capacity UNKNOWN, scoped
                                  LOGISTICS_CONFIRMATION_REQUIRED gate for material-intensive
                                  concurrent floor starts (multi-floor only).
    ``freight_lifts > 1``      -> MULTIPLE_LIFTS_DECLARED : capacity still UNKNOWN (a count is not
                                  a verified capacity); no concurrency gate required.

    No branch invents a throughput figure, a hoisting rate or a delay.
    """
    n = validate_freight_lifts(freight_lifts)
    floors = max(1, int(floors or 1))
    multi = floors > 1

    ev = dict(evidence or {})
    # Capacity is evidenced ONLY when the caller supplies verified capacity data.
    capacity_verified = bool(ev.get("capacity_verified"))
    capacity = ev.get("capacity_value") if capacity_verified else CAPACITY_UNKNOWN
    verified = capacity_verified

    if n is None:
        status = NOT_SPECIFIED
    elif n == 1:
        status = SINGLE_LIFT_DECLARED
    else:
        status = MULTIPLE_LIFTS_DECLARED

    block: Dict[str, Any] = dict(
        # --- declaration facts -------------------------------------------------
        freight_lifts_count=n,
        freight_lifts_specified=(n is not None),
        floors=floors,
        zones=(int(zones) if zones is not None else None),
        multi_floor=multi,
        # --- V6.6.0.1 canonical governance fields ------------------------------
        logistics_status=status,
        logistics_capacity=capacity,
        logistics_feasibility_verified=verified,
        # --- scope of the gate (never a blanket rule) --------------------------
        gate_applies_to=GATE_SCOPE,
        material_intensive_only=True,
        gated_trades=list(heavy_trades),
        not_gated_scope=NOT_GATED_SCOPE,
        adds_fs_dependency=False,          # this layer NEVER writes a dependency
        cpm_constraint_applied=False,      # the CPM may proceed without a logistics constraint
        # --- fact vs throughput ------------------------------------------------
        logistics_fact=(f"freight_lifts declared = {n}" if n is not None
                        else "freight_lifts not declared"),
        logistics_throughput_model="NONE — no throughput, velocity or tonnage model is used or claimed.",
        throughput_evidence_required=list(THROUGHPUT_EVIDENCE_REQUIRED),
        invented_delay_days=0,
        delay_invented=False,
        evidence_basis=("Freight lift count only. Any throughput conclusion would require lift "
                        "rating, loading-bay booking windows and night-hoist hours, none of which "
                        "are supplied."),
        model="GATE_BASED_GOVERNANCE",
        simulation="NONE — no throughput, velocity or tonnage model is used or claimed.",
        verification_statement=VERIFICATION_NOT_CONFIRMED,
    )

    if not multi:
        block.update(
            logistics_concurrency_gate=GATE_NOT_REQUIRED,
            parallel_floor_execution=CONCURRENCY_NOT_APPLICABLE,
            vertical_choke_warning=None,
            standard_warning=None,
            required_confirmations=[],
            confirmed=False,
            assumption=None,
        )
        return block

    if n is None:
        block.update(
            logistics_concurrency_gate=GATE_NOT_REQUIRED,
            parallel_floor_execution=CONCURRENCY_UNVERIFIED,
            vertical_choke_warning=None,
            standard_warning=NOT_SPECIFIED_WARNING,
            required_confirmations=list(THROUGHPUT_EVIDENCE_REQUIRED),
            confirmed=False,
            assumption=None,   # deliberately NOT "assumed unconstrained"
            explicitly_not_assumed_unconstrained=True,
            disclosure=("Vertical logistics feasibility has not been verified. Multi-floor "
                        "concurrency is UNVERIFIED, not unconstrained."),
        )
        return block

    if n == 1:
        block.update(
            logistics_concurrency_gate=GATE_CONFIRMATION_REQUIRED,
            parallel_floor_execution=CONCURRENCY_CONDITIONAL,
            vertical_choke_warning=SINGLE_LIFT_WARNING,
            standard_warning=None,
            required_confirmations=[
                "Loading bay booking window",
                "Night hoist hours / after-hours hoisting protocol",
                "Hoist tonnage and cage rating",
            ],
            confirmed=False,
            assumption=None,
            explicitly_not_assumed_unconstrained=True,
            disclosure=("Vertical logistics feasibility has not been verified. One freight lift is "
                        "a KNOWN count, not a known capacity. The confirmation gate applies only to "
                        "material-intensive concurrent floor starts; lightweight activities are not "
                        "gated."),
        )
        block["logistics_hold_point"] = HOLD_POINT
        block["hold_point_scope"] = GATE_SCOPE
        return block

    block.update(
        logistics_concurrency_gate=GATE_NOT_REQUIRED,
        parallel_floor_execution=CONCURRENCY_CONDITIONAL,
        vertical_choke_warning=None,
        standard_warning=None,
        required_confirmations=[],
        confirmed=False,
        assumption=None,
        explicitly_not_assumed_unconstrained=True,
        disclosure=(f"{n} freight lifts declared. The count reduces exposure but the declaration is "
                    f"a client input, not a verified booking — capacity and booking windows remain "
                    f"UNKNOWN and vertical logistics feasibility is not verified."),
    )
    return block


def requires_concurrency_gate(block: Dict[str, Any]) -> bool:
    return block.get("logistics_concurrency_gate") == GATE_CONFIRMATION_REQUIRED


def make_hold_point_task(block: Dict[str, Any], next_id: int,
                         outline_level: int = 2) -> Optional[Dict[str, Any]]:
    """A visible, zero-duration governance node. Never a duration invention.

    Deliberately NOT auto-inserted by the pipeline: inserting a node into the WBS
    would alter the frozen task ledger for a purely informational gate. The gate is
    recorded in the governance payload instead. Provided for callers that
    explicitly want a visible hold-point row; it carries no predecessors and no
    successors, so it can never block another activity.
    """
    if not block.get("logistics_hold_point"):
        return None
    return dict(
        id=next_id,
        name="[HOLD] LOGISTICS 垂直运输确认 (Vertical Logistics Confirmation)",
        milestone=True,
        duration_days=0,
        duration=0,
        outline_level=outline_level,
        spatial_scope="project",
        zone=None,
        floor=None,
        workfronts=1,
        workfronts_source="project",
        logistics_hold_point=block["logistics_hold_point"],
        activity_class="GOVERNANCE_HOLD",
        note=(block.get("vertical_choke_warning") or "") + " Required confirmations: "
             + "; ".join(block.get("required_confirmations") or []),
    )


def explain(block: Dict[str, Any]) -> List[str]:
    out = [f"垂直运输治理：freight_lifts={block.get('freight_lifts_count')} / "
           f"floors={block.get('floors')} → {block.get('logistics_status')} "
           f"(capacity={block.get('logistics_capacity')}, "
           f"verified={block.get('logistics_feasibility_verified')}, "
           f"gate={block.get('logistics_concurrency_gate')})"]
    if block.get("vertical_choke_warning"):
        out.append("  · " + block["vertical_choke_warning"])
    if block.get("logistics_hold_point"):
        out.append("  · " + block["logistics_hold_point"])
    if block.get("standard_warning"):
        out.append("  · " + block["standard_warning"])
    out.append("  · " + str(block.get("verification_statement")))
    out.append("  · " + str(block.get("simulation")))
    return out
