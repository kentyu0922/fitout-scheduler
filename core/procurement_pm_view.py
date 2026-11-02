"""Round-level reporting only. Keep every live tender activity and release."""
GROUPS=[('RFP及可比报价口径准备',{'scope','boq'}),
 ('Pre-Q与投标名单确认',{'access'}),
 ('第一轮报价（含发标答疑）',{'issue','clarify','prepare','quote1'}),
 ('首轮技术商务评审及面试',{'technical1','commercial1','interview1','negotiate1'}),
 ('第二轮澄清报价及复核',{'prepare2','quote2','review2','negotiate2'}),
 ('第三轮报价及综合评审',{'prepare3','quote3','evaluation'}),
 ('定标审批与通知',{'approval','award'}),
 ('合同签署',{'contract'})]

def group_rounds(children,seq,package,basis):
    """Return real L4 round summaries and filtered L5 calculation details."""
    known=set().union(*(keys for _,keys in GROUPS))
    if any(t.get('procurement_step') not in known for t in children):
        raise ValueError('UNKNOWN_PROCUREMENT_STEP_IN_PM_VIEW')
    out=[]
    for index,(label,keys) in enumerate(GROUPS,1):
        work=[t for t in children if t.get('procurement_step') in keys]
        if not work:continue
        head=dict(id=seq,name=label,client_task_name=label,
            activity_uid=f'pmp.pmview.procurement.{seq}',outline_level=4,level=4,
            summary=True,milestone=False,duration=0,duration_days=0,predecessors='',
            start=min(t['start'] for t in work),finish=max(t['finish'] for t in work),
            reporting_phase='招采阶段',master_phase='招采阶段',client_visible=True,
            pm_view_package=True,reporting_package=package,package_boundary_basis=basis,
            procurement_view_group_order=index,
            input_notes='完成条件与计算范围：'+'；'.join(t['name'].split('：')[-1] for t in work)+'。明细依赖不因主表汇总而改变。')
        seq+=1;out.append(head)
        for t in work:t.update(outline_level=5,level=5,pm_view_detail=True,reporting_package=package,package_boundary_basis=basis)
        out.extend(work)
    return out,seq
