"""Reference rate -> planning rate conversion, and the multi-slot productivity structure.

The reference rate is what a source says. The planning rate is what the schedule is
allowed to use. They are different objects and the gap between them must be explicit.

    planning_rate = reference_rate x scope_factor x site_factor x calibration_coefficient

A factor without evidence stays null. A null factor makes planning_rate null. No factor
is defaulted to 1.0 just to complete the formula.
"""
import copy
import math

from core.evidence_normalization import (build_pools, compatible_group_key, evaluate_confidence,
                                         sample_confidence, thresholds)

APPLICABILITY_PREFERENCE = ['direct', 'adjusted', 'reference_only', 'rejected']


def convert_reference(reference_rate, scope_factor=None, site_factor=None, calibration_coefficient=None):
    """Apply the conversion chain. Missing evidence means missing output, never a guessed factor."""
    factors = dict(scope_factor=scope_factor, site_factor=site_factor,
                   calibration_coefficient=calibration_coefficient)
    missing = [k for k, v in factors.items() if v is None]
    if missing:
        return dict(planning_rate=None, factors=factors, missing_factors=missing,
                    status='not_computed_missing_factors',
                    note='No factor is invented to complete the formula.')
    for k, v in factors.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f'{k} must be a positive finite number')
    value = reference_rate * scope_factor * site_factor * calibration_coefficient
    if not math.isfinite(value) or value <= 0:
        raise ValueError('planning_rate must be positive finite')
    return dict(planning_rate=value, factors=factors, missing_factors=[], status='computed')


def _best_reference(candidates, region=None):
    """Prefer the project's own region, then applicability, then confidence.

    Region is a preference, never a licence to pick a worse-scoped item: applicability
    still dominates, so a rejected local item never beats an adjusted one.
    """
    def key(r):
        return (APPLICABILITY_PREFERENCE.index(r['schedule_applicability']),
                0 if (region and r.get('source_region') == region) else 1,
                -r['confidence']['confidence_score'], r['evidence_id'])
    ranked = sorted(candidates, key=key)
    return ranked[0] if ranked else None


def build_structure(base_rates, records, pools=None, region=None):
    """Build the five-slot productivity structure for every library activity."""
    pools = pools if pools is not None else build_pools(records)
    pools_by_activity = {}
    for pool in pools:
        for r in records:
            if r['activity_type'] and compatible_group_key(r) == (pool['scope_id'], pool['normalized_unit'], pool['evidence_purpose']):
                pools_by_activity.setdefault(r['activity_type'], []).append(pool)
    structure = {}
    for name, entry in base_rates['activity_types'].items():
        candidates = [r for r in records if r.get('activity_type') == name]
        reference = _best_reference(candidates, region)
        reference_slot = None
        if reference:
            reference_slot = dict(
                value=reference['derived_productivity'],
                unit=reference['normalized_unit'],
                evidence_id=reference['evidence_id'],
                item_number=reference['item_number'],
                source_name=reference['source_name'],
                source_region=reference['source_region'],
                provenance_level=reference['provenance_level'],
                evidence_purpose=reference['evidence_purpose'],
                scope_id=reference['scope_id'],
                scope_equivalence=reference['scope_equivalence'],
                schedule_applicability=reference['schedule_applicability'],
                locator=f"pdf p.{reference['pdf_page']} / print {reference['print_page']}",
                confidence=reference['confidence'],
                limitations=reference['limitations'],
            )
        conversion = (convert_reference(reference['derived_productivity']) if reference
                      else dict(planning_rate=None, missing_factors=['reference_rate'],
                                status='no_reference_available'))
        observed = [r for r in records if r.get('activity_type') == name and r['provenance_level'] == 'A3']
        group = (pools_by_activity.get(name) or [None])[0]
        calibrated = None
        if group and group['sample_size'] >= thresholds()['minimum_for_pooling']['min_samples']:
            calibrated = dict(value=group['median'], basis='A4_calibrated_pool',
                              sample_size=group['sample_size'],
                              sample_size_confidence=group['sample_size_confidence'],
                              scope_id=group['scope_id'], status='descriptive_only')
        baseline = entry['rates_per_worker_day']['typical']
        selected = dict(value=baseline, basis='A0_current_baseline',
                        reason='No evidence-backed planning rate exists; the current baseline is retained.')
        structure[name] = dict(
            activity_type=name, unit=entry.get('unit'), scope_id=entry.get('scope_id'),
            reference_benchmark=reference_slot,
            planning_rate=conversion.get('planning_rate'),
            planning_conversion=conversion,
            observed_actual=(dict(sample_size=len(observed),
                                  status='unavailable' if not observed else 'available')
                             if not observed else dict(sample_size=len(observed), status='available')),
            calibrated_rate=calibrated,
            selected_rate=selected,
            baseline_rate=baseline,
            upgrade_applied=False,
            upgrade_blockers=_blockers(reference_slot, conversion, observed, calibrated),
        )
    return structure


def _blockers(reference_slot, conversion, observed, calibrated):
    blockers = []
    if not reference_slot:
        blockers.append('无已摄入参考基准')
        return blockers
    if reference_slot['schedule_applicability'] not in ('direct', 'adjusted'):
        blockers.append(f"排程适用性为 {reference_slot['schedule_applicability']}，不得进入计划率")
    if reference_slot['scope_equivalence'] != 'exact':
        blockers.append(f"范围等价为 {reference_slot['scope_equivalence']}，需拆分或调整系数")
    if reference_slot['evidence_purpose'] != 'calibrated_production':
        blockers.append('参考值为预算消耗量，非现场观测生产率')
    blockers.extend(f'缺少因子 {k}' for k in conversion.get('missing_factors', []))
    if not observed:
        blockers.append('无 A3 实际观测样本')
    if not calibrated:
        blockers.append('未达到校准池最小样本量')
    return blockers


def count_upgrades(structure):
    return sum(1 for v in structure.values() if v['upgrade_applied'])


def explain(activity_type, structure, quantity=None, quantity_source='derived_from_area', crew_size=None):
    """Human-readable justification. Never a bare 'AI estimated duration'."""
    slot = structure[activity_type]
    ref = slot['reference_benchmark']
    conf = ref['confidence'] if ref else dict(confidence_level='none', confidence_score=0.0,
                                              confidence_reasons=['无参考基准'])
    lines = [f"活动类型：{activity_type}"]
    if quantity is not None:
        lines.append(f"数量：{quantity} {slot['unit']}")
        lines.append(f"数量来源：{quantity_source}")
    lines.append(f"参考基准：{round(ref['value'], 2) if ref else '不可用'} {ref['unit'] if ref else ''}"
                 + (f"（{ref['item_number']}，{ref['locator']}）" if ref else ''))
    lines.append(f"参考用途：{ref['evidence_purpose'] if ref else '不可用'}")
    lines.append(f"计划率：{slot['planning_rate'] if slot['planning_rate'] is not None else '不可用（缺少有证据的调整因子）'}")
    lines.append(f"观察的作品集：{slot['observed_actual']['status']}（样本 {slot['observed_actual']['sample_size']}）")
    lines.append(f"校准率：{slot['calibrated_rate']['value'] if slot['calibrated_rate'] else '不可用'}")
    lines.append(f"精选率：{slot['selected_rate']['value']}（{slot['selected_rate']['basis']}）")
    lines.append(f"证据等级：{ref['provenance_level'] if ref else 'A0'}")
    lines.append(f"排程适用性：{ref['schedule_applicability'] if ref else '不适用'}")
    lines.append(f"信心：{conf['confidence_level']}（{conf['confidence_score']}）")
    lines.append('原因：')
    for reason in conf['confidence_reasons']:
        lines.append(f"  - {reason}")
    for blocker in slot['upgrade_blockers']:
        lines.append(f"  - {blocker}")
    if quantity is not None and crew_size:
        days = quantity / (slot['selected_rate']['value'] * crew_size)
        lines.append(f"持续时间：{round(days, 1)} 工作日（crew {crew_size}，按精选率计算）")
    return '\n'.join(lines)
