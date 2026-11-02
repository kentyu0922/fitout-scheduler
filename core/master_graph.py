"""Conservative work-package contraction before scheduling.

External conditions apply to package start.  FS consumers wait for the whole
package's finish; SS consumers retain each source member's internal start offset.
These are explicit conservative handovers, not a claim of date equivalence.
No dates, constraints or hidden-task shortcuts are introduced here.
"""
from __future__ import annotations

import copy
import math
import re
from collections import OrderedDict

from core.module_graph import dependency_order


_LINK = re.compile(r"\s*(\d+)(FS|SS|FF|SF)?([+-]\d+)?d?\s*", re.I)


def _links(value):
    """Parse strictly: silently losing an unsupported edge is not contraction."""
    result = []
    for token in str(value or "").split(","):
        if not token.strip():
            continue
        match = _LINK.fullmatch(token)
        if not match:
            raise ValueError("MASTER_UNSUPPORTED_DEPENDENCY: " + token)
        link = (int(match[1]), (match[2] or "FS").upper(), int(match[3] or 0))
        if link not in result:
            result.append(link)
    return result


def _tokens(links):
    return ",".join(str(pid) + kind + (f"{lag:+d}d" if lag else "")
                    for pid, kind, lag in links)


def _days(task):
    try:
        number = float(task.get("duration_days", task.get("duration", 0)) or 0)
    except (TypeError, ValueError):
        return None
    return int(number) if math.isfinite(number) and number.is_integer() else None


def _calendar(task):
    resolved = task.get("activity_calendar") or {}
    cid = resolved.get("calendar_id")
    if not cid:
        return None
    # A shared label cannot override a different explicit execution window.
    return (cid, resolved.get("calendar_mode"), resolved.get("working_days_per_week"),
            task.get("calendar_window"), bool(task.get("ignore_holidays")),
            task.get("mpp_calendar_name"))


def _protected(task, summary_ids):
    if task["id"] in summary_ids or task.get("summary"):
        return "summary"
    if task.get("milestone") or task.get("inspection_gate"):
        return "milestone_or_inspection"
    if task.get("conditional_requirement") or task.get("conditional"):
        return "conditional_requirement"
    if task.get("unresolved_placeholder"):
        return "unresolved_placeholder"
    for field in ("duration_status", "requirement_status", "scope_status"):
        if str(task.get(field, "")).upper() in {"UNKNOWN", "UNRESOLVED", "PENDING", "TBC"}:
            return "unresolved_status"
    if task.get("scope_exclusion") or task.get("duration_method") == "out_of_scope":
        return "excluded_scope"
    if task.get("constraint") or task.get("constraint_date"):
        return "explicit_constraint"
    if str(task.get("constraint_type", "ASAP")).upper() not in {"ASAP", "0", "NONE", ""}:
        return "explicit_constraint"
    days = _days(task)
    if days is None or days <= 0:
        return "nonpositive_or_unsupported_duration"
    if _calendar(task) is None:
        return "unresolved_calendar"
    return None


def contract_work_packages(tasks, assignments, *, copy_source=True):
    """Return a fresh network and an auditable conservative-contraction report.

    assignments maps task IDs to ``{key, name, phase}``.  Groups are attempted in
    source display order.  A refused group retains all of its original work and
    links.  Already accepted groups remain when a later group would create a
    cycle.  The caller must solve the returned network again.
    """
    source = copy.deepcopy(list(tasks)) if copy_source else list(tasks)
    # Project allows one relation per pair. Preserve independent mixed-calendar
    # FS/SS bounds with the existing lag window on the successor calendar,
    # rather than converting workdays across calendars or discarding a bound.
    interface_events = []
    next_id = max(t['id'] for t in source) + 1
    original_byid = {t['id']:t for t in source}
    for task in list(source):
        original_links = _links(task.get('predecessors'))
        by_pred = OrderedDict()
        for pid, kind, lag in original_links:by_pred.setdefault(pid,[]).append((kind,lag))
        links = list(original_links)
        for pid,pair in by_pred.items():
            if {k for k,_ in pair}!={'FS','SS'}:continue
            if _calendar(original_byid[pid])==_calendar(task):continue
            if max(v for k,v in pair if k=='FS')>=max(v for k,v in pair if k=='SS'):continue
            event=copy.deepcopy(task)
            window=max(v for k,v in pair if k=='SS')
            if window < 0:
                raise ValueError(f'MASTER_NEGATIVE_INTERFACE_WINDOW: {pid}->{task["id"]}')
            event.update(id=next_id,name='布线与机电工作面搭接窗口' if '机电' in task.get('name','') else '工作面搭接窗口',
                activity_uid=f'pmp.logic.interface.{pid}.{task["id"]}',duration_days=window,duration=window,
                milestone=False,summary=False,outline_level=3,level=3,logic_interface_event=True,
                predecessors=_tokens([(pid,'SS',0)]),
                input_notes=f'原SS+{window}工作日搭接偏移的等价表达；与前置并行，不是新增施工、审批或额外缓冲，使用后续工序日历。')
            event.pop('inspection_gate',None);event.pop('project_target',None)
            event.pop('master_task_name',None)
            source.insert(source.index(task),event)
            links=[edge for edge in links if not(edge[0]==pid and edge[1]=='SS')]
            links.append((next_id,'FS',0))
            interface_events.append(dict(id=next_id,predecessor=pid,successor=task['id'],relations=pair))
            next_id+=1
        task['predecessors']=_tokens(links)
    byid = {task["id"]: task for task in source}
    parsed = {task["id"]: _links(task.get("predecessors")) for task in source}
    order = dependency_order(source)  # Duplicates, missing IDs and cycles fail.
    unknown_ids = set(assignments) - set(byid)
    if unknown_ids:
        raise ValueError("MASTER_UNKNOWN_ASSIGNMENT: " + str(sorted(unknown_ids)))
    summary_ids = set()
    for index, task in enumerate(source):
        level = int(task.get("outline_level", task.get("level", 3)))
        if level <= 2 or (index + 1 < len(source) and
                         int(source[index + 1].get("outline_level", source[index + 1].get("level", 3))) > level):
            summary_ids.add(task["id"])

    proposed = OrderedDict()
    for task in source:
        if task["id"] in assignments:
            spec = assignments[task["id"]]
            if not spec.get("key") or not spec.get("name") or not spec.get("phase"):
                raise ValueError("MASTER_INCOMPLETE_ASSIGNMENT: " + str(task["id"]))
            proposed.setdefault(spec["key"], []).append(task["id"])

    report = dict(source_count=len(source), master_count=len(source), groups=[], refused=[],
                  mixed_calendar_interface_events=interface_events,
                  whole_package_handover_changes=[],
                  semantics="External conditions apply to package start. FS releases use whole-package completion; SS releases preserve the source member's internal start offset on a shared calendar. Re-solve required; detailed dates are not preserved.")
    accepted = OrderedDict()

    def render(groups):
        mapping = {tid: tid for tid in byid}
        representatives = {}
        for group in groups.values():
            rep = group["representative_id"]
            representatives[rep] = group
            for tid in group["source_task_ids"]:
                mapping[tid] = rep
        output = []
        for original in source:
            tid = original["id"]
            if mapping[tid] != tid:
                continue
            task = dict(original)  # source is already privately deep-copied; render replaces top-level fields.
            group = representatives.get(tid)
            member_ids = group["source_task_ids"] if group else [tid]
            outside = []
            for member_id in member_ids:
                for pid, kind, lag in parsed[member_id]:
                    target_pid = mapping[pid]
                    if target_pid == tid:
                        continue  # Accounted for by the package's internal duration.
                    if kind == "SS" and target_pid in representatives:
                        lag += representatives[target_pid]["member_start_offsets"][pid]
                    edge = (target_pid, kind, lag)
                    if edge not in outside:
                        outside.append(edge)
            # Project keeps one relation for a predecessor/successor pair.  On
            # one calendar FS+lag is SS+(predecessor duration+lag), so both
            # constraints can be represented by the stronger relation exactly.
            by_pred = OrderedDict()
            for pid, kind, lag in outside:
                by_pred.setdefault(pid, []).append((kind, lag))
            resolved = []
            normalizations = []
            for pid, links in by_pred.items():
                kinds = {kind for kind, _ in links}
                if kinds <= {"FS", "SS"}:
                    if kinds == {"FS", "SS"}:
                        fs_lag = max(value for relation, value in links if relation == "FS")
                        ss_lag = max(value for relation, value in links if relation == "SS")
                        # Finish cannot precede start. With an equal or greater
                        # successor-calendar lag FS dominates SS on ANY calendars.
                        # No cross-calendar duration conversion is needed here.
                        if fs_lag >= ss_lag:
                            edge = (pid, "FS", fs_lag)
                            resolved.append(edge)
                            normalizations.append(dict(predecessor=pid, source_relations=links,
                                adopted_relation=list(edge[1:]), basis="FS lower bound dominates SS without calendar conversion."))
                            continue
                        if _calendar(byid[pid]) is None or _calendar(byid[pid]) != _calendar(original):
                            raise ValueError(f"MASTER_MIXED_CALENDAR_RELATIONS: {pid}->{tid}; links={links}; predecessor={byid[pid].get('name')}; successor={original.get('name')}")
                        pred_days = representatives[pid]["duration_days"] if pid in representatives else _days(byid[pid])
                        if pred_days is None or pid in summary_ids:
                            raise ValueError(f"MASTER_UNRESOLVED_PREDECESSOR_SPAN: {pid}->{tid}")
                        lag = max(value if kind == "FS" else value - pred_days for kind, value in links)
                        edge = (pid, "FS", lag)
                    else:
                        kind = next(iter(kinds))
                        edge = (pid, kind, max(value for _, value in links))
                    resolved.append(edge)
                    if len(links) > 1:
                        normalizations.append(dict(predecessor=pid, source_relations=links,
                                                   adopted_relation=list(edge[1:]),
                                                   basis="Strongest lower bound retained; mixed FS/SS conversion requires a shared calendar."))
                else:
                    resolved.extend((pid, kind, lag) for kind, lag in links)
            task["predecessors"] = _tokens(resolved)
            if normalizations:
                task["master_link_normalizations"] = normalizations
            if group:
                task.update(duration_days=group["duration_days"], duration=group["duration_days"],
                            duration_authority="master_work_package", duration_basis=group["duration_basis"],
                            master_task_name=group["name"], master_phase=group["phase"],
                            master_group_key=group["key"], source_task_ids=list(member_ids),
                            source_activity_uids=list(group["source_activity_uids"]),
                            master_source_work=copy.deepcopy(group["source_work"]),
                            master_member_start_offsets=dict(group["member_start_offsets"]),
                            master_handover_policy="whole_package_fs_with_ss_offsets")
                task["duration_record"] = dict(adopted_days=group["duration_days"],
                                               authority="master_work_package", basis=group["duration_basis"])
                notes = ["工作范围：" + "；".join(row["name"] for row in group["source_work"]),
                         "交接条件：外部条件统一约束整包启动；完成后开始关系按整包完成移交，同时开始关系按包内对应工序的起点偏移保留搭接。",
                         "工期依据：" + group["duration_basis"]]
                for row in group["source_work"]:
                    if row["acceptance"]:
                        notes.append(row["name"] + "完成依据：" + row["acceptance"])
                    if row["notes"]:
                        notes.append(row["name"] + "：\n" + row["notes"])
                task["input_notes"] = "\n".join(notes)
            output.append(task)
        return output

    def refuse(key, members, reason, **extra):
        report["refused"].append(dict(key=key, source_task_ids=members, reason=reason, **extra))

    for key, assigned in proposed.items():
        specs = {tuple(assignments[tid].get(field) for field in ("name", "phase")) for tid in assigned}
        if len(specs) != 1:
            refuse(key, assigned, "inconsistent_group_name_or_phase")
            continue
        members = []
        for tid in assigned:
            reason = _protected(byid[tid], summary_ids)
            if reason:
                refuse(key, [tid], reason)
            else:
                members.append(tid)
        if len(members) < 2:
            if members:
                refuse(key, members, "fewer_than_two_eligible_activities")
            continue
        if len({_calendar(byid[tid]) for tid in members}) != 1:
            refuse(key, members, "incompatible_calendars")
            continue
        membership = set(members)
        affected = [(pid, task["id"], kind, lag) for task in source
                    for pid, kind, lag in parsed[task["id"]]
                    if pid in membership or task["id"] in membership]
        unsupported = [edge for edge in affected if edge[2] not in {"FS", "SS"} or edge[3] < 0]
        if unsupported:
            refuse(key, members, "unsupported_relationship_or_negative_lag", affected_edges=unsupported)
            continue
        cross_calendar_ss = [edge for edge in affected if edge[2] == "SS" and
                             (_calendar(byid[edge[0]]) is None or
                              _calendar(byid[edge[0]]) != _calendar(byid[edge[1]]))]
        if cross_calendar_ss:
            refuse(key, members, "cross_calendar_ss_interface", affected_edges=cross_calendar_ss)
            continue
        starts, finish = {}, {}
        for task in order:
            tid = task["id"]
            if tid in membership:
                starts[tid] = max(((finish[pid] if kind == "FS" else starts[pid]) + lag
                                   for pid, kind, lag in parsed[tid] if pid in membership), default=0)
                finish[tid] = starts[tid] + _days(task)
        name, phase = next(iter(specs))
        duration = max(finish.values())
        basis = f"按包内原工作日历及完成后开始、同时开始关系计算总跨度 {duration} 个工作日，保留工艺等待与搭接；外部完成后开始关系按整包交接，重新排程。"
        group = dict(key=key, name=name, phase=phase, representative_id=members[0],
                     source_task_ids=members, duration_days=duration, duration_basis=basis,
                     member_start_offsets=starts, member_finish_offsets=finish,
                     source_activity_uids=[str(byid[tid].get("activity_uid") or f"source-task:{tid}") for tid in members],
                     source_work=[dict(id=tid, activity_uid=byid[tid].get("activity_uid"),
                                       name=byid[tid].get("name", ""), duration_days=_days(byid[tid]),
                                       start_offset_days=starts[tid], finish_offset_days=finish[tid],
                                       acceptance=str(byid[tid].get("acceptance") or ""),
                                       notes=str(byid[tid].get("input_notes") or "")) for tid in members])
        candidate = OrderedDict(accepted)
        candidate[key] = group
        try:
            dependency_order(render(candidate))
        except ValueError as exc:
            if "MODULE_DEPENDENCY_CYCLE" in str(exc):
                refuse(key, members, "contraction_would_create_cycle")
                continue
            if str(exc).startswith(("MASTER_MIXED_CALENDAR_RELATIONS", "MASTER_UNRESOLVED_PREDECESSOR_SPAN")):
                refuse(key, members, "unsupported_parallel_interface_normalization", detail=str(exc))
                continue
            raise
        accepted[key] = group
        incoming = [dict(predecessor=pid, original_successor=sid, relationship=kind, lag_days=lag) for pid, sid, kind, lag in affected
                    if pid not in membership and sid in membership]
        outgoing = [dict(original_predecessor=pid, successor=sid, relationship=kind, lag_days=lag,
                         source_member_start_offset=starts[pid] if kind == "SS" else None,
                         package_relative_lag=lag + starts[pid] if kind == "SS" else lag) for pid, sid, kind, lag in affected
                    if pid in membership and sid not in membership]
        report["whole_package_handover_changes"].append(dict(key=key, package_id=members[0],
                                                             external_incoming=incoming, external_outgoing=outgoing,
                                                             effect="External conditions apply to package start. FS releases use whole-package completion; SS releases retain member start offsets. Dates may move later."))
    result = render(accepted)
    dependency_order(result)
    report["groups"] = list(accepted.values())
    report["master_count"] = len(result)
    report["parallel_interface_normalizations"] = [dict(successor=task["id"], **row) for task in result
                                                   for row in task.get("master_link_normalizations", [])]
    return result, report
