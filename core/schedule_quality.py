"""Separate schedule arithmetic, labor effort, elapsed spans and empirical validity."""
from datetime import date
from collections import Counter


def schedule_quality(tasks, optimizer=None):
    physical = [t for t in tasks if t.get('productivity')]
    tiers = Counter(t['productivity'].get('evidence',{}).get('tier','provisional_fallback') for t in physical)
    takeover = next((t for t in tasks if 'Site Takeover' in t.get('name','')), None)
    completion = next((t for t in tasks if 'Physical Completion' in t.get('name','')), None)
    phase = next((t for t in tasks if '实体施工阶段' in t.get('name','')), None)
    def span(start, finish):
        if not start or not finish:
            return None
        return {'start':start,'finish':finish,'elapsed_calendar_days':(date.fromisoformat(finish)-date.fromisoformat(start)).days,
                'counting':'finish date minus start date; not summed trade durations or person-days'}
    labor = sum(t['productivity']['quantity']/t['productivity']['productivity_rate']*t['productivity']['factor_product'] for t in physical)
    notes = []
    if tiers.get('provisional_fallback'):
        notes.append('Uncalibrated numeric rates remain: schedule is a scenario, not an empirically validated commitment.')
    notes.append('No verified aggregate China office fit-out duration benchmark is loaded; no arbitrary duration cap is applied.')
    notes.append('Recommended crews are trade demands; concurrent resource totals are not resource-levelled.')
    if phase:
        notes.append('The original construction phase includes post-completion IAQ/furniture/purge; do not label its span as physical fit-out time.')
    peak = 0
    # Upper bound on overlap; ignores non-working days and does not allocate labor.
    for day in sorted({t['start'] for t in physical}):
        peak = max(peak, sum(t['productivity']['crew_size'] for t in physical if t['start']<=day<=t['finish']))
    return {'status':'provisional_scenario', 'arithmetic_is_not_field_validation':True,
            'physical_activity_count':len(physical),'rate_tier_counts':dict(tiers),
            'excluded_activities':[{'id':t['id'],'activity':t['excluded_activity_type'],**t['scope_exclusion']} for t in tasks if t.get('scope_exclusion')],
            'physical_site_span':span(takeover['start'],completion['finish']) if takeover and completion else None,
            'construction_phase_including_iaq':span(phase['start'],phase['finish']) if phase else None,
            'summed_trade_duration_days_not_project_duration':sum(t.get('duration_days',0) for t in physical),
            'modeled_labor_person_8h_days_before_shift_efficiency':round(labor,2),
            'peak_overlapping_trade_headcount_upper_bound':peak,
            'driving_chain_ids':[t['id'] for t in (optimizer or {}).get('critical_path',{}).get('chain',[])],
            'warnings':notes}
