"""Client phase membership only; never a scheduling or scope decision."""
PHASES = ('前期规划', '设计阶段', '招采阶段', '报审阶段', '施工阶段', '竣工验收阶段', '搬迁阶段')
SUPPLY_TRACK='长周期设备供货跟踪（并行）'
DISPLAY_PHASES=PHASES[:4]+(SUPPLY_TRACK,)+PHASES[4:]

def reporting_phase(t, legacy):
    module = str(t.get('module_id', ''))
    step = t.get('module_step')
    if not t.get('iaq_role') and (legacy=='设备采购' and module not in {'procurement_design','procurement_gc','procurement_db','procurement_furniture'} or module=='module_procurement' or t.get('furniture_supply')):
        return SUPPLY_TRACK
    if module=='office_ff_move' and step==4:return PHASES[6]
    if module == 'core_handover' and step in {3,4,5}:
        return PHASES[6]
    if legacy == 'IT实施' and (t.get('lifecycle_step') in {'cutover','go_no_go','observe'} or module == 'it_cutover' and step in {3,4}):
        return PHASES[6]
    if t.get('reporting_phase_override'):
        return t['reporting_phase_override']
    if '物理竣工' in str(t.get('name','')): return PHASES[4]
    if t.get('authority_stage'):
        return PHASES[5] if t['authority_stage']=='completion' else PHASES[3]
    name = str(t.get('name', ''))
    uid = str(t.get('activity_uid', ''))
    module = str(t.get('module_id', ''))
    step = t.get('module_step')
    kind = t.get('activity_type')
    stage = t.get('industry_work_stage') or t.get('industry_stage')
    if stage:
        return dict(requirements=PHASES[0], design=PHASES[1], inspection=PHASES[4],
                    acceptance=PHASES[5], release=PHASES[5])[stage]
    if kind in {'tender', 'procurement_receipt'} or t.get('activity_class')=='TENDER_COMMERCIAL' or module.startswith('procurement_') or module == 'module_procurement':
        return PHASES[2]
    if legacy in {'设计采购', '施工采购', '设备采购'}:
        return PHASES[2]
    if module == 'office_ff_move':
        return PHASES[0] if step == 4 else PHASES[4]
    if t.get('semantic_phase') == 'IAQ_FLUSHOUT' or '室内空气' in name or '强排通风与空气净化散味' in name:
        return PHASES[5]
    if kind in {'testing_commissioning', 'snagging', 'final_cleaning'} or '物理竣工' in name:
        return PHASES[5]
    if legacy in {'验收审批', '交付搬迁'} or t.get('project_target'):
        return PHASES[5]
    if legacy == 'IT实施':
        if t.get('lifecycle_step') in {'design','plan','rehearsal'}: return PHASES[1]
        if module == 'it_cutover' and step == 2: return PHASES[4]
        if uid == 'pmp.integration.it.circuits': return PHASES[4]
        return PHASES[5]
    if legacy == '开工许可':
        # Internal technical readiness is not an authority approval.
        if t.get('lifecycle_step') in {'source','utility_scope','early_release'}: return PHASES[1]
        return PHASES[3]
    if legacy == '设计与审批':
        if t.get('activity_class') in {'AUTHORITY_APPROVAL','LANDLORD_APPROVAL'} or any(s in name for s in ('施工图审查','图审','审查合格书','消防设计审查','图纸送审')):
            return PHASES[3]
        return PHASES[1]
    if legacy == '前期策划':
        if any(s in name for s in ('招标','RFP','定标','生产交货','Lead Time','采购','合同签')): return PHASES[2]
        return PHASES[0]
    if legacy in {'实体施工','家具与空气'}:
        from core.master_schedule import MODULE_GROUPS
        package=MODULE_GROUPS.get(module,{}).get(step)
        if package and package[0]=='test': return PHASES[5]
        if t.get('lifecycle_step') in {'performance','iq','oq','pq','monitor','release','operation'}: return PHASES[5]
        if t.get('specialist_phase') == '验收审批': return PHASES[5]
        return PHASES[4]
    raise ValueError(f'Unmapped reporting phase: {legacy!r}, task {t.get("id")}')


def procurement_caption(t, caption):
    """Keep the purchase object visible when formerly separate L2s are united."""
    module = t.get('module_id', '')
    label = t.get('reporting_procurement_object') or {'procurement_design':'设计服务','procurement_gc':'施工总包',
             'procurement_db':'设计施工总包'}.get(module)
    if not label:
        label = {'设计采购':'设计服务','施工采购':'施工总包'}.get(t.get('master_phase'))
    if module == 'procurement_gc' and any(x in str(t.get('name','')) for x in ('设计施工一体','DB总承包')): label = '设计施工总包'
    if label:
        return label + '：' + caption
    return caption
