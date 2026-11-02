# -*- coding: utf-8 -*-
"""V6.5.0 Senior PM Intelligence Layer (FIX-01 … FIX-09, FIX-13 … FIX-16).

Runs AFTER the frozen V6.4.4.1 schedule calculation. It reads the calculated programme and
CHALLENGES / EXPLAINS / RECOMMENDS / FLAGS / PROPOSES. It never shortens, overwrites,
deletes, relaxes a dependency, changes productivity or changes crew.

Business rule enforced in code: no function in this module returns or mutates a CPM
duration. Every number it emits is either (a) read from the calculated schedule, or (b) a
benchmark REFERENCE read from ``data/senior_pm_benchmarks.json``.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCHMARK_PATH = os.path.join(BASE_DIR, "data", "senior_pm_benchmarks.json")

# FIX-01 status scale
WITHIN_EXPECTED_RANGE = "WITHIN_EXPECTED_RANGE"
CONSERVATIVE_BUT_PLAUSIBLE = "CONSERVATIVE_BUT_PLAUSIBLE"
SENIOR_PM_REVIEW_REQUIRED = "SENIOR_PM_REVIEW_REQUIRED"
EXTREME_OUTLIER = "EXTREME_OUTLIER"
INSUFFICIENT_BENCHMARK_DATA = "INSUFFICIENT_BENCHMARK_DATA"

# FIX-01 benchmark-file states. The distinction is the whole point:
#   * the file is ABSENT      -> a disclosed absence. The review still runs and reports
#                                INSUFFICIENT_BENCHMARK_DATA everywhere. Never a crash, and
#                                never a guessed band standing in for missing evidence.
#   * the file is PRESENT but unusable -> a hard error. A broken evidence file must not be
#                                laundered into "we have no evidence", because those two states
#                                call for different actions from the reader.
BENCHMARK_FILE_LOADED = "BENCHMARK_FILE_LOADED"
BENCHMARK_FILE_MISSING = "BENCHMARK_FILE_MISSING"

_REQUIRED_ENTRY_KEYS = ("id", "activity_family", "range", "unit", "basis", "source_type",
                        "confidence", "notes", "source_ref")


class BenchmarkDataError(ValueError):
    """A benchmark file exists but cannot be used as evidence."""

# FIX-05 driver classes
QUANTITY_DRIVEN = "QUANTITY_DRIVEN"
PRODUCTIVITY_DRIVEN = "PRODUCTIVITY_DRIVEN"
RESOURCE_CONSTRAINED = "RESOURCE_CONSTRAINED"
WORKFRONT_CONSTRAINED = "WORKFRONT_CONSTRAINED"
DEPENDENCY_CONSTRAINED = "DEPENDENCY_CONSTRAINED"
CALENDAR_DRIVEN = "CALENDAR_DRIVEN"
INPUT_UNCERTAINTY = "INPUT_UNCERTAINTY"
CALIBRATION_REVIEW_REQUIRED = "CALIBRATION_REVIEW_REQUIRED"

# FIX-16 reviewable outcomes
ACCEPT_RECOMMENDATION = "ACCEPT_RECOMMENDATION"
KEEP_CALCULATED_BASELINE = "KEEP_CALCULATED_BASELINE"
PROVIDE_PROJECT_DATA = "PROVIDE_PROJECT_DATA"
REJECT_RECOMMENDATION = "REJECT_RECOMMENDATION"

# FIX-42 minimum practical zone (mirrors the frozen capacity model, read-only)
MIN_PRACTICAL_ZONE_SQM = 800.0
RECOMMENDED_ZONE_SQM = 2000.0


def resolve_source_ref(ref: str, root: Optional[str] = None) -> bool:
    """True when ``file#key.path`` resolves to a shipped file AND a shipped key.

    The file half is package-relative. The key half walks object keys and ``[id]`` list
    selectors, so a band anchored to ``config/x.json#envelope_bands[one_plate]`` stops counting
    as evidenced the moment that row is renamed or removed. This is what lets a test assert
    that every published reference band still points at something real.
    """
    root = root or BASE_DIR
    file_part, _, key_part = str(ref).partition("#")
    file_part, key_part = file_part.strip(), key_part.strip()
    if not file_part:
        return False
    target = file_part if os.path.isabs(file_part) else os.path.join(root, file_part)
    if not os.path.isfile(target):
        return False
    if not key_part:
        return True
    try:
        with open(target, encoding="utf-8") as f:
            node: Any = json.load(f)
    except Exception:
        return False
    for token in re.findall(r"[^.\[\]]+|\[[^\]]*\]", key_part):
        if not token.strip():
            continue
        if token.startswith("["):
            key = token.strip("[]").strip()
            if isinstance(node, list):
                hit = next((item for item in node
                            if isinstance(item, dict) and str(item.get("id")) == key), None)
                if hit is None:
                    return False
                node = hit
            elif isinstance(node, dict):
                if key not in node:
                    return False
                node = node[key]
            else:
                return False
        else:
            if not isinstance(node, dict) or token not in node:
                return False
            node = node[token]
    return True


def no_evidence_benchmarks(reason: str, path: str) -> Dict[str, Any]:
    """The structure the review runs on when no evidenced benchmark file is present.

    Deliberately carries no numbers and no bands. Every domain then reports
    INSUFFICIENT_BENCHMARK_DATA, which is this module's stated rule — "stated explicitly,
    never guessed". A review that cannot be evidenced says so; it does not crash and it does
    not substitute an invented band.
    """
    return dict(
        version="none",
        status=BENCHMARK_FILE_MISSING,
        benchmarks=[],
        governance=dict(
            rule=("A reference band is read-only. It never supplies, shortens, overrides or "
                  "mutates a calculated CPM duration. A duration changes only through an "
                  "approved scenario that re-solves the CPM."),
            no_fabrication=("No reference band may be invented. Where no evidenced benchmark is "
                            "available the review states INSUFFICIENT_BENCHMARK_DATA."),
            no_overstatement=("A band is described only as what its source supports. It is never "
                              "presented as a trade-body figure or as a measured project actual."),
        ),
        provenance=dict(
            status=BENCHMARK_FILE_MISSING,
            path=path,
            origin="No Senior PM benchmark file is present in this pack.",
            derived_from=None,
            source_refs=[],
            limitations=[reason],
        ),
    )


def load_benchmarks(path: Optional[str] = None) -> Dict[str, Any]:
    """Load the Senior PM reference bands. Absence is disclosed; corruption is fatal.

    * absent         -> a structured no-evidence set (never raises).
    * present, usable-> the parsed set, after schema and source-ref validation.
    * present, broken-> BenchmarkDataError naming every defect, so a file that exists but is
                        unusable is never silently downgraded to "no evidence".
    """
    target = path or BENCHMARK_PATH
    if not os.path.isfile(target):
        return no_evidence_benchmarks(
            f"No Senior PM benchmark file at {target}. Reference bands are unavailable, so "
            f"every domain reports {INSUFFICIENT_BENCHMARK_DATA} until one is supplied.",
            target)

    with open(target, encoding="utf-8") as f:
        try:
            bms: Any = json.load(f)
        except json.JSONDecodeError as exc:
            raise BenchmarkDataError(f"{target} is not valid JSON: {exc}") from exc
    if not isinstance(bms, dict):
        raise BenchmarkDataError(
            f"{target} must contain a JSON object, got {type(bms).__name__}")

    errs: List[str] = []
    for key in ("governance", "provenance", "benchmarks"):
        if key not in bms:
            errs.append(f"missing top-level '{key}'")
    if isinstance(bms.get("governance"), dict):
        for key in ("rule", "no_fabrication", "no_overstatement"):
            if not bms["governance"].get(key):
                errs.append(f"missing governance.{key}")
    if isinstance(bms.get("provenance"), dict):
        if not bms["provenance"].get("limitations"):
            errs.append("missing provenance.limitations")
    else:
        errs.append("provenance must be an object")

    entries = bms.get("benchmarks")
    if not isinstance(entries, list):
        errs.append("'benchmarks' must be a list")
        entries = []
    for i, bm in enumerate(entries):
        where = f"benchmarks[{i}]"
        if not isinstance(bm, dict):
            errs.append(f"{where} is not an object")
            continue
        where = f"{where} ({bm.get('id') or '?'})"
        for key in _REQUIRED_ENTRY_KEYS:
            if bm.get(key) in (None, "", {}):
                errs.append(f"{where} missing '{key}'")
        rng = bm.get("range")
        if rng is None:
            pass
        elif not isinstance(rng, dict):
            errs.append(f"{where} range must be an object")
        else:
            for key in ("low", "high"):
                if rng.get(key) is None:
                    errs.append(f"{where} range.{key} missing")
            if (rng.get("low") is not None and rng.get("high") is not None
                    and float(rng["low"]) > float(rng["high"])):
                errs.append(f"{where} range.low exceeds range.high")
        ref = bm.get("source_ref")
        if ref and not resolve_source_ref(str(ref)):
            errs.append(f"{where} source_ref '{ref}' does not resolve to a shipped file-backed key")
        if "apply_duration" in bm or "override" in bm:
            errs.append(f"{where} carries an apply/override field, which the contract forbids")
    if errs:
        raise BenchmarkDataError(f"{target} is present but unusable as evidence:\n  - "
                                 + "\n  - ".join(errs))

    bms["status"] = BENCHMARK_FILE_LOADED
    return bms


def match_benchmark(activity_family: str, benchmarks: Dict[str, Any],
                    context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Return the best-evidenced benchmark for a family, or None (INSUFFICIENT_BENCHMARK_DATA)."""
    ctx = context or {}
    best = None
    for bm in benchmarks.get("benchmarks", []):
        if bm.get("activity_family") != activity_family:
            continue
        score = 0
        for dim in ("project_type", "area_band", "existing_condition", "target_condition",
                    "delivery_method", "fitout_complexity", "location"):
            if dim in bm and ctx.get(dim):
                score += 1 if str(bm[dim]) == str(ctx[dim]) else -1
        if best is None or score > best[0]:
            best = (score, bm)
    if best is None or best[0] < 0:
        return None
    return best[1]


def select_physical_construction_benchmark(benchmarks: Dict[str, Any],
                                           context: Optional[Dict[str, Any]] = None
                                           ) -> Optional[Dict[str, Any]]:
    """The signed envelope row that governs this project's area.

    The envelope table is keyed by cumulative maxima — a row governs while
    ``area_sqm <= max_area`` — so the governing row is the SMALLEST maximum area that covers
    the project. Selection is by area and not by the coarse ``area_band`` label, because one
    engine band can straddle two signed rows and picking by label would silently choose one.
    An area beyond the largest signed row returns None: no band, reported as
    INSUFFICIENT_BENCHMARK_DATA rather than extrapolated.
    """
    ctx = context or {}
    cands = [bm for bm in benchmarks.get("benchmarks", [])
             if bm.get("activity_family") == "physical_construction" and bm.get("max_area")]
    try:
        area = float(ctx.get("area_sqm"))
    except (TypeError, ValueError):
        area = None
    if area is None or not cands:
        return match_benchmark("physical_construction", benchmarks, ctx)
    covering = [bm for bm in cands if float(bm["max_area"]) >= area]
    if not covering:
        return None
    return min(covering, key=lambda bm: float(bm["max_area"]))


def classify_status(calculated: float, low: float, high: float) -> str:
    """FIX-01 status from a calculated value vs a benchmark range. Pure function.

    Bands: inside the reference; up to 1.6x the upper reference = conservative but plausible;
    up to 3x = Senior PM review required; beyond 3x = extreme outlier.
    """
    if calculated is None or low is None or high is None or high <= 0:
        return INSUFFICIENT_BENCHMARK_DATA
    if calculated <= high * 1.15:
        return WITHIN_EXPECTED_RANGE
    if calculated <= high * 1.6:
        return CONSERVATIVE_BUT_PLAUSIBLE
    if calculated <= high * 3.0:
        return SENIOR_PM_REVIEW_REQUIRED
    return EXTREME_OUTLIER


def _bench_row(domain: str, calculated, bm, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not bm:
        return dict(domain=domain, calculated_duration=calculated,
                    benchmark_status=INSUFFICIENT_BENCHMARK_DATA, benchmark_low=None,
                    benchmark_typical=None, benchmark_high=None, variance=None,
                    basis=None, confidence=None, source_type=None, source_ref=None,
                    unit=None, benchmark_id=None,
                    review_reason="No evidenced benchmark for this activity family.",
                    **(extra or {}))
    rng = bm.get("range") or {}
    low, typical, high = rng.get("low"), rng.get("typical"), rng.get("high")
    status = classify_status(calculated, low, high)
    variance = (round(float(calculated) - float(typical), 1)
                if (calculated is not None and typical is not None) else None)
    return dict(domain=domain, calculated_duration=calculated, benchmark_low=low,
                benchmark_typical=typical, benchmark_high=high, benchmark_status=status,
                variance=variance, basis=bm.get("basis"), confidence=bm.get("confidence"),
                source_type=bm.get("source_type"), source_ref=bm.get("source_ref"),
                benchmark_id=bm.get("id"),
                unit=bm.get("unit"), review_reason=_reason(domain, calculated, low, typical, high, status),
                **(extra or {}))


def _reason(domain, calc, low, typical, high, status) -> str:
    if status == WITHIN_EXPECTED_RANGE:
        return f"{domain}: {calc} is inside the Senior PM reference band {low}-{high}. No review trigger."
    if status == CONSERVATIVE_BUT_PLAUSIBLE:
        return (f"{domain}: {calc} sits above the reference band {low}-{high} "
                f"(typical {typical}). Conservative but plausible; confirm scope / logistics.")
    if status == SENIOR_PM_REVIEW_REQUIRED:
        return (f"{domain}: {calc} materially exceeds the reference band {low}-{high}. "
                f"Senior PM review required before presenting to the client.")
    if status == EXTREME_OUTLIER:
        return (f"{domain}: {calc} is an extreme outlier vs the reference band {low}-{high}. "
                f"Do not present as a normal programme; resolve the driver first.")
    if low is not None and high is not None:
        return (f"{domain}: signed reference band {low}-{high} is available, but this programme "
                f"carries no comparable calculated value for the domain, so nothing was compared.")
    return f"{domain}: no evidenced benchmark."


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------
def build_context(area_sqm: float, existing_condition: str = "cat_a", delivery: str = "DB",
                  complexity: str = "normal", location: str = "shanghai") -> Dict[str, Any]:
    band = ("small_lt_1500" if area_sqm < 1500 else
            "standard_1500_5000" if area_sqm <= 5000 else
            "large_5000_15000" if area_sqm <= 15000 else "major_gt_15000")
    loc = {"上海": "shanghai", "杭州": "hangzhou", "苏州": "suzhou",
           "北京": "beijing", "深圳": "shenzhen"}.get(str(location), "shanghai")
    return dict(project_type="commercial_office", area_band=band,
                existing_condition=existing_condition, target_condition="cat_b",
                delivery_method=delivery, fitout_complexity=complexity, location=loc,
                area_sqm=area_sqm)


# ---------------------------------------------------------------------------
# FIX-01 — programme sanity check
# ---------------------------------------------------------------------------
def _phase_span_workdays(phase_rows, opt_phases) -> Optional[int]:
    if not opt_phases:
        return None
    for p in opt_phases:
        if "Phase 6" in str(p.get("name", "")) or "实体施工" in str(p.get("name", "")):
            return p
    return None


def _count_workdays(start: str, finish: str) -> Optional[int]:
    """Engine-calendar working days (the site calendar; weekends are worked at 7D)."""
    try:
        from datetime import date, timedelta
        from core import holidays as H
        raw = H.load_holiday_raw()
        pairs = [(date.fromisoformat(str(h["start"])[:10]), date.fromisoformat(str(h["finish"])[:10]))
                 for h in raw]
        d, f = date.fromisoformat(start[:10]), date.fromisoformat(finish[:10])
        n = 0
        while d <= f:
            if not any(a <= d <= b for a, b in pairs):
                n += 1
            d += timedelta(days=1)
        return n
    except Exception:
        return None


def _count_standard_workdays(start: str, finish: str) -> Optional[int]:
    """Mon–Fri excluding holidays — the unit a PM's "planning months" benchmark is quoted in.

    The engine runs a 7-day site calendar (weekends are worked), so its own day count is not
    directly comparable with a "working days" reference. Both are reported; the benchmark
    comparison uses this standard basis.
    """
    try:
        from datetime import date, timedelta
        from core import holidays as H
        raw = H.load_holiday_raw()
        pairs = [(date.fromisoformat(str(h["start"])[:10]), date.fromisoformat(str(h["finish"])[:10]))
                 for h in raw]
        d, f = date.fromisoformat(start[:10]), date.fromisoformat(finish[:10])
        n = 0
        while d <= f:
            if d.weekday() < 5 and not any(a <= d <= b for a, b in pairs):
                n += 1
            d += timedelta(days=1)
        return n
    except Exception:
        return None


def programme_sanity_check(tasks: Sequence[Dict[str, Any]], solved: Dict[str, Any],
                           opt: Dict[str, Any], ctx: Dict[str, Any],
                           benchmarks: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """FIX-01: review calculated outputs against applicable benchmark references."""
    if benchmarks is None:
        benchmarks = load_benchmarks()
    by_id = {t.get("id"): t for t in tasks}
    rows: List[Dict[str, Any]] = []

    # overall programme (no evidenced benchmark -> INSUFFICIENT_BENCHMARK_DATA, stated)
    ov_start = solved.get("start") or min((t["start"] for t in tasks if t.get("start")), default="")
    overall_wd = _count_workdays(ov_start, solved.get("finish_date") or "")
    rows.append(_bench_row("Overall Programme Duration", overall_wd, None,
                           dict(engine_calendar_days=overall_wd,
                                note="Overall programme benchmark not evidenced; per-domain "
                                     "references are used instead.")))

    # physical construction (Phase 6). The comparison MUST use the unit the band declares:
    # the signed envelope bands are quoted in site days, so comparing them against a Mon-Fri
    # count would be a unit error that silently moves the verdict. Both day counts are always
    # reported so the basis stays auditable either way.
    ph6 = _phase_span_workdays(None, opt.get("phases") or [])
    engine_wd = _count_workdays(ph6["start"], ph6["finish"]) if ph6 else None
    std_wd = _count_standard_workdays(ph6["start"], ph6["finish"]) if ph6 else None
    bm_con = select_physical_construction_benchmark(benchmarks, ctx)
    band_unit = str((bm_con or {}).get("unit") or "")
    if bm_con and "working_day" in band_unit:
        con_value = std_wd
        basis_of_comparison = "standard working days (Mon–Fri less holidays)"
    else:
        con_value = engine_wd
        basis_of_comparison = (f"engine site-calendar days (band unit: {band_unit})" if bm_con
                               else "engine site-calendar days (no band available)")
    rows.append(_bench_row("Physical Construction Duration", con_value, bm_con,
                           dict(engine_calendar_days=engine_wd,
                                standard_working_days=std_wd,
                                basis_of_comparison=basis_of_comparison,
                                window=f'{ph6["start"]} → {ph6["finish"]}' if ph6 else None,
                                phase_id=(ph6 or {}).get("id"))))

    # mobilisation / site protection (activity duration; same calendar as the benchmark unit)
    mob = None
    for t in tasks:
        at = t.get("activity_type") or ""
        nm = str(t.get("name", ""))
        if at == "site_setup" or "场地保护" in nm or "临时设施" in nm:
            mob = t
            break
    bm_mob = match_benchmark("mobilisation_site_protection", benchmarks, ctx)
    rows.append(_bench_row("Mobilisation / Site Protection",
                           (mob or {}).get("duration_days"), bm_mob,
                           dict(task_id=(mob or {}).get("id"), task_name=(mob or {}).get("name"),
                                engine_calendar_days=(mob or {}).get("duration_days"))))

    # domains without evidenced benchmarks — stated explicitly, never guessed
    for dom in ("Design Duration", "Tender Duration", "Procurement Duration",
                "Major Trade Durations", "Testing & Commissioning",
                "Furniture Installation", "Closeout / Handover"):
        family = {"Testing & Commissioning": "testing_commissioning",
                  "Furniture Installation": "furniture_installation",
                  "Closeout / Handover": "closeout_handover"}.get(dom)
        calc = None
        if family:
            bm_calc = match_benchmark(family, benchmarks, ctx)
            cand = [t for t in tasks if t.get("activity_type") == family]
            calc = (cand[0].get("duration_days") if cand else None)
            rows.append(_bench_row(dom, calc, bm_calc))
        else:
            rows.append(_bench_row(dom, None, None,
                                   dict(note="No evidenced Senior PM benchmark; supply "
                                             "historical or contractor data to enable review.")))

    triggered = [r for r in rows if r["benchmark_status"] in
                 (SENIOR_PM_REVIEW_REQUIRED, EXTREME_OUTLIER)]
    return dict(domains=rows, review_triggered=len(triggered),
                worst_status=_worst(rows),
                governance="Benchmark data never overwrites the CPM duration.")


def _worst(rows) -> str:
    order = [INSUFFICIENT_BENCHMARK_DATA, WITHIN_EXPECTED_RANGE, CONSERVATIVE_BUT_PLAUSIBLE,
             SENIOR_PM_REVIEW_REQUIRED, EXTREME_OUTLIER]
    worst = WITHIN_EXPECTED_RANGE
    for r in rows:
        s = r["benchmark_status"]
        if order.index(s) > order.index(worst):
            worst = s
    return worst


# ---------------------------------------------------------------------------
# FIX-05 — duration driver review
# ---------------------------------------------------------------------------
def classify_driver(task: Dict[str, Any], n_zones: int = 1) -> Dict[str, Any]:
    prod = task.get("productivity") or {}
    if task.get("unresolved_placeholder") or task.get("conditional_requirement"):
        return dict(primary=INPUT_UNCERTAINTY, why="UNRESOLVED / conditional input")
    cs = prod.get("capacity_status")
    if cs == RESOURCE_CONSTRAINED:
        return dict(primary=RESOURCE_CONSTRAINED, why="availability below physical demand")
    if cs == WORKFRONT_CONSTRAINED:
        return dict(primary=WORKFRONT_CONSTRAINED,
                    why=f"feasible crew {prod.get('feasible_crew')} capped below theoretical "
                        f"{prod.get('theoretical_crew_demand')} by workfront capacity")
    if prod.get("rate_status") == "provisional" or str(prod.get("confidence")) == "low":
        return dict(primary=CALIBRATION_REVIEW_REQUIRED,
                    why=f"rate evidence {prod.get('evidence_level')} / status {prod.get('rate_status')}")
    if str(task.get("quantity_source", "")).startswith("derived") or task.get("quantity_source") == "derived_from_area":
        return dict(primary=QUANTITY_DRIVEN, why="quantity is derived, not measured")
    preds = [p for p in str(task.get("predecessors", "")).split(",") if p.strip()]
    if len(preds) >= 2:
        return dict(primary=DEPENDENCY_CONSTRAINED,
                    why=f"{len(preds)} predecessor links (driving chain)")
    if (task.get("activity_calendar") or {}).get("calendar_id"):
        cid = task["activity_calendar"]["calendar_id"]
        if cid not in ("SITE_7D",):
            return dict(primary=CALENDAR_DRIVEN, why=f"calendar {cid}")
    return dict(primary=QUANTITY_DRIVEN, why="quantity / rate balance over the given duration")


def duration_driver_review(tasks: Sequence[Dict[str, Any]], top_n: int = 8) -> Dict[str, Any]:
    """FIX-05: do NOT flag every long activity — only the material ones, with a reason."""
    prod = [t for t in tasks if t.get("duration_method") == "productivity_formula"]
    ranked = sorted(prod, key=lambda t: -(t.get("duration_days") or 0))
    rows = []
    for t in ranked[:top_n]:
        d = classify_driver(t)
        p = t.get("productivity") or {}
        rows.append(dict(
            task_id=t.get("id"), name=t.get("name"), client_task_name=t.get("client_task_name"),
            duration_days=t.get("duration_days"),
            quantity=p.get("quantity"), unit=p.get("unit"),
            theoretical_crew=p.get("theoretical_crew_demand"), feasible_crew=p.get("feasible_crew"),
            workfront_capacity=p.get("physical_capacity_workers"),
            effective_workfront_capacity=p.get("effective_workfront_capacity"),
            congestion=p.get("congestion_factor"), calendar=p.get("rate_level"),
            predecessors=str(t.get("predecessors", "")), critical=t.get("critical"),
            primary_reason=d["primary"], reason_detail=d["why"],
            capacity_status=p.get("capacity_status"),
            congestion_status=p.get("congestion_status"),
        ))
    return dict(rows=rows, note="Ranked by calculated duration; each row carries a measured "
                                "reason, not an assumption.")


# ---------------------------------------------------------------------------
# FIX-03 / FIX-04 — mobilisation + construction outlier
# ---------------------------------------------------------------------------
def mobilisation_review(sanity: Dict[str, Any]) -> Dict[str, Any]:
    row = next((r for r in sanity["domains"] if r["domain"] == "Mobilisation / Site Protection"), None)
    if not row:
        return dict(status=INSUFFICIENT_BENCHMARK_DATA)
    return dict(
        activity="Site Protection & Temporary Facilities",
        calculated=row["calculated_duration"], unit=row.get("unit") or "working_days",
        senior_pm_reference=f'{row["benchmark_low"]}–{row["benchmark_high"]}',
        status=row["benchmark_status"],
        review_questions=[
            "Is landlord access restricted?",
            "Is phased possession required?",
            "Are extensive hoardings required?",
            "Is temporary MEP unusually complex?",
            "Are logistics / elevator restrictions present?",
            "Is work allowed to commence after essential setup only?",
        ],
        recommendation=("Review whether essential mobilisation can complete in "
                        f'{row["benchmark_low"]}–{row["benchmark_high"]} working days and the '
                        "remaining temporary works continue in parallel with early construction."),
        must_not=("Do NOT automatically change the calculated mobilisation duration. A duration "
                  "is only changed by an approved scenario that re-solves the CPM."),
    )


def construction_outlier_review(sanity: Dict[str, Any], drivers: Dict[str, Any],
                                task_count: int) -> Dict[str, Any]:
    row = next((r for r in sanity["domains"] if r["domain"] == "Physical Construction Duration"), None)
    if not row:
        return dict(status=INSUFFICIENT_BENCHMARK_DATA)
    return dict(
        domain="Physical Construction", calculated=row["calculated_duration"],
        unit=row.get("unit") or "working_days",
        reference=dict(low=row["benchmark_low"], typical=row["benchmark_typical"],
                       high=row["benchmark_high"], basis=row["basis"],
                       confidence=row["confidence"], source_ref=row.get("source_ref")),
        status=row["benchmark_status"], variance_days=row["variance"],
        top_drivers=[dict(rank=i + 1, name=d["client_task_name"] or d["name"],
                          duration_days=d["duration_days"], reason=d["primary_reason"],
                          detail=d["reason_detail"])
                     for i, d in enumerate(drivers["rows"][:6])],
        ranking_basis=["calculated duration", "critical-path contribution",
                       "driving-chain contribution", "float",
                       "potential sectional sequencing opportunity"],
        note=(f"{task_count} production activities reviewed. Drivers are ranked on measured "
              "facts from the frozen engine output."),
    )


# ---------------------------------------------------------------------------
# FIX-06 — sectional / flow construction review
# ---------------------------------------------------------------------------
FLOW_CHAIN = [
    ("partition_framing", "hvac_duct", "in-wall / above-ceiling inspection and zone release",
     "rolling zone release: frame + first-fix Zone A → inspect → board Zone A while Zones B/C continue"),
    ("gypsum_boarding", "ceiling_grid", "in-wall inspection passed in that zone",
     "board and close ceiling zone-by-zone rather than waiting for whole-floor boarding"),
    ("hvac_duct", "gypsum_boarding", "duct pressure test / above-ceiling inspection per zone",
     "release zones to boarding as each zone's MEP first fix completes"),
    ("testing_commissioning", "furniture", "integrated test per zone / IAQ gate",
     "phase T&C and furniture by zone where the compliance gate permits"),
]


def _pred_ids(task: Dict[str, Any]) -> List[int]:
    out: List[int] = []
    for tok in str(task.get("predecessors", "") or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        digits = ""
        for ch in tok:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            out.append(int(digits))
    return out


_SCOPE_RANK = {"zone": 0, "floor": 1, "project": 2, "unknown": 2}


def _infer_spatial_scope(task: Optional[Dict[str, Any]],
                         pipeline_active: Optional[bool] = None) -> str:
    """Read the instantiated spatial scope; never assume 'project' from an inspection flag.

    Keyword config may say a gate *would* be zone-scoped. If the spatial pipeline did not
    instantiate zones, a single unzoned gate is project-scoped in the graph.
    """
    if not task:
        return "unknown"
    declared = task.get("spatial_scope")
    if declared in ("zone", "floor", "project"):
        if declared == "zone" and not task.get("zone") and pipeline_active is False:
            return "project"
        if declared == "floor" and not task.get("floor") and not task.get("zone") and pipeline_active is False:
            return "project"
        return declared
    if task.get("zone"):
        return "zone"
    if task.get("inspection_gate"):
        from core.zone_model import gate_scope
        named = gate_scope(str(task.get("name") or ""))["scope"]
        if pipeline_active is False and not task.get("zone") and not task.get("floor"):
            return "project"
        return named
    if task.get("floor") and not task.get("zone"):
        return "floor"
    at = task.get("activity_type")
    if at:
        from core.zone_model import spatial_scope as declared_scope
        named = declared_scope(at)
        if pipeline_active is False and named in ("zone", "floor") and not task.get("zone"):
            return "project"
        return named
    return "project"


def _scope_label(scope: Optional[str]) -> str:
    return {
        "zone": "zone-scoped FS gate",
        "floor": "floor-scoped FS gate",
        "project": "project-scoped FS gate",
    }.get(scope or "", "unscoped FS gate")


def sectional_flow_review(tasks: Sequence[Dict[str, Any]], area_sqm: float,
                          explicit_workfronts: Optional[int] = None) -> Dict[str, Any]:
    """FIX-06: identify excessive full-floor FS sequencing and propose a rolling release.

    Zone count uses the MINIMUM PRACTICAL zone area (a zone must be independently executable
    and large enough to work in) rather than the recommended size, because the question here
    is "could this be released in parts", not "how many full-size zones exist".

    Review honesty (spec correction): a named per-zone inspection is a zone-scoped FS gate.
    Do not label it as a project-level gate. Project-level wording is reserved for a gate
    whose instantiated scope is project, or for a successor that waits on another zone.
    """
    from core.workfront_capacity import resolve_workfronts
    wf = resolve_workfronts(float(area_sqm), explicit_workfronts)
    if explicit_workfronts is not None:
        zones = int(wf["sectional_workfront_count"])
    else:
        zones = max(1, min(8, int(float(area_sqm) // MIN_PRACTICAL_ZONE_SQM)))
    zone_area = area_sqm / zones if zones else area_sqm

    by_id = {t.get("id"): t for t in tasks}
    by_at: Dict[str, List[Dict[str, Any]]] = {}
    for t in tasks:
        at = t.get("activity_type")
        if at:
            by_at.setdefault(at, []).append(t)
    gates = [t for t in tasks if t.get("inspection_gate")]
    actual_zones = sorted({t.get("zone") for t in tasks if t.get("zone")})
    spatial_pipeline_active = bool(actual_zones) or any(
        t.get("spatial_scope") == "zone" for t in tasks)

    def _closure(tid: int, seen=None) -> set:
        seen = seen or set()
        for pid in _pred_ids(by_id.get(tid) or {}):
            if pid not in seen:
                seen.add(pid)
                _closure(pid, seen)
        return seen

    def _match_pred(succ: Dict[str, Any], preds: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        sz = succ.get("zone")
        if sz:
            same = [p for p in preds if p.get("zone") == sz]
            if same:
                return same[0]
        return preds[0] if preds else None

    def _gate_between(pred: Dict[str, Any], succ: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        succ_preds = _pred_ids(succ)
        for g in gates:
            if g.get("id") not in succ_preds:
                continue
            if pred.get("id") in _closure(g.get("id")) or pred.get("id") in _pred_ids(g):
                return g
        return None

    def _cross_zone(pred: Dict[str, Any], succ: Dict[str, Any], gate: Optional[Dict[str, Any]]) -> bool:
        sz = succ.get("zone")
        if not sz:
            return False
        if gate and gate.get("zone") and gate.get("zone") != sz:
            return True
        for pid in _pred_ids(succ):
            pt = by_id.get(pid)
            if (pt and pt.get("activity_type") == pred.get("activity_type")
                    and pt.get("zone") and pt.get("zone") != sz):
                return True
        return False

    def _row(pred, succ, gate, classification, issue, release, concept):
        g_scope = _infer_spatial_scope(gate, spatial_pipeline_active) if gate else None
        current = (f'{pred.get("activity_type")} FS → inspection gate 「{gate.get("name")}」 FS → '
                   f'{succ.get("activity_type")} ({_scope_label(g_scope)})'
                   if gate else f'{pred.get("activity_type")} FS {succ.get("activity_type")} (full-floor)')
        return dict(
            code="SECTIONAL_FLOW_OPPORTUNITY",
            classification=classification,
            blocking_gate_scope=g_scope,
            successor_scope=_infer_spatial_scope(succ, spatial_pipeline_active),
            candidate_predecessor=dict(id=pred.get("id"),
                                       name=pred.get("client_task_name") or pred.get("name"),
                                       finish=pred.get("finish"), zone=pred.get("zone")),
            candidate_successor=dict(id=succ.get("id"),
                                     name=succ.get("client_task_name") or succ.get("name"),
                                     start=succ.get("start"), zone=succ.get("zone")),
            blocking_gate=(dict(id=gate.get("id"),
                                name=gate.get("client_task_name") or gate.get("name"),
                                finish=gate.get("finish"),
                                spatial_scope=g_scope,
                                zone=gate.get("zone")) if gate else None),
            current_logic=current,
            observed_issue=issue,
            physical_release_condition=release,
            recommended_overlap_concept=concept,
            risk=("partial-zone handover requires a real per-zone inspection and a clean access "
                  "route; concealed-work quality hold points must not be compromised"),
            required_pm_confirmation=("confirm per-zone inspection is available, the release "
                                      "condition is physically verifiable, and the downstream crew "
                                      "can occupy the released zone"),
        )

    opportunities = []
    intact_releases = []
    for pred_at, succ_at, release, concept in FLOW_CHAIN:
        preds = by_at.get(pred_at) or []
        succs = by_at.get(succ_at) or []
        if not preds or not succs:
            continue
        intact_pair = None
        opportunity_pair = None
        for s in succs:
            p = _match_pred(s, preds)
            if not p:
                continue
            gate_hit = _gate_between(p, s)
            strict_fs = bool(p.get("finish") and s.get("start") and s["start"] >= p["finish"])
            if zones < 2 or not (gate_hit or strict_fs):
                continue
            g_scope = _infer_spatial_scope(gate_hit, spatial_pipeline_active) if gate_hit else None
            s_scope = _infer_spatial_scope(s, spatial_pipeline_active)
            cross = _cross_zone(p, s, gate_hit)
            coarse = bool(
                gate_hit and _SCOPE_RANK.get(g_scope, 2) > _SCOPE_RANK.get(s_scope, 0))
            pipeline_off = (not spatial_pipeline_active) and zones >= 2
            if cross:
                opportunity_pair = (p, s, gate_hit, "CROSS_ZONE_HOLD",
                    "the successor waits for an inspection or predecessor in another zone "
                    "instead of its own physically released zone")
                break
            if coarse:
                issue = ("the successor is finer-grained than the blocking gate "
                         f"({_scope_label(g_scope)} holds a {s_scope}-scoped trade). "
                         "This may be a declared compliance hold (floor IAQ / project T&C), "
                         "not a deleted inspection.")
                cls = ("COMPLIANCE_HOLD_COARSER_THAN_ZONE"
                       if g_scope in ("floor", "project") else "COARSE_GATE")
                opportunity_pair = (p, s, gate_hit, cls, issue)
                break
            if pipeline_off:
                opportunity_pair = (p, s, gate_hit, "PLANNING_ZONE_POTENTIAL",
                    "the schedule has no instantiated zones, so the inspection is a "
                    f"{_scope_label(g_scope or 'project')}; planning derivation suggests "
                    f"{zones} practical zones. This is not evidence that a zone-scoped gate "
                    "was deleted.")
                break
            if gate_hit and g_scope == "zone" and not cross:
                if intact_pair is None:
                    intact_pair = (p, s, gate_hit)
                continue
            if gate_hit and g_scope == "project":
                opportunity_pair = (p, s, gate_hit, "PROJECT_SCOPED_GATE",
                    "the successor waits for a project-scoped inspection gate rather than "
                    "a first physically released zone")
                break
        if opportunity_pair:
            p, s, g, cls, issue = opportunity_pair
            opportunities.append(_row(p, s, g, cls, issue, release, concept))
        elif intact_pair:
            p, s, g = intact_pair
            row = _row(p, s, g, "ZONE_FS_INTACT",
                       "this pair already uses a zone-scoped FS inspection release; remaining "
                       "seriality is same-trade crew FS or a coarser compliance gate, not a "
                       "missing per-zone hold point",
                       release, concept)
            row["code"] = "ZONE_FS_INTACT"
            intact_releases.append(row)
    return dict(executable_zones=zones, zone_area_sqm=round(zone_area, 1),
                minimum_practical_zone_sqm=MIN_PRACTICAL_ZONE_SQM,
                zone_basis=("explicit workfronts" if explicit_workfronts is not None
                            else f"floor(area / {MIN_PRACTICAL_ZONE_SQM:g} sqm practical zone)"),
                source_type=wf["source_type"], confidence=wf["confidence"],
                spatial_pipeline_active=spatial_pipeline_active,
                actual_zone_count=len(actual_zones),
                actual_zones=actual_zones,
                opportunities=opportunities, opportunity_count=len(opportunities),
                intact_releases=intact_releases, intact_release_count=len(intact_releases),
                rule=("Sectional sequencing is only proposed when independently executable zones "
                      "exist AND a minimum practical zone area is satisfied. FS is NEVER replaced "
                      "with SS automatically. Zone-scoped inspection gates are reported as intact "
                      "releases, not as project-level defects."),
                note=("Planning zone count is a provisional derivation. Instantiated zone / floor "
                      "data on the solved tasks takes priority. A floor-scoped IAQ gate is a "
                      "declared compliance serial, not an engine defect."))


# ---------------------------------------------------------------------------
# FIX-07 — critical path to management action
# ---------------------------------------------------------------------------
def critical_path_management(opt: Dict[str, Any], tasks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    cp = (opt or {}).get("critical_path") or {}
    longest = cp.get("longest_critical_activities") or []
    top = [dict(rank=i + 1, name=x.get("name"), duration_days=x.get("duration_days"),
                start=x.get("start"), finish=x.get("finish"), float_days=x.get("float_days"))
           for i, x in enumerate(longest[:6])]
    names = ", ".join(str(x.get("name")) for x in longest[:3]) or "the driving chain"
    compromised = ["concealed-work inspection", "fire stopping", "MEP quality hold points"]
    return dict(
        critical_task_count=cp.get("critical_task_count"),
        driving_chain_length=cp.get("driving_chain_length"),
        critical_status_counts=cp.get("critical_status_counts"),
        top_critical_activities=top,
        why_critical=(f"Critical exposure is driven by {names}."),
        pm_action=("Review sectional MEP inspection and rolling board / ceiling closure to protect "
                   "the commencement of downstream trades."),
        must_not_compromise=compromised,
        note=("Critical path facts are read directly from the frozen CPM output. This layer adds "
              "explanation and management action only."),
    )


# ---------------------------------------------------------------------------
# FIX-09 — T&C review
# ---------------------------------------------------------------------------
def tc_review(tasks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    tc = [t for t in tasks if t.get("activity_type") == "testing_commissioning"]
    if not tc:
        return dict(status=INSUFFICIENT_BENCHMARK_DATA)
    t = tc[0]
    preds = [p for p in str(t.get("predecessors", "")).split(",") if p.strip()]
    serial = len(preds) <= 1
    return dict(
        task_id=t.get("id"), name=t.get("client_task_name") or t.get("name"),
        duration_days=t.get("duration_days"), start=t.get("start"), finish=t.get("finish"),
        predecessor_links=preds,
        representation="single fully sequential block" if serial else "already de-sequenced",
        conceptual_breakdown=["discipline testing", "pre-commissioning", "progressive testing",
                              "integrated testing", "final demonstration"],
        flag="PROGRESSIVE_T&C_REVIEW" if serial else None,
        recommendation=("Where the compliance regime permits, review progressive discipline testing "
                        "and pre-commissioning ahead of the integrated test so the integrated block "
                        "is not the only path to handover."),
        must_not=("Do NOT invent additional T&C activities without scope evidence. The breakdown "
                  "above is conceptual and is only instantiated if the scope supports it."),
    )


# ---------------------------------------------------------------------------
# FIX-08 — optimisation register
# ---------------------------------------------------------------------------
def build_optimisation_register(mob: Dict[str, Any], outlier: Dict[str, Any],
                                flow: Dict[str, Any], tc: Dict[str, Any],
                                drivers: Dict[str, Any]) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    n = [0]

    def add(category, current, issue, rec, benefit, risk, prereq):
        n[0] += 1
        entries.append(dict(
            id=f"OPT-{n[0]:02d}", category=category, current_logic=current,
            observed_issue=issue, recommendation=rec, potential_benefit=benefit,
            risk=risk, prerequisites=prereq, pm_approval_required=True,
            quantified=False,
            quantification_rule=("Any day saving must come from a cloned scenario re-solved by the "
                                 "CPM. Never 154d − guessed savings."),
            applied=False,
        ))

    if mob.get("status") in (SENIOR_PM_REVIEW_REQUIRED, EXTREME_OUTLIER, CONSERVATIVE_BUT_PLAUSIBLE):
        add("MOBILISATION_REVIEW",
            f'essential mobilisation = {mob.get("calculated")} working days',
            f'senior PM reference is {mob.get("senior_pm_reference")} for a normal CAT A→CAT B office',
            "review whether essential setup can finish inside the reference band and the remainder "
            "proceed in parallel with early construction",
            "earlier construction start; not quantified until re-solved",
            "landlord access, phasing, hoarding and temporary MEP constraints",
            "site access survey, landlord rules, temporary works scope")

    for opp in flow.get("opportunities", [])[:3]:
        add("SECTIONAL_HANDOVER",
            opp["current_logic"],
            opp["observed_issue"],
            f'roll zone release: {opp["recommended_overlap_concept"]}',
            "reduce the full-floor serial chain; not quantified until re-solved",
            opp["risk"], opp["required_pm_confirmation"])

    if tc.get("flag") == "PROGRESSIVE_T&C_REVIEW":
        add("PROGRESSIVE_T&C",
            f'T&C represented as one sequential block of {tc.get("duration_days")} days',
            "progressive discipline testing is not separated from the integrated test",
            "review progressive testing / pre-commissioning where the compliance regime permits",
            "shortens the integrated block; not quantified until re-solved",
            "must not weaken integrated-test or IAQ obligations",
            "T&C method statement and authority / client acceptance of progressive testing")

    for d in drivers.get("rows", [])[:3]:
        if d["primary_reason"] == WORKFRONT_CONSTRAINED:
            add("RESOURCE_REVIEW",
                f'{d["client_task_name"] or d["name"]} = {d["duration_days"]}d with feasible crew '
                f'{d["feasible_crew"]} < theoretical {d["theoretical_crew"]}',
                "feasible crew is capped by workfront capacity",
                "review the workfront plan / zone count with the contractor",
                "higher executable crew if genuine zones exist; not quantified until re-solved",
                "must be contractor-confirmed; capacity is not a commitment",
                "contractor workfront plan, zone layout evidence")

    if not entries:
        add("TRADE_FLOW", "calculated programme", "no material review trigger found",
            "retain the calculated baseline", "none identified", "none", "none")

    return dict(register=entries, count=len(entries),
                closed_loop=dict(
                    baseline_applied=False,
                    applied=False,
                    quantified=False,
                    re_solve_module="core.scenario_orchestrator",
                    persisted_on_default_run=False,
                    recommended_scenario=None,
                    note=("Register assembled; clone comparison is attached by "
                          "scenario_orchestrator.attach_register_comparisons on the Senior PM "
                          "review path. Proposals remain applied=false.")),
                categories_available=["MOBILISATION_REVIEW", "SECTIONAL_HANDOVER",
                                       "DESIGN_PACKAGE_RELEASE", "PROCUREMENT_OVERLAP",
                                       "TRADE_FLOW", "PROGRESSIVE_INSPECTION", "PROGRESSIVE_T&C",
                                       "FURNITURE_PHASING", "RESOURCE_REVIEW", "CALENDAR_REVIEW"],
                note=("The register is produced even when no target date exists. "
                      "Closed-loop comparison is not persisted on the default run; "
                      "proposals remain applied=false."))


# ---------------------------------------------------------------------------
# FIX-13 — senior PM executive review (the 10 questions)
# ---------------------------------------------------------------------------
CONDITIONALLY_CREDIBLE = "CONDITIONALLY_CREDIBLE"
FREIGHT_STATEMENT = "Multi-floor concurrency is conditional upon Landlord freight lift reservation."

# V6.6.0.2 — controlled governance language, keyed off the CANONICAL logistics state.
LOGISTICS_LANG_NOT_SPECIFIED = ("Vertical logistics capacity has not been specified and multi-floor "
                                "execution remains unverified.")
LOGISTICS_LANG_ONE_LIFT_UNKNOWN = ("One freight lift is declared. Throughput capacity remains "
                                   "unknown and concurrent multi-floor execution requires "
                                   "logistics confirmation.")
LOGISTICS_LANG_MULTI_LIFT_UNVERIFIED = ("Multiple freight lifts are declared. Throughput capacity "
                                        "remains unknown and vertical logistics feasibility is not "
                                        "verified.")

# --- floor-area provenance vocabulary (P0-01) --------------------------------
FLOOR_AREA_SOURCE_CANONICAL = "CANONICAL_SPATIAL_HIERARCHY"
FLOOR_AREA_SOURCE_BREAKDOWN = "EXPLICIT_FLOOR_BREAKDOWN"
FLOOR_AREA_SOURCE_ZONES = "UNIQUE_ZONE_FALLBACK"
FLOOR_AREA_SOURCE_UNKNOWN = "UNKNOWN"

WET_TRADES = ("wall_prep", "painting", "wallcovering", "tile_stone")
DRY_FINISH_TRADES = ("carpet", "vinyl", "raised_floor", "flooring")


def _positive_area(value: Any) -> Optional[float]:
    """A usable floor/zone area, or None. Never fabricates a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def resolve_floor_areas(tasks: Sequence[Dict[str, Any]],
                        floor_specs: Optional[Sequence[Dict[str, Any]]] = None,
                        spatial_hierarchy: Optional[Dict[str, Any]] = None
                        ) -> tuple:
    """V6.6.0.2 P0-01 — resolve ONE area per floor. Canonical first.

    Priority
    --------
    1. ``spatial_hierarchy["floors"]`` — the canonical Project → Floor → Zone
       hierarchy produced by the V6.6 spatial engine.
    2. ``floor_specs`` — an explicit ``--floor_breakdown``.
    3. Fallback, **UNIQUE ZONES only**.

    The fallback counts each ZONE once, whatever number of activities reference it.
    The previous implementation accumulated ``zone_area_sqm`` once per ACTIVITY, so a
    3,000 ㎡ floor with ~60 zonal activities reported ~180,000 ㎡. Filtering out
    ``None`` would have removed the crash without removing the inflation; this
    function never sums per-activity rows.

    A floor whose zones cannot ALL be resolved stays UNKNOWN rather than receiving a
    fabricated partial total.

    Returns ``(areas, sources, unknown_floors)``.
    """
    areas: Dict[str, Optional[float]] = {}
    sources: Dict[str, str] = {}
    unknown: List[str] = []

    def _put(fid: str, value: Optional[float], source: str) -> None:
        areas[fid] = value
        sources[fid] = source if value is not None else FLOOR_AREA_SOURCE_UNKNOWN
        if value is None:
            unknown.append(fid)

    # 1. canonical spatial hierarchy ---------------------------------------
    for f in ((spatial_hierarchy or {}).get("floors") or []):
        fid = f.get("floor_id")
        if fid is None:
            continue
        _put(str(fid), _positive_area(f.get("area_sqm")), FLOOR_AREA_SOURCE_CANONICAL)
    if any(v is not None for v in areas.values()):
        return areas, sources, sorted(set(unknown))

    # 2. explicit floor specification --------------------------------------
    if floor_specs:
        areas, sources, unknown = {}, {}, []
        for f in floor_specs:
            fid = f.get("floor_id")
            if fid is None:
                continue
            _put(str(fid), _positive_area(f.get("area_sqm")), FLOOR_AREA_SOURCE_BREAKDOWN)
        if any(v is not None for v in areas.values()):
            return areas, sources, sorted(set(unknown))

    # 3. fallback: UNIQUE ZONES ---------------------------------------------
    zone_area: Dict[tuple, Optional[float]] = {}
    for t in tasks:
        fid, zid = t.get("floor"), t.get("zone")
        if fid is None or zid is None:
            continue
        key = (str(fid), str(zid))
        a = _positive_area(t.get("zone_area_sqm"))
        if key not in zone_area or (zone_area[key] is None and a is not None):
            zone_area[key] = a

    per_floor_zones: Dict[str, List[Optional[float]]] = {}
    for (fid, _zid), a in zone_area.items():
        per_floor_zones.setdefault(fid, []).append(a)

    areas, sources, unknown = {}, {}, []
    for fid, values in per_floor_zones.items():
        if values and all(v is not None for v in values):
            _put(fid, round(float(sum(values)), 4), FLOOR_AREA_SOURCE_ZONES)
        else:
            _put(fid, None, FLOOR_AREA_SOURCE_UNKNOWN)   # incomplete -> UNKNOWN
    return areas, sources, sorted(set(unknown))


# Legacy V6.6.0 aliases. They may be READ as a last resort so an older payload cannot
# crash the review, but they can NEVER override a canonical field.
LEGACY_LOGISTICS_ALIASES = {
    "logistics_capacity_status": "logistics_status + logistics_capacity",
    "concurrency_gate": "logistics_concurrency_gate",
}
_LEGACY_STATUS_MAP = {
    "NOT_SPECIFIED": "NOT_SPECIFIED",
    "UNKNOWN": "SINGLE_LIFT_DECLARED",
    "DECLARED_MULTIPLE": "MULTIPLE_LIFTS_DECLARED",
}


def canonical_logistics_contract(logistics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """V6.6.0.2 P0-02 — read the CANONICAL V6.6.0.1 logistics contract.

    Canonical fields ALWAYS win. A legacy alias is consulted only when the canonical
    field is absent, and every alias actually used is reported in
    ``legacy_aliases_used`` so the substitution is auditable. ``logistics_capacity``
    is never derived from a declared lift count.
    """
    lg = dict(logistics or {})
    used: List[str] = []

    status = lg.get("logistics_status")
    if status is None:
        legacy_status = lg.get("logistics_capacity_status")
        if legacy_status is not None:
            used.append("logistics_capacity_status → logistics_status")
            status = _LEGACY_STATUS_MAP.get(str(legacy_status), str(legacy_status))

    capacity = lg.get("logistics_capacity")
    if capacity is None:
        used.append("logistics_capacity defaulted to UNKNOWN (a declared count is not a capacity)")
        capacity = "UNKNOWN"

    gate = lg.get("logistics_concurrency_gate")
    if gate is None and lg.get("concurrency_gate") is not None:
        used.append("concurrency_gate → logistics_concurrency_gate")
        gate = lg.get("concurrency_gate")

    verified = lg.get("logistics_feasibility_verified")
    if verified is None:
        used.append("logistics_feasibility_verified defaulted to false")
        verified = False

    return dict(
        logistics_status=status,
        logistics_capacity=capacity,
        logistics_feasibility_verified=bool(verified),
        logistics_concurrency_gate=gate,
        parallel_floor_execution=lg.get("parallel_floor_execution"),
        freight_lifts_count=lg.get("freight_lifts_count"),
        gate_applies_to=lg.get("gate_applies_to"),
        material_intensive_only=lg.get("material_intensive_only"),
        not_gated_scope=lg.get("not_gated_scope"),
        adds_fs_dependency=lg.get("adds_fs_dependency"),
        invented_delay_days=lg.get("invented_delay_days"),
        verification_statement=lg.get("verification_statement"),
        vertical_choke_warning=lg.get("vertical_choke_warning"),
        logistics_hold_point=lg.get("logistics_hold_point"),
        legacy_aliases_used=used,
    )


def multi_floor_logistics_review(tasks: Sequence[Dict[str, Any]],
                                 ctx: Optional[Dict[str, Any]] = None,
                                 logistics: Optional[Dict[str, Any]] = None,
                                 floor_specs: Optional[Sequence[Dict[str, Any]]] = None,
                                 spatial_hierarchy: Optional[Dict[str, Any]] = None,
                                 ) -> Dict[str, Any]:
    """V6.6.0 / V6.6.0.2 — floor takt balance, vertical water-tightness risk, transit exposure.

    Reads the solved/spatial task list and the canonical spatial hierarchy. It never
    changes a duration, dependency or crew; it reports what a Senior PM would challenge
    before signing the programme.
    """
    ctx = ctx or {}
    tasks = list(tasks or [])

    floors = sorted({str(t.get("floor")) for t in tasks if t.get("floor")})
    multi = len(floors) > 1
    zones_by_floor: Dict[str, set] = {}
    for t in tasks:
        if t.get("zone"):
            zones_by_floor.setdefault(str(t.get("floor")), set()).add(t["zone"])

    # --- 1. floor-to-floor takt balance ------------------------------------
    # P0-01: canonical hierarchy first; fallback = UNIQUE ZONES (never per activity).
    areas, area_sources, unknown_floors = resolve_floor_areas(
        tasks, floor_specs=floor_specs, spatial_hierarchy=spatial_hierarchy)
    known = {k: v for k, v in areas.items() if v is not None}
    vals = list(known.values())
    complete = bool(areas) and all(v is not None for v in areas.values())
    _sources = set(area_sources.values())
    takt = dict(floors=len(floors), floor_areas=areas,
                known_floor_areas=known,
                area_source_by_floor=area_sources,
                floor_area_source=(sorted(_sources)[0] if len(_sources) == 1 else "MIXED"),
                unknown_floors=unknown_floors,
                equal=(len(set(round(v, 4) for v in vals)) <= 1 if complete and vals else None),
                skew_pct=(round((max(vals) - min(vals)) / max(vals) * 100, 2)
                          if complete and vals and max(vals) else None),
                zones_per_floor={k: len(v) for k, v in sorted(zones_by_floor.items())})
    if not complete:
        takt["note"] = ("Floor area could not be reliably reconstructed for "
                        + (", ".join(unknown_floors) if unknown_floors else "one or more floors")
                        + ". Takt balance is NOT assessed rather than estimated from an "
                          "incomplete zone set.")
    elif takt["equal"] is False:
        takt["note"] = ("Floor areas are skewed. A rolling multi-floor flow takes its takt from the "
                        "LARGEST floor plate; the smaller floors finish early and their crews idle "
                        "unless they are redeployed. Confirm the crew redeployment plan.")

    # --- 2. vertical water-tightness risk ----------------------------------
    risk_rows: List[Dict[str, Any]] = []
    by_floor: Dict[str, Dict[str, Any]] = {}
    for t in tasks:
        at = t.get("activity_type")
        fl = str(t.get("floor")) if t.get("floor") else None
        if not at or not fl or not t.get("start") or not t.get("finish"):
            continue
        d = by_floor.setdefault(fl, {"wet": [], "dry": []})
        if at in WET_TRADES:
            d["wet"].append(t)
        elif at in DRY_FINISH_TRADES:
            d["dry"].append(t)
    for fl, d in sorted(by_floor.items()):
        if not d["wet"] or not d["dry"]:
            continue
        wet_start = min(str(t["start"]) for t in d["wet"])
        dry_finish = max(str(t["finish"]) for t in d["dry"])
        if wet_start <= dry_finish:
            risk_rows.append(dict(floor=fl, wet_trade_start=wet_start, dry_finish=dry_finish,
                                  vertical_order="SAME_FLOOR_CONCURRENT",
                                  detail=("wet finishes and dry finishes overlap on this floor — "
                                          "confirm sequence separation and protection")))
    # cross-floor: wet work on an upper floor while dry finishes are still open below
    ordered = sorted(by_floor.items())
    for (lo, dlo), (hi, dhi) in zip(ordered, ordered[1:]):
        if not dhi["wet"] or not dlo["dry"]:
            continue
        wet_start_hi = min(str(t["start"]) for t in dhi["wet"])
        lo_dry_finish = max(str(t["finish"]) for t in dlo["dry"])
        if wet_start_hi < lo_dry_finish:
            risk_rows.append(dict(
                floor=hi, wet_trade_start=wet_start_hi,
                dry_finish_floor_below=lo_dry_finish, lower_floor=lo,
                vertical_order="WET_ABOVE_OPEN_DRY_FINISH",
                detail=("wet finishes start on a higher floor while dry finishes below are still "
                        "not protected — water ingress risk to finished surfaces")))
    water = dict(checked=len(by_floor), risks=risk_rows, risk_count=len(risk_rows),
                 pass_=(len(risk_rows) == 0),
                 rule=("Wet trades above unprotected dry finishes are a water-ingress exposure, "
                       "not a scheduling preference."))

    # --- 3. vertical-transport exposure -------------------------------------
    # P0-02: decision source is the CANONICAL V6.6.0.1 contract. Legacy V6.6.0 keys are
    # read only if a canonical field is missing, and the substitution is recorded.
    lg = canonical_logistics_contract(logistics)
    status = lg["logistics_status"]
    cap = lg["logistics_capacity"]
    verified = lg["logistics_feasibility_verified"]
    gate = lg["logistics_concurrency_gate"]
    transit = lg["parallel_floor_execution"]

    if not multi:
        exposure = None
    elif status == "SINGLE_LIFT_DECLARED":
        exposure = ("heavy material across %d floors shares one vertical resource" % len(floors))
    elif status == "MULTIPLE_LIFTS_DECLARED":
        exposure = ("%s freight lifts declared across %d floors; capacity and booking windows are "
                    "not verified" % (lg["freight_lifts_count"], len(floors)))
    else:
        exposure = ("vertical logistics capacity not specified across %d floors; multi-floor "
                    "execution is unverified" % len(floors))

    lift_block = dict(
        freight_lifts_count=lg["freight_lifts_count"],
        logistics_status=status,
        logistics_capacity=cap,
        logistics_feasibility_verified=verified,
        logistics_concurrency_gate=gate,
        parallel_floor_execution=transit,
        gate_applies_to=lg["gate_applies_to"],
        material_intensive_only=lg["material_intensive_only"],
        not_gated_scope=lg["not_gated_scope"],
        adds_fs_dependency=lg["adds_fs_dependency"],
        invented_delay_days=lg["invented_delay_days"],
        verification_statement=lg["verification_statement"],
        legacy_aliases_used=lg["legacy_aliases_used"],
        multi_floor=multi,
        exposure=exposure,
        statement=(FREIGHT_STATEMENT if (multi and status == "SINGLE_LIFT_DECLARED") else None),
        hold_point=lg["logistics_hold_point"],
    )

    # --- classification -----------------------------------------------------
    # Keyed off the DECLARATION status. Capacity is UNKNOWN without verified evidence in
    # every branch, so it must never be the discriminator.
    if not multi:
        classification = "NOT_APPLICABLE_SINGLE_FLOOR"
        reasons = ["Single floor: no vertical logistics gate applies."]
    elif status == "NOT_SPECIFIED":
        classification = SENIOR_PM_REVIEW_REQUIRED
        reasons = [LOGISTICS_LANG_NOT_SPECIFIED,
                   "UNKNOWN is not UNCONSTRAINED — no logistics constraint has been applied to "
                   "the CPM and none has been verified."]
    elif status == "SINGLE_LIFT_DECLARED":
        classification = CONDITIONALLY_CREDIBLE
        reasons = [LOGISTICS_LANG_ONE_LIFT_UNKNOWN, FREIGHT_STATEMENT]
    elif not verified:
        classification = CONDITIONALLY_CREDIBLE
        reasons = [LOGISTICS_LANG_MULTI_LIFT_UNVERIFIED]
    else:
        classification = "CREDIBLE"
        reasons = ["Vertical logistics capacity is evidenced and verified."]

    return dict(
        checked=len(tasks), floors=floors, floor_count=len(floors),
        multi_floor=multi, takt_balance=takt, vertical_water_tightness=water,
        freight_lift_exposure=lift_block, classification=classification,
        classification_reasons=reasons,
        rule=("This audit SHORTENS nothing. It reports floor takt balance, vertical water "
              "sequencing and vertical-transport exposure for PM confirmation."),
    )


def build_senior_pm_review(tasks, solved, opt, ctx, benchmarks=None, mode="NORMAL",
                           logistics=None, floor_specs=None,
                           spatial_hierarchy=None) -> Dict[str, Any]:
    if benchmarks is None:
        benchmarks = load_benchmarks()
    gov = benchmarks.get("governance") or {}
    entries = benchmarks.get("benchmarks") or []
    sanity = programme_sanity_check(tasks, solved, opt, ctx, benchmarks)
    drivers = duration_driver_review(tasks)
    mob = mobilisation_review(sanity)
    outlier = construction_outlier_review(sanity, drivers, len(
        [t for t in tasks if t.get("duration_method") == "productivity_formula"]))
    area = ctx.get("area_sqm")
    flow = sectional_flow_review(tasks, area)
    tc = tc_review(tasks)
    cpm = critical_path_management(opt, tasks)
    register = build_optimisation_register(mob, outlier, flow, tc, drivers)
    from core.scenario_orchestrator import attach_register_comparisons
    start = (solved or {}).get("start_date") or (solved or {}).get("start")
    attach_register_comparisons(register, tasks, solved, opt, start_date=start)
    # V6.6.0 — multi-floor takt, vertical water sequencing, transit exposure.
    # V6.6.0.2 P0-01: the canonical spatial hierarchy is the preferred floor-area source.
    floor_block = multi_floor_logistics_review(tasks, ctx, logistics, floor_specs,
                                               spatial_hierarchy=spatial_hierarchy)

    unresolved = [t for t in tasks if t.get("unresolved_placeholder")
                  or t.get("duration_status") == "UNRESOLVED"]
    conditional = [t for t in tasks if t.get("conditional_requirement")]
    provisional_rates = [t for t in tasks
                         if (t.get("productivity") or {}).get("rate_status") == "provisional"]

    q = {}
    q[1] = ("Commercially credible as a calculated baseline, but the physical construction window "
            "exceeds the Senior PM planning reference and must be reviewed before it is presented "
            "as a committed programme.")
    conservative = [f'{r["domain"]}: {r["calculated_duration"]} vs reference '
                    f'{r["benchmark_low"]}–{r["benchmark_high"]} ({r["benchmark_status"]})'
                    for r in sanity["domains"] if r["benchmark_status"] == CONSERVATIVE_BUT_PLAUSIBLE]
    over_review = [f'{r["domain"]}: {r["calculated_duration"]} vs reference '
                   f'{r["benchmark_low"]}–{r["benchmark_high"]} ({r["benchmark_status"]})'
                   for r in sanity["domains"]
                   if r["benchmark_status"] in (SENIOR_PM_REVIEW_REQUIRED, EXTREME_OUTLIER)]
    q[2] = (conservative + [x for x in over_review if x not in conservative]
            or ["No domain exceeds the Senior PM reference band."])
    q[3] = ([f'{r["domain"]} is below 85% of the reference low band — confirm nothing is omitted'
             for r in sanity["domains"]
             if r["calculated_duration"] and r["benchmark_low"]
             and r["calculated_duration"] < 0.85 * r["benchmark_low"]] or
            ["Nothing identified as aggressive in the benchmarked domains."])
    q[4] = [f'{t.get("client_task_name") or t.get("name")} — requirement status To Be Confirmed'
            for t in unresolved[:8]] or ["No client decision outstanding."]
    q[5] = ["Workfront / zone plan and the executable crew for the constrained trades",
            "Contractor confirmation that the mobilisation window can be met",
            "Contractor resource availability (physical capacity is not a commitment)"]
    q[6] = [f'{t.get("client_task_name") or t.get("name")} — authority applicability pending'
            for t in conditional[:5]] or ["No authority decision outstanding."]
    q[7] = [f'{d["client_task_name"] or d["name"]} ({d["duration_days"]}d, {d["primary_reason"]})'
            for d in drivers["rows"][:5]]
    q[8] = [f'{e["id"]} {e["category"]} — {e["recommendation"]}' for e in register["register"][:5]]
    q[9] = ["Confirm the workfront / zone plan with the contractor",
            "Run a cloned sectional-flow scenario and re-solve the CPM (never arithmetic savings)",
            "Confirm landlord access constraints that drive the mobilisation window",
            "Take the unresolved conditional activities to the client / authority"]
    q[10] = ["Do NOT change productivity rates, workfront mathematics, shift governance, "
             "authority logic or the CPM to shorten the programme.",
             "Do NOT relax FS/SS logic without an approved, re-solved scenario.",
             "Do NOT delete UNKNOWN activities to tidy the presentation."]
    q[11] = ([f'FLOOR TAKT: {floor_block["takt_balance"].get("note") or "floor plates are balanced — takt carries cleanly."}']
             if floor_block["multi_floor"] else ["Single floor — no floor-to-floor takt question."])
    q[12] = ([floor_block["freight_lift_exposure"]["statement"]]
             if floor_block["freight_lift_exposure"].get("statement")
             else ["No vertical logistics gate active on this run."])
    q[13] = ([f'{r["vertical_order"]} @ {r["floor"]}: {r["detail"]}'
              for r in floor_block["vertical_water_tightness"]["risks"][:3]]
             or ["No vertical water-ingress sequencing conflict detected."])
    q[14] = [f'Multi-floor classification: {floor_block["classification"]}'
             ] + [f'  · {r}' for r in floor_block["classification_reasons"][:3]]

    return dict(
        engine="V6.4.4.1 (frozen, unmodified)", layer="V6.6.0 Senior PM Intelligence (multi-floor)",
        mode=mode, context=ctx, generated_from="calculated programme only",
        benchmark_file=dict(
            status=benchmarks.get("status"),
            provenance_status=(benchmarks.get("provenance") or {}).get("status"),
            entry_count=len(entries),
            derived_from=(benchmarks.get("provenance") or {}).get("derived_from"),
            note=("Reference bands are available and every band resolves to a shipped source."
                  if entries else
                  "No reference band is available in this pack. Every benchmarked domain reports "
                  "INSUFFICIENT_BENCHMARK_DATA; supply signed or historical data to enable review."),
        ),
        benchmark_governance=dict(
            rule=gov.get("rule"),
            no_fabrication=gov.get("no_fabrication"),
            no_overstatement=gov.get("no_overstatement"),
            benchmark_file_status=benchmarks.get("status"),
            provenance_status=(benchmarks.get("provenance") or {}).get("status"),
            entry_count=len(entries),
            source_refs=(benchmarks.get("provenance") or {}).get("source_refs") or [],
            confidence=entries[0].get("confidence") if entries else None,
        ),
        programme_sanity_check=sanity, duration_drivers=drivers,
        mobilisation_review=mob, construction_outlier_review=outlier,
        sectional_flow_review=flow, tc_review=tc, critical_path_management=cpm,
        optimisation_register=register,
        multi_floor_logistics=floor_block,
        counts=dict(unresolved=len(unresolved), conditional=len(conditional),
                    provisional_rate_tasks=len(provisional_rates)),
        executive_review=q,
        review_outcomes=[ACCEPT_RECOMMENDATION, KEEP_CALCULATED_BASELINE,
                         PROVIDE_PROJECT_DATA, REJECT_RECOMMENDATION],
        recommendation_language="PM Review / Programme Review / Recommended Action / "
                                "Key Programme Exposure / Contractor Confirmation Required / "
                                "Client Decision Required",
        baseline_immutable=True,
        baseline_note=("No calculated duration, dependency, crew, rate or calendar was modified by "
                       "this layer. Accepting a recommendation requires a cloned scenario re-solved "
                       "by the CPM."),
    )


# ---------------------------------------------------------------------------
# markdown renderer (FIX-13 / FIX-14)
# ---------------------------------------------------------------------------
def render_markdown(rv: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append("# SENIOR_PM_REVIEW")
    L.append("")
    L.append(f"Engine: **{rv['engine']}** ｜ Layer: **{rv['layer']}** ｜ Mode: **{rv['mode']}**")
    L.append("")
    L.append("> " + rv["baseline_note"])
    L.append("")
    bf = rv.get("benchmark_file") or {}
    L.append(f'Reference bands: **{bf.get("status") or "—"}** '
             f'({bf.get("entry_count") or 0} band(s), source: {bf.get("derived_from") or "—"})')
    L.append("")
    L.append("> " + str(bf.get("note") or ""))
    L.append("")
    L.append("## 1. Programme sanity check")
    L.append("")
    L.append("| Domain | Calculated | Reference (low / typical / high) | Status | Basis | Confidence |")
    L.append("|---|---|---|---|---|---|")
    for r in rv["programme_sanity_check"]["domains"]:
        ref = ("—" if r["benchmark_low"] is None
               else f'{r["benchmark_low"]} / {r["benchmark_typical"]} / {r["benchmark_high"]}'
               + (" d" if r.get("unit") == "working_days" else ""))
        L.append(f'| {r["domain"]} | {r["calculated_duration"]} | {ref} | '
                 f'**{r["benchmark_status"]}** | {r.get("basis") or "—"} | {r.get("confidence") or "—"} |')
    L.append("")
    L.append("### Mobilisation / site protection — PM Review")
    m = rv["mobilisation_review"]
    if m.get("status"):
        L.append(f'- Calculated: **{m.get("calculated")}** working days ｜ Senior PM reference: '
                 f'**{m.get("senior_pm_reference")}** ｜ Status: **{m.get("status")}**')
        L.append("- Review questions: " + "; ".join(m.get("review_questions", [])))
        L.append(f'- Recommended Action: {m.get("recommendation")}')
        L.append(f'- {m.get("must_not")}')
    L.append("")
    L.append("### Physical construction — outlier review")
    o = rv["construction_outlier_review"]
    if o.get("status"):
        ref = o.get("reference") or {}
        L.append(f'- Calculated: **{o.get("calculated")}** working days ｜ Reference: typical '
                 f'≈{ref.get("typical_label") or ref.get("typical")}, upper '
                 f'≈{ref.get("high_label") or ref.get("high")} ｜ Status: **{o.get("status")}**')
        L.append("")
        L.append("| # | Top driver | Duration | Reason |")
        L.append("|---|---|---|---|")
        for d in o.get("top_drivers", []):
            L.append(f'| {d["rank"]} | {d["name"]} | {d["duration_days"]} d | '
                     f'**{d["reason"]}** — {d["detail"]} |')
    L.append("")
    L.append("## 2. Duration driver review")
    L.append("")
    L.append("| Task | Duration | Theoretical | Feasible | Capacity | Congestion | Primary reason |")
    L.append("|---|---|---|---|---|---|---|")
    for d in rv["duration_drivers"]["rows"]:
        L.append(f'| {d["client_task_name"] or d["name"]} | {d["duration_days"]} d | '
                 f'{d["theoretical_crew"]} | {d["feasible_crew"]} | {d["workfront_capacity"]} | '
                 f'{d["congestion"]} | **{d["primary_reason"]}** |')
    L.append("")
    L.append("## 3. Sectional / flow construction review")
    L.append("")
    f = rv["sectional_flow_review"]
    L.append(f'Executable zones (planning): **{f["executable_zones"]}** (≈{f["zone_area_sqm"]} sqm each, '
             f'minimum practical {f["minimum_practical_zone_sqm"]} sqm) ｜ source: {f["source_type"]}')
    L.append(f'Instantiated zones: **{f.get("actual_zone_count", 0)}** ｜ spatial pipeline active: '
             f'**{f.get("spatial_pipeline_active", False)}**')
    L.append("")
    for opp in f["opportunities"]:
        cls = opp.get("classification") or opp.get("code")
        L.append(f'- **{opp["code"]}** ({cls}) — {opp["candidate_predecessor"]["name"]} → '
                 f'{opp["candidate_successor"]["name"]}')
        L.append(f'  - Current logic: {opp["current_logic"]}')
        L.append(f'  - Observed issue: {opp["observed_issue"]}')
        L.append(f'  - Physical release condition: {opp["physical_release_condition"]}')
        L.append(f'  - Recommended overlap concept: {opp["recommended_overlap_concept"]}')
        L.append(f'  - Risk: {opp["risk"]}')
        L.append(f'  - PM confirmation required: {opp["required_pm_confirmation"]}')
    for intact in f.get("intact_releases") or []:
        L.append(f'- **{intact["code"]}** — {intact["candidate_predecessor"]["name"]} → '
                 f'{intact["candidate_successor"]["name"]}')
        L.append(f'  - Current logic: {intact["current_logic"]}')
        L.append(f'  - Observed issue: {intact["observed_issue"]}')
    if not f["opportunities"] and not f.get("intact_releases"):
        L.append("- No sectional-flow opportunity identified.")
    L.append("")
    L.append("## 4. T&C review")
    t = rv["tc_review"]
    L.append(f'- {t.get("name")}: {t.get("duration_days")} d, representation = '
             f'**{t.get("representation")}**' + (f', flag = **{t.get("flag")}**' if t.get("flag") else ""))
    L.append(f'- Recommended Action: {t.get("recommendation")}')
    L.append(f'- {t.get("must_not")}')
    L.append("")
    L.append("## 5. Critical path → management action")
    c = rv["critical_path_management"]
    L.append(f'- Critical tasks: **{c["critical_task_count"]}** ｜ driving chain: '
             f'**{c["driving_chain_length"]}**')
    L.append(f'- Key Programme Exposure: {c["why_critical"]}')
    L.append(f'- PM Action: {c["pm_action"]}')
    L.append("- Do not compromise: " + ", ".join(c["must_not_compromise"]))
    L.append("")
    L.append("| # | Critical activity | Duration | Float |")
    L.append("|---|---|---|---|")
    for x in c["top_critical_activities"]:
        L.append(f'| {x["rank"]} | {x["name"]} | {x["duration_days"]} d | {x["float_days"]} |')
    L.append("")
    L.append("## 6. PM optimisation register")
    L.append("")
    L.append("| ID | Category | Current logic | Recommendation | Risk | PM approval |")
    L.append("|---|---|---|---|---|---|")
    for e in rv["optimisation_register"]["register"]:
        L.append(f'| {e["id"]} | {e["category"]} | {e["current_logic"]} | {e["recommendation"]} | '
                 f'{e["risk"]} | required |')
    L.append("")
    L.append("Clone re-solve (copies only; baseline unchanged; no scenario auto-selected):")
    L.append("")
    for e in rv["optimisation_register"]["register"]:
        cc = e.get("clone_comparison") or {}
        L.append(f'- **{e["id"]}** `{cc.get("status")}` applied={cc.get("applied", False)}')
        if cc.get("finish_before") or cc.get("finish_after"):
            L.append(f'  - finish before/after: {cc.get("finish_before")} → {cc.get("finish_after")}')
        if cc.get("driving_chain_length_before") is not None:
            L.append(f'  - driving chain length before/after: '
                     f'{cc.get("driving_chain_length_before")} → {cc.get("driving_chain_length_after")}')
        if cc.get("labelling"):
            L.append(f'  - {cc["labelling"]}')
        if cc.get("reason"):
            L.append(f'  - {cc["reason"]}')
        if cc.get("risk"):
            L.append(f'  - risk: {cc["risk"]}')
        for loc in cc.get("evidence_fields") or []:
            L.append(f'  - evidence: `{loc}`')
    L.append("")
    L.append("## 7. Executive review")
    L.append("")
    q = rv["executive_review"]
    titles = {
        1: "Is this programme commercially credible?",
        2: "What looks conservative?", 3: "What looks aggressive?",
        4: "What requires client confirmation?", 5: "What requires contractor confirmation?",
        6: "What requires authority confirmation?", 7: "What are the top 5 programme drivers?",
        8: "What are the top optimisation opportunities?", 9: "What should the PM do next?",
        10: "What should NOT be changed without evidence?",
        11: "Is the multi-floor takt balanced?", 12: "What is the vertical logistics exposure?",
        13: "Is there a vertical water-ingress risk?",
        14: "What is the multi-floor classification?",
    }
    for i in range(1, 15):
        # JSON round-trip turns the integer question keys into strings
        val = q.get(i, q.get(str(i)))
        L.append(f'**{i}. {titles[i]}**')
        if isinstance(val, list):
            for v in val:
                L.append(f'- {v}')
        else:
            L.append(f'- {val}')
        L.append("")
    mfl = rv.get("multi_floor_logistics") or {}
    if mfl.get("multi_floor"):
        L.append("## 7b. Multi-floor & vertical logistics (V6.6.0)")
        L.append("")
        L.append(f"Classification: **{mfl.get('classification')}** — "
                 + "; ".join(mfl.get("classification_reasons") or []))
        L.append("")
        L.append("| Floor | Area (㎡) | Zones | Area source |")
        L.append("|---|---|---|---|")
        _src = mfl["takt_balance"].get("area_source_by_floor") or {}
        for fid, area in sorted((mfl["takt_balance"].get("floor_areas") or {}).items()):
            shown = f"{area:g}" if isinstance(area, (int, float)) else "UNKNOWN"
            L.append(f"| {fid} | {shown} | "
                     f"{mfl['takt_balance']['zones_per_floor'].get(fid, 0)} | "
                     f"{_src.get(fid, 'n/a')} |")
        L.append("")
        if mfl["takt_balance"].get("note"):
            L.append(f"- {mfl['takt_balance']['note']}")
        wa = mfl.get("vertical_water_tightness") or {}
        L.append(f"- Vertical water-tightness: {'PASS' if wa.get('pass_') else 'REVIEW'} "
                 f"({wa.get('risk_count', 0)} risk row(s))")
        for r in (wa.get("risks") or [])[:5]:
            L.append(f"  - {r.get('vertical_order')} @ {r.get('floor')}: {r.get('detail')}")
        fe = mfl.get("freight_lift_exposure") or {}
        L.append(f"- Freight lifts: {fe.get('freight_lifts_count')} · "
                 f"status {fe.get('logistics_status')} · capacity {fe.get('logistics_capacity')} · "
                 f"feasibility verified {fe.get('logistics_feasibility_verified')} · "
                 f"gate {fe.get('logistics_concurrency_gate')}")
        if fe.get("statement"):
            L.append(f"  - **{fe['statement']}**")
        if fe.get("verification_statement"):
            L.append(f"  - {fe['verification_statement']}")
        if fe.get("gate_applies_to"):
            L.append(f"  - Gate scope: {fe.get('gate_applies_to')} "
                     f"(material-intensive only; FS dependencies added: "
                     f"{fe.get('adds_fs_dependency')}; invented delay: "
                     f"{fe.get('invented_delay_days')} d)")
        if fe.get("not_gated_scope"):
            L.append(f"  - {fe['not_gated_scope']}")
        if fe.get("hold_point"):
            L.append(f"  - {fe['hold_point']}")
        if fe.get("legacy_aliases_used"):
            L.append("  - Legacy logistics aliases consulted (canonical fields were absent, "
                     f"aliases never override canonical): {fe['legacy_aliases_used']}")
        L.append("")
    L.append("## 8. Review outcomes")
    L.append("")
    L.append("Reviewable actions: " + ", ".join(rv["review_outcomes"]) + ".")
    L.append("")
    L.append("> " + rv["optimisation_register"]["note"])
    cl = (rv["optimisation_register"] or {}).get("closed_loop") or {}
    if cl:
        L.append(">")
        L.append(f'> Closed loop: persisted_on_default_run={cl.get("persisted_on_default_run")}; '
                 f'applied={cl.get("applied")}; quantified={cl.get("quantified")}; '
                 f'recommended_scenario={cl.get("recommended_scenario")}. '
                 f'{cl.get("note") or ""}')
    L.append("")
    return "\n".join(L)
