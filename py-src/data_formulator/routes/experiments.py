from flask import Blueprint, request

from data_formulator.auth.identity import get_identity_id
from data_formulator.error_handler import json_ok
from data_formulator.errors import AppError, ErrorCode
from data_formulator.experiments import ExperimentSpec, MAX_ROWS, analyze_experiment
from data_formulator.workspace_factory import get_workspace_manager


experiments_bp = Blueprint("experiments", __name__, url_prefix="/api/experiments")


@experiments_bp.route("/analyze", methods=["POST"])
def analyze():
    if request.content_length is None or request.content_length > 16_384:
        raise AppError(ErrorCode.INVALID_REQUEST, "Experiment specification exceeds the request limit.")
    try:
        spec = ExperimentSpec.parse(request.get_json(silent=True))
        workspace_id = request.headers.get("X-Workspace-Id")
        if not workspace_id:
            raise ValueError("Open or create a workspace before analyzing a table.")
        identity_id = get_identity_id()
        manager = get_workspace_manager(identity_id)
        if not manager.workspace_exists(workspace_id):
            raise ValueError("The workspace does not exist. Open or upload data before analysis.")
        workspace = manager.open_workspace(workspace_id, identity_id)
        metadata = workspace.get_table_metadata(spec.table)
        if metadata is None:
            raise ValueError("The selected table is not in the current workspace.")
        if metadata.row_count is not None and metadata.row_count > MAX_ROWS:
            raise ValueError(f"The table exceeds the {MAX_ROWS:,}-row analysis limit. No sample was taken.")
        frame = workspace.read_data_as_df(spec.table)
        return json_ok(analyze_experiment(frame, spec))
    except ValueError as error:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(error)) from error
