"""Project-specific duration reasoning; no industry defaults or approval inference."""
import copy
import re
from datetime import date

# Bounded adapter milestones, not a free-text escape from duration review.
MILESTONE_CONTRACTS={
    'kickoff': ('启动','项目启动','TDD招采启动'),
    'lease': ('租赁决策释放','TDD完成并供租赁决策'),
    'lease_decision': ('租赁决策释放',),
    'site_commence': ('现场开工释放',),
    'fitout_finish': ('硬装竣工',),
    'concealed_gate_z1': ('低区隐蔽验收',),
    'concealed_gate_z2': ('高区隐蔽验收',),
    'business': ('业务系统就绪',),
    'business_ready': ('业务系统就绪',),
    'occupancy': ('人员搬迁放行',),
    'occupancy_ready': ('人员搬迁放行',),
    'infra_ready': ('目标设施就绪', '核心机房上电就绪', '核心机房先行上电交付签认'),
    'systems_ready': ('系统就绪',),
    'mocap_ready': ('动捕与声学工程就绪', '动捕棚与专业声学录音棚验收就绪', '动捕声学就绪'),
    'fire_permit_pass': ('消防验收合格意见书获取', '消防验收通过'),
    'day1_opening': ('正式开业运营', '总部正式开业运营', '某游戏公司研发总部正式开业运营'),
}


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Duration judgement: {label} required')
    return value


def source(record, sources, label):
    sid = text(record.get('source_id'), label + '.source_id')
    quote = text(record.get('quote'), label + '.quote')
    if not isinstance(sources.get(sid), str) or quote not in sources[sid]:
        raise ValueError(f'Duration judgement: untraceable {label}')


def validate_judgement(value, days, calendar, sources, node_key, stage_label):
    if not isinstance(value, dict):
        raise ValueError('Duration judgement required for every nonzero work package')
    j = copy.deepcopy(value)
    for field in ('scope', 'stage', 'selection_reason'):
        text(j.get(field), field)
    scale = j.get('scale')
    if not isinstance(scale, dict) or scale.get('status') not in ('STATED', 'ASSUMED', 'UNKNOWN'):
        raise ValueError('Duration judgement: explicit scale status required')
    text(scale.get('description'), 'scale.description')
    cal = j.get('calendar')
    if not isinstance(cal, dict) or cal.get('mode') not in ('5D', '6D', '7D'):
        raise ValueError('Duration judgement: explicit supported calendar required')
    if cal['mode'] != calendar:
        raise ValueError('Duration judgement calendar conflicts with scheduled calendar')
    text(cal.get('basis'), 'calendar.basis')
    bounds = j.get('range')
    if not isinstance(bounds, dict) or bounds.get('unit') != 'working_day':
        raise ValueError('Duration judgement: working_day range required; no silent week/month conversion')
    low, high = bounds.get('low'), bounds.get('high')
    if any(type(v) is not int or v <= 0 for v in (low, high, days)) or not low <= days <= high:
        raise ValueError('Duration judgement: selected days must lie within positive ordered range')
    if j.get('basis') not in ('PM_ESTIMATE', 'HISTORICAL', 'SUPPLIER_QUOTE'):
        raise ValueError('Duration judgement: project estimate basis required; no synthetic or inferred L3')
    source(j, sources, 'estimate')
    binding=j.get('binding')
    expected=dict(node_key=node_key,stage_label=stage_label,selected_workdays=days,
                  low=low,high=high,unit='working_day',calendar=calendar)
    if not isinstance(binding,dict) or binding!=expected or any(type(binding.get(k)) is not int for k in ('selected_workdays','low','high')):
        raise ValueError('Duration judgement: stage/numeric binding conflicts with scheduled work package')
    # Check lexical anchors as well as typed equality. This is not NLP truth verification.
    numbers=set(re.findall(r'(?<![\d.])\d+(?:\.\d+)?(?![\d.])',j['quote']))
    if stage_label not in j['quote'] or not {str(low),str(days),str(high)}<=numbers or calendar not in j['quote']:
        raise ValueError('Duration judgement: quote must identify this stage, selected days, range and calendar')
    if not any(unit in j['quote'] for unit in ('工作日','working_day')):
        raise ValueError('Duration judgement: quoted working-day unit required')
    assumptions = j.get('assumptions')
    if not isinstance(assumptions, list) or not assumptions:
        raise ValueError('Duration judgement: explicit assumptions required')
    for a in assumptions:
        text(a, 'assumption')
    missing = j.get('missing_inputs')
    if not isinstance(missing, list):
        raise ValueError('Duration judgement: explicit missing_inputs list required')
    for item in missing:
        text(item, 'missing input')
    if scale['status'] == 'UNKNOWN' and not missing:
        raise ValueError('Unknown scale must be disclosed in missing_inputs')
    review = j.get('review')
    if not isinstance(review, dict) or review.get('status') not in ('UNREVIEWED', 'PM_REVIEWED'):
        raise ValueError('Duration judgement: explicit review status required')
    if review['status'] == 'PM_REVIEWED':
        text(review.get('reviewer'), 'review.reviewer')
        date.fromisoformat(text(review.get('date'), 'review.date'))
        source(review, sources, 'review')
    j.update(selected_workdays=days, evidence_check='STAGE_NUMERIC_MATCH_SEMANTICS_NOT_VERIFIED',
             commitment_allowed=False, calibration_status='NOT_ASSESSED')
    return j


def validate_milestone(node, sources):
    if node.get('node_type') != 'milestone':
        raise ValueError('Zero duration requires an explicit milestone, not an unquantified activity')
    if node.get('name') not in MILESTONE_CONTRACTS.get(node.get('key'),()):
        raise ValueError('Zero duration milestone key/name outside bounded milestone contract')
    if node.get('duration_judgement') or node.get('scope_keys'):
        raise ValueError('A work package cannot be relabelled as a zero-duration milestone')
    release = node.get('release_condition')
    if not isinstance(release, dict):
        raise ValueError('Milestone release condition required')
    text(release.get('description'), 'release_condition.description')
    source(release, sources, 'milestone release')


ALLOWED_JUSTIFICATION_PATHS = (
    'PHASED_CUTOVER', 'DUAL_PATH_STAGING', 'EXISTING_INFRA_REUSE', 'SCOPE_CARVEOUT', 'FAST_TRACK'
)


def validate_deviation(dev):
    if not isinstance(dev, dict):
        raise ValueError('Experience floor deviation must be an object')
    status = dev.get('status')
    if status != 'ACCEPTED_WITH_DEVIATION':
        raise ValueError("Experience floor deviation status must be 'ACCEPTED_WITH_DEVIATION'")
    dev_days = dev.get('deviation_days')
    if type(dev_days) is not int or dev_days >= 0:
        raise ValueError('Experience floor deviation_days must be a negative integer')
    path = text(dev.get('justification_path'), 'deviation.justification_path')
    if path not in ALLOWED_JUSTIFICATION_PATHS:
        raise ValueError(f"justification_path must be one of {ALLOWED_JUSTIFICATION_PATHS}")
    text(dev.get('rationale'), 'deviation.rationale')
    text(dev.get('invalidation_condition'), 'deviation.invalidation_condition')
    return copy.deepcopy(dev)


def experience_disclosures(plans, sources):
    """Keep unconverted experience visible; never silently turn months into WD."""
    records=[]
    for workflow,spec in plans.items():
        floors=spec.get('experience_floors')
        if floors is None or floors==[]:
            records.append(dict(workflow=workflow,status='EXPERIENCE_REQUIREMENTS_NOT_PROVIDED',
                experience_floor_not_enforced=True,reason='No whole-workflow experience-floor assessment supplied'))
            continue
        if not isinstance(floors,list):raise ValueError('experience_floors must be a list')
        ids=set()
        for floor in floors:
            if not isinstance(floor,dict):raise ValueError('Invalid experience floor record')
            fid=text(floor.get('id'),'experience floor id')
            if fid in ids:raise ValueError('Duplicate experience floor id')
            ids.add(fid);source(floor,sources,'experience floor')
            stages=floor.get('stages');estimates=spec['estimates']
            if not isinstance(stages,list) or not stages or any(not isinstance(s,str) or s not in estimates for s in stages) or len(stages)!=len(set(stages)):
                raise ValueError('Experience floor requires distinct included stages')
            threshold=floor.get('threshold')
            if not isinstance(threshold,dict) or type(threshold.get('value')) is not int or threshold['value']<=0 or threshold.get('unit') not in ('calendar_month','calendar_week','calendar_day','working_day'):
                raise ValueError('Explicit positive experience threshold and unit required')
            modes={estimates[s].get('calendar') for s in stages}
            selected_days=sum(estimates[s]['days'] for s in stages) if len(modes)==1 and None not in modes else None
            rec=dict(workflow=workflow,id=fid,stages=stages,threshold=copy.deepcopy(threshold),
                     source_id=floor['source_id'],quote=floor['quote'],
                     selected_stage_workdays=selected_days)
            dev=floor.get('deviation')
            if dev is not None:
                validated_dev=validate_deviation(dev)
                rec.update(status='ACCEPTED_WITH_DEVIATION',
                           experience_floor_not_enforced=True,
                           deviation=validated_dev,
                           reason=f"Deviation accepted via {validated_dev['justification_path']}; invalidation condition disclosed.")
            else:
                rec.update(status='EXPERIENCE_FLOOR_NOT_ENFORCED',
                           experience_floor_not_enforced=True,
                           reason='Experience reference only; aggregate floor has not been adopted as a computational constraint. No implied PASS.')
            records.append(rec)
    return records

