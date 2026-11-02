"""Compare approved baseline and a separately recalculated current forecast.

Never edits either input or invents actual progress. Stable activity_uid is
required; ambiguous name matching would manufacture a variance.
"""
from __future__ import annotations
from datetime import date


def compare_programmes(baseline, current, *, status_date, reasons=None):
    date.fromisoformat(status_date)
    reasons = reasons or {}
    def index(payload):
        out = {}
        for t in payload['tasks']:
            uid = t.get('activity_uid')
            if not uid or uid in out:
                raise ValueError('every task must have a unique activity_uid; no name-based reconciliation')
            out[uid] = t
        return out
    old, new = index(baseline), index(current)
    changes = []
    for uid in sorted(set(old) | set(new)):
        a, b = old.get(uid), new.get(uid)
        if a is None or b is None:
            changes.append({'activity_uid': uid, 'change': 'added' if a is None else 'removed',
                            'reason': reasons.get(uid), 'decision_required': uid not in reasons})
            continue
        row = {'activity_uid': uid, 'name': b.get('name'), 'changes': {}}
        for key in ('duration_days', 'predecessors', 'activity_class'):
            if a.get(key) != b.get(key):
                row['changes'][key] = {'baseline': a.get(key), 'current': b.get(key)}
        for key in ('start', 'finish'):
            av, bv = a.get(key), b.get(key)
            if av != bv:
                row['changes'][key] = {'baseline': av, 'current': bv,
                                       'calendar_day_variance': (date.fromisoformat(bv) - date.fromisoformat(av)).days if av and bv else None}
        if row['changes']:
            row.update(reason=reasons.get(uid), decision_required=uid not in reasons)
            changes.append(row)
    return {'status_date': status_date, 'baseline_modified': False, 'forecast_modified': False,
            'status': 'PM_REVIEW_REQUIRED', 'changes': changes,
            'note': 'Forecast comparison only; actual starts/finishes and remaining duration are not inferred from status date.'}
