"""Deterministic checks for benchmark statistics and generation trust boundary."""
from pathlib import Path
import runpy
import json
import subprocess
import sys
import tempfile

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
