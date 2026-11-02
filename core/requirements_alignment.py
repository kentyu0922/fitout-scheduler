"""Parallel fact gathering; final brief remains the common release gate."""
from core._common import parse_predecessor

def align_requirements(tasks):
    kickoff=next((t for t in tasks if t.get('activity_uid') in {'c53c0b6e-4404-5daf-b69d-7da21439a0c3','74b19f98-7fef-5a60-85c1-ae72cf35fdee'}),None)
    brief=next((t for t in tasks if t.get('activity_uid') in {'0973b1f8-34cb-520b-afca-12eb18fc22ec','0701fe82-5434-536f-be52-55a8fd6fa837'}),None)
    kickoff=kickoff or next((t for t in tasks if 'Kick Off' in t.get('name','')),None)
    brief=brief or next((t for t in tasks if '需求' in t.get('name','') and '任务书编制' in t.get('name','')),None)
    if not kickoff or not brief:raise ValueError('REQUIREMENTS_START_ANCHORS_MISSING')
    gates={t['id'] for t in tasks if t.get('industry_stage')=='requirements'}
    changes=[]
    for t in tasks:
        name=t.get('name','')
        workshop=t.get('industry_work_stage')=='requirements' or t.get('lifecycle_step')=='requirements'
        pep='PEP' in name
        if not(workshop or pep):continue
        before=t.get('predecessors','');links=parse_predecessor(before)
        if pep:links=[(kickoff['id'],'FS',0)]
        else:
            links=[(brief['id'],'SS',0) if p==brief['id'] or p in gates else (p,k,l) for p,k,l in links]
        t['predecessors']=','.join(str(p)+k+(f'{l:+d}d' if l else '') for p,k,l in dict.fromkeys(links))
        t['input_notes']=str(t.get('input_notes',''))+'\n初步需求收集与Brief编制并行；最终任务书仍须行业/专项需求、物业条件与PEP齐备后批准，不代表跳过范围冻结。'
        if before!=t['predecessors']:changes.append(dict(id=t['id'],before=before,after=t['predecessors']))
    return changes
