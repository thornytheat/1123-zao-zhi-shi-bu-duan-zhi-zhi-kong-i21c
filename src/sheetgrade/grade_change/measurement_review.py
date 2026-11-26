from ..breakpoint_model.grade_risk import *  # noqa: F401,F403


def __getattr__(name):
    from ..breakpoint_model import grade_risk
    return getattr(grade_risk, name)
