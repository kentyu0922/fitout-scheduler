"""PM industry discovery only: no graph mutation, no schedule approval."""
import json
from pathlib import Path

CATALOG=Path(__file__).resolve().parents[1]/'profiles/sectors.json'


def recommend_profile(project):
    cfg=json.loads(CATALOG.read_text(encoding='utf-8'))
    if not isinstance(project,dict):raise ValueError('Project profile must be an object')
    sector=project.get('sector')
    if sector not in cfg['sectors']:raise ValueError('Unknown sector; use office/gaming/internet/finance/biopharma/semiconductor')
    spaces=project.get('space_types',[])
    if not isinstance(spaces,list) or any(not isinstance(s,str) or s not in cfg['spaces'] for s in spaces):
        raise ValueError('space_types must be a list of supported space identifiers')
    suggestions=[]
    for space in dict.fromkeys(spaces):
        for module in cfg['spaces'][space]['modules']:
            if any(s['module_id']==module for s in suggestions):continue
            suggestions.append(dict(module_id=module,reason=f'实际空间：{cfg["spaces"][space]["label"]}',
                                    **cfg['modules'][module],selected=False))
    experience=[]
    if sector=='gaming':
        register=CATALOG.parent/'experience/gaming_master_programme.json'
        experience=json.loads(register.read_text(encoding='utf-8'))['items']
    return dict(schema_version=cfg['version'],sector=sector,sector_label=cfg['sectors'][sector]['label'],
                experience_review_cards=experience,
                space_types=spaces,management_focus=cfg['sectors'][sector]['focus'],
                suggested_modules=suggestions,enabled_modules=[],schedule_changed=False,
                needs_space_confirmation=not spaces,
                scope_warning='生产工艺未纳入普通装修引擎能力，需独立范围设计' if 'production' in spaces else None,
                status='RECOMMENDATION_ONLY',
                independent_dimensions={k:project.get(k) for k in ('delivery','bidding','city','calendar_profile')})


def run_cli(argv):
    import argparse
    parser=argparse.ArgumentParser(description='Industry and space discovery; does not schedule tasks')
    parser.add_argument('--sector_profile',required=True)
    parser.add_argument('--profile_output',required=True)
    args=parser.parse_args(argv)
    result=recommend_profile(json.loads(Path(args.sector_profile).read_text(encoding='utf-8-sig')))
    output=Path(args.profile_output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Industry recommendation saved: {output}; no dates changed.')
