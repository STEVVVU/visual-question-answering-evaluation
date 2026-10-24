# Repeated-evaluation answer cache

Five alternating benchmark pairs reduced median HTTP-to-completion time from **47.094 seconds uncached** to **0.453 seconds cached** for three synthetic image questions. Cached runs avoided all three model inferences and model-process startup. Every cached prediction and score matched its own prepopulation run exactly.

This measures the application answer cache, not faster model inference. The timed uncached range was **39.204–50.969 seconds**; the cached range was **0.250–0.484 seconds**. Terminal state was polled every 0.2 seconds. Do not interpret the cached values as sub-millisecond measurements or generalize them to other datasets and machines.

## Actual work and comparison

The production Flask application ran in an isolated durable workspace on Windows with pinned SmolVLM-256M weights. The owned input was one generated red-square image and three multiple-choice questions. No ScienceQA photograph, external credential, or model download was used.

Each pair created two empty workspaces with identical image, question, and annotation content. It populated the warm workspace before timing. The timed uncached and cached arms then alternated order across five pairs. Operating-system file caches were not flushed.

Across the complete campaign, prepopulation and uncached arms performed **30 real image inferences and 30 vision forwards**. Timed cached arms reused **15 answers**. Every completed run retained the pinned model-file manifest, answer keys, independently recomputed score keys, and scorer outputs. Annotation IDs differ across workspaces; input and model provenance remain equivalent. Predictions from independent model generations are compared descriptively, not required to have identical wording. Cached output must preserve its own prepopulation output exactly.

| Pair | Timed order | Uncached completion, seconds | Cached completion, seconds |
| ---: | --- | ---: | ---: |
| 1 | Uncached, cached | 44.781 | 0.453 |
| 2 | Cached, uncached | 39.204 | 0.250 |
| 3 | Uncached, cached | 47.094 | 0.484 |
| 4 | Cached, uncached | 50.969 | 0.484 |
| 5 | Uncached, cached | 48.015 | 0.453 |

Admission-request medians were 0.016 seconds uncached and 0.015 seconds cached. They measure run creation only, not model completion. All fifteen evaluated runs completed; no timed pair or failure was omitted. Cleanup stopped the owned application listener and retained its synthetic workspace and evidence.

## Limiter and scope

Uncached generation runs on four Torch CPU threads. Its receipts record three vision forwards and per-item generation, preprocessing, and model-load durations. Cached runs perform no model generation. They still check local model provenance, read stored answers, and compute or retrieve scores. That difference in work explains the improvement.

This was a developer-host measurement. Separate owned GPU experiments with one or two Torch CPU threads overlapped parts of the campaign. No compiler builds ran during it. Host-load and filesystem-cache effects remain in the observed ranges. The large separation supports this repeated-workflow result; it is not a quiet-host capacity or latency guarantee. Profiling mode was rejected before the timed campaign.

Three synthetic questions do not measure general VQA accuracy. The [ScienceQA study](benchmark-results-20261022.md) remains a separate model-quality evaluation. Existing deterministic cache invalidation, annotation/scoring separation, model input isolation, cancellation, and export tests remain applicable.

## Reproduction and retained evidence

Configure the already prepared local model and use the maintained launcher:

```text
python tools/verify-trialdesk.py unique-cache-run --ci --port 8432 --benchmark-vqa cache-impact
```

The [verification skill](../.agents/skills/verify-vqa/SKILL.md) specifies the interpreter, model manifest, and optional vision-package overlay. No configured model means an explicit failure, not fixture predictions.

The [complete result](evidence/cache-impact-20261024/cache-impact.json), all fifteen exports, [cleanup receipt](evidence/cache-impact-20261024/result.json), and byte identities are retained together. Public JSON is normalized to LF; `raw-sha256.json` preserves reversible original byte identities. `source-identity.json` binds the two driver sources to their exact recorded content while accounting for Git line-ending normalization.

Setup attempts A and B failed before model inference. Attempt C completed three real inferences but failed an incorrect answer-key assertion in the harness. Those local failure artifacts remain unchanged. The native application API and cache were not changed to make the benchmark pass; the harness was corrected and independently reviewed before the separate D campaign.
