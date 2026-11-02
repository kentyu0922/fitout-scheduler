"""Strict identities for historical date reviews; never infer identity from names."""
def select_task(rows, *, permit_role=None, activity_uid=None):
    if (permit_role is None) == (activity_uid is None):
        raise ValueError('REVIEW_IDENTITY: supply exactly one role or UID')
    key, value = ('permit_role', permit_role) if permit_role is not None else ('activity_uid', activity_uid)
    found = [t for t in rows if t.get(key) == value]
    if len(found) != 1:
        raise ValueError(f'REVIEW_IDENTITY: {key}={value!r}: expected one task, got {len(found)}')
    return found[0]
