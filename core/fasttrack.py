"""Fast-track (overlap) feasibility analysis — V6.4, GOV-18.

What it does
------------
Takes a solved-ready task list, inventories every Finish-to-Start edge, decides for each
edge whether overlapping is *legal* under the dependency ontology, and — for the legal
ones — measures the saving by changing **only that one edge** and re-solving the whole
network.

What it refuses to do
---------------------
* Touch an inspection hold point or a statutory link. Not optimisable, full stop.
* Touch any edge whose predecessor or successor sits in a statutory-waiting phase
  (permit approval, completion inspection):行政时间不因搭接而缩短。
* Estimate savings or add them up. GOV-18 requires one re-solve per edge, because
  summing per-edge savings double-counts the same critical path.

Everything it emits is OPTION / UNCONFIRMED. It never recommends a fast-track package.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence

from core._common import parse_predecessor
from core import dependency_ontology as DO
from core import programme_duration as PD
from core import recommendation_gate as GATE
from core.recommendation_gate import load_production_candidate_config
from core.solver_engine import GOV_APPROVAL_KEYWORDS

OPTION = "OPTION"

MOVABLE = "movable"
BLOCKED = "blocked"
NEEDS_CONFIRMATION = "needs_confirmation"


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------
def _leaf(t: Dict[str, Any]) -> bool:
    return bool(t.get("activity_type"))


def _predecessor_field(task: Dict[str, Any]) -> str:
    return str(task.get("predecessors", task.get("predecessors_template", "")) or "")


def _set_predecessor(tokens: Sequence[Any], index: int, new_token: str) -> str:
    parts = [str(x) for x in tokens]
    parts[index] = new_token
    return ",".join(parts)


def _split_predecessors(task: Dict[str, Any]) -> List[str]:
    raw = _predecessor_field(task)
    if not raw.strip():
        return []
    return [p.strip() for p in str(raw).replace(";", ",").split(",") if p.strip()]


def edge_inventory(tasks: Sequence[Dict[str, Any]],
                   onto: Optional[Dict[str, Any]] = None,
                   cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every FS edge, classified, with a legality verdict. No schedule is modified."""
    onto = onto or DO.load_ontology()
    pcfg = cfg or load_production_candidate_config()
    ft = pcfg.get("fast_track") or {}
    forbidden_classes = set(ft.get("forbidden_classes") or [])
    policies = ft.get("movable_policies") or {}

    by_id = {t.get("id"): t for t in tasks}
    phase_of: Dict[Any, Dict[str, Any]] = {}
    for ph in PD.phase_index(tasks, pcfg):
        for tid in ph["task_ids"]:
            phase_of[tid] = ph

    # 分区流水面数 = 同一工序被实例化到多少个 zone。任务上的 `workfronts` 字段是
    # 「该任务自身占用几个工作面」（恒为 1），不能用来判断流水，否则所有边都会被误判为不可搭接。
    zone_count = len({t.get("zone") for t in tasks if t.get("zone")}) or 1
    per_type_zones: Dict[str, int] = {}
    for t in tasks:
        at = t.get("activity_type")
        if at and t.get("zone"):
            per_type_zones.setdefault(at, set())
            per_type_zones[at].add(t.get("zone"))
    per_type_zones = {k: len(v) for k, v in per_type_zones.items()}

    rows: List[Dict[str, Any]] = []
    for succ in tasks:
        if not _leaf(succ):
            continue
        tokens = _split_predecessors(succ)
        for idx, tok in enumerate(tokens):
            parsed = parse_predecessor(tok)
            if not parsed:
                continue
            pid, rel = parsed[0][0], str(parsed[0][1] or "FS").upper()
            if rel != "FS":
                continue
            pred = by_id.get(pid)
            if pred is None:
                continue

            classes = DO.link_classes(onto, pred.get("activity_type"), succ.get("activity_type"))
            cls = classes[0] if classes else "unmodelled"
            gated = bool(succ.get("inspection_gate") or succ.get("inspection_required"))
            if gated:
                cls = "inspection"

            ph_succ = phase_of.get(succ.get("id")) or {}
            ph_pred = phase_of.get(pred.get("id")) or {}
            statutory = bool(ph_succ.get("statutory_wait") or ph_pred.get("statutory_wait"))
            gov_named = any(k in str(pred.get("name", "")) or k in str(succ.get("name", ""))
                            for k in GOV_APPROVAL_KEYWORDS)

            scope = str(succ.get("spatial_scope") or "project")
            workfronts = (int(per_type_zones.get(succ.get("activity_type"), 1))
                          if scope == "zone" else 1)
            entry = DO._policy_entry(onto, pred.get("activity_type"), succ.get("activity_type")) or {}
            lag_declared = bool(entry) and entry.get("lag") is not None
            lag_min = int(entry.get("lag", 0) or 0)
            lag_max = int(entry.get("max_lag", 0) or 0)

            verdict, reason, risk, confirmations = _verdict(
                cls, forbidden_classes, statutory, gov_named, scope, workfronts, policies,
                ph_succ, ph_pred, lag_min, lag_max, lag_declared)
            row = dict(
                edge_id=f"{pid}->{succ.get('id')}",
                predecessor_id=pid, predecessor_name=pred.get("name"),
                successor_id=succ.get("id"), successor_name=succ.get("name"),
                dependency_class=cls, gated=gated,
                predecessor_phase=ph_pred.get("phase_no"), successor_phase=ph_succ.get("phase_no"),
                statutory_phase=statutory, gov_approval_named=gov_named,
                successor_spatial_scope=scope, workfronts=workfronts, zone_count=zone_count,
                lag_min=lag_min, lag_max=lag_max,
                lag_source=("ontology.link_policy" if lag_declared else "not_declared"),
                verdict=verdict, reason=reason, risk=risk,
                requires_confirmation=confirmations,
                token_index=idx,
            )
            rows.append(row)
    return rows


def _verdict(cls, forbidden_classes, statutory, gov_named, scope, workfronts, policies,
             ph_succ, ph_pred, lag_min=0, lag_max=0, lag_declared=False):
    if cls in forbidden_classes:
        return (BLOCKED,
                f"依赖类 {cls} 属法定/合同持有点，overridable=false，不得改为重叠（GOV-18）。",
                "n/a", [])
    if statutory:
        names = [p.get("label_zh") for p in (ph_pred, ph_succ) if p.get("statutory_wait")]
        return (BLOCKED,
                f"前/后序位于法定等待阶段（{' / '.join(str(n) for n in names if n)}）："
                f"行政时间不因搭接而缩短（GOV-18）。",
                "n/a", [])
    if gov_named:
        return (BLOCKED, "涉及政府审批/许可事项，行政行为不可搭接（GOV-18）。", "n/a", [])
    if cls == "resource":
        return (BLOCKED, "资源竞争不产生网络边，不参与 fast-track。", "n/a", [])
    if cls == "unmodelled":
        return (BLOCKED, "本体未建模该类型对的条件关系，不得凭直觉搭接。", "n/a", [])

    pol = policies.get(cls) or {}
    if not pol or pol.get("movable") is False:
        return (BLOCKED, f"依赖类 {cls} 无允许重叠的策略条目。", "n/a", [])

    # ontology scope policy: SS requires zone scope AND workfronts > 1
    if scope != "zone":
        return (BLOCKED,
                f"后继工序作用域为 {scope}，非 zone；按本体 scope_policy 不允许 SS 重叠。",
                "n/a", [])
    if workfronts <= 1:
        return (BLOCKED,
                f"workfronts={workfronts}，无分区流水面可搭接；SS 的充分条件不成立。",
                "n/a", [])
    if not lag_declared:
        return (NEEDS_CONFIRMATION,
                f"依赖类 {cls} 允许重叠，但本体未声明该类型对的 lag 区间：搭接量无证据，"
                f"按 lag=0 测出的节省只是上界，不可作为可采用的搭接量。",
                "high", ["本体补充该类型对的 lag 区间（或由 PM/承包商书面确认搭接量）"])
    if cls == "information":
        return (NEEDS_CONFIRMATION,
                "分区作用域与流水面具备，但信息类依赖需设计/技术参数分批冻结确认后才可重叠。",
                pol.get("risk", "medium"), list(pol.get("requires_confirmation") or []))
    return (MOVABLE,
            f"依赖类 {cls} 允许重叠：条件作用域 zone 且 workfronts={workfronts} > 1，"
            f"按 lag 规则 clamp(ceil(前序工期 / 工作面数), {lag_min}, {lag_max}) 改为 SS+lag。",
            pol.get("risk", "high"), list(pol.get("requires_confirmation") or []))


# ---------------------------------------------------------------------------
# measurement (one edge at a time, full re-solve)
# ---------------------------------------------------------------------------
def measure_edge(tasks: Sequence[Dict[str, Any]], row: Dict[str, Any], start_date: str,
                 cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Change exactly one edge to SS+lag, re-solve, and report the real delta."""
    from core.solver_engine import solve_schedule

    pcfg = cfg or load_production_candidate_config()
    base = solve_schedule(copy.deepcopy(list(tasks)), start_date)
    base_finish = base["finish_date"]

    by_id = {t.get("id"): t for t in tasks}
    pred = by_id.get(row["predecessor_id"])
    succ = by_id.get(row["successor_id"])
    if pred is None or succ is None:
        return dict(saving_days=None, error="找不到边端点")

    pred_dur = int(pred.get("duration_days", pred.get("duration", 0)) or 0)
    workfronts = max(1, int(row.get("workfronts") or 1))
    lag, basis = DO.compute_pipeline_lag(pred_dur, workfronts,
                                         int(row.get("lag_min") or 0),
                                         int(row.get("lag_max") or 0))

    trial = copy.deepcopy(list(tasks))
    t_succ = next((t for t in trial if t.get("id") == row["successor_id"]), None)
    if t_succ is None:
        return dict(saving_days=None, error="找不到后继任务")
    tokens = _split_predecessors(t_succ)
    idx = int(row.get("token_index") or 0)
    if idx >= len(tokens):
        return dict(saving_days=None, error="前置 token 索引越界")
    tokens[idx] = f"{row['predecessor_id']}SS+{lag}"
    t_succ["predecessors"] = ",".join(tokens)

    try:
        after = solve_schedule(trial, start_date)
    except Exception as exc:  # pragma: no cover - defensive
        return dict(saving_days=None, error=f"重解失败：{exc}")

    def _delta(a: str, b: str) -> int:
        from datetime import date
        return (date.fromisoformat(b) - date.fromisoformat(a)).days

    saving = _delta(after["finish_date"], base_finish)
    return dict(proposed_token=f"{row['predecessor_id']}SS+{lag}", lag_days=lag,
                lag_basis=basis, baseline_finish=base_finish,
                trial_finish=after["finish_date"], saving_days=saving,
                error=None)


def analyse(tasks: Sequence[Dict[str, Any]], start_date: str,
            cfg: Optional[Dict[str, Any]] = None,
            onto: Optional[Dict[str, Any]] = None,
            max_edges: Optional[int] = None,
            resource_cfg: Optional[Dict[str, Any]] = None,
            required_confirmations: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Inventory + measure + gate. Returns rows and a no-recommendation decision."""
    pcfg = cfg or load_production_candidate_config()
    rows = edge_inventory(tasks, onto=onto, cfg=pcfg)
    movable = [r for r in rows if r["verdict"] in (MOVABLE, NEEDS_CONFIRMATION)]
    if max_edges:
        movable = movable[:max_edges]
    for r in movable:
        r.update(measure_edge(tasks, r, start_date, pcfg))
    for r in rows:
        r.setdefault("saving_days", None)
        r["status"] = OPTION
        if r["verdict"] == BLOCKED:
            r["classification"] = "NOT_PERMITTED"
        elif r["verdict"] == NEEDS_CONFIRMATION:
            r["classification"] = "OPTION_UNCONFIRMED"
        else:
            r["classification"] = "OPTION_UNCONFIRMED"

    decision = GATE.evaluate(
        [dict(r, id=r["edge_id"]) for r in rows if r["verdict"] != BLOCKED],
        resource_cfg=resource_cfg,
        required_confirmations=required_confirmations,
        context="fast_track",
        candidate_note=("每条边单独重解验证节省；不可累加——同一关键路径上的多次搭接会重复计一次收益。"),
    )
    GATE.assert_no_recommendation(decision)

    measured = [r for r in rows if r.get("saving_days") is not None]
    return dict(
        rows=rows,
        summary=dict(edges_total=len(rows),
                     blocked=len([r for r in rows if r["verdict"] == BLOCKED]),
                     movable=len([r for r in rows if r["verdict"] == MOVABLE]),
                     needs_confirmation=len([r for r in rows if r["verdict"] == NEEDS_CONFIRMATION]),
                     measured=len(measured),
                     max_single_edge_saving_days=(max((r["saving_days"] for r in measured),
                                                      default=None))),
        decision=decision,
        method=((pcfg.get("fast_track") or {}).get("evaluation") or {}),
        must_not_be_read_as=("本表不是可执行的 fast-track 方案：单边节省不可累加，"
                             "且每条搭接都需承包商与设计方确认后才成立。"),
    )
