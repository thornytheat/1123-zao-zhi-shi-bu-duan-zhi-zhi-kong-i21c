"""断纸拟合。"""
from .service import evaluate

__all__ = ["evaluate"]
from .service import compare_supporting_cast
__all__ += ['compare_supporting_cast']

from .grade_risk import (
    aggregate_candidate_risks,
    assess_grade_risks,
    assess_reel_grade_risk,
    combine_candidate_risks,
    displayed_probability,
    evaluate_grade_risks,
    is_high_risk,
    reaches_warning_line,
    replay_late_schedule,
)

__all__ += [
    'assess_reel_grade_risk', 'assess_grade_risks', 'evaluate_grade_risks',
    'combine_candidate_risks', 'aggregate_candidate_risks',
    'reaches_warning_line', 'is_high_risk', 'displayed_probability',
    'replay_late_schedule',
]


def __getattr__(name):
    if "risk" in name.lower() or "candidate" in name.lower():
        from . import grade_risk
        return getattr(grade_risk, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
