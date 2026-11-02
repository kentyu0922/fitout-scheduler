"""Separate arithmetic review from legal applicability and supplier validation."""
from datetime import date


def procurement_review(tasks):
    phase=next((t for t in tasks if t.get('outline_level',3)==2 and any(k in t.get('name','') for k in ('总包','总承包')) and any(k in t.get('name','') for k in ('招标','企业采购'))),None)
    issue=next((t for t in tasks if t.get('activity_uid')=='pmp.gc.bid_issue'),None)
    receipt=next((t for t in tasks if t.get('activity_uid')=='pmp.gc.bid_deadline'),None)
    if not issue or not receipt:
        block=[]
        if phase:
            start=tasks.index(phase)+1
            for task in tasks[start:]:
                if task.get('outline_level',3)<=2: break
                block.append(task)
        issue=next((t for t in block if any(k in t.get('name','') for k in ('标书发放','文件发售','正式发出'))),None)
        receipt=next((t for t in block if any(k in t.get('name','') for k in ('回标','投标截止','开标'))),None)
    window=(date.fromisoformat(receipt['finish'])-date.fromisoformat(issue['start'])).days if issue and receipt else None
    return dict(status='requires_context_validation',legal_applicability='unconfirmed',
                actual_phase=dict(start=phase['start'],finish=phase['finish'],elapsed_calendar_days=(date.fromisoformat(phase['finish'])-date.fromisoformat(phase['start'])).days) if phase else None,
                observed_bid_window_calendar_days=window,
                conditional_issue='If legally mandatory tendering applies, review the minimum bid-preparation period against the actual issuance/deadline timestamps.' if window is not None and window<20 else None,
                assumptions=['Invitation method does not determine whether statutory tendering applies.',
                             'Total project budget is not necessarily the construction contract estimate.',
                             'Internal approvals, bid design maturity and supplier lead times remain planning assumptions.'],
                sources=['https://www.npc.gov.cn/zgrdw/npc/xinwen/2018-01/04/content_2036284.htm',
                         'https://zfxxgk.ndrc.gov.cn/web/iteminfo.jsp?id=18461'])
