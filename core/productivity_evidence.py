"""Auditable L1/L2/L3 rate selection. Cost data never becomes schedule productivity.

The evidence store starts empty of verified numeric items. A document title or an
AI-supplied number is not sufficient evidence. No observation is collected from
calculated schedule durations (that would train the engine on its own predictions).
"""
import copy
import json
import math
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'config' / 'productivity_evidence.json'


def positive(value, label):
    if isinstance(value, bool):
        raise ValueError(f'{label} must be a positive finite number')
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f'{label} must be a positive finite number')
    return number


def normalize_rate(record):
    """Convert a verified time/output item to output per person per eight hours."""
    basis = record['basis']
    hours = positive(record['hours_per_person_day'], 'hours_per_person_day')
    if basis == 'time_norm':
        # e.g. 6.25 person-days per 100 m2 -> 16 m2/person-8h-day.
        return positive(record['quantity_basis'], 'quantity_basis') / positive(record['person_days'], 'person_days') * 8 / hours
    if basis == 'output_per_person_day':
        return positive(record['output'], 'output') * 8 / hours
    raise ValueError(f'{basis} cannot be used as per-person schedule productivity; cost/budget/total-duration/crew values require a separately reviewed scope conversion')


def _verified(record):
    return (record.get('review_status') == 'approved' and record.get('numeric_item_verified') is True
            and all(record.get(k) for k in ('source_reference', 'locator', 'reviewer', 'reviewed_on')))


def _matches(record, name, entry):
    return record.get('activity_type') == name and record.get('unit') == entry['unit'] and record.get('scope_id') == entry.get('scope_id')


def _observation(record):
    if not _verified(record) or record.get('record_type') != 'field_observation':
        raise ValueError('Only reviewed actual field observations are eligible')
    if not record.get('project_id') or not record.get('observation_id'):
        raise ValueError('Observation requires unique identity and project provenance')
    if record.get('quantity_source') != 'measured_accepted' or record.get('labor_source') != 'timesheet':
        raise ValueError('Calibration requires accepted takeoff and actual timesheet hours, not modeled dates')
    if record.get('conditions') != {'complexity': 'standard', 'access': 'open'}:
        raise ValueError('Non-reference conditions need a separately reviewed normalization; do not double-count modifiers')
    if record.get('labor_scope') != 'all_installation_workers' or record.get('includes_waiting') is not False:
        raise ValueError('Record all installation workers; separate inspection/cure waits from production labor')
    return positive(record['accepted_quantity'], 'accepted_quantity'), positive(record['labor_hours'], 'labor_hours') / 8


def load_evidence(path=None):
    with open(path or PATH, encoding='utf-8') as f:
        data = json.load(f)
    validate_evidence(data)
    return data


def validate_evidence(data):
    if not isinstance(data.get('industry_items', []), list):
        raise ValueError('industry_items must be a list')
    for key in ('national_items', 'regional_adjustments', 'observations', 'validation_observations'):
        if not isinstance(data.get(key), list):
            raise ValueError(f'Evidence store requires list: {key}')
    identifiers = [r.get('observation_id') for key in ('observations', 'validation_observations') for r in data[key]]
    if any(i is None for i in identifiers) or len(identifiers) != len(set(identifiers)):
        raise ValueError('Duplicate/missing observation IDs would double-count field data')
    p = data['calibration_policy']
    for key in ('min_observations','min_projects','min_validation_projects'):
        if not isinstance(p[key], int) or isinstance(p[key],bool) or p[key] < 1:
            raise ValueError(f'{key} must be a positive integer')
    for key in ('min_labor_days','prior_equivalent_labor_days','target_relative_error'):
        positive(p[key],key)
    if p['target_relative_error'] > 1:
        raise ValueError('target_relative_error must be <= 1')


def resolve_rates(base_rates, city=None, evidence=None, project_id=None):
    """Return a deep copy with traceable selections; never mutate the base library."""
    data = evidence if evidence is not None else load_evidence()
    validate_evidence(data)
    rates = copy.deepcopy(base_rates)
    policy = data['calibration_policy']
    from core.calibration_dataset import build_dataset
    dataset = build_dataset(base_rates, data['observations'], city, project_id)
    for name, entry in rates['activity_types'].items():
        trace = {'tier': 'provisional_fallback', 'city': city, 'scope_id': entry.get('scope_id'),
                 'rejected': [], 'source_ids': [], 'validation': {'status': 'not_evaluated', 'target_relative_error': policy['target_relative_error']}}
        nationals = [r for r in data['national_items'] if r.get('activity_type') == name]
        valid = []
        for r in nationals:
            if not _matches(r, name, entry) or not _verified(r):
                trace['rejected'].append({'id': r.get('id'), 'reason': 'numeric item or matching scope/unit not verified'})
                continue
            try:
                valid.append((r, normalize_rate(r)))
            except (KeyError, TypeError, ValueError) as ex:
                trace['rejected'].append({'id': r.get('id'), 'reason': str(ex)})
        if len(valid) > 1:
            raise ValueError(f'Ambiguous approved national items for {name}; select one in evidence config')
        if valid:
            record, rate = valid[0]
            # No invented +/- percentage: a single observed norm is a point, not a range.
            entry['rates_per_worker_day'] = {k: rate for k in ('low','typical','high')}
            entry['rate_status'] = 'verified_norm_scope_only'
            entry['base_confidence'] = 'medium'
            entry['source'] = {'type': 'verified_time_or_output_norm', 'reference': record['source_reference'],
                               'locator': record['locator'], 'confidence': 'medium', 'requires_validation': True}
            trace.update(tier='L1', source_ids=[record['id']], range_status='single_point_not_prediction_interval')
        industry = [r for r in data.get('industry_items', []) if r.get('activity_type') == name and r.get('city') in (None, city)]
        approved = []
        for r in industry:
            try:
                if not _matches(r, name, entry) or not _verified(r):
                    raise ValueError('L2 requires reviewed contractor/industry numeric provenance and matching scope')
                approved.append((r, normalize_rate(r)))
            except (KeyError, TypeError, ValueError) as ex:
                trace['rejected'].append({'id': r.get('id'), 'reason': str(ex)})
        if len(approved) > 1:
            raise ValueError(f'Ambiguous industry items for {name}')
        if approved:
            r, rate = approved[0]
            entry['rates_per_worker_day'] = dict(low=rate, typical=rate, high=rate)
            entry['rate_status'] = 'reviewed_industry_scope_only'
            entry['base_confidence'] = 'medium'
            entry['source'] = dict(type='contractor_industry_benchmark', reference=r['source_reference'], locator=r['locator'], confidence='medium', requires_validation=True)
            trace.update(tier='L2', source_ids=[r['id']], range_status='single_point_not_prediction_interval')
        regional = [r for r in data['regional_adjustments'] if r.get('city') == city and r.get('activity_type') == name]
        applicable = []
        for r in regional:
            if not _matches(r, name, entry) or not _verified(r) or r.get('basis') != 'measured_productivity_ratio':
                trace['rejected'].append({'id':r.get('id'), 'reason':'L2 requires measured productivity ratio; wages and budget allowances are cost-only'})
                continue
            applicable.append(r)
        if len(applicable) > 1:
            raise ValueError(f'Ambiguous regional productivity adjustments for {name}/{city}')
        if applicable:
            r = applicable[0]
            # Ratio must have been calibrated to this exact numeric anchor/version.
            anchor = trace['source_ids'][0] if trace['source_ids'] else base_rates['version']
            if r.get('anchor_id') != anchor:
                trace['rejected'].append({'id':r.get('id'), 'reason':'regional ratio anchor mismatch'})
            else:
                multiplier = positive(r['productivity_multiplier'], 'productivity_multiplier')
                entry['rates_per_worker_day'] = {k:v*multiplier for k,v in entry['rates_per_worker_day'].items()}
                trace['tier'] = 'L2'
                trace['source_ids'].append(r['id'])
                trace['regional_multiplier'] = multiplier
        accepted, seen = [], set()
        for r in sorted(data['observations'], key=lambda r: str(r.get('observation_id', ''))):
            if not _matches(r, name, entry) or r.get('city') != city:
                continue
            if project_id and r.get('project_id') != project_id:
                continue
            try:
                q, days = _observation(r)
                if r['observation_id'] in seen:
                    raise ValueError('Duplicate observation ID')
                seen.add(r['observation_id'])
                accepted.append((r,q,days))
            except (KeyError, TypeError, ValueError) as ex:
                trace['rejected'].append({'id':r.get('observation_id'), 'reason':str(ex)})
        projects = {r['project_id'] for r,q,d in accepted}
        days = sum(d for r,q,d in accepted)
        trace['sample_count'] = len(accepted)
        trace['project_count'] = len(projects)
        trace['labor_days'] = days
        required_projects = 1 if project_id else policy['min_projects']
        trace['calibration_scope'] = 'single_project' if project_id else 'city_multi_project'
        if len(accepted) >= policy['min_observations'] and len(projects) >= required_projects and days >= policy['min_labor_days']:
            prior = entry['rates_per_worker_day']['typical']
            prior_weight = positive(policy['prior_equivalent_labor_days'], 'prior_equivalent_labor_days')
            rate = (prior*prior_weight + sum(q for r,q,d in accepted)) / (prior_weight+days)
            individual = [q/d for r,q,d in accepted]
            old = entry['rates_per_worker_day']
            entry['rates_per_worker_day'] = dict(low=min(old['low'], rate, *individual), typical=rate, high=max(old['high'],rate,*individual))
            entry['rate_status'] = 'field_calibrated_provisional'
            entry['base_confidence'] = 'low' if project_id else 'medium'
            entry['source'] = {'type': 'reviewed_field_calibration', 'reference': [r['source_reference'] for r,q,d in accepted],
                               'confidence':entry['base_confidence'], 'requires_validation': True}
            trace.update(tier='L3', prior_rate=prior, prior_equivalent_labor_days=prior_weight,
                         observed_pooled_rate=sum(q for r,q,d in accepted)/days,
                         range_status='scenario_envelope_not_confidence_interval')
            trace['source_ids'].extend(r['observation_id'] for r,q,d in accepted)
        # Holdout evaluation is by unseen PROJECT, never by rows from a training project.
        errors, holdout_projects = [], set()
        for r in sorted(data['validation_observations'], key=lambda r: str(r.get('observation_id', ''))):
            if not _matches(r, name, entry) or r.get('city') != city:
                continue
            try:
                q, actual_days = _observation(r)
                if r['project_id'] in projects:
                    raise ValueError('Validation project overlaps training data')
                predicted_days = q / entry['rates_per_worker_day']['typical']
                errors.append(abs(predicted_days-actual_days)/actual_days)
                holdout_projects.add(r['project_id'])
            except (KeyError,TypeError,ValueError) as ex:
                trace['rejected'].append({'id':r.get('observation_id'), 'reason':str(ex)})
        if errors:
            trace['validation'].update(status='evaluated', sample_count=len(errors), project_count=len(holdout_projects),
                                       mean_absolute_relative_labor_error=sum(errors)/len(errors), max_relative_labor_error=max(errors),
                                       target_met=len(holdout_projects)>=policy['min_validation_projects'] and max(errors)<=policy['target_relative_error'])
        trace['provenance'] = [copy.deepcopy(r) for key in ('national_items', 'industry_items', 'regional_adjustments', 'observations') for r in data.get(key, []) if r.get('id') in trace['source_ids'] or r.get('observation_id') in trace['source_ids']]
        trace['level'] = trace['tier'] if trace['tier'] in ('L1', 'L2', 'L3') else 'L0'
        trace['calibration_summary'] = dataset['activity_types'][name]
        entry['evidence_level'] = trace['level']
        entry['evidence'] = trace
    rates['evidence_version'] = data['version']
    return rates
