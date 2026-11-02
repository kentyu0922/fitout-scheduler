"""V6.3 scenario optimizer — enumerate construction strategies, and refuse to recommend.

The question this module answers
--------------------------------
Given a fixed set of construction-strategy levers (zone count, crew pool, shifts),
which combinations are mathematically feasible, what do they cost in manpower, and
what unconfirmed inputs does each one bet on?

The question it refuses to answer
---------------------------------
"Which scenario should we adopt?" — not while the resource inputs behind every scenario
are assumptions. `scenario_recommendation()` is designed to return `recommended=None`
and to treat that as a correct terminal answer, not as missing data.

Hard rules (GOV-10 .. GOV-14, on top of V6.2.1 GOV-01..09)
----------------------------------------------------------
10. A scenario is an OPTION. RECOMMENDED only exists when
    `resource_governance.resource_readiness().overall == "confirmed"`.
11. A scenario may not reach its result by inflating workfront capacity, rewriting the
    congestion curve, raising a shift multiplier or touching a productivity rate. It may
    only *select* values already declared in config.
12. The scenario space is explicitly enumerated in config/scenario_space.json. No
    heuristic, search or gradient generation of "optimal" combinations.
13. Scenarios with different evidence states may be tabulated side by side but never
    ranked against each other.
14. "No recommendation" is a valid and preferred output. Shortest duration is never
    silently promoted into a recommendation.

Nothing here changes a productivity rate, a congestion value, a workfront capacity or a
dependency. This module runs scenarios, classifies them and reports what each one bets on.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIO_SPACE_PATH = os.path.join(BASE_DIR, "config", "scenario_space.json")

from core import resource_governance as RG  # noqa: E402
from core import resource_model as RM       # noqa: E402
from core import resource_sensitivity as RS # noqa: E402
import logging

# scenario classification labels
OPTION = "OPTION"
RECOMMENDED = "recommended"
UPPER_BOUND = "UPPER_BOUND"
BASELINE_ROLE = "BASELINE"

PARETO_LABEL = "PARETO_OPTION_SET"

LEVER_KEYS = ("zone_count", "crews_per_trade", "shifts")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def load_scenario_space(path: Optional[str] = None,
                        cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with open(path or SCENARIO_SPACE_PATH, encoding="utf-8") as f:
        space = json.load(f)
    validate_scenario_space(space, cfg)
    return space


def validate_scenario_space(space: Dict[str, Any],
                            cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """GOV-11 / GOV-12: levers are declared, finite, and select — never invent."""
    if not isinstance(space, dict):
        raise ValueError("scenario_space must be a JSON object")
    declared = (space.get("levers") or {})
    scenarios = space.get("scenarios")
    if not scenarios:
        raise ValueError("scenario_space.scenarios is mandatory and must be non-empty (GOV-12)")

    ids = [s.get("id") for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError(f"scenario ids must be unique: {ids}")

    shifts_allowed = set()
    cfg = cfg or RM.load_resource_constraints()
    for k in ((cfg.get("shift_model") or {}).get("shifts") or {}):
        try:
            shifts_allowed.add(int(k))
        except (TypeError, ValueError) as exc:

            logging.getLogger(__name__).warning("swallowed %s: %s", type(exc).__name__, exc)


    checked = []
    for scn in scenarios:
        sid = scn.get("id")
        if not sid:
            raise ValueError("every scenario needs an id")
        if not scn.get("label"):
            raise ValueError(f"{sid}: 每个情景必须有 label")
        if not scn.get("intent"):
            raise ValueError(f"{sid}: 每个情景必须有 intent（说明为什么要算它）")
        levers = scn.get("levers") or {}
        for key in levers:
            if key not in LEVER_KEYS:
                raise ValueError(f"{sid}: 未声明的杠杆 '{key}'（只允许 {list(LEVER_KEYS)}）— GOV-11")
            if key not in declared:
                raise ValueError(f"{sid}: 杠杆 '{key}' 未在 levers 中声明 — GOV-11")
        # GOV-11 — a scenario selects, it never edits a physical constraint
        for banned in ("workfront_capacity", "congestion_model", "productivity_rates",
                       "max_workers_per_workfront", "crew_efficiency_table", "output_multiplier"):
            if banned in levers:
                raise ValueError(f"{sid}: 情景不得改写 '{banned}'（GOV-11）")
        sh = levers.get("shifts")
        if sh is not None:
            if shifts_allowed and int(sh) not in shifts_allowed:
                raise ValueError(f"{sid}: shifts={sh} 未在 shift_model 中声明 — GOV-11")
        cr = levers.get("crews_per_trade")
        if cr is not None and not isinstance(cr, dict):
            try:
                if int(cr) < 1:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"{sid}: crews_per_trade 必须为正整数或按工种映射")
        zc = levers.get("zone_count")
        if zc is not None:
            try:
                if int(zc) < 1:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"{sid}: zone_count 必须为正整数或 null")
        checked.append(dict(id=sid, role=scn.get("role", "option"), levers=sorted(levers)))

    return dict(rules=[r["id"] for r in (space.get("governance") or {}).get("rules", [])],
                scenario_count=len(scenarios), checked=checked,
                assertion="scenario space is explicitly enumerated; no lever edits a physical constraint",
                assertion_pass=True)


def scenarios(space: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    space = space or load_scenario_space()
    return list(space["scenarios"])


def scenario_by_id(sid: str, space: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    for s in scenarios(space):
        if s.get("id") == sid:
            return s
    raise KeyError(f"unknown scenario id: {sid}")


# ---------------------------------------------------------------------------
# lever resolution — every lever carries its evidence
# ---------------------------------------------------------------------------
def resolve_levers(scn: Dict[str, Any], trades: Sequence[str],
                   cfg: Optional[Dict[str, Any]] = None,
                   space: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve a scenario's levers into concrete values + the evidence behind each."""
    cfg = cfg or RM.load_resource_constraints()
    space = space or load_scenario_space()
    levers = scn.get("levers") or {}
    out: Dict[str, Any] = {}

    # --- zone count -------------------------------------------------------
    zc = levers.get("zone_count", None)
    zspec = (space.get("levers") or {}).get("zone_count") or {}
    out["zone_count"] = dict(value=(int(zc) if zc else None),
                             mode=("manual" if zc else "auto"),
                             label=zspec.get("label"),
                             evidence_status=zspec.get("evidence_status"),
                             unconfirmed_because=zspec.get("unconfirmed_because"))

    # --- crews per trade --------------------------------------------------
    cr = levers.get("crews_per_trade", 1)
    if isinstance(cr, dict):
        crews = {t: int(cr.get(t, 1)) for t in trades}
    else:
        crews = {t: int(cr) for t in trades}
    pool_spec, pool_source = RM._spec_for(None, cfg.get("crew_pool") or {}, cfg)
    pool_conf = RG.source_confidence(pool_spec.get("source_type"), pool_spec.get("status"), cfg)
    cspec = (space.get("levers") or {}).get("crews_per_trade") or {}
    out["crews_per_trade"] = dict(value=crews, uniform=not isinstance(cr, dict),
                                  label=cspec.get("label"),
                                  source=pool_source,
                                  status=pool_spec.get("status"),
                                  source_type=pool_spec.get("source_type"),
                                  basis=pool_spec.get("basis"),
                                  confirmed_by=pool_spec.get("confirmed_by"),
                                  confidence=pool_conf["confidence"],
                                  is_contractor_commitment=pool_conf["is_contractor_commitment"],
                                  unconfirmed_because=cspec.get("unconfirmed_because"))

    # --- shifts -----------------------------------------------------------
    sh = int(levers.get("shifts", 1) or 1)
    sf = RM.shift_factor(sh, cfg=cfg)
    spec = ((cfg.get("shift_model") or {}).get("shifts") or {}).get(str(sh)) or {}
    if spec.get("status") == "baseline":
        conf = dict(confidence=1.0, confidence_label="by_definition",
                    is_contractor_commitment=False)
    else:
        conf = RG.source_confidence(spec.get("source_type"), spec.get("status"), cfg)
    sspec = (space.get("levers") or {}).get("shifts") or {}
    out["shifts"] = dict(value=sh,
                         applied_multiplier=sf.get("output_multiplier"),
                         status=spec.get("status"),
                         source_type=spec.get("source_type"),
                         confirmed_by=spec.get("confirmed_by"),
                         contractor_status=spec.get("contractor_status"),
                         confidence=conf["confidence"],
                         is_contractor_commitment=conf["is_contractor_commitment"],
                         label=sspec.get("label"),
                         unconfirmed_because=sspec.get("unconfirmed_because"))
    return out


# ---------------------------------------------------------------------------
# run one scenario
# ---------------------------------------------------------------------------
def run_scenario(scn: Dict[str, Any],
                 build_tasks: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
                 start_date: str, trades: Sequence[str],
                 cfg: Optional[Dict[str, Any]] = None,
                 rates: Optional[Dict[str, Any]] = None,
                 model: Optional[Dict[str, Any]] = None,
                 space: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one scenario end-to-end on a FRESH task list. Never mutates a baseline."""
    cfg = cfg or RM.load_resource_constraints()
    space = space or load_scenario_space()
    if rates is None:
        from core.productivity import load_productivity_rates
        rates = load_productivity_rates()

    levers = resolve_levers(scn, trades, cfg, space)
    shift_mult = levers["shifts"]["applied_multiplier"]
    if shift_mult is None:
        return dict(id=scn["id"], label=scn["label"], intent=scn.get("intent"),
                    role=scn.get("role", "option"), levers=levers,
                    status=RM.INSUFFICIENT_DATA,
                    blocking_reason=f"shifts={levers['shifts']['value']} 的产出系数无证据 → INSUFFICIENT_DATA",
                    classification=OPTION,
                    unconfirmed_dependency_count=None)

    tasks = list(build_tasks(levers))
    crews = levers["crews_per_trade"]["value"]
    run = RS.run_tasks(tasks, crews, start_date, cfg, rates, model)

    plan = run["plan"]
    zones = sorted({t.get("zone") for t in run["tasks"] if t.get("zone")})
    row = dict(
        id=scn["id"], label=scn["label"], intent=scn.get("intent"),
        role=scn.get("role", "option"),
        levers=levers,
        zone_count=len(zones) or levers["zone_count"]["value"],
        crews_per_trade=(max(crews.values()) if crews else 1),
        shifts=levers["shifts"]["value"],
        shift_multiplier_applied=shift_mult,
        finish=run["feasible_finish"],
        duration_days=run["feasible_duration_days"],
        raw_finish=run["raw_finish"],
        raw_duration_days=run["raw_duration_days"],
        levelling_edges=run["levelling_edges"],
        blocking_conflicts_before=run["blocking_before"],
        blocking_conflicts_after=run["blocking_after"],
        capacity_violations=run["capacity_violations"],
        peak_workers=run["peak_workers"],
        peak_date=run["peak_date"],
        total_worker_days=run["total_worker_days"],
        workfront_utilisation_max=run["workfront_utilisation_max"],
        task_count=run["task_count"],
    )
    row["status"] = _classify(run)
    precond = scenario_preconditions(scn, cfg, space)
    row["preconditions"] = precond
    row["unconfirmed_dependency_count"] = len([p for p in precond
                                               if p["status"] != RG.STATUS_CONFIRMED])
    row["evidence_state"] = _evidence_state(precond)
    row["classification"] = _classification_label(row, cfg)
    row["_run"] = run
    return row


def _classify(run: Dict[str, Any]) -> str:
    if run["blocking_after"]:
        return RM.WORKFRONT_CONSTRAINED
    if run["capacity_violations"]:
        return RM.RESOURCE_CONSTRAINED
    return RM.FEASIBLE


# ---------------------------------------------------------------------------
# what does this scenario bet on?
# ---------------------------------------------------------------------------
def scenario_preconditions(scn: Dict[str, Any],
                           cfg: Optional[Dict[str, Any]] = None,
                           space: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every unconfirmed input this scenario depends on, with its provenance."""
    cfg = cfg or RM.load_resource_constraints()
    space = space or load_scenario_space()
    levers = scn.get("levers") or {}
    out: List[Dict[str, Any]] = []

    def _add(lever, path, status, source_type, confirmed_by, basis, confidence, note, weight):
        out.append(dict(lever=lever, path=path, status=status, source_type=source_type,
                        confirmed_by=confirmed_by, basis=basis, confidence=confidence,
                        note=note, weight=weight))

    # crew pool — every scenario depends on it
    pool_spec, pool_source = RM._spec_for(None, cfg.get("crew_pool") or {}, cfg)
    pool_conf = RG.source_confidence(pool_spec.get("source_type"), pool_spec.get("status"), cfg)
    _add("crews_per_trade", f"resource_constraints.crew_pool.{pool_source}",
         pool_spec.get("status"), pool_spec.get("source_type"), pool_spec.get("confirmed_by"),
         pool_spec.get("basis"), pool_conf["confidence"],
         "班组池决定转场能力；未确认则『多班组更快』不可验证。", weight="high")

    # shifts — only if the scenario uses more than one shift
    sh = int(levers.get("shifts", 1) or 1)
    if sh > 1:
        spec = ((cfg.get("shift_model") or {}).get("shifts") or {}).get(str(sh)) or {}
        conf = RG.source_confidence(spec.get("source_type"), spec.get("status"), cfg)
        _add("shifts", f"resource_constraints.shift_model.shifts.{sh}",
             spec.get("status"), spec.get("source_type"), spec.get("confirmed_by"),
             spec.get("basis"), conf["confidence"],
             f"{sh} 班产出系数 {spec.get('output_multiplier')} 为施工组织经验构造，未获承包商确认。",
             weight="high")
    else:
        _add("shifts", "resource_constraints.shift_model.shifts.1", "baseline", None, None,
             "单班是生产率口径的定义基准", 1.0, "单班为定义基准，非估计值。", weight="none")

    # workfront capacity — matters most when the scenario splits into more zones
    cap_spec, cap_source = RM._spec_for(None, cfg.get("workfront_capacity") or {}, cfg)
    cap_conf = RG.source_confidence(cap_spec.get("source_type"), cap_spec.get("status"), cfg)
    _add("zone_count", f"resource_constraints.workfront_capacity.{cap_source}",
         cap_spec.get("status"), cap_spec.get("source_type"), cap_spec.get("confirmed_by"),
         cap_spec.get("basis"), cap_conf["confidence"],
         "分区的收益被单区容量封顶；容量未确认则『多分区更快』不可验证。", weight="high")

    # congestion — only binds when more than one crew is assumed
    if int(_uniform_crews(levers) or 1) > 1:
        cong = (cfg.get("congestion_model") or {}).get("default") or {}
        cconf = RG.source_confidence(cong.get("source_type"), cong.get("status"), cfg)
        _add("crews_per_trade", "resource_constraints.congestion_model.default",
             cong.get("status"), cong.get("source_type"), cong.get("confirmed_by"),
             cong.get("basis"), cconf["confidence"],
             "多班组的有效产出依赖拥挤曲线；该曲线 uncalibrated。", weight="high")

    # productivity rates — every scenario inherits them
    _add("(inherited)", "config/productivity_rates.json", "provisional", "inherited_assumption",
         None, "V5.3 证据层：参考率 ≠ 计划率，未做现场标定前一律临时", None,
         "所有情景共享同一套未标定生产率；情景之间的差异是同一组假设下的差异。", weight="high")
    return out


def _uniform_crews(levers: Dict[str, Any]) -> Optional[int]:
    cr = levers.get("crews_per_trade", 1)
    if isinstance(cr, dict):
        return max(cr.values()) if cr else None
    return cr


def _evidence_state(preconditions: Sequence[Dict[str, Any]]) -> str:
    if not preconditions:
        return RG.READINESS_UNCONFIRMED
    confirmed = [p for p in preconditions if p["status"] == RG.STATUS_CONFIRMED]
    if len(confirmed) == len(preconditions):
        return RG.READINESS_CONFIRMED
    return RG.READINESS_UNCONFIRMED if not confirmed else RG.READINESS_PARTIAL


def _classification_label(row: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """GOV-10: nothing is RECOMMENDED unless the resource inputs are confirmed."""
    if row.get("status") == RM.INSUFFICIENT_DATA:
        return OPTION
    if RG.may_label_recommendation(cfg):
        return RECOMMENDED
    if row.get("role") == "upper_bound":
        return UPPER_BOUND
    if row.get("role") == "baseline":
        return BASELINE_ROLE
    return OPTION


# ---------------------------------------------------------------------------
# comparison — tabulate, do not rank
# ---------------------------------------------------------------------------
def compare_scenarios(rows: Sequence[Dict[str, Any]],
                      cfg: Optional[Dict[str, Any]] = None,
                      space: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or RM.load_resource_constraints()
    space = space or load_scenario_space()
    rows = [r for r in rows if r.get("status") != RM.INSUFFICIENT_DATA]
    states = {r["evidence_state"] for r in rows}
    readiness = RG.resource_readiness(cfg)
    comparable = (len(states) == 1) and (readiness["overall"] == RG.READINESS_CONFIRMED)

    fastest = min((r for r in rows if r.get("duration_days") is not None),
                  key=lambda r: r["duration_days"], default=None)
    table = [dict(id=r["id"], label=r["label"], role=r.get("role"),
                  zone_count=r.get("zone_count"), crews_per_trade=r.get("crews_per_trade"),
                  shifts=r.get("shifts"), finish=r.get("finish"),
                  duration_days=r.get("duration_days"),
                  peak_workers=r.get("peak_workers"),
                  total_worker_days=r.get("total_worker_days"),
                  blocking_conflicts_after=r.get("blocking_conflicts_after"),
                  capacity_violations=r.get("capacity_violations"),
                  unconfirmed_dependency_count=r.get("unconfirmed_dependency_count"),
                  evidence_state=r["evidence_state"], status=r.get("status"),
                  classification=r.get("classification"))
             for r in rows]

    return dict(
        table=table,
        evidence_states=sorted(states),
        comparable=comparable,
        ranking=None,
        ranking_rule=(space.get("comparison") or {}).get("rank_rule"),
        shortest_duration_id=(fastest["id"] if fastest else None),
        shortest_duration_days=(fastest["duration_days"] if fastest else None),
        note=("情景已按工期并列列出，**未排序、未推荐**：证据状态不一致或资源输入未确认（GOV-13 / GOV-14）。"
              if not comparable else
              "证据状态一致且资源输入已确认，允许排序。"),
        caveat=("最短工期情景只是表格中的一个读数，不是推荐：把它当推荐等于把未确认的班组池、"
                "班次折算与工作面容量当成事实。"),
        readiness=readiness["overall"],
        scenario_statement=readiness["scenario_statement"],
    )


def lever_effect(rows: Sequence[Dict[str, Any]], lever: str) -> Dict[str, Any]:
    """Isolate ONE lever: pair scenarios that differ only in `lever`.

    This is the honest way to read the table — the total spread between two scenarios
    is meaningless when three levers move at once. Every row here is a like-for-like
    delta on a single lever, with the other two held identical.
    """
    if lever not in LEVER_KEYS:
        raise ValueError(f"unknown lever '{lever}' (allowed {list(LEVER_KEYS)})")
    others = [k for k in LEVER_KEYS if k != lever]
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for r in rows:
        if r.get("status") == RM.INSUFFICIENT_DATA:
            continue
        groups.setdefault(tuple(r.get(o) for o in others), []).append(r)

    out: List[Dict[str, Any]] = []
    for key, group in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda r: (r.get(lever) if r.get(lever) is not None else -1))
        base = ordered[0]
        d0 = base.get("duration_days")
        for r in ordered[1:]:
            d1 = r.get("duration_days")
            out.append(dict(
                lever=lever,
                held={o: v for o, v in zip(others, key)},
                from_value=base.get(lever), from_id=base["id"], from_duration_days=d0,
                to_value=r.get(lever), to_id=r["id"], to_duration_days=d1,
                saved_days=(d0 - d1) if (d0 is not None and d1 is not None) else None,
                saved_pct=(round((d0 - d1) / d0 * 100, 2) if (d0 and d1 is not None) else None),
                peak_workers_from=base.get("peak_workers"), peak_workers_to=r.get("peak_workers"),
                peak_delta=((r.get("peak_workers") or 0) - (base.get("peak_workers") or 0)),
                capacity_violations_from=base.get("capacity_violations"),
                capacity_violations_to=r.get("capacity_violations"),
                status_from=base.get("status"), status_to=r.get("status"),
                evidence_state=r["evidence_state"],
                classification=r.get("classification")))
    return dict(lever=lever, rows=out,
                note=(f"只移动 {lever}，其余杠杆固定的成对比较。"
                      "用来读清单个杠杆的真实效应，不是用来选情景。"),
                assertion="one lever at a time",
                assertion_pass=bool(out))


def dominance_front(rows: Sequence[Dict[str, Any]],
                    cfg: Optional[Dict[str, Any]] = None,
                    space: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Non-dominated set on (duration, peak manpower). A candidate SET, never a pick."""
    space = space or load_scenario_space()
    dims = ((space.get("comparison") or {}).get("dominance") or {}).get("dimensions") or \
        ["duration_days", "peak_workers"]
    cand = [r for r in rows if r.get("status") != RM.INSUFFICIENT_DATA
            and r.get(dims[0]) is not None and r.get(dims[1]) is not None]
    front = []
    for r in cand:
        dominated = any(
            (o[dims[0]] <= r[dims[0]] and o[dims[1]] <= r[dims[1]] and
             (o[dims[0]] < r[dims[0]] or o[dims[1]] < r[dims[1]]))
            for o in cand if o is not r)
        if not dominated:
            front.append(r)
    return dict(
        dimensions=dims,
        ids=[r["id"] for r in front],
        rows=[dict(id=r["id"], duration_days=r[dims[0]], peak_workers=r[dims[1]],
                   evidence_state=r["evidence_state"], classification=r.get("classification"))
              for r in front],
        candidate_count=len(cand),
        label=PARETO_LABEL,
        is_recommendation=False,
        note=("非支配集 = 在（工期，高峰人力）两维上没有被完全压过的情景集合。"
              "它是并列候选集，不是推荐：不说明可获得、不说明承包商接受、不说明成本可接受。"),
        assertion="a Pareto set is a candidate set, never a recommendation",
        assertion_pass=True,
    )


# ---------------------------------------------------------------------------
# recommendation — designed to return None
# ---------------------------------------------------------------------------
def scenario_recommendation(rows: Sequence[Dict[str, Any]],
                            cfg: Optional[Dict[str, Any]] = None,
                            space: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """GOV-10 / GOV-14 / GOV-15: `recommended=None` in every evidence state.

    V6.4 fix — this function used to return ``recommended=<fastest scenario>`` as soon as
    ``resource_readiness`` turned confirmed. That is exactly what GOV-14 forbids: one
    metric (shortest duration) promoted into a decision, with cost, crew availability and
    contract boundaries never evaluated. The decision now lives in
    ``core.recommendation_gate`` and `recommended` stays null unconditionally.
    """
    from core import recommendation_gate as GATE

    cfg = cfg or RM.load_resource_constraints()
    space = space or load_scenario_space()
    readiness = RG.resource_readiness(cfg)
    usable = [r for r in rows if r.get("status") != RM.INSUFFICIENT_DATA]
    fastest = min((r for r in usable if r.get("duration_days") is not None),
                  key=lambda r: r["duration_days"], default=None)

    decision = GATE.evaluate(
        [r for r in usable if r.get("duration_days") is not None],
        resource_cfg=cfg,
        required_confirmations=promotion_requirements(space, cfg)["minimum_confirmations"],
        context="scenario",
        candidate_note=("情景为并列候选：引擎不按工期、高峰人力或任何单一指标排序（GOV-13 / GOV-14）。"),
    )
    GATE.assert_no_recommendation(decision)

    out = dict(decision)
    out.update(
        reason=" ".join(b["message"] for b in decision["blockers"]),
        scenario_count=len(rows),
        usable_scenario_count=len(usable),
        shortest_duration_id=(fastest["id"] if fastest else None),
        shortest_duration_days=(fastest["duration_days"] if fastest else None),
        shortest_duration_note=("最短工期情景仅作为读数列出；GOV-14 / GOV-15 禁止以单一指标充当推荐。"
                                if fastest else None),
        statement=readiness["scenario_statement"],
        unconfirmed_sections=readiness["unconfirmed_sections"],
    )
    return out


def promotion_requirements(space: Optional[Dict[str, Any]] = None,
                           cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    space = space or load_scenario_space()
    cfg = cfg or RM.load_resource_constraints()
    promo = space.get("promotion") or {}
    readiness = RG.resource_readiness(cfg)
    items = list(promo.get("minimum_confirmations") or [])
    return dict(
        requires_readiness=promo.get("requires_readiness", "confirmed"),
        current_readiness=readiness["overall"],
        satisfied=(readiness["overall"] == promo.get("requires_readiness", "confirmed")),
        minimum_confirmations=items,
        engine_role=promo.get("engine_role"),
        promotion_note=promo.get("promotion_note"),
        note="引擎只解锁标签，不代替人做决策。",
    )


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------
def explain_scenarios(rows: Sequence[Dict[str, Any]],
                      comparison: Dict[str, Any],
                      recommendation: Dict[str, Any],
                      front: Dict[str, Any]) -> List[str]:
    L = []
    L.append(f"情景数 {len(rows)}　证据状态 {sorted({r['evidence_state'] for r in rows})}　"
             f"资源就绪度 {comparison['readiness']}")
    for r in rows:
        if r.get("status") == RM.INSUFFICIENT_DATA:
            L.append(f"{r['id']} | {r['label']} | INSUFFICIENT_DATA — {r.get('blocking_reason')}")
            continue
        L.append(f"{r['id']} | {r['label']} | 分区 {r['zone_count']} | "
                 f"{r['crews_per_trade']} 班组 | {r['shifts']} 班 | "
                 f"完成 {r['finish']} | {r['duration_days']} 天 | "
                 f"高峰 {r['peak_workers']} 人 | 工日 {r['total_worker_days']} | "
                 f"阻断 {r['blocking_conflicts_before']}→{r['blocking_conflicts_after']} | "
                 f"超限 {r['capacity_violations']} | {r['status']} | {r['classification']}")
    L.append(f"非支配集（{front['label']}）：{', '.join(front['ids']) or '—'} — 并列候选，非推荐")
    if recommendation["recommended"] is None:
        L.append("推荐：无（recommended=null）— 这是当前证据下的正确结论，不是缺数据")
        L.append(f"原因：{recommendation['reason']}")
        if recommendation.get("shortest_duration_id"):
            L.append(f"读数：最短工期情景 {recommendation['shortest_duration_id']} "
                     f"= {recommendation['shortest_duration_days']} 天（不得当推荐用）")
    else:
        L.append(f"推荐：{recommendation['recommended']}（{recommendation['label']}）")
    return L

