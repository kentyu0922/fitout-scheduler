"""Fit-out expansion used by the scheduler CLI.

Q/R/C productivity writes quantity/crew sidecars only. Occupancy on the client
Master Programme is applied afterwards by apply_l2_master_durations.
Tasks require explicit activity_type classification. No name guessing or LLM duration.
Inspection gates must be supplied as explicit tasks and inspection_gate_ids on closure
tasks; missing gates fail closed. A sectional release requires a real per-zone gate.
"""
import copy
from core.quantity_engine import derive_fitout_quantities
from core.productivity import apply_productivity_durations, load_productivity_rates
from core.productivity_evidence import load_evidence, resolve_rates
from core.dependency_engine import apply_fitout_dependencies, evaluate_sectional
from core.l2_master_duration import apply_l2_master_durations


def prepare_fitout(tasks, area_sqm, office_type='standard', fitout_grade='standard',
                   workfronts=None, sectional='auto', grade=None, city=None,
                   evidence_path=None, calibration_project=None, site_condition='unknown', demolition_fraction=None,
                   scope_excluded_activity_types=None, floors=1, freight_lifts=None):
    result = copy.deepcopy(tasks)
    if workfronts is not None and (isinstance(workfronts, bool) or int(workfronts) != workfronts or workfronts < 1):
        raise ValueError('workfronts must be a positive integer')
    quantities = derive_fitout_quantities(area_sqm, office_type, fitout_grade, grade=grade,
                                         site_condition=site_condition, demolition_fraction=demolition_fraction)
    rates = resolve_rates(load_productivity_rates(), city=city, evidence=load_evidence(evidence_path), project_id=calibration_project)
    decision = evaluate_sectional(area_sqm, mode=sectional, workfronts=workfronts)
    # P1-11: scope profile exclusions (explicit trades removed from scope)
    scope_excluded = set(scope_excluded_activity_types or [])
    for task in result:
        if task.get('scope_exclusion'):
            task['activity_type']=task.pop('excluded_activity_type')
            task.pop('scope_exclusion')
            task['milestone']=False
            task.pop('quantity',None)
            task.pop('quantity_source',None)
        if task.get('activity_type') and not task.get('inspection_gate'):
            # A failed recomputation must never reuse a caller's stale formula flag.
            task.pop('duration_method', None)
            task.pop('productivity', None)
            if task.get('quantity_source') == 'derived_from_area':
                task.pop('quantity', None)
                task.pop('quantity_key', None)
            task['project_area'] = area_sqm
            if 'workfronts' not in task or task.get('workfronts_source') == 'project':
                task['workfronts'] = decision['workfronts']
                task['workfronts_source'] = 'project'
            else:
                task.setdefault('workfronts_source', 'task')
            key=rates['activity_types'].get(task['activity_type'],{}).get('quantity_key')
            if scope_excluded and task['activity_type'] in scope_excluded and task.get('quantity_source') != 'measured':
                # P1-11: excluded by explicit scope profile — visible placeholder, never silently dropped
                task.update(excluded_activity_type=task.pop('activity_type'), duration_days=0,duration=0,milestone=False,
                        scope_exclusion={'reason':'Excluded by scope profile','quantity':None},
                        duration_method='out_of_scope')
                continue
            if task['activity_type'] in ('demolition','waste_removal') and task.get('quantity_source') != 'measured':
                demolition_value = quantities['quantities'][key]['value'] if key in quantities['quantities'] else 0
                if demolition_value == 0:
                    task.update(excluded_activity_type=task.pop('activity_type'), duration_days=0,duration=0,milestone=False,
                            scope_exclusion={'reason':'No demolition in selected site condition','site_condition':site_condition,'quantity':0},
                            duration_method='out_of_scope')
                elif demolition_value is None:
                    # P0-03: UNKNOWN site condition — demolition scope is unconfirmed.
                    # Keep audit-JSON placeholders; client Gantt hides them. Never invent full-strip work.
                    # P1-01: a scope-exclusion placeholder is an unresolved physical
                    # activity, never a completion milestone (milestone=False).
                    task.update(excluded_activity_type=task.pop('activity_type'), duration_days=0,duration=0,milestone=False,
                            scope_exclusion={'reason':'Site condition UNKNOWN - demolition scope not confirmed; rerun with explicit --site_condition','site_condition':site_condition,'quantity':None},
                            duration_method='out_of_scope')
    apply_productivity_durations(result, rates=rates, derived_quantities=quantities)
    for task in result:
        if task.get('activity_type') and not task.get('inspection_gate') and task.get('duration_method') != 'productivity_formula':
            raise ValueError(f"Task {task['id']} lacks valid productivity inputs")
        if task.get('productivity', {}).get('source', {}).get('confidence') == 'low':
            task['productivity']['confidence'] = 'low'
    l2_review = apply_l2_master_durations(
        result, area_sqm=area_sqm, floors=floors, freight_lifts=freight_lifts,
        spatial_split=False)
    dependencies = apply_fitout_dependencies(result, decision)
    return dict(tasks=result, quantities=quantities, dependencies=dependencies, l2_review=l2_review)


def prepare_spatial_fitout(tasks, area_sqm, office_type='standard', fitout_grade='standard',
                           zone_mode='auto', manual_zones=None, workfronts=None,
                           grade=None, city=None, evidence_path=None, calibration_project=None,
                           site_condition='unknown', demolition_fraction=None,
                           crews_per_trade=None, semantics=None,
                           floors_count=1, floor_breakdown=None,
                           scope_excluded_activity_types=None,
                           freight_lifts=None):
    """V6.1 entry point: expand the WBS into a zone-based network.

    Adds the spatial layer on top of the unchanged V5.3 quantity / productivity /
    evidence pipeline and the unchanged V6.0 dependency ontology. Nothing here
    writes to config/productivity_rates.json or config/dependency_ontology.json.
    """
    from core.spatial_engine import build_spatial_plan

    result = copy.deepcopy(tasks)
    quantities = derive_fitout_quantities(area_sqm, office_type, fitout_grade, grade=grade,
                                          site_condition=site_condition,
                                          demolition_fraction=demolition_fraction)
    rates = resolve_rates(load_productivity_rates(), city=city,
                          evidence=load_evidence(evidence_path), project_id=calibration_project)

    # P0-03: the spatial pipeline must honor UNKNOWN site condition exactly like
    # prepare_fitout does — demolition/waste stay audit placeholders (hidden on
    # client Gantt) and are never expanded into invented zone-level strip-out work.
    # P1-11 parity with prepare_fitout: an explicit scope profile must exclude the same
    # trades on the spatial path, and a scope-exclusion placeholder is never a milestone.
    scope_excluded = set(scope_excluded_activity_types or [])
    for task in result:
        if task.get('scope_exclusion'):
            task['activity_type'] = task.pop('excluded_activity_type')
            task.pop('scope_exclusion')
            task['milestone'] = False
            task.pop('quantity', None)
            task.pop('quantity_source', None)
        activity = task.get('activity_type')
        if (activity and not task.get('inspection_gate')
                and scope_excluded and activity in scope_excluded
                and task.get('quantity_source') != 'measured'):
            task.update(excluded_activity_type=task.pop('activity_type'), duration_days=0,
                        duration=0, milestone=False,
                        scope_exclusion={'reason': 'Excluded by scope profile', 'quantity': None},
                        duration_method='out_of_scope')
            continue
        if (not activity or task.get('inspection_gate')
                or activity not in ('demolition', 'waste_removal')
                or task.get('quantity_source') == 'measured'):
            continue
        key = rates['activity_types'].get(activity, {}).get('quantity_key')
        value = quantities['quantities'][key]['value'] if key in quantities['quantities'] else 0
        if value == 0:
            task.update(excluded_activity_type=task.pop('activity_type'), duration_days=0, duration=0, milestone=False,
                        scope_exclusion={'reason': 'No demolition in selected site condition',
                                         'site_condition': site_condition, 'quantity': 0},
                        duration_method='out_of_scope')
        elif value is None:
            task.update(excluded_activity_type=task.pop('activity_type'), duration_days=0, duration=0, milestone=False,
                        scope_exclusion={'reason': 'Site condition UNKNOWN - demolition scope not confirmed; '
                                                   'rerun with explicit --site_condition',
                                         'site_condition': site_condition, 'quantity': None},
                        duration_method='out_of_scope')

    def _apply(ts):
        apply_productivity_durations(ts, rates=rates, derived_quantities=quantities)

    plan = build_spatial_plan(result, area_sqm, quantities['quantities'], rates,
                              zone_mode=zone_mode, manual_zones=manual_zones,
                              workfronts=workfronts, crews_per_trade=crews_per_trade,
                              apply_durations=_apply, semantics=semantics,
                              floors_count=floors_count, floor_breakdown=floor_breakdown)
    for task in plan['tasks']:
        if task.get('activity_type') and not task.get('inspection_gate'):
            if task.get('duration_method') != 'productivity_formula':
                raise ValueError(f"Task {task['id']} lacks valid productivity inputs")
    l2_review = apply_l2_master_durations(
        plan['tasks'], area_sqm=area_sqm, floors=floors_count,
        freight_lifts=freight_lifts, spatial_split=True)
    return dict(tasks=plan['tasks'], quantities=quantities, plan=plan, l2_review=l2_review)


def solve_spatial_fitout(tasks, area_sqm, start_date, **context):
    """Solve the zone-based plan and compare it against the single-workfront baseline."""
    from core.solver_engine import solve_schedule
    from core.spatial_engine import compare_spatial_vs_baseline

    prepared = prepare_spatial_fitout(tasks, area_sqm, **context)
    solved = solve_schedule(prepared['tasks'], start_date)
    prepared['tasks'] = solved['tasks']
    baseline = solve_fitout(copy.deepcopy(tasks), area_sqm, start_date,
                            workfronts=1, sectional='off',
                            **{k: v for k, v in context.items()
                               if k not in ('zone_mode', 'manual_zones', 'workfronts',
                                            'crews_per_trade', 'semantics')})
    prepared['comparison'] = compare_spatial_vs_baseline(baseline, solved, start_date)
    return prepared


def solve_fitout(tasks, area_sqm, start_date, **context):
    """Solve first, then compare re-solved proposals without mutating the baseline."""
    from core.solver_engine import solve_schedule
    from core.optimizer import analyze_schedule
    prepared = prepare_fitout(tasks, area_sqm, **context)
    solved = solve_schedule(prepared['tasks'], start_date)
    prepared['tasks'] = solved['tasks']
    prepared['optimizer'] = analyze_schedule(solved['tasks'], start_date=start_date, verify=True)
    return prepared
