"""V6.1 spatial sequencing engine.

Turns a flat fit-out WBS into a zone-based construction sequence:

    Project → Floor → Zone → Workfront

Design rules (all enforced in code, none optional):
  1. Spatial dependency resolution follows zone → floor → project. A zone-scoped
     predecessor is NEVER replaced by a floor/project-level equivalent.
  2. A floor/project task that depends on a zone-scoped task is linked to EVERY zone
     instance. Otherwise the gate would not wait for zone B and C.
  3. Inspection and authority gates are instantiated per their declared spatial scope.
     Concealed-work hold points are per zone (分区报验); whole-system hold points
     (调试前安全验收, 首次空气盲测) stay at floor/project level.
  4. Inter-zone pipeline for one trade with one crew is FS. A crew cannot occupy two
     zones at once; this is physics, not a compression choice.
  5. Productivity rates, the evidence layer and the dependency ontology are read-only
     inputs here. Nothing in this module writes to them.
  6. V6.1.1 duration semantics: a minimum duration is applied per its declared scope
     (PROJECT / FLOOR / ZONE / MOBILISATION / INSPECTION_CYCLE). Unclassified entries
     stay LEGACY_UNSPECIFIED and keep the historical behaviour. A project-level minimum
     is NEVER multiplied by the zone count.

Everything this module produces carries the formula or rule that produced it.
"""
from __future__ import annotations

import copy
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core._common import parse_predecessor, split_predecessor_id_suffix
from core import dependency_ontology as ONTO
from core.dependency_engine import evaluate_sectional
from core.zone_model import (build_spatial_project, gate_scope, load_spatial_model,
                             spatial_scope, spatial_scope_reason)
from core.workfront_model import (build_workfronts, resolve_inter_zone_pipeline,
                                  trade_contention, trade_of)

logger = logging.getLogger(__name__)

QTY_ZONE = "zone_allocated"


# ---------------------------------------------------------------------------
# materialisation
# ---------------------------------------------------------------------------
def _task_scope(task: Dict[str, Any], model: Dict[str, Any]) -> str:
    """Gate tasks resolve by name keyword; production tasks by activity_type."""
    if task.get("inspection_gate"):
        return gate_scope(task.get("name", ""), model)["scope"]
    if not task.get("activity_type"):
        return "project"
    return spatial_scope(task.get("activity_type"), model)


def _materialise(tasks: Sequence[Dict[str, Any]], zones, model: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[Any, Dict[Any, int]]]:
    """Emit one instance per spatial scope, in template-major order.

    Template-major ordering guarantees no forward references: if template task A
    precedes B, every instance of A precedes every instance of B, so remapped
    predecessors always point backwards.
    """
    out: List[Dict[str, Any]] = []
    id_map: Dict[Any, Dict[Any, int]] = defaultdict(dict)  # template_id -> {scope_key: new_id}
    scope_of: Dict[Any, str] = {}
    next_id = 1

    # V6.6.0 — floor list in declaration order (floor-major zone ordering).
    floor_ids: List[str] = []
    for z in zones:
        if z.floor_id not in floor_ids:
            floor_ids.append(z.floor_id)
    multi_floor = len(floor_ids) > 1
    default_floor = floor_ids[0] if floor_ids else "F1"

    for src in tasks:
        scope = _task_scope(src, model)
        scope_of[src["id"]] = scope
        # A floor-scoped activity (demolition / waste / staged cleaning) happens ONCE PER
        # FLOOR in a multi-floor building; on a single floor it stays a single instance.
        if scope == "project":
            keys: List[Any] = [None]
        elif scope == "floor":
            keys = [("@" + fid) for fid in floor_ids] if multi_floor else [None]
        else:
            keys = [z.zone_id for z in zones]
        for key in keys:
            inst = copy.deepcopy(src)
            inst["id"] = next_id
            inst["spatial_scope"] = scope
            inst["template_id"] = src["id"]
            if scope == "floor":
                fid = key[1:] if isinstance(key, str) and key.startswith("@") else default_floor
                inst["zone"] = None
                inst["floor"] = fid
                if key is not None and inst.get("name"):
                    inst["name"] = f"{fid} {inst['name']}"
            elif scope == "zone":
                zone = next(z for z in zones if z.zone_id == key)
                inst["zone"] = zone.zone_id
                inst["floor"] = zone.floor_id          # never hardcoded
                inst["zone_label"] = zone.label
                inst["zone_area_sqm"] = zone.area_sqm
                if inst.get("name"):
                    inst["name"] = f"{zone.label} {inst['name']}"
            else:
                inst["zone"] = None
                inst["floor"] = default_floor
            inst["predecessors_template"] = inst.get("predecessors", "")
            inst["predecessors"] = ""
            inst.pop("inspection_gate_ids", None)
            id_map[src["id"]][key] = next_id
            next_id += 1
            out.append(inst)
    return out, {k: dict(v) for k, v in id_map.items()}


def _remap_links(tasks: List[Dict[str, Any]], id_map: Dict[Any, Dict[Any, int]],
                 scope_of: Dict[Any, str]) -> List[Dict[str, Any]]:
    """Re-point every predecessor / gate id at the right spatial instance.

    zone-scoped target  ← zone-scoped source : same zone instance
    non-zone target     ← zone-scoped source : ALL zone instances
    any target          ← non-zone source    : the single instance
    """
    trace: List[Dict[str, Any]] = []

    def _resolve(mapping: Dict[Any, int], p_scope: str, zone_key, floor_key,
                 task_floor: Any) -> Tuple[List[Any], str]:
        """Which instance(s) of the predecessor does this successor wait for?"""
        if p_scope == "zone":
            if zone_key is not None:
                return ([mapping[zone_key]] if zone_key in mapping else []), "zone→zone 同分区实例"
            if floor_key is not None:
                tid = [v for k, v in mapping.items()
                       if str(k).startswith(str(task_floor) + "-")]
                if tid:
                    return tid, "zone→floor：只等本楼层的分区实例"
                return sorted(mapping.values()), "zone→floor：无本层实例，退化为全部分区实例"
            return sorted(mapping.values()), "zone→上层：链接到全部分区实例，避免只等 A 区就放行"
        if p_scope == "floor":
            if floor_key is not None and floor_key in mapping:
                return [mapping[floor_key]], "floor→floor：同层实例"
            if zone_key is not None:
                fk = "@" + str(task_floor)
                if fk in mapping:
                    return [mapping[fk]], "floor→zone：链接到本楼层的楼层级实例"
                if None in mapping:
                    return [mapping[None]], "floor→zone：单层退化实例"
                return sorted(mapping.values()), "floor→zone：无本层实例，退化为全部楼层"
            return sorted(mapping.values()), "floor→上层：链接到全部楼层实例"
        return [mapping.get(None)], "上层→任意：链接到唯一实例"

    for task in tasks:
        key = task.get("zone")
        floor_key = ("@" + str(task.get("floor"))) if task.get("spatial_scope") == "floor" else None
        tokens: List[str] = []
        for pid, suffix in split_predecessor_id_suffix(str(task.get("predecessors_template", "") or "")):
            mapping = id_map.get(pid)
            if not mapping:
                continue
            targets, rule = _resolve(mapping, scope_of.get(pid), key, floor_key, task.get("floor"))
            for tid in targets:
                if tid is None or tid == task["id"]:
                    continue
                tokens.append(f"{tid}{suffix}")
                trace.append(dict(successor=task["id"], predecessor=tid, template_predecessor=pid, rule=rule))
        task["predecessors"] = ",".join(dict.fromkeys(tokens))
        # inspection_gate_ids remap mirrors the same spatial rule
        raw_gates = task.get("inspection_gate_ids_template")
        if raw_gates:
            gates: List[int] = []
            for gid in raw_gates:
                mapping = id_map.get(gid)
                if not mapping:
                    continue
                targets, _rule = _resolve(mapping, scope_of.get(gid), key, floor_key, task.get("floor"))
                if scope_of.get(gid) == "zone" and key is not None and key in mapping:
                    gates.append(mapping[key])
                else:
                    gates.extend(t for t in targets if t is not None)
            if gates:
                task["inspection_gate_ids"] = sorted(set(gates))
    return trace


def _assign_zone_quantities(tasks: List[Dict[str, Any]], zones, allocation: Dict[str, Dict[str, Any]],
                            rates: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Give every zone-scoped production task its zone quantity (never the project total)."""
    trace: List[Dict[str, Any]] = []
    lib = rates["activity_types"]
    for task in tasks:
        activity = task.get("activity_type")
        if not activity or task.get("inspection_gate") or task.get("spatial_scope") != "zone":
            continue
        key = lib.get(activity, {}).get("quantity_key")
        if not key:
            continue
        row = (allocation.get(task["zone"]) or {}).get(key)
        if not row or row.get("value") is None:
            # P0-03: an UNKNOWN quantity is never assigned as a zone value.
            continue
        task["quantity"] = row["value"]
        task["quantity_key"] = key
        task["quantity_source"] = QTY_ZONE
        task["quantity_formula"] = row["formula"]
        task["quantity_share_of_area"] = row["share_of_area"]
        task["unit"] = row.get("unit", lib.get(activity, {}).get("unit", ""))
        trace.append(dict(task_id=task["id"], activity_type=activity, zone=task["zone"],
                          quantity_key=key, value=row["value"], unit=row.get("unit", ""),
                          formula=row["formula"]))
    return trace


def _pin_project_crew(tasks: List[Dict[str, Any]], project_quantities: Dict[str, Dict[str, Any]],
                      area_sqm: float, rates: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pin the single trade crew that walks zone to zone.

    The crew is sized for the WHOLE project quantity, then applied to each zone's
    smaller quantity — so Σ zone durations equals the project duration for that trade
    and no phantom crew capacity is invented.
    """
    from core.productivity import recommend_crew

    trace: List[Dict[str, Any]] = []
    by_activity: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        if task.get("spatial_scope") == "zone" and task.get("activity_type") and not task.get("inspection_gate"):
            by_activity[task["activity_type"]].append(task)
    for activity, group in by_activity.items():
        key = rates["activity_types"].get(activity, {}).get("quantity_key")
        total_qty = float((project_quantities.get(key) or {}).get("value", 0) or 0) if key else 0.0
        crew = recommend_crew(activity, total_qty, project_area=area_sqm, workfronts=1, rates=rates)
        for task in group:
            task["crew_size"] = int(crew)
            task["crew_model"] = "single_crew_pipeline"
            task["crew_allocation_note"] = (
                f"全项目 {activity} 班组 {crew} 人整体转场；每区工期 = 分区工程量 ÷ (生产率 × {crew})。"
                f"Σ 分区工期 ≈ 单工作面总工期，未凭空增加班组产能。")
        trace.append(dict(activity_type=activity, crew_size=int(crew), zones=len(group),
                          total_quantity=total_qty, quantity_key=key))
    return trace


def _inter_zone_pipeline(tasks: List[Dict[str, Any]], model: Dict[str, Any],
                         crews_per_trade: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """Link zone N → zone N+1 for the same activity (the trade's crew moving on)."""
    rows: List[Dict[str, Any]] = []
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        if task.get("spatial_scope") == "zone" and task.get("activity_type") and not task.get("inspection_gate"):
            groups[task["activity_type"]].append(task)
    for activity, group in groups.items():
        group = sorted(group, key=lambda t: (str(t.get("zone")), t["id"]))
        trade = trade_of(activity, model)
        crews = (crews_per_trade or {}).get(trade)
        for pred, succ in zip(group, group[1:]):
            resolved = resolve_inter_zone_pipeline(
                activity,
                int(pred.get("duration_days", pred.get("duration", 0)) or 0),
                crews_for_trade=crews, model=model)
            if not resolved.get("emits_edge") or not resolved.get("relationship"):
                continue
            tokens = [x for x in str(succ.get("predecessors", "") or "").split(",") if x.strip()]
            tokens.append(f"{pred['id']}{resolved['relationship']}+{resolved['lag']}")
            succ["predecessors"] = ",".join(dict.fromkeys(tokens))
            rows.append(dict(predecessor=pred["id"], successor=succ["id"],
                             predecessor_zone=pred.get("zone"), successor_zone=succ.get("zone"),
                             activity_type=activity, trade=trade,
                             relationship=resolved["relationship"], lag=resolved["lag"],
                             dependency_class="resource", condition="resource.same_trade_crew",
                             condition_scope="zone", reason=resolved["reason"],
                             explanation=resolved["explanation"],
                             confidence=resolved["confidence"], policy=resolved["policy"],
                             crews_for_trade=resolved["crews_for_trade"],
                             source="spatial.inter_zone_pipeline"))
    return rows


# ---------------------------------------------------------------------------
# compliance
# ---------------------------------------------------------------------------
def validate_spatial_compliance(tasks: Sequence[Dict[str, Any]], zones, model: Dict[str, Any],
                                onto: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Zone overlap must never delete, downgrade or skip a mandatory hold point."""
    onto = onto or ONTO.load_ontology()
    issues: List[Dict[str, Any]] = list(ONTO.validate_gate_protection(tasks, onto))
    by_id = {t["id"]: t for t in tasks}

    release_cfg = (model.get("release_conditions") or {}).get("by_activity") or {}
    for zone in zones:
        for activity, spec in release_cfg.items():
            insts = [t for t in tasks if t.get("activity_type") == activity and t.get("zone") == zone.zone_id]
            if not insts:
                continue
            for inst in insts:
                gates = [by_id.get(g) for g in (inst.get("inspection_gate_ids") or [])]
                gates = [g for g in gates if g and g.get("inspection_gate")]
                if not gates:
                    issues.append(dict(level="error", code="GATE_MISSING_PER_ZONE", task_id=inst["id"],
                                       message=f"{zone.label} {activity} 缺少分区级检查门实例；"
                                               f"分区重叠不得共用一道门来省验收"))
                    continue
                zone_gates = [g for g in gates if g.get("spatial_scope") == "zone"]
                if zone_gates:
                    # A per-zone hold point must be the zone's OWN instance; two zones
                    # sharing one concealed-work inspection is exactly the shortcut this
                    # validator exists to catch.
                    if not any(g.get("zone") == zone.zone_id for g in zone_gates):
                        issues.append(dict(level="error", code="GATE_MISSING_PER_ZONE", task_id=inst["id"],
                                           message=f"{zone.label} {activity} 的分区级检查门不属于本分区（跨区共用门）"))
                else:
                    # Floor/project-scoped hold points (IAQ blind test, pre-commissioning)
                    # are legitimately shared: every zone waits for the same one.
                    g = gates[0]
                    issues.append(dict(level="info", code="GATE_SHARED_BY_SCOPE", task_id=inst["id"],
                                       message=f"{zone.label} {activity} 等待 {g.get('spatial_scope')} 级检查门 "
                                               f"#{g['id']}（{g.get('name', '')}）：该门按配置不分区，"
                                               f"所有分区共用同一道，属合规约束导致的真实串行"))
                for g in gates:
                    rels = {rel for pid, rel, _ in parse_predecessor(str(inst.get("predecessors", "") or ""))
                            if pid == g["id"]}
                    if rels and rels != {"FS"}:
                        issues.append(dict(level="error", code="GATE_NOT_FS", task_id=inst["id"],
                                           message=f"{zone.label} {activity} ← 门 #{g['id']} 为 {sorted(rels)}，必须为 FS"))
    return issues


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def build_spatial_plan(tasks: Sequence[Dict[str, Any]], area_sqm: float,
                       project_quantities: Dict[str, Dict[str, Any]], rates: Dict[str, Any],
                       zone_mode: str = "auto", manual_zones=None, workfronts: Optional[int] = None,
                       model: Optional[Dict[str, Any]] = None, onto: Optional[Dict[str, Any]] = None,
                       crews_per_trade: Optional[Dict[str, int]] = None,
                       apply_durations=None,
                       semantics: Optional[Dict[str, Any]] = None,
                       floors_count: int = 1,
                       floor_breakdown=None) -> Dict[str, Any]:
    """Build the zone-based task network. Returns a fully auditable plan dict.

    V6.6.0 — ``floors_count`` / ``floor_breakdown`` extend the hierarchy to
    Project → Floor → Zone → Workfront. Zones are ordered floor-major, so the same
    inter-zone FS pipeline that links Zone 1 → Zone 2 also links the last zone of one
    floor to the first zone of the next — the vertical line-of-balance.
    """
    model = model or load_spatial_model()
    onto = onto or ONTO.load_ontology()

    built = build_spatial_project(area_sqm, mode=zone_mode, manual_zones=manual_zones,
                                  model=model, project_quantities=project_quantities,
                                  floors_count=floors_count,
                                  floor_breakdown=floor_breakdown)
    project = built["project"]
    zones = project.zones
    allocation = built["allocation"]

    src = copy.deepcopy(list(tasks))
    for t in src:
        if t.get("inspection_gate_ids"):
            t["inspection_gate_ids_template"] = list(t["inspection_gate_ids"])
    materialised, id_map = _materialise(src, zones, model)
    scope_of = {}
    for t in src:
        scope_of[t["id"]] = _task_scope(t, model)

    remap_trace = _remap_links(materialised, id_map, scope_of)
    qty_trace = _assign_zone_quantities(materialised, zones, allocation, rates)
    crew_trace = _pin_project_crew(materialised, project_quantities, area_sqm, rates)

    for t in materialised:
        t["project_area"] = area_sqm
        t["workfronts"] = 1
        t["workfronts_source"] = "zone"

    if apply_durations is not None:
        apply_durations(materialised)

    # --- V6.1.1 duration semantics -------------------------------------------
    # Re-resolve every minimum against its declared scope BEFORE the network is
    # linked, so a PROJECT-level minimum is never paid once per zone, and real
    # mobilisation time becomes its own task instead of a hidden production floor.
    from core import duration_semantics as DS

    semantics = semantics or DS.load_duration_semantics()
    min_report = DS.apply_scope_aware_minimums(materialised, cfg=semantics, rates=rates)
    mobilisation_tasks = DS.apply_mobilisation(materialised, cfg=semantics, model=model)

    decision = evaluate_sectional(area_sqm, workfronts=workfronts or len(zones))
    decision = dict(decision)
    decision["sectional"] = len(zones) > 1
    decision["zone_count"] = len(zones)

    pipeline_rows = _inter_zone_pipeline(materialised, model, crews_per_trade)

    onto_report = ONTO.apply_ontology_dependencies(materialised, decision, onto=onto, mode="typed")
    from core.dependency_engine import validate_dependency_graph
    graph_issues = validate_dependency_graph(materialised)
    issues = [dict(level="error", code=i.get("code", "GRAPH"), task_id=i.get("task_id"),
                   message=i.get("message", "")) for i in graph_issues]

    compliance = validate_spatial_compliance(materialised, zones, model, onto)

    workfronts_rows = [wf.to_dict() for wf in build_workfronts(materialised, model, crews_per_trade)]

    return dict(
        model_version=model.get("version"),
        project=project.to_dict(),
        zones=[z.to_dict() for z in zones],
        floors=built.get("floors") or [],
        generation=built["generation"],
        allocation_issues=built["allocation_issues"],
        tasks=materialised,
        decision=decision,
        quantity_trace=qty_trace,
        crew_trace=crew_trace,
        duration_semantics=dict(version=semantics.get("version"),
                                min_duration_rows=min_report["rows"],
                                aggregate_checks=min_report["aggregate_checks"],
                                changed_task_ids=min_report["changed_task_ids"]),
        duration_diagnostics=DS.plan_duration_diagnostics(materialised, semantics),
        mobilisation=[t.get("name") for t in mobilisation_tasks],
        remap_trace=remap_trace,
        pipeline=pipeline_rows,
        ontology=onto_report,
        workfronts=workfronts_rows,
        trade_contention=trade_contention(build_workfronts(materialised, model, crews_per_trade)),
        graph_issues=issues,
        compliance_issues=compliance,
        scope_decisions=[dict(template_id=t["id"], name=t.get("name"), activity_type=t.get("activity_type"),
                              spatial_scope=_task_scope(t, model),
                              reason=(gate_scope(t.get("name", ""), model)["reason"] if t.get("inspection_gate")
                                      else spatial_scope_reason(t.get("activity_type"), model)))
                         for t in src if t.get("activity_type") or t.get("inspection_gate")],
    )


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------
def compare_spatial_vs_baseline(baseline_solved: Dict[str, Any], spatial_solved: Dict[str, Any],
                                start_date: str) -> Dict[str, Any]:
    """Compare two solved schedules on finish date, duration, critical path and float."""
    def _summarise(solved, label):
        tasks = solved.get("tasks") or []
        finishes = [str(t.get("finish", "")) for t in tasks if t.get("finish")]
        finish = max(finishes) if finishes else None
        critical = [t for t in tasks if t.get("critical")]
        floats = [float(t.get("total_float", t.get("float", 0)) or 0) for t in tasks
                  if t.get("total_float", t.get("float")) is not None]
        return dict(label=label, task_count=len(tasks), finish_date=finish,
                    critical_task_count=len(critical),
                    critical_path_ids=[int(t["id"]) for t in critical],
                    total_float_days=round(sum(floats), 1),
                    scheduled_duration_days=int(solved.get("project_duration_days")
                                                or _days_between(start_date, finish) or 0))

    base = _summarise(baseline_solved, "baseline_single_workfront")
    spat = _summarise(spatial_solved, "spatial_multi_zone")
    saved = None
    if base["finish_date"] and spat["finish_date"]:
        saved = _days_between(spat["finish_date"], base["finish_date"])
    return dict(baseline=base, spatial=spat, finish_date_saved_days=saved,
                spatial_is_faster=(saved is not None and saved > 0),
                assertion="spatial_finish <= baseline_finish",
                assertion_pass=(saved is not None and saved >= 0),
                critical_path_changed=(set(base["critical_path_ids"]) != set(spat["critical_path_ids"])),
                note=("若 saved <= 0，必须如实报告：常见原因是分区数过多导致每个分区都触发 "
                      "min_duration_days 下限，流水收益被下限吞掉。此时应减少分区数，而不是放宽下限。"))


def _days_between(start: str, end: str) -> Optional[int]:
    from datetime import date

    try:
        a = date.fromisoformat(str(start)[:10])
        b = date.fromisoformat(str(end)[:10])
    except (TypeError, ValueError):
        return None
    return (b - a).days


# ---------------------------------------------------------------------------
# explainability
# ---------------------------------------------------------------------------
def explain_zone_generation(plan: Dict[str, Any]) -> List[str]:
    gen = plan["generation"]
    lines = [f"分区数：{gen['zone_count']}（方法 {gen['method']}）", f"  原因：{gen['reason']}",
             f"  主方法依据：{gen.get('primary_method_reason', '')}"]
    for alt in gen.get("alternatives") or []:
        if alt.get("cross_check_only"):
            lines.append(f"  交叉校核（不参与决策）：{alt['method']} → {alt['zone_count']} 个；{alt['reason']}")
    for w in gen.get("warnings") or []:
        lines.append(f"  警告：{w}")
    return lines


def explain_zone_quantities(plan: Dict[str, Any], limit: int = 6) -> List[str]:
    lines = []
    rows = [r for r in plan["quantity_trace"]]
    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_key[r["quantity_key"]].append(r)
    for key, group in list(by_key.items())[:limit]:
        group = sorted(group, key=lambda r: str(r["zone"]))
        parts = " + ".join(f"{r['zone']}:{r['value']:g}{r['unit']}" for r in group)
        lines.append(f"{key}：{parts}")
        lines.append(f"    分摊公式示例：{group[-1]['formula']}")
    return lines


def explain_pipeline(plan: Dict[str, Any], limit: int = 8) -> List[str]:
    lines = []
    counts = Counter(r["relationship"] for r in plan["pipeline"])
    lines.append("跨区流水关系分布：" + "、".join(f"{k} {v}" for k, v in counts.items()))
    for row in plan["pipeline"][:limit]:
        lines.append(f"  {row['predecessor_zone']} → {row['successor_zone']} · {row['activity_type']} "
                     f"{row['relationship']}+{row['lag']}｜{row['explanation']}")
    return lines


def summarise_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    scope_counts = Counter(s["spatial_scope"] for s in plan["scope_decisions"])
    return dict(
        zone_count=len(plan["zones"]),
        zone_areas=[z["area_sqm"] for z in plan["zones"]],
        task_count=len(plan["tasks"]),
        scope_decisions=dict(scope_counts),
        pipeline_links=len(plan["pipeline"]),
        pipeline_relationships=dict(Counter(r["relationship"] for r in plan["pipeline"])),
        ontology_links=len(plan["ontology"]["applied"]),
        ontology_relationships=dict(Counter(a["relationship"] for a in plan["ontology"]["applied"])),
        graph_issues=len(plan["graph_issues"]),
        compliance_errors=len([i for i in plan["compliance_issues"] if i["level"] == "error"]),
        allocation_issues=len(plan["allocation_issues"]),
        workfronts=len(plan["workfronts"]),
        trade_groups=len(plan["trade_contention"]),
    )
