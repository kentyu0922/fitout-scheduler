"""Apply only the explicitly selected whole-area handover condition."""
def align_handover(tasks,mode):
    changes=[]
    if mode=='whole':
        gates=[t for t in tasks if '隔墙及主管管线隐蔽' in t.get('name','')]
        if len(gates)!=1:raise ValueError('WHOLE_HANDOVER_REQUIRES_UNIQUE_WALL_INSPECTION')
        gate=gates[0]
        for task in tasks:
            if task.get('activity_type')!='ceiling_grid':continue
            old=task.get('predecessors','')
            link=str(gate['id'])+'FS'
            if link not in old.split(','):
                task['predecessors']=','.join(x for x in [old,link] if x)
                task['input_notes']=str(task.get('input_notes',''))+'\n本次按整体工作面移交：天花龙骨待隔墙及主管管线隐蔽验收完成，不采用未确认的分区提前释放。'
                changes.append(dict(id=task['id'],name=task['name'],before=old,after=task['predecessors']))
    return dict(mode=mode,changes=changes,duration_calibrated=False,
        note='仅落实明确的工作面移交约定；不代表班组、工程量或隐蔽验收间隔已校准。')
