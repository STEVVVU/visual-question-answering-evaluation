"""Frozen held-out evaluation through the real owned Flask verification lifecycle."""
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import time


def quantiles(values):
    ordered = sorted(values)
    def at(p):
        return ordered[max(0, math.ceil(p * len(ordered)) - 1)] if ordered else None
    return {"n": len(ordered), "p50": at(.5), "p95": at(.95), "p99": at(.99), "min": ordered[0] if ordered else None, "max": ordered[-1] if ordered else None}


def wilson(correct, total):
    z = 1.959963984540054
    center = (correct / total + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(correct / total * (1 - correct / total) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [center - half, center + half]


def benchmark(api, run, workspace_id, evidence, manifest_path):
    from PIL import Image
    from data_formulator.vqa import score
    smoke = manifest_path == "smoke"
    profile = bool(os.environ.get("VQA_PROFILE_DIR"))
    if shutil.disk_usage(run).free < 10 * 1024 ** 3:
        raise RuntimeError("10 GiB free disk reserve required")
    output = {"schema": 1, "passed": False, "smoke": smoke, "profile": profile, "repeats": [], "environment": {"python": sys.version, "platform": platform.platform(), "cpu_count": os.cpu_count(), "processor": platform.processor(), "command": sys.argv}, "source": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(), "dirty": subprocess.check_output(["git", "status", "--porcelain"], text=True)}
    evidence["benchmark"] = output
    if smoke:
        image = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(image, "PNG")
        items = [{"source_id": "owned-smoke", "image": base64.b64encode(image.getvalue()).decode(), "annotation": {"question": "Which color?", "choices": ["white", "black"], "answer": "white", "answer_label": 0, "answer_mode": "mcq", "context": "", "aliases": [], "split": "validation"}}]
        output["dataset"] = "owned synthetic lifecycle only; no quality or inference claim"
    else:
        source = Path(manifest_path).resolve()
        raw = source.read_bytes()
        manifest = json.loads(raw)
        assert manifest["schema"] == 1 and manifest["split"] in ("validation", "test")
        assert manifest["count"] == len(manifest["items"]) == (128 if manifest["split"] == "test" else 16)
        assert manifest["provenance"]["official_revision"] == "2cbf8318e07b9ece895bb2ae605e71e38d623264"
        assert manifest["provenance"]["mirror_revision"] == "f18b0a70359ebfb41f658fd564208d0355b013f4"
        assert manifest["provenance"]["license"] == "CC-BY-NC-SA-4.0"
        items = manifest["items"]
        assert len({row["source_id"] for row in items}) == len(items)
        output["dataset"] = {"manifest_sha256": hashlib.sha256(raw).hexdigest(), "split": manifest["split"], "count": len(items), "source_ids": [row["source_id"] for row in items], "provenance": manifest["provenance"], "eligible": manifest["eligible"], "excluded": manifest["excluded"]}
        shutil.copyfile(source.parent / "LICENSE-DATA", run / "ScienceQA-LICENSE-DATA")
        output["subjects"] = {subject: sum(row["subject"] == subject for row in items) for subject in sorted({row["subject"] for row in items})}
        output["uniform_choice_baselines"] = []
        for seed in range(5):
            rng = random.Random(seed)
            correct = sum(rng.randrange(len(row["annotation"]["choices"])) == row["annotation"]["answer_label"] for row in items)
            output["uniform_choice_baselines"].append({"seed": seed, "correct": correct, "total": len(items), "accuracy": correct / len(items)})
        output["uniform_choice_expectation"] = sum(1 / len(row["annotation"]["choices"]) for row in items) / len(items)
    start = time.monotonic()
    if profile:
        assert smoke or manifest["split"] == "validation", "Profiles must not use final held-out test"
        items = items[:8]
    for repeat in range(1 if smoke or profile else 5):
        repetition = {"repeat": repeat, "batches": [], "correct": 0, "completed": 0, "failed": 0, "rejected": 0, "timed_out": 0}
        output["repeats"].append(repetition)
        ordered = items if repeat % 2 == 0 else list(reversed(items))
        for offset in range(0, len(ordered), 8):
            if time.monotonic() - start > 5 * 3600:
                raise TimeoutError("Five-hour VQA benchmark ceiling exceeded")
            workspace = f"benchmark-{repeat}-{offset}"
            api("/api/sessions/create", {"id": workspace})
            batch = ordered[offset:offset + 8]
            ids = {}
            for row in batch:
                if not smoke:
                    assert hashlib.sha256(base64.b64decode(row["image"], validate=True)).hexdigest() == row["image_sha256"]
                saved = api("/api/vqa/items", {**row["annotation"], "image": row["image"]}, workspace=workspace)
                ids[saved["id"]] = row["source_id"]
            version = api("/api/vqa/versions", {"split": batch[0]["annotation"]["split"]}, workspace=workspace)
            at = time.monotonic()
            evaluation = api("/api/vqa/runs", {"version": version["id"]}, workspace=workspace)
            record = {"workspace": workspace, "version": version["id"], "run": evaluation["id"], "source_ids": ids, "offered": len(batch)}
            repetition["batches"].append(record)
            if not smoke:
                rejected = api("/api/vqa/runs", {"version": version["id"]}, workspace=workspace, expected="error")
                assert "busy" in rejected["message"].lower()
                record["run_admission"] = {"offered": 2, "accepted": 1, "rejected": 1, "reason": rejected["message"]}
            deadline = time.monotonic() + 650
            while time.monotonic() < deadline:
                state = api("/api/vqa/state", workspace=workspace)
                result = next(row for row in state["runs"] if row["id"] == evaluation["id"])
                if result["status"] != "running":
                    break
                time.sleep(.2)
            else:
                repetition["timed_out"] += len(batch)
                api(f"/api/vqa/runs/{evaluation['id']}/cancel", {}, workspace=workspace)
                raise TimeoutError("Real model run did not finish inside650seconds")
            record.update(end_to_end_seconds=time.monotonic() - at, status=result["status"], completed=result["metrics"]["completed"], cache_hits=result["cache_hits"], error=result["error"])
            exported = api(f"/api/vqa/runs/{evaluation['id']}/export", workspace=workspace)
            export_path = run / f"benchmark-{repeat}-{offset}-export.json"
            export_path.write_text(json.dumps(exported, indent=2), encoding="utf-8")
            record["export_sha256"] = hashlib.sha256(export_path.read_bytes()).hexdigest()
            record["export"] = export_path.name
            if smoke:
                assert result["status"] == "failed" and result["error"] and result["metrics"]["completed"] == 0
                record["expected_no_model_failure"] = True
                repetition["failed"] += len(batch)
                continue
            repetition["completed"] += result["metrics"]["completed"]
            repetition["failed"] += len(batch) - result["metrics"]["completed"]
            assert result["status"] == "completed" and result["rankable"], result
            assert result["cache_hits"] == 0 and len(result["results"]) == len(batch), "Fresh workspace must execute every image"
            assert all(row["vision_forwards"] > 0 and row["pixel_shape"] and row["generation_seconds"] > 0 for row in result["results"])
            record["predictions"] = result["results"]
            annotations = {row["id"]: row for row in version["manifest"]["items"]}
            record["unrecognized_answers"] = sum(not any(score({**annotations[row["id"]], "answer_label": label}, row["prediction"])["correct"] for label in range(len(annotations[row["id"]]["choices"]))) for row in result["results"])
            repetition["correct"] += sum(row["score"]["correct"] for row in result["results"])
        if not smoke:
            repetition["accuracy"] = repetition["correct"] / len(items)
            repetition["wilson_95"] = wilson(repetition["correct"], len(items))
            predictions = [row for batch in repetition["batches"] for row in batch["predictions"]]
            repetition["generation_seconds"] = quantiles([row["generation_seconds"] for row in predictions])
            repetition["item_seconds"] = quantiles([row["item_seconds"] for row in predictions])
            repetition["end_to_end_seconds"] = sum(batch["end_to_end_seconds"] for batch in repetition["batches"])
            repetition["items_per_second"] = len(items) / repetition["end_to_end_seconds"]
            repetition["peak_rss_kib"] = max(row.get("peak_rss_kib", 0) for row in predictions)
            repetition["tokens_per_generation_second"] = sum(row["generated_tokens"] for row in predictions) / sum(row["generation_seconds"] for row in predictions)
        (run / "benchmark-progress.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    output["elapsed_seconds"] = time.monotonic() - start
    output["passed"] = True
    (run / "benchmark.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"benchmark_passed": True, "smoke": smoke, "repeats": len(output["repeats"])}), flush=True)
