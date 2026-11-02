"""P2-16 Crew plan input schema.

The old flat {trade: headcount} dict breaks the moment a user adds a comment
or metadata key. The upgraded schema is:

    {
      "meta":   {"project_id": ..., "basis": ..., "confirmed_by": ...,
                 "notes": ..., "source": ..., "revision": ..., "confirmed_at": ...},
      "trades": {"partition_framing": 12, ...}
    }

`meta` is free-form (any documentation keys are accepted — comments never
crash the parser). `trades` is strictly validated against the active activity
types. The legacy flat schema remains fully supported (backward compatible):
a dict without a "trades" key is interpreted as flat, where EVERY key must be
an active trade and every value a positive integer.
"""
from typing import Any, Dict, FrozenSet


class CrewPlanSchemaError(ValueError):
    """Raised when a crew plan file does not match either schema."""


def parse_crew_plan(data: Any, active_trades: FrozenSet) -> Dict[str, Any]:
    """Validate and normalise a crew plan into {schema, trades, meta}.

    active_trades: the set of activity types present in this run's WBS;
    trades outside that set are rejected (an unknown trade is a typo, not a
    staffing decision — fail loudly instead of silently ignoring).
    """
    if not isinstance(data, dict):
        raise CrewPlanSchemaError("crew_plan must be a JSON object")
    active = set(active_trades)

    if isinstance(data.get("trades"), dict):
        # Nested schema: {meta, trades}. meta is free-form documentation.
        extra_top = set(data) - {"meta", "trades"}
        if extra_top:
            raise CrewPlanSchemaError(
                f"nested crew_plan schema allows only 'meta' and 'trades' at the top level; "
                f"unexpected keys {sorted(extra_top)} — put documentation inside 'meta'")
        trades = data["trades"]
        meta = data.get("meta") or {}
        if not isinstance(meta, dict):
            raise CrewPlanSchemaError("crew_plan 'meta' must be a JSON object")
        schema = "nested"
    else:
        # Legacy flat schema: every key must itself be an active trade.
        trades = data
        meta = {}
        schema = "flat"

    unknown = set(trades) - active
    if unknown:
        raise CrewPlanSchemaError(
            f"crew_plan trades must be active activity types in this run; unknown: {sorted(unknown)}")
    for key, value in trades.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise CrewPlanSchemaError(
                f"crew_plan headcounts must be positive integers; {key}={value!r}")
    return dict(schema=schema, trades=dict(trades), meta=dict(meta))
