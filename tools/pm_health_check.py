# -*- coding: utf-8 -*-
"""Conservative structural review of an existing schedule JSON; not a CPM validator."""
import argparse, json, re
from collections import Counter
from pathlib import Path

EDGE = re.compile(r"^\s*(\d+)\s*(FS|SS|FF|SF)?(?:\s*[+-]\s*\d+(?:\.\d+)?\s*[a-zA-Z]*)?\s*$", re.I)

def audit(doc):
    tasks=doc.get("tasks")
    if not isinstance(tasks,list) or not tasks: raise ValueError("schedule.tasks is missing or empty")
    findings=[]; ids=[str(t.get("id")) for t in tasks]; counts=Counter(ids)
    for ident,count in counts.items():
        if count>1: findings.append({"severity":"ERROR","type":"duplicate_id","id":ident})
    known=set(ids); graph={i:[] for i in ids}; indeg={i:0 for i in ids}
    for task in tasks:
        target=str(task.get("id")); raw=task.get("predecessors") or ""
        if not isinstance(raw,str):
            findings.append({"severity":"REVIEW","type":"unparsed_predecessors","id":target,"value":str(raw)}); continue
        for part in filter(None, re.split(r"[,;]",raw)):
            match=EDGE.fullmatch(part)
            if not match:
                findings.append({"severity":"REVIEW","type":"unparsed_predecessor","id":target,"value":part}); continue
            source=match.group(1)
            if source not in known:
                findings.append({"severity":"ERROR","type":"missing_predecessor","id":target,"predecessor":source}); continue
            graph[source].append(target); indeg[target]+=1
    # Isolated nodes can be intentional project milestones or summary headers.
    # Report for review; do not treat isolation alone as proof of invalidity.
    if len(tasks)>1:
        for index, task in enumerate(tasks):
            ident=str(task.get("id")); level=task.get("outline_level",task.get("level",1))
            next_level=tasks[index+1].get("outline_level",tasks[index+1].get("level",1)) if index+1<len(tasks) else 0
            is_summary=next_level>level or task.get("summary") is True
            if not is_summary and indeg.get(ident)==0 and not graph.get(ident):
                findings.append({"severity":"REVIEW","type":"isolated_task","id":ident,"name":task.get("name"),"reason":"Confirm independent milestone or missing logic."})
    ready=[k for k,v in indeg.items() if v==0]; visited=0
    while ready:
        current=ready.pop();visited+=1
        for nxt in graph[current]:
            indeg[nxt]-=1
            if indeg[nxt]==0:ready.append(nxt)
    if visited<len(graph): findings.append({"severity":"ERROR","type":"cycle_or_blocked_by_cycle","ids":[k for k,v in indeg.items() if v>0]})
    authority=doc.get("authority_draft_review") or {}
    if authority.get("fire_route") in (None,"unknown"):
        findings.append({"severity":"REVIEW","type":"fire_route_unverified"})
    decision=doc.get("target_decision") or {}
    return {"status":"ERROR" if any(x["severity"]=="ERROR" for x in findings) else "REVIEW_REQUIRED", "task_count":len(tasks),"findings":findings,"target":{k:decision.get(k) for k in ("target","comparison","calculated_move_in","calculated_finish","variance_calendar_days")},"limits":["结构检查不能证明工序完整、日期正确、审批合规或资源可行。","未计算总浮动、工作面冲突或法定时限；审批及供货需项目核实。","保留源文件，检查不改排程、不自动改规则。"]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("schedule");ap.add_argument("--output",required=True);a=ap.parse_args()
    doc=json.loads(Path(a.schedule).read_text(encoding="utf-8"));result=audit(doc)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"status":result["status"],"findings":len(result["findings"]),"output":a.output},ensure_ascii=False))
if __name__=="__main__":main()
