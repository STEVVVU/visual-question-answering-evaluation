# ScienceQA held-out test results

[Hosted test run 39745041299](https://github.com/STEVVVU/visual-question-answering-evaluation/actions/runs/39745041299) completed five fresh-workspace CPU repeats on the same frozen set of 128 image-bearing questions from the official ScienceQA test split. Each repeat scored 77/128 correct (60.16%), with a Wilson 95% interval of 51.50–68.22%. The model returned two unrecognized answers per repeat. All 640 item executions completed, with no item failures, timeouts, or cache hits. Every execution recorded one vision forward. The 80 busy-admission rejections, one for each eight-item batch, are admission checks and are separate from item completions.

The test set is the pinned subset in the [frozen benchmark plan](benchmark-plan.md). It contains 88 natural science, 39 social science, and one language science questions. Five seeded uniform-choice baselines scored 43, 47, 60, 46, and 49 correct. The expected accuracy from each question's number of choices is 38.40%. Repeats are deterministic measurements on the same 128 questions, not 640 independent quality samples. This small, image-bearing ScienceQA result does not establish performance on general VQA datasets or broad superiority over a random baseline.

| Runtime measure | Median across repeats | Range |
| --- | ---: | ---: |
| End-to-end seconds per 128 items | 2,226.20 | 2,223.08–2,242.11 |
| CPU process seconds per 128 items | 8,447.20 | 8,441.45–8,491.93 |
| Throughput, items per second | 0.05750 | 0.05709–0.05758 |
| Item latency p50, seconds | 17.021 | 16.895–17.117 |
| Item latency p95, seconds | 20.986 | 20.901–21.076 |
| Generation latency p50, seconds | 16.808 | 16.656–16.905 |
| Generation latency p95, seconds | 20.685 | 20.635–20.791 |
| Model-process peak RSS, KiB | 3,684,036 | 3,669,756–3,734,108 |

Each eight-item batch starts a fresh model process. End-to-end time includes model startup, service polling, and export overhead. CPU process seconds sum the final process CPU-time reading from each batch. Model processes ran on a four-vCPU Microsoft virtual machine exposing an Intel Xeon Platinum 8370C at 2.80 GHz. The runtime used Python 3.11.15, PyTorch 2.11.0+cpu, Transformers 5.20.2, Pillow 13.0.0, and the exact isolated dependency freeze retained with the run. Each batch used the pinned `HuggingFaceTB/SmolVLM-256M-Instruct` revision and the unchanged deterministic generation configuration. The operating system file cache was not flushed between batches, so later batches may benefit from cached files.

The source checkout was clean at `c556992069d1318c092e59c155fe3eb298052709`. The run retained all 80 export files and verified their embedded batch manifest hashes. The run's embedded aggregate manifest hash is `948e90d3612a6545d07eb53196443d2c22f5a428f7566343918bbf05a0128d0e`. The independently prepared local manifest has a different byte hash, and its PNG byte encodings differ from the exports. The 128 source IDs, annotations, provenance, and decoded pixels match, but the full hosted prepared manifest was not retained for byte-for-byte reconciliation. Exact full-source byte identity therefore remains unreconciled.

No model, prompt, scorer, dataset, or generation settings changed during the held-out campaign. Model pretraining contamination is unknown. The benchmark reports one fixed subset and makes no speedup claim. The benchmark does not expose confidence probabilities, so calibration was not measured.

ScienceQA is by Lu et al. at [the official pinned revision](https://github.com/lupantech/ScienceQA/tree/2cbf8318e07b9ece895bb2ae605e71e38d623264). The official dataset license is [CC BY-NC-SA 4.0](https://github.com/lupantech/ScienceQA/blob/2cbf8318e07b9ece895bb2ae605e71e38d623264/LICENSE-DATA). The benchmark used image bytes from a pinned [Hugging Face dataset mirror](https://huggingface.co/datasets/derek-thomas/ScienceQA/tree/f18b0a70359ebfb41f658fd564208d0355b013f4); the official license applies. The [aggregate evidence JSON](evidence/vqa-test-20261022.json) contains metrics and source hashes only. It excludes images, answers, and local paths.
