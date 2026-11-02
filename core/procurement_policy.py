"""Explicit client procurement profile; invitation is not negotiation authority."""
PROFILE = 'enterprise_3round_tech_commercial'

def policy(facts):
    selected = facts.get('procurement_policy', 'unspecified')
    regime = facts.get('procurement_regime', 'unknown')
    errors = []
    if selected == PROFILE and regime == 'formal_tender':
        errors.append(dict(field='procurement_policy', reason='三轮议价与 formal_tender 冲突；请确认适用采购制度，不自动改成企业比选。'))
    return dict(profile=selected, effective_regime='enterprise_selection' if selected == PROFILE and not errors else regime,
                negotiation_allowed=selected == PROFILE and not errors,
                quotation_rounds=3 if selected == PROFILE and not errors else None,
                evaluation='technical_and_commercial' if selected == PROFILE else None,
                application='设计服务及DB/GC发包；不自动套用框架订单或甲供设备', blockers=errors)

def apply_three_round(tasks, facts):
    """Replace procurement-phase activities, retaining external milestone IDs."""
    from core._common import parse_predecessor
    p = policy(facts)
    if p['blockers']: raise ValueError(str(p['blockers']))
    # Unspecified procurement still has clarification, never inferred bargaining.
    negotiated=p['negotiation_allowed']
    from core.duration_derivation import statutory_tender_required
    formal=statutory_tender_required(facts) or facts.get('procurement_regime')=='formal_tender'
    public=facts.get('bidding')=='public'
    phases = [t for t in tasks if t.get('outline_level') == 2 and
              ('Design Procurement' in t['name'] or any(k in t['name'] for k in ('总包','总承包')) and ('招标' in t['name'] or '定标' in t['name']))]
    if not phases: raise ValueError('PROCUREMENT_PHASE_MISSING')
    nextid = max(t['id'] for t in tasks) + 1
    for header in phases:
        start = tasks.index(header) + 1
        end = next((i for i in range(start,len(tasks)) if tasks[i].get('outline_level',3)<=2),len(tasks))
        old = tasks[start:end]
        if not old: raise ValueError('EMPTY_PROCUREMENT_PHASE')
        design = 'Design Procurement' in header['name']
        package = 'design' if design else 'gc'
        prefix = '设计服务' if design else 'DB总承包' if facts.get('delivery')=='DB' else '施工总包'
        ids = {t['id'] for t in old}
        # Contract and LOA milestones may have been moved into the control panel.
        outside = {pid for t in old for pid,_,_ in parse_predecessor(str(t.get('predecessors','') or '')) if pid not in ids}
        descendant_milestones = {t['id'] for t in tasks if t.get('milestone') and any(pid in ids for pid,_,_ in parse_predecessor(str(t.get('predecessors','') or '')))}
        outside -= descendant_milestones
        if not outside: raise ValueError('PROCUREMENT_ENTRY_MISSING: '+package)
        from core.duration_derivation import procurement_step_days
        def _step_days(key, fallback):
            try:
                return int(procurement_step_days(key, facts, design=design, public=public, formal=formal)['duration_days'])
            except Exception:
                return fallback
        steps = [
            ('scope','RFP范围与评审口径准备',_step_days('scope', 3)),
            ('boq','工程量及可比报价口径准备',_step_days('boq', 5 if not design else 3)),
            ('access','公开征集及资格筛选' if public else '邀请名单与资格确认',_step_days('access', 5 if public else 3)),
            ('issue','发出RFP及统一报价口径',0),
            ('clarify','踏勘与技术答疑',_step_days('clarify', 2)),
            ('prepare','法定投标准备窗口（招标投标法第24条，不少于20日）' if (formal and not design) else '技术方案与报价编制',_step_days('prepare', 20 if (formal and not design) else 5)),
            ('quote1','第一轮回标截止',0),
            ('technical1','技术标分析与履约能力评审',_step_days('technical1', 3)),
            ('commercial1','商务清标与可比报价分析',_step_days('commercial1', 3)),
            ('interview1','技术商务澄清与述标',_step_days('interview1', 1)),
        ]
        if negotiated:
            steps += [('prepare2','第二轮报价及VE澄清',3),('quote2','第二轮报价提交',0),
                      ('review2','二轮技术商务复核',2),('prepare3','第三轮最终承诺与报价',2),
                      ('quote3','第三轮最终报价提交',0)]
        else:
            steps += [('clarification_close','评审澄清闭合（不改变实质内容）',2)]
        steps += [('evaluation','综合评审与定标建议',2),('approval','业主定标审批',2)]
        if formal and not design:
            steps += [('publicity','中标候选人公示（实施条例第54条，不少于3日）',3)]
        steps += [('award','定标通知发出',0),('contract','合同附件闭合与签署',3)]
        nodes=[]; bykey={}
        kickoff=next((t for t in tasks if 'Kick Off' in t['name']), None)
        brief=next((t for t in tasks if '(Brief)' in t['name'] or '任务书' in t['name']), None)
        for key,label,days in steps:
            prev = [nodes[-1]['id']] if nodes else sorted(outside)
            if key=='boq':prev=sorted(outside)
            if key=='access':prev=sorted(outside)  # Prequalification can start with the approved package brief.
            if not design and facts.get('delivery')=='DBB' and key in ('scope','boq','access'):
                # Takeoff during IFC production; RFP issue still waits for 全专业施工图完成.
                cd=next((t for t in tasks if '全专业施工图' in t['name'] and t.get('duration_days',0)>0), None)
                if cd:
                    prev=[str(cd['id'])+'SS']
            if design and kickoff and key in ('scope','boq','access'):
                prev=[kickoff['id']]
            if not design and facts.get('delivery')=='DB' and key in ('scope','boq','access') and kickoff:
                # D&B: RFP assembly overlaps employer brief.
                prev=[kickoff['id']]
            if key=='issue':
                prev=[bykey[k]['id'] for k in ['scope','access','boq']]
                # D&B RFP can be issued on a draft ER; employer brief continues in parallel.
            if key=='approval' and not public and not formal and 'interview1' in bykey:
                prev=[bykey['interview1']['id']]
            if key=='prepare': prev=[bykey['issue']['id']]
            if key=='quote1': prev=[bykey['prepare']['id'],bykey['clarify']['id']]
            if key=='commercial1': prev=[bykey['quote1']['id']]
            if key=='interview1':prev=[bykey['technical1']['id'],bykey['commercial1']['id']]
            if key=='negotiate1': prev=[bykey['interview1']['id']]
            if key=='evaluation' and 'interview1' in bykey:
                prev=[bykey['interview1']['id']]
            if key=='award':
                prev=[bykey['evaluation']['id'], bykey['approval']['id']]
                if 'clarification_close' in bykey:
                    prev.append(bykey['clarification_close']['id'])
                if 'publicity' in bykey:
                    prev.append(bykey['publicity']['id'])
            node=dict(id=nextid,name=prefix+'：'+label,outline_level=3,duration_days=days,duration=days,
                      milestone=days==0,calendar='7D',
                      work_weekend=key=='publicity' or key=='prepare' and (formal or public), phase=prefix+'企业采购（三轮报价）',
                      predecessors=','.join(map(str,prev)),module_id='procurement_'+package,
                      activity_uid='pmp.'+package+'.'+('bid_deadline' if key=='quote1' else 'bid_issue' if key=='issue' else key),
                      procurement_step=key,procurement_profile=PROFILE if negotiated else 'formal_tender' if formal else 'clarification_only',
                      activity_class='CLIENT_APPROVAL' if key=='approval' else 'TENDER_COMMERCIAL',
                      activity_type='tender',duration_authority='uncalibrated_procurement_planning',
                      duration_basis='未校准PM采购规划值；仅formal_tender适用的窗口引用法定下限，企业征集窗口为规划量',
                      acceptance='评审记录、授权审批及一致的合同附件；不预设评分权重或最低价中选')
            if key=='publicity' or key=='prepare' and (formal or public):
                node.update(ignore_holidays=True,duration_unit='calendar_days',elapsed_window=True)
            if formal and facts.get('delivery')=='DBB' and key in (
                    'evaluation','approval','contract','technical1','commercial1','interview1','clarification_close'):
                node.update(calendar='7D', ignore_holidays=True, elapsed_window=True, duration_unit='calendar_days')
            nextid+=1;nodes.append(node);bykey[key]=node
        if not design and facts.get('delivery')=='DBB':
            drawing=next((t for t in tasks if '全专业施工图' in t['name']
                          and int(t.get('duration_days') or 0) > 0
                          and '审查' not in t['name']), None)
            if drawing:
                bykey['issue']['predecessors']+=','+str(drawing['id'])+'FS'
        replacement=nodes[-1]['id']
        for t in tasks:
            if t['id'] in ids: continue
            edges=[]
            for pid,rel,lag in parse_predecessor(str(t.get('predecessors','') or '')):
                if pid in ids:
                    pid=bykey['award']['id'] if 'LOA' in t['name'] or '中标通知' in t['name'] else replacement
                    rel='FS';lag=0
                edges.append(str(pid)+rel+(f'{lag:+d}' if lag else ''))
            t['predecessors']=','.join(dict.fromkeys(edges))
        header['name']=prefix+'采购（'+('公开' if public else '邀请')+('，三轮报价' if negotiated else '，评审澄清')+'）'
        tasks[start:end]=nodes
    return tasks
