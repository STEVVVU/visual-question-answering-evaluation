---
name: verify-trialdesk
description: Launch and drive Trialdesk's actual Flask application with an isolated durable workspace, real HTTP table uploads, restart verification and CUA browser observations.
---

# Verify Trialdesk

## Launch

Install the frozen Python and Yarn workspaces, then build the real frontend with `yarn build`. From the repository root run `.venv/Scripts/python.exe tools/verify-trialdesk.py <unique-run-name>` on Windows, or `.venv/bin/python tools/verify-trialdesk.py <unique-run-name>` on Unix. For interactive tool execution allocate a PTY so stdin stays open. `--port` defaults to 8430; the driver refuses occupied ports and existing run directories. `--ci` runs the baseline journey and cleanup automatically.

The helper launches the production `python -m data_formulator` entry with an absolute, empty `DATA_FORMULATOR_HOME` inside `.audit/verification/<run>/home`. It uses inherited single-user loopback identity, keeps durable local workspaces, disables external connectors and custom model entry, and prevents automatic browser opening. No user home, external database or model connection is needed. `READY` follows actual application initialization and the doctor.

## Doctor

Send `doctor`. It checks the owned process and production `/api/app-config`, verifying loopback mode, isolated home, durable storage and disabled connectors. Inspect the retained `server.log` if startup fails. Never free a port by stopping another application.

## Drive

Send `drive`. It creates a synthetic session through `/api/sessions/save`, uploads 120 rows through `/api/tables/create-table`, and reads them through `/api/tables/sample-table`. The original row-position field `#rowId` is part of the expected API response. `restart` stops and relaunches the owned process, then verifies exact rows from the retained Parquet workspace. `verify` repeats the read.

Open the logged URL with CUA. Choose the `trialdesk-verification` session. The inherited Upload data → Upload Data → Browse → Load Table path accepts `tools/fixtures/synthetic-experiment.csv`. Its preview contains 1,000 synthetic units and four columns. This upload works without a model, but the inherited main canvas has a Select Models overlay and model-dependent semantic inference fails without credentials. Do not count the hidden canvas behind that overlay as visual acceptance.

## Evidence

The helper stores actual HTTP actions, row hashes, child PID, errors and cleanup in `.audit/verification/<run>/result.json`, with server logs and retained data. Save CUA screenshots and AX snapshots in the same run directory. Record both the input action and resulting state. Do not substitute component fixtures or internal Redux mutation for a browser journey. Do not publish credentials, private table data or personal filesystem paths.

## Cleanup

Send `cleanup`. The helper stops only its own child tree and checks its loopback listener is gone. It preserves the run's profile, JSON and screenshots. Confirm evidence remains after exit. Close temporary CUA tabs after cleanup.

## Helpers

`tools/verify-trialdesk.py <unique-run-name> [--port 8430] [--ci]` is the maintained driver. Baseline-003 passed real upload/read/restart/read and owned cleanup on October 20, 2026. Its screenshot records the inherited model gate, not a successful model workflow. See the [feature map](features/README.md). Keep this map current when user paths change; the maintain-verification-skill workflow can repair broader drift.
