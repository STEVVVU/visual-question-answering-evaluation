# ScienceQA validation evidence

[Hosted validation run 39730284596](https://github.com/STEVVVU/visual-question-answering-evaluation/actions/runs/39730284596) used clean source `47d33e2591bbd89c7c7af2c55940538014304319`, Python 3.11.15, PyTorch 2.11.0+cpu, Transformers 5.20.2, Pillow 13.0.0 and the pinned SmolVLM revision in the [frozen plan](benchmark-plan.md). The runner had four logical CPUs (Intel Xeon Platinum 8573C), Linux 6.17.0-1022-azure. Model generation, prompt, four CPU threads, image splitting and generation settings were unchanged.

The 16 validation images/questions were selected from the official validation split, independently of labels, before generation. Five fresh-workspace repeats evaluated the same 16 items, alternating order. All 80 image evaluations completed, with zero cache hits, failures or timeouts and positive vision-encoder evidence for every item. Ten duplicate run submissions were explicitly rejected as busy (one per eight-item batch). These admission rejections are separate from item failures. Owned listener cleanup passed. Final held-out test results remain unavailable; validation is not a final generalization estimate.

| Validation scoring | Correct / 16 per repeat | Accuracy | Unrecognized / 16 |
| --- | ---: | ---: | ---: |
| Original saved scorer `mode-exact-v2` | 1 in all five repeats | 6.25% | 13 |
| Revised scorer `mode-exact-v3`, same saved predictions | 12 in all five repeats | 75.00% | 0 |

The original scorer accepts bare letters, exact choices and consistent letter/choice responses. It rejected explicit outputs such as `Answer: C`. The deterministic compatibility change adds that whole-response letter form, rejects ambiguous prose/multiple answers/out-of-range choices, and changes the scorer version so existing scores and comparison groups remain separate. No new predictions, prompt tuning or model changes produced the revised numbers. All original exports remain immutable; re-scoring verifies their recorded SHA256 hashes first. This is a scorer compatibility correction, not a model-quality improvement. Twelve parameterized parser controls and the existing backend tests pass (40 total).

Uniform-choice seeds 0–4 scored 4/6/8/5/6 correct of 16: median 37.5%, range 25–50%; expectation from each item's choice count is 37.71%. The revised 12/16 Wilson 95% interval is 50.50–89.82%, versus original 1/16 interval 1.11–28.33%. Repeated deterministic predictions do not turn 16 distinct validation items into 80 independent quality samples. No significance or broad superiority claim follows from this small validation set.

| Runtime measure | Median across five repeats | Range |
| --- | ---: | ---: |
| End-to-end seconds per 16 items | 264.91 | 264.54–270.79 |
| End-to-end items/s | 0.06040 | 0.05909–0.06048 |
| Per-repeat item median, seconds | 18.817 | 18.705–18.835 |
| Per-repeat generation median, seconds | 18.552 | 18.466–18.565 |
| Model process peak RSS, GiB | 3.488 | 3.460–3.521 |

Each eight-item batch loads a fresh CPU model; end-to-end time includes that startup and actual service polling/export overhead. Individual image tensors used 5–17 crops under the shipped processor defaults. Per-repeat maximum item times ranged 19.05–19.34 seconds; 16 samples per repeat do not establish a useful p99. Total campaign elapsed time was 1,332.81 seconds. Re-scoring does not change the retained runtime measurements. The final test remains gated on main review of the scorer correction and separate profile evidence below.

## Separate native CPU profile

[Profile run 39741751233](https://github.com/STEVVVU/visual-question-answering-evaluation/actions/runs/39741751233), clean source `c556992069d1318c092e59c155fe3eb298052709`, executed the first eight frozen validation items. All eight completed uncached, with six correct, zero unrecognized answers/errors, one expected busy-admission rejection and successful cleanup. Their source IDs, raw predictions, prompt hashes and pixel shapes match the prior validation evidence. This is an integrity check on the same validation items, not eight additional independent quality samples.

The profiler retained exactly eight native tables with 100 operator/shape rows each. Actual Torch is 2.11.0+cpu, Release build, MKL/oneDNN/OpenMP, AVX2 capability; intra-op/inter-op thread counts are 4/4. The runner has four logical CPUs on AMD EPYC 7763. These are build/runtime facts, not proof that a different backend or thread count would be faster. The normal model settings remain unchanged.

| Operator family | Sum of displayed native self CPU seconds across eight items |
| --- | ---: |
| `aten::addmm` |128.02|
| `aten::bmm` |36.99|
| `aten::mm` |15.10|
| `aten::_softmax` |11.91|

These diagnostic values come from the 100 displayed rows per native table; rounded native self-time totals are 219.70s. Dominant projections use shapes such as `[17408,768]` and `[17408,3072]`, corresponding to 17 image crops ×1024 vision tokens; the two 13-crop items use 13312 tokens. The attention tables' 9.56 GB allocation rows sum 12 calls and must not be read as simultaneous live memory or a leak. Instrumented whole-process peak RSS was 3.99 GiB. Native memory columns cover tensor allocations within generation, separately from startup/model weights and process RSS.

All profiler timings/RSS are excluded from the five-repeat runtime results. The instrumented batch took 264.63s; cProfile attributes 26.14s to producing native receipts, primarily event parsing/grouping, in addition to tracing overhead inside model execution. This is bounded profiling work, not a production-path regression. The earlier cProfile-only run used another VM; no causal overhead ratio follows from comparing those runs. No avoidable product defect is established by a hot dense-matrix operator. The smallest next step is the unchanged final held-out evaluation after main review, without tuning model/prompt/generation/thread settings on this evidence.

Reproduce through the workflow's separate `profile` stage. Native artifact `vqa-benchmark-profile-c556992069d1318c092e59c155fe3eb298052709` retains `profiles/model-2568-item-0.txt` through `item-7.txt`, cProfile and the validation export. Profile `benchmark.json` SHA256 is `55131559965acdf8e5009b4fa2271d33b1bb4bf847cb6b48f899ef09a41382a0`; export SHA256 is `de7b0bdf0539f6c6f71eceac8e4e36a69bb1761e93f207738e59430434b088ce`. The raw receipt header contains only item UUID/index and Torch configuration; tables contain operator shapes/timing/allocation metadata, without question/gold text, tensor values, model data or Chrome traces.

Reproduce through the manual `validation` stage of `.github/workflows/vqa-benchmark.yml`, or provision the exact isolated CPU environment, run `tools/download-vqa-model.py`, `tools/prepare-vqa-benchmark.py --download`, then `tools/verify-trialdesk.py <unique-run> --ci --port 8432 --benchmark-vqa .tooling/scienceqa/validation-manifest.json`. Downloads are explicit; the official ScienceQA data license is CC BY-NC-SA 4.0. Never tune on the separate final test.

Retained artifact: `vqa-benchmark-validation-47d33e2591bbd89c7c7af2c55940538014304319`, directory `verification/benchmark-validation-39730284596/`. `benchmark.json` records all predictions, timings, model/dataset provenance and five repeat results; ten `benchmark-<repeat>-<offset>-export.json` files retain the immutable versions and original scores. Hosted validation manifest SHA256 is `030e6cb2dd2aac3dd46bae153295b83f7f3e0171455bcb57b741dc5431f63b61`. Raw exports include licensed dataset images and remain outside Git. Reproduce the same-output analysis with `python tools/rescore-vqa-validation.py <downloaded-validation-directory> <new-output.json>` in the app environment. This validation-only helper verifies original export hashes, rejects final-test metadata and refuses to overwrite existing output. Local derived evidence is `.audit/validation-rescore-portable-39730284596.json`, retaining source/export hashes, source IDs, raw predictions and both scoring results; it does not overwrite the original run.
