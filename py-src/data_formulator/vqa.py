"""Workspace-owned VQA manifests, answer/score caches and durable run state."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sqlite3
import time
import uuid
import warnings
from pathlib import Path

from PIL import Image

MODEL = "HuggingFaceTB/SmolVLM-256M-Instruct"
REVISION = "7e3e67edbbed1bf9888184d9df282b700a323964"
SCORER = "mode-exact-v3"
PROMPT = "image-question-choices-v1"
GENERATION = {"max_new_tokens": 32, "do_sample": False}
BOOT = uuid.uuid4().hex
MAX_ITEMS = 64


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def text(value, label, limit=2000, optional=False):
    if not isinstance(value, str) or len(value) > limit or (not optional and not value.strip()):
        raise ValueError(f"{label} must be {'optional ' if optional else ''}text, at most {limit} characters.")
    return value.strip()


def annotation(value):
    if not isinstance(value, dict):
        raise ValueError("Annotation must be an object.")
    mode = value.get("answer_mode")
    if mode not in ("mcq", "exact", "manual"):
        raise ValueError("Answer mode must be mcq, exact or manual.")
    choices = value.get("choices", [])
    aliases = value.get("aliases", [])
    if not isinstance(choices, list) or len(choices) > 10:
        raise ValueError("At most ten choices are allowed.")
    choices = [text(x, "Choice", 300) for x in choices]
    if len({normalize(x) for x in choices}) != len(choices):
        raise ValueError("Choices must be distinct after normalization.")
    if not isinstance(aliases, list) or len(aliases) > 10:
        raise ValueError("At most ten answer aliases are allowed.")
    aliases = [text(x, "Alias", 300) for x in aliases]
    answer = text(value.get("answer", ""), "Answer", 1000, mode == "manual")
    label = value.get("answer_label")
    if mode == "mcq":
        if len(choices) < 2 or isinstance(label, bool) or not isinstance(label, int) or not 0 <= label < len(choices):
            raise ValueError("MCQ requires 2–10 choices and a zero-based answer_label.")
        if answer != choices[label]:
            raise ValueError("MCQ answer must equal the selected choice.")
    else:
        label = None
    split = value.get("split", "validation")
    if split not in ("train", "validation", "test"):
        raise ValueError("Split must be train, validation or test.")
    return {"question": text(value.get("question"), "Question"),
            "context": text(value.get("context", ""), "Context", 4000, True),
            "choices": choices, "answer": answer, "answer_label": label,
            "answer_mode": mode, "aliases": aliases, "split": split}


def image_png(encoded):
    if not isinstance(encoded, str) or len(encoded) > 2_800_000:
        raise ValueError("Image must be base64 PNG/JPEG, at most 2 MB.")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 2_000_000:
            raise ValueError("Image exceeds 2 MB.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ("PNG", "JPEG") or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Only single-frame PNG/JPEG images are accepted.")
                if not 1 <= image.width <= 2048 or not 1 <= image.height <= 2048 or image.width * image.height > 2_000_000:
                    raise ValueError("Image dimensions exceed 2048 or two million pixels.")
                image.load()
                out = io.BytesIO()
                image.convert("RGB").save(out, format="PNG")
                return out.getvalue()
    except (OSError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise ValueError("Image cannot be safely decoded as PNG/JPEG.") from error


def normalize(value):
    return " ".join(value.casefold().strip().rstrip(".!?").split())


def prompt_item(item):
    return {key: item[key] for key in ("image_sha256", "question", "context", "choices", "answer_mode")}


def answer_key(item, model_manifest):
    return digest({"input": prompt_item(item), "model": MODEL, "revision": REVISION,
                   "files": model_manifest, "generation": GENERATION, "prompt": PROMPT})


def score(item, prediction):
    if item["answer_mode"] == "manual":
        return {"status": "manual_required", "correct": None}
    predicted = normalize(prediction)
    if item["answer_mode"] == "mcq":
        label = re.fullmatch(r"(?:Answer:\s*)?\(?([A-J])\)?[.)]?", prediction.strip(), re.IGNORECASE)
        selected = ord(label[1].upper()) - ord("A") if label else None
        labeled_choice = re.fullmatch(r"([A-Ja-j])[.)]\s*(.+)", prediction.strip())
        if labeled_choice:
            index = ord(labeled_choice[1].upper()) - ord("A")
            if index < len(item["choices"]) and normalize(labeled_choice[2]) == normalize(item["choices"][index]):
                selected = index
        if selected is None:
            matches = [i for i, choice in enumerate(item["choices"]) if normalize(choice) == predicted]
            selected = matches[0] if len(matches) == 1 else None
        return {"status": "scored", "correct": selected == item["answer_label"]}
    return {"status": "scored", "correct": predicted in {normalize(x) for x in [item["answer"], *item["aliases"]]}}


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "vqa.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS images (id TEXT PRIMARY KEY, data BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS answers (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scores (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def get(self, table, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError("Invalid object identifier.")
        with self.connect() as db:
            row = db.execute(f"SELECT body FROM {table} WHERE id=?", (key,)).fetchone()
        if not row:
            raise ValueError(f"Unknown {table} identifier.")
        return json.loads(row[0])

    def all(self, table):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(f"SELECT body FROM {table} ORDER BY rowid DESC")]

    def put(self, table, key, value, immutable=False):
        limits = {"items": 64, "versions": 128, "runs": 256, "answers": 4096, "scores": 8192}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if table == "runs":
                previous = db.execute("SELECT body FROM runs WHERE id=?", (key,)).fetchone()
                if previous and json.loads(previous[0]).get("cancel_requested"):
                    value["cancel_requested"] = True
                    if value["status"] == "completed":
                        value.update(status="cancelled", error="Cancelled by user.", rankable=False)
            if db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] >= limits[table] and not db.execute(f"SELECT 1 FROM {table} WHERE id=?", (key,)).fetchone():
                raise ValueError(f"Workspace {table} limit reached.")
            clause = "OR IGNORE" if immutable else "OR REPLACE"
            db.execute(f"INSERT {clause} INTO {table} VALUES (?,?)", (key, canonical(value)))

    def cancel(self, run_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise ValueError("Unknown run identifier.")
            run = json.loads(row[0])
            if run["status"] == "running":
                run["cancel_requested"] = True
                db.execute("UPDATE runs SET body=? WHERE id=?", (canonical(run), run_id))
            return run

    def save_item(self, value):
        item = annotation(value)
        item_id = value.get("id") or uuid.uuid4().hex
        if not isinstance(item_id, str) or not re.fullmatch("[a-f0-9]{32}", item_id):
            raise ValueError("Invalid item identifier.")
        previous = self.get("items", item_id) if value.get("id") else None
        if value.get("image"):
            raw = image_png(value["image"])
            image_id = hashlib.sha256(raw).hexdigest()
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                total = db.execute("SELECT COALESCE(SUM(length(data)),0) FROM images").fetchone()[0]
                if total + len(raw) > 128_000_000:
                    raise ValueError("Workspace image storage limit reached.")
                db.execute("INSERT OR IGNORE INTO images VALUES (?,?)", (image_id, raw))
        elif previous:
            image_id = previous["image_sha256"]
        else:
            raise ValueError("An image is required.")
        item.update(id=item_id, image_sha256=image_id, revision=(previous["revision"] + 1 if previous else 1))
        self.put("items", item_id, item)
        return item

    def image(self, image_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM images WHERE id=?", (image_id,)).fetchone()
        if not row:
            raise ValueError("Unknown image identifier.")
        return row[0]

    def freeze(self, split):
        if split not in ("train", "validation", "test"):
            raise ValueError("Invalid split.")
        items = sorted([x for x in self.all("items") if x["split"] == split], key=lambda x: x["id"])
        if not items:
            raise ValueError("Annotate at least one item in this split first.")
        manifest = {"schema": "vqa-manifest-v1", "split": split, "items": items}
        version = {"id": digest(manifest), "manifest": manifest}
        self.put("versions", version["id"], version, immutable=True)
        return version

    def runs(self):
        runs = self.all("runs")
        for run in runs:
            if run["status"] == "running" and run["boot"] != BOOT:
                run.update(status="failed", error="Application restarted before completion; partial results retained.", finished=time.time())
                self.put("runs", run["id"], run)
        return runs

    def export(self, run_id):
        run = next((r for r in self.runs() if r["id"] == run_id), None)
        if run is None:
            raise ValueError("Unknown run identifier.")
        version = self.get("versions", run["version"])
        return {"schema": "vqa-export-v1", "run": run, "version": version,
                "images": {i["image_sha256"]: base64.b64encode(self.image(i["image_sha256"])).decode()
                           for i in version["manifest"]["items"]}}


def summarize(run):
    scored = [r for r in run["results"] if r["score"]["correct"] is not None]
    run["metrics"] = {"total": run["total"], "completed": len(run["results"]), "scored": len(scored),
                      "correct": sum(r["score"]["correct"] for r in scored),
                      "manual": sum(r["score"]["correct"] is None for r in run["results"]),
                      "accuracy": sum(r["score"]["correct"] for r in scored) / len(scored) if scored else None}
    run["rankable"] = run["status"] == "completed" and len(scored) == run["total"]
    return run
