"""V6.1.1 duration semantics hardening.

The problem this module exists to fix
-------------------------------------
`config/productivity_rates.json` carries one scalar `min_duration_days` per activity
type, and V6.1 applied it to *every* spatial instance. When one activity is split into
N zones that lower bound is therefore paid N times. That is not a physical constraint —
it is an artefact of discretising the model — and it silently eats the pipeline benefit
that spatial decomposition is supposed to create.

What this module does
---------------------
1. Gives `min_duration_days` an explicit semantic scope:
       PROJECT | FLOOR | ZONE | MOBILISATION | INSPECTION_CYCLE
   Unclassified entries are `LEGACY_UNSPECIFIED` and keep the existing behaviour, so no
   historical value is silently reinterpreted and no safeguard is loosened by accident.
2. Recomputes a zone activity's duration as
       zone_quantity / (productivity x crew x shifts x efficiency) x modifiers
   then applies ONLY the minimum that is physically applicable at that instance's scope.
   A PROJECT-level minimum is never multiplied by the zone count; it is checked once
   against the *sum* of the zone durations and reported, never silently back-filled.
3. Models mobilisation as its own task instead of hiding it inside every zone's
   production minimum.
4. Locks `target_days` as a CREW SIZING HORIZON (never a duration cap).
5. Emits per-activity diagnostics (raw / rounded / applied min / scope / rounding
   penalty / minimum-duration penalty) so discretisation inflation is visible.

Hard rules
----------
* This module never writes to config/productivity_rates.json or
  config/dependency_ontology.json or config/spatial_model.json.
* Unclassified == preserve existing behaviour. No invented classification.
* Nothing here exists to make the spatial plan look faster.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DURATION_SEMANTICS_PATH = os.path.join(BASE_DIR, "config", "duration_semantics.json")

SCOPE_PROJECT = "PROJECT"
SCOPE_FLOOR = "FLOOR"
SCOPE_ZONE = "ZONE"
SCOPE_MOBILISATION = "MOBILISATION"
SCOPE_INSPECTION_CYCLE = "INSPECTION_CYCLE"
SCOPE_LEGACY = "LEGACY_UNSPECIFIED"
SCOPE_NONE = "NONE"

DECLARED_SCOPES = (SCOPE_PROJECT, SCOPE_FLOOR, SCOPE_ZONE, SCOPE_MOBILISATION, SCOPE_INSPECTION_CYCLE)
ALL_SCOPES = DECLARED_SCOPES + (SCOPE_LEGACY, SCOPE_NONE)

ST_APPLIED_ZONE = "APPLIED_ZONE"
ST_APPLIED_FLOOR = "APPLIED_FLOOR"
ST_APPLIED_PROJECT = "APPLIED_PROJECT"
ST_APPLIED_TO_GATE = "APPLIED_TO_GATE"
ST_NOT_APPLIED_PROJECT_AGGREGATE = "NOT_APPLIED_PROJECT_AGGREGATE"
ST_NOT_APPLIED_FLOOR_AGGREGATE = "NOT_APPLIED_FLOOR_AGGREGATE"
ST_NOT_APPLIED_MOBILISATION = "NOT_APPLIED_MOBILISATION_MODELLED_SEPARATELY"
ST_NOT_APPLIED_GATE_ONLY = "NOT_APPLIED_GATE_ONLY"
ST_NOT_APPLIED_NO_MATCH = "NOT_APPLIED_NO_MATCHING_SCOPE"
ST_LEGACY_PRESERVED = "LEGACY_PRESERVED"
ST_NO_LIBRARY_MIN = "NO_LIBRARY_MIN"

DIAGNOSTIC_FIELDS = (
    "raw_calculated_duration",
    "rounded_duration",
    "applied_min_duration",
    "min_duration_scope",
    "rounding_penalty_days",
    "minimum_duration_penalty_days",
)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def load_duration_semantics(path: Optional[str] = None) -> Dict[str, Any]:
    with open(path or DURATION_SEMANTICS_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    validate_duration_semantics(cfg)
    return cfg


def validate_duration_semantics(cfg: Dict[str, Any]) -> None:
    """Reject a malformed or unsubstantiated classification rather than degrading silently."""
    if not isinstance(cfg, dict):
        raise ValueError("duration_semantics must be a JSON object")
    for scope in DECLARED_SCOPES:
        if scope not in (cfg.get("min_duration_scope_definitions") or {}):
            raise ValueError(f"duration_semantics: missing scope definition '{scope}'")
    for name, entry in (cfg.get("classification") or {}).items():
        pol = (entry or {}).get("min_duration_policy") or entry
        if "scope" not in pol:
            raise ValueError(f"classification '{name}': min_duration_policy.scope is mandatory")
        scopes = pol["scope"] if isinstance(pol["scope"], (list, tuple)) else [pol["scope"]]
        for s in scopes:
            if s not in DECLARED_SCOPES:
                raise ValueError(f"classification '{name}': unknown scope '{s}'; allowed {list(DECLARED_SCOPES)}")
        if not pol.get("basis"):
            raise ValueError(f"classification '{name}': 'basis' is mandatory (why is this the right scope?)")
        if not pol.get("confirmed_by"):
            raise ValueError(f"classification '{name}': 'confirmed_by' is mandatory (unconfirmed entries must not ship)")
        if "value" in pol and (not float(pol["value"]).is_integer() or int(pol["value"]) < 0):
            raise ValueError(f"classification '{name}': value must be a non-negative integer number of days")
    for name, spec in (cfg.get("mobilisation") or {}).items():
        if int(spec.get("duration_days", 0)) < 0:
            raise ValueError(f"mobilisation '{name}': duration_days must be >= 0")
        if spec.get("scope", "project") not in ("project", "floor", "zone"):
            raise ValueError(f"mobilisation '{name}': scope must be project|floor|zone")
        if not spec.get("basis"):
            raise ValueError(f"mobilisation '{name}': 'basis' is mandatory")
        if not spec.get("confirmed_by"):
            raise ValueError(f"mobilisation '{name}': 'confirmed_by' is mandatory")
    sem = cfg.get("target_days_semantics") or {}
    if sem.get("definition") != "CREW_SIZING_HORIZON":
        raise ValueError("duration_semantics: target_days_semantics.definition must stay 'CREW_SIZING_HORIZON'")


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------
def _scopes_of(policy: Dict[str, Any]) -> List[str]:
    raw = policy.get("scope")
    if raw is None:
        return []
    scopes = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    return [str(s) for s in scopes]


def classify_min_duration(activity_type: Optional[str], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the declared minimum-duration policy for one activity type.

    Unclassified -> status UNCLASSIFIED, scope LEGACY_UNSPECIFIED. There is no guessing:
    an activity with no explicit entry keeps its historical behaviour.
    """
    cfg = cfg or load_duration_semantics()
    entry = (cfg.get("classification") or {}).get(activity_type or "")
    if not entry:
        return dict(activity_type=activity_type, status="UNCLASSIFIED",
                    min_duration_scope=SCOPE_LEGACY, declared_scopes=[], value=None,
                    basis=None, confirmed_by=None, policy=None)
    policy = entry.get("min_duration_policy") or entry
    return dict(activity_type=activity_type, status="CLASSIFIED",
                min_duration_scope=_scopes_of(policy),
                declared_scopes=_scopes_of(policy),
                value=(int(policy["value"]) if policy.get("value") is not None else None),
                basis=policy.get("basis"), confirmed_by=policy.get("confirmed_by"),
                policy=policy)


def library_min_duration(activity_type: Optional[str], rates: Optional[Dict[str, Any]] = None) -> int:
    if not activity_type:
        return 0
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    entry = (rates.get("activity_types") or {}).get(activity_type) or {}
    return int(entry.get("min_duration_days", 1) or 0)


def resolve_min_for_instance(activity_type: Optional[str], instance_scope: str = "zone",
                             is_gate: bool = False, cfg: Optional[Dict[str, Any]] = None,
                             rates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Decide which minimum duration, if any, applies to ONE task instance.

    instance_scope: project | floor | zone (the spatial scope the instance was emitted at).

    Returns {applied_min_duration, min_duration_scope, declared_scopes, status,
             aggregate_scope, aggregate_value, legacy_min_duration_days, reason}.
    `applied_min_duration == 0` means "no minimum applies here".
    """
    cfg = cfg or load_duration_semantics()
    legacy_min = library_min_duration(activity_type, rates)
    cls = classify_min_duration(activity_type, cfg)
    base = dict(activity_type=activity_type, instance_scope=instance_scope, is_gate=is_gate,
                declared_scopes=cls["declared_scopes"], legacy_min_duration_days=legacy_min,
                aggregate_scope=None, aggregate_value=None)

    if cls["status"] != "CLASSIFIED":
        return dict(base, applied_min_duration=legacy_min, min_duration_scope=SCOPE_LEGACY,
                    status=(ST_LEGACY_PRESERVED if legacy_min else ST_NO_LIBRARY_MIN),
                    reason=("语义未分类 → LEGACY_UNSPECIFIED：沿用库中的 min_duration_days="
                            f"{legacy_min} 并保持现有行为；由此产生的膨胀记入 legacy_min_penalty_days，"
                            "由计划工程师判定后显式分类。"))

    value = cls["value"] if cls["value"] is not None else legacy_min
    declared = set(cls["declared_scopes"])

    if is_gate:
        if SCOPE_INSPECTION_CYCLE in declared:
            return dict(base, applied_min_duration=int(value), min_duration_scope=SCOPE_INSPECTION_CYCLE,
                        status=ST_APPLIED_TO_GATE,
                        reason=f"检查门最短周期 {value} 天（INSPECTION_CYCLE）：{cls['basis']}")
        return dict(base, applied_min_duration=0, min_duration_scope=SCOPE_NONE,
                    status=ST_NOT_APPLIED_GATE_ONLY,
                    reason="检查门任务未声明 INSPECTION_CYCLE 下限 → 不套用生产工序的最短工期。")

    if SCOPE_ZONE in declared:
        return dict(base, applied_min_duration=int(value), min_duration_scope=SCOPE_ZONE,
                    status=ST_APPLIED_ZONE,
                    reason=f"分区级物理最短工期 {value} 天（ZONE，显式声明）：{cls['basis']}")

    if SCOPE_MOBILISATION in declared:
        return dict(base, applied_min_duration=0, min_duration_scope=SCOPE_MOBILISATION,
                    status=ST_NOT_APPLIED_MOBILISATION,
                    reason=(f"该最短工期实为进场比赛/准备时间 {value} 天（MOBILISATION）："
                            "必须建模为独立动员任务，不得藏进每个分区的生产下限。"))

    if SCOPE_FLOOR in declared:
        if instance_scope == "floor":
            return dict(base, applied_min_duration=int(value), min_duration_scope=SCOPE_FLOOR,
                        status=ST_APPLIED_FLOOR,
                        reason=f"楼层级最短工期 {value} 天（FLOOR）：{cls['basis']}")
        return dict(base, applied_min_duration=0, min_duration_scope=SCOPE_FLOOR,
                    status=ST_NOT_APPLIED_FLOOR_AGGREGATE, aggregate_scope="floor",
                    aggregate_value=int(value),
                    reason=(f"FLOOR 级最短工期 {value} 天不按分区逐个施加；"
                            "改按楼层聚合校验 Σ分区工期，缺口如实报告而不回填。"))

    if SCOPE_PROJECT in declared:
        if instance_scope == "project":
            return dict(base, applied_min_duration=int(value), min_duration_scope=SCOPE_PROJECT,
                        status=ST_APPLIED_PROJECT,
                        reason=f"项目级最短工期 {value} 天（PROJECT）：{cls['basis']}")
        return dict(base, applied_min_duration=0, min_duration_scope=SCOPE_PROJECT,
                    status=ST_NOT_APPLIED_PROJECT_AGGREGATE, aggregate_scope="project",
                    aggregate_value=int(value),
                    reason=(f"PROJECT 级最短工期 {value} 天不得因空间分解而乘以分区数；"
                            "本实例施加 0，改由项目聚合校验 Σ分区工期是否达到下限。"))

    return dict(base, applied_min_duration=0, min_duration_scope=SCOPE_NONE,
                status=ST_NOT_APPLIED_NO_MATCH,
                reason=f"声明的作用域 {cls['declared_scopes']} 与实例作用域 '{instance_scope}' 不匹配 → 不施加。")


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------
def _instance_scope(task: Dict[str, Any]) -> str:
    return str(task.get("spatial_scope") or "project")


def _diagnostics(raw: float, rounded: int, applied_min: int, scope: str) -> Dict[str, Any]:
    final = max(int(applied_min or 0), int(rounded))
    return dict(
        raw_calculated_duration=round(float(raw), 6),
        rounded_duration=int(rounded),
        applied_min_duration=int(applied_min or 0),
        min_duration_scope=str(scope),
        rounding_penalty_days=round(int(rounded) - float(raw), 6),
        minimum_duration_penalty_days=int(final - int(rounded)),
    )


def apply_scope_aware_minimums(tasks: Sequence[Dict[str, Any]],
                               cfg: Optional[Dict[str, Any]] = None,
                               rates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Re-resolve the minimum duration of every already-computed task by scope.

    Requires `apply_productivity_durations` to have run (tasks carry a `productivity`
    block). Gate tasks keep their template duration and only receive an INSPECTION_CYCLE
    minimum when one is explicitly declared.

    Returns {rows, aggregate_checks, totals, changed_task_ids}.
    """
    from core.productivity import final_duration_days

    cfg = cfg or load_duration_semantics()
    rows: List[Dict[str, Any]] = []
    agg: Dict[Any, Dict[str, Any]] = {}
    changed: List[int] = []

    for task in tasks:
        activity = task.get("activity_type")

        # ---- inspection gates: template duration + optional declared cycle --------
        if task.get("inspection_gate"):
            res = resolve_min_for_instance(activity, _instance_scope(task), is_gate=True,
                                           cfg=cfg, rates=rates)
            current = int(round(float(task.get("duration_days", task.get("duration", 0)) or 0)))
            applied = int(res["applied_min_duration"] or 0)
            final = max(applied, current)
            diag = _diagnostics(float(current), current, applied, res["min_duration_scope"])
            task["duration_days"] = final
            task["duration"] = final
            task["duration_semantics"] = dict(diag, min_duration_status=res["status"],
                                              min_duration_reason=res["reason"],
                                              legacy_min_duration_days=res["legacy_min_duration_days"],
                                              is_gate=True)
            if applied:
                rows.append(dict(task_id=task["id"], name=task.get("name"), activity_type=activity,
                                 zone=task.get("zone"), is_gate=True, **diag,
                                 status=res["status"]))
            if final != current:
                changed.append(task["id"])
            continue

        prod = task.get("productivity")
        if not prod or task.get("duration_method") != "productivity_formula":
            continue

        res = resolve_min_for_instance(activity, _instance_scope(task), is_gate=False,
                                       cfg=cfg, rates=rates)
        raw = float(prod.get("raw_calculated_duration", prod.get("calculated_duration", 0.0)) or 0.0)
        rounded = int(prod.get("rounded_duration", math.ceil(raw)))
        applied = int(res["applied_min_duration"] or 0)
        previous = int(task.get("duration_days", task.get("duration", 0)) or 0)
        final = max(applied, rounded)

        diag = _diagnostics(raw, rounded, applied, res["min_duration_scope"])
        task["duration_days"] = final
        task["duration"] = final
        prod.update(applied_min_duration=diag["applied_min_duration"],
                    min_duration_scope=diag["min_duration_scope"],
                    min_duration_status=res["status"],
                    min_duration_reason=res["reason"],
                    raw_calculated_duration=diag["raw_calculated_duration"],
                    rounded_duration=diag["rounded_duration"],
                    rounding_penalty_days=diag["rounding_penalty_days"],
                    minimum_duration_penalty_days=diag["minimum_duration_penalty_days"],
                    final_duration=final)
        # keep the rate-level range coherent with the minimum actually applied
        table = prod.get("rate_table") or {}
        if table:
            rf = float(prod.get("shifts", 1)) * float(prod.get("workfront_efficiency", 1))
            prod["duration_by_rate_level"] = {
                lv: final_duration_days(float(prod.get("quantity", 0.0)), float(r) * rf,
                                        int(prod.get("crew_size", 1) or 1),
                                        float(prod.get("factor_product", 1.0)), applied)
                for lv, r in table.items()
            }
        task["duration_semantics"] = dict(diag, min_duration_status=res["status"],
                                          min_duration_reason=res["reason"],
                                          legacy_min_duration_days=res["legacy_min_duration_days"],
                                          is_gate=False)
        if final != previous:
            changed.append(task["id"])

        rows.append(dict(task_id=task["id"], name=task.get("name"), activity_type=activity,
                         zone=task.get("zone"), spatial_scope=_instance_scope(task),
                         quantity=prod.get("quantity"),
                         is_gate=False, **diag, status=res["status"],
                         legacy_min_duration_days=res["legacy_min_duration_days"],
                         previous_duration_days=previous, final_duration_days=final))

        if res["aggregate_scope"] and res["aggregate_value"]:
            key = (activity, res["aggregate_scope"],
                   task.get("floor") if res["aggregate_scope"] == "floor" else None)
            bucket = agg.setdefault(key, dict(activity_type=activity,
                                              aggregate_scope=res["aggregate_scope"],
                                              floor=task.get("floor"),
                                              required_min_days=int(res["aggregate_value"]),
                                              instances=0, rounded_sum_days=0.0, task_ids=[]))
            bucket["instances"] += 1
            bucket["rounded_sum_days"] += rounded
            bucket["task_ids"].append(task["id"])

    aggregate_checks: List[Dict[str, Any]] = []
    for bucket in agg.values():
        bucket["rounded_sum_days"] = round(bucket["rounded_sum_days"], 6)
        shortfall = round(float(bucket["required_min_days"]) - bucket["rounded_sum_days"], 6)
        bucket["shortfall_days"] = max(0.0, shortfall)
        bucket["meets_minimum"] = shortfall <= 0
        if shortfall > 0:
            aggregate_checks.append(dict(
                level="warning", code="MIN_AGGREGATE_UNDERRUN",
                activity_type=bucket["activity_type"],
                aggregate_scope=bucket["aggregate_scope"], floor=bucket["floor"],
                required_min_days=bucket["required_min_days"],
                rounded_sum_days=bucket["rounded_sum_days"],
                shortfall_days=bucket["shortfall_days"],
                instances=bucket["instances"], task_ids=bucket["task_ids"],
                action="report_only",
                message=(f"{bucket['activity_type']} 在 {bucket['aggregate_scope']} 级要求最短 "
                         f"{bucket['required_min_days']} 天，但 {bucket['instances']} 个分区取整工期合计仅 "
                         f"{bucket['rounded_sum_days']:g} 天，缺口 {bucket['shortfall_days']:g} 天。"
                         "按约定只报告、不静默回填到分区工期；如确需保障，请作为独立约束/任务显式建模。")))

    return dict(rows=rows, aggregate_checks=aggregate_checks,
                totals=_sum_rows(rows), changed_task_ids=changed)


def _sum_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = dict(instances=len(rows), production_instances=0, gate_instances=0,
                 raw_calculated_days=0.0, rounded_days=0, final_days=0,
                 rounding_penalty_days=0.0, minimum_duration_penalty_days=0,
                 legacy_min_penalty_days=0)
    for r in rows:
        if r.get("is_gate"):
            total["gate_instances"] += 1
        else:
            total["production_instances"] += 1
        total["raw_calculated_days"] += float(r.get("raw_calculated_duration", 0) or 0)
        total["rounded_days"] += int(r.get("rounded_duration", 0) or 0)
        total["final_days"] += int(r.get("rounded_duration", 0) or 0) + int(r.get("minimum_duration_penalty_days", 0) or 0)
        total["rounding_penalty_days"] += float(r.get("rounding_penalty_days", 0) or 0)
        total["minimum_duration_penalty_days"] += int(r.get("minimum_duration_penalty_days", 0) or 0)
        if r.get("min_duration_scope") == SCOPE_LEGACY:
            total["legacy_min_penalty_days"] += int(r.get("minimum_duration_penalty_days", 0) or 0)
    total["raw_calculated_days"] = round(total["raw_calculated_days"], 6)
    total["rounding_penalty_days"] = round(total["rounding_penalty_days"], 6)
    return total


def plan_duration_diagnostics(tasks: Sequence[Dict[str, Any]],
                              cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Aggregate per-task duration diagnostics for a whole plan (for CSV / report)."""
    cfg = cfg or load_duration_semantics()
    rows: List[Dict[str, Any]] = []
    by_scope: Dict[str, Dict[str, Any]] = {}
    by_activity: Dict[str, Dict[str, Any]] = {}
    for t in tasks:
        d = t.get("duration_semantics")
        if not d:
            continue
        row = dict(task_id=t.get("id"), name=t.get("name"), activity_type=t.get("activity_type"),
                   zone=t.get("zone"), zone_label=t.get("zone_label"),
                   spatial_scope=t.get("spatial_scope"), is_gate=bool(t.get("inspection_gate")),
                   duration_days=int(t.get("duration_days", t.get("duration", 0)) or 0),
                   **{k: d.get(k) for k in DIAGNOSTIC_FIELDS})
        row["min_duration_status"] = d.get("min_duration_status")
        rows.append(row)
        s = by_scope.setdefault(str(d.get("min_duration_scope")),
                                dict(instances=0, rounding_penalty_days=0.0,
                                     minimum_duration_penalty_days=0, final_days=0))
        s["instances"] += 1
        s["rounding_penalty_days"] = round(s["rounding_penalty_days"] + float(d.get("rounding_penalty_days", 0) or 0), 6)
        s["minimum_duration_penalty_days"] += int(d.get("minimum_duration_penalty_days", 0) or 0)
        s["final_days"] += row["duration_days"]
        a = by_activity.setdefault(str(t.get("activity_type")),
                                   dict(instances=0, raw_calculated_days=0.0, rounded_days=0,
                                        final_days=0, rounding_penalty_days=0.0,
                                        minimum_duration_penalty_days=0))
        a["instances"] += 1
        a["raw_calculated_days"] = round(a["raw_calculated_days"] + float(d.get("raw_calculated_duration", 0) or 0), 6)
        a["rounded_days"] += int(d.get("rounded_duration", 0) or 0)
        a["final_days"] += row["duration_days"]
        a["rounding_penalty_days"] = round(a["rounding_penalty_days"] + float(d.get("rounding_penalty_days", 0) or 0), 6)
        a["minimum_duration_penalty_days"] += int(d.get("minimum_duration_penalty_days", 0) or 0)
    totals = _sum_rows(rows)
    return dict(version=cfg.get("version"), rows=rows, by_min_scope=by_scope,
                by_activity=[dict(activity_type=k, **v) for k, v in sorted(by_activity.items())],
                totals=totals)


# ---------------------------------------------------------------------------
# mobilisation
# ---------------------------------------------------------------------------
def _mobilisation_task(task_id: int, name: str, duration: int, scope: str, zone: Optional[str],
                       floor: Optional[str], target_key: str, basis: str,
                       confirmed_by: str) -> Dict[str, Any]:
    return dict(id=task_id, name=name, duration_days=int(duration), duration=int(duration),
                predecessors="", outline_level=3, milestone=False,
                spatial_scope=("zone" if scope == "zone" else scope),
                zone=zone, floor=floor, activity_type=None, inspection_gate=False,
                is_mobilisation=True, mobilisation_for=target_key,
                mobilisation_basis=basis, mobilisation_confirmed_by=confirmed_by,
                duration_method="mobilisation_explicit",
                duration_semantics=dict(raw_calculated_duration=float(duration),
                                        rounded_duration=int(duration),
                                        applied_min_duration=0,
                                        min_duration_scope=SCOPE_MOBILISATION,
                                        rounding_penalty_days=0.0,
                                        minimum_duration_penalty_days=0,
                                        min_duration_status=ST_NOT_APPLIED_MOBILISATION,
                                        min_duration_reason="动员时间作为独立任务建模，不计入任何分区的生产下限。",
                                        is_gate=False))


def apply_mobilisation(tasks: List[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None,
                       model: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Insert explicit mobilisation tasks ahead of a trade's first production task.

    Mobilisation is organisation time, not a production floor. Putting it in every zone's
    `min_duration_days` is exactly the conflation V6.1.1 removes.
    Mutates `tasks` in place (inserted immediately before the successor so list order
    stays topologically valid) and returns the created tasks.
    """
    cfg = cfg or load_duration_semantics()
    entries = cfg.get("mobilisation") or {}
    if not entries:
        return []
    from core.workfront_model import trade_of

    def _in_trade(task: Dict[str, Any], key: str) -> bool:
        activity = task.get("activity_type")
        if not activity or task.get("inspection_gate"):
            return False
        return activity == key or trade_of(activity, model) == key

    inserts: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    next_id = max(int(t.get("id", 0)) for t in tasks) + 1
    created: List[Dict[str, Any]] = []

    for key, spec in entries.items():
        duration = int(spec.get("duration_days", 0) or 0)
        if duration <= 0:
            continue
        scope = str(spec.get("scope", "project"))
        targets = [t for t in tasks if _in_trade(t, key)]
        if not targets:
            continue
        if scope == "zone":
            groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
            for t in targets:
                groups[t.get("zone")].append(t)
            for zone_id, group in groups.items():
                first = min(group, key=lambda t: int(t["id"]))
                label = str(group[0].get("zone_label") or zone_id or "")
                task = _mobilisation_task(next_id, f"{label} {key} 进场准备（分区级动员）", duration,
                                          scope, zone_id, first.get("floor"), key,
                                          spec.get("basis", ""), spec.get("confirmed_by", ""))
                next_id += 1
                created.append(task)
                inserts[int(first["id"])].append(task)
        elif scope == "floor":
            groups = defaultdict(list)
            for t in targets:
                groups[t.get("floor")].append(t)
            for floor_id, group in groups.items():
                first = min(group, key=lambda t: int(t["id"]))
                task = _mobilisation_task(next_id, f"{floor_id} {key} 进场准备（楼层级动员）", duration,
                                          scope, None, floor_id, key,
                                          spec.get("basis", ""), spec.get("confirmed_by", ""))
                next_id += 1
                created.append(task)
                inserts[int(first["id"])].append(task)
        else:
            first = min(targets, key=lambda t: int(t["id"]))
            task = _mobilisation_task(next_id, f"{key} 进场准备（项目级动员）", duration,
                                      scope, None, first.get("floor"), key,
                                      spec.get("basis", ""), spec.get("confirmed_by", ""))
            next_id += 1
            created.append(task)
            inserts[int(first["id"])].append(task)

    if not created:
        return []

    out: List[Dict[str, Any]] = []
    for t in tasks:
        for mob in inserts.get(int(t["id"]), []):
            out.append(mob)
            tokens = [x for x in str(t.get("predecessors", "") or "").split(",") if x.strip()]
            tokens.insert(0, f"{mob['id']}FS")
            t["predecessors"] = ",".join(dict.fromkeys(tokens))
        out.append(t)
    tasks[:] = out
    return created


# ---------------------------------------------------------------------------
# target_days
# ---------------------------------------------------------------------------
def explain_target_days(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    cfg = cfg or load_duration_semantics()
    sem = cfg["target_days_semantics"]
    lines = [f"target_days 语义（已锁定）：{sem['definition']} —— {sem.get('definition_zh', '')}",
             f"只用于：{', '.join(sem.get('used_by', []))}"]
    for item in sem.get("is_not", []):
        lines.append(f"  ✗ 不是：{item}")
    lines.append(f"最终工期公式：{sem.get('final_duration_formula', '')}")
    return lines


def check_target_days_not_a_cap(rates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Prove by construction that target_days only sizes crews and never caps duration.

    For every activity with a crew_scaling policy: compute the duration at the recommended
    crew for (a) a tiny quantity and (b) a huge quantity. A cap would produce the same
    duration in both cases and pin it at target_days; the formula does not.
    """
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()
    from core.productivity import compute_activity_duration

    rows = []
    for name, entry in (rates.get("activity_types") or {}).items():
        policy = entry.get("crew_scaling")
        if not policy:
            continue
        target = float(policy.get("target_days", 0) or 0)
        typical = float(entry["rates_per_worker_day"]["typical"])
        small_qty = typical * 1.0                       # one worker-day of work
        big_qty = typical * float(policy.get("max_workers_per_front", 1)) * target * 20
        small = compute_activity_duration(name, small_qty, crew_size=1, rates=rates,
                                          quantity_source="measured")
        big = compute_activity_duration(name, big_qty, crew_size=int(policy.get("max_workers_per_front", 1)),
                                        rates=rates, quantity_source="measured")
        rows.append(dict(activity_type=name, target_days=target,
                         small_quantity=round(small_qty, 3), small_duration_days=small["final_duration"],
                         large_quantity=round(big_qty, 3), large_duration_days=big["final_duration"],
                         capped_at_target=(small["final_duration"] == big["final_duration"] == int(target))))
    offenders = [r for r in rows if r["capped_at_target"]]
    return dict(checked=len(rows), offenders=offenders,
                assertion="target_days is a crew sizing horizon, never a duration cap",
                assertion_pass=not offenders, rows=rows)


# ---------------------------------------------------------------------------
# explainability
# ---------------------------------------------------------------------------
def explain_min_scope(task: Dict[str, Any]) -> str:
    d = task.get("duration_semantics") or {}
    if not d:
        return f"#{task.get('id')} {task.get('name')}: 无工期语义诊断（未走生产率公式路径）"
    return (f"#{task.get('id')} {task.get('name')}｜{task.get('activity_type')}"
            f"{' [' + str(task.get('zone_label') or task.get('zone')) + ']' if task.get('zone') else ''}："
            f"原始 {d.get('raw_calculated_duration')}d → 取整 {d.get('rounded_duration')}d"
            f"（取整 +{d.get('rounding_penalty_days')}d）→ 施加下限 {d.get('applied_min_duration')}d"
            f"（{d.get('min_duration_scope')}）→ 最终 {task.get('duration_days')}d"
            f"（下限 +{d.get('minimum_duration_penalty_days')}d）")
