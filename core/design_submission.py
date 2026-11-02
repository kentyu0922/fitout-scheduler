"""Design adequacy belongs before signed drawings enter third-party review."""
from core.module_graph import add_pred, dependency_order

def align_submission_conditions(tasks):
    stamp=next((t for t in tasks if 'LDI' in t.get('name','') or '本地设计复核与盖章' in t.get('name','')),None)
    if stamp is None:return []
    drawing=next((t for t in tasks if '全专业施工图' in t.get('name','')),None)
    checks=[t for t in tasks if t.get('industry_stage')=='design' or
            ('公共工程与专项' in t.get('name','') and '分界' in t.get('name',''))]
    changed=[]
    for t in checks:
        if t['id']==stamp['id']:continue
        add_pred(stamp,t);changed.append(t['id'])
    stamp['input_notes']=str(stamp.get('input_notes',''))+'\n正式签章前完成行业及公共/专项设计接口核查。建设单位组织合格设计单位送审；DBB图审不以施工定标作为通用前置，施工许可另待施工主体和适用资料齐备。'
    dependency_order(tasks)
    return changed
