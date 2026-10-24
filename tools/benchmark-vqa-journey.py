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
import statistics
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


def cache_manifest_parity(manifest):
    return {
        "schema": manifest["schema"],
        "split": manifest["split"],
        "items": sorted((
            {key: value for key, value in item.items() if key != "id"}
            for item in manifest["items"]
        ), key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))),
    }


def cache_input_parity(exported):
    run = exported["run"]
    provenance = ("model", "revision", "generation", "prompt_version", "scorer", "model_manifest", "device")
    if any(not run.get(key) for key in provenance) or not run["model_manifest"].get("files"):
        raise ValueError("Run is missing pinned model provenance")
    if any(not isinstance(row.get("prediction"), str) or not row.get("answer_key") or not row.get("score_key") or not isinstance(row.get("score"), dict) or not isinstance(row["score"].get("correct"), bool) for row in run["results"]):
        raise ValueError("Run is missing a prediction or scoring result")
    return {"version": cache_manifest_parity(exported["version"]["manifest"]),
            "model": {key: run[key] for key in provenance}}


def cache_predictions(exported):
    return {
        row["answer_key"]: {"prediction": row["prediction"], "score": row["score"]}
        for row in exported["run"]["results"]
    }


def validate_cache_arm(exported, arm, expected_total):
    from data_formulator.vqa import answer_key, digest, score

    run = exported["run"]
    results = run.get("results", [])
    hits = sum(row.get("cache_hit") is True for row in results)
    misses = len(results) - hits
    if run.get("status") != "completed" or len(results) != expected_total or run.get("total") != expected_total:
        raise ValueError(f"{arm} run did not complete every item")
    if run.get("cache_hits") != hits:
        raise ValueError(f"{arm} cache hit count does not match per-item records")
    items = {item["id"]: item for item in exported["version"]["manifest"]["items"]}
    if len(items) != expected_total or len({row.get("answer_key") for row in results}) != expected_total:
        raise ValueError(f"{arm} run does not map one-to-one to the frozen questions")
    model_identity = digest(run["model_manifest"])
    for row in results:
        item = items.get(row.get("id"))
        if item is None or answer_key(item, model_identity) != row["answer_key"]:
            raise ValueError(f"{arm} answer key does not match its frozen question and model")
        expected_score_key = digest({"answer_key": row["answer_key"], "prediction": row["prediction"], "annotation": item, "scorer": run["scorer"]})
        if row["score_key"] != expected_score_key or row["score"] != score(item, row["prediction"]):
            raise ValueError(f"{arm} scorer result does not match its answer and annotation")
    if arm == "cold" and (hits != 0 or misses != expected_total or any(row.get("vision_forwards", 0) < 1 for row in results)):
        raise ValueError("Cold run must infer every item through the vision encoder")
    if arm == "cached" and (hits != expected_total or misses != 0):
        raise ValueError("Cached run must reuse every answer")
    return {"input": cache_input_parity(exported), "predictions": cache_predictions(exported)}


def cache_setup(api, workspace, base, questions):
    api("/api/sessions/create", {"id": workspace})
    for question in questions:
        api("/api/vqa/items", {**base, **question}, workspace=workspace)
    return api("/api/vqa/versions", {"split": "validation"}, workspace=workspace)


def cache_timing_summary(rows):
    def spread(values):
        return {"median": statistics.median(values), "min": min(values), "max": max(values)}

    cold = [row["arms"]["cold"] for row in rows]
    cached = [row["arms"]["cached"] for row in rows]
    return {
        "cold_terminal_seconds": spread([arm["terminal_seconds"] for arm in cold]),
        "cached_terminal_seconds": spread([arm["terminal_seconds"] for arm in cached]),
        "cold_request_seconds": spread([arm["request_seconds"] for arm in cold]),
        "cached_request_seconds": spread([arm["request_seconds"] for arm in cached]),
        "pairs": len(rows),
    }


def cache_impact(api, run, evidence):
    """Compare real uncached and workspace-cached runs through the production HTTP API."""
    from PIL import Image, ImageDraw
    if not os.environ.get("VQA_MODEL_PATH") or not os.environ.get("VQA_MODEL_PYTHON"):
        raise RuntimeError("Cache-impact benchmark requires explicit VQA_MODEL_PATH and VQA_MODEL_PYTHON. No model download or fallback is available.")
    if os.environ.get("VQA_PROFILE_DIR"):
        raise RuntimeError("Cache-impact timings cannot run with VQA_PROFILE_DIR enabled.")
    if shutil.disk_usage(run).free < 10 * 1024 ** 3:
        raise RuntimeError("10 GiB free disk reserve required")
    output = {
        "schema": 1, "passed": False, "mode": "real-model-cache-impact", "pairs": [],
        "environment": {"python": sys.version, "platform": platform.platform(), "cpu_count": os.cpu_count(), "processor": platform.processor(), "command": sys.argv},
        "source": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dirty": subprocess.check_output(["git", "status", "--porcelain"], text=True),
        "source_files_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__).resolve(), Path(__file__).with_name("verify-trialdesk.py"))
        },
        "timing": {"endpoint_request": "POST /api/vqa/runs admission call only", "terminal": "admission call through first terminal state poll", "poll_interval_seconds": 0.2},
        "cache_policy": "Each pair prepopulates a separate warm workspace before timed arms. Cold and cached arms use distinct otherwise-identical workspaces. Timed arm order alternates; the cold arm still starts with an empty workspace cache. Cold and prepopulation runs start a new model process; a fully cached run skips model process startup. The OS file cache is not flushed, so this measures the application answer cache and includes normal host cache effects.",
    }
    evidence["cache_impact_benchmark"] = output
    image = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((48, 48, 208, 208), fill="red")
    draw.text((64, 18), "RED SQUARE", fill="black")
    encoded_buffer = io.BytesIO()
    image.save(encoded_buffer, "PNG")
    encoded = base64.b64encode(encoded_buffer.getvalue()).decode()
    (run / "cache-impact-owned-red-square.png").write_bytes(encoded_buffer.getvalue())
    base = {"image": encoded, "context": "", "aliases": [], "split": "validation", "answer_mode": "mcq"}
    questions = [
        {"question": "What color is the square?", "choices": ["red", "blue"], "answer": "red", "answer_label": 0},
        {"question": "Which shape is shown?", "choices": ["square", "circle"], "answer": "square", "answer_label": 0},
        {"question": "What color is the background?", "choices": ["white", "black"], "answer": "white", "answer_label": 0},
    ]

    def checkpoint():
        target = run / "cache-impact-progress.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(output, indent=2), encoding="utf-8")
        temporary.replace(target)

    def complete(workspace, version, label, timed, arm):
        record = {"workspace": workspace, "timed": timed}
        try:
            before = time.monotonic()
            started = api("/api/vqa/runs", {"version": version["id"]}, workspace=workspace)
            admitted = time.monotonic()
            deadline = admitted + 650
            while time.monotonic() < deadline:
                state = api("/api/vqa/state", workspace=workspace)
                result = next(row for row in state["runs"] if row["id"] == started["id"])
                if result["status"] != "running":
                    observed = time.monotonic()
                    break
                time.sleep(0.2)
            else:
                api(f"/api/vqa/runs/{started['id']}/cancel", {}, workspace=workspace)
                raise TimeoutError("VQA cache-impact run exceeded the 650-second HTTP journey bound")
            exported = api(f"/api/vqa/runs/{started['id']}/export", workspace=workspace)
            name = f"cache-impact-{label}-{started['id']}.json"
            (run / name).write_text(json.dumps(exported, indent=2), encoding="utf-8")
            record.update({
                "run": started["id"], "status": result["status"], "cache_hits": result["cache_hits"],
                "inference_items": sum(not row["cache_hit"] for row in result["results"]),
                "vision_forwards": sum(row["vision_forwards"] for row in result["results"] if not row["cache_hit"]),
                "generation_seconds": [row["generation_seconds"] for row in result["results"] if not row["cache_hit"]],
                "item_seconds": [row["item_seconds"] for row in result["results"] if not row["cache_hit"]],
                "request_seconds": admitted - before, "terminal_seconds": observed - before,
                "terminal_observation_seconds_after_admission": observed - admitted,
                "export": name, "export_sha256": hashlib.sha256((run / name).read_bytes()).hexdigest(),
            })
            record["input_parity"] = cache_input_parity(exported)
            record["predictions"] = cache_predictions(exported)
            validate_cache_arm(exported, arm, len(questions))
            record["model_manifest_sha256"] = hashlib.sha256(json.dumps(result["model_manifest"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
        return record

    start = time.monotonic()
    try:
        for index in range(5):
            pair = {"pair": index + 1, "questions": [question["question"] for question in questions], "arms": {}}
            output["pairs"].append(pair)
            cold_workspace = f"cache-cold-{index + 1}"
            warm_workspace = f"cache-warm-{index + 1}"
            cold_version = cache_setup(api, cold_workspace, base, questions)
            warm_version = cache_setup(api, warm_workspace, base, questions)
            if cache_manifest_parity(cold_version["manifest"]) != cache_manifest_parity(warm_version["manifest"]):
                raise AssertionError("Paired workspaces must freeze identical manifests")
            prime = complete(warm_workspace, warm_version, f"prime-{index + 1}", False, "cold")
            pair["prepopulation"] = prime
            if prime.get("error") or prime.get("status") != "completed" or prime.get("cache_hits") != 0 or prime.get("inference_items") != len(questions):
                raise RuntimeError(f"Warm cache prepopulation did not run all model inferences: {prime}")
            if output.setdefault("model_manifest_sha256", prime["model_manifest_sha256"]) != prime["model_manifest_sha256"]:
                raise RuntimeError("Model manifest hash changed during the cache-impact campaign")
            order = ("cold", "cached") if index % 2 == 0 else ("cached", "cold")
            pair["timed_order"] = list(order)
            for arm in order:
                workspace, version = (cold_workspace, cold_version) if arm == "cold" else (warm_workspace, warm_version)
                pair["arms"][arm] = complete(workspace, version, f"{arm}-{index + 1}", True, arm)
                if pair["arms"][arm].get("error"):
                    checkpoint()
                    raise RuntimeError(pair["arms"][arm]["error"])
                if pair["arms"][arm]["model_manifest_sha256"] != output["model_manifest_sha256"]:
                    raise RuntimeError("Timed arm used a different model manifest")
            cold, prime, cached = pair["arms"]["cold"], pair["prepopulation"], pair["arms"]["cached"]
            if cold["input_parity"] != prime["input_parity"] or cold["input_parity"] != cached["input_parity"]:
                raise AssertionError(f"Input manifest or model provenance differs in pair {index + 1}")
            if cached["predictions"] != prime["predictions"]:
                raise AssertionError(f"Cached outputs differ from their own prepopulation in pair {index + 1}")
            keys = cold["predictions"].keys()
            pair["independent_generation_agreement"] = {
                "items": len(keys),
                "prediction_matches": sum(cold["predictions"][key]["prediction"] == prime["predictions"][key]["prediction"] for key in keys),
                "score_matches": sum(cold["predictions"][key]["score"] == prime["predictions"][key]["score"] for key in keys),
            }
            checkpoint()
        output["summary"] = cache_timing_summary(output["pairs"])
        output["elapsed_seconds"] = time.monotonic() - start
        output["passed"] = True
        (run / "cache-impact.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(json.dumps({"cache_impact_passed": True, "pairs": len(output["pairs"])}), flush=True)
    except Exception as error:
        output["error"] = f"{type(error).__name__}: {error}"
        output["elapsed_seconds"] = time.monotonic() - start
        checkpoint()
        raise


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
