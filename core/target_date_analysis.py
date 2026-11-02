"""Target date verdict — three states, never a boolean (V6.4, GOV-16).

A boolean "can we hit the date?" is the single most dangerous output a scheduler can
produce, because it converts an unconfirmed resource assumption into a commitment.

This module answers with one of three verdicts, derived from the V6.3 scenario window:

    MET          目标日期不早于最保守情景的完成日期
                 → 当前证据下的所有情景都能达成。仍是"以未确认资源为前提"，不等同承诺。

    NOT_MET      目标日期早于最激进情景的完成日期
                 → 硬结论。即使资源全部确认到位也排不进；必须改范围或改交付模式。

    UNVERIFIABLE 落在两者之间
                 → 不是"大概可以"。要判定必须先确认班组池 / 班次 / 工作面容量 / 拥挤曲线。

The verdict is produced *and* gated: it never comes with a recommended scenario.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional, Sequence

from core import recommendation_gate as GATE
from core.recommendation_gate import load_production_candidate_config
from core import resource_governance as RG

MET = "MET"
NOT_MET = "NOT_MET"
UNVERIFIABLE = "UNVERIFIABLE"


def _d(value: Any) -> Optional[_dt.date]:
    if value is None:
        return None
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _delta_days(later: Optional[_dt.date], earlier: Optional[_dt.date]) -> Optional[int]:
    if not later or not earlier:
        return None
    return (later - earlier).days


def attainable_window(scenario_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Earliest / latest finish across usable scenarios. Not a forecast — a bracket."""
    usable = [r for r in scenario_rows
              if r.get("duration_days") is not None and r.get("finish")]
    if not usable:
        return dict(available=False, earliest=None, latest=None,
                    note="没有可用情景（全部 INSUFFICIENT_DATA），无法给出可达区间。")
    by_dur = sorted(usable, key=lambda r: r["duration_days"])
    fastest, slowest = by_dur[0], by_dur[-1]
    return dict(
        available=True,
        earliest=dict(scenario_id=fastest.get("id"), label=fastest.get("label"),
                      finish=fastest.get("finish"), duration_days=fastest.get("duration_days"),
                      evidence_state=fastest.get("evidence_state")),
        latest=dict(scenario_id=slowest.get("id"), label=slowest.get("label"),
                    finish=slowest.get("finish"), duration_days=slowest.get("duration_days"),
                    evidence_state=slowest.get("evidence_state")),
        spread_days=(slowest["duration_days"] - fastest["duration_days"]),
        note=("区间宽度 = 资源假设未确认造成的工期不确定度；确认班组池后区间才会收窄。"),
    )


def analyse(target_finish_date: str,
            scenario_rows: Sequence[Dict[str, Any]],
            cfg: Optional[Dict[str, Any]] = None,
            resource_cfg: Optional[Dict[str, Any]] = None,
            required_confirmations: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Three-state verdict for a target completion date."""
    pcfg = cfg or load_production_candidate_config()
    tcfg = pcfg.get("target_date") or {}
    target = _d(target_finish_date)
    if target is None:
        raise ValueError(f"target_finish_date 无法解析：{target_finish_date!r}")

    window = attainable_window(scenario_rows)
    readiness = RG.resource_readiness(resource_cfg)

    if not window["available"]:
        verdict, reason = UNVERIFIABLE, "没有可用情景，无法判定目标日期。"
    else:
        earliest = _d(window["earliest"]["finish"])
        latest = _d(window["latest"]["finish"])
        if target >= latest:
            verdict = MET
            reason = (f"目标日期 {target} 不早于最保守情景 {window['latest']['scenario_id']} "
                      f"的完成日期 {window['latest']['finish']}：当前证据下所有情景都能达成。")
        elif target < earliest:
            verdict = NOT_MET
            reason = (f"目标日期 {target} 早于最激进情景 {window['earliest']['scenario_id']} "
                      f"的完成日期 {window['earliest']['finish']}：即使资源全部确认到位也排不进。")
        else:
            verdict = UNVERIFIABLE
            reason = (f"目标日期 {target} 落在最激进情景 {window['earliest']['scenario_id']}"
                      f"（{window['earliest']['finish']}）与最保守情景 "
                      f"{window['latest']['scenario_id']}（{window['latest']['finish']}）之间："
                      f"能否达成取决于未确认的资源输入。")

    usable = [dict(r, id=r.get("id")) for r in scenario_rows
              if r.get("duration_days") is not None and _d(r.get("finish"))]
    if verdict == NOT_MET:
        candidates = []          # 没有任何情景能达成：候选集为空是正确输出
    elif verdict == UNVERIFIABLE and window["available"]:
        earliest = _d(window["earliest"]["finish"])
        candidates = [c for c in usable if earliest <= _d(c["finish"]) <= target]
    else:
        candidates = usable      # MET：全部情景并列

    decision = GATE.evaluate(
        candidates, resource_cfg=resource_cfg,
        required_confirmations=required_confirmations,
        context="target_date",
        candidate_note=("达成目标日期的并列候选情景；引擎不挑选，也不因『刚好赶上』而推荐。"),
    )
    GATE.assert_no_recommendation(decision)

    meta = (tcfg.get("verdicts") or {}).get(verdict) or {}
    return dict(
        target_date=target.isoformat(),
        verdict=verdict,
        verdict_zh=meta.get("label_zh"),
        verdict_rule=meta.get("rule"),
        verdict_strength=meta.get("strength"),
        reason=reason,
        attainable_window=window,
        gap_days=dict(
            vs_earliest=(_delta_days(_d(window["earliest"]["finish"]), target)
                         if window["available"] else None),
            vs_latest=(_delta_days(_d(window["latest"]["finish"]), target)
                       if window["available"] else None),
            note="正值 = 目标日期早于该情景（缺口）；负值 = 目标日期有富余。"),
        readiness=dict(overall=readiness.get("overall"),
                       unconfirmed_sections=readiness.get("unconfirmed_sections")),
        statement=readiness.get("scenario_statement"),
        decision=decision,
        recommended=None,
        structural_options_if_not_met=(
            ["缩小装修范围 / 分期交付",
             "改交付模式（如分阶段搬迁、先交付部分楼层）",
             "提前启动招采与设计（压缩施工前段，而非现场段）"]
            if verdict == NOT_MET else []),
        must_not_be_read_as=("MET 不是承诺：它仍以未确认的资源输入为前提。"
                             "UNVERIFIABLE 不是『大概可以』：它是『要判定必须先确认』。"),
    )


def explain(result: Dict[str, Any]) -> List[str]:
    lines = [f"目标日期 {result['target_date']} → {result['verdict']}"
             f"（{result.get('verdict_zh')}）",
             f"  {result['reason']}"]
    w = result["attainable_window"]
    if w.get("available"):
        lines.append(f"  可达区间：{w['earliest']['finish']}（{w['earliest']['scenario_id']}，"
                     f"{w['earliest']['duration_days']}d） … "
                     f"{w['latest']['finish']}（{w['latest']['scenario_id']}，"
                     f"{w['latest']['duration_days']}d），区间宽 {w['spread_days']} 天")
    lines.append(f"  {result['must_not_be_read_as']}")
    for opt in result["structural_options_if_not_met"]:
        lines.append(f"  结构性选项：{opt}")
    return lines
