"""Framework registry + real install machinery.

Each entry knows:
  - which language it belongs to (drives the UI dropdown filter),
  - the spec extension to use when saving generated code,
  - which workspace folder it lives in (relative to the qaflow-ai monorepo),
  - the shell commands required to install it,
  - the command to verify it's installed (used to populate `installed` flag).

Installs run in a worker thread per framework so the UI can poll for
progress without blocking the event loop. State is in-memory — restart
loses the install logs but the on-disk install is detected on next list.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


QAFLOW_ROOT = Path(__file__).resolve().parent.parent.parent  # /…/qaflow-ai
WORKSPACES_ROOT = QAFLOW_ROOT / "test-frameworks"


@dataclass
class FrameworkSpec:
    id: str
    name: str
    language: Literal["javascript", "typescript", "python"]
    extension: str
    workspace_rel: str             # relative to QAFLOW_ROOT — where the framework lives
    specs_subdir: str              # within the workspace, where spec files belong
    folder_name: str               # short token used in `{project}-{folder_name}/` paths
    install_steps: list[list[str]]
    check_argv: list[str]
    description: str = ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: list[FrameworkSpec] = [
    FrameworkSpec(
        id="cypress-js",
        name="Cypress",
        language="javascript",
        extension=".cy.js",
        workspace_rel="cypress-tests",
        specs_subdir="cypress/e2e",
        folder_name="cypress",
        install_steps=[[ "npm", "install", "--silent" ]],
        check_argv=["node_modules/.bin/cypress", "--version"],
        description="JavaScript end-to-end runner. Already wired into the demo's Test Runner.",
    ),
    FrameworkSpec(
        id="cypress-ts",
        name="Cypress (TypeScript)",
        language="typescript",
        extension=".cy.ts",
        workspace_rel="cypress-tests",
        specs_subdir="cypress/e2e",
        folder_name="cypress-ts",
        install_steps=[
            ["npm", "install", "--silent"],
            ["npm", "install", "--silent", "--save-dev", "typescript", "@types/node"],
        ],
        check_argv=["node_modules/.bin/cypress", "--version"],
        description="Cypress with TypeScript types — same workspace as JS variant.",
    ),
    FrameworkSpec(
        id="playwright-js",
        name="Playwright Test",
        language="javascript",
        extension=".spec.js",
        workspace_rel="test-frameworks/playwright-js",
        specs_subdir="tests",
        folder_name="playwright",
        install_steps=[
            ["npm", "init", "-y"],
            ["npm", "install", "--silent", "--save-dev", "@playwright/test"],
            ["npx", "--yes", "playwright", "install", "chromium"],
        ],
        check_argv=["node_modules/.bin/playwright", "--version"],
        description="Microsoft Playwright Test runner with chromium.",
    ),
    FrameworkSpec(
        id="robot-py",
        name="Robot Framework",
        language="python",
        extension=".robot",
        workspace_rel="test-frameworks/robot-py",
        specs_subdir="tests",
        folder_name="robotframework",
        install_steps=[
            ["python3", "-m", "venv", ".venv"],
            [".venv/bin/pip", "install", "--quiet", "--upgrade", "pip"],
            [".venv/bin/pip", "install", "--quiet",
             "robotframework", "robotframework-seleniumlibrary", "webdrivermanager"],
        ],
        check_argv=[".venv/bin/python", "-c",
                    "import robot; print('Robot Framework', robot.version.VERSION)"],
        description="Keyword-driven Python runner — Robot Framework + SeleniumLibrary.",
    ),
    FrameworkSpec(
        id="pytest-playwright",
        name="Pytest + Playwright",
        language="python",
        extension=".py",
        workspace_rel="test-frameworks/pytest-playwright",
        specs_subdir="tests",
        folder_name="pytest-playwright",
        install_steps=[
            ["python3", "-m", "venv", ".venv"],
            [".venv/bin/pip", "install", "--quiet", "--upgrade", "pip"],
            [".venv/bin/pip", "install", "--quiet", "pytest", "pytest-playwright"],
            [".venv/bin/playwright", "install", "chromium"],
        ],
        check_argv=[".venv/bin/pytest", "--version"],
        description="Pytest test runner with Playwright fixtures and chromium.",
    ),
    FrameworkSpec(
        id="selenium-py",
        name="Pytest + Selenium",
        language="python",
        extension=".py",
        workspace_rel="test-frameworks/selenium-py",
        specs_subdir="tests",
        folder_name="selenium",
        install_steps=[
            ["python3", "-m", "venv", ".venv"],
            [".venv/bin/pip", "install", "--quiet", "--upgrade", "pip"],
            [".venv/bin/pip", "install", "--quiet", "pytest", "selenium", "webdriver-manager"],
        ],
        check_argv=[".venv/bin/pytest", "--version"],
        description="Pytest + Selenium WebDriver — classic pairing for cross-browser flows.",
    ),
    FrameworkSpec(
        id="appium-py",
        name="Appium (Mobile)",
        language="python",
        extension=".py",
        workspace_rel="test-frameworks/appium-py",
        specs_subdir="tests",
        folder_name="appium",
        install_steps=[
            ["python3", "-m", "venv", ".venv"],
            [".venv/bin/pip", "install", "--quiet", "--upgrade", "pip"],
            [".venv/bin/pip", "install", "--quiet",
             "pytest", "Appium-Python-Client", "selenium"],
        ],
        check_argv=[".venv/bin/python", "-c",
                    "import appium; print('Appium', getattr(appium, '__version__', '?'))"],
        description=(
            "Mobile-flow runner. Picked by orchestrator when APP_INDEX."
            "mobile_relevant is true. Requires an Appium server running locally "
            "(brew install appium) and platform tools (adb / Xcode) on the host."
        ),
    ),
]


def specs_root(spec: FrameworkSpec) -> Path:
    """Where this framework's spec files live (auto-test/ goes inside this)."""
    return QAFLOW_ROOT / spec.workspace_rel / spec.specs_subdir


def by_id(framework_id: str) -> FrameworkSpec | None:
    return next((f for f in REGISTRY if f.id == framework_id), None)


# ---------------------------------------------------------------------------
# Workspace + install
# ---------------------------------------------------------------------------

def workspace_dir(spec: FrameworkSpec) -> Path:
    return QAFLOW_ROOT / spec.workspace_rel


def is_installed(spec: FrameworkSpec) -> tuple[bool, str | None]:
    """Run the framework's check command. Return (installed, version)."""
    wd = workspace_dir(spec)
    if not wd.exists():
        return (False, None)
    try:
        res = subprocess.run(
            spec.check_argv, cwd=str(wd), capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return (False, None)
    if res.returncode != 0:
        return (False, None)
    version = (res.stdout or res.stderr).strip().splitlines()[0] if (res.stdout or res.stderr) else None
    return (True, version)


# ---------------------------------------------------------------------------
# Install jobs — in-process, polled via REST
# ---------------------------------------------------------------------------

@dataclass
class InstallJob:
    framework_id: str
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    log: list[str] = field(default_factory=list)
    error: str | None = None


_jobs: dict[str, InstallJob] = {}
_jobs_lock = threading.Lock()


def get_job(framework_id: str) -> InstallJob | None:
    with _jobs_lock:
        return _jobs.get(framework_id)


def _run_install(spec: FrameworkSpec, job: InstallJob) -> None:
    """Execute install_steps inside the workspace, streaming output to job.log."""
    wd = workspace_dir(spec)
    wd.mkdir(parents=True, exist_ok=True)
    job.status = "running"
    job.log.append(f"[install] workspace = {wd}")

    for step in spec.install_steps:
        cmd_str = " ".join(step)
        job.log.append(f"$ {cmd_str}")
        try:
            proc = subprocess.Popen(
                step,
                cwd=str(wd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout
            for line in proc.stdout:
                stripped = line.rstrip("\n")
                if stripped:
                    job.log.append(stripped)
                if len(job.log) > 400:  # cap memory
                    job.log = job.log[-400:]
            proc.wait()
            if proc.returncode != 0:
                job.status = "failed"
                job.error = f"`{cmd_str}` exited with code {proc.returncode}"
                job.log.append(f"[install] step failed (rc={proc.returncode})")
                job.finished_at = time.time()
                return
        except FileNotFoundError as e:
            job.status = "failed"
            job.error = f"command not found: {step[0]} ({e})"
            job.log.append(f"[install] {job.error}")
            job.finished_at = time.time()
            return
        except Exception as e:
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
            job.log.append(f"[install] {job.error}")
            job.finished_at = time.time()
            return

    # Sanity check the install actually worked.
    ok, version = is_installed(spec)
    if not ok:
        job.status = "failed"
        job.error = "install finished but the version-check command did not succeed"
        job.log.append(f"[install] verification failed (cwd={wd})")
    else:
        job.status = "succeeded"
        job.log.append(f"[install] verified — {version}")
    job.finished_at = time.time()


def start_install(framework_id: str) -> InstallJob:
    spec = by_id(framework_id)
    if not spec:
        raise ValueError(f"unknown framework: {framework_id}")
    with _jobs_lock:
        existing = _jobs.get(framework_id)
        if existing and existing.status == "running":
            return existing
        job = InstallJob(framework_id=framework_id)
        _jobs[framework_id] = job
    threading.Thread(
        target=_run_install, args=(spec, job), daemon=True, name=f"install-{framework_id}",
    ).start()
    return job


# ---------------------------------------------------------------------------
# Public listing
# ---------------------------------------------------------------------------

def list_with_status() -> list[dict]:
    out = []
    for spec in REGISTRY:
        installed, version = is_installed(spec)
        job = get_job(spec.id)
        out.append({
            "id":             spec.id,
            "name":           spec.name,
            "language":       spec.language,
            "extension":      spec.extension,
            "workspace":      spec.workspace_rel,
            "specs_subdir":   spec.specs_subdir,
            "folder_name":    spec.folder_name,
            "description":    spec.description,
            "installed":      installed,
            "version":        version,
            "install_status": job.status if job else None,
            "install_error":  job.error if job else None,
        })
    return out


def install_log(framework_id: str, tail: int = 50) -> dict:
    job = get_job(framework_id)
    if not job:
        return {"status": "not-started", "log": []}
    return {
        "status":      job.status,
        "started_at":  job.started_at,
        "finished_at": job.finished_at,
        "error":       job.error,
        "log":         job.log[-tail:],
        "duration_s":  round((job.finished_at or time.time()) - job.started_at, 1),
    }
