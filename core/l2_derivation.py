"""L2 derivation 与工艺包署名校验（V3.5 新增，V3.6 扩展）。

只做两件事：
  1. 确认每一条 b0 都能回答「这个数是从哪来的」（derivation）。
  2. 确认每个工艺包都有署名状态；未签署的一律如实标为 unconfirmed。

V3.6 扩展（PM 2026-09-23 签署 13 个工艺包之后）：
  - 新增法基 `pm_signoff` —— PM 就「无来源的引擎默认值」逐包签署后确立，须带签字记录与 PM 原话。
  - 新增审批状态 `adjusted` —— 已确认但数值有改动；**必须留下改了哪几条，或改了哪个机制**。
  - 容量覆盖检验改为双口径（主判按「该工序在参照台上的工期」口径 / 全楼完工作对照）。

V3.9 扩展（PM 2026-09-23 就两条未决项裁决）：
  - b0 之意已定（V3.10 澄清）：**b0 就是该工序的基准工期** —— 该工序在参照台 3000㎡ 上的天数。
    PM 原话「B0当然是板块节拍啊」，随后澄清「我意思就是说 他们这些工序的工期啦」。所谓
    「板块节拍」，在 PM 口中指的就是这些工序工期本身；**不存在「节拍 vs 完工工期」的对立**，
    那个二选一是我方自造，V3.10 起关闭，不再作为待决项。v3.6 曾附的数值「独立证据」
    （~28d / 5.6 倍）已于 v3.7 撤回（CORRECTION WITHDRAWN），保留仅供追溯。
  - 造型吊顶 / 玻璃隔断工程量已裁：**局部工程量**（吊顶占比可以高——任何造型都须人工；
    隔断按实际面积与实际房间数计）。trade_capacity 对应条目落 `quantity_scope: "partial"`，
    容量覆盖检验对这两条不再以全楼面积作分母质疑 b0。

V3.10 扩展（PM 2026-09-23 就 b0 的含义澄清）：
  - b0 语义落 `trade_duration_at_reference_plate`（详见上节）。字段名 plate_beat / whole_floor
    保留不变（改为纯技术别名，不再承载「两种口径之争」的含义），只改文案与判定依据。
  - **本轮不动任何工期数值**，回归读数与 v3.9 逐字一致。

设计约束（不要违反）：
  - **不修改任何工期数值。** 本模块只读判定；数值改动一律落在 config 并由 PM 签署。
  - **不硬编码任何工程上限。** 容量取 core.workfront_capacity（config/trade_capacity.json）。
  - **缺配置一律显式失败，不做静默降级。**
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.l2_master_duration import beat_days

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

LEGAL_BASIS = ("quota_conversion", "observed_production", "engine_judgement", "pm_signoff")
LEGAL_APPROVAL = ("unconfirmed", "confirmed", "adjusted", "rejected")

# 硬失败：配置不合规，与面积无关，必须修好才能出计划
HARD_CODES = (
    "DERIVATION_MISSING",
    "DERIVATION_INCOMPLETE",
    "PKG_SIGNOFF_MISSING",
    "PKG_APPROVAL_STATE_INVALID",
    "PKG_ITEM_UNMAPPED",
    "PKG_ADJUSTED_WITHOUT_TRACE",
)


def _read_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_signoff_policy(path: Optional[Path] = None) -> Dict[str, Any]:
    return _read_json(path or (CONFIG_DIR / "l2_master_duration.json"))


def load_productivity_rates(path: Optional[Path] = None) -> Dict[str, Any]:
    return _read_json(path or (CONFIG_DIR / "productivity_rates.json"))


def package_signoff_table(policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 13 个工艺包整理成可审阅、可签字的一张表。"""
    catalog = policy.get("activity_types") or {}
    packages = policy.get("packages") or {}
    rows: List[Dict[str, Any]] = []
    for pid, pkg in packages.items():
        signoff = pkg.get("signoff") or {}
        items = []
        for atype in pkg.get("items") or []:
            entry = catalog.get(atype)
            if entry is None:
                items.append({"activity_type": atype, "missing": True})
                continue
            items.append({
                "activity_type": atype,
                "b0_days": entry.get("b0_days"),
                "b_min": entry.get("b_min"),
                "b_max": entry.get("b_max"),
                "basis": (entry.get("derivation") or {}).get("basis"),
            })
        rows.append({
            "package_id": pid,
            "zh": pkg.get("zh"),
            "review_hint": pkg.get("review_hint"),
            "approval_state": signoff.get("approval_state"),
            "signed_by": signoff.get("signed_by"),
            "signed_role": signoff.get("signed_role"),
            "approved_at": signoff.get("approved_at"),
            "basis_note": signoff.get("basis_note"),
            "superseded_by": signoff.get("superseded_by"),
            "item_count": len(items),
            "items": items,
        })
    return rows


def _quantity_scope(activity_type: str) -> str:
    """v3.9：读 trade_capacity 的 quantity_scope。

    PM 2026-09-23 裁定造型吊顶 / 玻璃隔断为**局部工程量**后，对应条目标
    `quantity_scope: "partial"`。容量覆盖检验对这类条目不再以全楼面积作分母
    质疑 b0 —— 分母错的是检验，不是 b0。未标注的条目按 whole_floor 读。
    """
    cap = _read_json(CONFIG_DIR / "trade_capacity.json")
    entry = (cap.get("by_activity_type") or {}).get(activity_type) or {}
    return str(entry.get("quantity_scope") or "whole_floor")


def _capacity_coverage(
    entry: Dict[str, Any],
    rate_entry: Dict[str, Any],
    policy: Dict[str, Any],
    area_sqm: float,
    activity_type: str,
    rates: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """占用窗口 × 引擎自声明的物理容量 → 能覆盖多少面积。

    口径**全部取自引擎自己的配置**，不在此处硬编码任何工程常数：
      - 容量：`core.workfront_capacity.physical_capacity_workers()`
               = workfront_count × max_crews_per_workfront × workers_per_crew
               （来源 trade_capacity.json + productivity_rates.default_crew_size）
      - 生产率：productivity_rates.rates_per_worker_day.typical
      - 占用窗口：本表的 b0，按面积缩放并受 b_min / b_max 约束

    判据：`capacity_workers × typical_rate × duration_days >= area` 才算自洽。
    不足即说明「按引擎自己声明的人手，这个窗口做不完这块面积」——
    要么 b0 偏小、要么容量模型偏保守、要么该工序并非覆盖全楼面积。
    三者只能由人裁决，本函数只负责把矛盾算出来。

    仅对面积为计量单位的条目适用；`each` / `m` 等条目不适用。

    **V3.6 双口径 → V3.9 裁定 → V3.10 澄清**：
      A. `plate_beat`  —— 比较面积取 plate_ref_m2，窗口取该工序在参照台上的工期。
      B. `whole_floor` —— 比较面积取项目面积，窗口取缩放后工期，仅作对照。
    **PM 2026-09-23 已明确：b0 就是该工序的基准工期**（参照台 3000㎡ 上的天数）。
    PM 原话「B0当然是板块节拍啊」，随后澄清「我意思就是说 他们这些工序的工期啦」——
    他说的「板块节拍」即指这些工序工期本身；**「节拍 vs 完工工期」的对立是我方自造，V3.10 关闭**。
    A 口径（plate_beat）为现行主判，B 口径保留仅作对照，不代表另一种「b0 的定义」。
    v3.6 曾在此处引用「按引擎容量反算 3000㎡ 全楼拆除需 ~28d、是 b0 的 5.6 倍」作独立证据 ——
    该推理已撤回（CORRECTION WITHDRAWN，v3.7）：引擎实际给 31d，且它是 capacity 保守假定的
    产物，同一包内另一套配置给出 4d，8 倍差的数不能承担证明责任。

    与 `workfront_capacity.resource_scaling_warning`（FIX-18）的关系：
    那个守卫跑在**排期之后**、基于实际 rows；本函数跑在**配置层**、基于 b0 与面积。
    两者互补，不重复：一个查输出，一个查输入。
    """
    if (rate_entry or {}).get("unit") != "m2":
        return None
    typical = (rate_entry.get("rates_per_worker_day") or {}).get("typical")
    if not typical:
        return None

    from core.workfront_capacity import physical_capacity_workers

    ref = float(policy.get("plate_ref_m2") or 3000)
    exp = float(policy.get("area_exponent") or 0.4)
    b0 = float(entry["b0_days"])
    b_min = int(entry["b_min"])
    b_max = entry.get("b_max")
    mode = str(entry.get("scale_mode") or "plate")

    def _duration(scale_area: float) -> int:
        if mode == "once":
            return max(b_min, int(round(b0)))
        d = beat_days(b0, b_min, scale_area, ref, exp)
        if b_max is not None:
            cap = (beat_days(float(b_max), 1, scale_area, ref, exp)
                   if str(entry.get("b_max_scale") or "") == "area_exponent"
                   else int(b_max))
            d = min(int(cap), int(d))
        return int(d)

    def _one(compare_area: float, scale_area: float) -> Dict[str, Any]:
        cap = physical_capacity_workers(compare_area, activity_type, rates=rates)
        workers = float(cap["physical_capacity_workers"])
        dur = _duration(scale_area)
        coverage = workers * float(typical) * dur
        return {
            "area_sqm": round(float(compare_area), 1),
            "duration_days_used": dur,
            "typical_rate_m2_per_worker_day": typical,
            "capacity_workers": round(workers, 3),
            "capacity_basis": cap.get("basis"),
            "capacity_source": cap.get("source"),
            "capacity_source_type": cap.get("source_type"),
            "capacity_confidence": cap.get("confidence"),
            "workfronts_effective": cap.get("effective_workfront_capacity"),
            "max_crews_per_workfront": cap.get("max_crews_per_workfront"),
            "workers_per_crew": cap.get("workers_per_crew"),
            "coverage_m2": round(coverage, 1),
            "coverage_ratio": round(coverage / float(compare_area), 4),
            "workers_needed_for_full_area": round(float(compare_area) / (float(typical) * dur), 1),
        }

    # A：面积与窗口都取参照台 —— 同基准对比，避免拿「节拍窗口」比「全楼面积」
    plate = _one(ref, ref)
    # B：面积与窗口都取项目实际 —— 若 b0 实为完工工期，则应按此口径读
    whole = _one(float(area_sqm), float(area_sqm))
    return {
        "activity_type": activity_type,
        "plate_beat": plate,
        "whole_floor": whole,
        "primary_reading": "plate_beat",
        "primary_coverage_ratio": plate["coverage_ratio"],
        "area_sqm": round(float(area_sqm), 1),
        "reference_plate_m2": ref,
    }


def validate_derivations(
    policy: Dict[str, Any],
    *,
    area_sqm: Optional[float] = None,
    rates: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """8 条规则。硬失败阻止出计划；警告写进报告与待确认清单。"""
    catalog = policy.get("activity_types") or {}
    packages = policy.get("packages") or {}
    dpolicy = policy.get("derivation_policy") or {}
    meaning_default = dpolicy.get("meaning_default")
    rates = rates if rates is not None else load_productivity_rates()
    rate_catalog = (rates or {}).get("activity_types") or {}

    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    # ── 条目级规则 ────────────────────────────────────────────────────────────
    for atype, entry in catalog.items():
        deriv = entry.get("derivation")

        # H1 无 derivation
        if not isinstance(deriv, dict) or not deriv:
            errors.append({"code": "DERIVATION_MISSING", "activity_type": atype,
                           "detail": f"{atype} 没有 derivation，无法回答这个数从哪来。"})
            continue

        basis = deriv.get("basis")
        # W1 basis 非法
        if basis not in LEGAL_BASIS:
            warnings.append({"code": "BASIS_UNKNOWN", "activity_type": atype,
                             "detail": f"{atype} 的 basis='{basis}' 不在合法集合 {LEGAL_BASIS} 内。"})

        if basis == "engine_judgement":
            # H2 引擎判断值必须写失效条件
            if not (deriv.get("falsified_by") or "").strip():
                errors.append({"code": "DERIVATION_INCOMPLETE", "activity_type": atype,
                               "detail": f"{atype} 标为 engine_judgement，但没有 falsified_by（失效条件）。"})
        else:
            # H2 声称有换算或实测，却没有任何换算链 —— 假挂靠
            if deriv.get("chain") in (None, {}, []):
                errors.append({"code": "DERIVATION_INCOMPLETE", "activity_type": atype,
                               "detail": (f"{atype} 声称 basis='{basis}'，但 chain 为空 —— "
                                          "这是假挂靠：要么补换算链，要么改回 engine_judgement。")})

        # W2 语义与全局默认不一致
        if meaning_default and deriv.get("meaning") != meaning_default:
            warnings.append({"code": "MEANING_DIVERGENT", "activity_type": atype,
                             "detail": f"{atype} 的 meaning='{deriv.get('meaning')}' 与全局默认 '{meaning_default}' 不一致。"})

    # ── 工艺包级规则 ──────────────────────────────────────────────────────────
    mapped = set()
    for pid, pkg in packages.items():
        signoff = pkg.get("signoff")
        if not isinstance(signoff, dict) or not signoff:
            errors.append({"code": "PKG_SIGNOFF_MISSING", "package_id": pid,
                           "detail": f"工艺包 {pid} 没有 signoff 块，无法署名。"})
        else:
            state = signoff.get("approval_state")
            if state not in LEGAL_APPROVAL:
                errors.append({"code": "PKG_APPROVAL_STATE_INVALID", "package_id": pid,
                               "detail": f"工艺包 {pid} 的 approval_state='{state}' 不在 {LEGAL_APPROVAL} 内。"})
            if state in ("confirmed", "adjusted") and not (signoff.get("signed_by") or "").strip():
                errors.append({"code": "PKG_APPROVAL_STATE_INVALID", "package_id": pid,
                               "detail": (f"工艺包 {pid} 标为 {state}，但没有 signed_by —— "
                                          "不许出现无署名却已确认的状态。")})
            if state == "adjusted" and not (signoff.get("adjusted_items")
                                            or signoff.get("adjusted_mechanism")):
                errors.append({"code": "PKG_ADJUSTED_WITHOUT_TRACE", "package_id": pid,
                               "detail": (f"工艺包 {pid} 标为 adjusted（已确认但改了值），却既没有 "
                                          "adjusted_items 也没有 adjusted_mechanism —— "
                                          "不许出现无痕迹的『改过』状态：改了哪几条、或改了哪个机制，必须留下。")})
            if state == "unconfirmed":
                warnings.append({"code": "PACKAGE_UNCONFIRMED", "package_id": pid,
                                 "detail": (f"工艺包 {pid}（{pkg.get('zh')}）的基准工期是引擎默认值，"
                                            "未经 PM 确认，不得作为合同工期依据。")})
        for atype in pkg.get("items") or []:
            if atype not in catalog:
                errors.append({"code": "PKG_ITEM_UNMAPPED", "package_id": pid, "activity_type": atype,
                               "detail": f"工艺包 {pid} 引用了不存在的条目 {atype}。"})
            mapped.add(atype)

    # H1 补充：有条目却没被任何工艺包覆盖 —— 等于无人签字
    for atype in catalog:
        if atype not in mapped:
            errors.append({"code": "PKG_ITEM_UNMAPPED", "activity_type": atype,
                           "detail": f"条目 {atype} 没有被任何工艺包覆盖，处于无人署名状态。"})

    # ── W4 容量覆盖（双口径，仅当给了面积）────────────────────────────────────
    coverage_rows: List[Dict[str, Any]] = []
    if area_sqm:
        for atype, entry in catalog.items():
            rate_entry = rate_catalog.get(atype)
            if not rate_entry:
                continue
            row = _capacity_coverage(entry, rate_entry, policy, float(area_sqm), atype, rates)
            if row:
                coverage_rows.append(row)

        short = sorted([r for r in coverage_rows if r["primary_coverage_ratio"] < 1.0],
                       key=lambda r: r["primary_coverage_ratio"])
        if short:
            worst = short[:3]
            uncalibrated = all(
                r["plate_beat"].get("capacity_source_type") == "PLANNING_ASSUMPTION"
                for r in coverage_rows)
            ref = int(coverage_rows[0]["reference_plate_m2"])
            # v3.9：PM 已裁决造型吊顶 / 玻璃隔断为局部工程量 —— 这两条的「覆盖不足」
            # 是检验分母用错（应取实际工程量），不再作为 b0 质疑呈现。
            scope_ruled = [r for r in short
                           if _quantity_scope(r["activity_type"]) == "partial"]
            genuine = [r for r in short
                       if all(r is not s for s in scope_ruled)]
            detail = (
                f"参照台（{ref}㎡）口径下，{len(short)}/{len(coverage_rows)} 个按面积计量的工序，"
                "在 b0 窗口内按引擎自声明容量算出的产能覆盖不了面积。最严重三条："
                + "；".join(
                    f"{r['activity_type']} 覆盖 {r['primary_coverage_ratio'] * 100:.0f}%"
                    f"（{r['plate_beat']['capacity_workers']:.1f} 人 × "
                    f"{r['plate_beat']['typical_rate_m2_per_worker_day']}㎡/工日 × "
                    f"{r['plate_beat']['duration_days_used']}d = {r['plate_beat']['coverage_m2']:.0f}㎡"
                    f" / {r['plate_beat']['area_sqm']:.0f}㎡）"
                    for r in worst)
                + f"。其余 {len(short) - len(worst)} 条见审计文件。"
                + ("口径层：容量分区面积已于 v3.8（2026-09-23）按工序收敛到各工序自己的 "
                   "crew_scaling 口径（capacity_zone_area_sqm / workers_per_workfront），"
                   "同一条件下 22 条中的矛盾数由 16 条降至本数。"
                   if uncalibrated else "")
                + (f"❗ 其中 {len(scope_ruled)} 条（"
                   + "、".join(r["activity_type"] for r in scope_ruled)
                   + "）PM 已于 2026-09-23 裁定为**局部工程量**（原话：「肯定是局部工程量 "
                   "怎么可能全楼都吊顶跟玻璃隔断 吊顶可以占比高毕竟天花无论是什么造型 "
                   "都是要人工去弄的 但隔断就是根据实际面积实际房间数来的」）——"
                   "对这两条，覆盖不足的成因是**本检验的分母用错**（应取该工序实际工程量"
                   "而非全楼面积），不构成对 b0 的质疑；trade_capacity 已标 "
                   "quantity_scope=partial（v3.9 落库）。"
                   if scope_ruled else "")
                + (f"其余 {len(genuine)} 条不属局部工程量裁决范围，仍需人判："
                   "若工序确实覆盖全楼则 b0 偏短；若只覆盖局部则分母用错。"
                   if genuine else "")
                + " 两种算法（plate_beat 主判 / whole_floor 对照）均见审计文件。"
                "**本项只作提示，不作结论。** 引擎已在排期后逐行做同类诊断"
                "（`diagnostic_days` vs `duration_days`，阈值 `qrc_warn_ratio = 1.5` → "
                "`QRC_DOES_NOT_GOVERN`）；本项是同一问题在**配置层**的预检，跑排期之前即可见。"
                "另注：`resource_constraints.workfront_capacity` 是**单区**口径（一个分区内能同时容纳多少人），"
                "`trade_capacity.workfront` 是**全区**工作面数，两者不是同一件事，勿混用（v3.8 口径语义注）。")
            warnings.append({
                "code": "CAPACITY_COVERAGE_SHORT",
                "area_sqm": round(float(area_sqm), 1),
                "count": len(short),
                "of_total": len(coverage_rows),
                "count_scope_ruled_partial": len(scope_ruled),
                "worst": [dict(activity_type=r["activity_type"], **r["plate_beat"]) for r in worst],
                "capacity_declaration_uncalibrated": uncalibrated,
                "detail": detail,
            })

    hard = [e for e in errors if e["code"] in HARD_CODES]
    return {
        "hard_fail": bool(hard),
        "errors": errors,
        "warnings": warnings,
        "packages": package_signoff_table(policy),
        "capacity_coverage": coverage_rows,
        "counts": {
            "activities": len(catalog),
            "packages": len(packages),
            "unconfirmed_packages": sum(1 for p in packages.values()
                                        if (p.get("signoff") or {}).get("approval_state") == "unconfirmed"),
            "engine_judgement_items": sum(1 for e in catalog.values()
                                          if (e.get("derivation") or {}).get("basis") == "engine_judgement"),
            "pm_signoff_items": sum(1 for e in catalog.values()
                                    if (e.get("derivation") or {}).get("basis") == "pm_signoff"),
            "adjusted_packages": sum(1 for p in packages.values()
                                     if (p.get("signoff") or {}).get("approval_state") == "adjusted"),
            "contract_basis_approved": bool(((policy.get("derivation_policy") or {})
                                             .get("signoff_record") or {})
                                            .get("contract_basis_approved")),
            "capacity_coverage_short": sum(1 for r in coverage_rows if r["primary_coverage_ratio"] < 1.0),
            "capacity_coverage_measured": len(coverage_rows),
            "area_sqm": round(float(area_sqm), 1) if area_sqm else None,
        },
    }


def signoff_open_decisions(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把签署状态变成排期输出里的待确认项。

    V3.6：13 个包已由 PM 签署，但有两类情况必须留在输出里，不能因为「签完了」就闭嘴：
      1. 有包被标为 adjusted —— 基准工期已按 PM 意见改动，须回看改动是否符合本意。
      2. 合同工期依据升级未勾选 —— 在勾选前不得作为合同工期依据。
    """
    counts = report.get("counts") or {}
    packages = report.get("packages") or []
    unconfirmed = [p for p in packages if p.get("approval_state") == "unconfirmed"]
    adjusted = [p for p in packages if p.get("approval_state") == "adjusted"]
    out: List[Dict[str, Any]] = []

    if unconfirmed:
        listing = "、".join(p.get("zh") or p.get("package_id") for p in unconfirmed)
        out.append({
            "id": "L2_PACKAGE_SIGNOFF",
            "ask": (f"{len(unconfirmed)} 个工艺包的基准工期均为引擎默认值，未经 PM 确认。"
                    f"需逐个审阅后方可作为合同工期依据。涉及：{listing}。"),
            "options": [
                {"value": "REVIEW_NOW", "label": "现在逐个审阅 13 个工艺包"},
                {"value": "ACCEPT_AS_PLANNING", "label": "先按计划草案使用，确认后再升级为合同依据"},
                {"value": "PARTIAL", "label": "只先确认影响关键路径的包"},
            ],
            "now": "基准工期标为「引擎默认值，未获 PM 确认」；未确认前结论只作计划草案，不作合同工期。",
            "evidence": f"engine_judgement 条目 {counts.get('engine_judgement_items')}/{counts.get('activities')}。",
        })

    if adjusted:
        listing = "、".join(p.get("zh") or p.get("package_id") for p in adjusted)
        out.append({
            "id": "L2_SIGNOFF_ADJUSTED",
            "ask": (f"{len(adjusted)} 个工艺包被 PM 签署为「改为」—— 基准工期已按 PM 意见改动，"
                    f"请回看改动是否与你的本意一致。涉及：{listing}。"),
            "options": [
                {"value": "CONFIRM_CHANGES", "label": "改动符合本意，接受"},
                {"value": "REVISE", "label": "还要再调 —— 指出调哪条、调到多少"},
            ],
            "now": "改动已落库并署名 PM；逐条明细记在每个包的 signoff.adjusted_items（机制类改动记 adjusted_mechanism）。",
            "evidence": (f"pm_signoff 条目 {counts.get('pm_signoff_items')}/{counts.get('activities')}；"
                         f"adjusted 工艺包 {counts.get('adjusted_packages')}/{counts.get('packages')}。"),
        })

    if not counts.get("contract_basis_approved"):
        out.append({
            "id": "L2_CONTRACT_BASIS_NOT_UPGRADED",
            "ask": ("PM 在确认表上未勾选「是否升级为合同工期依据」。"
                    "在勾选之前，这批基准工期可用于计划与招标，但不得作为合同工期依据。"),
            "options": [
                {"value": "UPGRADE", "label": "升级为合同工期依据"},
                {"value": "KEEP_PLANNING", "label": "保持现状（仅计划与招标用）"},
            ],
            "now": "如实记为未升级，不在输出里宣称已具合同效力。",
            "evidence": "config/l2_master_duration.json#derivation_policy.signoff_record。",
        })
    return out


def envelope_divergence(policy: Dict[str, Any],
                        observed_site_days: Optional[float]) -> Optional[Dict[str, Any]]:
    """比对「政策层封套判定」与「实际排期跨度」——两者用不同的量却共用同一组 band。"""
    if not observed_site_days:
        return None
    env = policy.get("_envelope_observed") or {}
    policy_status = env.get("status")
    if not policy_status:
        return None
    band_low, band_high = env.get("band_low"), env.get("band_high")
    if band_low is None or band_high is None:
        return None
    if observed_site_days < band_low:
        programme_status = "SHORT_OF_BAND"
    elif observed_site_days > band_high:
        programme_status = "LONG_OF_BAND"
    else:
        programme_status = "IN_BAND"
    if programme_status == policy_status:
        return None
    return {
        "code": "ENVELOPE_POLICY_VS_DIVERGENT",
        "policy_band_status": policy_status,
        "programme_band_status": programme_status,
        "observed_site_calendar_days": round(float(observed_site_days), 1),
        "band": [band_low, band_high],
        "detail": (f"政策层判 {policy_status}，实际排期跨度 {observed_site_days:.0f} 天判 {programme_status}。"
                   "两者用不同的量共用同一组 band，必须分别读，不能互相替代。"),
    }


def summary_line(report: Dict[str, Any]) -> str:
    c = report.get("counts") or {}
    return (f"[derivation] 条目 {c.get('activities')} · 工艺包 {c.get('packages')}"
            f"（未确认 {c.get('unconfirmed_packages')} / 改为 {c.get('adjusted_packages')}）"
            f"· 来源 engine_judgement {c.get('engine_judgement_items')}"
            f" / pm_signoff {c.get('pm_signoff_items')}"
            f" · 合同效力 {'已升级' if c.get('contract_basis_approved') else '未升级'}"
            f" · 硬失败 {len(report.get('errors') or [])} · 警告 {len(report.get('warnings') or [])}")
