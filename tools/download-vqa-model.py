"""Explicit, bounded download; never called by application or verification."""
import hashlib
import json
from pathlib import Path
import urllib.request

MODEL = "HuggingFaceTB/SmolVLM-256M-Instruct"
REVISION = "7e3e67edbbed1bf9888184d9df282b700a323964"
FILES = ["README.md", "added_tokens.json", "chat_template.json", "config.json",
         "generation_config.json", "merges.txt", "model.safetensors",
         "preprocessor_config.json", "processor_config.json", "special_tokens_map.json",
         "tokenizer.json", "tokenizer_config.json", "vocab.json"]
root = Path(__file__).resolve().parents[1] / ".tooling" / "smolvlm"
root.mkdir(parents=True, exist_ok=True)
manifest = {"model": MODEL, "revision": REVISION, "license": "Apache-2.0", "files": []}
for name in FILES:
    url = f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}"
    target = root / name
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".download")
        size = 0
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > (600_000_000 if name.endswith("safetensors") else 20_000_000):
                    raise RuntimeError("Download exceeded bounded size")
                out.write(chunk)
        temporary.replace(target)
    with target.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if name == "model.safetensors" and digest != "74dea5904032e5ae99a2e0eef5179e6ac0f1dedc3ab0c7c2a5d4d387c843203e":
        raise RuntimeError("Pinned safetensors checksum mismatch")
    manifest["files"].append({"name": name, "bytes": target.stat().st_size, "sha256": digest, "url": url})
    print(name, target.stat().st_size, digest, flush=True)
(root / "download-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
