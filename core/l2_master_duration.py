# -*- coding: utf-8 -*-
"""L2 Master Schedule occupancy windows.

Physical trade bars on the client/PM programme scale with **project area only**.
Floors, zones, freight lifts and sector do not change construction durations.
Quantity ÷ (rate × crew) stays diagnostic here. Furniture installation may be
overridden downstream by explicit project quantity and logistics-constrained crew
capacity (core.furniture_installation); absent evidence it remains provisional.

Scaling law:
    D = max(b_min, ceil(b0 × (area_sqm / 3000) ** 0.4))
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "config", "l2_master_duration.json")

AUTHORITY = "l2_master_beat"
_CFG: Optional[Dict[str, Any]] = None

GOV_DURATION_KWS = (
    "施工许可证", "图审合格", "消防设计专篇", "消防设计审查", "消防验收备案",
    "消防验收", "竣工备案", "联合竣工", "联合验收通过",
)
TIMED_TEST_KWS = (
    "72小时", "假负载", "Load Bank", "本底噪声", "RT60", "NC≤",
    "打压", "1.4 MPa", "满水",
)
SITE_PHASE_KWS = ("实体施工", "Phase 6")
NOT_AREA_SCALE_KWS = (
    "散味", "flushout", "空气净化",
    "室内空气质量", "IAQ",
    "搬迁入驻", "正式开业",
    "方案设计", "扩初设计", "施工图深化", "招标文件", "回标", "澄清答疑",
    "租前技术尽职", "TDD",
    "周末全量割接", "IT 迁移方案", "试迁移",
    "FM 运维", "物业接管",
)


def _is_not_area_scaled(task: Dict[str, Any]) -> bool:
    """Design / tender / IAQ flushout / move-in / weekend cutover keep clock or policy days."""
    name = str(task.get("name") or "")
    # A room-local allowance must not scale with the entire office area.
    # It remains provisional until room quantities/resources are supplied.
    if (task.get('duration_scope') == 'single_room_allowance'
            and task.get('acoustic_finish_role') in {'check', 'install', 'clean'}
            and task.get('duration_basis')):
        return True
    if task.get('activity_type')=='receipt_inspection' or task.get('module_id')=='office_ff_move' and task.get('module_step')==0:
        return True
    if any(kw in name for kw in NOT_AREA_SCALE_KWS):
        return True
    if str(task.get("calendar_window") or "") == "weekend_only":
        return True
    atype = str(task.get("activity_type") or "")
    if atype in ("flushout", "iaq_sample", "move_in", "tender", "design"):
        return True
    return False


def _is_gov_duration_lock(task: Dict[str, Any]) -> bool:
    name = str(task.get("name") or "")
    return any(kw in name for kw in GOV_DURATION_KWS)


def _is_timed_site_test(task: Dict[str, Any]) -> bool:
    name = str(task.get("name") or "")
    return any(kw in name for kw in TIMED_TEST_KWS)


def _is_construction_phase_task(task: Dict[str, Any]) -> bool:
    if task.get("milestone") or int(task.get("duration_days") or task.get("duration") or 0) <= 0:
        return False
    if _is_gov_duration_lock(task) or _is_timed_site_test(task) or _is_not_area_scaled(task):
        return False
    phase = str(task.get("phase") or "")
    if any(kw in phase for kw in SITE_PHASE_KWS):
        return True
    # Calendar is not a business-class signal. Weekend/7D occupancy (flushout)
    # must not be treated as a construction package.
    return False


def _scale_orphan_construction(task: Dict[str, Any], *, area_sqm: float,
                               policy: Dict[str, Any]) -> Dict[str, Any]:
    """Treat the template duration as b0 at plate_ref, then area-scale."""
    b0 = max(1, int(round(float(task.get("duration_days") or task.get("duration") or 0))))
    b_min = 2
    occ = occupancy_days(
        {"b0_days": b0, "b_min": b_min, "scale_mode": "plate",
         "role": "SLICE", "package_id": "sector_site", "overlap_group": "sector_site"},
        area_sqm=area_sqm, policy=policy,
    )
    diagnostic = b0
    duration = int(occ["duration_days"])
    task["duration_days"] = duration
    task["duration"] = duration
    task["duration_authority"] = AUTHORITY
    task["diagnostic_days"] = diagnostic
    occ.update(diagnostic_days=diagnostic, activity_type=task.get("activity_type"),
               template_b0_days=b0, source="sector_or_uncatalogued_site")
    task["l2_duration"] = occ
    return occ


def load_l2_duration_policy(path: Optional[str] = None) -> Dict[str, Any]:
    global _CFG
    cfg_path = path or CONFIG_PATH
    if path is None and _CFG is not None:
        return _CFG
    with open(cfg_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if path is None:
        _CFG = data
    return data


def concurrent_packages(floors: int = 1, freight_lifts: Optional[int] = None,
                        max_concurrent: int = 3) -> Tuple[int, str]:
    del floors, freight_lifts, max_concurrent
    return 1, "area_scaling_only"


def n_serial(floors: int = 1, freight_lifts: Optional[int] = None,
             max_concurrent: int = 3) -> Tuple[int, int, str]:
    del floors, freight_lifts, max_concurrent
    return 1, 1, "area_scaling_only"


def plate_area_m2(area_sqm: float, floors: int = 1, plate_cap: float = 0) -> float:
    """Compatibility shim: programme uses gross area, not floor plates."""
    del floors, plate_cap
    return max(1.0, float(area_sqm or 0))


def label_reference_plate(tasks: List[Dict[str, Any]],
                          policy: Optional[Dict[str, Any]] = None) -> None:
    """Label the demolition row with its per-trade reference-plate duration.

    Area is already carried by the per-trade scaling law, so no per-plate rows are
    chained ahead of physical completion: that counted the same area twice.
    """
    policy = policy or load_l2_duration_policy()
    for task in tasks:
        if task.get("activity_type") == "demolition":
            _dem = ((policy.get("activity_types") or {}).get("demolition") or {})
            _cap = _dem.get("b_max_ref_days", _dem.get("b_max"))
            _cap_txt = f"，参照台上限{int(_cap)}天" if _cap else ""
            task["client_task_name"] = f"原有装修拆除（按工序基准工期，参照台 3000㎡{_cap_txt}）"


def beat_days(b0: float, b_min: int, plate_m2: float, plate_ref: float,
              exponent: float) -> int:
    ratio = max(1e-9, float(plate_m2) / float(plate_ref))
    raw = float(b0) * (ratio ** float(exponent))
    return max(int(b_min), int(math.ceil(raw - 1e-12)))


def occupancy_days(entry: Dict[str, Any], *, area_sqm: float, floors: int = 1,
                   freight_lifts: Optional[int] = None, policy: Dict[str, Any],
                   spatial_split: bool = False) -> Dict[str, Any]:
    del floors, freight_lifts, spatial_split
    area = max(1.0, float(area_sqm or 0))
    plate_ref = float(policy["plate_ref_m2"])
    exponent = float(entry.get("area_exponent") or policy["area_exponent"])
    mode = str(entry.get("scale_mode") or "plate")
    b0 = float(entry["b0_days"])
    b_min = int(entry["b_min"])
    if mode == "once":
        duration = max(b_min, int(round(b0)))
        beat = duration
    else:
        beat = beat_days(b0, b_min, area, plate_ref, exponent)
        duration = beat
    # 上限的参照台标定量：config 文案、SKILL.md、VERSION.md 与 b_max_rule 统一称
    # b_max_ref_days；历史条目只写 b_max。两者同义（当前均为 6），优先读 b_max_ref_days，
    # 回退 b_max —— 否则将来按文档只改 b_max_ref_days 会静默无效。
    # （2026-09-24 修正：原实现只读 b_max，导致 b_max_ref_days 成为死字段。）
    b_max = entry.get("b_max_ref_days", entry.get("b_max"))
    cap: Optional[int] = None
    if b_max is not None:
        if str(entry.get("b_max_scale") or "") == "area_exponent":
            # 上限与 b0 同律标定：参照台上的单板块上限，按同一指数升到当前面积。
            # 固定常数会在面积超过参照台后截断缩放结果 —— 旧 b_max=6 自 6000㎡ 起截断，
            # 60000㎡ 把 17d 钳回 6d（损失 11d）。PM 2026-09-23 指出并要求给依据。
            # 依据：b_max 与 b0 同为「参照台 3000㎡ 上的量」（PM 2026-09-23 澄清 b0 即该工序
            # 在参照台上的基准工期；表内 b0 / 下限 / 上限三栏与引擎 b0_days / b_min / b_max 一一对应），
            # 故按同一缩放律标定到当前面积。
            cap = beat_days(float(b_max), 1, area, plate_ref, exponent)
        else:
            cap = int(b_max)
        duration = min(int(cap), int(duration))
    return dict(
        duration_days=int(duration),
        beat_days=int(beat),
        plate_area_m2=round(area, 1),
        n_serial=1,
        n_serial_building=1,
        concurrent_packages=1,
        concurrent_reason="area_scaling_only",
        stagger_days=0,
        scale_mode=mode,
        spatial_split=False,
        role=entry.get("role"),
        package_id=entry.get("package_id"),
        overlap_group=entry.get("overlap_group"),
        b0_days=b0,
        b_min=b_min,
        b_max=b_max,
        b_max_ref_days=entry.get("b_max_ref_days"),
        b_max_effective=cap,
        b_max_scale=entry.get("b_max_scale"),
        evidence_level="L0",
        status=policy.get("status"),
        area_sqm=round(area, 1),
        area_exponent=exponent,
        plate_ref_m2=plate_ref,
    )


_SLICE_SMELL = ("partition_framing", "hvac_duct", "raised_floor", "carpet", "vinyl")


def _slice_band_area(policy: Dict[str, Any]) -> float:
    """Smell caps use the legal beat at the top of the multi_floor band."""
    for band in policy.get("envelope_bands") or []:
        if band.get("id") in ("four_plate", "multi_floor"):
            return float(band.get("max_area") or 12000)
    return 12000.0


def _slice_too_fat(atype: str, beat: int, role: Optional[str],
                   entry: Dict[str, Any], policy: Dict[str, Any]) -> Optional[str]:
    if role not in ("SLICE", None) or atype not in _SLICE_SMELL:
        return None
    cap = beat_days(
        float(entry["b0_days"]), int(entry["b_min"]),
        _slice_band_area(policy),
        float(policy["plate_ref_m2"]),
        float(policy["area_exponent"]),
    )
    if beat > cap:
        return f"{atype} beat {beat}d exceeds slice cap {cap}d (possible PACKAGE/SLICE mix-up)"
    return None


def apply_l2_master_durations(
    tasks: Sequence[Dict[str, Any]],
    *,
    area_sqm: float,
    floors: int = 1,
    freight_lifts: Optional[int] = None,
    spatial_split: bool = False,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Overwrite task duration_days with L2 occupancy. Keep productivity as diagnostic."""
    policy = policy or load_l2_duration_policy()
    catalog = policy.get("activity_types") or {}
    warn_ratio = float(policy.get("qrc_warn_ratio") or 1.5)
    warnings: List[Dict[str, Any]] = []
    applied = 0
    skipped = 0
    for task in tasks:
        if task.get("inspection_gate") or task.get("milestone"):
            continue
        if task.get("duration_method") == "out_of_scope":
            continue
        if _is_timed_site_test(task):
            task["duration_authority"] = "timed_test_once"
            warnings.append(dict(
                task_id=task.get("id"), name=task.get("name"),
                code="TIMED_TEST_NO_AREA_SCALE",
                detail="72h load-bank / acoustic sample windows stay clock time, not area scaling",
            ))
            continue
        if _is_not_area_scaled(task):
            task["duration_authority"] = "not_area_scaled"
            continue
        atype = task.get("activity_type")
        if not atype:
            continue
        entry = catalog.get(atype)
        if not entry:
            skipped += 1
            warnings.append(dict(
                task_id=task.get("id"), name=task.get("name"), activity_type=atype,
                code="L2_POLICY_MISSING", detail="keep productivity duration; no L2 beat configured",
            ))
            continue
        occ = occupancy_days(
            entry, area_sqm=float(area_sqm), floors=1, freight_lifts=None,
            policy=policy, spatial_split=False,
        )
        diagnostic = None
        prod = task.get("productivity") or {}
        if prod.get("final_duration") is not None:
            diagnostic = int(prod["final_duration"])
        elif task.get("duration_days") is not None:
            diagnostic = int(task.get("duration_days") or 0)
        duration = int(occ["duration_days"])
        fat = _slice_too_fat(atype, int(occ["beat_days"]), entry.get("role"), entry, policy)
        if fat:
            warnings.append(dict(task_id=task.get("id"), name=task.get("name"),
                                 activity_type=atype, code="SLICE_BEAT_FAT", detail=fat))
        qrc_note = None
        if diagnostic is not None and duration > 0 and diagnostic > warn_ratio * duration:
            qrc_note = (
                f"productivity_formula {diagnostic}d is {diagnostic / duration:.1f}× L2 "
                f"{duration}d — diagnostic only; duration_days not overwritten by Q/R/C"
            )
            warnings.append(dict(
                task_id=task.get("id"), name=task.get("name"), activity_type=atype,
                code="QRC_DOES_NOT_GOVERN", detail=qrc_note,
                diagnostic_days=diagnostic, l2_days=duration,
            ))
        task["duration_days"] = duration
        task["duration"] = duration
        task["duration_authority"] = AUTHORITY
        task["diagnostic_days"] = diagnostic
        occ.update(
            diagnostic_days=diagnostic,
            qrc_does_not_govern=bool(qrc_note),
            qrc_note=qrc_note,
            activity_type=atype,
        )
        task["l2_duration"] = occ
        if isinstance(prod, dict) and prod:
            prod["l2_master_days"] = duration
            prod["diagnostic_only"] = True
        applied += 1
    for task in tasks:
        if task.get("duration_authority") == AUTHORITY:
            continue
        if task.get("inspection_gate") or task.get("milestone"):
            continue
        if task.get("specialist_room_scale"):
            task["duration_authority"] = "specialist_room_scale"
            continue
        if not _is_construction_phase_task(task):
            continue
        if _is_not_area_scaled(task):
            task["duration_authority"] = "not_area_scaled"
            continue
        if _is_timed_site_test(task):
            task["duration_authority"] = "timed_test_once"
            warnings.append(dict(
                task_id=task.get("id"), name=task.get("name"),
                code="TIMED_TEST_NO_AREA_SCALE",
                detail="72h load-bank / acoustic sample windows stay clock time, not area scaling",
            ))
            continue
        _scale_orphan_construction(task, area_sqm=float(area_sqm), policy=policy)
        applied += 1
    review = dict(
        applied=applied,
        skipped_unconfigured=skipped,
        authority=AUTHORITY,
        floors=1,
        freight_lifts=None,
        spatial_split=False,
        area_sqm=float(area_sqm),
        warnings=warnings,
        policy_version=policy.get("version"),
        status=policy.get("status"),
    )
    serial, conc, conc_reason = n_serial()
    review["n_serial"] = serial
    review["concurrent_packages"] = conc
    review["concurrent_reason"] = conc_reason
    review["envelope"] = _envelope_hint(area_sqm, 1, serial, conc, conc_reason, policy)
    return review


def _envelope_hint(area: float, floors: int, serial: int, conc: int,
                   conc_reason: str, policy: Dict[str, Any]) -> Dict[str, Any]:
    """Area-only scale check. Floors are not an input."""
    del floors, serial, conc
    bands = policy.get("envelope_bands") or []
    hit = None
    for band in bands:
        if area <= float(band.get("max_area") or 0):
            hit = band
            break
    if hit is None and bands:
        hit = bands[-1]
    implied = beat_days(105, 45, max(1.0, float(area or 0)),
                        float(policy["plate_ref_m2"]),
                        float(policy["area_exponent"]))
    status = "UNBANDED"
    if hit:
        lo, hi = int(hit["site_days_low"]), int(hit["site_days_high"])
        if implied < lo:
            status = "SHORT_OF_BAND"
        elif implied > hi:
            status = "LONG_OF_BAND"
        else:
            status = "IN_BAND"
    return dict(
        band_id=(hit or {}).get("id"),
        band_low=(hit or {}).get("site_days_low"),
        band_high=(hit or {}).get("site_days_high"),
        implied_site_days=implied,
        status=status,
        concurrent_reason=conc_reason,
        note="Envelope is an area-scale check, not the sum of trade bars. Floors are not used.",
    )


def freight_unspecified(reason: str) -> bool:
    return "unspecified" in str(reason)


def l2_summary_rows(tasks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for t in tasks:
        block = t.get("l2_duration")
        if not block:
            continue
        rows.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "activity_type": t.get("activity_type"),
            "duration_days": t.get("duration_days"),
            "diagnostic_days": t.get("diagnostic_days"),
            **block,
        })
    return rows
