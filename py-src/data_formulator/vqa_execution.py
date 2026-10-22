"""Single bounded local evaluator, with durable partial results and cancellation."""
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from data_formulator.vqa import (
    BOOT, GENERATION, MODEL, PROMPT, REVISION, SCORER,
    answer_key, digest, prompt_item, score, summarize,
)

SLOT = threading.BoundedSemaphore(1)
CLEANUP_FAILURE = None


def stop_model(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Windows venv launchers have descendants whose file handles outlive the launcher.
        script = f"""
        $ErrorActionPreference = 'Stop'
        $modelRootPid = {process.pid}
        $inventory = @(Get-CimInstance Win32_Process)
        $ownedIds = [System.Collections.Generic.List[int]]::new()
        $ownedIds.Add($modelRootPid)
        for ($index = 0; $index -lt $ownedIds.Count; $index++) {{
            foreach ($entry in $inventory) {{
                if ($entry.ParentProcessId -eq $ownedIds[$index] -and -not $ownedIds.Contains([int]$entry.ProcessId)) {{
                    $ownedIds.Add([int]$entry.ProcessId)
                }}
            }}
        }}
        $tracked = @()
        foreach ($ownedId in $ownedIds) {{
            try {{
                $ownedProcess = [System.Diagnostics.Process]::GetProcessById($ownedId)
                $null = $ownedProcess.Handle
                $tracked += $ownedProcess
            }} catch [System.ArgumentException] {{
                # This enumerated process already exited before its handle was opened.
            }}
        }}
        try {{
            if (-not $tracked.Count) {{ return }}
            & taskkill /PID $modelRootPid /T /F
            if ($LASTEXITCODE -ne 0) {{ throw 'Owned model process-tree termination failed.' }}
            foreach ($ownedProcess in $tracked) {{
                if (-not $ownedProcess.WaitForExit(10000)) {{ throw 'Owned model descendant did not exit.' }}
            }}
        }} finally {{
            foreach ($ownedProcess in $tracked) {{ $ownedProcess.Dispose() }}
        }}
        """
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       check=True, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=10)


def model_configuration():
    interpreter = os.environ.get("VQA_MODEL_PYTHON")
    model_path = os.environ.get("VQA_MODEL_PATH")
    if not interpreter or not model_path:
        raise ValueError("Configure VQA_MODEL_PYTHON and VQA_MODEL_PATH to a verified local pinned model. No automatic download or fallback is available.")
    root = Path(model_path).resolve()
    manifest = json.loads((root / "download-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("model") != MODEL or manifest.get("revision") != REVISION or manifest.get("license") != "Apache-2.0":
        raise ValueError("Local model manifest does not match the pinned model revision/license.")
    required = {"model.safetensors", "config.json", "tokenizer.json", "preprocessor_config.json", "processor_config.json"}
    names = {entry["name"] for entry in manifest["files"]}
    if not required <= names or len(names) != len(manifest["files"]):
        raise ValueError("Incomplete local model manifest.")
    for entry in manifest["files"]:
        name = entry["name"]
        if Path(name).name != name or name.endswith((".py", ".bin", ".pkl")):
            raise ValueError("Unsafe model manifest file.")
        path = root / name
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if path.stat().st_size != entry["bytes"] or actual != entry["sha256"]:
            raise ValueError(f"Local model checksum mismatch: {name}.")
    return interpreter, root, manifest


def start(store, version_id):
    version = store.get("versions", version_id)
    if not SLOT.acquire(blocking=False):
        raise ValueError("The local evaluator is busy. Wait for or cancel its active run; no unbounded queue is accepted.")
    run = {"id": uuid.uuid4().hex, "version": version_id, "status": "running", "boot": BOOT,
           "created": time.time(), "model": MODEL, "revision": REVISION,
           "generation": GENERATION, "prompt_version": PROMPT, "scorer": SCORER,
           "total": len(version["manifest"]["items"]), "results": [], "error": None,
           "cancel_requested": False, "cache_hits": 0, "device": "cpu"}
    try:
        if CLEANUP_FAILURE:
            raise ValueError("The evaluator is blocked after an owned-process cleanup failure: " + CLEANUP_FAILURE)
        store.put("runs", run["id"], summarize(run))
        threading.Thread(target=execute, args=(store, run, version), daemon=True).start()
    except BaseException:
        SLOT.release()
        raise
    return run


def execute(store, run, version):
    global CLEANUP_FAILURE
    process = None

    def cancelled():
        if store.get("runs", run["id"])["cancel_requested"]:
            run.update(status="cancelled", error="Cancelled by user.", cancel_requested=True)
            return True
        return False

    try:
        if cancelled():
            return
        interpreter, model_path, manifest = model_configuration()
        run["model_manifest"] = manifest
        identity = digest(manifest)
        missing = []
        items = {item["id"]: item for item in version["manifest"]["items"]}

        def record(item, prediction, key, hit):
            scoring_key = digest({"answer_key": key, "prediction": prediction["prediction"],
                                  "annotation": item, "scorer": SCORER})
            try:
                result_score = store.get("scores", scoring_key)
            except ValueError:
                result_score = score(item, prediction["prediction"])
                store.put("scores", scoring_key, result_score, immutable=True)
            run["results"].append({**prediction, "id": item["id"], "answer_key": key, "score_key": scoring_key,
                                   "cache_hit": hit, "score": result_score})
            run["cache_hits"] += int(hit)
            store.put("runs", run["id"], summarize(run))

        for item in items.values():
            if cancelled():
                return
            key = answer_key(item, identity)
            try:
                cached = store.get("answers", key)
            except ValueError:
                missing.append({"id": item["id"], **prompt_item(item),
                                "image": base64.b64encode(store.image(item["image_sha256"])).decode()})
            else:
                record(item, cached, key, True)
        with tempfile.TemporaryDirectory(prefix="vqa-") as directory:
            temp = Path(directory)
            source, output, errors = temp / "input.json", temp / "output.jsonl", temp / "stderr.txt"
            source.write_text(json.dumps({"items": missing, "generation": GENERATION}), encoding="utf-8")
            if missing:
                if cancelled():
                    return
                offset, pending = 0, ""
                received = set()

                def ingest():
                    nonlocal offset, pending
                    if not output.exists():
                        return
                    with output.open("r", encoding="utf-8") as stream:
                        stream.seek(offset)
                        while chunk := stream.read(65_536):
                            pending += chunk
                            offset = stream.tell()
                            lines = pending.split("\n")
                            pending = lines.pop()
                            if len(pending) > 32_768:
                                raise ValueError("Model output line exceeds its bound.")
                            for line in lines:
                                prediction = json.loads(line)
                                item = items[prediction["id"]]
                                if item["id"] in received or not isinstance(prediction["prediction"], str) or len(prediction["prediction"]) > 4000:
                                    raise ValueError("Invalid bounded model prediction.")
                                received.add(item["id"])
                                key = answer_key(item, identity)
                                store.put("answers", key, prediction, immutable=True)
                                record(item, prediction, key, False)

                env = {k: v for k, v in os.environ.items() if not k.endswith(("API_KEY", "API_TOKEN"))}
                env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
                overlay = os.environ.get("VQA_VISION_PACKAGES")
                if overlay:
                    env["PYTHONPATH"] = overlay
                try:
                    with errors.open("w", encoding="utf-8") as error_file:
                        process = subprocess.Popen([interpreter, str(Path(__file__).with_name("vqa_runner.py")),
                                                    str(source), str(model_path), str(output)],
                                                   stdout=error_file, stderr=error_file, env=env,
                                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                                   start_new_session=os.name != "nt")
                        deadline = time.monotonic() + 600
                        while process.poll() is None:
                            ingest()
                            if cancelled() or time.monotonic() > deadline:
                                run["status"] = "cancelled" if time.monotonic() <= deadline else "failed"
                                run["error"] = "Cancelled by user." if run["status"] == "cancelled" else "CPU generation exceeded the 600 second bound."
                                break
                            time.sleep(0.2)
                finally:
                    if process is not None:
                        try:
                            stop_model(process)
                        except Exception as error:
                            CLEANUP_FAILURE = f"run {run['id']}, owned PID {process.pid}: {type(error).__name__}: {error}"
                            run["cleanup"] = {"status": "failed", "process_id": process.pid,
                                              "may_be_alive": True, "error": str(error)}
                            raise
                ingest()
                if process.returncode and run["status"] == "running":
                    run.update(status="failed", error="Local model process failed: " + errors.read_text(encoding="utf-8")[-3000:])
        if run["status"] == "running":
            if not cancelled():
                run["status"] = "completed" if len(run["results"]) == run["total"] else "failed"
                if run["status"] == "failed":
                    run["error"] = "Local model exited without producing every requested prediction."
    except Exception as error:
        run.update(status="failed", error=f"{type(error).__name__}: {error}")
    finally:
        run["finished"] = time.time()
        try:
            store.put("runs", run["id"], summarize(run))
        finally:
            SLOT.release()
