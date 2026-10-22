"""Opt-in real Windows model-process ownership check; never downloads a model."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from PIL import Image
import pytest

from data_formulator.vqa_execution import model_configuration, stop_model


@pytest.mark.skipif(os.name != "nt" or not os.environ.get("VQA_MODEL_PATH"), reason="Requires explicit verified local Windows model configuration")
def test_real_model_descendants_release_output_before_scratch_cleanup(tmp_path):
    interpreter, model, _ = model_configuration()
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(image, "PNG")
    items = [{"id": str(i), "image": base64.b64encode(image.getvalue()).decode(), "question": question,
              "context": "", "choices": [], "answer_mode": "manual"}
             for i, question in enumerate(["Describe the image in detail.", "Explain the visible colors and composition."])]
    env = {**os.environ, "PYTHONPATH": os.environ.get("VQA_VISION_PACKAGES", ""),
           "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    with tempfile.TemporaryDirectory(dir=tmp_path, prefix="owned-model-") as directory:
        scratch = Path(directory)
        assert scratch.resolve().is_relative_to(tmp_path.resolve())
        source, output = scratch / "input.json", scratch / "output.jsonl"
        source.write_text(json.dumps({"generation": {"max_new_tokens": 32, "do_sample": False}, "items": items}))
        with (scratch / "stderr.txt").open("w") as log:
            process = subprocess.Popen([interpreter, str(Path(__file__).resolve().parents[2] / "py-src/data_formulator/vqa_runner.py"), str(source), str(model), str(output)],
                                       env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline = time.monotonic() + 120
                while not output.exists() or "\n" not in output.read_text(encoding="utf-8"):
                    assert process.poll() is None, "Real model exited before emitting a result"
                    assert time.monotonic() < deadline, "No real partial result within the test bound"
                    time.sleep(0.1)
                assert process.poll() is None, "The cancellation check requires a live model process"
                stop_model(process)
                assert process.poll() is not None
                assert json.loads(output.read_text(encoding="utf-8").splitlines()[0])["vision_forwards"] > 0
            finally:
                stop_model(process)
    assert not scratch.exists(), "All model descendant output handles must be closed before cleanup"
