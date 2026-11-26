from __future__ import annotations

from .contracts import GradeWindow

from collections import defaultdict
from dataclasses import asdict


def revision_windows(records, available_at):
    active = defaultdict(list)
    for window in records:
        if window.recorded <= available_at:
            active[window.line, window.order].append(window)
    ledger = []
    for key, candidates in sorted(active.items()):
        revision = max(item.revision for item in candidates)
        eligible = [item for item in candidates if item.revision == revision]
        contents = {(item.grade, item.effective, item.until, item.stabilize) for item in eligible}
        row = min(eligible, key=lambda value: (value.recorded, value.effective, value.grade, value.until, value.stabilize))
        if len(contents) > 1:
            reason = 'GRADE_REVISION_CONFLICT'
        elif row.effective >= row.until or row.effective + row.stabilize > row.until:
            reason = 'INVALID_GRADE_INTERVAL'
        else:
            reason = 'ACTIVE_GRADE_WINDOW'
        ledger.append(asdict(row) | {'usable': reason == 'ACTIVE_GRADE_WINDOW', 'reason': reason,
                                     'superseded': len(candidates) - 1})
    return ledger


def key_overlaps(ledger):
    blocked = defaultdict(set)
    usable = [row for row in ledger if row['usable']]
    for index, row in enumerate(usable):
        for other in usable[index+1:]:
            if row['line'] != other['line']:
                continue
            if row['effective'] < other['until'] and other['effective'] < row['until']:
                blocked[row['line'], row['order']].add(other['order'])
                blocked[other['line'], other['order']].add(row['order'])
    return blocked


def candidate_windows(ledger, line, period):
    """Return every schedule version that covers *period* on *line*.

    Overlapping windows are deliberately not collapsed here.  Risk scoring needs
    all possible grades, while :func:`match_shift` remains the strict unique
    lookup used by the existing timeline semantics.
    """
    return sorted(
        (row for row in ledger if row['line'] == line and row['effective'] <= period < row['until']),
        key=lambda row: (row['effective'], row['order'], row['grade'], row['until'], row['revision']),
    )


candidates_at = candidate_windows
grade_candidates_at = candidate_windows
candidate_grades = candidate_windows


def match_shift(ledger, line, period):
    candidates = candidate_windows(ledger, line, period)
    candidate_view = [{
        'line': row['line'], 'grade': row['grade'], 'order': row['order'],
        'steady': period >= row['effective'] + row['stabilize'] if row['usable'] else False,
        'usable': row['usable'], 'reason': row['reason'],
        'effective': row['effective'], 'until': row['until'],
        'stabilize': row['stabilize'],
    } for row in candidates]
    grade_names = [row['grade'] for row in candidate_view]
    if not candidates:
        return {'grade': None, 'order': None, 'steady': False, 'grade_reason': 'GRADE_MISSING',
                'candidates': [], 'candidate_grades': []}
    if len(candidates) != 1 or not candidates[0]['usable']:
        return {'grade': None, 'order': None, 'steady': False, 'grade_reason': 'GRADE_AMBIGUOUS',
                'candidates': candidate_view, 'candidate_grades': grade_names}
    plan = candidates[0]
    stable = period >= plan['effective'] + plan['stabilize']
    candidate_view[0]['steady'] = stable
    return {'grade': plan['grade'], 'order': plan['order'], 'steady': stable,
            'grade_reason': 'STABLE_GRADE' if stable else 'TRANSITION_HOLD',
            'candidates': candidate_view, 'candidate_grades': grade_names}


def calculate(records, *, available_at):
    ledger = revision_windows(records, available_at)
    clashes = key_overlaps(ledger)
    return [row | {'conflicts': sorted(clashes[row['line'], row['order']])} for row in ledger]
