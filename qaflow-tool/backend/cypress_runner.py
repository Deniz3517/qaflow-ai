"""Cypress runner for the QAFLOW backend.

Spawns `cypress run` against the SportHub site, streams stdout to a callback
(used to forward to WebSocket clients), and parses the mochawesome / spec
output to produce a structured result.

Public API:
    run_all(workspace, on_line) -> dict
    run_spec(workspace, spec, on_line) -> dict
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

CYPRESS_DIR = Path(__file__).resolve().parent.parent.parent / "cypress-tests"
CACHE_DIR = "/tmp/qaflow-cypress-cache"

# Patterns for parsing the default spec reporter output.
_RX_RUNNING_SPEC = re.compile(r"Running:\s+(\S+\.cy\.js)")
_RX_PASS         = re.compile(r"^\s+✓\s+(.+?)\s+\((\d+)ms\)\s*$")
_RX_PASS_NOMS    = re.compile(r"^\s+✓\s+(.+?)\s*$")
_RX_FAIL         = re.compile(r"^\s+\d+\)\s+(.+?)\s*$")
_RX_PENDING      = re.compile(r"^\s+-\s+(.+?)\s*$")
_RX_TOTALS       = re.compile(r"^\s*(\d+)\s+(passing|failing|pending)\s*$")
_RX_FAILMSG      = re.compile(r"^\s+\d+\)\s+(.+)$")


def _stream_subprocess(cmd: list[str], cwd: Path, env: dict, on_line: Callable[[str], None]):
    """Run cmd, stream stdout/stderr line-by-line to `on_line`. Returns exit code + full log."""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
    )
    full = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        full.append(line)
        try:
            on_line(line)
        except Exception:
            pass
    proc.wait()
    return proc.returncode, "\n".join(full)


def _parse_spec_output(log: str) -> dict:
    """Best-effort parse of Cypress' spec reporter output."""
    tests = []  # list of {name, status: "pass"|"fail"|"pending", duration_ms?, spec}
    current_spec = None
    summary = {"passing": 0, "failing": 0, "pending": 0}

    in_describe = False
    pending_test_name = None  # used to attach failure stacks/lines to a test

    for raw in log.splitlines():
        m = _RX_RUNNING_SPEC.search(raw)
        if m:
            current_spec = m.group(1)
            continue

        m = _RX_PASS.match(raw)
        if m:
            tests.append({
                "spec": current_spec,
                "name": m.group(1).strip(),
                "status": "pass",
                "duration_ms": int(m.group(2)),
            })
            continue

        m = _RX_PASS_NOMS.match(raw)
        if m and "✓" in raw:
            tests.append({
                "spec": current_spec,
                "name": m.group(1).strip(),
                "status": "pass",
            })
            continue

        m = _RX_FAIL.match(raw)
        if m and not raw.lstrip().startswith("'"):
            # the first occurrence is the inline list, second is the detailed failure block
            name = m.group(1).strip()
            existing = next((t for t in tests if t["name"] == name and t["spec"] == current_spec), None)
            if not existing:
                tests.append({
                    "spec": current_spec,
                    "name": name,
                    "status": "fail",
                })
            continue

        m = _RX_PENDING.match(raw)
        if m and raw.lstrip().startswith("-"):
            tests.append({
                "spec": current_spec,
                "name": m.group(1).strip(),
                "status": "pending",
            })
            continue

        m = _RX_TOTALS.match(raw)
        if m:
            summary[m.group(2)] = int(m.group(1))

    # Reduce duplicates (passes can show up in two places — header + final list)
    seen = set()
    unique = []
    for t in tests:
        key = (t["spec"], t["name"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(t)

    # Re-derive totals from de-duplicated list
    counted = {"pass": 0, "fail": 0, "pending": 0}
    for t in unique:
        counted[t["status"]] = counted.get(t["status"], 0) + 1

    return {
        "tests": unique,
        "passed": counted["pass"],
        "failed": counted["fail"],
        "pending": counted["pending"],
        "total": len(unique),
        "reporter_summary": summary,
    }


def list_screenshots(spec: str | None = None) -> list[str]:
    """Return paths to screenshots cypress generated on failure."""
    root = CYPRESS_DIR / "cypress" / "screenshots"
    if not root.exists():
        return []
    out = []
    for p in root.rglob("*.png"):
        if spec and spec not in str(p):
            continue
        out.append(str(p))
    return sorted(out)


def run_specs(specs: list[str] | None, on_line: Callable[[str], None]) -> dict:
    """Run cypress for the given specs (None = all). Returns a structured result.

    Caller passes `on_line` to receive each stdout line as it arrives.
    """
    started_at = time.time()

    cmd = ["node_modules/.bin/cypress", "run", "--browser", "chrome"]
    if specs:
        cmd += ["--spec", ",".join(f"cypress/e2e/{s}" for s in specs)]

    env = os.environ.copy()
    env["CYPRESS_CACHE_FOLDER"] = CACHE_DIR
    env["TERM"] = "dumb"
    env["FORCE_COLOR"] = "0"

    # Clear previous screenshots so we only see new failures
    shots_dir = CYPRESS_DIR / "cypress" / "screenshots"
    if shots_dir.exists():
        import shutil
        shutil.rmtree(shots_dir, ignore_errors=True)

    code, log = _stream_subprocess(cmd, CYPRESS_DIR, env, on_line)
    parsed = _parse_spec_output(log)
    parsed["exit_code"] = code
    parsed["duration_s"] = round(time.time() - started_at, 1)
    parsed["screenshots"] = [str(p.relative_to(CYPRESS_DIR)) for p in (CYPRESS_DIR / "cypress" / "screenshots").rglob("*.png")] if (CYPRESS_DIR / "cypress" / "screenshots").exists() else []
    parsed["log"] = log
    return parsed


def run_all(on_line: Callable[[str], None]) -> dict:
    return run_specs(None, on_line)


def run_spec(spec: str, on_line: Callable[[str], None]) -> dict:
    return run_specs([spec], on_line)
