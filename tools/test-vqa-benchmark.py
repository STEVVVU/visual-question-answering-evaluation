"""Deterministic checks for benchmark statistics and generation trust boundary."""
from pathlib import Path
import runpy
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

root = Path(__file__).resolve().parents[1]
helpers = runpy.run_path(str(root / "tools" / "benchmark-vqa-journey.py"))
assert helpers["quantiles"]([5, 1, 4, 2, 3]) == {"n": 5, "p50": 3, "p95": 5, "p99": 5, "min": 1, "max": 5}
assert helpers["quantiles"]([])["p99"] is None
assert helpers["wilson"](0, 128)[0] >= -1e-15
assert helpers["wilson"](128, 128)[1] <= 1 + 1e-15
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
