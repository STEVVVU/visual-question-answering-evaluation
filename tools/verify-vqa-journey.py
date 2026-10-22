"""Real HTTP journey, loaded by the existing owned-process verification driver."""
import base64
import io
import json
import os
import time

from PIL import Image, ImageDraw


def journey(api, run, workspace_id, evidence):
    api("/api/sessions/save", {"id": workspace_id, "state": {"vqaVerification": True}})
    image = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((48, 48, 208, 208), fill="red")
    draw.text((64, 18), "RED SQUARE", fill="black")
    output = io.BytesIO()
    image.save(output, "PNG")
    encoded = base64.b64encode(output.getvalue()).decode()
    (run / "owned-red-square.png").write_bytes(output.getvalue())
    base = {"image": encoded, "context": "", "choices": [], "aliases": [], "split": "validation", "answer_label": None}
    exact = api("/api/vqa/items", {**base, "question": "What color is the square?", "answer": "red", "answer_mode": "exact"})
    api("/api/vqa/items", {**base, "question": "Which shape is shown?", "choices": ["square", "circle"], "answer": "square", "answer_label": 0, "answer_mode": "mcq"})
    api("/api/vqa/items", {**base, "question": "Describe this image.", "answer": "A red square on white.", "answer_mode": "manual"})
    first = api("/api/vqa/versions", {"split": "validation"})
    assert api("/api/vqa/versions", {"split": "validation"}) == first
    invalid = api("/api/vqa/items", {**base, "image": base64.b64encode(b"<svg/>").decode(), "question": "bad", "answer": "bad", "answer_mode": "exact"}, expected="error")
    assert invalid["code"] == "VALIDATION_ERROR"
    api("/api/vqa/items", {**exact, "answer": "scarlet", "aliases": ["red"]})
    second = api("/api/vqa/versions", {"split": "validation"})
    assert first["id"] != second["id"]
    assert first["manifest"]["items"] != second["manifest"]["items"]

    def evaluate(version, cancel=False):
        started = api("/api/vqa/runs", {"version": version["id"]})
        if cancel:
            if cancel == "after-progress":
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    live = next(r for r in api("/api/vqa/state")["runs"] if r["id"] == started["id"])
                    assert live["status"] == "running", "Cancellation proof requires a live partial run"
                    if live["metrics"]["completed"]:
                        evidence["vqaLiveBeforeCancel"] = {"run": live["id"], "completed": live["metrics"]["completed"], "status": live["status"]}
                        break
                    time.sleep(0.4)
                else:
                    raise RuntimeError("No emitted partial prediction before cancellation deadline")
            accepted = api(f"/api/vqa/runs/{started['id']}/cancel", {})
            assert accepted["status"] == "running" and accepted["cancel_requested"], "Run completed before the cancellation test could accept cancellation"
        deadline = time.monotonic() + 650
        while time.monotonic() < deadline:
            state = api("/api/vqa/state")
            result = next(r for r in state["runs"] if r["id"] == started["id"])
            if result["status"] == "running" and result["metrics"]["completed"]:
                progress = {"run": result["id"], "completed": result["metrics"]["completed"], "total": result["total"]}
                if progress not in evidence.setdefault("vqaProgress", []):
                    evidence["vqaProgress"].append(progress)
            if result["status"] != "running":
                return result
            time.sleep(0.4)
        raise RuntimeError("VQA run did not reach a terminal state")

    evaluated = evaluate(first)
    real_model = bool(os.environ.get("VQA_MODEL_PATH") and os.environ.get("VQA_MODEL_PYTHON"))
    if real_model:
        assert evaluated["status"] == "completed", evaluated
        assert evaluated["metrics"]["completed"] == 3
        assert evaluated["metrics"]["scored"] == 2 and evaluated["metrics"]["manual"] == 1
        assert all(r["vision_forwards"] > 0 and r["pixel_shape"] for r in evaluated["results"])
        assert not evaluated["rankable"]
        cached = evaluate(second)
        assert cached["status"] == "completed" and cached["cache_hits"] == 3
        old = {r["id"]: r for r in evaluated["results"]}
        new = {r["id"]: r for r in cached["results"]}
        assert old[exact["id"]]["answer_key"] == new[exact["id"]]["answer_key"]
        assert old[exact["id"]]["score_key"] != new[exact["id"]]["score_key"]
        cancelled_cache = evaluate(second, cancel=True)
        assert cancelled_cache["status"] == "cancelled" and not cancelled_cache["rankable"]
        evidence["vqaCachedCancellation"] = {"run": cancelled_cache["id"], "status": "cancelled", "rankable": False}
        evidence["vqaCacheInvalidation"] = {"answerReuseAfterGoldEdit": True, "scoreRecomputed": True}
        ranked_item = api("/api/vqa/items", {**base, "question": "What color is the square?", "answer": "red", "answer_mode": "exact", "split": "test"})
        ranked_version = api("/api/vqa/versions", {"split": "test"})
        ranked = evaluate(ranked_version)
        assert ranked["status"] == "completed" and ranked["rankable"] and ranked["cache_hits"] == 1
        changed = api("/api/vqa/items", {**ranked_item, "question": "Name the color of the central square."})
        changed_version = api("/api/vqa/versions", {"split": "test"})
        changed_run = evaluate(changed_version)
        assert changed_run["status"] == "completed" and changed_run["cache_hits"] == 0
        assert changed_run["results"][0]["answer_key"] != ranked["results"][0]["answer_key"]
        evidence["vqaRanked"] = {"run": ranked["id"], "version": ranked_version["id"], "rankable": True}
        evidence["vqaChangedQuestion"] = {"run": changed_run["id"], "version": changed_version["id"], "cache_hits": 0}
        api("/api/vqa/items", {**base, "question": "Explain all the geometry in this image.", "answer": "", "answer_mode": "manual", "split": "train"})
        api("/api/vqa/items", {**base, "question": "Describe the arrangement of objects and colors in detail.", "answer": "", "answer_mode": "manual", "split": "train"})
        cancelled = evaluate(api("/api/vqa/versions", {"split": "train"}), cancel="after-progress")
        assert cancelled["status"] == "cancelled" and not cancelled["rankable"]
        assert 0 < cancelled["metrics"]["completed"] < cancelled["total"]
        evidence["vqaCancelled"] = {"run": cancelled["id"], "status": cancelled["status"], "rankable": False}
    else:
        assert evaluated["status"] == "failed" and not evaluated["rankable"] and evaluated["error"]
    exported = api(f"/api/vqa/runs/{evaluated['id']}/export")
    assert exported["version"] == first and len(exported["images"]) == 1
    isolated_workspace = workspace_id + "-isolated"
    api("/api/sessions/create", {"id": isolated_workspace})
    isolated = api("/api/vqa/state", workspace=isolated_workspace)
    assert isolated == {"items": [], "versions": [], "runs": []}
    denied = api(f"/api/vqa/runs/{evaluated['id']}/export", expected="error", workspace=isolated_workspace)
    assert denied["code"] == "VALIDATION_ERROR"
    evidence["vqaIsolation"] = {"separateWorkspaceEmpty": True, "foreignExportRejected": True}
    (run / "vqa-export.json").write_text(json.dumps(exported, indent=2), encoding="utf-8")
    evidence["vqa"] = {"run": evaluated["id"], "status": evaluated["status"], "real_model": real_model,
                       "versions": [first["id"], second["id"]], "metrics": evaluated["metrics"],
                       "predictions": evaluated["results"], "export": "vqa-export.json"}
    print(f"VQA DRIVE: {evaluated['status']}; actual_model={real_model}; export retained", flush=True)
    return exported
