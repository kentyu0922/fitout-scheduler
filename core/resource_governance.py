"""V6.2.1 resource governance — separate what is NEEDED from what is AVAILABLE from what is COMMITTED.

The question this module answers
--------------------------------
V6.2 answered "do we have enough people and physical room?".
V6.2.1 answers "which of those resource inputs are facts, which are assumptions,
and how sensitive is the plan to them?".

Three things that must never be collapsed into one number:

    required resources        — arithmetic output of quantity / productivity
    authorised availability   — what the planner or contractor says can be on site
    confirmed commitment      — what the contractor has actually committed to

Hard rules
----------
1. A default inherited from an earlier version stays `provisional` with
   `basis=inherited_v6_1_single_crew_pipeline` and `confirmed_by=null`. It must never be
   read as "the contractor has one crew".
2. `status=confirmed` requires a non-empty `basis`, a non-empty `confirmed_by`, and a
   source_type that can carry a commitment (contractor_confirmed / site_observed /
   historical_calibrated). Otherwise the config is rejected — no silent downgrade.
3. `insufficient_data` entries may not carry numeric availability at all.
4. Crew COUNT is not crew SIZE: 3 crews x 8 workers is not 1 crew x 24 workers, because
   several crews can occupy several workfronts while one oversized crew congests itself.
5. Nothing here changes a productivity rate, a congestion value or a workfront capacity.
   This module only classifies, validates and reports.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOURCE_CONSTRAINTS_PATH = os.path.join(BASE_DIR, "config", "resource_constraints.json")

STATUS_CONFIRMED = "confirmed"
STATUS_PROVISIONAL = "provisional"
STATUS_INSUFFICIENT_DATA = "insufficient_data"

STATUS_WEIGHT = {STATUS_CONFIRMED: 1.0, STATUS_PROVISIONAL: 0.5, STATUS_INSUFFICIENT_DATA: 0.0}

# source types that may carry a commitment
COMMITMENT_SOURCES = ("contractor_confirmed", "site_observed", "historical_calibrated")

SOURCE_INHERITED = "inherited_assumption"
SOURCE_PLANNER = "planner_commitment"
SOURCE_PROPOSAL = "contractor_proposal"
SOURCE_CONFIRMED = "contractor_confirmed"
SOURCE_OBSERVED = "site_observed"
SOURCE_CALIBRATED = "historical_calibrated"

INHERITED_BASIS = "inherited_v6_1_single_crew_pipeline"

READINESS_UNCONFIRMED = "unconfirmed"
READINESS_PARTIAL = "partially_confirmed"
READINESS_CONFIRMED = "confirmed"

DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
RECOMMENDED = "recommended"


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def load_resource_constraints(path: Optional[str] = None) -> Dict[str, Any]:
    with open(path or RESOURCE_CONSTRAINTS_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    validate_governance(cfg)
    return cfg


# ---------------------------------------------------------------------------
# source types and confidence
# ---------------------------------------------------------------------------
def source_types(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_resource_constraints()
    reg = ((cfg.get("source_types") or {}).get("registry") or {})
    if not reg:
        raise ValueError("resource_constraints.source_types.registry is mandatory (V6.2.1)")
    return reg


def source_confidence(source_type: Optional[str], status: Optional[str],
                      cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Confidence depends on the SOURCE, not on how convenient the number is."""
    cfg = cfg or load_resource_constraints()
    reg = source_types(cfg)
    spec = reg.get(source_type or "") or {}
    base = float(spec.get("confidence", 0.0))
    weight = STATUS_WEIGHT.get((status or STATUS_PROVISIONAL).lower(), 0.0)
    if (source_type or "") not in reg:
        return dict(source_type=source_type, status=status, base_confidence=None,
                    status_weight=weight, confidence=None,
                    is_contractor_commitment=False, confidence_label="unknown_source",
                    note="未登记的来源类型：信心无法评估，按无证据处理。")
    conf = round(base * weight, 4)
    label = ("high" if conf >= 0.8 else "medium" if conf >= 0.4 else
             "low" if conf > 0 else "none")
    return dict(source_type=source_type, status=status, base_confidence=base,
                status_weight=weight, confidence=conf, confidence_label=label,
                is_contractor_commitment=(source_type == SOURCE_CONFIRMED),
                is_planner_assumption=(source_type in (SOURCE_PLANNER, SOURCE_INHERITED)),
                note=spec.get("never_treated_as") or "")


# ---------------------------------------------------------------------------
# governance validation (GOV-01 .. GOV-07)
# ---------------------------------------------------------------------------
def _specs(section: str, cfg: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """(name, spec) pairs of every declared entry in a resource section."""
    node = cfg.get(section) or {}
    out: List[Tuple[str, Dict[str, Any]]] = []
    if section == "shift_model":
        for k, v in (node.get("shifts") or {}).items():
            out.append((f"shifts.{k}", v or {}))
        if node.get("night_shift"):
            out.append(("night_shift", node["night_shift"]))
        return out
    if node.get("default"):
        out.append(("default", node["default"]))
    for k, v in (node.get("by_trade") or {}).items():
        out.append((f"by_trade.{k}", v or {}))
    return out


def validate_governance(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Reject a resource config that turns an assumption into a false commitment."""
    if not isinstance(cfg, dict):
        raise ValueError("resource_constraints must be a JSON object")
    reg = source_types(cfg)
    findings: List[Dict[str, Any]] = []

    def _check(name: str, spec: Dict[str, Any], section: str) -> None:
        status = (spec.get("status") or STATUS_PROVISIONAL).lower()
        stype = spec.get("source_type")
        basis = spec.get("basis")
        confirmed_by = spec.get("confirmed_by")

        # GOV-09 — an explicit per-trade override must justify itself
        if name.startswith("by_trade.") and not basis:
            raise ValueError(f"{section}.{name}: GOV-09 — 按工种覆盖必须具备非空 basis")

        if stype is not None and stype not in reg:
            raise ValueError(f"{section}.{name}: unknown source_type '{stype}' "
                             f"(allowed: {sorted(reg)})")
        # GOV-01
        if status == STATUS_CONFIRMED:
            if not basis:
                raise ValueError(f"{section}.{name}: GOV-01 — status=confirmed 必须具备非空 basis")
            if not confirmed_by:
                raise ValueError(f"{section}.{name}: GOV-01 — status=confirmed 必须具备非空 confirmed_by")
            if stype not in COMMITMENT_SOURCES:
                raise ValueError(f"{section}.{name}: GOV-01 — status=confirmed 的 source_type 必须属于 "
                                 f"{list(COMMITMENT_SOURCES)}，当前为 {stype!r}")
        # GOV-08
        if status != "baseline" and stype is None:
            raise ValueError(f"{section}.{name}: GOV-08 — 非 baseline 条目必须声明 source_type "
                             f"（当前 status={status}）")
        # GOV-02
        if status == STATUS_PROVISIONAL and stype == SOURCE_CONFIRMED:
            raise ValueError(f"{section}.{name}: GOV-02 — provisional 条目不得使用 contractor_confirmed 来源")
        # GOV-03
        if status == STATUS_INSUFFICIENT_DATA:
            for key in ("available_crews", "workers_per_crew", "output_multiplier",
                        "default_multiplier", "max_workers_per_workfront"):
                if spec.get(key) is not None:
                    raise ValueError(f"{section}.{name}: GOV-03 — insufficient_data 不得携带数值 '{key}'")
        findings.append(dict(section=section, entry=name, status=status, source_type=stype,
                             confirmed_by=confirmed_by, basis=bool(basis)))

    for section in ("crew_pool", "workfront_capacity", "congestion_model", "shift_model"):
        for name, spec in _specs(section, cfg):
            _check(name, spec, section)

    # GOV-04 — the inherited single-crew default must be declared as such
    pool = cfg.get("crew_pool") or {}
    if not (pool.get("by_trade") or {}):
        dflt = pool.get("default") or {}
        if int(dflt.get("available_crews", 1) or 1) == 1:
            if (dflt.get("status") or "") != STATUS_PROVISIONAL:
                raise ValueError("crew_pool.default: GOV-04 — 继承的单班组假设必须保持 status=provisional")
            if dflt.get("confirmed_by") is not None:
                raise ValueError("crew_pool.default: GOV-04 — 继承假设的 confirmed_by 必须为 null")
            if INHERITED_BASIS not in str(dflt.get("basis") or ""):
                raise ValueError(f"crew_pool.default: GOV-04 — basis 必须声明 '{INHERITED_BASIS}'")

    # entry_schema must stay a structure illustration, never data
    schema = ((pool.get("entry_schema") or {}).get("example") or {})
    for trade, spec in schema.items():
        filled = {k: v for k, v in (spec or {}).items()
                  if k not in ("status",) and v is not None}
        if filled:
            raise ValueError(f"crew_pool.entry_schema.example.{trade}: 结构示例不得填入数值 "
                             f"（发现 {sorted(filled)}）。真实数据写入 by_trade。")

    return dict(rules=[r["id"] for r in (cfg.get("governance") or {}).get("rules", [])],
                checked=findings,
                assertion="no assumption is silently promoted to a commitment",
                assertion_pass=True)


# ---------------------------------------------------------------------------
# resource input readiness
# ---------------------------------------------------------------------------
def section_readiness(section: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_resource_constraints()
    specs = _specs(section, cfg)
    if not specs:
        return dict(section=section, entries=0, readiness=READINESS_UNCONFIRMED,
                    counts={}, note="该节无任何声明条目")
    counts = {STATUS_CONFIRMED: 0, STATUS_PROVISIONAL: 0, STATUS_INSUFFICIENT_DATA: 0}
    rows = []
    for name, spec in specs:
        status = (spec.get("status") or STATUS_PROVISIONAL).lower()
        counts[status] = counts.get(status, 0) + 1
        conf = source_confidence(spec.get("source_type"), status, cfg)
        rows.append(dict(entry=name, status=status, source_type=spec.get("source_type"),
                         confirmed_by=spec.get("confirmed_by"),
                         confidence=conf["confidence"],
                         confidence_label=conf["confidence_label"]))
    total = len(specs)
    if counts.get(STATUS_CONFIRMED, 0) == total:
        readiness = READINESS_CONFIRMED
    elif counts.get(STATUS_CONFIRMED, 0) == 0:
        readiness = READINESS_UNCONFIRMED
    else:
        readiness = READINESS_PARTIAL
    return dict(section=section, entries=total, counts=counts, readiness=readiness, rows=rows,
                note=("全部条目已确认" if readiness == READINESS_CONFIRMED else
                      "无任何已确认条目 —— 资源可用性未确认" if readiness == READINESS_UNCONFIRMED else
                      "部分条目已确认，其余仍为假设"))


def resource_readiness(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Overall readiness of the resource inputs. V6.3 must be able to say:
    'the scenario is mathematically feasible but contractor availability is unconfirmed'."""
    cfg = cfg or load_resource_constraints()
    sections = ((cfg.get("governance") or {}).get("readiness") or {}).get("sections") or \
        ["crew_pool", "workfront_capacity", "congestion_model", "shift_model"]
    rows = [section_readiness(s, cfg) for s in sections]
    confirmed = [r for r in rows if r["readiness"] == READINESS_CONFIRMED]
    if rows and len(confirmed) == len(rows):
        overall = READINESS_CONFIRMED
    elif confirmed:
        overall = READINESS_PARTIAL
    else:
        overall = READINESS_UNCONFIRMED
    return dict(overall=overall, sections=rows,
                confirmed_sections=[r["section"] for r in confirmed],
                unconfirmed_sections=[r["section"] for r in rows if r["readiness"] != READINESS_CONFIRMED],
                scenario_gate=(True if overall == READINESS_CONFIRMED else False),
                scenario_statement=(
                    "资源输入已确认：情景结论可用于对外承诺。" if overall == READINESS_CONFIRMED else
                    "情景在数学上可行，但承包商资源可用性尚未确认。"
                    if overall == READINESS_PARTIAL else
                    "情景在数学上可行，但资源输入全部为假设，承包商资源可用性尚未确认，不得作为承诺。"),
                note="就绪度只描述资源输入的证据强度，不描述排程质量。")


def may_label_recommendation(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """GOV-07: a crew-count 'optimum' may only be called recommended when inputs are confirmed."""
    return resource_readiness(cfg)["overall"] == READINESS_CONFIRMED


def label_for_optimum(cfg: Optional[Dict[str, Any]] = None) -> str:
    return RECOMMENDED if may_label_recommendation(cfg) else DIAGNOSTIC_ONLY


# ---------------------------------------------------------------------------
# registers: what is assumed, what is confirmed
# ---------------------------------------------------------------------------
def assumption_register(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_resource_constraints()
    provisional, confirmed = [], []
    for section in ("crew_pool", "workfront_capacity", "congestion_model", "shift_model"):
        for name, spec in _specs(section, cfg):
            status = (spec.get("status") or STATUS_PROVISIONAL).lower()
            conf = source_confidence(spec.get("source_type"), status, cfg)
            row = dict(section=section, entry=name, status=status,
                       source_type=spec.get("source_type"),
                       basis=spec.get("basis"), confirmed_by=spec.get("confirmed_by"),
                       calibration_status=spec.get("calibration_status"),
                       confidence=conf["confidence"],
                       confidence_label=conf["confidence_label"],
                       is_contractor_commitment=conf["is_contractor_commitment"])
            (confirmed if status == STATUS_CONFIRMED else provisional).append(row)
    return dict(provisional=provisional, confirmed=confirmed,
                provisional_count=len(provisional), confirmed_count=len(confirmed),
                note="confirmed 清单为空是正常的：V6.2.1 阶段没有任何承包商确认的资源输入。")


# ---------------------------------------------------------------------------
# crew pool governance: pool is not pool size
# ---------------------------------------------------------------------------
def crew_pool_declaration(trade: Optional[str], cfg: Optional[Dict[str, Any]] = None,
                          rates: Optional[Dict[str, Any]] = None,
                          activity_type: Optional[str] = None) -> Dict[str, Any]:
    """The declared crew pool for one trade, with its provenance attached.

    When nothing is declared the inherited V6.1 single-crew assumption is returned and
    explicitly marked — it is an ordering assumption, not a contractor commitment.
    """
    from core import resource_model as RM
    cfg = cfg or load_resource_constraints()
    pool = RM.crew_pool(trade, cfg, rates, activity_type)
    spec, source = RM._spec_for(trade, cfg.get("crew_pool") or {}, cfg)
    conf = source_confidence(spec.get("source_type"), spec.get("status"), cfg)
    declared = bool(source == f"by_trade.{trade}")
    return dict(
        trade=trade, declared=declared, source=source,
        available_crews=int(pool["available_crews"]),
        workers_per_crew=int(pool["workers_per_crew"]),
        total_workers=int(pool["available_workers"]),
        shifts=spec.get("shifts"), hours_per_shift=spec.get("hours_per_shift"),
        assigned_floors=spec.get("assigned_floors"), assigned_zones=spec.get("assigned_zones"),
        available_from=spec.get("available_from"), available_until=spec.get("available_until"),
        status=spec.get("status", STATUS_PROVISIONAL),
        source_type=spec.get("source_type"), basis=spec.get("basis"),
        confirmed_by=spec.get("confirmed_by"),
        confidence=conf["confidence"], confidence_label=conf["confidence_label"],
        is_contractor_commitment=conf["is_contractor_commitment"],
        inherited=not declared,
        must_not_be_read_as=("承包商只有一个班组" if not declared else None),
        warning=(("未声明班组池 —— 沿用 V6.1 单班组流水假设（basis=inherited_v6_1_single_crew_pipeline）。"
                  "这是排序假设，不是承包商的资源承诺。") if not declared else None))


def pool_composition(trade: Optional[str], crews: int, workers_per_crew: int,
                     cfg: Optional[Dict[str, Any]] = None,
                     zone_area: Optional[float] = None) -> Dict[str, Any]:
    """Crew COUNT x crew SIZE, and why the product is not the whole story."""
    from core import resource_model as RM
    cfg = cfg or load_resource_constraints()
    crews = max(1, int(crews))
    per_crew = max(1, int(workers_per_crew))
    total = crews * per_crew
    eff_one = RM.crew_efficiency(trade, per_crew, cfg)
    eff_all = RM.crew_efficiency(trade, total, cfg)
    cap = RM.workfront_capacity(trade, zone_area, cfg)
    independent_output = round(crews * eff_one["effective_workers"], 4)
    pooled_output = round(eff_all["effective_workers"], 4)
    return dict(
        trade=trade, crew_count=crews, workers_per_crew=per_crew, total_workers=total,
        effective_workers_if_crews_work_separately=independent_output,
        effective_workers_if_pooled_into_one_crew=pooled_output,
        separation_gain_workers=round(independent_output - pooled_output, 4),
        separation_gain_pct=(round((independent_output - pooled_output) / pooled_output * 100, 2)
                             if pooled_output else None),
        zone_capacity_workers=int(cap["capacity_workers"]) if zone_area else None,
        crews_that_fit_in_zone=(int(min(crews, int(cap["capacity_workers"]) // per_crew))
                                if zone_area and per_crew else None),
        congestion_status=eff_all["status"],
        congestion_source_type=(cfg.get("congestion_model", {}).get("default", {}).get("source_type")),
        formula=(f"{crews} 个班组 × {per_crew} 人/班组 = {total} 人；"
                 f"分开作业有效工效 {independent_output} 人当量，"
                 f"并为 1 个 {total} 人班组则只有 {pooled_output} 人当量。"),
        assertion="crew_count != crew_size",
        assertion_pass=independent_output > pooled_output + 1e-9)


def compare_pool_compositions(trade: Optional[str], total_workers: int,
                              cfg: Optional[Dict[str, Any]] = None,
                              zone_area: Optional[float] = None,
                              options: Optional[Sequence[Tuple[int, int]]] = None
                              ) -> Dict[str, Any]:
    """Same headcount, different crew structure — the output is not the same.

    Default comparison for 24 workers: 3x8 vs 2x12 vs 1x24.
    """
    from core import resource_model as RM
    cfg = cfg or load_resource_constraints()
    total = int(total_workers)
    opts = list(options) if options else [(3, 8), (2, 12), (1, 24)]
    opts = [(c, w) for (c, w) in opts if c * w == total] or opts
    rows = []
    for crews, per in opts:
        comp = pool_composition(trade, crews, per, cfg, zone_area)
        cap = RM.workfront_capacity(trade, zone_area, cfg)
        fits = (zone_area is None) or (per <= int(cap["capacity_workers"]))
        rows.append(dict(crew_count=crews, workers_per_crew=per, total_workers=crews * per,
                         effective_workers=comp["effective_workers_if_crews_work_separately"],
                         output_per_worker=round(comp["effective_workers_if_crews_work_separately"]
                                                 / (crews * per), 4),
                         workfronts_occupied=crews,
                         fits_in_zone=bool(fits),
                         zone_capacity_workers=(int(cap["capacity_workers"]) if zone_area else None),
                         note=("多个班组可占用多个工作面" if crews > 1 else
                               "单一超大班组受自身拥堵与单区容量双重限制")))
    best = max(rows, key=lambda r: (r["effective_workers"], -r["crew_count"]))
    return dict(trade=trade, total_workers=total, zone_area=zone_area, rows=rows,
                best_effective_workers=best["effective_workers"],
                best_structure=f"{best['crew_count']} 班组 × {best['workers_per_crew']} 人",
                spread_workers=round(best["effective_workers"]
                                     - min(r["effective_workers"] for r in rows), 4),
                assertion="same headcount, different crew structure -> different effective output",
                assertion_pass=len({r["effective_workers"] for r in rows}) > 1,
                caveat="该比较只说明结构差异，不代表承包商能提供该班组数（来源仍为假设）。")


# ---------------------------------------------------------------------------
# diminishing returns
# ---------------------------------------------------------------------------
def classify_marginal_gain(marginal_saving_days: Optional[float],
                           cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A positive marginal saving is NOT automatically a reason to add crews."""
    cfg = cfg or load_resource_constraints()
    th = ((cfg.get("sensitivity") or {}).get("diminishing_returns") or {})
    neg = float(th.get("negligible_days", 2))
    mar = float(th.get("marginal_days", 5))
    sub = float(th.get("substantial_days", 15))
    if marginal_saving_days is None:
        return dict(category="unknown", marginal_saving_days=None, advice="无数据")
    s = float(marginal_saving_days)
    if s <= 0:
        cat = "none_or_negative"
    elif s <= neg:
        cat = "negligible"
    elif s <= mar:
        cat = "marginal"
    elif s <= sub:
        cat = "moderate"
    else:
        cat = "substantial"
    advice = {
        "none_or_negative": "增派班组不产生工期收益（甚至更慢）——明确不建议。",
        "negligible": f"边际节省 {s:g} 天 ≤ {neg:g} 天：分类为『递减收益』，不建议仅为该收益增派班组。",
        "marginal": f"边际节省 {s:g} 天：需权衡班组调遣、管理与现场协调成本后再决定。",
        "moderate": f"边际节省 {s:g} 天：值得评估，但先核实承包商能否提供该班组数。",
        "substantial": f"边际节省 {s:g} 天：显著收益，但必须核实资源可用性来源后才是有效结论。",
    }[cat]
    return dict(category=cat, marginal_saving_days=round(s, 2),
                thresholds=dict(negligible_days=neg, marginal_days=mar, substantial_days=sub),
                advice=advice, suggests_adding_crew=(cat in ("moderate", "substantial")))


def diminishing_returns_point(rows: Sequence[Dict[str, Any]],
                              cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """First crew count whose marginal saving falls into the negligible band."""
    cfg = cfg or load_resource_constraints()
    def _count(r):
        return r.get("crews_per_trade", r.get("crews_for_trade"))

    def _dur(r):
        return r.get("feasible_duration_days")

    point = None
    for r in rows:
        cls = classify_marginal_gain(r.get("marginal_saving_days"), cfg)
        if cls["category"] == "negligible":
            point = r
            break
    if point is None:
        return dict(found=False, crew_count=None, marginal_saving_days=None,
                    note="扫描范围内未出现递减收益点（可能在更大的班组数处）")
    prev = None
    for i, r in enumerate(rows):
        if r is point and i:
            prev = rows[i - 1]
    return dict(
        found=True,
        crew_count=_count(point),
        duration_days=_dur(point),
        marginal_saving_days=point.get("marginal_saving_days"),
        previous_crew_count=_count(prev) if prev else None,
        previous_duration_days=_dur(prev) if prev else None,
        classification="递减收益",
        statement=(f"{_count(prev) if prev else '?'} → {_count(point)} 班组："
                   f"+1 个班组换来 {point.get('marginal_saving_days'):g} 天收益，"
                   f"已落入递减区间，不建议仅为该收益增派班组。"),
        label=label_for_optimum(cfg))


def first_naturally_feasible_point(rows: Sequence[Dict[str, Any]],
                                   cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """First crew count where the plan needs no levelling edge to be conflict-free."""
    cfg = cfg or load_resource_constraints()
    for r in rows:
        raw_before = r.get("blocking_conflicts_before", r.get("blocking_before"))
        if raw_before is None:
            raw_before = 1
        if int(raw_before) == 0:
            return dict(found=True,
                        crew_count=r.get("crews_per_trade", r.get("crews_for_trade")),
                        duration_days=r.get("feasible_duration_days"),
                        finish=r.get("feasible_finish"),
                        levelling_edges=r.get("levelling_edges"),
                        statement=(f"{r.get('crews_per_trade', r.get('crews_for_trade'))} 班组时，"
                                   f"未经平衡即无阻断型班组冲突"
                                   f"（首个自然可行点）。"),
                        label=label_for_optimum(cfg))
    return dict(found=False, crew_count=None,
                note="扫描范围内没有自然可行的班组池；必须靠串行平衡或增加班组。",
                label=label_for_optimum(cfg))


# ---------------------------------------------------------------------------
# calibration dataset
# ---------------------------------------------------------------------------
def calibration_dataset_schema(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_resource_constraints()
    cal = ((cfg.get("calibration") or {}).get("congestion") or {})
    return dict(
        calibration_status=cal.get("calibration_status", "uncalibrated"),
        source_type=cal.get("source_type"),
        confirmed_by=cal.get("confirmed_by"),
        required_fields=((cal.get("dataset_schema") or {}).get("required_fields") or []),
        derived=((cal.get("dataset_schema") or {}).get("derived") or []),
        fit_rule=(cal.get("dataset_schema") or {}).get("fit_rule"),
        minimum_sample_size=(cal.get("dataset_schema") or {}).get("minimum_sample_size"),
        note="现场观测数据按此 schema 采集后，才允许改写拥挤曲线（GOV-06）。")


def blank_observation_row(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    schema = calibration_dataset_schema(cfg)
    return {f["field"]: None for f in schema["required_fields"]}


# ---------------------------------------------------------------------------
# governance summary for reports
# ---------------------------------------------------------------------------
def governance_summary(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_resource_constraints()
    reg = assumption_register(cfg)
    return dict(version=cfg.get("version"),
                readiness=resource_readiness(cfg),
                provisional=reg["provisional"], confirmed=reg["confirmed"],
                rules=(cfg.get("governance") or {}).get("rules", []),
                source_types=source_types(cfg),
                calibration=calibration_dataset_schema(cfg),
                optimum_label=label_for_optimum(cfg),
                may_recommend=may_label_recommendation(cfg))
