from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from .algorithm import calculate
from .contracts import BreakFeature, InvalidRow, parse
from .grade_risk import (
    assess_grade_risks,
    assess_reel_grade_risk,
    combine_candidate_risks,
    evaluate_grade_risks,
)


@dataclass(frozen=True, slots=True)
class BreakFeatureRejection:
    line: int
    field: str
    code: str


def validate(rows: Iterable[Mapping[str, Any]]) -> tuple[list[BreakFeature], list[BreakFeatureRejection]]:
    accepted = []
    rejected = []
    for line, raw in enumerate(rows, 1):
        try:
            accepted.append(parse(raw))
        except InvalidRow as issue:
            rejected.append(BreakFeatureRejection(line, issue.field, issue.code))
    return accepted, rejected


_GRADE_RISK_OPTION_KEYS = {
    'orders', 'order_windows', 'schedule', 'schedules', 'schedule_records',
    'grade_windows', 'ledger', 'plans', 'wet_history', 'signal_history',
    'baselines', 'references', 'grade_references', 'grade_candidates',
    'candidate_grades', 'schedule_candidates', 'possible_grades',
    'aggregate_grades', 'aggregate_candidates', 'by_reel_grades',
    'by_reel', 'group_by_reel', 'grade_risk', 'include_grade_risks',
}


def evaluate(rows: Iterable[Mapping[str, Any]], **options) -> dict:
    """计算断纸拟合，逐行隔离字段错误，保留原因码和行号。

    When schedule/wet-baseline context is supplied, evaluate every plausible
    grade candidate and aggregate risk per reel.  The ordinary BreakFeature
    contract path remains unchanged for existing model callers.
    """
    materialized_rows = list(rows)
    aggregate_rows = bool(materialized_rows) and all(
        isinstance(row, Mapping) and (
            'candidate_count' in row
            or ('probability' in row and 'model_ready' in row)
        )
        for row in materialized_rows
    )
    if aggregate_rows or _GRADE_RISK_OPTION_KEYS.intersection(options):
        result = evaluate_grade_risks(materialized_rows, **options)
        result.setdefault('accepted_count', len(result.get('records', [])))
        result.setdefault('rejected_count', 0)
        result.setdefault('errors', [])
        return result

    accepted, rejected = validate(materialized_rows)
    outcome = calculate(accepted, **options)
    return {
        'records': outcome,
        'accepted_count': len(accepted),
        'rejected_count': len(rejected),
        'errors': [asdict(error) for error in rejected],
    }
from .feature_counterfactual import compare_supporting_cast


def __getattr__(name):
    if "grade_risk" in name.lower() or "candidate_risk" in name.lower() or name in {
        "assess_grade_risks", "assess_reel_grade_risk", "evaluate_grade_risks",
    }:
        from . import grade_risk
        return getattr(grade_risk, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
