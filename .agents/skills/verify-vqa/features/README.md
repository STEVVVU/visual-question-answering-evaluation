# VQA feature map

| Feature | User path | Driver proof |
| --- | --- | --- |
| Held-out benchmark | Actual upload/freeze/run/export API with fresh workspaces and pinned ScienceQA image questions | Separate validation/profile/test, five uncached repeated CPU runs, vision execution, chance baseline, quality/latency/RSS/error and busy-admission accounting; smoke proves visible no-model failure and cleanup |
| Annotation | `/vqa` → workspace → upload PNG/question → save | Image decode limits, canonical schema and durable read |
| Versions | Dataset → Freeze version | Immutable manifest/hash; revision creates different version |
| Evaluation | Experiments → Run | Real local model, bounded generation, persisted partial/failure/cancel status |
| Comparison/export | Leaderboard → select version → export | Comparable version/scorer groups, only complete scored runs ranked; full JSON provenance |

## Sub-features

Answer cache excludes gold; score cache includes annotations and scorer revision. Manual freeform remains explicitly unscored.

## How to get to it (user POV)

Open `/vqa`, create/select a workspace and annotate an image. Freeze a version before starting a run.

## Driving it with HTTP

Run the verification skill driver and retain evidence. It proves gold-only cache reuse with changed score keys, question-change misses, rankable exact-only runs, unranked manual runs, cancellation and foreign-workspace export rejection. Browser journeys use CUA only.

## Gotchas

No model weights ship in Git. The optional real-model journey requires an explicit verified pinned local model path and interpreter. Synthetic fixture results do not measure generalization.
