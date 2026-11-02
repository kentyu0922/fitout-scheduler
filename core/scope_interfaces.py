"""Scope allocation assumptions and a project confirmation gate, not a BOQ takeoff."""
from core.module_graph import add_pred

def attach_scope_interfaces(tasks,plan):
 selected=set(plan['selected']);seq=max(t['id'] for t in tasks)+1
 def step(module,n):return next(t for t in tasks if t.get('module_id')==module and t.get('module_step')==n)
 common_cable=[t for t in tasks if t.get('activity_type')=='structured_cabling']
 av=step('office_ff_move',2)
 av['name']='公共办公区会议AV与门禁末端设备安装、系统联调（线路由基础布线包承担）'
 allocations=[dict(key='CORE_OFFICE_CABLING',owner_module='core_office',scope='公共办公区干支线、数据插座及线路测试',
                   excludes='机房内部配线、专项设备内部线束；不含AV/门禁末端设备安装',tasks=[t['id'] for t in common_cable]),
              dict(key='CORE_OFFICE_ENDPOINTS',owner_module='office_ff_move',scope='公共办公区AV/门禁末端设备与系统联调',
                   excludes='重复敷设已在基础包计入的线路；已明确划给专项的设备和区域',tasks=[av['id']])]
 for t in common_cable:
  t['name']='公共办公区综合布线安装与线路测试（不含机房内部及专项设备内部配线）'
  t['scope_allocation_key']='CORE_OFFICE_CABLING'
  add_pred(av,t)
 av['scope_allocation_key']='CORE_OFFICE_ENDPOINTS'
 choices=[('A04','DC_INTERNAL','datacenter',6,'机房内部主干及机柜配线、接口测试','公共办公区干支线与末端；公共上联分界由接口表确定'),
          ('A06','MOCAP_DEVICE','mocap',4,'动捕相机、控制设备及设备侧接入','公共网络干线/插座及独立声学围护'),
          ('A09','TRADING_DEVICE','trading_desk',3,'交易终端与控制设备部署','普通办公终端；公共主干由接口清单指定唯一施工方'),
          ('A10','ENHANCED_SECURITY','vault_security',2,'已批准强化安防区域内的设备与系统集成','同一设备不得重复列入普通门禁末端包')]
 for i,key,module,n,scope,excludes in choices:
  if i not in selected and not (i=='A10' and 'A11' in selected):continue
  t=step(module,n);t.update(scope_allocation_key=key,scope_includes=scope,scope_excludes=excludes)
  allocations.append(dict(key=key,owner_module=module,scope=scope,excludes=excludes,tasks=[t['id']]))
 # A single project-specific boundary approval precedes drawing release and purchasing.
 drawing=next(t for t in tasks if '全专业施工图' in t['name'])
 finaldesign=next(t for t in tasks if '施工图全套确认' in t['name'])
 gate=dict(id=seq,name='公共工程与专项系统范围／空间分界及唯一施工责任确认',outline_level=3,
           duration_days=0,duration=0,milestone=True,calendar='5D',predecessors=str(drawing['id']),
           module_id='scope_interfaces',activity_uid='pmp.scope.boundaries',activity_class='CLIENT_APPROVAL',
           interface_status='pending_project_confirmation',evidence_required=True,
           acceptance='批准的系统及空间分界表、供货/安装/测试责任矩阵；同一资产与线路不得重复计入',
           owner='业主PM、设计方、GC及适用专项承包商共同确认',
           duration_basis='分界核查并入设计审查活动；此为成果放行节点，不表示已批准')
 add_pred(finaldesign,gate)
 plan['scope_allocation']=dict(status='planning_allocation_pending_project_confirmation',
                               entries=allocations,gate_uid=gate['activity_uid'],
                               quantity_deduplication='not_verified_without_asset_location_and_boq_mapping')
 tasks.insert(tasks.index(drawing)+1,gate)
 return tasks
