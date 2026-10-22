import io
import re

from flask import Blueprint, jsonify, request, send_file

from data_formulator.auth.identity import get_identity_id
from data_formulator.error_handler import json_ok
from data_formulator.errors import ErrorCode
from data_formulator.workspace_factory import get_workspace_manager, _get_backend
from data_formulator.vqa import Store, summarize
from data_formulator.vqa_execution import start

vqa_bp = Blueprint("vqa", __name__, url_prefix="/api/vqa")


def store():
    if _get_backend() not in ("local", "ephemeral"):
        raise ValueError("VQA requires a local or ephemeral workspace backend.")
    identity = get_identity_id()
    workspace = request.headers.get("X-Workspace-Id", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workspace):
        raise ValueError("Select a valid workspace first.")
    manager = get_workspace_manager(identity)
    if not manager.workspace_exists(workspace):
        raise ValueError("Create or save this workspace first.")
    opened = manager.open_workspace(workspace, identity)
    return Store(opened.confined_root.resolve("vqa", mkdir_parents=True))


@vqa_bp.errorhandler(ValueError)
def invalid(error):
    return jsonify({"status": "error", "error": {"code": ErrorCode.VALIDATION_ERROR, "message": str(error), "retry": False}}), 200


def payload():
    if request.content_length is None or request.content_length > 3_000_000:
        raise ValueError("VQA request exceeds the 3 MB limit.")
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


@vqa_bp.get("/state")
def state():
    data = store()
    runs = [summarize(r) for r in data.runs()]
    return json_ok({"items": data.all("items"), "versions": data.all("versions"), "runs": runs})


@vqa_bp.post("/items")
def save_item():
    value = payload()
    return json_ok(store().save_item(value))


@vqa_bp.get("/images/<image_id>")
def image(image_id):
    data = store().image(image_id)
    response = send_file(io.BytesIO(data), mimetype="image/png", max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@vqa_bp.post("/versions")
def freeze():
    return json_ok(store().freeze(payload().get("split")))


@vqa_bp.post("/runs")
def run():
    return json_ok(start(store(), payload().get("version")))


@vqa_bp.post("/runs/<run_id>/cancel")
def cancel(run_id):
    payload()
    return json_ok(store().cancel(run_id))


@vqa_bp.get("/runs/<run_id>/export")
def export(run_id):
    return json_ok(store().export(run_id))
