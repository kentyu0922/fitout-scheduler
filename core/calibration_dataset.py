"""V5.3 actuals and deterministic descriptive calibration; never edits a baseline."""
import copy
import math

from core.productivity_evidence import _observation, positive

EVIDENCE_HIERARCHY = {
    'L0': 'assumption',
    'L1': 'published_official_benchmark',
    'L2': 'contractor_industry_benchmark',
    'L3': 'historical_project_actual',
}


def planned_vs_actual(record):
    """Snapshot a completed activity. Actual hours are summed timesheet worker-hours.

    No multiplication by planned crew, shifts or efficiency: those are already
    reflected in actual labor. Workdays must be supplied from the actual calendar.
    """
    r = copy.deepcopy(record)
    for key in ('project_id', 'activity_id', 'activity_type', 'unit', 'scope_id', 'baseline_id'):
        if not isinstance(r.get(key), str) or not r[key].strip():
            raise ValueError(f'Missing identity: {key}')
    planned, actual = r['planned'], r['actual']
    pq = positive(planned['quantity'], 'planned quantity')
    pd = positive(planned['duration_workdays'], 'planned workdays')
    aq = positive(actual['accepted_quantity'], 'accepted quantity')
    hours = positive(actual['labor_hours'], 'actual labor hours')
    ad = positive(actual['duration_workdays'], 'actual workdays')
    if actual.get('quantity_source') != 'measured_accepted' or actual.get('labor_source') != 'timesheet':
        raise ValueError('Actuals require accepted measured quantity and timesheet labor')
    if actual.get('status') != 'completed':
        raise ValueError('Only completed activities are comparable')
    for key in ('quantity_reference', 'labor_reference'):
        if not isinstance(actual.get(key), str) or not actual[key].strip():
            raise ValueError(f'Missing actual provenance: {key}')
    productivity = aq / hours * 8
    if not math.isfinite(productivity) or productivity <= 0:
        raise ValueError('Actual productivity must be positive finite')
    r['comparison'] = {
        'actual_productivity': productivity,
        'productivity_unit': r['unit'] + '/worker-8h-day',
        'quantity_variance': aq - pq,
        'duration_variance_workdays': ad - pd,
        'quantity_ratio': aq / pq,
        'duration_ratio': ad / pd,
        'note': 'Raw actual output/labor; duration variance includes scope change and is not causal attribution.',
    }
    return r


def to_observation(record):
    """Convert reviewed reference-condition actuals into the existing evidence contract."""
    r = planned_vs_actual(record)
    actual = r['actual']
    observation = {k: copy.deepcopy(r[k]) for k in ('project_id', 'activity_type', 'unit', 'scope_id')}
    observation.update(copy.deepcopy(r.get('review', {})))
    observation.update(record_type='field_observation', observation_id=r['activity_id'] + '@' + r['project_id'],
                       city=r.get('city'), accepted_quantity=actual['accepted_quantity'],
                       labor_hours=actual['labor_hours'], quantity_source=actual['quantity_source'],
                       labor_source=actual['labor_source'], conditions=actual.get('conditions'),
                       labor_scope=actual.get('labor_scope'), includes_waiting=actual.get('includes_waiting'),
                       quantity_reference=actual['quantity_reference'], labor_reference=actual['labor_reference'],
                       baseline_id=r['baseline_id'])
    _observation(observation)
    return observation


def percentiles(values):
    """Linear interpolation at (n-1)*p (type 7); unweighted observations."""
    values = sorted(positive(v, 'sample productivity') for v in values)
    def at(p):
        if not values:
            return None
        position = (len(values) - 1) * p
        lo, hi = math.floor(position), math.ceil(position)
        return values[lo] + (values[hi] - values[lo]) * (position - lo)
    return dict(sample_size=len(values), p25=at(.25), median=at(.5), p75=at(.75))


def build_dataset(base_rates, observations, city=None, project_id=None):
    """Summarize matching reviewed actuals; reject incompatible units/scopes/conditions.

    Empty groups are included for every library activity. No automatic rate adoption.
    """
    groups = {name: [] for name in sorted(base_rates['activity_types'])}
    rejected, seen = [], set()
    for r in sorted(observations, key=lambda item: str(item.get('observation_id', ''))):
        identifier = r.get('observation_id')
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            raise ValueError('Duplicate/missing observation ID')
        seen.add(identifier)
        if city is not None and r.get('city') != city:
            continue
        if project_id is not None and r.get('project_id') != project_id:
            continue
        try:
            entry = base_rates['activity_types'].get(r.get('activity_type'))
            if not entry or r.get('unit') != entry['unit'] or r.get('scope_id') != entry.get('scope_id'):
                raise ValueError('Unknown activity or incompatible unit/scope')
            q, days = _observation(r)
            rate = positive(q / days, 'actual productivity')
            groups[r['activity_type']].append((r, rate))
        except (ValueError, KeyError, TypeError) as ex:
            rejected.append({'observation_id': identifier, 'reason': str(ex)})
    result = {}
    for name, rows in groups.items():
        entry = base_rates['activity_types'][name]
        result[name] = dict(percentiles([rate for _, rate in rows]),
                            unit=entry['unit'], scope_id=entry.get('scope_id'),
                            evidence_level='L3' if rows else 'L0',
                            project_count=len({r['project_id'] for r, _ in rows}),
                            source_ids=[r['observation_id'] for r, _ in rows],
                            provenance=[copy.deepcopy(r) for r, _ in rows],
                            status='descriptive_only' if rows else 'no_samples_baseline_fallback')
    return dict(schema_version='5.3', percentile_method='linear_type_7_unweighted',
                city=city, project_id=project_id, activity_types=result, rejected=rejected,
                baseline_modified=False, note='Percentiles describe samples, not confidence intervals or approved rates.')
