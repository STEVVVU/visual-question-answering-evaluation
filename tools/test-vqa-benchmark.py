"""Deterministic checks for benchmark statistics and generation trust boundary."""
from pathlib import Path
import base64
import hashlib
import runpy
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from data_formulator.vqa import answer_key, digest, GENERATION, MODEL, PROMPT, REVISION, score, SCORER

root = Path(__file__).resolve().parents[1]
helpers = runpy.run_path(str(root / "tools" / "benchmark-vqa-journey.py"))
assert helpers["quantiles"]([5, 1, 4, 2, 3]) == {"n": 5, "p50": 3, "p95": 5, "p99": 5, "min": 1, "max": 5}
assert helpers["quantiles"]([])["p99"] is None
assert helpers["wilson"](0, 128)[0] >= -1e-15
assert helpers["wilson"](128, 128)[1] <= 1 + 1e-15
model_manifest = {"model": MODEL, "revision": REVISION, "license": "Apache-2.0", "files": [{"name": "model.safetensors", "sha256": "weights"}]}
annotation = {"id": "1" * 32, "revision": 1, "image_sha256": "owned-image", "question": "What color?",
    "context": "", "choices": ["red", "blue"], "answer": "red", "answer_label": 0, "answer_mode": "mcq",
    "aliases": [], "split": "validation"}
def make_export(item, prediction="A", cache_hit=False):
    version = {"schema": "vqa-manifest-v1", "split": "validation", "items": [item]}
    key = answer_key(item, digest(model_manifest))
    scored = score(item, prediction)
    row = {"id": item["id"], "prediction": prediction, "answer_key": key,
        "score_key": digest({"answer_key": key, "prediction": prediction, "annotation": item, "scorer": SCORER}),
        "score": scored, "cache_hit": cache_hit, "vision_forwards": 1, "generation_seconds": 3.2}
    run = {"model": MODEL, "revision": REVISION, "generation": GENERATION, "prompt_version": PROMPT,
        "scorer": SCORER, "model_manifest": model_manifest, "device": "cpu", "finished": 123,
        "status": "completed", "total": 1, "cache_hits": int(cache_hit), "results": [row]}
    return {"version": {"id": digest(version), "manifest": version}, "run": run}

exported = make_export(annotation)
assert exported["run"]["results"][0]["answer_key"] == answer_key(annotation, digest(model_manifest))
assert exported["run"]["results"][0]["answer_key"] != answer_key(annotation, model_manifest)
other_id_item = {**annotation, "id": "2" * 32}
cached_export = make_export(other_id_item, cache_hit=True)
assert exported["version"]["id"] != cached_export["version"]["id"]
assert exported["run"]["results"][0]["score_key"] != cached_export["run"]["results"][0]["score_key"]
assert helpers["cache_input_parity"](exported) == helpers["cache_input_parity"](cached_export)
assert helpers["cache_predictions"](exported) == helpers["cache_predictions"](cached_export)
assert helpers["validate_cache_arm"](exported, "cold", 1) == helpers["validate_cache_arm"](cached_export, "cached", 1)
different_content = make_export({**other_id_item, "question": "What shape?"}, cache_hit=True)
assert helpers["cache_input_parity"](exported) != helpers["cache_input_parity"](different_content)
different_answer = make_export(other_id_item, prediction="B", cache_hit=True)
assert helpers["cache_predictions"](exported) != helpers["cache_predictions"](different_answer)
different_score = json.loads(json.dumps(cached_export))
different_score["run"]["results"][0]["score"]["correct"] = False
try:
    helpers["validate_cache_arm"](different_score, "cached", 1)
except ValueError:
    pass
else:
    raise AssertionError("Mismatched scorer output was accepted")
multi_items = [{**annotation, "id": f"{index + 1:032x}", "question": f"Question {index + 1}?"} for index in range(3)]
multi_export = make_export(multi_items[0])
multi_export["version"]["manifest"]["items"] = multi_items
multi_export["run"]["total"] = 3
multi_export["run"]["results"] = [
    {**make_export(item)["run"]["results"][0], "vision_forwards": forwards}
    for item, forwards in zip(multi_items, (0, 2, 1))]
try:
    helpers["validate_cache_arm"](multi_export, "cold", 3)
except ValueError:
    pass
else:
    raise AssertionError("A cold item without a vision forward was accepted")
for invalid, arm in [
    ({**exported, "run": {**exported["run"], "cache_hits": 1}}, "cold"),
    ({**exported, "run": {**exported["run"], "cache_hits": 1, "results": [{**exported["run"]["results"][0], "cache_hit": True}]}}, "cold"),
    ({**cached_export, "run": {**cached_export["run"], "cache_hits": 0}}, "cached"),
    ({**cached_export, "run": {**cached_export["run"], "cache_hits": 0, "results": [{**cached_export["run"]["results"][0], "cache_hit": False}]}}, "cached"),
]:
    try:
        helpers["validate_cache_arm"](invalid, arm, 1)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Invalid {arm} cache counts were accepted")
try:
    helpers["cache_input_parity"]({**exported, "run": {**exported["run"], "model_manifest": None}})
except (ValueError, TypeError):
    pass
else:
    raise AssertionError("Missing model provenance was accepted")
try:
    helpers["cache_input_parity"]({**exported, "run": {**exported["run"], "results": [{**exported["run"]["results"][0], "score": None}]}})
except (ValueError, TypeError):
    pass
else:
    raise AssertionError("Missing scorer output was accepted")
summary = helpers["cache_timing_summary"]([
    {"pair": 1, "prepopulation": {}, "timed_order": ["cold", "cached"], "arms": {"cold": {"terminal_seconds": 5, "request_seconds": 1}, "cached": {"terminal_seconds": 2, "request_seconds": 0.5}}},
    {"pair": 2, "prepopulation": {}, "timed_order": ["cached", "cold"], "arms": {"cold": {"terminal_seconds": 7, "request_seconds": 3}, "cached": {"terminal_seconds": 4, "request_seconds": 1.5}}},
])
assert summary == {"cold_terminal_seconds": {"median": 6.0, "min": 5, "max": 7},
    "cached_terminal_seconds": {"median": 3.0, "min": 2, "max": 4},
    "cold_request_seconds": {"median": 2.0, "min": 1, "max": 3},
    "cached_request_seconds": {"median": 1.0, "min": 0.5, "max": 1.5}, "pairs": 2}
owned_workspaces = {}
new_id = 0
def fake_api(route, payload=None, workspace=None):
    global new_id
    if route == "/api/sessions/create":
        owned_workspaces[payload["id"]] = []
        return {}
    if route == "/api/vqa/items":
        assert "id" not in payload, "The item endpoint treats a supplied ID as an update."
        new_id += 1
        item = {key: value for key, value in payload.items() if key != "image"}
        item.update(id=f"{new_id:032x}", revision=1, image_sha256="owned-image")
        owned_workspaces[workspace].append(item)
        return item
    if route == "/api/vqa/versions":
        manifest = {"schema": "vqa-manifest-v1", "split": payload["split"],
            "items": sorted(owned_workspaces[workspace], key=lambda row: row["id"])}
        return {"id": digest(manifest), "manifest": manifest}
    raise AssertionError(f"Unexpected mocked HTTP route: {route}")
setup_base = {"image": "owned-image", "context": "", "aliases": [], "split": "validation", "answer_mode": "mcq"}
setup_questions = [{"question": "What color?", "choices": ["red", "blue"], "answer": "red", "answer_label": 0}]
first_version = helpers["cache_setup"](fake_api, "cold-workspace", setup_base, setup_questions)
second_version = helpers["cache_setup"](fake_api, "warm-workspace", setup_base, setup_questions)
assert first_version["id"] != second_version["id"]
assert [{k: v for k, v in row.items() if k != "id"} for row in first_version["manifest"]["items"]] == [
    {k: v for k, v in row.items() if k != "id"} for row in second_version["manifest"]["items"]]
auto_cold = make_export(first_version["manifest"]["items"][0])
auto_cached = make_export(second_version["manifest"]["items"][0], cache_hit=True)
assert auto_cold["run"]["results"][0]["id"] != auto_cached["run"]["results"][0]["id"]
assert auto_cold["run"]["results"][0]["score_key"] != auto_cached["run"]["results"][0]["score_key"]
assert helpers["validate_cache_arm"](auto_cold, "cold", 1) == helpers["validate_cache_arm"](auto_cached, "cached", 1)

mock_workspaces, mock_versions, mock_runs, mock_answers = {}, {}, {}, {}
mock_sequence = 0
def mock_http(route, payload=None, workspace=None):
    global mock_sequence
    if route == "/api/sessions/create":
        mock_workspaces[payload["id"]] = []
        mock_answers[payload["id"]] = {}
        return {}
    if route == "/api/vqa/items":
        assert "id" not in payload
        mock_sequence += 1
        item = {key: value for key, value in payload.items() if key != "image"}
        item.update(id=f"{mock_sequence:032x}", revision=1,
            image_sha256=hashlib.sha256(base64.b64decode(payload["image"])).hexdigest())
        mock_workspaces[workspace].append(item)
        return item
    if route == "/api/vqa/versions":
        manifest = {"schema": "vqa-manifest-v1", "split": payload["split"],
            "items": sorted(mock_workspaces[workspace], key=lambda row: row["id"])}
        version = {"id": digest(manifest), "manifest": manifest}
        mock_versions.setdefault(workspace, {})[version["id"]] = version
        return version
    if route == "/api/vqa/runs":
        version = mock_versions[workspace][payload["version"]]
        results = []
        for item in version["manifest"]["items"]:
            key = answer_key(item, digest(model_manifest))
            cached = mock_answers[workspace].get(key)
            hit = cached is not None
            prediction = cached["prediction"] if hit else (item["choices"][0] if workspace.startswith("cache-warm-") else "A")
            row = {"id": item["id"], "prediction": prediction, "answer_key": key,
                "score_key": digest({"answer_key": key, "prediction": prediction, "annotation": item, "scorer": SCORER}),
                "score": score(item, prediction), "cache_hit": hit, "vision_forwards": 1,
                "generation_seconds": 2.0, "item_seconds": 2.1}
            results.append(row)
            if not hit:
                mock_answers[workspace][key] = row
        run_id = f"mock-run-{len(mock_runs) + 1}"
        run = {"id": run_id, "version": version["id"], "status": "completed", "total": len(results),
            "cache_hits": sum(row["cache_hit"] for row in results), "results": results,
            "model": MODEL, "revision": REVISION, "generation": GENERATION, "prompt_version": PROMPT,
            "scorer": SCORER, "model_manifest": model_manifest, "device": "cpu"}
        mock_runs[workspace, run_id] = (run, version)
        return {"id": run_id}
    if route == "/api/vqa/state":
        return {"runs": [run for (owner, _), (run, _) in mock_runs.items() if owner == workspace]}
    if route.endswith("/export"):
        run_id = route.split("/")[-2]
        run, version = mock_runs[workspace, run_id]
        image_hash = version["manifest"]["items"][0]["image_sha256"]
        return {"run": run, "version": version,
            "images": {image_hash: base64.b64encode(b"owned image").decode()}}
    raise AssertionError(f"Unexpected mocked HTTP route: {route}")

mock_clock = [0.0]
def mock_monotonic():
    mock_clock[0] += 0.01
    return mock_clock[0]
with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"VQA_MODEL_PATH": "mock", "VQA_MODEL_PYTHON": "mock-python"}), \
        patch("shutil.disk_usage", return_value=SimpleNamespace(free=11 * 1024 ** 3)), \
        patch("subprocess.check_output", side_effect=lambda *args, **kwargs: "mock-source"), \
        patch("time.monotonic", side_effect=mock_monotonic):
    mock_evidence = {}
    helpers["cache_impact"](mock_http, Path(directory), mock_evidence)
    receipt = json.loads((Path(directory) / "cache-impact.json").read_text())
    assert receipt["passed"] and len(receipt["pairs"]) == 5
    assert [pair["timed_order"] for pair in receipt["pairs"]] == [
        ["cold", "cached"], ["cached", "cold"], ["cold", "cached"], ["cached", "cold"], ["cold", "cached"]]
    assert all(pair["prepopulation"]["inference_items"] == 3 for pair in receipt["pairs"])
    assert all(pair["arms"]["cold"]["cache_hits"] == 0 and pair["arms"]["cold"]["inference_items"] == 3 for pair in receipt["pairs"])
    assert all(pair["arms"]["cached"]["cache_hits"] == 3 and pair["arms"]["cached"]["inference_items"] == 0 for pair in receipt["pairs"])
    assert receipt["summary"]["pairs"] == 5
    assert receipt["source_files_sha256"]["benchmark-vqa-journey.py"]
    assert receipt["source_files_sha256"]["verify-trialdesk.py"]
    assert receipt["model_manifest_sha256"]
    assert all(pair["independent_generation_agreement"] == {
        "items": 3, "prediction_matches": 0, "score_matches": 3} for pair in receipt["pairs"])
with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {
    "VQA_MODEL_PATH": "mock", "VQA_MODEL_PYTHON": "mock-python", "VQA_PROFILE_DIR": "profile-output"}), \
        patch("shutil.disk_usage") as disk_usage:
    guarded_evidence = {}
    try:
        helpers["cache_impact"](lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP must not start")), Path(directory), guarded_evidence)
    except RuntimeError as error:
        assert "VQA_PROFILE_DIR" in str(error)
    else:
        raise AssertionError("Cache-impact mode accepted profiling overhead")
    assert guarded_evidence == {}
    disk_usage.assert_not_called()
from data_formulator.vqa import prompt_item
item = {"image_sha256": "image", "question": "Question?", "context": "Hint", "choices": ["a", "b"], "answer_mode": "mcq", "answer": "b", "answer_label": 1, "lecture": "leak", "solution": "leak"}
assert set(prompt_item(item)) == {"image_sha256", "question", "context", "choices", "answer_mode"}
assert not set(prompt_item(item)) & {"answer", "answer_label", "lecture", "solution"}
runner = runpy.run_path(str(root / "py-src" / "data_formulator" / "vqa_runner.py"))
native = MagicMock()
native.__enter__.return_value = native
native.key_averages.return_value.table.return_value = "stub native operator table"
torch = SimpleNamespace(__version__="stub-torch", get_num_threads=Mock(return_value=4),
    get_num_interop_threads=Mock(return_value=1), __config__=SimpleNamespace(show=Mock(return_value="stub build")),
    profiler=SimpleNamespace(profile=Mock(return_value=native), ProfilerActivity=SimpleNamespace(CPU="CPU")))
with patch.dict("os.environ", {"VQA_PROFILE_DIR": ""}):
    with runner["cpu_profile_context"](torch, 0) as inactive:
        assert inactive is None
    torch.profiler.profile.assert_not_called()
    torch.__config__.show.assert_not_called()
with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"VQA_PROFILE_DIR": directory}):
    for index in range(10):
        with runner["cpu_profile_context"](torch, index) as profile:
            pass
        if profile is not None:
            runner["write_cpu_profile"](torch, profile, index, f"item-{index}")
    assert torch.profiler.profile.call_count == 8
    torch.profiler.profile.assert_called_with(activities=["CPU"], record_shapes=True, profile_memory=True, with_stack=False)
    native.key_averages.assert_called_with(group_by_input_shape=True)
    native.key_averages.return_value.table.assert_called_with(sort_by="self_cpu_time_total", row_limit=100)
    receipts = sorted(Path(directory).glob("*.txt"))
    assert len(receipts) == 8 and len(list(Path(directory).iterdir())) == 8
    metadata, table = receipts[0].read_text().split("\n\n", 1)
    metadata = json.loads(metadata)
    assert set(metadata) == {"item_index", "item_id", "torch_version", "threads", "interop_threads", "torch_build", "scope"}
    assert metadata["threads"] == 4 and metadata["interop_threads"] == 1
    assert metadata["torch_version"] == "stub-torch" and metadata["torch_build"] == "stub build"
    assert table.strip() == "stub native operator table"
    native.export_chrome_trace.assert_not_called()
with tempfile.TemporaryDirectory() as directory:
    import base64
    import io
    from contextlib import nullcontext
    from PIL import Image
    source = Path(directory)
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(image, "PNG")
    request = {"items": [{"id": "owned-item", "image": base64.b64encode(image.getvalue()).decode(),
        "question": "Question?", "context": "", "choices": ["left", "right"], "answer_mode": "mcq",
        "answer": "SECRET_GOLD", "lecture": "SECRET_LECTURE", "solution": "SECRET_SOLUTION"}],
        "generation": {"max_new_tokens": 32, "do_sample": False}}
    (source / "request.json").write_text(json.dumps(request))
    inputs = {"input_ids": SimpleNamespace(shape=(1, 2)), "pixel_values": SimpleNamespace(shape=(1, 1, 3, 2, 2))}
    generated = MagicMock()
    generated.shape = (1, 4)
    processor = MagicMock(return_value=inputs)
    processor.apply_chat_template.return_value = "stub chat template"
    processor.batch_decode.return_value = ["A"]
    model = Mock()
    model.to.return_value = model
    model.eval.return_value = model
    hooks, events = [], []
    model.model = SimpleNamespace(vision_model=SimpleNamespace(register_forward_hook=lambda hook: (hooks.append(hook), Mock())[1]))
    def generate(**kwargs):
        events.append("generate")
        hooks[-1]()
        return generated
    model.generate.side_effect = generate
    torch.set_num_threads = Mock()
    torch.float32 = "float32"
    torch.inference_mode = nullcontext
    transformers = SimpleNamespace(AutoProcessor=SimpleNamespace(from_pretrained=Mock(return_value=processor)),
        AutoModelForImageTextToText=SimpleNamespace(from_pretrained=Mock(return_value=model)))
    native.__enter__.side_effect = lambda: (events.append("profile-enter"), native)[1]
    native.__exit__.side_effect = lambda *_: events.append("profile-exit")
    for enabled in (False, True):
        events.clear()
        output = source / f"output-{enabled}.jsonl"
        with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}), \
             patch.dict("os.environ", {"VQA_PROFILE_DIR": str(source / "profiles") if enabled else ""}), \
             patch.object(sys, "argv", ["runner", str(source / "request.json"), str(source / "model"), str(output)]):
            runner["main"]()
        assert events == (["profile-enter", "generate", "profile-exit"] if enabled else ["generate"])
        assert model.generate.call_args.kwargs == {**inputs, **request["generation"]}
        assert json.loads(output.read_text())["vision_forwards"] == 1
    assert all(call.args == (4,) for call in torch.set_num_threads.call_args_list)
    messages = processor.apply_chat_template.call_args.args[0]
    assert not any(secret in json.dumps(messages) for secret in ("SECRET_GOLD", "SECRET_LECTURE", "SECRET_SOLUTION"))
    assert len(list((source / "profiles").glob("*.txt"))) == 1
with tempfile.TemporaryDirectory() as directory:
    source = Path(directory)
    target = source / "rescore.json"
    command = [sys.executable, str(root / "tools" / "rescore-vqa-validation.py"), str(source), str(target)]
    (source / "benchmark.json").write_text(json.dumps({"dataset": {"split": "test", "count": 128}}))
    assert subprocess.run(command, capture_output=True, timeout=10).returncode != 0
    assert not target.exists()
    target.write_text("preserve")
    assert subprocess.run(command, capture_output=True, timeout=10).returncode != 0
    assert target.read_text() == "preserve"
    target.unlink()
    (source / "export.json").write_text("{}")
    (source / "benchmark.json").write_text(json.dumps({"source": "fixture", "dataset": {"split": "validation", "count": 16},
        "repeats": [{"batches": [{"export": "export.json", "export_sha256": "wrong"}]}]}))
    assert subprocess.run(command, capture_output=True, timeout=10).returncode != 0
    assert not target.exists()
print("Benchmark statistics and no-gold model input checks passed")
