# -*- coding: utf-8 -*-
"""
core/quantity_engine.py — Quantity Engine v0 (benchmark quantities without a BOQ)

Given gross fit-out area + building grade (+ optional layout / parameter overrides),
derive the small set of quantities the productivity path needs:

    ceiling_area, flooring_area, partition_length, partition_area,
    drywall_partition_area, glass_partition_area, paint_area

Formulas are fixed here; every number (ratios, heights, shares, sanity bands) lives
only in config/quantity_benchmarks.json. Same inputs → same outputs, no LLM, no I/O
besides reading that file. Output is explainable: each quantity carries its formula
with substituted values, its unit, `quantity_source = "derived_from_area"` and a
`confidence` of low, so downstream productivity confidence is downgraded exactly as
for the legacy `area × ratio` bridge.

Measured BOQ always wins over these numbers — see core.productivity hook
(`apply_productivity_durations(..., derived_quantities=...)`).
"""
import os
import json
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCHMARKS_PATH = os.path.join(BASE_DIR, "config", "quantity_benchmarks.json")

METHOD_BENCHMARK = "benchmark_v0"

# Names of the quantities this engine derives, in output order.
QUANTITY_KEYS = (
    "ceiling_area",
    "flooring_area",
    "partition_length",
    "partition_area",
    "drywall_partition_area",
    "glass_partition_area",
    "paint_area",
)

_REQUIRED_PARAMS = (
    "net_ceiling_ratio",
    "net_floor_ratio",
    "partition_density_m_per_m2",
    "partition_height_m",
    "glass_partition_share",
    "core_wall_density_m_per_m2",
    "painted_face_fraction",
    "painted_ceiling_share",
)

# Parameters that are fractions in [0, 1]; everything else must simply be > 0.
_FRACTION_PARAMS = (
    "net_ceiling_ratio",
    "net_floor_ratio",
    "glass_partition_share",
    "painted_face_fraction",
    "painted_ceiling_share",
)


def load_quantity_benchmarks(path: Optional[str] = None) -> Dict[str, Any]:
    """Load the benchmark library. Raises on missing/invalid file (fail loudly, no silent defaults)."""
    p = path or BENCHMARKS_PATH
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    grades = data.get("grades")
    if not isinstance(grades, dict) or not grades:
        raise ValueError(f"quantity benchmarks file {p} lacks a non-empty 'grades' map")
    for g, body in grades.items():
        params = (body or {}).get("params", {})
        missing = [k for k in _REQUIRED_PARAMS if k not in params]
        if missing:
            raise ValueError(f"grade '{g}' in {p} lacks params {missing}")
    if not isinstance(data.get("layouts"), dict) or not data["layouts"]:
        raise ValueError(f"quantity benchmarks file {p} lacks a non-empty 'layouts' map")
    return data


def normalize_grade(grade: Optional[str], benchmarks: Optional[Dict[str, Any]] = None) -> str:
    """Map user spellings ('grade a', '甲级', 'A') to a canonical grade key. Raises on unknown."""
    b = benchmarks or load_quantity_benchmarks()
    if grade is None or str(grade).strip() == "":
        return b.get("default_grade", "A")
    raw = str(grade).strip()
    if raw in b["grades"]:
        return raw
    key = raw.lower().replace("-", " ").replace("  ", " ")
    aliases = {str(k).lower(): v for k, v in (b.get("grade_aliases") or {}).items()}
    if key in aliases and aliases[key] in b["grades"]:
        return aliases[key]
    if raw.upper() in b["grades"]:
        return raw.upper()
    raise ValueError(f"unknown building grade '{grade}'; allowed: {sorted(b['grades'])} (or aliases {sorted(aliases)})")


def normalize_layout(layout: Optional[str], benchmarks: Optional[Dict[str, Any]] = None) -> str:
    b = benchmarks or load_quantity_benchmarks()
    if layout is None or str(layout).strip() == "":
        return b.get("default_layout", "standard")
    key = str(layout).strip().lower().replace("-", "_").replace(" ", "_")
    if key not in b["layouts"]:
        raise ValueError(f"unknown layout '{layout}'; allowed: {sorted(b['layouts'])}")
    return key


def resolve_parameters(
    grade: Optional[str] = None,
    layout: Optional[str] = None,
    overrides: Optional[Dict[str, float]] = None,
    benchmarks: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Grade params + layout multiplier + caller overrides → one validated parameter set.

    Returns {"grade", "layout", "params", "overrides_applied", "assumptions"}.
    Overrides may only name known parameters; values are validated (fractions in [0,1], others > 0).
    """
    b = benchmarks or load_quantity_benchmarks()
    g = normalize_grade(grade, b)
    lay = normalize_layout(layout, b)
    params = {k: float(v) for k, v in b["grades"][g]["params"].items()}
    params["layout_partition_density_multiplier"] = float(b["layouts"][lay].get("partition_density_multiplier", 1.0))

    applied: Dict[str, float] = {}
    for k, v in (overrides or {}).items():
        if k not in params:
            raise ValueError(f"unknown quantity parameter override '{k}'; allowed: {sorted(params)}")
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"override '{k}' must be numeric, got {v!r}")
        params[k] = fv
        applied[k] = fv

    for k in _FRACTION_PARAMS:
        if not (0.0 <= params[k] <= 1.0):
            raise ValueError(f"parameter '{k}' must be within [0, 1], got {params[k]}")
    for k, v in params.items():
        if k in _FRACTION_PARAMS:
            continue
        if not (v > 0):
            raise ValueError(f"parameter '{k}' must be > 0, got {v}")

    assumptions: List[str] = list(b["grades"][g].get("assumptions", []))
    if lay != b.get("default_layout", "standard"):
        assumptions.append(
            f"layout '{lay}' ({b['layouts'][lay].get('label', lay)}) multiplies partition density by "
            f"{params['layout_partition_density_multiplier']:g}"
        )
    for k, v in applied.items():
        assumptions.append(f"caller override: {k} = {v:g} (replaces grade {g} benchmark)")
    return {"grade": g, "layout": lay, "params": params, "overrides_applied": applied, "assumptions": assumptions}


def _q(value: float, unit: str, formula: str, components: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    row = {"value": round(value, 1), "unit": unit, "formula": formula}
    if components:
        row["components"] = {k: round(v, 1) for k, v in components.items()}
    return row


def derive_quantities(
    area_sqm: float,
    grade: Optional[str] = None,
    layout: Optional[str] = None,
    overrides: Optional[Dict[str, float]] = None,
    benchmarks: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Derive benchmark quantities for one fit-out from gross area + grade (+ layout / overrides).

    Returns an explainable dict:
        method, benchmarks_version, inputs, parameters, quantities{name: {value, unit, formula, ...}},
        quantity_source, confidence, assumptions, warnings
    Raises ValueError on bad input (area <= 0, unknown grade/layout/override) — callers decide fallback.
    """
    b = benchmarks or load_quantity_benchmarks()
    try:
        area = float(area_sqm)
    except (TypeError, ValueError):
        raise ValueError(f"area_sqm must be numeric, got {area_sqm!r}")
    if not (area > 0):
        raise ValueError(f"area_sqm must be > 0, got {area}")

    res = resolve_parameters(grade, layout, overrides, b)
    p = res["params"]
    units = {k: v.get("unit", "") for k, v in (b.get("quantities") or {}).items()}

    ceiling_area = area * p["net_ceiling_ratio"]
    flooring_area = area * p["net_floor_ratio"]
    partition_length = area * p["partition_density_m_per_m2"] * p["layout_partition_density_multiplier"]
    partition_area = partition_length * p["partition_height_m"]
    drywall_partition_area = partition_area * (1.0 - p["glass_partition_share"])
    glass_partition_area = partition_area * p["glass_partition_share"]
    wall_paint_area = drywall_partition_area * 2.0 * p["painted_face_fraction"]
    core_wall_paint_area = area * p["core_wall_density_m_per_m2"] * p["partition_height_m"] * p["painted_face_fraction"]
    ceiling_paint_area = ceiling_area * p["painted_ceiling_share"]
    paint_area = wall_paint_area + core_wall_paint_area + ceiling_paint_area

    quantities: Dict[str, Dict[str, Any]] = {
        "ceiling_area": _q(ceiling_area, units.get("ceiling_area", "m2"),
                           f"{area:g} × net_ceiling_ratio {p['net_ceiling_ratio']:g}"),
        "flooring_area": _q(flooring_area, units.get("flooring_area", "m2"),
                            f"{area:g} × net_floor_ratio {p['net_floor_ratio']:g}"),
        "partition_length": _q(partition_length, units.get("partition_length", "m"),
                               f"{area:g} × partition_density {p['partition_density_m_per_m2']:g} m/m2 "
                               f"× layout multiplier {p['layout_partition_density_multiplier']:g}"),
        "partition_area": _q(partition_area, units.get("partition_area", "m2"),
                             f"partition_length {partition_length:g} × partition_height {p['partition_height_m']:g} m"),
        "drywall_partition_area": _q(drywall_partition_area, units.get("drywall_partition_area", "m2"),
                                     f"partition_area {partition_area:g} × (1 − glass share {p['glass_partition_share']:g})"),
        "glass_partition_area": _q(glass_partition_area, units.get("glass_partition_area", "m2"),
                                   f"partition_area {partition_area:g} × glass share {p['glass_partition_share']:g}"),
        "paint_area": _q(paint_area, units.get("paint_area", "m2"),
                         f"drywall {drywall_partition_area:g} × 2 faces × painted fraction {p['painted_face_fraction']:g} "
                         f"+ core walls {area:g} × {p['core_wall_density_m_per_m2']:g} m/m2 × {p['partition_height_m']:g} m × {p['painted_face_fraction']:g} "
                         f"+ ceiling {ceiling_area:g} × painted ceiling share {p['painted_ceiling_share']:g}",
                         components={"wall_paint_area": wall_paint_area,
                                     "core_wall_paint_area": core_wall_paint_area,
                                     "ceiling_paint_area": ceiling_paint_area}),
    }
    source = b.get("quantity_source", "derived_from_area")
    confidence = b.get("confidence", "low")
    for name, row in quantities.items():
        row["quantity_source"] = source
        row["confidence"] = confidence
        row["per_gross_m2"] = round(row["value"] / area, 4)

    warnings: List[str] = []
    bands = b.get("sanity_ranges_per_gross_m2") or {}
    for name, row in quantities.items():
        band = bands.get(name)
        if isinstance(band, (list, tuple)) and len(band) == 2:
            lo, hi = float(band[0]), float(band[1])
            ratio = row["per_gross_m2"]
            row["sanity_band_per_gross_m2"] = [lo, hi]
            row["within_sanity_band"] = lo <= ratio <= hi
            if not row["within_sanity_band"]:
                warnings.append(
                    f"{name} = {row['value']:g}{row['unit']} is {ratio:g} per gross m2, outside benchmark band [{lo:g}, {hi:g}]"
                )

    return {
        "method": METHOD_BENCHMARK,
        "benchmarks_version": b.get("version", "unknown"),
        "inputs": {"area_sqm": area, "grade": res["grade"], "layout": res["layout"], "overrides": res["overrides_applied"]},
        "parameters": p,
        "quantities": quantities,
        "quantity_source": source,
        "confidence": confidence,
        "assumptions": res["assumptions"],
        "warnings": warnings,
    }


def quantity_values(result: Dict[str, Any]) -> Dict[str, float]:
    """Flatten a derive_quantities() result to {name: value} for the productivity hook.

    P0-03: rows whose value is UNKNOWN (None — e.g. demolition under an
    unconfirmed site condition) are omitted, never coerced to a fake number.
    """
    return {k: float(v["value"]) for k, v in (result.get("quantities") or {}).items()
            if v.get("value") is not None}


def derive_fitout_quantities(area_sqm, office_type="standard", fitout_grade="standard", grade=None,
                            site_condition='unknown', demolition_fraction=None):
    """Extended provisional takeoff; legacy seven-quantity API remains stable."""
    import math
    b = load_quantity_benchmarks()
    if fitout_grade not in b['fitout_profiles']:
        raise ValueError('unknown fitout_grade')
    if not math.isfinite(float(area_sqm)):
        raise ValueError('area must be finite')
    result = derive_quantities(area_sqm, grade=grade, layout=office_type, benchmarks=b)
    # P0-03: UNKNOWN is not full-strip. Vocabulary is explicit; each token maps to a
    # declared demolition fraction, and UNKNOWN maps to None (quantity not invented).
    SITE_CONDITIONS = ('unknown', 'shell', 'cat_a', 'cat_b_existing',
                       'renovation_partial', 'full_strip', 'partial')
    if site_condition not in SITE_CONDITIONS:
        raise ValueError('unknown site_condition')
    if site_condition in ('partial', 'renovation_partial') and demolition_fraction is None:
        raise ValueError('partial renovation requires an explicit demolition_fraction')
    fraction = {'shell': 0.0, 'cat_a': 0.0, 'cat_b_existing': 1.0, 'full_strip': 1.0}.get(site_condition)
    if site_condition in ('partial', 'renovation_partial'):
        fraction = demolition_fraction
    if demolition_fraction is not None and site_condition not in ('partial', 'renovation_partial'):
        raise ValueError('demolition_fraction applies only to partial renovation')
    if fraction is not None and (isinstance(fraction, bool) or not math.isfinite(float(fraction)) or not 0 <= float(fraction) <= 1):
        raise ValueError('demolition_fraction must be finite in [0,1]')
    values = quantity_values(result)
    values['area'] = float(area_sqm)
    for key, spec in b['extended_quantities'].items():
        multiplier = b['fitout_profiles'][fitout_grade] if spec['profile_sensitive'] else 1
        if spec['office_sensitive']:
            multiplier *= b['layouts'][result['inputs']['layout']]['partition_density_multiplier']
        value = values[spec['basis']] * spec['ratio'] * multiplier
        value = math.ceil(value) if spec['unit'] == 'each' else round(value, 1)
        values[key] = value
        result['quantities'][key] = dict(value=value, unit=spec['unit'],
            formula=f"{spec['basis']} ({values[spec['basis']]}) * {spec['ratio']} * {multiplier}",
            quantity_source='derived_from_area', confidence='low', status='provisional', source=spec['source'])
    result['inputs'].update(office_type=office_type, fitout_grade=fitout_grade)
    result['quantities']['demolition_area']=dict(result['quantities']['gross_area'])
    # P0-03 scenario disclosure bases, captured before fraction scaling.
    base_demolition_area = result['quantities']['demolition_area']['value']
    base_waste_volume = result['quantities']['waste_volume']['value']
    for key in ('demolition_area','waste_volume'):
        row=result['quantities'][key]
        if fraction is None:
            # UNKNOWN: the engine must not invent a demolition quantity.
            row['value']=None
            row['status']='SITE_CONDITION_UNCONFIRMED'
            row['formula'] += ' * demolition_fraction UNKNOWN (site condition unconfirmed)'
            row['scope_note']=('Demolition scope UNKNOWN. Rerun with an explicit --site_condition '
                               '(shell|cat_a|cat_b_existing|renovation_partial|full_strip) to obtain quantities.')
        else:
            row['value']=round(row['value']*float(fraction),1)
            row['formula'] += f' * demolition_fraction {fraction}'
            row['scope_note']='Demolition scope only; fit-out construction cleaning is separate.'
    result['inputs'].update(site_condition=site_condition,
                            demolition_fraction=(float(fraction) if fraction is not None else None))
    if site_condition == 'unknown':
        result['warnings'].append(
            'SITE CONDITION UNCONFIRMED: demolition quantity is UNKNOWN and was not invented. '
            'The presented schedule excludes demolition (scenario A, no auto-selection); confirm '
            'the site condition and rerun, or evaluate scenario B via --site_condition full_strip.')
        result['site_condition_scenarios'] = {
            'status': 'SITE_CONDITION_UNCONFIRMED',
            'selected': None,
            'note': ('Diagnostic scenario pair only; neither is auto-selected. Scenario A is what the '
                     'presented schedule assumes; scenario B quantities are the full-strip upper bound.'),
            'A_shell': {'demolition_area': 0.0, 'waste_volume': 0.0},
            'B_full_strip': {'demolition_area': round(base_demolition_area, 1),
                             'waste_volume': round(base_waste_volume, 1),
                             'obtain_by': 'rerun with --site_condition full_strip'},
        }
    prepared = result['quantities']['wall_prep_area']['value']
    covering = result['quantities']['wallcovering_area']['value']
    painted = round(prepared - covering, 1)
    if painted <= 0:
        raise ValueError('Wallcovering allowance exceeds prepared surface')
    result['quantities']['paint_area'] = dict(value=painted, unit='m2',
        formula=f'prepared surface {prepared} - wallcovering surface {covering}',
        quantity_source='derived_from_area', confidence='low', status='provisional',
        source='Finish allocation excludes wallcovering from painted area')
    return result


def as_quantity_map(derived: Any) -> Dict[str, float]:
    """Accept a derive_quantities() result, a {name: value} map or None → {name: value}."""
    if not derived:
        return {}
    if isinstance(derived, dict) and "quantities" in derived and isinstance(derived["quantities"], dict):
        return quantity_values(derived)
    if isinstance(derived, dict):
        out: Dict[str, float] = {}
        for k, v in derived.items():
            if isinstance(v, dict) and "value" in v:
                if v["value"] is None:      # P0-03: UNKNOWN quantities are never coerced
                    continue
                out[k] = float(v["value"])
            elif isinstance(v, (int, float)):
                out[k] = float(v)
        return out
    raise ValueError(f"derived quantities must be a dict, got {type(derived).__name__}")


def quantity_table(result: Dict[str, Any]) -> List[str]:
    """Human-readable lines for logs."""
    lines = []
    inp = result.get("inputs", {})
    lines.append(
        f"[quantity] {result.get('method')} v{result.get('benchmarks_version')}: gross {inp.get('area_sqm'):g} m2, "
        f"grade {inp.get('grade')}, layout {inp.get('layout')}, overrides {inp.get('overrides') or {}}"
    )
    for name, row in result.get("quantities", {}).items():
        flag = "" if row.get("within_sanity_band", True) else "  [OUT OF BAND]"
        lines.append(f"[quantity]   {name:<24s} = {row['value']:>10g} {row['unit']:<3s} ({row['per_gross_m2']:g}/m2)  ← {row['formula']}{flag}")
    for w in result.get("warnings", []):
        lines.append(f"[quantity]   WARNING: {w}")
    return lines
