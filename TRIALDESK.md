# Trialdesk implementation notes

## Data and API boundary

`POST /api/experiments/analyze` accepts JSON describing a table in the current workspace. Send `X-Workspace-Id` explicitly. The request selects `table`, `unit_column`, `variant_column`, `outcome_column`, `covariate_column`, `control`, `treatment`, and `expected_treatment_fraction`. The three boolean declarations `randomized_units_confirmed`, `pre_period_confirmed`, and `fixed_horizon_confirmed` must be true. The route resolves the existing workspace through the current identity; an unknown workspace is rejected without creating one.

Requests are limited to 16 KiB. Analysis reads the entire selected table, with a maximum of 100,000 rows. Selected columns must be distinct and complete. Units must be unique and nonblank, variant labels exact, and both metrics real, finite and at most 1e12 in absolute value. At least 30 units per arm are required. These limits are validation policies, not statistical validity guarantees. Analysis does not modify the table or retain a report history.

The inherited upload route remains part of the underlying application. The new UI limits CSV files to 10 MiB and assigns a fresh table identifier. Each request binds its explicit workspace header; workspace changes unmount the studio and discard late responses. Editing design inputs clears the displayed report.

## Numerical method

The treatment effect is treatment mean minus control mean. CUPED uses the common pooled coefficient `theta = cov(Y, X) / var(X)` and adjusts every outcome by `Y - theta * (X - mean(X))`. Both ordinary and adjusted effects use Welch standard errors and approximate two-sided t intervals and p-values. The covariate coefficient is estimated from the same sample, so the intervals are plug-in approximations, not exact finite-sample coverage claims.

An exact-binomial allocation test compares the observed count to the declared planned treatment fraction. A p-value below 0.001 blocks inference. Constant covariates, zero residual variance and unsupported numerical ranges also return blocked diagnostics without effect estimates. A negative variance reduction is reported rather than hidden. Extreme outcomes produce a diagnostic; no rows are discarded.

The exported schema includes `schema_version`, `method_version`, the complete specification, selected-input SHA-256, counts, diagnostics, assumptions and either estimates or a blocked status. The fingerprint is based on stable unit ordering and canonical selected column names/values. It identifies those analyzed values, not an entire source file, original collection time, ownership or an immutable audit record.

No sequential stopping rule, multiple-outcome correction, clustered inference, observational causal identification, ratio-metric extension or missing-covariate extension is implemented. The app cannot verify randomization or pre-treatment timing from declarations.

## Verification evidence

The maintained driver is `tools/verify-trialdesk.py`. The completed `final-http-001` and `final-ui-002` runs use the production application with isolated durable workspaces. HTTP acceptance covers table upload/read, valid analysis, mismatch blocking, declaration rejection, restart and exact report replay. Browser acceptance covers the bundled synthetic CSV, declaration gating, ordinary/CUPED results, actual JSON download, input invalidation and mismatch blocking. Screenshots and detailed local logs remain in ignored `.audit/verification/`; the public example report and studio screenshot are in `docs/trialdesk/`.

`tools/evaluate-cuped.py` runs 2,500 seeded Gaussian trials across five scenarios. The public result records bias and Monte Carlo uncertainty, reported and empirical variances, interval coverage and package versions. Those scenarios establish bounded synthetic behavior only. The inherited model-powered workflows and a narrow browser viewport were not evaluated.

## Dependency assessment

Frozen lockfiles were installed and exercised on Python 3.11 and Node 24.22. Compatible updates reduced inherited audit findings. The remaining findings as checked on October 20, 2026 are:

| Package | Locked version | Advisory and constraint |
| --- | --- | --- |
| KaTeX | 0.16.42 | GHSA-238p-pmpm-9mq7, low; advertised fix 0.18.2 crosses the inherited 0.x renderer line. |
| oauthlib | 3.3.1 | PYSEC-2026-4114; advertised fix 4.0.0 conflicts with Databricks SQL connector's `<4` requirement. The audit feed duplicates this entry. |
| thrift | 0.22.0 | PYSEC-2026-3925, PYSEC-2026-3926 and PYSEC-2026-3927; advertised fix 0.24.0 conflicts with the connector's `<0.23` requirement. |

The new experiment path does not invoke model providers, the Databricks connector or the math renderer. That observation is not a proof that the inherited application is safe to expose publicly. The verification helper disables external connectors and uses loopback single-user mode. Its local synthetic-data setup is not a multi-user deployment assessment. Inherited generated-Python execution is not a hostile-code sandbox.

Certifi uses an explicit official PyPI index and a minimum version of 2026.10.19; the inherited Microsoft feed remains the default for other packages. Litellm remains below 1.92 to preserve the inherited Windows wheel compatibility constraint. Dependency audit results are time-sensitive; rerun them before deployment rather than treating this document as a permanent clean bill of health.
