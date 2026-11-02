"""Compute in dependency order while preserving the client's WBS display order."""
import heapq
from core._common import parse_predecessor

def dependency_order(tasks):
    byid={t['id']:t for t in tasks}
    if len(byid)!=len(tasks): raise ValueError('MODULE_DUPLICATE_ID')
    position={t['id']:i for i,t in enumerate(tasks)}
    indegree={i:0 for i in byid}; successors={i:[] for i in byid}
    for t in tasks:
        for pid in {p for p,_,_ in parse_predecessor(str(t.get('predecessors','') or ''))}:
            if pid not in byid: raise ValueError(f'MODULE_MISSING_PREDECESSOR: {t["id"]} -> {pid}')
            indegree[t['id']]+=1; successors[pid].append(t['id'])
    queue=[(position[i],i) for i,d in indegree.items() if not d];heapq.heapify(queue)
    result=[]
    while queue:
        _,i=heapq.heappop(queue);result.append(byid[i])
        for child in successors[i]:
            indegree[child]-=1
            if not indegree[child]: heapq.heappush(queue,(position[child],child))
    if len(result)!=len(tasks): raise ValueError('MODULE_DEPENDENCY_CYCLE')
    return result

def add_pred(task, predecessor, relationship='FS', lag=0):
    pid=predecessor['id'] if isinstance(predecessor,dict) else predecessor
    if pid==task['id']: raise ValueError('MODULE_SELF_REFERENCE')
    parts=[p for p in str(task.get('predecessors','') or '').split(',') if p]
    if pid not in {i for i,_,_ in parse_predecessor(','.join(parts))}:
        parts.append(str(pid)+relationship+(f'{lag:+d}' if lag else ''))
    task['predecessors']=','.join(parts)

def remove_nodes(tasks, remove_ids):
    """Splice deleted FS0 activities through to their surviving predecessors.

    Non-FS or lagged edges cannot be silently flattened.
    """
    byid={t['id']:t for t in tasks}
    def expand(pid,rel,lag,seen):
        if pid not in remove_ids: return [(pid,rel,lag)]
        if pid in seen: raise ValueError('MODULE_REMOVE_CYCLE')
        if rel!='FS' or lag: raise ValueError('MODULE_REMOVE_NON_FS: '+str(pid))
        out=[]
        for p,r,l in parse_predecessor(str(byid[pid].get('predecessors','') or '')):
            out.extend(expand(p,r,l,seen|{pid}))
        return out
    out=[]
    for t in tasks:
        if t['id'] in remove_ids: continue
        preds=[]
        for pid,rel,lag in parse_predecessor(str(t.get('predecessors','') or '')):
            preds.extend(expand(pid,rel,lag,set()))
        t['predecessors']=','.join(str(i)+r+(f'{l:+d}' if l else '') for i,r,l in dict.fromkeys(preds))
        out.append(t)
    return out
