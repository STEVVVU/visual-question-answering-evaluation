---
name: verify-vqa
description: Drive the VQA evaluation Flask application through real HTTP with isolated durable workspaces, immutable manifests, runs and exported evidence.
---

# Verify VQA

## Launch

Install `uv sync --frozen` and `yarn install --frozen-lockfile`, then `yarn build`. Run `.venv/Scripts/python.exe tools/verify-vqa.py <unique-run-name> --ci --port 8432`. Reuses the maintained Trialdesk launcher and its isolated local home, production entry point, owned process cleanup and occupied-port refusal. No credentials or model download occurs. Run without `--ci` for interactive commands. Before VQA implementation, prove the existing baseline driver with `tools/verify-trialdesk.py`.

## Doctor

The driver checks `/api/app-config`, the owned process, durable local workspace backend, disabled connectors and isolated home. Read retained `server.log` on failure.

## Drive

The HTTP journey creates a workspace, uploads an owned generated PNG and question, freezes a version, evaluates using the configured local model, polls terminal state, exports the full artifact, restarts and compares persisted manifests/results. It checks annotation invalidation and isolation. Explicit model configuration is required for real generation; failure remains visible, never replaced with fixture predictions. Unit tests use injected deterministic adapters separately.

Real-model cancellation waits for a prediction committed while another question is still generating, then verifies cancelled/unranked partial results and scratch cleanup. On Windows also run `.venv/Scripts/python.exe -m pytest tests/backend/test_vqa_model_process.py -q` with the same explicit model environment. This opt-in integration test exercises the actual virtualenv launcher/descendant and verifies that all output handles close before cleanup; it skips without local model configuration and never downloads weights.

## Evidence

Retain `.audit/verification/<run>/result.json`, export JSON, server logs and isolated home. Main agent captures actual CUA desktop/narrow screenshots of `/vqa`. HTTP proof is not visual acceptance.

## Cleanup

Stop only the driver's child tree; retain artifacts and confirm its listener stopped. No user home or unrelated process is touched.

## Frozen evaluation benchmark

Reuse this owned launcher with `tools/verify-trialdesk.py <unique-run> --ci --port 8432 --benchmark-vqa smoke` for a no-inference lifecycle check. Follow `docs/benchmark-plan.md` for explicit pinned ScienceQA preparation, separate validation/profile and final held-out test stages. The maintained HTTP journey is `tools/benchmark-vqa-journey.py`; `tools/test-vqa-benchmark.py` checks deterministic statistics and absence of gold in model inputs. `.github/workflows/vqa-benchmark.yml` uses an isolated CPU model environment and retains full evidence. No local inference/load campaigns run without main's resource grant.

## Helpers

`tools/verify-vqa.py` is the maintained HTTP driver. `tools/verify-trialdesk.py` retains inherited table/experiment coverage. See [feature map](features/README.md).
