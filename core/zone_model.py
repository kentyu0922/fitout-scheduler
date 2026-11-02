"""V6.1 spatial model: Project / Floor / Zone hierarchy, zone generation and quantity apportionment.

Everything produced here is PLANNING CONSTRUCT, not evidence:
  - a zone count comes from an area rule, not from observed productivity;
  - a zone quantity is an apportionment of the project take-off, not a new measurement.
Every generated number carries its formula so a planning manager can audit it.

This module never touches config/productivity_rates.json or config/dependency_ontology.json.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Sequence

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPATIAL_MODEL_PATH = os.path.join(BASE_DIR, "config", "spatial_model.json")

ZONE_STATUSES = ("not_activated", "available", "in_progress", "released", "complete")
SPATIAL_SCOPES = ("project", "floor", "zone")


def load_spatial_model(path: Optional[str] = None) -> Dict[str, Any]:
    import json

    with open(path or SPATIAL_MODEL_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# quantity apportionment
# ---------------------------------------------------------------------------
def apportion(value: float, weights: Sequence[float], integer: bool = False) -> List[float]:
    """Split `value` across `weights`, preserving the total exactly.

    Continuous values: direct proportional split (float, total preserved to 1e-6).
    Integer units (`each`): largest-remainder method so the sum equals the total
    exactly — never a silent rounding loss in a BOQ count.
    """
    if not weights:
        return []
    total_w = float(sum(weights))
    if total_w <= 0:
        raise ValueError("weights must sum to a positive number")
    if not integer:
        raw = [float(value) * float(w) / total_w for w in weights]
        # Round first, then let the last bucket absorb the residual so the
        # apportioned parts sum back to the project total exactly.
        out = [round(v, 4) for v in raw[:-1]]
        out.append(round(float(value) - sum(out), 4))
        return out
    base = int(round(float(value)))
    exact = [base * float(w) / total_w for w in weights]
    floors = [int(math.floor(v)) for v in exact]
    remainder = base - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[: max(0, remainder)]:
        floors[i] += 1
    return [float(v) for v in floors]


def allocate_zone_quantities(
    project_quantities: Dict[str, Dict[str, Any]],
    zones: Sequence["Zone"],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Apportion every project quantity across zones by zone area.

    Returns {zone_id: {quantity_name: {value, unit, formula, share, ...}}}.
    Every row carries its formula; the sum over zones equals the project value.
    """
    weights = [z.area_sqm for z in zones]
    total_area = float(sum(weights))
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for zone in zones:
        out[zone.zone_id] = {}
    for name, row in project_quantities.items():
        unit = str(row.get("unit", ""))
        if row.get("value") is None:
            # P0-03: UNKNOWN quantities must propagate as UNKNOWN to every zone.
            # Coercing None to 0 and apportioning a "zone zero" would fabricate a
            # number that looks derived but is actually invented.
            for zone in zones:
                out[zone.zone_id][name] = dict(
                    value=None,
                    unit=unit,
                    share_of_area=None,
                    formula="UNKNOWN — 现场条件未确认（site condition unconfirmed），未做分区分摊",
                    quantity_source="unconfirmed",
                    confidence=row.get("confidence", "low"),
                    source_project_quantity=name,
                    status=row.get("status"),
                )
            continue
        value = float(row["value"])
        integer = unit == "each"
        parts = apportion(value, weights, integer=integer)
        for zone, part in zip(zones, parts):
            share = (zone.area_sqm / total_area) if total_area else 0.0
            out[zone.zone_id][name] = dict(
                value=part,
                unit=unit,
                share_of_area=round(share, 6),
                formula=(f"项目总量 {value:g}{unit} × 面积占比 {share:.4f} "
                         f"({zone.area_sqm:g}㎡ / {total_area:g}㎡)"
                         + ("  [整数单位：最大余数法配平，Σ 分区 = 项目总量]" if integer else "")),
                quantity_source="zone_allocated",
                confidence=row.get("confidence", "low"),
                source_project_quantity=name,
            )
    return out


def verify_allocation(project_quantities: Dict[str, Dict[str, Any]],
                      allocation: Dict[str, Dict[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Assert Σ zone quantity == project quantity for every name. Returns discrepancies."""
    issues: List[Dict[str, Any]] = []
    for name, row in project_quantities.items():
        total = row.get("value")
        if total is None:
            # P0-03: an UNKNOWN project quantity must stay None in every zone.
            invented = sorted(zid for zid, zr in allocation.items()
                              if (zr.get(name) or {}).get("value") is not None)
            if invented:
                issues.append(dict(code="QTY_UNKNOWN_INVENTED", quantity=name,
                                   zones=invented,
                                   message="项目总量为 UNKNOWN，分区不得自行补出数字"))
            continue
        total = float(total)
        summed = sum(float(z.get(name, {}).get("value", 0) or 0) for z in allocation.values())
        if abs(summed - total) > 1e-6:
            issues.append(dict(code="QTY_TOTAL_DRIFT", quantity=name,
                               project_total=total, zone_sum=summed, delta=round(summed - total, 6)))
    return issues


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
class Zone:
    """A zone: an independently handed-over, built and inspected piece of a floor."""

    def __init__(self, zone_id: str, label: str, floor_id: str, area_sqm: float,
                 boundary: Optional[str] = None, workfronts: int = 1,
                 generation_reason: str = "", quantities: Optional[Dict[str, Any]] = None,
                 status: str = "not_activated"):
        if area_sqm <= 0:
            raise ValueError(f"zone area must be > 0, got {area_sqm}")
        if status not in ZONE_STATUSES:
            raise ValueError(f"unknown zone status {status!r}")
        self.zone_id = zone_id
        self.label = label
        self.floor_id = floor_id
        self.area_sqm = float(area_sqm)
        self.boundary = boundary
        self.workfronts = max(1, int(workfronts))
        self.generation_reason = generation_reason
        self.quantities = quantities or {}
        self.status = status
        self.available_condition: Optional[str] = None
        self.release_condition: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dict(zone_id=self.zone_id, label=self.label, floor_id=self.floor_id,
                    area_sqm=self.area_sqm, boundary=self.boundary, workfronts=self.workfronts,
                    status=self.status, generation_reason=self.generation_reason,
                    available_condition=self.available_condition,
                    release_condition=self.release_condition,
                    quantity_count=len(self.quantities),
                    quantities=self.quantities)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Zone {self.zone_id} {self.area_sqm:g}m2 {self.status}>"


class Floor:
    def __init__(self, floor_id: str, label: str, area_sqm: float):
        self.floor_id = floor_id
        self.label = label
        self.area_sqm = float(area_sqm)
        self.zones: List[Zone] = []

    def to_dict(self) -> Dict[str, Any]:
        return dict(floor_id=self.floor_id, label=self.label, area_sqm=self.area_sqm,
                    zone_ids=[z.zone_id for z in self.zones])


class SpatialProject:
    def __init__(self, project_id: str, label: str, area_sqm: float):
        self.project_id = project_id
        self.label = label
        self.area_sqm = float(area_sqm)
        self.floors: List[Floor] = []

    @property
    def zones(self) -> List[Zone]:
        return [z for f in self.floors for z in f.zones]

    def to_dict(self) -> Dict[str, Any]:
        return dict(project_id=self.project_id, label=self.label, area_sqm=self.area_sqm,
                    floors=[f.to_dict() for f in self.floors],
                    zones=[z.to_dict() for z in self.zones])


# ---------------------------------------------------------------------------
# zone generation
# ---------------------------------------------------------------------------
def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def generate_zones_auto(area_sqm: float, model: Optional[Dict[str, Any]] = None,
                        single_crew_trade_days: Optional[float] = None) -> Dict[str, Any]:
    """Recommend a zone count. Returns {zones:[...], method, reason, alternatives, warnings}.

    The primary method is by_area; other methods are computed for cross-check only and
    never override it. Every number carries the formula that produced it.
    """
    model = model or load_spatial_model()
    sizing = model["zone_sizing"]
    gen = model["zone_generation"]
    recommended = float(sizing["recommended_zone_area_sqm"])
    wf_area = float(sizing["workfront_area_sqm"])
    lo, hi = int(sizing["min_zones"]), int(sizing["max_zones"])
    area_min = float(sizing["min_zone_area_sqm"])
    area_max = float(sizing["max_zone_area_sqm"])

    alternatives: List[Dict[str, Any]] = []
    primary: Dict[str, Any] = {}

    for method in gen["auto_methods"]:
        mid = method["id"]
        if mid == "by_area":
            raw = int(math.ceil(float(area_sqm) / recommended))
            n = _clamp(raw, lo, hi)
            row = dict(method=mid, zone_count=n, raw=raw,
                       reason=method["reason_template"].format(
                           area=area_sqm, recommended=recommended, raw=raw, min=lo, max=hi, n=n,
                           per_zone=area_sqm / n if n else 0))
        elif mid == "by_workfront_capacity":
            raw = int(math.floor(float(area_sqm) / wf_area))
            n = _clamp(raw, lo, hi)
            row = dict(method=mid, zone_count=n, raw=raw,
                       reason=method["reason_template"].format(area=area_sqm, wf=wf_area, n=n))
        elif mid == "by_target_duration":
            if not method.get("enabled", False) or single_crew_trade_days is None:
                continue
            target = float(method.get("target_zone_chain_days", 60))
            raw = int(math.ceil(float(single_crew_trade_days) / target))
            n = _clamp(raw, lo, hi)
            row = dict(method=mid, zone_count=n, raw=raw,
                       reason=method["reason_template"].format(total=single_crew_trade_days,
                                                               target=target, n=n))
        else:  # pragma: no cover - unknown configured method
            continue
        row["cross_check_only"] = (mid != gen["primary_method"])
        if mid == gen["primary_method"]:
            primary = row
        alternatives.append(row)

    if not primary:
        raise ValueError("no primary zone generation method produced a result")

    n = int(primary["zone_count"])
    areas = _balance_areas(area_sqm, n)

    warnings: List[str] = []
    for a in areas:
        if a < area_min:
            warnings.append(f"分区面积 {a:g}㎡ 低于下限 {area_min:g}㎡：分区过小，单工序易触发 min_duration_days 下限，流水收益被吞掉。")
        if a > area_max:
            warnings.append(f"分区面积 {a:g}㎡ 高于上限 {area_max:g}㎡：分区过大，流水段数不足。")

    return dict(zone_count=n, areas=areas, method=primary["method"], reason=primary["reason"],
                primary_method_reason=gen["primary_method_reason"],
                alternatives=alternatives, warnings=warnings)


def _balance_areas(area_sqm: float, n: int) -> List[float]:
    """Split area into n zones; integer part even, remainder distributed to the first zones."""
    n = max(1, int(n))
    base = float(area_sqm) / n
    if area_sqm == int(area_sqm) and n > 0:
        whole = int(area_sqm) // n
        rem = int(area_sqm) - whole * n
        return [float(whole + (1 if i < rem else 0)) for i in range(n)]
    return [round(base, 1)] * n


def split_area(area_sqm: float, n: int) -> List[float]:
    """Public entry for the balanced area split used by the fragmentation sweep."""
    return _balance_areas(area_sqm, n)


def generate_zones_manual(specs: Sequence[Dict[str, Any]], area_sqm: float,
                          model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build zones from explicit user input. Area must reconcile with the project area."""
    model = model or load_spatial_model()
    sizing = model["zone_sizing"]
    if not specs:
        raise ValueError("manual zone generation requires at least one zone")
    total = float(sum(float(s.get("area_sqm", 0) or 0) for s in specs))
    if abs(total - float(area_sqm)) > 1e-6:
        raise ValueError(f"manual zone areas sum to {total:g}㎡ but project area is {area_sqm:g}㎡")
    warnings: List[str] = []
    for s in specs:
        a = float(s["area_sqm"])
        if a < float(sizing["min_zone_area_sqm"]):
            warnings.append(f"{s.get('zone_id')} 面积 {a:g}㎡ 低于下限 {sizing['min_zone_area_sqm']:g}㎡")
        if a > float(sizing["max_zone_area_sqm"]):
            warnings.append(f"{s.get('zone_id')} 面积 {a:g}㎡ 高于上限 {sizing['max_zone_area_sqm']:g}㎡")
    return dict(zone_count=len(specs), areas=[float(s["area_sqm"]) for s in specs],
                method="manual", reason=f"用户显式指定 {len(specs)} 个分区，面积合计 {total:g}㎡ 与项目面积一致",
                alternatives=[], warnings=warnings)


def normalise_floors(area_sqm: float, floors_count: int = 1, floor_breakdown=None,
                     floor_label: str = "F1") -> List[Dict[str, Any]]:
    """Resolve Project → Floor decomposition as ``[{"floor_id","area_sqm"}]``.

    Priority: explicit ``floor_breakdown`` > ``floors_count`` > the single legacy floor.
    Floor areas are reconciled through :func:`apportion` so Σ floor area == project area
    exactly — no rounding drift is allowed to leak into a floor take-off.
    """
    if floor_breakdown:
        specs: List[Dict[str, Any]] = []
        for row in floor_breakdown:
            fid = str(row.get("floor_id") or "").strip()
            if not fid:
                raise ValueError("floor_breakdown entries must carry floor_id")
            a = row.get("area_sqm")
            if a is None or float(a) <= 0:
                raise ValueError(f"{fid}: floor area_sqm must be > 0")
            specs.append(dict(floor_id=fid, area_sqm=float(a)))
        total = float(sum(s["area_sqm"] for s in specs))
        if abs(total - float(area_sqm)) > 1e-6:
            raise ValueError(f"floor_breakdown areas sum to {total:g}㎡ but the project "
                             f"area is {float(area_sqm):g}㎡")
        return specs

    n = int(floors_count or 1)
    if isinstance(floors_count, bool) or n < 1:
        raise ValueError(f"floors_count must be an integer >= 1, got {floors_count!r}")
    if n == 1:
        return [dict(floor_id=str(floor_label or "F1"), area_sqm=float(area_sqm))]
    parts = apportion(float(area_sqm), [1.0] * n)
    return [dict(floor_id=f"F{i + 1:02d}", area_sqm=float(p)) for i, p in enumerate(parts)]


def _assign_manual_zones(specs: Sequence[Dict[str, Any]],
                         floors: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Distribute project-level manual zone specs across floors.

    An explicit ``floor_id`` on a spec wins. Otherwise specs are consumed in order and
    each floor must be filled exactly to its own area — a floor take-off may not be
    topped up with another floor's zone.
    """
    if all(s.get("floor_id") for s in specs):
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for s in specs:
            grouped.setdefault(str(s["floor_id"]), []).append(dict(s))
        for f in floors:
            zs = grouped.get(f["floor_id"]) or []
            if not zs:
                raise ValueError(f"{f['floor_id']}: no manual zone assigned to this floor")
            tot = float(sum(float(z.get("area_sqm", 0) or 0) for z in zs))
            if abs(tot - f["area_sqm"]) > 1e-6:
                raise ValueError(f"{f['floor_id']}: manual zones sum to {tot:g}㎡ but the "
                                 f"floor area is {f['area_sqm']:g}㎡")
        return grouped

    out: Dict[str, List[Dict[str, Any]]] = {f["floor_id"]: [] for f in floors}
    idx = 0
    for f in floors:
        acc = 0.0
        while idx < len(specs) and acc < f["area_sqm"] - 1e-9:
            s = dict(specs[idx])
            out[f["floor_id"]].append(s)
            acc += float(s.get("area_sqm", 0) or 0)
            idx += 1
        if abs(acc - f["area_sqm"]) > 1e-6:
            raise ValueError(
                f"{f['floor_id']}: manual zones fill {acc:g}㎡ but the floor area is "
                f"{f['area_sqm']:g}㎡ — a manual zone may not straddle two floors")
    if idx != len(specs):
        raise ValueError("manual zone areas exceed the declared floor areas")
    return out


def build_spatial_project(area_sqm: float, mode: str = "auto", manual_zones=None,
                          floor_label: str = "F1", model: Optional[Dict[str, Any]] = None,
                          single_crew_trade_days: Optional[float] = None,
                          project_quantities: Optional[Dict[str, Dict[str, Any]]] = None,
                          floors_count: int = 1, floor_breakdown=None) -> Dict[str, Any]:
    """Assemble Project → Floor → Zone and apportion quantities.

    Single-floor (the default) keeps the V6.1 namespace and code path byte-identical.
    Multi-floor generates zones WITHIN each floor and uses the composite namespace
    ``{floor_id}-{zone_id}`` (e.g. ``F01-Z01``), which orders floor-major and therefore
    produces the vertical line-of-balance for free.
    """
    model = model or load_spatial_model()
    if mode not in ("manual", "auto"):
        raise ValueError(f"zone mode must be manual|auto, got {mode!r}")
    floors = normalise_floors(area_sqm, floors_count, floor_breakdown, floor_label)
    multi = len(floors) > 1
    letters = "ABCDEFGH"

    project = SpatialProject("P1", "项目", area_sqm)

    if not multi:
        # ---- legacy single-floor path (unchanged) -------------------------
        gen = (generate_zones_manual(manual_zones, area_sqm, model) if mode == "manual"
               else generate_zones_auto(area_sqm, model, single_crew_trade_days))
        floor = Floor("F1", floor_label, area_sqm)
        for i, zarea in enumerate(gen["areas"]):
            zid = f"Z{i + 1}"
            label = f"{letters[i] if i < len(letters) else 'Z' + str(i + 1)}区"
            zone = Zone(zid, label, floor.floor_id, zarea, generation_reason=gen["reason"])
            zone.available_condition = model["release_conditions"]["zone_available"]["condition"]
            zone.release_condition = model["release_conditions"]["zone_released"]["condition"]
            floor.zones.append(zone)
        project.floors.append(floor)
    else:
        # ---- multi-floor path ---------------------------------------------
        if mode == "manual" and not manual_zones:
            raise ValueError("manual zone mode with multiple floors requires manual_zones")
        assignment = _assign_manual_zones(manual_zones, floors) if mode == "manual" else None
        per_floor: List[Dict[str, Any]] = []
        warnings: List[str] = []
        for f in floors:
            fid, farea = f["floor_id"], f["area_sqm"]
            if mode == "manual":
                specs = assignment[fid]
                gen_f = generate_zones_manual(specs, farea, model)
                raw_ids = [str(s.get("zone_id") or f"Z{i + 1}") for i, s in enumerate(specs)]
            else:
                gen_f = generate_zones_auto(farea, model, single_crew_trade_days)
                raw_ids = [f"Z{i + 1}" for i in range(len(gen_f["areas"]))]
            floor = Floor(fid, fid, farea)
            for i, zarea in enumerate(gen_f["areas"]):
                zid = f"{fid}-{raw_ids[i]}"
                label = f"{fid}-{letters[i] if i < len(letters) else 'Z' + str(i + 1)}区"
                zone = Zone(zid, label, fid, zarea,
                            generation_reason=f"[{fid}] {gen_f['reason']}")
                zone.available_condition = model["release_conditions"]["zone_available"]["condition"]
                zone.release_condition = model["release_conditions"]["zone_released"]["condition"]
                floor.zones.append(zone)
            project.floors.append(floor)
            per_floor.append(dict(floor_id=fid, area_sqm=farea,
                                  zone_count=len(floor.zones),
                                  zone_ids=[z.zone_id for z in floor.zones],
                                  basis=gen_f.get("reason")))
            warnings.extend(f"[{fid}] {w}" for w in (gen_f.get("warnings") or []))
        floor_areas = [f["area_sqm"] for f in floors]
        floor_ids_txt = "/".join(str(f["floor_id"]) for f in floors)
        floor_area_txt = "、".join(
            "{0}={1:g}㎡".format(f["floor_id"], f["area_sqm"]) for f in floors)
        gen = dict(
            zone_count=len(project.zones),
            areas=[z.area_sqm for z in project.zones],
            method=("manual" if mode == "manual" else "by_area_per_floor"),
            reason=("{0} 个楼层（{1}）逐层生成分区；楼板面积 {2}"
                    .format(len(floors), floor_ids_txt, floor_area_txt)),
            primary_method_reason=model["zone_generation"]["primary_method_reason"],
            alternatives=[], warnings=warnings,
            floors=per_floor,
            floor_count=len(floors),
            floor_area_balance=dict(
                areas=floor_areas,
                equal=(max(floor_areas) - min(floor_areas) <= 1e-6 if floor_areas else True),
                skew_pct=round((max(floor_areas) - min(floor_areas)) / max(floor_areas) * 100, 2)
                if floor_areas and max(floor_areas) else 0.0),
        )

    allocation: Dict[str, Dict[str, Dict[str, Any]]] = {}
    issues: List[Dict[str, Any]] = []
    if project_quantities:
        allocation = allocate_zone_quantities(project_quantities, project.zones)
        issues = verify_allocation(project_quantities, allocation)
        for zone in project.zones:
            zone.quantities = allocation[zone.zone_id]

    return dict(project=project, generation=gen, allocation=allocation,
                allocation_issues=issues, model_version=model.get("version"),
                floors=[f.to_dict() for f in project.floors])


# ---------------------------------------------------------------------------
# spatial scope
# ---------------------------------------------------------------------------
def spatial_scope(activity_type: Optional[str], model: Optional[Dict[str, Any]] = None) -> str:
    """Resolve the spatial scope of one activity_type. Explicit declaration only."""
    model = model or load_spatial_model()
    scope_cfg = model["spatial_scope"]
    if not activity_type:
        return "project"
    for scope in ("project", "floor", "zone"):
        if activity_type in (scope_cfg.get(scope) or {}).get("activity_types", []):
            return scope
    return scope_cfg.get("default", "zone")


def spatial_scope_reason(activity_type: Optional[str], model: Optional[Dict[str, Any]] = None) -> str:
    model = model or load_spatial_model()
    scope_cfg = model["spatial_scope"]
    if not activity_type:
        return "无 activity_type（许可、里程碑、审批等非生产任务）→ 项目级单实例"
    for scope in ("project", "floor", "zone"):
        if activity_type in (scope_cfg.get(scope) or {}).get("activity_types", []):
            return (scope_cfg[scope].get("reason") or "")
    return scope_cfg.get("default_reason", "")


def gate_scope(gate_name: str, model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve how one inspection gate task is instantiated (zone / floor / project)."""
    model = model or load_spatial_model()
    for entry in model["gate_instantiation"]["by_keyword"]:
        if entry["keyword"] in str(gate_name):
            return dict(scope=entry["scope"], condition=entry.get("condition"),
                        reason=entry["reason"], keyword=entry["keyword"])
    return dict(scope=model["gate_instantiation"]["default_scope"], condition=None,
                reason=model["gate_instantiation"]["default_reason"], keyword=None)
