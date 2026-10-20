from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("run_name")
parser.add_argument("--port", type=int, default=8430)
parser.add_argument("--ci", action="store_true")
args = parser.parse_args()
if not args.run_name.replace("-", "").replace("_", "").isalnum():
    parser.error("Use a unique alphanumeric run name")
if not 1024 < args.port < 65535:
    parser.error("Use a non-privileged port")
run = ROOT / ".audit" / "verification" / args.run_name
run.mkdir(parents=True, exist_ok=False)
home = run / "home"
base = f"http://127.0.0.1:{args.port}"
workspace_id = "trialdesk-verification"
evidence: dict = {"run": args.run_name, "base": base, "actions": []}
child: subprocess.Popen | None = None
log = (run / "server.log").open("a", encoding="utf-8")


def port_free() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", args.port)) != 0


def api(route: str, payload=None, form=None):
    headers = {"X-Workspace-Id": workspace_id}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(base + route, body, headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    assert result["status"] == "success", result
    return result["data"]


def doctor():
    assert child is not None and child.poll() is None
    config = api("/api/app-config")
    assert config["WORKSPACE_BACKEND"] == "local"
    assert config["IS_LOCAL_MODE"] is True
    assert config["DISABLE_DATA_CONNECTORS"] is True
    assert Path(config["DATA_FORMULATOR_HOME"]).resolve() == home.resolve()
    evidence["actions"].append({"action": "doctor", "isolated_home": True})
    print("DOCTOR passed: actual app, loopback identity, isolated durable workspace", flush=True)


def launch():
    global child
    assert port_free(), "Refusing an occupied port"
    env = {
        key: value for key, value in os.environ.items()
        if not key.endswith(("API_KEY", "API_TOKEN"))
        and not key.startswith(("AZURE_", "AUTH_", "OPENAI_", "ANTHROPIC_"))
    }
    env.update({
        "DATA_FORMULATOR_HOME": str(home),
        "DATA_FORMULATOR_DESKTOP": "1",
        "HOST": "127.0.0.1",
        "PYTHONUTF8": "1",
    })
    child = subprocess.Popen(
        [sys.executable, "-m", "data_formulator", "--host", "127.0.0.1",
         "--port", str(args.port), "--data-dir", str(home),
         "--disable-display-keys", "--disable-data-connectors", "--disable-custom-models"],
        cwd=ROOT, env=env, stdout=log, stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        assert child.poll() is None, "App exited; inspect server.log"
        try:
            api("/api/app-config")
            api("/api/sessions/list")
            doctor()
            evidence["actions"].append({"action": "launch", "pid": child.pid})
            return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    raise RuntimeError("App did not initialize; inspect server.log")


def stop():
    global child
    if child is None:
        return
    if child.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                           check=True, capture_output=True)
        else:
            os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=20)
    child = None
    assert port_free(), "Owned listener did not stop"


def drive():
    api("/api/sessions/save", {"id": workspace_id, "state": {"trialdeskVerification": True}})
    rows = [{"unit": f"u{i}", "arm": "treatment" if i % 2 else "control",
             "pre": i % 17, "outcome": (i % 17) * 2 + i % 3 + (i % 2)}
            for i in range(120)]
    result = api("/api/tables/create-table", form={
        "table_name": "verification_experiment", "raw_data": json.dumps(rows)})
    assert result["row_count"] == len(rows)
    evidence["actions"].append({"action": "upload-table", "rows": len(rows)})
    verify()


def verify():
    result = api("/api/tables/sample-table", {"table": "verification_experiment", "size": 200})
    assert result["total_row_count"] == 120
    rows = sorted(result["rows"], key=lambda row: row["unit"])
    expected = sorted([
        {"#rowId": i + 1, "unit": f"u{i}", "arm": "treatment" if i % 2 else "control",
         "pre": i % 17, "outcome": (i % 17) * 2 + i % 3 + (i % 2)}
        for i in range(120)], key=lambda row: row["unit"])
    assert rows == expected
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    evidence["actions"].append({"action": "read-table", "rows": len(rows), "sha256": digest})
    print("DRIVE passed: exact 120-row table through production upload/read routes", flush=True)


try:
    launch()
    print(f"READY {base}; commands doctor, drive, restart, verify, cleanup", flush=True)
    commands = ["drive", "restart", "verify", "cleanup"] if args.ci else sys.stdin
    for line in commands:
        command = line.strip()
        if command == "cleanup":
            break
        if command == "doctor":
            doctor()
        elif command == "drive":
            drive()
        elif command == "verify":
            verify()
        elif command == "restart":
            stop()
            launch()
            verify()
        else:
            print("Use doctor, drive, restart, verify, cleanup", flush=True)
except Exception as error:
    evidence["error"] = f"{type(error).__name__}: {error}"
    raise
finally:
    stop()
    log.close()
    evidence["cleanup"] = {"listenerStopped": port_free(), "retainedHome": str(home)}
    (run / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"Evidence retained at {run}", flush=True)
