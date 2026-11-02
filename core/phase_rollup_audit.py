"""Read-only allocation audit of the existing hierarchy; no rescheduling."""
import re
PHASES={'前期规划':'需求冻结 / 方案','设计阶段':'设计深化','招采阶段':'招采发包','报审阶段':'报建审批','施工阶段':'现场施工','竣工验收阶段':'验收移交','搬迁阶段':'验收移交'}
def audit(tasks):
 if not tasks:raise ValueError('empty schedule')
 phases={};summaries=set();leaves=[];current=None;issues=[];seen=set()
 for i,t in enumerate(tasks):
  level=t.get('outline_level',t.get('level',1));next_level=tasks[i+1].get('outline_level',tasks[i+1].get('level',1)) if i+1<len(tasks) else 0
  if level==2:current=PHASES.get(t.get('name'));
  if next_level>level or t.get('summary') is True:summaries.add(str(t.get('id')));continue
  uid=t.get('activity_uid');key=uid or 'id:'+str(t.get('id'))
  if key in seen:issues.append({'type':'duplicate_leaf_identity','id':t.get('id'),'identity':key})
  seen.add(key)
  if not current:issues.append({'type':'unassigned_leaf','id':t.get('id'),'name':t.get('name')})
  leaves.append({'id':t.get('id'),'activity_uid':uid,'name':t.get('name'),'phase':current,'start':t.get('start'),'finish':t.get('finish'),'predecessors':t.get('predecessors','')})
 for t in leaves:
  for token in str(t['predecessors'] or '').replace(';',',').split(','):
   m=re.match(r'\s*(\d+)',token)
   if m and m.group(1) in summaries:issues.append({'type':'dependency_on_summary','id':t['id'],'predecessor':m.group(1)})
 for leaf in leaves:
  p=leaf['phase']
  if p:
   x=phases.setdefault(p,{'leaf_count':0,'start':None,'finish':None});x['leaf_count']+=1
   if leaf['start']:x['start']=min(filter(None,(x['start'],leaf['start'])))
   if leaf['finish']:x['finish']=max(filter(None,(x['finish'],leaf['finish'])))
 return {'status':'REVIEW_REQUIRED','leaf_count':len(leaves),'summary_count':len(summaries),'phase_rollups':phases,'issues':issues,'leaves':leaves,'limitations':['Hierarchy mapping is provisional; not C01-C12 classification or integration into an independent new spine.','Dates are copied from solved leaf tasks, not a second CPM computation.','No proof of engineering scope, FS/SS/FF integrity, calendar correctness or contractual responsibility.']}
