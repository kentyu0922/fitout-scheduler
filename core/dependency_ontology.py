# -*- coding: utf-8 -*-
"""
core/dependency_ontology.py — V6.0 typed dependency ontology

Replaces name/keyword guessing with a condition model over activity_type IDs.

    activity A  --produces-->  condition C  --required by-->  activity B

A condition carries a class (hard / information / resource / inspection /
authority) and a scope (zone / floor / project). The relationship is *derived*,
never guessed:

    SS is allowed only when
        (a) the condition class permits overlap, AND
        (b) the condition scope is `zone`, AND
        (c) the project decision is sectional with workfronts > 1.

Everything else falls back to FS. Inspection and authority conditions are never
overlapped and never dropped — that is enforced in code, not left to callers.

Design rules
------------
* This module models construction logic only. It never sets duration, crew or
  productivity; those stay in the quantity engine and the productivity engine.
* The legacy keyword table (config/dependency_rules.json#rules) and the flat
  typed table (#fitout_rules) are compatibility sources only. Production mode
  (`mode="typed"`) resolves by activity_type; `mode="auto"` additionally accepts
  keyword-derived links for pairs the ontology does not model.
* No factor is invented to complete a formula: when the ontology has no answer
  the resolver returns source="none" rather than a defaulted FS+0.
"""
import json
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from core._common import parse_predecessor, split_predecessor_id_suffix
except ImportError:  # pragma: no cover
    from _common import parse_predecessor, split_predecessor_id_suffix  # type: ignore

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONTOLOGY_PATH = os.path.join(BASE_DIR, "config", "dependency_ontology.json")
LEGACY_RULES_PATH = os.path.join(BASE_DIR, "config", "dependency_rules.json")

CLASSES = ("hard", "information", "resource", "inspection", "authority")
NON_OVERRIDABLE = ("inspection", "authority")
SCOPES = ("zone", "floor", "project")
RELATIONSHIPS = ("FS", "SS", "FF", "SF")
MODES = ("typed", "legacy", "auto")

# Activities whose completion is held by a concealed-work release. Kept in sync
# with fitout_wbs.json#inspection_requirements.
GATED_ACTIVITIES = ("gypsum_boarding", "gypsum_ceiling", "feature_ceiling", "testing_commissioning")


# ---------------------------------------------------------------------------
# loading + self-validation
# ---------------------------------------------------------------------------
def load_ontology(path: Optional[str] = None, validate: bool = True) -> Dict[str, Any]:
    p = path or ONTOLOGY_PATH
    with open(p, "r", encoding="utf-8") as f:
        onto = json.load(f)
    if validate:
        issues = validate_ontology(onto)
        errors = [i for i in issues if i["level"] == "error"]
        if errors:
            raise ValueError("dependency ontology invalid: " + "; ".join(e["message"] for e in errors[:5]))
    return onto


def validate_ontology(onto: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Config integrity. Returns issues; callers decide whether to fail."""
    issues: List[Dict[str, Any]] = []
    conditions = onto.get("conditions") or {}
    activities = onto.get("activity_types") or {}
    policy = onto.get("relationship_policy") or {}
    scopes = onto.get("scope_policy") or {}

    for name, meta in conditions.items():
        if meta.get("class") not in CLASSES:
            issues.append({"level": "error", "code": "ONTO_CLASS", "message": f"condition {name}: class must be one of {CLASSES}"})
        if meta.get("scope") not in SCOPES:
            issues.append({"level": "error", "code": "ONTO_SCOPE", "message": f"condition {name}: scope must be one of {SCOPES}"})

    known = set(conditions)
    for act, spec in activities.items():
        for field in ("requires", "produces", "resource"):
            for cid in spec.get(field, []) or []:
                if cid not in known:
                    issues.append({"level": "error", "code": "ONTO_UNKNOWN_CONDITION",
                                   "message": f"activity {act}.{field} references undefined condition {cid}"})
        for cid in spec.get("produces", []) or []:
            if conditions.get(cid, {}).get("class") == "resource":
                issues.append({"level": "error", "code": "ONTO_RESOURCE_PRODUCED",
                               "message": f"activity {act} produces resource condition {cid}; resource conditions are consumed, never produced"})
        for cid in spec.get("resource", []) or []:
            if conditions.get(cid, {}).get("class") != "resource":
                issues.append({"level": "error", "code": "ONTO_RESOURCE_MISCLASS",
                               "message": f"activity {act}.resource lists {cid} which is not class=resource"})

    for cid, meta in conditions.items():
        if any(cid in (spec.get("produces") or []) for spec in activities.values()):
            continue
        cls = meta.get("class")
        if cls == "resource":
            continue  # consumed only, by design
        if cls == "inspection":
            continue  # produced by explicit gate tasks, not by a typed activity
        issues.append({"level": "warn", "code": "ONTO_NO_PRODUCER",
                       "message": f"condition {cid} (class={cls}) has no producing activity_type; "
                                  f"it is external (permit / approval) or unused"})

    for cls in CLASSES:
        if cls not in policy:
            issues.append({"level": "error", "code": "ONTO_POLICY", "message": f"relationship_policy missing class {cls}"})
    for cls in ("hard", "information", "inspection", "authority"):
        spec = policy.get(cls) or {}
        for key in ("single_workfront", "sectional"):
            if spec.get(key) not in RELATIONSHIPS:
                issues.append({"level": "error", "code": "ONTO_POLICY_REL",
                               "message": f"relationship_policy.{cls}.{key} must be one of {RELATIONSHIPS}"})
    for scope in SCOPES:
        if scope not in scopes:
            issues.append({"level": "warn", "code": "ONTO_SCOPE_POLICY", "message": f"scope_policy missing {scope}"})

    levels = set(onto.get("confidence_levels") or [])
    if not levels:
        issues.append({"level": "error", "code": "ONTO_CONF", "message": "confidence_levels missing"})

    seen_pairs = set()
    for entry in onto.get("link_policy") or []:
        eid = entry.get("id") or f"{entry.get('predecessor')}>{entry.get('successor')}"
        pair = (entry.get("predecessor"), entry.get("successor"))
        if pair in seen_pairs:
            issues.append({"level": "error", "code": "ONTO_DUP", "message": f"link_policy duplicate pair {pair}"})
        seen_pairs.add(pair)
        cid = entry.get("condition")
        if cid not in known:
            issues.append({"level": "error", "code": "ONTO_LINK_CONDITION", "message": f"{eid}: undefined condition {cid}"})
            continue
        for key in ("single_workfront_relationship", "sectional_relationship"):
            if entry.get(key) not in RELATIONSHIPS:
                issues.append({"level": "error", "code": "ONTO_LINK_REL", "message": f"{eid}: {key} must be one of {RELATIONSHIPS}"})
        for key in ("lag", "max_lag"):
            value = entry.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                issues.append({"level": "error", "code": "ONTO_LINK_LAG", "message": f"{eid}: {key} must be a nonnegative integer"})
        if entry.get("lag", 0) > entry.get("max_lag", 0):
            issues.append({"level": "error", "code": "ONTO_LINK_LAG", "message": f"{eid}: lag must not exceed max_lag"})
        conf = entry.get("confidence", onto.get("confidence_default"))
        if conf not in levels:
            issues.append({"level": "error", "code": "ONTO_LINK_CONF", "message": f"{eid}: confidence {conf!r} not in {sorted(levels)}"})
        # the link must be derivable from the condition model
        pred_spec = activities.get(entry.get("predecessor")) or {}
        succ_spec = activities.get(entry.get("successor")) or {}
        if cid not in (pred_spec.get("produces") or []):
            issues.append({"level": "error", "code": "ONTO_LINK_NOT_PRODUCED",
                           "message": f"{eid}: predecessor {entry.get('predecessor')} does not produce {cid}"})
        if cid not in (succ_spec.get("requires") or []):
            issues.append({"level": "error", "code": "ONTO_LINK_NOT_REQUIRED",
                           "message": f"{eid}: successor {entry.get('successor')} does not require {cid}"})
    return issues


# ---------------------------------------------------------------------------
# condition model helpers
# ---------------------------------------------------------------------------
def produces(onto: Dict[str, Any], activity_type: Optional[str]) -> List[str]:
    return list((onto.get("activity_types") or {}).get(activity_type or "", {}).get("produces") or [])


def requires(onto: Dict[str, Any], activity_type: Optional[str]) -> List[str]:
    return list((onto.get("activity_types") or {}).get(activity_type or "", {}).get("requires") or [])


def resource_conditions(onto: Dict[str, Any], activity_type: Optional[str]) -> List[str]:
    return list((onto.get("activity_types") or {}).get(activity_type or "", {}).get("resource") or [])


def condition_class(onto: Dict[str, Any], condition: str) -> Optional[str]:
    return (onto.get("conditions") or {}).get(condition, {}).get("class")


def shared_conditions(onto: Dict[str, Any], pred: Optional[str], succ: Optional[str]) -> List[str]:
    """Conditions the predecessor produces that the successor requires (resource excluded)."""
    out = []
    for cid in produces(onto, pred):
        if cid in requires(onto, succ):
            out.append(cid)
    return out


def link_classes(onto: Dict[str, Any], pred: Optional[str], succ: Optional[str]) -> List[str]:
    """Ordered, de-duplicated classes of the conditions linking pred → succ."""
    seen, out = set(), []
    for cid in shared_conditions(onto, pred, succ):
        cls = condition_class(onto, cid)
        if cls and cls not in seen:
            seen.add(cls)
            out.append(cls)
    return out


def compute_pipeline_lag(pred_duration_days: int, workfronts: int, lag_min: int, lag_max: int) -> Tuple[int, str]:
    """Deterministic sectional lag: clamp(ceil(pred_duration / workfronts), min, max)."""
    dur = max(0, int(pred_duration_days or 0))
    wf = max(1, int(workfronts))
    raw = int(math.ceil(dur / wf)) if dur else 0
    lag = max(int(lag_min), min(int(lag_max), raw))
    return lag, f"ceil({dur}d / {wf} workfronts) = {raw} → clamp[{int(lag_min)},{int(lag_max)}] = {lag}"


# ---------------------------------------------------------------------------
# resolution — the "why SS+3 and not FS" answer
# ---------------------------------------------------------------------------
def _policy_entry(onto: Dict[str, Any], pred: str, succ: str) -> Optional[Dict[str, Any]]:
    for entry in onto.get("link_policy") or []:
        if entry.get("predecessor") == pred and entry.get("successor") == succ:
            return entry
    return None


def _no_link(pred: str, succ: str, reason: str) -> Dict[str, Any]:
    return dict(predecessor=pred, successor=succ, condition=None, condition_scope=None,
                dependency_class=None, relationship=None, lag=None, lag_basis=None,
                reason=reason, why_not_fs=None, why_not_ss=None, confidence=None,
                confidence_reasons=[], policy_id=None, source="none")


def resolve_dependency(
    predecessor: Optional[str],
    successor: Optional[str],
    context: Optional[Dict[str, Any]] = None,
    onto: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve one pred→succ pair into a fully explained link.

    context keys:
        workfronts            int   (default 1)
        sectional             bool  (default False)
        predecessor_duration_days int (default 0)
        gated                 bool  — successor completion is held by an inspection gate
    """
    onto = onto or load_ontology()
    ctx = context or {}
    if not predecessor or not successor:
        return _no_link(predecessor, successor, "缺少 activity_type，无法按类型解析")

    workfronts = max(1, int(ctx.get("workfronts", 1) or 1))
    sectional = bool(ctx.get("sectional", False)) and workfronts > 1
    pred_dur = int(ctx.get("predecessor_duration_days", 0) or 0)
    gated = bool(ctx.get("gated", False))

    entry = _policy_entry(onto, predecessor, successor)
    shared = shared_conditions(onto, predecessor, successor)
    if entry is None and not shared:
        return _no_link(predecessor, successor, f"本体未建模 {predecessor} → {successor} 的条件关系")

    if entry is not None:
        condition = entry["condition"]
        source = "ontology.link_policy"
        policy_id = entry.get("id")
    else:
        condition = shared[0]
        source = "ontology.condition"
        policy_id = None

    cond_meta = (onto.get("conditions") or {}).get(condition) or {}
    cls = cond_meta.get("class") or "hard"
    scope = cond_meta.get("scope") or "project"
    class_policy = (onto.get("relationship_policy") or {}).get(cls) or {}
    gate_pol = onto.get("gate_policy") or {}
    conf_levels = onto.get("confidence_levels") or []

    # ---- relationship ----
    if entry is not None:
        base_rel = entry["sectional_relationship"] if sectional else entry["single_workfront_relationship"]
        base_lag = int(entry.get("lag", 0) or 0)
        max_lag = int(entry.get("max_lag", 0) or 0)
        rationale = entry.get("rationale") or ""
    else:
        base_rel = class_policy.get("sectional") if sectional else class_policy.get("single_workfront")
        base_rel = base_rel or "FS"
        base_lag, max_lag = 0, 10
        rationale = f"{cond_meta.get('label_zh', condition)}：{cond_meta.get('label_en', '')}".strip("：")

    relationship = base_rel
    notes: List[str] = []
    conf_reasons: List[str] = []

    if cls in NON_OVERRIDABLE:
        if relationship != "FS":
            notes.append(f"{cls} 类依赖不可重叠，{base_rel} 已强制回落 FS")
        relationship = "FS"
    # FF aligns finishes — it never starts the successor early, so floor/project
    # scope does not forbid it. SS does start the successor early, so it does.
    if relationship == "SS" and scope in ("floor", "project"):
        notes.append(f"条件作用域为 {scope}（{ (onto.get('scope_policy') or {}).get(scope, '') }），不随分区重叠")
        relationship = "FS"
    if gated and gate_pol.get("force_fs_when_gated", True) and relationship != "FS":
        notes.append(f"后继工序被检查门约束（{gate_pol.get('force_fs_label', 'inspection_gate_ids / inspection_required')}），强制 FS")
        relationship = "FS"

    # A successor that itself needs a floor/project-scoped hold point cannot start
    # early: the hold point is not yet passed anywhere on site.
    blocking_conditions = [
        cid for cid in requires(onto, successor)
        if condition_class(onto, cid) in NON_OVERRIDABLE
        and ((onto.get("conditions") or {}).get(cid) or {}).get("scope") in ("floor", "project")
    ]
    if blocking_conditions and relationship != "FS":
        labels = "/".join(str((onto.get("conditions") or {}).get(c, {}).get("label_zh", c)) for c in blocking_conditions)
        notes.append(f"后继工序 {successor} 需先通过整层/项目级检查或许可（{labels}），不得提前开始，"
                     f"{base_rel} 已强制回落 FS")
        relationship = "FS"

    # ---- lag ----
    if relationship == "SS":
        lag, lag_basis = compute_pipeline_lag(pred_dur, workfronts, 1, max_lag)
    elif relationship == "FF":
        lag, lag_basis = 0, "FF 以完成面对齐；另附 SS 锚点防止后继先于前序启动"
    else:
        lag, lag_basis = base_lag, (entry.get("lag_basis") or ("drying_curing_allowance" if base_lag else "none")) if entry else "none"

    # ---- why not ----
    why_not_fs = None
    why_not_ss = None
    if relationship == "SS":
        why_not_fs = (f"条件 {condition} 作用域为 {scope}，分区流水已启用（workfronts={workfronts}）："
                      f"前序在首个分区形成该条件后，后继即可跟进，等全层完成会浪费流水。")
    elif relationship == "FF":
        why_not_fs = (f"条件 {condition} 属于逐段形成、同步收口的作业面状态："
                      f"以后继完成面对齐前序完成面（FF），并附 SS 锚点防止后继先于前序启动。")
    if relationship != "SS":
        blockers = []
        if relationship == "FF":
            blockers.append(f"{base_rel} 为完成面对齐而非提前开始：分区流水不改变“后继不得早于前序收口”这一性质")
        else:
            if cls in NON_OVERRIDABLE:
                blockers.append(f"{cls} 类依赖（{cond_meta.get('label_zh', condition)}）不可重叠，必须先通过")
            if scope in ("floor", "project"):
                blockers.append(f"条件作用域为 {scope}，须整体形成后后继才可开始")
            if blocking_conditions:
                blockers.append("后继工序需先通过整层/项目级检查或许可（"
                                + "/".join(str((onto.get("conditions") or {}).get(c, {}).get("label_zh", c))
                                           for c in blocking_conditions) + "）")
        if not sectional:
            blockers.append("未启用分区流水（workfronts<=1 或非分区决策）")
        elif entry is not None and entry.get("sectional_relationship") == "FS":
            extra = f"（{entry['lag_basis']}）" if entry.get("lag_basis") else "（该策略未授权分区重叠）"
            blockers.append(f"连接策略 {entry.get('id')} 在分区模式下仍规定 FS{extra}")
        if gated and gate_pol.get("force_fs_when_gated", True):
            blockers.append("后继被检查门约束")
        why_not_ss = "；".join(blockers) if blockers else "该连接策略未授权重叠"

    confidence = entry.get("confidence") if entry else onto.get("confidence_default", "medium")
    if confidence not in conf_levels:
        confidence = onto.get("confidence_default", "medium")
    conf_reasons.append(f"依赖类别 {cls}，条件作用域 {scope}")
    conf_reasons.append(f"来源 {source}")
    if entry and entry.get("status") == "provisional":
        conf_reasons.append("连接策略状态为 provisional，缺现场确认")
    if relationship == "SS" and pred_dur == 0:
        conf_reasons.append("前序工期为 0，滞后按 0 计算，分区流水的实际节拍未验证")
        confidence = "low" if confidence == "high" else confidence
    for n in notes:
        conf_reasons.append(n)

    return dict(
        predecessor=predecessor, successor=successor, condition=condition, condition_scope=scope,
        dependency_class=cls, relationship=relationship, lag=lag, lag_basis=lag_basis,
        reason=rationale, why_not_fs=why_not_fs, why_not_ss=why_not_ss,
        confidence=confidence, confidence_reasons=conf_reasons, policy_id=policy_id,
        source=source, constraint_notes=notes, blocking_conditions=blocking_conditions,
        condition_label_zh=cond_meta.get("label_zh"), condition_label_en=cond_meta.get("label_en"),
    )


def request_override(
    predecessor: Optional[str],
    successor: Optional[str],
    requested_relationship: str,
    context: Optional[Dict[str, Any]] = None,
    onto: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fast-track request: may this link be changed to `requested_relationship`?

    Returns {"allowed": bool, "relationship": str, "reason": str}. Inspection and
    authority links are never allowed to be overlapped or dropped.
    """
    onto = onto or load_ontology()
    resolved = resolve_dependency(predecessor, successor, context, onto)
    if resolved["source"] == "none":
        return dict(allowed=False, relationship=None, reason=resolved["reason"], resolved=resolved)
    if requested_relationship.upper() not in RELATIONSHIPS:
        return dict(allowed=False, relationship=resolved["relationship"],
                    reason=f"未知关系类型 {requested_relationship!r}", resolved=resolved)
    cls = resolved["dependency_class"]
    if cls in NON_OVERRIDABLE and requested_relationship.upper() != "FS":
        return dict(allowed=False, relationship="FS", resolved=resolved,
                    reason=f"{cls} 类依赖（{resolved.get('condition_label_zh')}）不可重叠或删除；"
                           f"请求 {requested_relationship} 已拒绝，保持 FS。")
    if resolved.get("blocking_conditions") and requested_relationship.upper() != "FS":
        labels = "/".join(str((onto.get("conditions") or {}).get(c, {}).get("label_zh", c))
                          for c in resolved["blocking_conditions"])
        return dict(allowed=False, relationship="FS", resolved=resolved,
                    reason=f"后继工序 {successor} 需先通过整层/项目级检查或许可（{labels}），"
                           f"请求 {requested_relationship} 已拒绝，保持 FS。")
    gate_pol = onto.get("gate_policy") or {}
    if (context or {}).get("gated") and gate_pol.get("force_fs_when_gated", True) and requested_relationship.upper() != "FS":
        return dict(allowed=False, relationship="FS", resolved=resolved,
                    reason="后继被检查门约束，强制 FS，不得改为 " + requested_relationship.upper())
    return dict(allowed=True, relationship=requested_relationship.upper(), resolved=resolved,
                reason=f"{cls} 类依赖允许调整；由 CPM 复核后生效。")


# ---------------------------------------------------------------------------
# legacy keyword fallback (compatibility only)
# ---------------------------------------------------------------------------
def legacy_keyword_links(tasks: Sequence[Dict[str, Any]], rules: Optional[Dict[str, Any]] = None
                         ) -> List[Dict[str, Any]]:
    """Keyword-derived links from config/dependency_rules.json#rules (SEC-01..05).

    Compatibility fallback. Production resolves by activity_type; this is only
    consulted in mode="auto" for pairs the ontology does not model.
    """
    if rules is None:
        with open(LEGACY_RULES_PATH, "r", encoding="utf-8") as f:
            rules = json.load(f)
    scope = (rules or {}).get("scope") or {}
    min_level = int(scope.get("min_outline_level", 3))
    out: List[Dict[str, Any]] = []
    for rule in (rules or {}).get("rules", []):
        for si, succ in enumerate(tasks):
            if not _leaf(succ, min_level) or not _matches(succ, rule.get("successor_keywords") or []):
                continue
            pi = None
            for j in range(si - 1, -1, -1):
                if _leaf(tasks[j], min_level) and _matches(tasks[j], rule.get("predecessor_keywords") or []):
                    pi = j
                    break
            if pi is None:
                continue
            out.append(dict(rule_id=rule.get("id"), predecessor_id=tasks[pi]["id"], successor_id=succ["id"],
                            relationship=rule.get("relationship", "SS"), lag_days=rule.get("lag_days") or {},
                            predecessor_duration_days=int(tasks[pi].get("duration_days", tasks[pi].get("duration", 0)) or 0),
                            source="legacy.keyword"))
    return out


def _leaf(task: Dict[str, Any], min_level: int) -> bool:
    if task.get("milestone"):
        return False
    if int(task.get("outline_level", task.get("level", 3)) or 3) < min_level:
        return False
    return float(task.get("duration_days", task.get("duration", 0)) or 0) > 0


def _matches(task: Dict[str, Any], keywords: Sequence[str]) -> bool:
    name = str(task.get("name", ""))
    return any(k in name for k in keywords)


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------
def _zone(task: Dict[str, Any]) -> Any:
    return task.get("zone")


def _token_id(token: str) -> Optional[int]:
    parsed = parse_predecessor(token or "")
    return parsed[0][0] if parsed else None


def _is_gated(task: Dict[str, Any]) -> bool:
    return bool(task.get("inspection_gate_ids") or task.get("inspection_required")
                or task.get("activity_type") in GATED_ACTIVITIES)


def apply_ontology_dependencies(
    tasks: List[Dict[str, Any]],
    decision: Dict[str, Any],
    onto: Optional[Dict[str, Any]] = None,
    mode: str = "typed",
) -> Dict[str, Any]:
    """Materialise ontology links onto the task list (in place).

    Returns {"decision", "applied": [...], "skipped": [...], "explanations": [...],
             "unbound_conditions": [...], "protection": [...]}.
    Each applied row carries dependency_class / relationship / lag / lag_basis /
    reason / confidence / source so a planning manager can audit every link.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    onto = onto or load_ontology()
    by_id = {t["id"]: t for t in tasks}
    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    explanations: List[Dict[str, Any]] = []
    workfronts = max(1, int(decision.get("workfronts", 1) or 1))
    sectional = bool(decision.get("sectional")) and workfronts > 1

    for index, task in enumerate(tasks):
        activity = task.get("activity_type")
        gates = list(task.get("inspection_gate_ids") or [])
        needs_gate = _is_gated(task)
        if needs_gate and not gates:
            raise ValueError(f"Task {task['id']} requires explicit inspection_gate_ids")
        tokens = [x.strip() for x in str(task.get("predecessors", "") or "").split(",") if x.strip()]
        for gid in gates:
            gate_task = by_id.get(gid)
            if gate_task is None or not gate_task.get("inspection_gate") or tasks.index(gate_task) >= index:
                raise ValueError("Inspection gate must be an earlier explicit inspection task")
            tokens = [x for x in tokens if _token_id(x) != gid]
            tokens.append(f"{gid}FS")

        if activity:
            pairs: List[Tuple[str, str]] = []
            for entry in onto.get("link_policy") or []:
                if entry.get("successor") == activity:
                    pairs.append((entry["predecessor"], entry["condition"]))
            if mode != "legacy":
                for cls in (onto.get("auto_emit_classes") or []):
                    for cid in requires(onto, activity):
                        if condition_class(onto, cid) != cls:
                            continue
                        for cand in tasks[:index]:
                            if cid in produces(onto, cand.get("activity_type")) and _zone(cand) == _zone(task):
                                pairs.append((cand["activity_type"], cid))
            for pred_activity, _cid in dict.fromkeys(pairs):
                candidates = [p for p in tasks[:index]
                              if p.get("activity_type") == pred_activity and _zone(p) == _zone(task)]
                if not candidates:
                    skipped.append(dict(successor_id=task["id"], successor_activity=activity,
                                        predecessor_activity=pred_activity,
                                        reason="前序 activity_type 在当前网络中不存在或不在同一分区"))
                    continue
                pred = candidates[-1]
                resolved = resolve_dependency(
                    pred_activity, activity,
                    dict(workfronts=workfronts, sectional=sectional,
                         predecessor_duration_days=int(pred.get("duration_days", pred.get("duration", 0)) or 0),
                         gated=needs_gate),
                    onto,
                )
                if resolved["source"] == "none":
                    continue
                rel, lag = resolved["relationship"], resolved["lag"]
                tokens = [x for x in tokens if _token_id(x) != pred["id"]]
                tokens.append(f"{pred['id']}{rel}+{lag}")
                if rel == "FF":
                    tokens.append(f"{pred['id']}SS+0")
                row = dict(predecessor=pred["id"], successor=task["id"], relationship=rel, lag=lag,
                           predecessor_activity=pred_activity, successor_activity=activity,
                           dependency_class=resolved["dependency_class"], condition=resolved["condition"],
                           condition_scope=resolved["condition_scope"], lag_basis=resolved["lag_basis"],
                           reason=resolved["reason"], why_not_fs=resolved["why_not_fs"],
                           why_not_ss=resolved["why_not_ss"], confidence=resolved["confidence"],
                           confidence_reasons=resolved["confidence_reasons"], policy_id=resolved["policy_id"],
                           source=resolved["source"])
                applied.append(row)
                explanations.append(row)

        if mode in ("legacy", "auto") and not activity:
            pass  # keyword fallback is applied globally below for untyped tasks

        task["predecessors"] = ",".join(dict.fromkeys(tokens))

    if mode in ("legacy", "auto"):
        typed_pairs = {(a["predecessor"], a["successor"]) for a in applied}
        for link in legacy_keyword_links(tasks):
            key = (link["predecessor_id"], link["successor_id"])
            if key in typed_pairs:
                continue
            succ = by_id.get(link["successor_id"])
            if succ is None:
                continue
            rel = link["relationship"]
            spec = link.get("lag_days") or {}
            if rel == "SS":
                lag, lag_basis = compute_pipeline_lag(link.get("predecessor_duration_days", 0), workfronts,
                                                      int(spec.get("min", 0)), int(spec.get("max", 0)))
            else:
                lag, lag_basis = 0, "legacy_keyword_rule"
            tokens = [x for x in str(succ.get("predecessors", "") or "").split(",") if x.strip()]
            tokens.append(f"{link['predecessor_id']}{rel}+{lag}")
            succ["predecessors"] = ",".join(dict.fromkeys(tokens))
            row = dict(predecessor=link["predecessor_id"], successor=link["successor_id"],
                       relationship=rel, lag=lag,
                       dependency_class=None, condition=None, lag_basis=lag_basis,
                       reason="本体未建模该对，回退到遗留关键词规则（兼容路径，非生产首选）",
                       confidence="low", policy_id=link.get("rule_id"), source="legacy.keyword")
            applied.append(row)
            explanations.append(row)

    return dict(decision=decision, applied=applied, skipped=skipped,
                explanations=explanations,
                unbound_conditions=unbound_conditions(tasks, onto),
                protection=validate_gate_protection(tasks, onto))


# ---------------------------------------------------------------------------
# validation + contention
# ---------------------------------------------------------------------------
def unbound_conditions(tasks: Sequence[Dict[str, Any]], onto: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Conditions required by present activities with no producing task in the network.

    Reported, never silently dropped: statutory permits and approvals are normally
    carried by the WBS template as named milestone tasks rather than typed activities.
    """
    onto = onto or load_ontology()
    present = {t.get("activity_type") for t in tasks if t.get("activity_type")}
    produced = set()
    for act in present:
        produced.update(produces(onto, act))
    rows = []
    for task in tasks:
        act = task.get("activity_type")
        if not act:
            continue
        gates = set(task.get("inspection_gate_ids") or [])
        for cid in requires(onto, act):
            if cid in produced:
                continue
            meta = (onto.get("conditions") or {}).get(cid) or {}
            cls = meta.get("class")
            # An inspection condition is satisfied by the explicit gate task linked
            # through inspection_gate_ids, not by a typed producing activity.
            if cls == "inspection" and gates:
                continue
            note = ("网络内无 typed 生产者任务；由模板中的具名审批/许可节点承载，"
                    "需人工确认该节点确实存在且未被绕过" if cls in NON_OVERRIDABLE
                    else "网络内无生产者任务")
            rows.append(dict(activity_type=act, condition=cid, dependency_class=cls,
                             scope=meta.get("scope"), label_zh=meta.get("label_zh"), note=note))
    seen, out = set(), []
    for r in rows:
        key = (r["activity_type"], r["condition"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return sorted(out, key=lambda r: (r["dependency_class"] or "", r["activity_type"], r["condition"]))


def validate_gate_protection(tasks: Sequence[Dict[str, Any]], onto: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Inspection and authority links must exist and must be FS. Never dropped."""
    onto = onto or load_ontology()
    by_id = {t["id"]: t for t in tasks}
    issues: List[Dict[str, Any]] = []
    for task in tasks:
        for pid, rel, _lag in parse_predecessor(str(task.get("predecessors", "") or "")):
            pred = by_id.get(pid)
            if pred is None:
                continue
            classes = link_classes(onto, pred.get("activity_type"), task.get("activity_type"))
            blocking = [c for c in classes if c in NON_OVERRIDABLE]
            if not blocking and not pred.get("inspection_gate"):
                continue
            if rel != "FS":
                issues.append({"level": "error", "code": "GATE_NOT_FS", "task_id": task["id"],
                               "message": f"task {task['id']} ← {pid} 为 {rel}；"
                                          f"{'/'.join(blocking) or 'inspection_gate'} 类依赖必须为 FS，不得重叠"})
        before = task.get("predecessors_before_rules")
        if before is None:
            continue
        before_ids = {pid for pid, _s in split_predecessor_id_suffix(str(before))}
        after_ids = {pid for pid, _s in split_predecessor_id_suffix(str(task.get("predecessors", "") or ""))}
        for pid in sorted(before_ids - after_ids):
            pred = by_id.get(pid)
            if pred is None:
                continue
            classes = link_classes(onto, pred.get("activity_type"), task.get("activity_type"))
            if pred.get("inspection_gate") or any(c in NON_OVERRIDABLE for c in classes):
                issues.append({"level": "error", "code": "GATE_DROPPED", "task_id": task["id"],
                               "message": f"task {task['id']} 删除了检查/权威前置 {pid}；检查门与许可不得绕过"})
    return issues


def derive_resource_contention(tasks: Sequence[Dict[str, Any]], onto: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Resource-class conditions → contention groups. No network edge is emitted.

    Consumed by V6.2 (resource / crew optimisation); a resource dependency is a
    capacity conflict, not a sequencing rule.
    """
    onto = onto or load_ontology()
    groups: Dict[str, List[int]] = {}
    for task in tasks:
        if not task.get("activity_type"):
            continue
        for cid in resource_conditions(onto, task["activity_type"]):
            groups.setdefault(cid, []).append(task["id"])
    rows = []
    for cid, ids in groups.items():
        meta = (onto.get("conditions") or {}).get(cid) or {}
        rows.append(dict(condition=cid, label_zh=meta.get("label_zh"), scope=meta.get("scope"),
                         task_ids=sorted(set(ids)), emits_edge=False,
                         note="同一工作面/通道/工种的产能竞争，交由 V6.2 资源优化处理，不构成先后逻辑"))
    return sorted(rows, key=lambda r: r["condition"])


# ---------------------------------------------------------------------------
# explanation
# ---------------------------------------------------------------------------
def explain_link(row: Dict[str, Any]) -> str:
    """Human-readable audit line for one applied link."""
    head = (f"#{row['predecessor']} ({row.get('predecessor_activity')}) "
            f"→ #{row['successor']} ({row.get('successor_activity')})  "
            f"{row['relationship']}+{row['lag']}")
    body = [f"  条件   : {row.get('condition')} [{row.get('dependency_class')}/{row.get('condition_scope')}]"]
    body.append(f"  依据   : {row.get('reason')}")
    if row.get("lag_basis"):
        body.append(f"  滞后   : {row['lag']}d ← {row['lag_basis']}")
    if row.get("why_not_ss"):
        body.append(f"  为何非SS: {row['why_not_ss']}")
    if row.get("why_not_fs"):
        body.append(f"  为何非FS: {row['why_not_fs']}")
    body.append(f"  信心   : {row.get('confidence')}（来源 {row.get('source')}"
                + (f"，策略 {row.get('policy_id')}" if row.get("policy_id") else "") + ")")
    for r in row.get("confidence_reasons") or []:
        body.append(f"           · {r}")
    return head + "\n" + "\n".join(body)
