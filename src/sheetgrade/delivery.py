from __future__ import annotations

import argparse
import json
import re
from hashlib import sha256
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from .breakpoint_model.grade_risk import (
    assess_reel_grade_risk,
    replay_late_schedule as grade_risk_replay_helper,
)
from .pipeline import grade_risk_report, run

MODULES = (('machine_reel', '机台抄表'), ('grade_change', '纸种换线'), ('wet_signals', '湿部特征'), ('breakpoint_model', '断纸拟合'), ('release_order', '质检处置'), ('spool_audit', '班组对账'))
application = Flask(__name__, static_folder="web/static", template_folder="web/templates")
application.config["MAX_CONTENT_LENGTH"] = 8192
application.json.sort_keys = False
application.json.sort_keys = False
_initial_snapshot = None
_initial_lock = Lock()


def snapshot(capacity=None):
    business_result = run(capacity)
    if len(business_result["stages"]) != len(MODULES):
        raise RuntimeError("运行证据缺少业务环节")
    content = json.dumps(business_result, ensure_ascii=False, allow_nan=False, sort_keys=True)
    return {**business_result, "run_id": sha256(content.encode()).hexdigest()[:12]}


def initial_snapshot():
    global _initial_snapshot
    with _initial_lock:
        if _initial_snapshot is None:
            _initial_snapshot = snapshot()
    return _initial_snapshot


def parse_capacity(value):
    if type(value) is not int or not 1 <= value <= 100:
        raise ValueError("请输入 1 到 100 之间的整数")
    return value


def parse_as_of(value):
    parsed = int(value)
    if not 0 <= parsed <= 10_000_000:
        raise ValueError("时间水位超出范围")
    return parsed


@application.get("/")
def index():
    return render_template("index.html", modules=MODULES)


@application.get("/health")
def health():
    return jsonify(status="ok", service="sheetgrade")


@application.get("/api/overview")
def overview():
    return jsonify(initial_snapshot())


@application.post("/api/scenario")
def scenario():
    try:
        body = request.get_json()
        if not isinstance(body, dict) or set(body) != {"capacity"}:
            raise ValueError("请提交仅包含 capacity 的 JSON 对象")
        capacity = parse_capacity(body["capacity"])
    except (BadRequest, UnsupportedMediaType, ValueError):
        return jsonify(code="INVALID_SCENARIO", message="请输入 1 到 100 之间的整数"), 400
    return jsonify(snapshot(capacity))


@application.get("/api/stages/<module>")
def stage_evidence(module):
    positions = {slug: index for index, (slug, _) in enumerate(MODULES)}
    if module not in positions or not re.fullmatch(r"[a-z_]+", module):
        return jsonify(code="UNKNOWN_STAGE", message="找不到该业务环节"), 404
    raw_capacity = request.args.get("capacity")
    raw_as_of = next((request.args.get(key) for key in ("as_of", "at", "available_at", "schedule_at")
                      if request.args.get(key) is not None), None)
    try:
        result = initial_snapshot() if raw_capacity is None else snapshot(parse_capacity(int(raw_capacity)))
        as_of = parse_as_of(raw_as_of) if raw_as_of is not None else None
    except ValueError:
        return jsonify(code="INVALID_SCENARIO", message="请输入 1 到 100 之间的整数"), 400
    stage = dict(result["stages"][positions[module]])
    if as_of is not None and module == "breakpoint_model":
        report = grade_risk_report(at=as_of)
        overlap_risks = [row for row in report["records"] if row.get("candidate_count", 0) > 1]
        stage["grade_risks"] = overlap_risks
        stage["all_grade_risks"] = report["records"]
        stage["reel_grade_risks"] = report["records"]
        stage["overlap_grade_risks"] = overlap_risks
        stage["overlapping_grade_risks"] = overlap_risks
        stage["candidate_grade_risks"] = overlap_risks
        stage["grade_risk_threshold"] = report["threshold"]
        stage["schedule_as_of"] = as_of
        # Replace only aggregate rows; ordinary CART evidence is schedule-independent.
        stage["records"] = [
            row for row in stage.get("records", []) if row.get("candidate_count", 0) <= 1
        ] + [row for row in report["records"] if row.get("candidate_count", 0) > 1]
    return jsonify(run_id=result["run_id"], **stage)


def _grade_risk_payload():
    body = request.get_json()
    if not isinstance(body, dict):
        raise ValueError("JSON object required")
    if isinstance(body.get("reading"), dict):
        reading = body["reading"]
    else:
        reading_keys = {
            "reel", "line", "period", "grade", "speed", "vacuum", "vacuum_kpa",
            "vacuum_unit", "consistency", "steady", "spliced",
        }
        reading = {key: body[key] for key in reading_keys if key in body}
    options = {key: value for key, value in body.items() if key != "reading"}
    return reading, options


@application.post("/api/grade-risk")
@application.post("/api/grade-risks")
@application.post("/api/grade_risk")
@application.post("/api/grade_risks")
@application.post("/api/grade-risk/replay")
@application.post("/api/grade-risks/replay")
def grade_risk_replay():
    try:
        reading, options = _grade_risk_payload()
        decided_at = options.pop("decided_at", None)
        current_at = options.pop("current_at", None)
        if decided_at is not None and current_at is not None:
            result = grade_risk_replay_helper(
                reading, options.pop("orders", []),
                decided_at=parse_as_of(decided_at), current_at=parse_as_of(current_at),
                **options,
            )
        else:
            result = assess_reel_grade_risk(reading, **options)
    except (BadRequest, UnsupportedMediaType, TypeError, ValueError) as error:
        return jsonify(code="INVALID_GRADE_RISK_REQUEST", message="换线风险请求无法计算"), 400
    return jsonify(result)


@application.errorhandler(RequestEntityTooLarge)
def request_too_large(_error):
    return jsonify(code="REQUEST_TOO_LARGE", message="请求内容超出 8 KiB"), 413


def main():
    parser = argparse.ArgumentParser(description="造纸湿部断纸质控")
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate", help="执行本地样例并输出业务证据")
    evaluate.add_argument("--capacity", type=int)
    evaluate.add_argument("--output", type=Path)
    serve = commands.add_parser("serve", help="启动浏览服务")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "serve":
        from waitress import serve as serve_wsgi
        initial_snapshot()
        serve_wsgi(application, host="0.0.0.0", port=args.port)
        return
    try:
        capacity = parse_capacity(args.capacity) if args.capacity is not None else None
    except ValueError as error:
        parser.error(str(error))
    output = json.dumps(snapshot(capacity), ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
