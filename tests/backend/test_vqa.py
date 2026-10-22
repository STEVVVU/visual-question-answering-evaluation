import base64
import io
from unittest.mock import patch

from flask import Flask
from PIL import Image
import pytest

from data_formulator.datalake.workspace_manager import WorkspaceManager
from data_formulator.error_handler import register_error_handlers
from data_formulator.routes.vqa import vqa_bp
from data_formulator.vqa import Store, annotation, answer_key, digest, image_png, score, summarize
from data_formulator.vqa_execution import execute


def fixture():
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(output, "PNG")
    return {"image": base64.b64encode(output.getvalue()).decode(), "question": "What color?", "context": "",
            "answer": "red", "answer_label": None, "answer_mode": "exact", "choices": [], "aliases": [], "split": "validation"}


def test_immutable_manifest_annotation_and_question_cache_invalidation(tmp_path):
    store = Store(tmp_path)
    item = store.save_item(fixture())
    original = store.freeze("validation")
    assert store.freeze("validation") == original
    revised = store.save_item({**item, "answer": "scarlet", "aliases": ["red"]})
    second = store.freeze("validation")
    assert second["id"] != original["id"]
    assert store.get("versions", original["id"]) == original
    assert answer_key(item, "model-files") == answer_key(revised, "model-files")
    assert digest({"annotation": item, "scorer": "v1"}) != digest({"annotation": revised, "scorer": "v1"})
    for field, value in [("question", "Which color?"), ("context", "Use the foreground."),
                         ("choices", ["red", "blue"]), ("image_sha256", "changed"), ("answer_mode", "manual")]:
        assert answer_key(item, "model-files") != answer_key({**item, field: value}, "model-files")
    assert answer_key(item, "model-files") != answer_key(item, "new-model-files")


@pytest.mark.parametrize("prediction,correct", [("RED.", True), (" red  ", True), ("blue", False), ("It is red", False)])
def test_exact_scoring_is_explicit_not_subjective(prediction, correct):
    assert score(annotation(fixture()), prediction) == {"status": "scored", "correct": correct}


def test_mcq_and_manual_mode_routing():
    item = annotation({**fixture(), "choices": ["red", "blue"], "answer_mode": "mcq", "answer_label": 0})
    for value in ["A", "(a)", "A.", "red", "A. red."]:
        assert score(item, value)["correct"] is True
    assert score(item, "The answer is A because...")["correct"] is False
    assert score(item, "A. blue")["correct"] is False
    assert score({**item, "answer_mode": "manual"}, "anything")["correct"] is None


@pytest.mark.parametrize("change", [{"answer_mode": "judge"}, {"question": ""}, {"split": "../../"},
                                   {"choices": ["red", "RED"]}, {"answer_mode": "mcq", "answer_label": True},
                                   {"choices": "red"}, {"aliases": [None]}, {"context": "x" * 4001}])
def test_canonical_schema_rejects_invalid_boundary(change):
    with pytest.raises(ValueError):
        annotation({**fixture(), **change})


@pytest.mark.parametrize("raw", [b"<svg></svg>", b"not an image"])
def test_image_decode_rejects_non_raster(raw):
    with pytest.raises(ValueError):
        image_png(base64.b64encode(raw).decode())


def test_dimensions_and_size_are_bounded():
    output = io.BytesIO()
    Image.new("RGB", (2049, 1)).save(output, "PNG")
    with pytest.raises(ValueError):
        image_png(base64.b64encode(output.getvalue()).decode())
    with pytest.raises(ValueError):
        image_png("a" * 2_800_001)


def test_failed_and_manual_runs_never_rank():
    base = {"status": "failed", "total": 1, "results": [{"score": {"correct": True}}]}
    assert not summarize(base)["rankable"]
    assert summarize({**base, "status": "completed"})["rankable"]
    assert not summarize({**base, "status": "completed", "results": [{"score": {"correct": None}}]})["rankable"]


def test_model_load_failure_is_durable_and_never_falls_back(tmp_path):
    from data_formulator.vqa_execution import SLOT
    store = Store(tmp_path)
    store.save_item(fixture())
    version = store.freeze("validation")
    run = {"id": "failure", "status": "running", "total": 1, "results": [], "cache_hits": 0}
    run["cancel_requested"] = False
    store.put("runs", run["id"], run)
    SLOT.acquire()
    with patch.dict("os.environ", {"VQA_MODEL_PATH": "", "VQA_MODEL_PYTHON": ""}):
        execute(store, run, version)
    saved = store.get("runs", "failure")
    assert saved["status"] == "failed" and saved["results"] == [] and not saved["rankable"]
    assert "Configure" in saved["error"]


def test_interrupted_run_reconciliation_retains_partial_results(tmp_path):
    store = Store(tmp_path)
    store.put("runs", "old", {"id": "old", "status": "running", "boot": "prior-process", "results": [{"prediction": "saved"}]})
    assert store.runs()[0]["status"] == "failed"
    assert store.runs()[0]["results"] == [{"prediction": "saved"}]


def test_cancel_request_survives_worker_progress_write(tmp_path):
    store = Store(tmp_path)
    store.put("runs", "run", {"status": "running", "cancel_requested": False, "results": []})
    stale = store.get("runs", "run")
    store.put("runs", "run", {"status": "running", "cancel_requested": True, "results": []})
    store.put("runs", "run", {**stale, "results": ["completed item"]})
    assert store.get("runs", "run")["cancel_requested"] is True


def test_cancel_and_completion_use_atomic_terminal_state(tmp_path):
    store = Store(tmp_path)
    store.put("runs", "run", {"status": "running", "cancel_requested": False, "rankable": False})
    store.cancel("run")
    store.put("runs", "run", {"status": "completed", "cancel_requested": False, "rankable": True})
    assert store.get("runs", "run")["status"] == "cancelled"
    assert not store.get("runs", "run")["rankable"]
    store.put("runs", "done", {"status": "completed", "cancel_requested": False})
    assert store.cancel("done")["status"] == "completed"


def test_partial_model_failure_retains_real_boundary_output_without_ranking(tmp_path):
    import json
    from data_formulator.vqa_execution import SLOT
    store = Store(tmp_path / "store")
    item = store.save_item(fixture())
    version = store.freeze("validation")
    run = {"id": "partial", "status": "running", "total": 1, "results": [], "cache_hits": 0, "cancel_requested": False}
    store.put("runs", run["id"], run)

    class FailedProcess:
        returncode = 1

        def __init__(self, command, **kwargs):
            from pathlib import Path
            Path(command[-1]).write_text(json.dumps({"id": item["id"], "prediction": "red"}) + "\n", encoding="utf-8")

        def poll(self):
            return self.returncode

    SLOT.acquire()
    with patch("data_formulator.vqa_execution.model_configuration", return_value=("python", tmp_path, {})), \
         patch("data_formulator.vqa_execution.subprocess.Popen", FailedProcess):
        execute(store, run, version)
    saved = store.get("runs", run["id"])
    assert saved["status"] == "failed" and not saved["rankable"]
    assert len(saved["results"]) == 1 and saved["results"][0]["prediction"] == "red"


def test_shared_answer_cache_preserves_each_annotation_identity_and_gold(tmp_path):
    from data_formulator.vqa_execution import SLOT
    store = Store(tmp_path)
    first = store.save_item(fixture())
    second = store.save_item({**fixture(), "answer": "blue"})
    version = store.freeze("validation")
    key = answer_key(first, digest({}))
    store.put("answers", key, {"id": first["id"], "prediction": "red"})
    run = {"id": "shared", "status": "running", "total": 2, "results": [], "cache_hits": 0, "cancel_requested": False}
    store.put("runs", run["id"], run)
    SLOT.acquire()
    with patch("data_formulator.vqa_execution.model_configuration", return_value=("python", tmp_path, {})):
        execute(store, run, version)
    saved = store.get("runs", run["id"])
    assert saved["status"] == "completed" and saved["cache_hits"] == 2
    results = {r["id"]: r for r in saved["results"]}
    assert set(results) == {first["id"], second["id"]}
    assert results[first["id"]]["score"]["correct"] is True
    assert results[second["id"]]["score"]["correct"] is False


def test_pre_requested_cancellation_wins_over_all_cached_completion(tmp_path):
    from data_formulator.vqa_execution import SLOT
    store = Store(tmp_path)
    item = store.save_item(fixture())
    version = store.freeze("validation")
    store.put("answers", answer_key(item, digest({})), {"prediction": "red"})
    run = {"id": "cancel-cache", "status": "running", "total": 1, "results": [], "cache_hits": 0, "cancel_requested": True}
    store.put("runs", run["id"], run)
    SLOT.acquire()
    with patch("data_formulator.vqa_execution.model_configuration", return_value=("python", tmp_path, {})):
        execute(store, run, version)
    result = store.get("runs", run["id"])
    assert result["status"] == "cancelled"
    assert not result["rankable"]


def test_emitted_prediction_is_durable_before_process_exit(tmp_path):
    import json
    from pathlib import Path
    from data_formulator.vqa_execution import SLOT
    store = Store(tmp_path / "store")
    first = store.save_item(fixture())
    store.save_item({**fixture(), "question": "Which shape?"})
    version = store.freeze("validation")
    run = {"id": "live-partial", "status": "running", "total": 2, "results": [], "cache_hits": 0, "cancel_requested": False}
    store.put("runs", run["id"], run)
    observed = []

    class PartialProcess:
        returncode = None

        def __init__(self, command, **kwargs):
            self.calls = 0
            Path(command[-1]).write_text(json.dumps({"id": first["id"], "prediction": "red"}) + "\n", encoding="utf-8")

        def poll(self):
            self.calls += 1
            if self.calls == 2:
                observed.extend(store.get("runs", run["id"])["results"])
                self.returncode = 1
            return self.returncode

    SLOT.acquire()
    with patch("data_formulator.vqa_execution.model_configuration", return_value=("python", tmp_path, {})), \
         patch("data_formulator.vqa_execution.subprocess.Popen", PartialProcess):
        execute(store, run, version)
    assert len(observed) == 1, "The flushed prediction must reach SQLite while the model process is alive"
    saved = store.get("runs", run["id"])
    assert saved["status"] == "failed" and not saved["rankable"] and len(saved["results"]) == 1


def test_cleanup_failure_persists_terminal_state_and_releases_slot(tmp_path):
    import subprocess
    import threading
    from data_formulator import vqa_execution as execution
    store = Store(tmp_path)
    store.save_item(fixture())
    version = store.freeze("validation")
    run = {"id": "cleanup-failure", "status": "running", "total": 1, "results": [], "cache_hits": 0, "cancel_requested": False}
    store.put("runs", run["id"], run)

    class LiveProcess:
        pid = 4242
        returncode = None

        def __init__(self, *args, **kwargs):
            store.cancel(run["id"])

        def poll(self):
            return None

    slot = threading.BoundedSemaphore(1)
    with patch.object(execution, "SLOT", slot), \
         patch.object(execution, "CLEANUP_FAILURE", None), \
         patch.object(execution, "model_configuration", return_value=("python", tmp_path, {})), \
         patch.object(execution.subprocess, "Popen", LiveProcess), \
         patch.object(execution, "stop_model", side_effect=subprocess.TimeoutExpired("owned-stop", 30)):
        slot.acquire()
        escaped = None
        try:
            execution.execute(store, run, version)
        except subprocess.TimeoutExpired as error:
            escaped = error
        assert escaped is None, "Cleanup failure must not skip final persistence and semaphore bookkeeping"
        saved = store.get("runs", run["id"])
        assert saved["status"] == "failed" and not saved["rankable"]
        assert saved["cleanup"]["process_id"] == 4242 and saved["cleanup"]["may_be_alive"]
        assert "TimeoutExpired" in saved["error"]
        assert slot.acquire(blocking=False), "The failed worker must release its semaphore ownership"
        slot.release()
        with pytest.raises(ValueError, match="blocked after an owned-process cleanup failure"):
            execution.start(store, version["id"])
        assert slot.acquire(blocking=False)
        slot.release()


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)
    app.config["TESTING"] = True
    register_error_handlers(app)
    app.register_blueprint(vqa_bp)
    manager = WorkspaceManager(tmp_path / "user-one")
    manager.create_workspace("owned")
    with patch("data_formulator.routes.vqa.get_identity_id", return_value="one"), \
         patch("data_formulator.routes.vqa.get_workspace_manager", return_value=manager), \
         patch("data_formulator.routes.vqa._get_backend", return_value="local"):
        yield app.test_client(), manager


def test_http_upload_version_export_boundaries_and_workspace_isolation(client, tmp_path):
    http, manager = client
    headers = {"X-Workspace-Id": "owned"}
    item = http.post("/api/vqa/items", json=fixture(), headers=headers).get_json()["data"]
    image = http.get(f"/api/vqa/images/{item['image_sha256']}", headers=headers)
    assert image.content_type == "image/png" and image.headers["X-Content-Type-Options"] == "nosniff"
    version = http.post("/api/vqa/versions", json={"split": "validation"}, headers=headers).get_json()["data"]
    assert len(version["manifest"]["items"]) == 1
    other = WorkspaceManager(tmp_path / "user-two")
    other.create_workspace("owned")
    with patch("data_formulator.routes.vqa.get_workspace_manager", return_value=other):
        assert http.get("/api/vqa/state", headers=headers).get_json()["data"]["items"] == []
        assert http.get(f"/api/vqa/images/{item['image_sha256']}", headers=headers).get_json()["status"] == "error"
    for bad in ["", "../../outside", "absent"]:
        response = http.get("/api/vqa/state", headers={"X-Workspace-Id": bad}).get_json()
        assert response["status"] == "error"
    assert not manager.workspace_exists("absent")
    for value in [{}, {**fixture(), "image": "<svg/>"}, {**fixture(), "image": "a" * 3_000_000}]:
        assert http.post("/api/vqa/items", json=value, headers=headers).get_json()["status"] == "error"
