# Experiment analysis

## Sub-features

Two-arm schema validation, ordinary/CUPED estimates, allocation diagnostics, input fingerprint and JSON export.

## How to get to it (user POV)

Open `/experiments`, create an experiment workspace and load the synthetic example. Confirm all three design declarations, then Validate and analyze. Inspect estimates and export JSON. Changing the planned treatment fraction to 0.8 clears results; analyzing again blocks inference with an allocation diagnostic.

## Driving it with HTTP and CUA

Run the maintained driver with a unique run name. Send `drive`, `experiment`, `restart`, `verify`, then `cleanup`. `experiment` uses production `/api/experiments/analyze`, checks valid results, mismatch blocking and declaration rejection. Restart compares the entire report and input hash. `--ci` executes this journey automatically.

With CUA, drive the real browser steps above and wait for an actual download before recording export success. Record screenshots and AX snapshots beside the driver's evidence. `final-http-001` passed the HTTP journey and cleanup. `final-ui-002` passed synthetic-example loading, declaration gating, estimates, actual export, invalidation and mismatch blocking on the final production build. Desktop layout was inspected, including scientific notation for tiny allocation p-values. A narrow viewport and live model calls remain unverified.

## Gotchas

Use independent randomized units and pre-treatment covariates. Do not present simulations as real users or a completed university assignment.
