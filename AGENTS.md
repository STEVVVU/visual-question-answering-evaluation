# Visual question answering evaluation

The VQA extension uses `.agents/skills/verify-vqa/SKILL.md` and `tools/verify-vqa.py`. Read that map before changing VQA behavior. Core deterministic tests are `tests/backend/test_vqa.py`; the actual CPU model proof requires explicit local configuration and never downloads implicitly. Keep the model runner free of gold annotations and remote code. Preserve separate answer/scoring cache provenance, immutable manifests and visible failed/partial states. Use Node 24 for inherited frontend checks.

Trialdesk extends the complete Microsoft Data Formulator application. Preserve the MIT license, Microsoft notices, original README and upstream history. Keep `upstream` pointed at Microsoft. Publish only to the user's fork; do not create release tags or run inherited deployment workflows.

Use Python 3.11 or newer with the checked-in `uv.lock`, and Node 24 with Yarn 1.22.22 and `yarn.lock`. Install with `uv sync --frozen` and `yarn install --frozen-lockfile`. Do not switch package managers or globally install tooling. This checkout keeps optional tools under ignored `.tooling/`.

Read `.agents/skills/verify-trialdesk/SKILL.md` before product changes. Its real-product driver is `tools/verify-trialdesk.py`. Use unique ignored `.audit/verification/` profiles, loopback addresses and synthetic data. Do not read a personal Data Formulator home or configure external model credentials. The inherited local Python executor is not a security boundary for hostile code.

Existing checks are `yarn test`, `yarn build`, `yarn tsc --noEmit` and `uv run pytest tests/backend`. Use relevant focused backend tests first. The inherited `yarn lint` applies fixes; invoke ESLint without `--fix` when checking a bounded change. Preserve deterministic schema, input-boundary and numerical checks. Never assert live LLM prose or claim model quality without evaluation.

New HTTP routes use the existing JSON success/error envelope, identity and workspace selection. Do not accept arbitrary filesystem paths or evaluate user code in the fixed experiment-analysis workflow. Use existing MUI components/tokens. Drive UI with CUA and inspect actual rendered states. Retain evidence after stopping only owned processes.

Current-date logical commits must distinguish original additions from inherited features. Never commit `.env`, API keys, user workspaces, generated distributions, local tooling or verification data.
