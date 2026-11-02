"""Evidence normalization layer: raw evidence -> verified -> scope/unit normalized -> applicability.

Hard rules encoded here:
  * Evidence is not productivity. An official quota is evidence, not a schedule rate.
  * An A1 (official) source never earns 'direct' schedule applicability on its own.
  * Two observations may be pooled only when scope_id, normalized unit and evidence
    purpose are all compatible.
  * Every normalized value keeps its raw value, raw unit and conversion formula.
  * The baseline productivity library is read-only for this module.
"""
import copy
import json
import math
from pathlib import Path

from core.calibration_dataset import percentiles

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config'
OBSERVATIONS_PATH = CONFIG / 'evidence_observations.json'
PRODUCTIVITY_PATH = CONFIG / 'productivity_rates.json'


def _load(name):
    with open(CONFIG / name, encoding='utf-8') as f:
        return json.load(f)


def schema():
    return _load('evidence_schema.json')


def scope_catalog():
    return _load('scope_equivalence.json')


def unit_config():
    return _load('unit_normalization.json')


def thresholds():
    return _load('calibration_thresholds.json')


def load_observations(path=None):
    with open(path or OBSERVATIONS_PATH, encoding='utf-8') as f:
        data = json.load(f)
    records = [normalize_observation(r) for r in data['observations']]
    return dict(meta={k: v for k, v in data.items() if k != 'observations'}, records=records)


# ---------------------------------------------------------------- validation

def validate_observation(record):
    """Required-field and enum check. A gap is reported, never silently filled."""
    spec = schema()
    missing = [f for f in spec['required_fields'] if f not in record]
    if missing:
        raise ValueError(f'missing required evidence fields: {missing}')
    if record['provenance_level'] not in spec['axis_a_provenance']:
        raise ValueError('unknown provenance_level')
    if record['evidence_purpose'] not in spec['axis_b_purpose']:
        raise ValueError('unknown evidence_purpose')
    if record['scope_equivalence'] not in spec['scope_equivalence_classes']:
        raise ValueError('unknown scope_equivalence')
    if record['schedule_applicability'] not in spec['schedule_applicability_classes']:
        raise ValueError('unknown schedule_applicability')
    if record['verification_status'] not in spec['verification_status_values']:
        raise ValueError('unknown verification_status')
    if record['source_authenticity_confidence'] not in spec['source_authenticity_values']:
        raise ValueError('unknown source_authenticity_confidence')
    guard = spec['applicability_guardrails']
    if guard['official_source_never_auto_direct']:
        if record['provenance_level'] == 'A1' and record['schedule_applicability'] == 'direct':
            raise ValueError('official source cannot be auto-classified as direct')
    if record['scope_equivalence'] == 'composite' and record['schedule_applicability'] == 'direct':
        raise ValueError('a composite item cannot be a direct planning candidate')
    return True


# ------------------------------------------------------------ normalization

def _quantity_scale(unit, cfg):
    for group in cfg['quantity_unit_groups'].values():
        if unit in group['units']:
            return group, group['units'][unit]
    raise ValueError(f'unknown quantity unit: {unit}')


def normalize_unit(raw_value, raw_quantity_unit, hours_per_workday, normalized_unit):
    """Invert a labour consumption into output per standard workday, keeping raw values."""
    cfg = unit_config()
    group, scale = _quantity_scale(raw_quantity_unit, cfg)
    if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool) or raw_value <= 0:
        raise ValueError('raw_value must be a positive number')
    hours = float(hours_per_workday)
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError('hours_per_workday must be positive')
    hours_factor = cfg['standard_hours'] / hours
    value = (scale / float(raw_value)) * hours_factor
    if not math.isfinite(value) or value <= 0:
        raise ValueError('normalized productivity must be positive finite')
    return dict(
        raw_value=raw_value,
        raw_unit=f'工日/{raw_quantity_unit}',
        quantity_scale=scale,
        hours_per_workday=hours,
        hours_factor=hours_factor,
        normalized_value=value,
        normalized_unit=normalized_unit or cfg['normalized_output_unit'][_group_key(group)],
        conversion_formula=cfg['conversion_formula'],
    )


def _group_key(group):
    cfg = unit_config()
    for key, candidate in cfg['quantity_unit_groups'].items():
        if candidate is group:
            return key
    raise ValueError('unknown unit group')


def unit_match_kind(record):
    scale = record['normalization']['quantity_scale']
    if scale == 1.0 and record['hours_per_workday'] == unit_config()['standard_hours']:
        return 'native'
    if scale == 1.0:
        return 'converted'
    return 'rescaled'


def normalize_observation(record):
    r = copy.deepcopy(record)
    validate_observation(r)
    r['normalization'] = normalize_unit(r['raw_value'], r['raw_quantity_unit'],
                                        r['hours_per_workday'], r.get('normalized_unit'))
    r['derived_productivity'] = r['normalization']['normalized_value']
    r['unit_match'] = unit_match_kind(r)
    r['confidence'] = evaluate_confidence(r, sample_size=1)
    return r


# ------------------------------------------------------------- calibration

def sample_confidence(n):
    """Configurable sample-size bands. Small samples never claim high confidence."""
    for band in thresholds()['sample_size_confidence']:
        upper = band['max_n']
        if n >= band['min_n'] and (upper is None or n <= upper):
            return band['level']
    return 'none'


def pool_stats(values):
    stats = percentiles(list(values))
    stats['sample_size_confidence'] = sample_confidence(stats['sample_size'])
    stats['min'] = min(values) if values else None
    stats['max'] = max(values) if values else None
    return stats


def compatible_group_key(record):
    return (record['scope_id'], record['normalized_unit'], record['evidence_purpose'])


def build_pools(records):
    """Pool only scope_id + unit + purpose compatible items. Always descriptive."""
    spec = schema()
    groups = {}
    for r in sorted(records, key=lambda x: x['evidence_id']):
        groups.setdefault(compatible_group_key(r), []).append(r)
    result = []
    for (scope_id, unit, purpose), rows in sorted(groups.items()):
        values = [r['derived_productivity'] for r in rows]
        stats = pool_stats(values)
        minimum = thresholds()['minimum_for_pooling']
        project_count = len({r['project_id'] for r in rows if r.get('project_id')})
        result.append(dict(
            scope_id=scope_id, normalized_unit=unit, evidence_purpose=purpose,
            activity_type=rows[0]['activity_type'],
            regions=sorted({r['source_region'] for r in rows}),
            evidence_ids=[r['evidence_id'] for r in rows],
            provenance_levels=sorted({r['provenance_level'] for r in rows}),
            project_count=project_count,
            **stats,
            meets_minimum_for_pooling=(stats['sample_size'] >= minimum['min_samples']
                                       and project_count >= minimum['min_projects']),
            status='descriptive_only_reference_pool',
            note='Budget-consumption pool. Describes the reference level only; it does not produce a selected or calibrated planning rate.',
        ))
    return result


# -------------------------------------------------------------- confidence

def evaluate_confidence(record, sample_size=1, cross_source='single_source'):
    """Multi-factor confidence. Source authenticity alone cannot raise the level."""
    t = thresholds()['confidence_model']
    weights, scores = t['weights'], t['factor_scores']
    factors = {
        'source_authenticity': scores['source_authenticity'][record['source_authenticity_confidence']],
        'scope_match': scores['scope_match'][record['scope_equivalence']],
        'unit_match': scores['unit_match'][record.get('unit_match', 'native')],
        'schedule_applicability': scores['schedule_applicability'][record['schedule_applicability']],
        'data_order': scores['data_order'][record['provenance_level']],
        'cross_source_consistency': scores['cross_source_consistency'][cross_source],
    }
    sample_level = sample_confidence(sample_size)
    factors['sample_size'] = t['sample_size_score'][sample_level]
    score = round(sum(weights[k] * factors[k] for k in weights), 4)
    level = 'none'
    for band in t['score_bands']:
        if band['min_score'] <= score <= band['max_score']:
            level = band['level']
            break
    reasons = []
    caps = []
    context = {
        'schedule_applicability': record['schedule_applicability'],
        'scope_equivalence': record['scope_equivalence'],
        'evidence_purpose': record['evidence_purpose'],
        'sample_size_confidence': sample_level,
    }
    for cap in t['hard_caps']:
        when = cap['when']
        hit = any(context.get(key) in values for key, values in when.items())
        if hit:
            caps.append(cap)
    order = ['none', 'very_low', 'low', 'medium', 'high']
    for cap in caps:
        if order.index(level) > order.index(cap['max_level']):
            level = cap['max_level']
            reasons.append('上限生效：' + cap['reason'])
    reasons.append(f"证据来源 {record['provenance_level']}，来源真实性 {record['source_authenticity_confidence']}")
    reasons.append(f"范围等价 {record['scope_equivalence']}，排程适用性 {record['schedule_applicability']}")
    reasons.append(f"证据目的 {record['evidence_purpose']}；样本量 {sample_size}（{sample_level}）")
    if record['unit_match'] != 'native':
        reasons.append(f"单位经 {record['unit_match']} 换算，非原生单位")
    return dict(confidence_level=level, confidence_score=score, factors=factors,
                sample_size=sample_size, sample_size_confidence=sample_level,
                hard_caps_applied=[c['reason'] for c in caps], confidence_reasons=reasons)


# ---------------------------------------------------------- cross region

def cross_region_comparison(records):
    """Compare regions only inside a shared scope_id + unit + purpose group."""
    pools = build_pools(records)
    rows = []
    for pool in pools:
        by_region = {}
        for r in sorted(records, key=lambda x: x['evidence_id']):
            if compatible_group_key(r)[:3] != (pool['scope_id'], pool['normalized_unit'], pool['evidence_purpose']):
                continue
            # A rejected item has no defensible reference level, so it never enters a comparison.
            if r['schedule_applicability'] == 'rejected':
                continue
            by_region.setdefault(r['source_region'], []).append(r)
        if len(by_region) < 2:
            continue
        median = pool['median']
        for region, items in sorted(by_region.items()):
            for r in items:
                value = r['derived_productivity']
                rows.append(dict(
                    scope_id=pool['scope_id'], normalized_unit=pool['normalized_unit'],
                    evidence_purpose=pool['evidence_purpose'], region=region,
                    evidence_id=r['evidence_id'], item_number=r['item_number'],
                    reference_rate=round(value, 4),
                    group_median=round(median, 4) if median is not None else None,
                    variance_vs_median=(round(value / median - 1, 4) if median else None),
                    provenance_level=r['provenance_level'],
                    scope_equivalence=r['scope_equivalence'],
                    schedule_applicability=r['schedule_applicability'],
                    condition_note=r['limitations'],
                ))
    return dict(method='same scope_id + same normalized unit + compatible purpose only',
                comparable_groups=sorted({r['scope_id'] for r in rows}),
                rows=rows,
                note='Variance is a comparison of budget reference levels, not a measure of site productivity.')


# ------------------------------------------------------- baseline guard

def baseline_snapshot():
    import hashlib
    with open(PRODUCTIVITY_PATH, 'rb') as f:
        raw = f.read()
    data = json.loads(raw.decode('utf-8'))
    return dict(path=str(PRODUCTIVITY_PATH.relative_to(ROOT)),
                sha256=hashlib.sha256(raw).hexdigest(),
                activity_count=len(data['activity_types']),
                rates={k: v['rates_per_worker_day'] for k, v in data['activity_types'].items()},
                version=data.get('version'))


def summarize(records):
    from collections import Counter
    return dict(
        ingested=len(records),
        by_region=dict(Counter(r['source_region'] for r in records)),
        by_scope_equivalence=dict(Counter(r['scope_equivalence'] for r in records)),
        by_schedule_applicability=dict(Counter(r['schedule_applicability'] for r in records)),
        by_provenance=dict(Counter(r['provenance_level'] for r in records)),
        by_purpose=dict(Counter(r['evidence_purpose'] for r in records)),
        by_verification=dict(Counter(r['verification_status'] for r in records)),
        distinct_scope_ids=len({r['scope_id'] for r in records}),
    )
