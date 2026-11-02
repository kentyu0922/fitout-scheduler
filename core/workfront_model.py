"""V6.1 workfront model.

A workfront is NOT a headcount. It is the combination of
    space × trade × crew × access × sequence
that decides whether two tasks can occupy the site at the same time.

V6.1 uses the workfront model for exactly one decision: how a single trade crew
moves from one zone to the next (the inter-zone pipeline). Crew sizing, crowding
loss and crash-by-crew belong to V6.2 and are NOT invented here.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from core.zone_model import load_spatial_model

DEFAULT_CREWS = 1


def load_model(path: Optional[str] = None) -> Dict[str, Any]:
    return load_spatial_model(path)


def trade_of(activity_type: Optional[str], model: Optional[Dict[str, Any]] = None) -> str:
    """Resolve the trade group of one activity_type (explicit table, no keyword guessing)."""
    model = model or load_model()
    groups = (model.get("trades") or {}).get("groups") or {}
    for trade, members in groups.items():
        if activity_type in members:
            return trade
    return (model.get("trades") or {}).get("default", "general")


class Workfront:
    """One crew of one trade working in one zone on one activity."""

    def __init__(self, workfront_id: str, zone_id: Optional[str], activity_type: str,
                 trade: str, crew_size: int, task_id: Optional[int] = None,
                 crew_index: int = 1, access: str = "default", sequence_index: int = 0,
                 area_sqm: Optional[float] = None):
        self.workfront_id = workfront_id
        self.zone_id = zone_id
        self.activity_type = activity_type
        self.trade = trade
        self.crew_size = max(1, int(crew_size))
        self.task_id = task_id
        self.crew_index = max(1, int(crew_index))
        self.access = access
        self.sequence_index = int(sequence_index)
        self.area_sqm = area_sqm

    def to_dict(self) -> Dict[str, Any]:
        return dict(workfront_id=self.workfront_id, zone_id=self.zone_id, task_id=self.task_id,
                    activity_type=self.activity_type, trade=self.trade, crew_size=self.crew_size,
                    crew_index=self.crew_index, access=self.access,
                    sequence_index=self.sequence_index, area_sqm=self.area_sqm)


def build_workfronts(zone_tasks: Sequence[Dict[str, Any]],
                     model: Optional[Dict[str, Any]] = None,
                     crews_per_trade: Optional[Dict[str, int]] = None) -> List[Workfront]:
    """Build one workfront per (zone, activity) instance.

    Sequence index follows the task list order within a zone, so a trade's position
    in the zone's internal sequence is preserved and auditable.
    """
    model = model or load_model()
    crews_per_trade = crews_per_trade or {}
    per_zone_seq: Dict[Any, int] = {}
    out: List[Workfront] = []
    for task in zone_tasks:
        activity = task.get("activity_type")
        if not activity:
            continue
        zone_id = task.get("zone")
        seq = per_zone_seq.get(zone_id, 0)
        per_zone_seq[zone_id] = seq + 1
        trade = trade_of(activity, model)
        out.append(Workfront(
            workfront_id=f"WF-{zone_id or 'P'}-{activity}",
            zone_id=zone_id,
            activity_type=activity,
            trade=trade,
            crew_size=int(task.get("crew_size") or 1),
            task_id=task.get("id"),
            crew_index=1,
            access=str(task.get("access", "default")),
            sequence_index=seq,
            area_sqm=task.get("zone_area_sqm"),
        ))
    return out


def resolve_inter_zone_pipeline(activity_type: str, predecessor_duration_days: int,
                                crews_for_trade: Optional[int] = None,
                                model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve the zone-to-zone link for one trade moving between zones.

    Returns {relationship, lag, reason, confidence, policy, crews_for_trade, emits_edge}.
    With one crew (V6.1 default) the answer is FS — a crew cannot be in two zones at once.
    This is a physical constraint, not a schedule-compression choice.
    """
    model = model or load_model()
    cfg = (model.get("workfront_model") or {}).get("inter_zone_pipeline") or {}
    crews = int(crews_for_trade if crews_for_trade is not None
                else cfg.get("crews_per_trade_default", DEFAULT_CREWS))
    crews = max(1, crews)
    policy_id = str(cfg.get("default_policy", "single_crew"))
    policy = (cfg.get("policies") or {}).get(policy_id) or {}
    dur = max(0, int(predecessor_duration_days or 0))

    if policy_id == "parallel_crews":
        return dict(relationship=None, lag=0, emits_edge=False, policy=policy_id,
                    crews_for_trade=crews, confidence=policy.get("confidence", "low"),
                    reason=policy.get("reason", ""),
                    explanation=f"{activity_type}：{policy.get('label_zh', policy_id)}，不生成跨区边")

    if crews <= 1:
        return dict(relationship="FS", lag=0, emits_edge=True, policy=policy_id,
                    crews_for_trade=1, confidence=policy.get("confidence", "medium"),
                    reason=policy.get("reason_when_fs", ""),
                    explanation=(f"{activity_type}：单班组流水，A 区完成 → B 区开始（FS）。"
                                 f"{policy.get('reason_when_fs', '')}"))

    lag = max(1, min(dur, int(math.ceil(dur / crews))) if dur else 1)
    return dict(relationship="SS", lag=lag, emits_edge=True, policy=policy_id,
                crews_for_trade=crews, confidence=policy.get("confidence", "medium"),
                reason=str(policy.get("reason_when_ss", "")).format(crews=crews),
                explanation=(f"{activity_type}：{crews} 个班组滚动流水，SS+{lag}d。"
                             f"滞后 = clamp(ceil(分区工期 {dur}d / {crews} 班组), 1, {dur}) = {lag}d"))


def trade_contention(workfronts: Sequence[Workfront]) -> List[Dict[str, Any]]:
    """Group workfronts that compete for the same trade crew. No network edge is emitted.

    This is resource contention, not sequencing: it is handed to V6.2.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for wf in workfronts:
        groups.setdefault(wf.trade, []).append(
            dict(workfront_id=wf.workfront_id, zone_id=wf.zone_id, task_id=wf.task_id,
                 activity_type=wf.activity_type, crew_size=wf.crew_size))
    rows = []
    for trade, items in sorted(groups.items()):
        rows.append(dict(trade=trade, workfronts=items, count=len(items), emits_edge=False,
                         note="同一工种的班组转场竞争，V6.1 以单班组转场边（FS）表达；班组数量与效率折减由 V6.2 处理"))
    return rows
