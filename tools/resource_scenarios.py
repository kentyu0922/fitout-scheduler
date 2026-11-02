"""Propose area-based trade teams and re-solve copies; never replace a baseline."""
import argparse
import copy
import json
import math
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.productivity import load_productivity_rates, compute_activity_duration
from core.solver_engine import solve_schedule
from core.optimizer import analyze_schedule
from core.schedule_quality import schedule_quality


def propose(baseline,area):
    if isinstance(area,bool) or not math.isfinite(float(area)) or area<=0:
        raise ValueError('area must be positive finite')
    before=copy.deepcopy(baseline)
    rates=load_productivity_rates()
    tasks=copy.deepcopy(baseline['tasks'])
    old_analysis=analyze_schedule(copy.deepcopy(baseline['tasks']),baseline['start_date'],verify=True)
    driving_ids={node['id'] for node in old_analysis['critical_path']['chain']}
    crews={}
    for task in tasks:
        if not task.get('productivity'):
            continue
        old=task['productivity']; entry=rates['activity_types'][task['activity_type']]
        policy=entry['crew_scaling']
        entry['rates_per_worker_day']=copy.deepcopy(old['rate_table'])
        for key in ('source','evidence','rate_status','scope_id'):
            entry[key]=copy.deepcopy(old[key])
        spaces=min(policy['max_workfronts'],max(1,math.ceil(area/policy['area_per_front'])))
        # Existing configured default team per available workspace. This is an
        # explicit demand scenario, not an inferred contractor commitment.
        crews[task['activity_type']]=max(old['crew_size'],min(spaces*entry['default_crew_size'],spaces*policy['max_workers_per_front'])) if task['id'] in driving_ids else old['crew_size']
        proposal=compute_activity_duration(task['activity_type'],old['quantity'],crew_size=crews[task['activity_type']],
                                          rate_level=old['rate_level'],quantity_source=old['quantity_source'],
                                          factors=old['factors'],shifts=old['shifts'],workfront_efficiency=old['workfront_efficiency'],
                                          project_area=area,workfronts=old['workfronts'],rates=rates)
        task.update(duration_days=proposal['final_duration'],duration=proposal['final_duration'],productivity=proposal)
    solved=solve_schedule(tasks,baseline['start_date'])
    analysis=analyze_schedule(solved['tasks'],baseline['start_date'],verify=True)
    assert baseline==before
    return dict(proposal_only=True,baseline_modified=False,confidence='low',
                assumption='Additional teams only on the original driving chain, per configured area workspace; headcount availability and congestion require contractor validation. Acceptance-zone count unchanged.',
                before_finish=baseline['finish_date'],after_finish=solved['finish_date'],
                before_quality=schedule_quality(baseline['tasks'],old_analysis),after_quality=schedule_quality(solved['tasks'],analysis),
                crew_plan=crews,tasks=solved['tasks'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',required=True);parser.add_argument('--area',type=float,required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    source=Path(args.baseline).resolve();output=Path(args.output).resolve()
    if source==output: parser.error('Cannot overwrite baseline')
    report=propose(json.loads(source.read_text(encoding='utf-8')),args.area)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    output.with_name(output.stem+'_crews.json').write_text(json.dumps(report['crew_plan'],ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('proposal_only','before_finish','after_finish')},ensure_ascii=False))
