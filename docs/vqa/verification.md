# VQA verification and dependency notes

## Local evidence

Verification uses the actual Flask entry point and HTTP workspace routes. `.audit/verification/vqa-baseline-001/` retained the inherited table upload, exact readback, analysis, restart and owned cleanup before VQA product changes. `vqa-real-001` and `vqa-real-002` completed actual CPU generation through the pinned SmolVLM vision encoder. The second run also proved a fully scored rankable split and a changed-question answer-cache miss. Each keeps `result.json`, `vqa-export.json`, the generated owned PNG, server logs and isolated workspace files after cleanup. These ignored artifacts are local evidence, not redistributed model weights.

The final real-model driver journey additionally checks cancellation of a cached run and cancellation after a real partial prediction is durably stored while the model is still generating another item, separate workspace state and rejected foreign-workspace exports. The model-free CI journey expects a visible failed run and records `real_model: false`. It never substitutes mock predictions. Unit tests inject deterministic boundary outputs only for failure/cache cases, separate from real generation proof.

The retained shared-cache identity reproduction (`.audit/cache-identity-reproduction.log`) failed because one result ID was missing when two annotations shared an answer entry. The shared record boundary now assigns the current annotation ID after prediction metadata. Its regression proves both annotation IDs and independently correct/incorrect gold scores.

`.audit/cancel-partial-reproduction.log` retains failing controls for cancellation of an all-cache run and missing durable results while a child had already flushed output. Cancellation now applies before cache work and at atomic finalization; predictions are ingested incrementally while the child lives. Actual CUA then exposed a separate Windows cleanup failure: killing the virtualenv launcher alone left its Python descendant holding stderr. The configured real-model command reproduced the launcher/descendant PID pair and `WinError 32` in `.audit/verification/windows-cancel-repro-002/result.json`. The application stops its owned process tree before the temporary-directory context exits. This covers cancellation, timeout and exceptional ingestion exits without swallowing cleanup errors or adding sleeps/retries.

CPU pixel tensors and positive vision-forward counters establish that image generation ran. Raw answers and counts are observations on an owned synthetic square fixture. They do not establish generalization, subjective answer quality or a performance claim. The processor's pinned tiling settings are unchanged. No GPU or external judge was used.

## Dependencies

Pillow 13.0.0 is locked from the [official PyPI distribution](https://pypi.org/project/pillow/13.0.0/), whose installed metadata declares MIT-CMU. It supplies bounded raster decoding/re-encoding. The optional model process uses the [official torchvision 0.26.0 distribution](https://pypi.org/project/torchvision/0.26.0/) under BSD-3-Clause; the official compatibility table pairs torchvision 0.26 with PyTorch 2.11. This project used a local two-package overlay for Pillow and torchvision without modifying the shared Torch/Transformers interpreter or copying Torch.

The exact local installation command was:

```powershell
uv pip install --python .venv/Scripts/python.exe --target .tooling/vision --no-deps --index-url https://pypi.org/simple Pillow==13.0.0 torchvision==0.26.0
```

Set `VQA_VISION_PACKAGES` to the absolute `.tooling/vision` directory. Set `VQA_MODEL_PYTHON` to an operator-provisioned interpreter containing Torch 2.11, Transformers 5.20.2 and safetensors 0.8.0. This optional environment is not part of core CI and no installer runs during application execution.

`pip-audit 2.10.1` reported no known vulnerabilities for the two overlay packages on October 21, 2026. The complete core environment still reported inherited oauthlib 3.3.1 and thrift 0.22.0 findings (five feed entries); Yarn reported the inherited low KaTeX advisory through two dependency paths. The version constraints and exposure limits remain in [TRIALDESK.md](../../TRIALDESK.md). The new VQA path does not call those connectors or math rendering. No claim is made that the complete inherited app is safe for public deployment.

The [model manifest](model-manifest.json) records every downloaded file URL, exact revision, size and SHA-256. License attribution is supplied by the pinned official model card (`README.md`, Apache-2.0); the source repository lists no separate LICENSE file. The application validates those recorded hashes before use and loads only safetensors with local/offline settings and no trusted remote Python.

## Final process proof

`vqa-real-006` passed the full real HTTP journey after the Windows helper began waiting for captured owned descendant handles, using bounded [Process.WaitForExit](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.process.waitforexit) after [taskkill `/T`](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/taskkill). It observed a committed 1/2 partial result while the child was still generating, then cancelled and retained that partial result without locked files. Cached cancellation, gold/question invalidation, rankability, foreign-workspace rejection, export/restart replay and owned listener cleanup also passed. The final browser cancel `c50d917c` independently settled cancelled/unranked without WinError; its JSON export and screenshot remain in local orchestration evidence.

Final review additionally injected a cleanup timeout. The old implementation skipped terminal bookkeeping when its cleanup helper raised; the failing control is retained in `.audit/cleanup-failure-reproduction.log`. Finalization now saves a failed run with cleanup PID/error and `may_be_alive`, releases semaphore ownership and explicitly blocks new starts behind a cleanup-failure latch. It does not claim the process stopped or permit overlapping work. Confirm owned cleanup before restarting after such a failure. This failure-only delta is covered by deterministic regression; normal real generation/cancellation proof remains separate.

## Browser evidence

The primary design reference and permitted functional adaptations are in [the design notes](../../.agents/design-reference.md). Main-agent CUA review owns actual wide/narrow screenshots and interaction evidence. HTTP tests and builds are not substitutes for that review.
