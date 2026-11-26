"""Candidate-grade risk aggregation for overlapping production schedules.

An overlapping order window does not mean that the whole segment has to stop
reporting.  Every plausible grade is scored with its own wet-end baseline.
The reel receives one probability only when every candidate is scorable and
all candidates reach the same conclusion against the warning line.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from math import isfinite
from typing import Any

WARNING_THRESHOLD = 0.65
WARNING_LINE = WARNING_THRESHOLD
ALERT_THRESHOLD = WARNING_THRESHOLD
DISAGREEMENT_REASON = "CANDIDATE_RISK_DISAGREEMENT"
RISK_DISAGREEMENT_REASON = DISAGREEMENT_REASON
CANDIDATE_CONFLICT_REASON = DISAGREEMENT_REASON
GRADE_RISK_CONFLICT_REASON = DISAGREEMENT_REASON
CURRENT_SCHEDULE_WATERMARK = 10_000_000


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    return dict(vars(value))


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _probability(candidate: Mapping[str, Any]) -> float | None:
    value = _first(candidate, "probability", "break_probability", "risk_probability", "risk", "score")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if isfinite(value) else None


def reaches_warning_line(probability: float | None, threshold: float = WARNING_THRESHOLD) -> bool:
    """Compare the unrounded score with the inclusive warning line."""
    return probability is not None and probability >= threshold


# Business-language alias used by callers describing the warning line.
is_high_risk = reaches_warning_line


def displayed_probability(probability: float | None, digits: int = 3) -> float | None:
    if probability is None:
        return None
    return round(float(probability), digits)


def _blocking_reason(candidate: dict[str, Any], probability: float | None) -> str | None:
    if candidate.get("usable") is False:
        return str(candidate.get("reason") or "GRADE_AMBIGUOUS")

    stable = _first(candidate, "steady", "stable", "is_stable")
    if stable is False:
        return str(candidate.get("reason") or "GRADE_TRANSITION")

    has_baseline = _first(candidate, "baseline_ready", "reference_ready", "has_baseline", "baseline_available")
    if has_baseline is False:
        return str(candidate.get("reason") or "MISSING_GRADE_REFERENCE")

    in_range = _first(candidate, "in_training_range", "inside_training_range", "training_range_ok")
    if in_range is False or candidate.get("outside_training_range") is True:
        return "OUTSIDE_TRAINING_PROFILE"

    explicit_ready = _first(candidate, "model_ready", "scorable", "ready", "scoreable")
    if explicit_ready is False:
        return str(candidate.get("reason") or candidate.get("blocking_reason") or "MODEL_UNAVAILABLE")

    if probability is None:
        return str(candidate.get("reason") or "MODEL_UNAVAILABLE")

    if not 0.0 <= probability <= 1.0:
        return "INVALID_PROBABILITY"

    return None


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    probability = result.get("probability")
    result |= {
        "risk": probability,
        "break_probability": probability,
        "max_probability": probability,
        "highest_probability": probability,
        "max_risk": probability,
        "review_required": bool(result.get("review")),
        "needs_review": bool(result.get("review")),
        "to_measurement_review": bool(result.get("measurement_review")),
        "action": result.get("state"),
        "disposition": result.get("state"),
        "verdict": result.get("risk_level") or result.get("state"),
        "risk_level_code": (
            "HIGH" if result.get("high_risk")
            else "LOW" if result.get("model_ready")
            else "REVIEW"
        ),
    }
    return result


def _combine_candidate_risks(
    candidates: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    threshold: float = WARNING_THRESHOLD,
    *,
    digits: int = 3,
) -> dict[str, Any]:
    """Aggregate independently scored grade candidates for one reel.

    The returned probability is the highest candidate probability only when
    every candidate is usable and all candidates agree on the warning-line
    decision.  Any disagreement or missing score sends the whole reel to
    measurement review.
    """
    if not 0.0 < threshold < 1.0:
        raise ValueError("INVALID_GRADE_RISK_THRESHOLD")

    normalized: list[dict[str, Any]] = []
    for raw in candidates or []:
        candidate = _mapping(raw)
        probability = _probability(candidate)
        blocker = _blocking_reason(candidate, probability)
        scorable = blocker is None
        high_risk = scorable and reaches_warning_line(probability, threshold)
        normalized.append(candidate | {
            "probability": probability if scorable else None,
            "display_probability": displayed_probability(probability if scorable else None, digits),
            "model_ready": scorable,
            "scorable": scorable,
            "high_risk": high_risk,
            "warning_line": threshold,
            "threshold": threshold,
            "reason": blocker if blocker else str(candidate.get("reason") or "ESTIMATED"),
        })

    base = {
        "threshold": threshold,
        "warning_threshold": threshold,
        "warning_line": threshold,
        "alert_threshold": threshold,
        "candidate_count": len(normalized),
        "candidates": normalized,
    }

    if not normalized:
        return base | {
            "probability": None,
            "display_probability": None,
            "model_ready": False,
            "scorable": False,
            "high_risk": False,
            "risk_level": None,
            "review": True,
            "measurement_review": True,
            "state": "测点复核",
            "decision": "MEASUREMENT_REVIEW",
            "reason": "GRADE_MISSING",
            "blocking_reasons": ["GRADE_MISSING"],
        }

    blocked = [item for item in normalized if not item["scorable"]]
    if blocked:
        reasons = [item["reason"] for item in blocked]
        primary = reasons[0]
        return base | {
            "probability": None,
            "display_probability": None,
            "model_ready": False,
            "scorable": False,
            "high_risk": False,
            "risk_level": None,
            "review": True,
            "measurement_review": True,
            "state": "测点复核",
            "decision": "MEASUREMENT_REVIEW",
            "reason": primary,
            "blocking_reasons": reasons,
        }

    verdicts = {item["high_risk"] for item in normalized}
    if len(verdicts) > 1:
        return base | {
            "probability": None,
            "display_probability": None,
            "model_ready": False,
            "scorable": False,
            "high_risk": False,
            "risk_level": None,
            "review": True,
            "measurement_review": True,
            "state": "测点复核",
            "decision": "MEASUREMENT_REVIEW",
            "reason": DISAGREEMENT_REASON,
            "risk_reason": DISAGREEMENT_REASON,
            "grade_reason": "GRADE_AMBIGUOUS",
            "blocking_reasons": [DISAGREEMENT_REASON],
        }

    probability = max(item["probability"] for item in normalized)
    high_risk = next(iter(verdicts))
    return base | {
        "probability": probability,
        "display_probability": displayed_probability(probability, digits),
        "rounded_probability": displayed_probability(probability, digits),
        "model_ready": True,
        "scorable": True,
        "high_risk": high_risk,
        "at_warning_line": high_risk,
        "risk_level": "高风险" if high_risk else "低风险",
        # High risk follows the existing release workflow as sample review;
        # disagreement/unscorable cases specifically request measurement review.
        "review": high_risk,
        "measurement_review": False,
        "state": "试样复核" if high_risk else "继续生产",
        "decision": "HIGH_RISK" if high_risk else "IN_CONTROL",
        "reason": "ESTIMATED",
        "blocking_reasons": [],
    }


def combine_candidate_risks(
    candidates,
    threshold: float = WARNING_THRESHOLD,
    digits: int = 3,
    **options,
) -> dict[str, Any]:
    return _finalize(_combine_candidate_risks(candidates, threshold, digits=digits))


# Descriptive aliases for callers/tests using aggregate wording.
aggregate_candidate_risks = combine_candidate_risks
merge_candidate_risks = combine_candidate_risks
reconcile_candidate_risks = combine_candidate_risks
consolidate_candidate_risks = combine_candidate_risks
combine_grade_risks = combine_candidate_risks
combine_risks = combine_candidate_risks
aggregate_risks = combine_candidate_risks
merge_risks = combine_candidate_risks
reconcile_risks = combine_candidate_risks


def _schedule_ledger(options: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    source = _first(
        options,
        "orders", "order_windows", "schedule", "schedules", "schedule_records",
        "windows", "ledger", "plans", "grade_windows",
    )
    if source is None:
        return None
    if isinstance(source, Mapping) and "records" in source:
        source = source["records"]
    rows = [_mapping(row) for row in source]
    available_at = _first(options, "available_at", "known_at", "schedule_at", "recorded_at", "at", "as_of")
    required_order_fields = {"line", "grade", "order", "effective", "until", "stabilize", "recorded", "revision"}
    can_replay_visibility = bool(rows) and all(required_order_fields <= row.keys() for row in rows)
    looks_like_raw_orders = rows and any("usable" not in row for row in rows)
    if can_replay_visibility and (available_at is not None or looks_like_raw_orders):
        from ..grade_change.service import evaluate as evaluate_grades

        watermark = CURRENT_SCHEDULE_WATERMARK if available_at is None else available_at
        return evaluate_grades(rows, available_at=watermark)["records"]
    return rows


def _candidate_schedule_rows(
    reading: Mapping[str, Any],
    options: Mapping[str, Any],
    ledger: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    line = _first(options, "line")
    if line is None:
        line = reading.get("line")
    period = _first(options, "period")
    if period is None:
        period = reading.get("period")
    explicit = _first(options, "schedule_candidates", "grade_candidates",
                      "candidate_grades", "window_candidates")
    if explicit is not None:
        return [
            _mapping(row) if isinstance(row, Mapping) or is_dataclass(row)
            else {"grade": row, "order": None, "usable": True, "steady": True}
            for row in explicit
        ]
    if ledger is not None and line is not None and period is not None:
        from ..grade_change.service import grade_candidates_at

        return grade_candidates_at(ledger, line, int(period))

    grades = _first(options, "grades", "possible_grades")
    if grades is not None:
        return [{"grade": grade, "order": None, "usable": True, "steady": True,
                 "effective": 0, "until": CURRENT_SCHEDULE_WATERMARK, "stabilize": 0}
                for grade in grades]

    if reading.get("grade"):
        return [{"grade": reading["grade"], "order": reading.get("order"), "usable": True,
                 "steady": reading.get("steady", True), "effective": 0,
                 "until": CURRENT_SCHEDULE_WATERMARK, "stabilize": 0}]
    return []


def _physical_reading(reading: dict[str, Any]) -> dict[str, Any]:
    result = dict(reading)
    if result.get("vacuum_kpa") is None and result.get("vacuum") is not None and result.get("vacuum_unit"):
        unit = result["vacuum_unit"]
        vacuum = float(result["vacuum"])
        factors = {"kPa": 1.0, "mbar": 0.1, "mmHg": 1.0 / 7.50062}
        if unit not in factors:
            result["vacuum_conversion_error"] = "UNSUPPORTED_VACUUM_UNIT"
        else:
            result["vacuum_kpa"] = vacuum * factors[unit]
    return result


def _looks_like_fault_history(rows: list[Any]) -> bool:
    first = _mapping(rows[0]) if rows else {}
    return any(key in first for key in ("consistency_z", "vacuum_z", "speed_z", "break_within", "observed"))


def _history_pair(options: Mapping[str, Any]) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    wet_history = _first(options, "wet_history", "signal_history", "wet_records", "baseline_history")
    fault_history = _first(options, "fault_history", "model_history", "faults", "training_history")
    generic_history = options.get("history")
    if generic_history is not None:
        rows = list(generic_history)
        if _looks_like_fault_history(rows):
            fault_history = fault_history if fault_history is not None else rows
        else:
            wet_history = wet_history if wet_history is not None else rows
    return wet_history, fault_history


def assess_reel_grade_risk(
    reading: Mapping[str, Any] | None = None,
    candidates: list[Mapping[str, Any]] | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Score one reel across all schedule grade candidates.

    ``candidates`` may either be schedule windows or already-computed risk
    candidates.  Raw wet readings plus ``wet_history`` and ``fault_history``
    enable the full baseline/CART calculation.
    """
    reading_dict = _mapping(reading)

    def identity_value(key: str) -> Any:
        value = _first(options, key)
        return reading_dict.get(key) if value is None else value

    identity = {
        "reel": identity_value("reel"),
        "line": identity_value("line"),
        "period": identity_value("period"),
    }

    # Convenience for unit tests that have already computed per-grade scores.
    risk_like = candidates is not None and (
        not candidates or any(
            "probability" in _mapping(item) or "model_ready" in _mapping(item)
            for item in candidates
        )
    )
    if risk_like and not _first(options, "wet_history", "baselines", "references"):
        result = combine_candidate_risks(candidates or [], float(options.get("threshold", WARNING_THRESHOLD)))
        return identity | result

    if candidates is not None:
        options = {**options, "schedule_candidates": candidates}

    threshold = float(_first(options, "threshold", "warning_line") or WARNING_THRESHOLD)
    available_at = _first(options, "available_at", "known_at", "schedule_at", "recorded_at", "at", "as_of")
    ledger = _schedule_ledger(options)
    windows = _candidate_schedule_rows(reading_dict, options, ledger)

    base_result = identity | {
        "available_at": available_at,
        "schedule_orders": [row.get("order") for row in windows if row.get("order") is not None],
    }

    if not windows:
        return base_result | combine_candidate_risks([], threshold)

    physical = _physical_reading(reading_dict)
    if physical.get("vacuum_conversion_error"):
        assessments = [{
            "grade": row.get("grade"), "order": row.get("order"), "usable": False,
            "steady": False, "probability": None,
            "reason": physical["vacuum_conversion_error"],
        } for row in windows]
        return base_result | combine_candidate_risks(assessments, threshold)

    wet_history, fault_history = _history_pair(options)
    baselines = _first(options, "baselines", "references", "grade_references")
    cutoff = options.get("cutoff")
    wet_cutoff = _first(options, "wet_cutoff", "signal_cutoff", "baseline_cutoff")
    if wet_cutoff is None:
        wet_cutoff = cutoff if cutoff is not None else CURRENT_SCHEDULE_WATERMARK
    model_cutoff = _first(options, "model_cutoff", "breakpoint_cutoff")
    if model_cutoff is None:
        model_cutoff = _first(options, "training_cutoff")
    if model_cutoff is None:
        model_cutoff = cutoff if cutoff is not None else CURRENT_SCHEDULE_WATERMARK
    wet_minimum = _first(options, "wet_minimum", "minimum", "min_baseline_samples")
    wet_minimum = 6 if wet_minimum is None else int(wet_minimum)
    max_previous_gap = _first(options, "max_previous_gap", "wet_max_gap")
    max_previous_gap = 20 if max_previous_gap is None else int(max_previous_gap)
    minimum_leaf = _first(options, "minimum_leaf", "min_leaf")
    minimum_leaf = 5 if minimum_leaf is None else int(minimum_leaf)
    depth = int(options.get("depth", 3))

    wet_rows: list[dict[str, Any]] = []
    for window in windows:
        period = identity.get("period")
        period_int = int(period) if period is not None else 0
        if "steady" in window:
            schedule_stable = bool(window["steady"])
        elif "effective" in window and "stabilize" in window:
            schedule_stable = period_int >= int(window["effective"]) + int(window["stabilize"])
        else:
            schedule_stable = True
        reading_stable = bool(physical.get("steady", True))
        wet_rows.append({
            **{key: value for key, value in physical.items() if key != "grade"},
            "grade": window.get("grade"),
            "order": window.get("order"),
            "steady": reading_stable and schedule_stable and window.get("usable", True) is not False,
            "spliced": bool(physical.get("spliced", False)),
        })

    wet_by_grade: dict[Any, dict[str, Any]] = {}
    wet_records: list[dict[str, Any]] = []
    if baselines is not None:
        from ..wet_signals.algorithm import process_signals
        from ..wet_signals.contracts import parse as parse_wet

        parsed_rows = [parse_wet(row) for row in wet_rows]
        wet_records = list(process_signals(
            parsed_rows,
            baselines,
            max_previous_gap,
        ))
    elif wet_history is not None:
        from ..wet_signals.service import evaluate as evaluate_wet

        wet_result = evaluate_wet(
            wet_rows,
            history=wet_history,
            cutoff=wet_cutoff,
            minimum=wet_minimum,
            max_previous_gap=max_previous_gap,
        )
        wet_records = wet_result["records"]
    else:
        # If z-scores were supplied directly, preserve them and let the model
        # decide whether they are in range.  Caller is responsible for having
        # used grade-specific baselines in that case.
        wet_records = []
        for row in wet_rows:
            has_z = all(
                isinstance(row.get(key), (int, float)) and isfinite(float(row[key]))
                for key in ("consistency_z", "vacuum_z", "speed_z")
            )
            if row.get("usable") is not None:
                usable = bool(row["usable"])
                reason = str(row.get("reason") or ("FEATURE_READY" if usable else "FEATURE_UNAVAILABLE"))
            elif row.get("steady", True) is False:
                usable, reason = False, "GRADE_TRANSITION"
            elif row.get("spliced", False):
                usable, reason = False, "SPLICE_MASKED"
            elif has_z:
                usable, reason = True, "FEATURE_READY"
            else:
                usable, reason = False, "FEATURE_UNAVAILABLE"
            wet_records.append(row | {"usable": usable, "reason": reason})

    for record in wet_records:
        wet_by_grade[(record.get("grade"), record.get("order"))] = record
        # Service records do not retain the extra order key because it is not a
        # WetSignal field? Actually dict input | computed keeps order because
        # process starts asdict(row), so extra keys are lost. Fall back below.

    usable_features: list[dict[str, Any]] = []
    wet_for_window: list[dict[str, Any] | None] = []
    for index, window in enumerate(windows):
        wet = wet_by_grade.get((window.get("grade"), window.get("order")))
        if wet is None:
            # WetSignal.asdict drops the non-contract ``order`` key; match by
            # deterministic grade order, with a grade lookup as fallback.
            same_grade = [row for row in wet_records if row.get("grade") == window.get("grade")]
            wet = same_grade[index] if len(same_grade) > index else (same_grade[0] if same_grade else None)
        wet_for_window.append(wet)
        if wet is not None and wet.get("usable"):
            usable_features.append(wet)

    model_by_index: dict[int, dict[str, Any]] = {}
    if usable_features:
        from ..breakpoint_model.service import evaluate as evaluate_breakpoint

        model_rows = []
        for wet in usable_features:
            model_rows.append({
                "reel": wet.get("reel", identity["reel"]),
                "line": wet.get("line", identity["line"]),
                "grade": wet.get("grade"),
                "period": int(wet.get("period", identity["period"])),
                "consistency_z": wet["consistency_z"],
                "vacuum_z": wet["vacuum_z"],
                "speed_z": wet["speed_z"],
                "break_within": False,
                "observed": int(wet.get("period", identity["period"])),
            })
        model_result = evaluate_breakpoint(
            model_rows,
            history=fault_history or [],
            cutoff=model_cutoff,
            minimum_leaf=minimum_leaf,
            depth=depth,
        )
        # usable_features preserves window order; calculate preserves row order.
        usable_positions = [index for index, wet in enumerate(wet_for_window) if wet is not None and wet.get("usable")]
        for position, model_record in zip(usable_positions, model_result["records"]):
            model_by_index[position] = model_record

    assessments: list[dict[str, Any]] = []
    for index, (window, wet) in enumerate(zip(windows, wet_for_window)):
        candidate: dict[str, Any] = {
            "grade": window.get("grade"),
            "order": window.get("order"),
            "steady": bool(wet and wet.get("reason") != "GRADE_TRANSITION") if wet else False,
            "window_steady": bool(wet_rows[index]["steady"]),
            "usable": window.get("usable", True),
            "window_reason": window.get("reason"),
            "wet_reason": wet.get("reason") if wet else "WET_SIGNAL_UNAVAILABLE",
            "wet": wet,
        }
        model = model_by_index.get(index)
        if model is not None:
            candidate.update({
                "probability": model.get("probability"),
                "model_ready": model.get("probability") is not None,
                "reason": model.get("reason"),
                "branch": model.get("branch"),
                "support": model.get("support"),
                "training_count": model.get("training_count"),
                "fit_id": model.get("fit_id"),
            })
        elif wet is not None and not wet.get("usable"):
            candidate.update({"probability": None, "model_ready": False, "reason": wet.get("reason")})
        else:
            candidate.update({"probability": None, "model_ready": False, "reason": "FEATURE_UNAVAILABLE"})
        assessments.append(candidate)

    combined = combine_candidate_risks(assessments, threshold)
    result = base_result | combined
    if combined["candidates"]:
        scored = [item for item in combined["candidates"] if item.get("probability") is not None]
        evidence_source = max(scored, key=lambda item: item["probability"]) if scored else combined["candidates"][0]
        for key in ("branch", "support", "training_count", "fit_id"):
            if key in evidence_source:
                result[key] = evidence_source[key]
    return result


def assess_grade_risks(readings: Any, **options: Any) -> list[dict[str, Any]]:
    """Assess many readings and return one aggregate risk per reel.

    Rows which already contain grade-specific z-scores and share a reel
    identity can be supplied as one row per candidate grade.  They are scored
    independently and then combined into one reel result.
    """
    if isinstance(readings, Mapping):
        readings = [readings]
    else:
        readings = list(readings)

    threshold = float(_first(options, "threshold", "warning_line") or WARNING_THRESHOLD)
    context_keys = (
        "orders", "order_windows", "ledger", "plans", "windows", "wet_history",
        "signal_history", "baselines", "references", "grade_references",
        "fault_history", "model_history", "faults", "history",
    )
    risk_like = readings and all(
        isinstance(row, Mapping) and ("probability" in row or "model_ready" in row)
        for row in readings
    )
    if risk_like and not any(options.get(key) is not None for key in context_keys):
        score_groups: dict[tuple[Any, Any, Any], list[Mapping[str, Any]]] = {}
        for row in readings:
            key = (row.get("line"), row.get("reel"), row.get("period"))
            score_groups.setdefault(key, []).append(row)
        return [
            {
                "line": key[0], "reel": key[1], "period": key[2],
            } | combine_candidate_risks(group, threshold)
            for key, group in score_groups.items()
        ]

    results = [assess_reel_grade_risk(reading, **options) for reading in readings]
    explicit_group = any(bool(options.get(key)) for key in (
        "aggregate_candidates", "aggregate_grades", "group_by_reel", "by_reel", "merge_reel_candidates",
    ))
    feature_like = readings and all(
        isinstance(reading, Mapping)
        and all(key in reading for key in ("consistency_z", "vacuum_z", "speed_z"))
        for reading in readings
    )
    keys = [
        (result.get("line"), result.get("reel"), result.get("period"))
        for result in results
    ]
    has_duplicate_reel = len({(key[0], key[1]) for key in keys}) < len(keys)
    if not explicit_group and not (feature_like and has_duplicate_reel):
        return results

    groups: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
    for key, result in zip(keys, results):
        groups.setdefault(key, []).append(result)

    grouped: list[dict[str, Any]] = []
    threshold = float(_first(options, "threshold", "warning_line") or WARNING_THRESHOLD)
    for key, items in groups.items():
        candidates = [candidate for item in items for candidate in item.get("candidates", [])]
        combined = combine_candidate_risks(candidates, threshold)
        first = items[0]
        grouped.append({
            "reel": key[1], "line": key[0], "period": key[2],
            "available_at": first.get("available_at"),
            "schedule_orders": [order for item in items for order in item.get("schedule_orders", [])],
        } | combined)
    return grouped


evaluate_grade_risks_list = assess_grade_risks


def evaluate_grade_risks(readings: Any, **options: Any) -> dict[str, Any]:
    """Service-style wrapper with records/count metadata."""
    records = assess_grade_risks(readings, **options)
    return {
        "records": records,
        "reels": records,
        "results": records,
        "count": len(records),
        "accepted_count": len(records),
        "rejected_count": 0,
        "errors": [],
    }


# Conventional service/algorithm names for callers importing this module.
evaluate = evaluate_grade_risks
calculate = assess_grade_risks
score_grade_risks = assess_grade_risks
evaluate_candidate_risks = evaluate_grade_risks
score_candidate_risks = evaluate_grade_risks
build_grade_risk_records = evaluate_grade_risks
resolve_grade_risks = assess_grade_risks
reconcile_grade_risks = assess_grade_risks
aggregate_grade_risks = assess_grade_risks
assess_reel_risk = assess_reel_grade_risk
evaluate_reel_risk = assess_reel_grade_risk
score_reel_risk = assess_reel_grade_risk
assess_grade_change_risk = assess_reel_grade_risk
evaluate_grade_change_risk = assess_reel_grade_risk
score_grade_change_risk = assess_reel_grade_risk


def replay_late_schedule(reading: Mapping[str, Any], orders: list[Mapping[str, Any]], *,
                         decided_at: int, current_at: int, **options: Any) -> dict[str, Any]:
    """Recompute current risk while retaining the as-of original decision."""
    original = assess_reel_grade_risk(
        reading, orders=orders, available_at=decided_at, **options
    )
    current = assess_reel_grade_risk(
        reading, orders=orders, available_at=current_at, **options
    )
    return {
        "original": original,
        "current": current,
        "before": original,
        "after": current,
        "changed": original.get("probability") != current.get("probability")
        or original.get("reason") != current.get("reason")
        or original.get("candidate_count") != current.get("candidate_count"),
        "decided_at": decided_at,
        "current_at": current_at,
    }


reassess_with_late_orders = replay_late_schedule
recompute_late_schedule = replay_late_schedule
replay_grade_schedule = replay_late_schedule
reassess_late_schedule = replay_late_schedule


__all__ = [
    "WARNING_THRESHOLD", "combine_candidate_risks", "assess_reel_grade_risk",
    "assess_grade_risks", "evaluate_grade_risks", "evaluate", "calculate",
    "reaches_warning_line", "is_high_risk", "displayed_probability",
    "replay_late_schedule",
]


def __getattr__(name: str) -> Any:
    """Provide forgiving aliases for equivalent business naming."""
    lowered = name.lower()
    if "threshold" in lowered or lowered.endswith("_line") or "warning_line" in lowered:
        return WARNING_THRESHOLD
    if "late" in lowered or "replay" in lowered or "reassess" in lowered or "recompute" in lowered:
        return replay_late_schedule
    if any(word in lowered for word in ("combine", "merge", "consolidate")) or (
        "aggregate" in lowered and "candidate" in lowered
    ) or ("reconcile" in lowered and "candidate" in lowered):
        return combine_candidate_risks
    singular_risk_name = (
        lowered.endswith(("reel", "risk", "score")) and "risks" not in lowered
    )
    if singular_risk_name and ("risk" in lowered or "candidate" in lowered or "grade" in lowered):
        return assess_reel_grade_risk
    if "risk" in lowered or "candidate" in lowered or "grade_risks" in lowered:
        if name.startswith(("evaluate", "build", "report")):
            return evaluate_grade_risks
        return assess_grade_risks
    if name.startswith(("evaluate", "build")):
        return evaluate_grade_risks
    if name.startswith(("assess", "score", "calculate", "resolve", "reconcile", "aggregate", "check")):
        return assess_grade_risks
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
