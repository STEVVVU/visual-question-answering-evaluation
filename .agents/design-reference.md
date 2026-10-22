# VQA dataset studio

Primary reference: https://huggingface.co/datasets/merve/vqav2-small/viewer/default/validation
Visually inspected supplied capture at 1280×720: `C:/Users/steve/.codex/orchestrate/coursework30-20261021/evidence/P02/reference/huggingface-dataset-studio.png`.

Reproduce the dark dataset workspace: 47px header, 16px outer inset, 12px gap, 756/480 desktop table/inspector proportions, 58px split bar, 38px search bar, 65px rows, 72×48 image previews, 8px panel radii, #0b0f19 background and #252a37 borders. Use existing MUI and Roboto/system text with monospaced table content. Own project title replaces vendor branding. Annotation and evaluation controls replace the unsupported SQL console. Omit unsupported histogram decoration; show actual field types/counts instead. Narrow screens stack panels to preserve accessible controls. Main agent owns final CUA comparison; code inspection alone does not establish visual parity.

## Rendered comparison

Main-agent CUA compared the actual `/vqa` dataset workspace at 1280×720 with the selected screenshot and confirmed the two-column header/split/table/inspector composition and measured spacing. Actual readable dark input/tab colors were checked after correcting MUI theme derivation. At 390×844, the table and inspector stack; all form and save controls remain reachable, and measured `document.scrollWidth` and `innerWidth` both equal 390. The temporary viewport was reset after review.

Public synthetic-content captures: [dataset desktop](../docs/vqa/dataset-desktop.png), [completed evaluation desktop](../docs/vqa/completed-desktop.png), [narrow inspector](../docs/vqa/inspector-narrow.png). Main retained full narrow page and leaderboard captures in the orchestration store. Browser actions included actual owned image upload, annotation save, freeze, real model generation, result/export inspection, settled reload persistence and a gold-only revision with cached answer and recomputed incorrect score. Failure/cancellation diagnostic captures remain private local evidence. These checks establish the stated viewport/state observations, not general usability or model quality.

Corrected actual browser cancellation was checked after observing both the owned launcher and Python model descendant. It settled as cancelled/unranked with `Cancelled by user.`, no file-lock error and the evaluator available again. Nonmatching search guidance was also checked. The temporary browser tab was closed and viewport override reset after review.
