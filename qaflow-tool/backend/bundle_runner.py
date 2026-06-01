"""Generic test-runner for AI-generated test bundles.

The legacy ``cypress_runner.py`` is hard-wired to the project-root
``cypress-tests/`` workspace. After STEP 2 (smoke gen) the AI writes a
NEW bundle to ``tests/{project}/{project}-{framework}/``. This module
runs that bundle and parses pass/fail counts so the orchestrator gate
has a real signal.

Framework coverage in this build:
    - cypress-js / cypress-ts    full support
    - playwright-js              full support (json reporter)
    - pytest-playwright          full support (junit-xml)
    - robot-py                   full support (output.xml)
    - selenium-py                same parser as pytest-playwright

Returns a dict:
    {{
      "exit_code": int,
      "passed": int,
      "failed": int,
      "total": int,
      "pass_rate_pct": int,
      "duration_s": float,
      "tests": [{"name": str, "status": "pass"|"fail"|"pending", "duration_ms": int|None}],
      "log": str,                  # raw stdout
      "log_tail": str,             # last ~4 KB
      "screenshots": [str],        # relative paths if any
    }}
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Literal

import frameworks


Step = Literal["smoke", "e2e", "negative"]


def _stream(cmd: list[str], cwd: Path, env: dict, on_line: Callable[[str], None]) -> tuple[int, str]:
    """Run cmd, stream each line to on_line, return (exit_code, full_log)."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    full: list[str] = []
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


def _pass_rate(passed: int, total: int) -> int:
    if total <= 0:
        return 0
    return int(round((passed / total) * 100))


def _tail(log: str, n: int = 4000) -> str:
    return log if len(log) <= n else "…(truncated)\n" + log[-n:]


# ---------------------------------------------------------------------------
# Cypress
# ---------------------------------------------------------------------------

_RX_CY_PASS = re.compile(r"^\s+✓\s+(.+?)(?:\s+\((\d+)ms\))?\s*$")
_RX_CY_FAIL = re.compile(r"^\s+\d+\)\s+(.+?)\s*$")
_RX_CY_TOTALS = re.compile(r"^\s*(\d+)\s+(passing|failing|pending)\s*$")


def _parse_cypress_log(log: str) -> dict:
    tests: list[dict] = []
    summary = {"passing": 0, "failing": 0, "pending": 0}

    for raw in log.splitlines():
        m = _RX_CY_PASS.match(raw)
        if m and "✓" in raw:
            tests.append({
                "name": m.group(1).strip(),
                "status": "pass",
                "duration_ms": int(m.group(2)) if m.group(2) else None,
            })
            continue
        m = _RX_CY_FAIL.match(raw)
        if m and not raw.lstrip().startswith("'"):
            name = m.group(1).strip()
            if name.endswith(".png") or "screenshots/" in name:
                continue
            if not any(t["name"] == name for t in tests):
                tests.append({"name": name, "status": "fail", "duration_ms": None})
            continue
        m = _RX_CY_TOTALS.match(raw)
        if m:
            summary[m.group(2)] = int(m.group(1))

    # Prefer the reporter totals if they're populated; otherwise derive from
    # the per-test list (more robust against reporter-quirk diffs).
    if summary["passing"] + summary["failing"] + summary["pending"] > 0:
        passed = summary["passing"]
        failed = summary["failing"]
        pending = summary["pending"]
        total = passed + failed + pending
    else:
        passed = sum(1 for t in tests if t["status"] == "pass")
        failed = sum(1 for t in tests if t["status"] == "fail")
        pending = sum(1 for t in tests if t["status"] == "pending")
        total = len(tests)

    return {
        "tests": tests,
        "passed": passed, "failed": failed, "pending": pending, "total": total,
    }


def _run_cypress(bundle: Path, step: Step, on_line: Callable[[str], None]) -> dict:
    """Run cypress against {bundle}/{step}/**/*.cy.{js,ts}.

    Requires {bundle}/node_modules/.bin/cypress to exist (caller runs
    install if needed). We do not auto-install here — that's a separate
    explicit endpoint to keep the runner side-effect-free.
    """
    bin_path = bundle / "node_modules" / ".bin" / "cypress"
    if not bin_path.exists():
        return {
            "exit_code": -1,
            "passed": 0, "failed": 0, "total": 0,
            "pass_rate_pct": 0, "duration_s": 0.0,
            "tests": [],
            "log": f"cypress not installed at {bin_path}",
            "log_tail": "",
            "screenshots": [],
            "install_required": True,
        }

    started = time.time()
    env = os.environ.copy()
    env["TERM"] = "dumb"
    env["FORCE_COLOR"] = "0"
    env["CYPRESS_CACHE_FOLDER"] = "/tmp/qaflow-cypress-cache"

    spec_glob = f"{step}/**/*.cy.{{js,ts}}"
    cmd = [str(bin_path), "run", "--browser", "chrome",
           "--spec", spec_glob, "--reporter", "spec"]

    code, log = _stream(cmd, bundle, env, on_line)
    parsed = _parse_cypress_log(log)
    duration = round(time.time() - started, 1)

    shots_root = bundle / "cypress" / "screenshots"
    screenshots = (
        [str(p.relative_to(bundle)) for p in shots_root.rglob("*.png")]
        if shots_root.exists() else []
    )

    parsed.update({
        "exit_code": code, "duration_s": duration,
        "log": log, "log_tail": _tail(log),
        "screenshots": screenshots,
        "pass_rate_pct": _pass_rate(parsed["passed"], parsed["total"]),
    })
    return parsed


# ---------------------------------------------------------------------------
# Playwright (JS) — uses --reporter json
# ---------------------------------------------------------------------------

def _run_playwright_js(bundle: Path, step: Step, on_line: Callable[[str], None]) -> dict:
    bin_path = bundle / "node_modules" / ".bin" / "playwright"
    if not bin_path.exists():
        return _not_installed("playwright", bin_path)
    started = time.time()
    env = os.environ.copy()
    env["TERM"] = "dumb"
    env["FORCE_COLOR"] = "0"

    test_glob = f"{step}/**/*"
    cmd = [str(bin_path), "test", test_glob, "--reporter=line"]
    code, log = _stream(cmd, bundle, env, on_line)

    # Playwright "line" reporter prints "ok N" / "fail N" lines and a final summary.
    passed = log.count("\n  ok ") + log.count(" passed")
    failed = log.count(" failed")
    # Try to extract precise final totals.
    m = re.search(r"(\d+)\s+passed.*?(\d+)\s+failed", log, re.S)
    if m:
        passed = int(m.group(1))
        failed = int(m.group(2))
    total = passed + failed
    return {
        "exit_code": code, "duration_s": round(time.time() - started, 1),
        "passed": passed, "failed": failed, "pending": 0, "total": total,
        "pass_rate_pct": _pass_rate(passed, total),
        "tests": [],   # line reporter doesn't give us per-test fidelity
        "log": log, "log_tail": _tail(log), "screenshots": [],
    }


# ---------------------------------------------------------------------------
# pytest / pytest-playwright / pytest-selenium — JUnit XML reporter
# ---------------------------------------------------------------------------

def _parse_junit_xml(xml_path: Path) -> dict:
    if not xml_path.exists():
        return {"passed": 0, "failed": 0, "pending": 0, "total": 0, "tests": []}
    tree = ET.parse(xml_path)
    root = tree.getroot()
    # JUnit may wrap in <testsuites> or be a single <testsuite>.
    suites = root.findall("testsuite") or [root]

    passed = failed = pending = 0
    tests: list[dict] = []
    for ts in suites:
        for tc in ts.findall("testcase"):
            name = f"{tc.get('classname', '')}::{tc.get('name', '')}"
            duration_ms = int(float(tc.get("time", "0")) * 1000)
            if tc.find("failure") is not None or tc.find("error") is not None:
                tests.append({"name": name, "status": "fail", "duration_ms": duration_ms})
                failed += 1
            elif tc.find("skipped") is not None:
                tests.append({"name": name, "status": "pending", "duration_ms": duration_ms})
                pending += 1
            else:
                tests.append({"name": name, "status": "pass", "duration_ms": duration_ms})
                passed += 1
    return {"passed": passed, "failed": failed, "pending": pending,
            "total": passed + failed + pending, "tests": tests}


def _run_pytest(bundle: Path, step: Step, on_line: Callable[[str], None]) -> dict:
    pytest_bin = bundle / ".venv" / "bin" / "pytest"
    if not pytest_bin.exists():
        return _not_installed("pytest", pytest_bin)

    started = time.time()
    env = os.environ.copy()
    env["TERM"] = "dumb"
    env["FORCE_COLOR"] = "0"

    junit_xml = bundle / f".qaflow_junit_{step}.xml"
    if junit_xml.exists():
        junit_xml.unlink()

    test_dir = bundle / step
    cmd = [str(pytest_bin), str(test_dir), f"--junit-xml={junit_xml}", "-q"]
    code, log = _stream(cmd, bundle, env, on_line)

    parsed = _parse_junit_xml(junit_xml)
    parsed.update({
        "exit_code": code, "duration_s": round(time.time() - started, 1),
        "pass_rate_pct": _pass_rate(parsed["passed"], parsed["total"]),
        "log": log, "log_tail": _tail(log), "screenshots": [],
    })
    return parsed


# ---------------------------------------------------------------------------
# Appium (Python) — pytest under bundle .venv with APPIUM_HOST env var
# ---------------------------------------------------------------------------

def _run_appium(bundle: Path, step: Step, on_line: Callable[[str], None]) -> dict:
    """Run mobile-flow tests via pytest + Appium-Python-Client.

    Requires:
      - bundle/.venv with pytest + Appium-Python-Client installed
      - an Appium server reachable at env APPIUM_HOST (default 4723)
      - a connected device or emulator (adb / Xcode) on the host
    """
    pytest_bin = bundle / ".venv" / "bin" / "pytest"
    if not pytest_bin.exists():
        return _not_installed("appium (pytest+appium-python-client)", pytest_bin)

    started = time.time()
    env = os.environ.copy()
    env["TERM"] = "dumb"
    env["FORCE_COLOR"] = "0"
    env.setdefault("APPIUM_HOST", "http://127.0.0.1:4723")

    junit_xml = bundle / f".qaflow_junit_appium_{step}.xml"
    if junit_xml.exists():
        junit_xml.unlink()

    test_dir = bundle / step
    cmd = [str(pytest_bin), str(test_dir), f"--junit-xml={junit_xml}", "-q",
           "-m", "mobile or not mobile"]   # accept both tagged and untagged
    code, log = _stream(cmd, bundle, env, on_line)

    parsed = _parse_junit_xml(junit_xml)
    parsed.update({
        "exit_code": code, "duration_s": round(time.time() - started, 1),
        "pass_rate_pct": _pass_rate(parsed["passed"], parsed["total"]),
        "log": log, "log_tail": _tail(log), "screenshots": [],
        "appium_host": env["APPIUM_HOST"],
    })
    return parsed


# ---------------------------------------------------------------------------
# Robot Framework — output.xml
# ---------------------------------------------------------------------------

def _parse_robot_output(xml_path: Path) -> dict:
    if not xml_path.exists():
        return {"passed": 0, "failed": 0, "pending": 0, "total": 0, "tests": []}
    tree = ET.parse(xml_path)
    root = tree.getroot()
    passed = failed = pending = 0
    tests: list[dict] = []
    for tc in root.iter("test"):
        status_el = tc.find("status")
        status_attr = (status_el.get("status") if status_el is not None else "FAIL") or "FAIL"
        name = tc.get("name", "")
        if status_attr == "PASS":
            tests.append({"name": name, "status": "pass", "duration_ms": None})
            passed += 1
        elif status_attr == "SKIP":
            tests.append({"name": name, "status": "pending", "duration_ms": None})
            pending += 1
        else:
            tests.append({"name": name, "status": "fail", "duration_ms": None})
            failed += 1
    return {"passed": passed, "failed": failed, "pending": pending,
            "total": passed + failed + pending, "tests": tests}


def _run_robot(bundle: Path, step: Step, on_line: Callable[[str], None]) -> dict:
    robot_bin = bundle / ".venv" / "bin" / "robot"
    if not robot_bin.exists():
        return _not_installed("robot", robot_bin)

    started = time.time()
    env = os.environ.copy()
    env["TERM"] = "dumb"

    output_dir = bundle / ".qaflow_robot_out"
    output_dir.mkdir(exist_ok=True)
    output_xml = output_dir / f"output_{step}.xml"

    cmd = [str(robot_bin),
           "--outputdir", str(output_dir),
           "--output", output_xml.name,
           str(bundle / step)]
    code, log = _stream(cmd, bundle, env, on_line)

    parsed = _parse_robot_output(output_xml)
    parsed.update({
        "exit_code": code, "duration_s": round(time.time() - started, 1),
        "pass_rate_pct": _pass_rate(parsed["passed"], parsed["total"]),
        "log": log, "log_tail": _tail(log), "screenshots": [],
    })
    return parsed


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _not_installed(name: str, expected_path: Path) -> dict:
    return {
        "exit_code": -1,
        "passed": 0, "failed": 0, "pending": 0, "total": 0,
        "pass_rate_pct": 0, "duration_s": 0.0,
        "tests": [],
        "log": f"{name} not found at {expected_path} — run install for this framework first",
        "log_tail": "",
        "screenshots": [],
        "install_required": True,
    }


def run(
    bundle_root: Path,
    framework_id: str,
    step: Step,
    on_line: Callable[[str], None] | None = None,
) -> dict:
    """Run the saved {bundle}/{step}/ suite for the given framework.

    on_line, if provided, receives each stdout line as it streams.
    """
    cb = on_line or (lambda _l: None)
    bundle = Path(bundle_root)
    if not bundle.exists():
        return {"exit_code": -1, "passed": 0, "failed": 0, "total": 0,
                "pass_rate_pct": 0, "duration_s": 0.0, "tests": [],
                "log": f"bundle does not exist: {bundle}",
                "log_tail": "", "screenshots": [],
                "missing_bundle": True}

    if framework_id in ("cypress-js", "cypress-ts"):
        return _run_cypress(bundle, step, cb)
    if framework_id == "playwright-js":
        return _run_playwright_js(bundle, step, cb)
    if framework_id in ("pytest-playwright", "selenium-py"):
        return _run_pytest(bundle, step, cb)
    if framework_id == "appium-py":
        return _run_appium(bundle, step, cb)
    if framework_id == "robot-py":
        return _run_robot(bundle, step, cb)

    return {"exit_code": -1, "passed": 0, "failed": 0, "total": 0,
            "pass_rate_pct": 0, "duration_s": 0.0, "tests": [],
            "log": f"runner not implemented for framework: {framework_id}",
            "log_tail": "", "screenshots": [],
            "unsupported_framework": True}


def install_bundle_deps(bundle_root: Path, framework_id: str) -> dict:
    """Best-effort one-time install of bundle deps so the runner can execute.

    Returns {{ok: bool, log: str}} — caller decides what to do on failure.
    """
    spec = frameworks.by_id(framework_id)
    if not spec:
        return {"ok": False, "log": f"unknown framework: {framework_id}"}
    bundle = Path(bundle_root)
    if not bundle.exists():
        return {"ok": False, "log": f"bundle does not exist: {bundle}"}

    logs: list[str] = []
    for step_cmd in spec.install_steps:
        logs.append(f"$ {' '.join(step_cmd)}")
        try:
            res = subprocess.run(
                step_cmd, cwd=bundle, capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            logs.append("(timed out)")
            return {"ok": False, "log": "\n".join(logs)}
        logs.append(res.stdout.strip()[-1500:])
        if res.returncode != 0:
            logs.append(f"(exit {res.returncode})\n{res.stderr.strip()[-1500:]}")
            return {"ok": False, "log": "\n".join(logs)}

    return {"ok": True, "log": "\n".join(logs)}
