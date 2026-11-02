"""Timeline public API; layout is implemented once in pm_timeline."""
def generate_pptx_milestones(project_title,project_meta,tasks,output_pptx_path,programme_certainty=None):
    from pathlib import Path
    from exporters.pm_timeline import generate
    from exporters.bd_timeline import generate as generate_bd
    result=generate(project_title,project_meta,tasks,output_pptx_path,programme_certainty)
    path=Path(output_pptx_path)
    bd_name=path.stem.replace('_Milestones','')+'_BD_KeyMilestones.pptx'
    generate_bd(project_title,project_meta,tasks,str(path.with_name(bd_name)))
    return result

def main():
    import argparse,json
    parser=argparse.ArgumentParser(description="Export PM timeline")
    parser.add_argument('--json',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args()
    with open(args.json,encoding='utf-8') as stream:data=json.load(stream)
    print(generate_pptx_milestones(data.get('project_title',data.get('project_name','项目')),data.get('project_meta',{}),data.get('tasks',[]),args.out))

if __name__=='__main__':main()
