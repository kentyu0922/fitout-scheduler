"""Classify real activities for the client master reading hierarchy.

Display packages never replace activity execution or release conditions.
"""
import copy
from core._common import parse_predecessor
from core.module_graph import dependency_order
from core.module_catalog import CATALOG

STAGE_PHASE = {'requirements':'前期策划','design':'设计与审批','inspection':'实体施工',
               'acceptance':'验收审批','release':'交付搬迁'}

# The common workface has one release. Specialist/system interfaces remain
# separate where the downstream user needs a distinct completion condition.
TRADE_GROUPS = {
 'site_setup':('site','进场准备'), 'setting_out':('setting','测量放线'),
 'partition_framing':('rough','隔墙骨架及机电粗装'), 'hvac_duct':('rough','隔墙骨架及机电粗装'),
 'chw_pipe':('rough','隔墙骨架及机电粗装'), 'containment':('rough','隔墙骨架及机电粗装'),
 'gypsum_boarding':('second','隔墙封板及天花机电安装'), 'ceiling_grid':('second','隔墙封板及天花机电安装'),
 'fcu':('second','隔墙封板及天花机电安装'), 'sprinkler':('second','隔墙封板及天花机电安装'),
 'fire_alarm':('second','隔墙封板及天花机电安装'), 'structured_cabling':('second','隔墙封板及天花机电安装'),
 'power':('second','隔墙封板及天花机电安装'),
 'gypsum_ceiling':('finishing','装饰收口及机电末端'), 'feature_ceiling':('finishing','装饰收口及机电末端'),
 'wall_prep':('finishing','装饰收口及机电末端'),
 'painting':('finishing','装饰收口及机电末端'), 'wallcovering':('finishing','装饰收口及机电末端'),
 'raised_floor':('floor_doors','地面、门体及固定装饰'),
 'carpet':('floor_doors','地面、门体及固定装饰'), 'vinyl':('floor_doors','地面、门体及固定装饰'),
 'tile_stone':('floor_doors','地面、门体及固定装饰'),
 'glass_partition':('floor_doors','地面、门体及固定装饰'), 'doors':('floor_doors','地面、门体及固定装饰'),
 'joinery':('floor_doors','地面、门体及固定装饰'),
 'diffuser':('finishing','装饰收口及机电末端'), 'lighting':('finishing','装饰收口及机电末端'),
 'testing_commissioning':('tc','系统联调'), 'snagging':('snag','竣工整改'),
 'construction_cleaning':('clean','工程保洁'), 'security':('basic_security','门禁与安防安装'),
 'furniture':('furniture','家具安装'), 'av':('av','会议影音系统'),
 'final_cleaning':('final_clean','交付保洁'), 'move_planning':('move_plan','搬迁策划'),
}
MODULE_GROUPS = {
 'datacenter':{1:('base','机房基础及机柜'),5:('base','机房基础及机柜'),
              2:('power_cooling','供电与冷却配套'),4:('power_cooling','供电与冷却配套'),
              3:('base','机房基础及机柜'),6:('network','机柜配线与网络接入'),
              7:('test','机房综合测试'),8:('ready','IT部署条件移交')},
 'vault_security':{1:('enclosure','保管空间围护及门体'),2:('install','门禁与安防安装'),
                   3:('test','安防与消防联动测试'),4:('handover','安防权限移交')},
 'it_cutover':{1:('plan','迁移及回退方案'),2:('deploy','目标环境部署'),
              3:('cutover','业务切换'),4:('ready','业务就绪确认')},
 'mocap':{1:('enclosure','声学围护'),2:('enclosure','声学围护'),
          3:('capture','动捕系统安装'),4:('capture','动捕系统安装'),
          5:('finish','声学门及吸声饰面'),6:('test','声学性能测试'),7:('ready','专项空间验收')},
 'lab_hvac':{1:('install','实验室通风设施'),2:('install','实验室通风设施'),
             3:('test','通风性能及联锁测试'),4:('ready','通风系统验收')},
 'cleanroom_iso':{1:('install','受控空间围护及末端'),2:('install','受控空间围护及末端'),
                  3:('test','受控环境性能检测'),4:('ready','受控环境验收')},
 'trading_desk':{1:('infra','交易岗位电力及网络'),2:('infra','交易岗位电力及网络'),
                 3:('deploy','交易终端部署'),4:('test','交易功能及恢复测试'),5:('ready','交易业务就绪')},
 'anti_vibration':{1:('works','隔振设施'),2:('works','隔振设施'),3:('test','运行振动测试'),4:('ready','微振验收')},
 'esd_grounding':{1:('works','静电防护设施'),2:('works','静电防护设施'),3:('test','静电防护检测'),4:('ready','静电防护验收')},
 'cda_piping':{1:('gas','工艺气体管路'),2:('cda','压缩空气系统'),3:('test','介质系统试验'),4:('ready','介质投用确认')},
 'dev_zone':{0:('works','高密研发工位配套')},
}
UID_NAMES = {
 '2472dab9-baca-5826-99f6-e9e8cd741f43':('iaq_sampling','首次室内空气采样','家具与空气'),
 'pmp.integration.power_ready':('power_ready','送电及电气安全检查','实体施工'),
 'pmp.integration.hvac_ready':('hvac_ready','空调运行及平衡测试','实体施工'),
 'pmp.integration.security.rough':('trade.rough','隔墙骨架及机电粗装','实体施工'),
 'pmp.integration.security.point':('sec_test_prep','安防点测及权限配置','实体施工'),
 'pmp.integration.security.rights':('sec_test_prep','安防点测及权限配置','实体施工'),
 'pmp.integration.dc.prestart':('dc_start','机房单机及接口测试','实体施工'),
 'pmp.integration.dc.fire_interface':('dc_start','机房单机及接口测试','实体施工'),
 'pmp.integration.dc.clean':('dc_start','机房单机及接口测试','实体施工'),
 'pmp.integration.dc.submittal':('dc_submittal','机房设备报审','设备采购'),
 'pmp.integration.it.circuits':('it_circuit','线路开通及网络验证','IT实施'),
 'pmp.integration.it.backup':('it_backup','备份恢复验证','前期策划'),
 'pmp.integration.it.uat':('it_uat','终端业务验证','IT实施'),
 'pmp.integration.acoustic.concealed':('ac_concealed','声学隐蔽验收','实体施工'),
 'pmp.integration.acoustic.retest':('ac_retest','声学整改复测','实体施工'),
 'pmp.integration.equipment.workface':('eq_workface','设备进场条件检查','实体施工'),
}

def _phase_from_heading(name):
    if '总承包' in name and any(x in name for x in ('招标','定标','采购')):return '施工采购'
    if '采购' in name and any(x in name for x in ('长周期','家具','设备')):return '设备采购'
    if '设计服务' in name or '设计单位' in name:return '设计采购'
    if '施工总包' in name or '总包招' in name or '总承包采购' in name or '设计施工一体' in name:return '施工采购'
    if '许可证办理' in name:return '开工许可'
    if '专项设备采购' in name:return '设备采购'
    if '设计' in name:return '设计与审批'
    if '实体施工' in name:return '实体施工'
    if '竣工验收' in name:return '验收审批'
    if 'IT迁移' in name:return 'IT实施'
    if '搬迁入驻' in name:return '交付搬迁'
    return '前期策划'

def flatten_source(tasks):
    """Remove display headings only, expanding their zero-day start anchors."""
    rows=copy.deepcopy(tasks);byid={t['id']:t for t in rows}
    summaries=set()
    for a,b in zip(rows,rows[1:]):
        if int(b.get('outline_level',3))>int(a.get('outline_level',3)):
            summaries.add(a['id'])
    summaries.update(t['id'] for t in rows if int(t.get('outline_level',3))==2)
    summaries.discard(rows[0]['id'])
    phase='前期策划'; heading=''
    for t in rows:
        if int(t.get('outline_level',3))==2:
            heading=t['name']; phase=_phase_from_heading(heading)
        t['master_phase']=t.get('process_phase',phase)
        if phase in {'设计采购','施工采购'}:
            t['reporting_procurement_object']='设计服务' if phase=='设计采购' else '设计施工总包' if any(x in heading for x in ('D&B','DB总承包','设计施工一体')) else '施工总包'
    def expand(pid,kind,lag,seen):
        if pid not in summaries:return [(pid,kind,lag)]
        if pid in seen:raise ValueError('MASTER_SUMMARY_CYCLE')
        parent=byid[pid]
        if parent.get('duration_days',0) or kind!='FS' or lag:
            raise ValueError('MASTER_UNSUPPORTED_SUMMARY_LINK: '+str(pid))
        result=[]
        for p,r,l in parse_predecessor(str(parent.get('predecessors','') or '')):
            if r!='FS' or l:raise ValueError('MASTER_NON_FS_SUMMARY')
            result+=expand(p,r,l,seen|{pid})
        return result
    out=[]
    for t in rows:
        if t['id'] in summaries:continue
        links=[]
        for p,r,l in parse_predecessor(str(t.get('predecessors','') or '')):links+=expand(p,r,l,set())
        t['predecessors']=','.join(str(p)+r+(f'{l:+d}' if l else '') for p,r,l in dict.fromkeys(links))
        t['outline_level']=1 if t is rows[0] else 3
        if t is not rows[0]:t['summary']=False
        out.append(t)
    dependency_order(out)
    return out, sorted(summaries)

def assignments_for(tasks):
    result={}
    for t in tasks:
        if t.get('outline_level')==1:continue
        tid=t['id'];uid=t.get('activity_uid','');module=t.get('module_id');step=t.get('module_step')
        stage=t.get('industry_work_stage') or t.get('industry_stage')
        phase=t.get('master_phase','前期策划');name=None;key='single.'+str(tid)
        if stage:
            phase=STAGE_PHASE[stage]
            if t.get('industry_work_stage'):
                key='industry.'+stage
                from core.industry_base import WORK_PACKAGE_NAMES
                name=WORK_PACKAGE_NAMES[t.get('industry_profile') or t.get('industry','office')][stage]
        lifecycle=t.get('lifecycle_step')
        if lifecycle:
            if lifecycle in {'requirements','assessment','capacity','applicability','strategy','organization','retirement_scope'}:phase='前期策划'
            elif lifecycle in {'design','dq','plan','logistics'}:phase='设计与审批'
            elif lifecycle in {'supply'}:phase='设备采购'
            elif lifecycle in {'consent','early_release','source','utility_scope'}:phase='开工许可'
            elif lifecycle in {'release','assets','training','operation'}:phase='交付搬迁'
            elif lifecycle in {'iq','oq','pq','monitor'}:phase='验收审批'
            else:phase='实体施工'
            if module=='A05':
                phase='前期策划' if lifecycle in {'requirements','retirement_scope'} else 'IT实施'
                name={'requirements':'IT范围与中断条件确认','rehearsal':'切换与回退演练',
                      'go_no_go':'停机窗口批准','observe':'运行观察及支持交接',
                      'release':'IT接管确认','retirement_scope':'旧环境保留及退出责任'}.get(lifecycle)
            # Parallel requirements and specialist coordination share an owner
            # workshop; their distinct deliverables are retained in notes.
            if lifecycle in {'requirements','design'} and module in CATALOG:
                key='specialist.'+lifecycle
                name='专项需求确认' if lifecycle=='requirements' else '专项接口协调'
        if module and module.startswith('procurement_'):
            phase='设备采购' if module=='procurement_furniture' else '设计采购' if module=='procurement_design' else '施工采购'
        if module=='module_procurement' or uid.startswith('pmp.integration.receipt.'):
            phase='设备采购'
        if module in MODULE_GROUPS and step in MODULE_GROUPS[module]:
            g,name=MODULE_GROUPS[module][step];key=module+'.'+g
            phase='前期策划' if module=='it_cutover' and step==1 else ('IT实施' if module=='it_cutover' else '实体施工')
        if module=='tdd':phase='前期策划'
        if module=='office_ff_move':phase='前期策划' if step==4 else '家具与空气'
        if module=='core_handover':phase='交付搬迁'
        if t.get('activity_type') in TRADE_GROUPS:
            g,name=TRADE_GROUPS[t['activity_type']];key='trade.'+g
            phase='实体施工'
            if t['activity_type'] in {'furniture','av'}:phase='家具与空气'
            if t['activity_type']=='move_planning':phase='前期策划'
            if t['activity_type']=='final_cleaning':phase='交付搬迁'
        if uid in UID_NAMES:
            key,name,phase=UID_NAMES[uid]
        if '空气' in t['name'] and any(x in t['name'] for x in ('盲测','复测','净化','检测合格')):phase='家具与空气'
        if '物理竣工' in t['name']:phase='实体施工';name='物理竣工验收'
        if '隔墙及主管管线隐蔽' in t['name']:phase='实体施工';name='隔墙隐蔽验收'
        if '天花隐蔽工程验收' in t['name']:phase='实体施工';name='天花隐蔽验收'
        if '迁移方案' in t['name'] or t.get('project_target'):phase='交付搬迁' if t.get('project_target') else '前期策划'
        if t.get('specialist_phase'):phase=t['specialist_phase']
        if t.get('simple_renovation'):
            name={'trade.rough':'翻新管线与基层处理','trade.second':'天花基层及机电翻新',
                  'trade.finishing':'饰面收口及机电末端更换','trade.floor_doors':'地面、门窗及固定家具翻新'}.get(key,name)
        result[tid]={'key':key,'name':name or t['name'],'phase':phase}
    return result

def build_master_schedule(tasks):
    from core.master_graph import contract_work_packages
    flat,headings=flatten_source(tasks)
    assignments=assignments_for(flat)
    counts={}
    for spec in assignments.values():counts[spec['key']]=counts.get(spec['key'],0)+1
    for t in flat:
        if t['id'] in assignments:
            spec=assignments[t['id']]
            t['master_phase']=spec['phase']
            if counts[spec['key']]==1:t['master_task_name']=spec['name']
    # Reading groups must never become execution/release boundaries. In
    # particular, adding a specialist consumer must not change which base
    # activities are contracted and thereby shorten the baseline.
    # Empty assignments retain the calendar-interface normalization, without
    # replacing real activities or their external conditions by packages.
    # flatten_source already owns all nested records; transfer ownership internally.
    combined,report=contract_work_packages(flat,{},copy_source=False)
    report['control_combinations']=[]
    report['calculation_packaging']='disabled; PM packages are display-only'
    from core.master_completion import absorb_completion_gates
    combined, completion_changes=absorb_completion_gates(combined,copy_source=False)
    report['completion_conditions_absorbed']=completion_changes
    report['master_count']=len(combined)
    report['removed_display_headings']=headings
    report['basis']='Original activity release conditions retained; PM grouping is applied after CPM and does not add package-completion dependencies.'
    report['source_task_count']=len(tasks)
    return combined,report
