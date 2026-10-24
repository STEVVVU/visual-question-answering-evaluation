"""Check retained cache-workflow receipts without running a model."""
import hashlib
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/cache-impact-20261024"
helpers = runpy.run_path(str(ROOT / "tools/benchmark-vqa-journey.py"))
hashes = json.loads((EVIDENCE / "sha256.json").read_text(encoding="utf-8"))
for name, digest in hashes.items():
    assert hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest() == digest, name
raw_identities = json.loads((EVIDENCE / "raw-sha256.json").read_text(encoding="utf-8"))
for name, identity in raw_identities.items():
    data = (EVIDENCE / name).read_bytes()
    if identity["line_endings"] == "crlf":
        data = data.replace(b"\n", b"\r\n")
    assert hashlib.sha256(data).hexdigest() == identity["sha256"], name
result = json.loads((EVIDENCE / "cache-impact.json").read_text(encoding="utf-8"))
assert result["passed"] and len(result["pairs"]) == 5
assert helpers["cache_timing_summary"](result["pairs"]) == result["summary"]
sources = json.loads((EVIDENCE / "source-identity.json").read_text(encoding="utf-8"))
for name, identity in sources.items():
    content = (ROOT / "tools" / name).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(content).hexdigest() == identity["canonical_lf_sha256"], name
    if identity["native_line_endings"] == "crlf":
        content = content.replace(b"\n", b"\r\n")
    assert hashlib.sha256(content).hexdigest() == identity["native_sha256"] == result["source_files_sha256"][name]
inferences = cached_items = 0
for index, pair in enumerate(result["pairs"]):
    assert pair["pair"] == index + 1
    assert pair["timed_order"] == (["cold", "cached"] if index % 2 == 0 else ["cached", "cold"])
    exports = {}
    for name, descriptor, arm in (
        ("prime", pair["prepopulation"], "cold"),
        ("cold", pair["arms"]["cold"], "cold"),
        ("cached", pair["arms"]["cached"], "cached"),
    ):
        assert "error" not in descriptor
        filename = descriptor["export"]
        assert raw_identities[filename]["sha256"] == descriptor["export_sha256"]
        exported = json.loads((EVIDENCE / filename).read_text(encoding="utf-8"))
        checked = helpers["validate_cache_arm"](exported, arm, 3)
        assert checked["input"] == descriptor["input_parity"]
        assert checked["predictions"] == descriptor["predictions"]
        exports[name] = checked
        inferences += descriptor["inference_items"]
        cached_items += descriptor["cache_hits"]
    assert exports["prime"]["predictions"] == exports["cached"]["predictions"]
    assert exports["prime"]["input"] == exports["cold"]["input"] == exports["cached"]["input"]
assert inferences == 30 and cached_items == 15
cleanup = json.loads((EVIDENCE / "result.json").read_text(encoding="utf-8"))["cleanup"]
assert cleanup["listenerStopped"]
print("Retained five-pair cache receipts verified. No model inference ran in this check.")
