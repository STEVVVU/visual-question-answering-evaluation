from pathlib import Path
from unittest.mock import patch

from flask import Flask
import pandas as pd
import pytest

from data_formulator.datalake.workspace_manager import WorkspaceManager
from data_formulator.error_handler import register_error_handlers
from data_formulator.routes.experiments import experiments_bp


@pytest.fixture
def workspace(tmp_path):
    manager = WorkspaceManager(tmp_path)
    manager.create_workspace("test")
    workspace = manager.open_workspace("test", "test-user")
    frame = pd.read_csv(Path(__file__).resolve().parents[3] / "tools/fixtures/synthetic-experiment.csv")
    workspace.write_parquet(frame, "experiment")
    return workspace, manager


@pytest.fixture
def client(workspace):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(experiments_bp)
    register_error_handlers(app)
    with patch("data_formulator.routes.experiments.get_workspace_manager", return_value=workspace[1]), \
         patch("data_formulator.routes.experiments.get_identity_id", return_value="test-user"):
        with app.test_client() as client:
            yield client


def payload():
    return {
        "table": "experiment", "unit_column": "unit_id", "variant_column": "variant",
        "outcome_column": "outcome", "covariate_column": "pre_period", "control": "control",
        "treatment": "treatment", "expected_treatment_fraction": 0.5,
        "randomized_units_confirmed": True, "pre_period_confirmed": True, "fixed_horizon_confirmed": True,
    }


def test_actual_workspace_table_is_analyzed_without_sampling_or_writes(client, workspace):
    workspace = workspace[0]
    before = {p.relative_to(workspace.confined_root.root): p.read_bytes()
              for p in workspace.confined_root.root.rglob("*") if p.is_file()}
    result = client.post("/api/experiments/analyze", json=payload(), headers={"X-Workspace-Id": "test"})
    report = result.get_json()["data"]
    assert result.status_code == 200
    assert report["status"] == "ready"
    assert report["counts"]["total"] == 1000
    after = {p.relative_to(workspace.confined_root.root): p.read_bytes()
             for p in workspace.confined_root.root.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.parametrize("change", [{"table": "../../outside"}, {"pre_period_confirmed": False}, {"extra": True}])
def test_invalid_specification_returns_standard_error(client, change):
    body = client.post("/api/experiments/analyze", json={**payload(), **change},
                       headers={"X-Workspace-Id": "test"}).get_json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_missing_workspace_is_rejected_before_read(client):
    with patch("data_formulator.routes.experiments.get_workspace_manager") as get_manager:
        body = client.post("/api/experiments/analyze", json=payload()).get_json()
        assert body["status"] == "error"
        get_manager.assert_not_called()


def test_oversized_specification_is_rejected(client):
    body = client.post("/api/experiments/analyze", json={**payload(), "padding": "x" * 17000},
                       headers={"X-Workspace-Id": "test"}).get_json()
    assert body["status"] == "error"


def test_unknown_workspace_does_not_create_a_workspace(client, workspace):
    manager = workspace[1]
    before = sorted(str(p) for p in manager.root.rglob("*"))
    body = client.post("/api/experiments/analyze", json=payload(),
                       headers={"X-Workspace-Id": "absent"}).get_json()
    assert body["status"] == "error"
    assert not manager.workspace_exists("absent")
    assert sorted(str(p) for p in manager.root.rglob("*")) == before


@pytest.mark.parametrize("body,content_type", [("{", "application/json"), ("{}", "text/plain")])
def test_malformed_or_non_json_body_is_a_validation_error(client, body, content_type):
    response = client.post("/api/experiments/analyze", data=body, content_type=content_type,
                           headers={"X-Workspace-Id": "test"})
    result = response.get_json()
    assert response.status_code != 500
    assert result["status"] == "error"
    assert result["error"]["code"] == "VALIDATION_ERROR"
