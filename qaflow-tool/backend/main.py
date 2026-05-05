"""QAFLOW AI — FastAPI backend.

Routes:

  Auth:
    POST   /api/auth/login                -> {token, user}
    GET    /api/auth/me                   -> current user
    POST   /api/auth/logout               -> revoke session
    GET    /api/users                     -> list users (optional ?role= filter)

  AI auto-fix loop (existing demo):
    POST   /api/runs                      -> trigger UI test run     [pm | automation_engineer]
    GET    /api/runs                      -> list runs               [authenticated]
    GET    /api/bugs                      -> list AI-detected bugs   [authenticated]
    GET    /api/bugs/{bug_uid}            -> bug detail              [authenticated]
    POST   /api/bugs/{bug_uid}/approve    -> merge fix               [developer]
    POST   /api/bugs/{bug_uid}/reject     -> reject fix              [developer]
    GET    /api/screenshots/{name}        -> serve PNG               [public]
    WS     /ws                            -> live dashboard events

  Manual-tester → developer flow (new):
    GET    /api/manual-bugs               -> list (role-filtered)
    POST   /api/manual-bugs               -> create new bug         [manual_tester]
    GET    /api/manual-bugs/{id}          -> detail with comments
    POST   /api/manual-bugs/{id}/assign   -> set assignee           [pm | manual_tester]
    POST   /api/manual-bugs/{id}/status   -> change status          [developer | pm | manual_tester]
    POST   /api/manual-bugs/{id}/comments -> add comment
"""

import asyncio
import difflib
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import ai_engine
import auth
import cypress_fix_catalog
import cypress_runner
import db
import sandbox
import test_runner

# ---------------------------------------------------------------------------
# Config & app
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
SCREENSHOT_DIR = ROOT / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

BUGGY_APP_URL = "http://localhost:3001/login.html"

app = FastAPI(title="QAFLOW AI Backend", version="1.1.0-demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# AI bug/run state — kept in-memory for the demo (resets on backend restart)
state = {
    "runs": [],
    "bugs": {},
    "cypress_runs": [],   # list of {id, status, started_at, finished_at, passed, failed, total, log, tests, screenshots}
    "buggy_app_head": None,  # last seen buggy-app HEAD (used by the watcher)
}
ws_clients: Set[WebSocket] = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _has_claude_key() -> bool:
    import os
    return bool(os.getenv("ANTHROPIC_API_KEY"))


async def _broadcast(event: dict):
    dead = []
    for ws in ws_clients:
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)


def _broadcast_threadsafe(loop: asyncio.AbstractEventLoop, event: dict):
    asyncio.run_coroutine_threadsafe(_broadcast(event), loop)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
def login(payload: dict):
    username = (payload or {}).get("username", "").strip()
    password = (payload or {}).get("password", "")
    user = db.find_user_by_username(username)
    if not user or not db.verify_password(password, user["password_hash"]):
        raise HTTPException(401, "invalid credentials")
    token = db.issue_token(user["id"])
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "full_name": user["full_name"],
        },
    }


@app.get("/api/auth/me")
def me(user: dict = Depends(auth.current_user)):
    return user


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing token")
    token = authorization.split(" ", 1)[1].strip()
    db.revoke_token(token)
    return {"ok": True}


@app.get("/api/users")
def list_users(role: str | None = None, _user: dict = Depends(auth.current_user)):
    if role:
        return db.find_users_by_role(role)
    return db.list_users()


# ---------------------------------------------------------------------------
# Health / dashboard summary
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "ai_mode": "claude" if _has_claude_key() else "mock"}


def _bug_target_repo(bug: dict) -> str:
    return (bug.get("fix") or {}).get("target_repo") or "buggy-app"


@app.get("/api/dashboard")
def dashboard(_user: dict = Depends(auth.current_user)):
    bugs = list(state["bugs"].values())
    active = sum(1 for b in bugs if b["status"] in ("DETECTED", "FIX_READY"))
    fixed = sum(1 for b in bugs if b["status"] == "MERGED")
    pending = sum(1 for b in bugs if b["status"] == "FIX_READY")
    rejected = sum(1 for b in bugs if b["status"] == "REJECTED")

    # Approval queues split by target repo (role split mirrors this).
    app_pending = sum(
        1 for b in bugs
        if b["status"] == "FIX_READY" and _bug_target_repo(b) == "buggy-app"
    )
    test_pending = sum(
        1 for b in bugs
        if b["status"] == "FIX_READY" and _bug_target_repo(b) == "cypress-tests"
    )

    last_run = state["runs"][-1] if state["runs"] else None
    last_cypress = state["cypress_runs"][-1] if state["cypress_runs"] else None

    pass_rate = 0
    if last_run:
        total = last_run.get("total", 0)
        passed = last_run.get("passed", 0)
        pass_rate = int((passed / total) * 100) if total else 0
    elif last_cypress:
        total = last_cypress.get("total", 0)
        passed = last_cypress.get("passed", 0)
        pass_rate = int((passed / total) * 100) if total else 0

    # Manual-bug summary (role-aware)
    manual_open = len(db.list_manual_bugs())
    manual_assigned_to_me = len(db.list_manual_bugs(assignee_id=_user["id"]))
    manual_reported_by_me = len(db.list_manual_bugs(reporter_id=_user["id"]))

    return {
        "active_bugs": active,
        "auto_fixed": fixed,
        "pending": pending,
        "rejected": rejected,
        "pass_rate": pass_rate,
        "ai_mode": "claude" if _has_claude_key() else "mock",
        "last_run": last_run,
        "last_cypress_run": last_cypress,
        "app_approvals_pending": app_pending,
        "test_approvals_pending": test_pending,
        "manual_open": manual_open,
        "manual_assigned_to_me": manual_assigned_to_me,
        "manual_reported_by_me": manual_reported_by_me,
    }


# ---------------------------------------------------------------------------
# AI auto-fix routes
# ---------------------------------------------------------------------------

@app.get("/api/runs")
def list_runs(_user: dict = Depends(auth.current_user)):
    return list(reversed(state["runs"]))


@app.get("/api/bugs")
def list_bugs(_user: dict = Depends(auth.current_user)):
    items = list(state["bugs"].values())
    items.sort(key=lambda b: b["created_at"], reverse=True)
    return items


@app.get("/api/bugs/{bug_uid}")
def get_bug(bug_uid: str, _user: dict = Depends(auth.current_user)):
    bug = state["bugs"].get(bug_uid)
    if not bug:
        raise HTTPException(404, "bug not found")
    return bug


@app.get("/api/screenshots/{name}")
def get_screenshot(name: str):
    p = SCREENSHOT_DIR / name
    if not p.exists() or ".." in name:
        raise HTTPException(404, "screenshot not found")
    return FileResponse(p, media_type="image/png")


def _process_run(run_id: str, loop: asyncio.AbstractEventLoop):
    def emit(evt):
        _broadcast_threadsafe(loop, evt)

    run = next((r for r in state["runs"] if r["id"] == run_id), None)
    if not run:
        return
    try:
        run["status"] = "RUNNING_TESTS"
        emit({"type": "run.update", "run": run})

        before_path = SCREENSHOT_DIR / f"run-{run_id}-before.png"
        result = test_runner.run_ui_suite_sync(BUGGY_APP_URL, before_path)

        run["before_screenshot"] = before_path.name
        run["passed"] = result["passed"]
        run["failed"] = result["failed"]
        run["total"] = result["total"]
        run["finished_at"] = _now_iso()

        bug_uids = []
        for raw_bug in result["bugs"]:
            bug_uid = uuid.uuid4().hex[:8]
            bug_id = raw_bug["id"]
            bug = {
                "uid": bug_uid,
                "id": bug_id,
                "title": raw_bug["title"],
                "type": raw_bug["type"],
                "evidence": raw_bug["evidence"],
                "run_id": run_id,
                "status": "DETECTED",
                "created_at": _now_iso(),
                "branch": f"bug/{bug_id}",
            }
            state["bugs"][bug_uid] = bug
            bug_uids.append(bug_uid)
            emit({"type": "bug.created", "bug": bug})

            try:
                bug["status"] = "AI_ANALYZING"
                emit({"type": "bug.update", "bug": bug})

                source_file = sandbox.BUGGY_APP / ai_engine.BUG_CATALOG[bug_id]["file"]
                fix = ai_engine.analyze_and_fix(bug_id, source_file)
                bug["fix"] = fix

                bug["status"] = "SANDBOX_APPLYING"
                emit({"type": "bug.update", "bug": bug})

                workdir = sandbox.prepare_branch(bug_id)
                sandbox.apply_fix(workdir, fix["file"], fix["old"], fix["new"])
                sandbox.commit_fix(workdir, bug_id, fix["title"])

                proc, port = sandbox.serve_sandbox(workdir)
                try:
                    after_path = SCREENSHOT_DIR / f"bug-{bug_uid}-after.png"
                    test_runner.screenshot_sync(f"http://localhost:{port}/login.html", after_path)
                finally:
                    sandbox.stop_sandbox(proc)

                bug["before_screenshot"] = before_path.name
                bug["after_screenshot"] = after_path.name
                bug["diff"] = sandbox.diff_for_branch(workdir)
                bug["status"] = "FIX_READY"
                emit({"type": "bug.update", "bug": bug})
            except Exception as e:
                bug["status"] = "FIX_FAILED"
                bug["error"] = f"{type(e).__name__}: {e}"
                emit({"type": "bug.update", "bug": bug})

        run["bug_uids"] = bug_uids
        run["status"] = "COMPLETED"
        emit({"type": "run.update", "run": run})
    except Exception as e:
        run["status"] = "FAILED"
        run["error"] = f"{type(e).__name__}: {e}"
        run["finished_at"] = _now_iso()
        emit({"type": "run.update", "run": run})


@app.post("/api/runs")
async def trigger_run(
    payload: dict | None = None,
    user: dict = Depends(auth.require_roles("project_manager", "automation_engineer")),
):
    suite = (payload or {}).get("suite", "ui")
    run_id = uuid.uuid4().hex[:8]
    run = {
        "id": run_id,
        "suite": suite,
        "status": "QUEUED",
        "started_at": _now_iso(),
        "bug_uids": [],
        "triggered_by": user["username"],
    }
    state["runs"].append(run)
    await _broadcast({"type": "run.created", "run": run})
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _process_run, run_id, loop)
    return run


QAFLOW_ROOT = Path(__file__).resolve().parent.parent.parent  # /Users/.../qaflow-ai


def _target_repo_path(target_repo: str) -> Path:
    """Return the absolute path of a fix's target repo."""
    if target_repo == "buggy-app":
        return sandbox.BUGGY_APP
    if target_repo == "cypress-tests":
        return cypress_runner.CYPRESS_DIR
    raise ValueError(f"unknown target_repo: {target_repo}")


def _commit_repo_for(target_repo: str) -> Path:
    """Repo where the commit lands. cypress-tests lives inside qaflow-ai monorepo."""
    if target_repo == "cypress-tests":
        return QAFLOW_ROOT
    return _target_repo_path(target_repo)


def _compute_unified_diff(file_rel: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{file_rel}",
        tofile=f"b/{file_rel}",
        n=3,
    ))


def _process_auto_fix(bug_uid: str, loop: asyncio.AbstractEventLoop):
    """Mock-mode AI auto-fix for a Cypress-detected bug.

    1. Look up fix in the catalog.
    2. Apply it to the live target file.
    3. Re-run the failing spec to verify the test now passes.
    4. On success: status FIX_READY (file stays patched, awaiting approve).
       On failure: revert and mark FIX_FAILED.
    """
    bug = state["bugs"].get(bug_uid)
    if not bug:
        return

    def emit():
        _broadcast_threadsafe(loop, {"type": "bug.update", "bug": bug})

    try:
        bug["status"] = "AI_ANALYZING"
        emit()

        fix = cypress_fix_catalog.find_fix(bug["title"], bug.get("spec"))
        fix_mode = "rule-based"

        if not fix:
            # Catalog miss → try Claude if a key is configured.
            if _has_claude_key():
                try:
                    spec_path = cypress_runner.CYPRESS_DIR / "cypress" / "e2e" / (bug.get("spec") or "")
                    fix = ai_engine.analyze_cypress_with_claude(
                        bug["title"],
                        spec_path,
                        sandbox.BUGGY_APP,
                        cypress_runner.CYPRESS_DIR,
                    )
                    fix_mode = "claude"
                except Exception as e:
                    bug["status"] = "FIX_FAILED"
                    bug["error"] = f"claude_failed: {type(e).__name__}: {e}"
                    emit()
                    return
            else:
                bug["status"] = "FIX_FAILED"
                bug["error"] = (
                    "No matching fix in mock catalog. "
                    "Set ANTHROPIC_API_KEY for live AI fallback."
                )
                emit()
                return

        # Persist proposed fix on the bug
        bug["fix"] = {
            "mode": fix_mode,
            "bug_id": bug["id"],
            "title": fix["title"],
            "type": fix["type"],
            "severity": fix["severity"],
            "file": fix["file"],
            "target_repo": fix["target_repo"],
            "old": fix["old"],
            "new": fix["new"],
            "analysis": fix["analysis"],
            "confidence": fix["confidence"],
        }

        target_root = _target_repo_path(fix["target_repo"])
        target_file = target_root / fix["file"]
        if not target_file.exists():
            bug["status"] = "FIX_FAILED"
            bug["error"] = f"target file not found: {fix['file']}"
            emit()
            return

        original_text = target_file.read_text()
        if fix["old"] not in original_text:
            bug["status"] = "FIX_FAILED"
            bug["error"] = (
                f"patch.old not found in {fix['file']} — file may have drifted"
            )
            emit()
            return

        bug["status"] = "SANDBOX_APPLYING"
        emit()

        new_text = original_text.replace(fix["old"], fix["new"], 1)
        target_file.write_text(new_text)

        # Verify by re-running the failing spec.
        bug["status"] = "VERIFYING_FIX"
        emit()

        spec = bug.get("spec")
        result = cypress_runner.run_spec(spec, lambda line: None) if spec else None
        if not result:
            bug["status"] = "FIX_FAILED"
            bug["error"] = "no spec recorded for bug — cannot verify"
            target_file.write_text(original_text)
            emit()
            return

        test_name = bug.get("test_name")
        passes = any(
            t.get("status") == "pass" and t.get("name") == test_name
            for t in result.get("tests", [])
        )

        if not passes:
            target_file.write_text(original_text)  # revert
            bug["status"] = "FIX_FAILED"
            bug["error"] = "fix applied but the test still fails — reverted"
            bug["verification"] = {
                "passed": False,
                "duration_s": result.get("duration_s"),
            }
            emit()
            return

        bug["diff"] = _compute_unified_diff(fix["file"], original_text, new_text)
        bug["verification"] = {
            "passed": True,
            "duration_s": result.get("duration_s"),
        }
        bug["status"] = "FIX_READY"
        emit()

    except Exception as e:
        bug["status"] = "FIX_FAILED"
        bug["error"] = f"{type(e).__name__}: {e}"
        emit()


@app.post("/api/bugs/{bug_uid}/auto-fix")
async def auto_fix_bug(
    bug_uid: str,
    user: dict = Depends(auth.require_roles("automation_engineer", "developer")),
):
    bug = state["bugs"].get(bug_uid)
    if not bug:
        raise HTTPException(404, "bug not found")
    if bug["status"] != "DETECTED":
        raise HTTPException(400, f"bug already in status {bug['status']}")
    if bug.get("source") != "cypress":
        raise HTTPException(400, "auto-fix only available for cypress-source bugs")
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _process_auto_fix, bug_uid, loop)
    return {"status": "started", "uid": bug_uid, "triggered_by": user["username"]}


def _merge_cypress_fix(bug: dict, fix: dict) -> bool:
    """Commit + push a Cypress-source fix to the right repo.

    The file is already patched by _process_auto_fix; this stages, commits and
    pushes. Returns True if a commit was created, False if there was nothing
    new to commit.
    """
    import subprocess as _sp

    repo = _commit_repo_for(fix["target_repo"])
    target_file = _target_repo_path(fix["target_repo"]) / fix["file"]
    file_rel_to_repo = str(target_file.relative_to(repo))

    sandbox._git(repo, "add", "--", file_rel_to_repo)

    res = _sp.run(
        ["git", "commit", "-q", "-m", f"fix(bug/{bug['id']}): {fix['title']}"],
        cwd=repo, capture_output=True, text=True,
    )
    if res.returncode != 0:
        return False  # likely "nothing to commit"

    sandbox._push_if_remote_configured(repo)
    return True


def _kick_post_change_validation(triggered_by: str, loop: asyncio.AbstractEventLoop):
    """Run the full Cypress suite after buggy-app changes.

    Each new failure surfaces as a bug (existing _process_cypress_run flow);
    the role-aware approve route then funnels test fixes to the AE.
    """
    run_id = uuid.uuid4().hex[:8]
    run = {
        "id": run_id,
        "specs": "all",
        "status": "QUEUED",
        "started_at": _now_iso(),
        "triggered_by": triggered_by,
        "passed": 0, "failed": 0, "pending": 0, "total": 0,
        "tests": [],
    }
    state["cypress_runs"].append(run)
    _broadcast_threadsafe(loop, {
        "type": "cypress.created",
        "run": _public_cypress(run),
    })
    _process_cypress_run(run_id, None, loop)


async def _watch_buggy_app():
    """Background poller: detect manual buggy-app edits made outside QAFlow.

    Polls `git rev-parse HEAD` every 30s. On a HEAD change we didn't initiate
    ourselves, kick off Cypress so any newly broken tests show up as bugs
    routed to the automation engineer.
    """
    poll_interval = 30  # seconds
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(poll_interval)
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(sandbox.BUGGY_APP),
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        prev = state.get("buggy_app_head")
        if prev is None:
            state["buggy_app_head"] = head
            continue
        if head != prev:
            state["buggy_app_head"] = head
            loop.run_in_executor(
                None,
                _kick_post_change_validation,
                "buggy-app-watcher",
                loop,
            )


@app.on_event("startup")
async def _startup():
    """Initialise the buggy-app HEAD baseline and launch the watcher."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(sandbox.BUGGY_APP),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        state["buggy_app_head"] = head
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    asyncio.create_task(_watch_buggy_app())


def _required_role_for(bug: dict) -> str:
    """Approver role depends on which repo the fix lands in.

    - cypress-tests target → automation_engineer (test code is their domain)
    - buggy-app / no target → developer (app code is their domain)
    """
    target = (bug.get("fix") or {}).get("target_repo") or "buggy-app"
    return "automation_engineer" if target == "cypress-tests" else "developer"


@app.post("/api/bugs/{bug_uid}/approve")
async def approve_bug(
    bug_uid: str,
    user: dict = Depends(auth.current_user),
):
    bug = state["bugs"].get(bug_uid)
    if not bug:
        raise HTTPException(404, "bug not found")
    if bug["status"] != "FIX_READY":
        raise HTTPException(400, f"cannot approve in status {bug['status']}")

    required = _required_role_for(bug)
    if user["role"] != required:
        target = (bug.get("fix") or {}).get("target_repo") or "buggy-app"
        raise HTTPException(
            403,
            f"only {required}s can approve fixes targeting {target}",
        )

    fix = bug["fix"]
    if bug.get("source") == "cypress":
        merged = _merge_cypress_fix(bug, fix)
    else:
        merged = sandbox.merge_to_main(
            bug_id=bug["id"],
            file_rel=fix["file"],
            old=fix["old"],
            new=fix["new"],
            message=fix["title"],
        )
    bug["status"] = "MERGED"
    bug["merged_at"] = _now_iso()
    bug["merged_by"] = user["username"]
    bug["merge_skipped"] = not merged
    await _broadcast({"type": "bug.update", "bug": bug})

    # When a developer ships an app change, kick off cypress to surface any
    # tests that drifted — those become bugs auto-routed to the AE.
    target_repo = fix.get("target_repo") or "buggy-app"
    if target_repo == "buggy-app" and merged:
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(sandbox.BUGGY_APP),
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            state["buggy_app_head"] = head  # claim it so the watcher won't re-fire
        except subprocess.CalledProcessError:
            pass
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None, _kick_post_change_validation, "post-merge", loop,
        )

    return bug


@app.post("/api/bugs/{bug_uid}/reject")
async def reject_bug(
    bug_uid: str,
    user: dict = Depends(auth.current_user),
):
    bug = state["bugs"].get(bug_uid)
    if not bug:
        raise HTTPException(404, "bug not found")
    required = _required_role_for(bug)
    if user["role"] != required:
        target = (bug.get("fix") or {}).get("target_repo") or "buggy-app"
        raise HTTPException(
            403,
            f"only {required}s can reject fixes targeting {target}",
        )
    bug["status"] = "REJECTED"
    bug["rejected_at"] = _now_iso()
    bug["rejected_by"] = user["username"]
    await _broadcast({"type": "bug.update", "bug": bug})
    return bug


# ---------------------------------------------------------------------------
# Manual bugs (manual_tester reports → developer works on them)
# ---------------------------------------------------------------------------

@app.get("/api/manual-bugs")
def list_manual_bugs(
    scope: str | None = None,
    user: dict = Depends(auth.current_user),
):
    """scope=mine for tester (their reports) / developer (assigned to them).
    Default: PM/auto see everything; tester sees their own; dev sees assigned."""
    role = user["role"]
    if scope == "mine" or role == "manual_tester":
        return db.list_manual_bugs(reporter_id=user["id"])
    if role == "developer":
        return db.list_manual_bugs(assignee_id=user["id"])
    return db.list_manual_bugs()


@app.post("/api/manual-bugs")
async def create_manual_bug(
    payload: dict,
    user: dict = Depends(auth.require_roles("manual_tester")),
):
    title = payload.get("title", "").strip()
    description = payload.get("description", "").strip()
    severity = payload.get("severity", "medium")
    page_url = payload.get("page_url")
    steps = payload.get("steps_to_reproduce")
    assignee_id = payload.get("assignee_id")
    if not title or not description:
        raise HTTPException(400, "title and description are required")
    if severity not in ("low", "medium", "high", "critical"):
        raise HTTPException(400, "invalid severity")
    bug = db.create_manual_bug(
        title=title,
        description=description,
        severity=severity,
        page_url=page_url,
        steps=steps,
        reporter_id=user["id"],
        assignee_id=int(assignee_id) if assignee_id else None,
    )
    await _broadcast({"type": "manual-bug.created", "bug": bug})
    return bug


@app.get("/api/manual-bugs/{bug_id}")
def get_manual_bug(bug_id: int, _user: dict = Depends(auth.current_user)):
    bug = db.get_manual_bug(bug_id)
    if not bug:
        raise HTTPException(404, "bug not found")
    return bug


@app.post("/api/manual-bugs/{bug_id}/assign")
async def assign_manual_bug(
    bug_id: int,
    payload: dict,
    user: dict = Depends(auth.require_roles("project_manager", "manual_tester")),
):
    assignee_id = payload.get("assignee_id")
    if assignee_id is not None:
        assignee_id = int(assignee_id)
    bug = db.assign_manual_bug(bug_id, assignee_id)
    if not bug:
        raise HTTPException(404, "bug not found")
    await _broadcast({"type": "manual-bug.update", "bug": bug})
    return bug


@app.post("/api/manual-bugs/{bug_id}/status")
async def set_manual_bug_status(
    bug_id: int,
    payload: dict,
    user: dict = Depends(auth.require_roles("developer", "project_manager", "manual_tester")),
):
    status_value = payload.get("status")
    if status_value not in ("OPEN", "IN_PROGRESS", "RESOLVED", "REJECTED"):
        raise HTTPException(400, "invalid status")
    bug = db.update_manual_bug_status(bug_id, status_value)
    if not bug:
        raise HTTPException(404, "bug not found")
    await _broadcast({"type": "manual-bug.update", "bug": bug})
    return bug


@app.post("/api/manual-bugs/{bug_id}/comments")
async def comment_manual_bug(
    bug_id: int,
    payload: dict,
    user: dict = Depends(auth.current_user),
):
    body = payload.get("body", "").strip()
    if not body:
        raise HTTPException(400, "comment body required")
    bug = db.add_comment(bug_id, user["id"], body)
    if not bug:
        raise HTTPException(404, "bug not found")
    await _broadcast({"type": "manual-bug.update", "bug": bug})
    return bug


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        await ws.send_text(json.dumps({
            "type": "snapshot",
            "runs": list(reversed(state["runs"])),
            "bugs": list(state["bugs"].values()),
        }))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)


# ---------------------------------------------------------------------------
# Cypress test runner
# ---------------------------------------------------------------------------

@app.get("/api/cypress/runs")
def list_cypress_runs(_user: dict = Depends(auth.current_user)):
    return list(reversed(state["cypress_runs"]))


@app.get("/api/cypress/runs/{run_id}")
def get_cypress_run(run_id: str, _user: dict = Depends(auth.current_user)):
    run = next((r for r in state["cypress_runs"] if r["id"] == run_id), None)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@app.get("/api/cypress/screenshots/{path:path}")
def get_cypress_screenshot(path: str):
    if ".." in path:
        raise HTTPException(400, "bad path")
    full = cypress_runner.CYPRESS_DIR / path
    if not full.exists():
        raise HTTPException(404, "screenshot not found")
    return FileResponse(full, media_type="image/png")


def _process_cypress_run(run_id: str, specs: list[str] | None, loop: asyncio.AbstractEventLoop):
    run = next((r for r in state["cypress_runs"] if r["id"] == run_id), None)
    if not run:
        return

    def emit_run():
        _broadcast_threadsafe(loop, {"type": "cypress.update", "run": _public_cypress(run)})

    def on_line(line: str):
        run["log_tail"] = (run.get("log_tail") or [])[-50:] + [line]
        # Stream a compact event for live log feed
        _broadcast_threadsafe(loop, {"type": "cypress.line", "run_id": run_id, "line": line})

    try:
        run["status"] = "RUNNING"
        emit_run()
        result = cypress_runner.run_specs(specs, on_line)
        run.update({
            "status":      "COMPLETED",
            "finished_at": _now_iso(),
            "passed":      result["passed"],
            "failed":      result["failed"],
            "pending":     result["pending"],
            "total":       result["total"],
            "tests":       result["tests"],
            "exit_code":   result["exit_code"],
            "duration_s":  result["duration_s"],
            "screenshots": result["screenshots"],
        })
        # Keep the full log out of WebSocket events but accessible via GET
        run["log"] = result["log"]

        # Each failing test becomes a bug entry on the dashboard.
        bug_uids = []
        for test in result["tests"]:
            if test["status"] != "fail":
                continue
            bug_uid = uuid.uuid4().hex[:8]
            bug_id = state.setdefault("next_cypress_bug_id", 5000)
            state["next_cypress_bug_id"] = bug_id + 1
            spec = test.get("spec") or "unknown.cy.js"

            screenshot = None
            for s in result.get("screenshots", []):
                if test["name"] in s or spec in s:
                    screenshot = s
                    break

            bug = {
                "uid": bug_uid,
                "id": bug_id,
                "title": test["name"],
                "type": "Cypress",
                "evidence": f"Failed in {spec}",
                "run_id": run_id,
                "status": "DETECTED",
                "created_at": _now_iso(),
                "branch": "",
                "source": "cypress",
                "spec": spec,
                "test_name": test["name"],
                "failure_screenshot": screenshot,
            }
            state["bugs"][bug_uid] = bug
            bug_uids.append(bug_uid)
            _broadcast_threadsafe(loop, {"type": "bug.created", "bug": bug})

        run["bug_uids"] = bug_uids
    except Exception as e:
        run["status"] = "FAILED"
        run["error"] = f"{type(e).__name__}: {e}"
        run["finished_at"] = _now_iso()
    finally:
        emit_run()


def _public_cypress(run: dict) -> dict:
    """Strip large fields for broadcast events."""
    return {k: v for k, v in run.items() if k not in ("log",)}


@app.post("/api/cypress/runs")
async def trigger_cypress_run(
    payload: dict | None = None,
    user: dict = Depends(auth.require_roles("project_manager", "automation_engineer")),
):
    specs = (payload or {}).get("specs")  # None or list[str]
    run_id = uuid.uuid4().hex[:8]
    run = {
        "id": run_id,
        "specs": specs or "all",
        "status": "QUEUED",
        "started_at": _now_iso(),
        "triggered_by": user["username"],
        "passed": 0, "failed": 0, "pending": 0, "total": 0,
        "tests": [],
    }
    state["cypress_runs"].append(run)
    await _broadcast({"type": "cypress.created", "run": _public_cypress(run)})
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _process_cypress_run, run_id, specs, loop)
    return _public_cypress(run)


@app.get("/")
def root():
    return JSONResponse({
        "service": "qaflow-ai-backend",
        "version": "1.1.0-demo",
        "ai_mode": "claude" if _has_claude_key() else "mock",
        "buggy_app": BUGGY_APP_URL,
    })
