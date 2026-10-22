"""Explicitly prepare pinned ScienceQA image-only validation/test subsets."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

OFFICIAL = "2cbf8318e07b9ece895bb2ae605e71e38d623264"
MIRROR = "f18b0a70359ebfb41f658fd564208d0355b013f4"
FILES = {
    "test": ("test-00000-of-00001-f0e719df791966ff.parquet", "235e92d1f30155266df76bc9f28fc6e4fcb6bec2c6a8c7d67f9086ea6b392a84"),
    "validation": ("validation-00000-of-00001-6c7328ff6c84284c.parquet", "e66b5215ce71e748ade4ee629bc14fbf86762071d355a4fcd831581cc04d72d8"),
}
ROOT = Path(__file__).resolve().parents[1]


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(root, name, url, maximum, expected=None):
    target = root / name
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".download")
        try:
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > maximum:
                        raise ValueError("Pinned data download exceeded size bound")
                    if shutil.disk_usage(root).free < 10 * 1024 ** 3:
                        raise ValueError("10 GiB disk reserve required")
                    output.write(chunk)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    actual = checksum(target)
    if expected and actual != expected:
        raise ValueError(f"Pinned data checksum mismatch for {name}")
    return target, {"name": name, "url": url, "sha256": actual, "bytes": target.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", required=True, help="Explicit network authorization, about250 MB; never invoked by app")
    args = parser.parse_args()
    assert args.download
    import pyarrow.parquet as parquet
    import sys
    sys.path.insert(0, str(ROOT / "py-src"))
    from data_formulator.vqa import annotation, image_png

    root = ROOT / ".tooling" / "scienceqa"
    root.mkdir(parents=True, exist_ok=True)
    provenance = {"official_revision": OFFICIAL, "mirror_revision": MIRROR, "license": "CC-BY-NC-SA-4.0", "files": []}
    official = f"https://raw.githubusercontent.com/lupantech/ScienceQA/{OFFICIAL}"
    sources = {}
    for name, remote in [("LICENSE-DATA", "LICENSE-DATA"), ("problems.json", "data/scienceqa/problems.json"), ("pid_splits.json", "data/scienceqa/pid_splits.json")]:
        target, entry = download(root, name, f"{official}/{remote}", 32_000_000)
        provenance["files"].append(entry)
        if name.endswith("json"):
            sources[name] = json.loads(target.read_text(encoding="utf-8"))
    assert "Attribution-NonCommercial-ShareAlike 4.0" in (root / "LICENSE-DATA").read_text(encoding="utf-8")
    problems, splits = sources["problems.json"], sources["pid_splits.json"]
    for split, count in [("validation", 16), ("test", 128)]:
        name, expected_sha = FILES[split]
        target, entry = download(root, name, f"https://huggingface.co/datasets/derek-thomas/ScienceQA/resolve/{MIRROR}/data/{name}", 140_000_000, expected_sha)
        provenance["files"].append(entry)
        split_ids = set(splits['val' if split == 'validation' else split])
        ordered_ids = [source_id for source_id in problems if source_id in split_ids]
        row_index = 0
        candidates, excluded = [], []
        for batch in parquet.ParquetFile(target).iter_batches(batch_size=64, columns=["image", "question", "choices", "answer", "hint", "subject", "topic", "grade"]):
            for row in batch.to_pylist():
                source_id = ordered_ids[row_index]
                row_index += 1
                official_item = problems[source_id]
                assert row["question"] == official_item["question"] and row["choices"] == official_item["choices"]
                assert row["answer"] == official_item["answer"] and row["hint"] == official_item["hint"]
                assert all(row[key] == official_item[key] for key in ("subject", "topic", "grade"))
                if not row["image"] or not official_item["image"]:
                    continue
                raw = row["image"]["bytes"]
                value = {"question": row["question"], "choices": row["choices"], "context": row["hint"], "answer": row["choices"][row["answer"]], "answer_label": row["answer"], "answer_mode": "mcq", "aliases": [], "split": split}
                try:
                    value = annotation(value)
                except ValueError as error:
                    excluded.append({"id": source_id, "reason": str(error)})
                    continue
                key = hashlib.sha256(f"scienceqa-benchmark-v1/{split}/{source_id}".encode()).hexdigest()
                candidates.append({"source_id": source_id, "selection_hash": key, "annotation": value, "raw": raw, "subject": row["subject"], "topic": row["topic"], "grade": row["grade"]})
        assert row_index == len(ordered_ids)
        assert len(candidates) >= count
        selected = []
        for row in sorted(candidates, key=lambda row: row["selection_hash"]):
            try:
                canonical = image_png(base64.b64encode(row.pop("raw")).decode())
            except ValueError as error:
                excluded.append({"id": row["source_id"], "reason": str(error)})
                continue
            selected.append({**row, "image": base64.b64encode(canonical).decode(), "image_sha256": hashlib.sha256(canonical).hexdigest()})
            if len(selected) == count:
                break
        assert len(selected) == count
        assert not set(row["source_id"] for row in selected) & set(splits["train"])
        result = {"schema": 1, "split": split, "selection": "sha256 scienceqa-benchmark-v1/split/source_id ascending, image-bearing supported inputs only", "eligible": len(candidates), "excluded": excluded, "count": count, "provenance": provenance, "items": selected}
        output = root / f"{split}-manifest.json"
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({"manifest": str(output), "sha256": checksum(output), "count": count, "eligible": len(candidates), "excluded": len(excluded)}), flush=True)


if __name__ == "__main__":
    main()
