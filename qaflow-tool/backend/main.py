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
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import ai_engine
import audit
import auth
import cypress_fix_catalog
import cypress_runner
import db
import frameworks
import hooks
import llm_cache
import sandbox
import test_runner
import test_writer

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
            hooks.fire("on_bug_detected", bug)

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
                        bug_uid=bug_uid,
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

        # Take a BEFORE screenshot of the page where the bug visually shows.
        screenshot_url = fix.get("screenshot_url")
        if screenshot_url:
            try:
                before_path = SCREENSHOT_DIR / f"bug-{bug_uid}-before.png"
                test_runner.screenshot_sync(screenshot_url, before_path)
                bug["before_screenshot"] = before_path.name
                emit()
            except Exception:
                pass  # non-fatal — fix flow continues without screenshot

        bug["status"] = "SANDBOX_APPLYING"
        emit()

        # Branch flow only applies to the buggy-app — cypress-tests fixes still
        # commit straight to the qaflow-ai monorepo on main.
        is_app_fix = fix["target_repo"] == "buggy-app"
        branch_name = f"qaflow/bug-{bug['id']}" if is_app_fix else None
        if is_app_fix:
            try:
                sandbox.open_live_branch(sandbox.BUGGY_APP, branch_name)
            except subprocess.CalledProcessError as e:
                bug["status"] = "FIX_FAILED"
                bug["error"] = f"branch_open_failed: {e.stderr or e}"
                emit()
                return
            bug["branch"] = branch_name

        new_text = original_text.replace(fix["old"], fix["new"], 1)
        target_file.write_text(new_text)

        # Verify by re-running the failing spec while the patch is on disk.
        bug["status"] = "VERIFYING_FIX"
        emit()

        spec = bug.get("spec")
        result = cypress_runner.run_spec(spec, lambda line: None) if spec else None
        if not result:
            bug["status"] = "FIX_FAILED"
            bug["error"] = "no spec recorded for bug — cannot verify"
            target_file.write_text(original_text)
            if is_app_fix:
                try: sandbox.back_to_main(sandbox.BUGGY_APP)
                except Exception: pass
            emit()
            return

        test_name = bug.get("test_name")
        passes = any(
            t.get("status") == "pass" and t.get("name") == test_name
            for t in result.get("tests", [])
        )

        if not passes:
            bug["status"] = "FIX_FAILED"
            bug["error"] = "fix applied but the test still fails — reverted"
            bug["verification"] = {
                "passed": False,
                "duration_s": result.get("duration_s"),
            }
            target_file.write_text(original_text)
            if is_app_fix:
                try:
                    sandbox.back_to_main(sandbox.BUGGY_APP)
                    sandbox.discard_branch(sandbox.BUGGY_APP, branch_name)
                except Exception:
                    pass
            emit()
            return

        bug["diff"] = _compute_unified_diff(fix["file"], original_text, new_text)
        bug["verification"] = {
            "passed": True,
            "duration_s": result.get("duration_s"),
        }

        # Take the AFTER screenshot while the patched file is still on disk.
        if screenshot_url:
            try:
                after_path = SCREENSHOT_DIR / f"bug-{bug_uid}-after.png"
                test_runner.screenshot_sync(screenshot_url, after_path)
                bug["after_screenshot"] = after_path.name
            except Exception:
                pass

        if is_app_fix:
            try:
                sandbox.commit_and_push_branch(
                    sandbox.BUGGY_APP,
                    branch_name,
                    fix["file"],
                    f"fix(bug/{bug['id']}): {fix['title']}",
                )
                bug["branch_pushed"] = True
            except subprocess.CalledProcessError as e:
                bug["branch_pushed"] = False
                bug["error"] = f"branch_push_failed: {e.stderr or e}"
            # Switch the live working tree back to main so the buggy-app server
            # serves the unfixed version until the dev approves the merge.
            try:
                sandbox.back_to_main(sandbox.BUGGY_APP)
            except Exception:
                pass

        bug["status"] = "FIX_READY"
        emit()
        hooks.fire("on_fix_ready", bug, bug["fix"])

        # Audit the whole auto-fix pipeline (catalog or claude already
        # logged inside ai_engine; this captures the rules-only path).
        if fix_mode == "rule-based":
            audit.record(
                "cypress_fix", success=True,
                bug_uid=bug_uid, engine="rule-based",
                summary=f"target={fix.get('target_repo')} file={fix.get('file')}",
            )

    except Exception as e:
        bug["status"] = "FIX_FAILED"
        audit.record(
            "cypress_fix", success=False,
            bug_uid=bug_uid, engine="unknown",
            error=f"{type(e).__name__}: {e}",
        )
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
    """Initialise sqlite tables, hook subscribers, and the buggy-app watcher."""
    # Migration: create the AI infra tables on first boot.
    audit.init()
    llm_cache.init()
    # Default hook subscribers (audit + log).
    hooks.install_defaults()

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
    target_repo = fix.get("target_repo") or "buggy-app"
    branch = bug.get("branch")

    if bug.get("source") == "cypress" and target_repo == "buggy-app" and branch:
        # Branch flow: fast-forward merge the bug branch into main + push.
        merged = sandbox.merge_branch_into_main(sandbox.BUGGY_APP, branch)
    elif bug.get("source") == "cypress":
        # cypress-tests target — commit straight to the monorepo.
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
    hooks.fire("on_merged", bug, fix, merged)

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

    # Discard the throwaway fix branch (locally + on origin) so the repo
    # doesn't accumulate stale auto-fix branches.
    branch = bug.get("branch")
    target_repo = (bug.get("fix") or {}).get("target_repo")
    if branch and target_repo == "buggy-app":
        try:
            sandbox.discard_branch(sandbox.BUGGY_APP, branch)
        except Exception:
            pass

    await _broadcast({"type": "bug.update", "bug": bug})
    return bug


# ---------------------------------------------------------------------------
# AI Test Cover Engine (automation_engineer)
# ---------------------------------------------------------------------------

@app.post("/api/ai/test-writer/scan")
async def ai_test_writer_scan(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    payload = payload or {}
    url = payload.get("url", "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must start with http:// or https://")
    auth_cfg = payload.get("auth")  # optional login flow
    capture_baseline = bool(payload.get("capture_baseline", False))
    loop = asyncio.get_running_loop()
    try:
        scan = await loop.run_in_executor(
            None, test_writer.scan_page, url, auth_cfg, capture_baseline,
        )
    except Exception as e:
        raise HTTPException(502, f"scan failed: {type(e).__name__}: {e}")
    return scan


@app.post("/api/ai/test-writer/crawl")
async def ai_test_writer_crawl(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    payload = payload or {}
    url = payload.get("url", "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must start with http:// or https://")
    max_pages = int(payload.get("max_pages") or 8)
    same_origin = bool(payload.get("same_origin", True))
    auth_cfg = payload.get("auth")
    loop = asyncio.get_running_loop()
    try:
        pages = await loop.run_in_executor(
            None, test_writer.crawl_pages, url, max_pages, same_origin, auth_cfg,
        )
    except Exception as e:
        raise HTTPException(502, f"crawl failed: {type(e).__name__}: {e}")
    return {"pages": pages, "count": len(pages)}


@app.post("/api/ai/test-writer/coverage")
async def ai_test_writer_coverage(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Estimate which tests a generation would produce — call after /scan."""
    scan = (payload or {}).get("scan")
    focus = (payload or {}).get("test_focus") or ["smoke"]
    if not scan:
        raise HTTPException(400, "scan is required")
    return test_writer.estimate_coverage(scan, focus)


@app.post("/api/ai/test-writer/score")
async def ai_test_writer_score(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Quality scorecard for an arbitrary {files: {...}} bundle."""
    files = (payload or {}).get("files")
    if not files or not isinstance(files, dict):
        raise HTTPException(400, "files (dict) is required")
    return test_writer.score_suite({str(k): str(v) for k, v in files.items()})


@app.post("/api/ai/test-writer/diff")
async def ai_test_writer_diff(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Compare a fresh generation against the previous bundle on disk."""
    payload = payload or {}
    files = payload.get("files")
    if not files or not isinstance(files, dict):
        raise HTTPException(400, "files (dict) is required")
    project = payload.get("project")
    framework = payload.get("framework", "cypress-js")
    env = payload.get("env")
    if not project:
        raise HTTPException(400, "project is required")
    return test_writer.diff_against_existing(
        framework, project, env,
        {str(k): str(v) for k, v in files.items()},
    )


@app.post("/api/ai/test-writer/run")
async def ai_test_writer_run(
    payload: dict,
    user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Execute a saved bundle in its framework's runner."""
    payload = payload or {}
    project = payload.get("project")
    framework = payload.get("framework", "cypress-js")
    env = payload.get("env")
    if not project:
        raise HTTPException(400, "project is required")
    timeout_s = int(payload.get("timeout_s") or 300)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, test_writer.run_suite, framework, project, env, timeout_s,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"run failed: {type(e).__name__}: {e}")
    audit.record(
        "test_writer_run", success=result["exit_code"] == 0,
        framework_id=result.get("framework_id"),
        engine="runner",
        duration_ms=int(result["duration_s"] * 1000),
        summary=f"{result.get('passed','?')}/{(result.get('passed') or 0) + (result.get('failed') or 0)} passed",
    )
    return result


@app.post("/api/ai/test-writer/generate")
async def ai_test_writer_generate(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    if not payload or "url" not in payload:
        raise HTTPException(400, "url is required")

    opts = test_writer.GenerateOptions(
        url=payload["url"],
        framework=payload.get("framework", "cypress"),
        language=payload.get("language", "javascript"),
        mode=payload.get("mode", "black-box"),
        test_focus=payload.get("test_focus") or ["smoke"],
        source_paste=payload.get("source_paste"),
        source_repo_url=payload.get("source_repo_url"),
        extra_instructions=payload.get("extra_instructions"),
    )

    # If the caller already scanned, reuse it. Otherwise, scan now.
    scan = payload.get("scan")
    if not scan:
        loop = asyncio.get_running_loop()
        try:
            scan = await loop.run_in_executor(None, test_writer.scan_page, opts.url)
        except Exception as e:
            raise HTTPException(502, f"scan failed: {type(e).__name__}: {e}")

    # Generation can take 10s+ when Claude is in the loop — push to a worker
    # thread so the event loop stays responsive.
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, test_writer.generate_tests, scan, opts,
    )
    result["scan_summary"] = scan.get("counts")
    result["url"] = opts.url
    return result


@app.post("/api/ai/test-writer/save")
async def ai_test_writer_save(
    payload: dict,
    user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    payload = payload or {}
    framework = payload.get("framework", "cypress")
    project = payload.get("project")
    env = payload.get("env")
    if not project:
        raise HTTPException(400, "project is required")

    # Multi-file path: caller sends {files: {relpath: content, ...}}
    files = payload.get("files")
    if files and isinstance(files, dict) and len(files) > 0:
        try:
            saved = test_writer.save_bundle(
                files={str(k): str(v) for k, v in files.items()},
                framework=framework, project=project, env=env,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))

        # Optional visual baseline — caller can pass a base64 PNG that
        # was captured at scan time; we drop it next to the test files.
        baseline = payload.get("baseline_b64")
        if baseline:
            wrote = test_writer.write_visual_baseline_to_bundle(
                framework, project, env, baseline,
            )
            if wrote: saved["baseline_path"] = wrote

        saved["saved_by"] = user["username"]
        saved["saved_at"] = _now_iso()
        return saved

    # Legacy single-file path: {code, filename}
    code = payload.get("code")
    filename = payload.get("filename")
    if not code or not filename:
        raise HTTPException(400, "either `files` (dict) or `code`+`filename` is required")
    saved = test_writer.save_spec(filename, code, framework, project=project, env=env)
    saved["saved_by"] = user["username"]
    saved["saved_at"] = _now_iso()
    return saved


@app.get("/api/ai/test-writer/projects")
async def ai_test_writer_projects(
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    return test_writer.list_projects()


# ---------------------------------------------------------------------------
# Multi-step pipeline (QAFLOW v2): discovery → smoke → e2e → negative → ...
# Each endpoint is one step; orchestrator.py persists state between calls.
# ---------------------------------------------------------------------------

import app_index            # noqa: E402  (intentional late import — keeps module surface small)
import orchestrator         # noqa: E402
import source_extractors    # noqa: E402
import fakemail             # noqa: E402
import perf_runner          # noqa: E402


@app.post("/api/ai/test-writer/discover")
async def ai_test_writer_discover(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """STEP 1 — analyze the target and produce APP_INDEX.

    Body:
      {
        "project":         "<required slug>",
        "mode":            "product" | "git" | "pdf",
        "url":             "<for product mode>",
        "repo_url":        "<for git mode>",
        "pdf_path":        "<for pdf mode>",
        "auth":            { ... }      # optional login config (same shape as /scan)
        "test_users":      [ ... ]      # optional pre-provisioned accounts
        "max_pages":       8,           # crawl budget
        "extra_instructions": "..."     # passed through if needed
      }

    Returns the APP_INDEX JSON and persists it to
    tests/{project}/.qaflow/app_index.json.
    """
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    project_slug = app_index.project_slug_from(project)

    mode = (payload.get("mode") or "product").strip()
    if mode not in ("product", "git", "pdf"):
        raise HTTPException(400, "mode must be one of: product, git, pdf")

    url = (payload.get("url") or "").strip() or None
    repo_url = (payload.get("repo_url") or "").strip() or None
    pdf_path = (payload.get("pdf_path") or "").strip() or None

    if mode == "product" and not (url and url.startswith(("http://", "https://"))):
        raise HTTPException(400, "product mode requires a url (http:// or https://)")
    if mode == "git" and not repo_url:
        raise HTTPException(400, "git mode requires repo_url")
    if mode == "pdf" and not pdf_path:
        raise HTTPException(400, "pdf mode requires pdf_path")

    max_pages = int(payload.get("max_pages") or 8)
    auth_cfg = payload.get("auth")
    test_users = payload.get("test_users") or []

    orchestrator.mark_step_started(project_slug, "DISCOVERY")
    await _broadcast({
        "type": "pipeline.step_started",
        "project": project_slug,
        "step": "DISCOVERY",
    })

    # ------------------------------------------------------------------
    # Gather inputs by mode. For now, product mode is fully wired;
    # git/pdf gather a stub block — to be expanded in Week 3.
    # ------------------------------------------------------------------
    scan: dict = {}
    crawl_pages_list: list[dict] = []
    git_index_text: str | None = None
    pdf_excerpt_text: str | None = None

    loop = asyncio.get_running_loop()
    try:
        if mode == "product":
            scan = await loop.run_in_executor(
                None,
                lambda: test_writer.scan_page(
                    url, auth_cfg, False, True,   # capture_network=True
                ),
            )
            crawl_pages_list = await loop.run_in_executor(
                None,
                lambda: test_writer.crawl_pages(
                    url, max_pages, True, auth_cfg, True,
                ),
            )
        elif mode == "git":
            git_index_text = await loop.run_in_executor(
                None,
                lambda: source_extractors.extract_git_index(repo_url),
            )
        elif mode == "pdf":
            pdf_excerpt_text = await loop.run_in_executor(
                None,
                lambda: source_extractors.extract_pdf(pdf_path),
            )
    except Exception as e:
        orchestrator.record_step_result(
            project_slug, "DISCOVERY",
            success=False, error=f"input_gather_failed: {type(e).__name__}: {e}",
        )
        await _broadcast({
            "type": "pipeline.step_failed", "project": project_slug,
            "step": "DISCOVERY", "error": str(e),
        })
        raise HTTPException(502, f"input gather failed: {e}")

    if not os.getenv("ANTHROPIC_API_KEY"):
        orchestrator.record_step_result(
            project_slug, "DISCOVERY",
            success=False, error="anthropic_api_key_missing",
        )
        raise HTTPException(503, "ANTHROPIC_API_KEY required for discovery step")

    try:
        index = await loop.run_in_executor(
            None,
            lambda: ai_engine.run_discovery(
                project_slug=project_slug,
                source_mode=mode,
                target_url=url,
                scan=scan,
                crawl_pages_list=crawl_pages_list,
                auth_config=auth_cfg,
                test_users=test_users,
                git_index=git_index_text,
                pdf_excerpt=pdf_excerpt_text,
                crawl_max_pages=max_pages,
            ),
        )
    except Exception as e:
        orchestrator.record_step_result(
            project_slug, "DISCOVERY",
            success=False, error=f"llm_failed: {type(e).__name__}: {e}",
        )
        await _broadcast({
            "type": "pipeline.step_failed", "project": project_slug,
            "step": "DISCOVERY", "error": str(e),
        })
        raise HTTPException(502, f"discovery failed: {e}")

    # Persist + advance state.
    index["project_slug"] = project_slug
    app_index.save(project_slug, index)

    # Seed the traffic dump with what we observed during scan + crawl.
    # api-discovery will read this later to enrich the OpenAPI synthesis.
    seed_traffic: list[dict] = list(scan.get("network_requests") or [])
    for page in (crawl_pages_list or []):
        seed_traffic.extend(page.get("network_requests") or [])
    traffic_written = app_index.append_traffic(project_slug, seed_traffic)

    state = orchestrator.record_step_result(
        project_slug, "DISCOVERY",
        success=True,
        summary=f"pages={len(index.get('pages') or [])}",
        files_generated=None,
        artifacts={
            "app_index_path": str(app_index.index_path(project_slug)),
            "pages_count": len(index.get("pages") or []),
            "apis_count": len(index.get("discovered_apis") or []),
            "traffic_records_seeded": traffic_written,
        },
    )

    await _broadcast({
        "type": "pipeline.step_completed",
        "project": project_slug,
        "step": "DISCOVERY",
        "state": state.current_state,
        "blocked_reason": state.blocked_reason,
    })

    return {
        "project": project_slug,
        "app_index": index,
        "orchestrator": orchestrator.progress_snapshot(project_slug),
    }


@app.post("/api/ai/test-writer/smoke")
async def ai_test_writer_smoke(
    payload: dict,
    user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """STEP 2 — generate the smoke test suite from APP_INDEX.

    Body:
      {
        "project":   "<required slug — must already have an app_index.json>",
        "framework": "cypress-js" | "playwright-js" | "robot-py" | ...
        "env":       "<optional sub-folder under {project}-{framework}>"
      }
    """
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    project_slug = app_index.project_slug_from(project)

    index = app_index.load(project_slug)
    if index is None:
        raise HTTPException(409, "no app_index found — run /discover first")

    framework_id = payload.get("framework") or "cypress-js"
    spec = frameworks.by_id(framework_id)
    if not spec:
        raise HTTPException(400, f"unknown framework: {framework_id}")

    engine_for_prompt = {
        "cypress-js": "cypress", "cypress-ts": "cypress",
        "playwright-js": "playwright",
        "pytest-playwright": "pytest-playwright",
        "robot-py": "robot",
        "selenium-py": "selenium",
    }.get(framework_id, "cypress")

    base_url = (
        (index.get("application") or {}).get("base_url")
        or (index.get("source") or {}).get("url")
        or "http://localhost:3000"
    )

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY required for smoke generation")

    orchestrator.mark_step_started(project_slug, "SMOKE_GEN")
    await _broadcast({
        "type": "pipeline.step_started",
        "project": project_slug, "step": "SMOKE_GEN",
    })

    slice_ = app_index.slice_for_prompt(index, "smoke_gen")

    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            lambda: ai_engine.run_smoke_generation(
                project_slug=project_slug,
                framework=engine_for_prompt,
                language=spec.language,
                framework_folder=spec.folder_name,
                base_url=base_url,
                app_index_slice=slice_,
            ),
        )
    except Exception as e:
        orchestrator.record_step_result(
            project_slug, "SMOKE_GEN",
            success=False, error=f"llm_failed: {type(e).__name__}: {e}",
        )
        await _broadcast({
            "type": "pipeline.step_failed", "project": project_slug,
            "step": "SMOKE_GEN", "error": str(e),
        })
        raise HTTPException(502, f"smoke generation failed: {e}")

    files = data.get("files") or {}
    if not files:
        orchestrator.record_step_result(
            project_slug, "SMOKE_GEN",
            success=False, error="no_files_returned",
        )
        raise HTTPException(502, "smoke generation returned no files")

    env = (payload.get("env") or "").strip() or None
    try:
        saved = test_writer.save_bundle(
            files={str(k): str(v) for k, v in files.items()},
            framework=framework_id,
            project=project_slug,
            env=env,
        )
    except ValueError as e:
        orchestrator.record_step_result(
            project_slug, "SMOKE_GEN",
            success=False, error=f"save_failed: {e}",
        )
        raise HTTPException(400, str(e))

    state = orchestrator.record_step_result(
        project_slug, "SMOKE_GEN",
        success=True,
        summary=data.get("summary") or "",
        files_generated=len(files),
        artifacts={
            "bundle_root": saved.get("bundle_root"),
            "framework": framework_id,
            "expected_pass_rate_pct": data.get("expected_pass_rate_pct"),
            "fragility_notes": data.get("fragility_notes") or [],
        },
    )

    await _broadcast({
        "type": "pipeline.step_completed",
        "project": project_slug,
        "step": "SMOKE_GEN",
        "state": state.current_state,
        "files_count": len(files),
    })

    return {
        "project": project_slug,
        "framework": framework_id,
        "saved": saved,
        "summary": data.get("summary"),
        "expected_pass_rate_pct": data.get("expected_pass_rate_pct"),
        "fragility_notes": data.get("fragility_notes") or [],
        "deferred_to_e2e": data.get("deferred_to_e2e") or [],
        "files": list(files.keys()),
        "orchestrator": orchestrator.progress_snapshot(project_slug),
    }


def _bundle_root(project_slug: str, framework_id: str, env: str | None = None) -> Path:
    """Resolve the bundle root path for a project+framework (read-only helper)."""
    spec = frameworks.by_id(framework_id)
    if not spec:
        raise ValueError(f"unknown framework: {framework_id}")
    root = frameworks.QAFLOW_ROOT / "tests" / project_slug / f"{project_slug}-{spec.folder_name}"
    if env:
        root = root / env
    return root


def _list_relpaths(root: Path, subdir: str | None = None) -> list[str]:
    """Walk root[/subdir] and return relative paths (sorted) — used to feed
    'existing artifacts' / 'existing spec filenames' into prompts so the AI
    knows what NOT to duplicate."""
    base = root / subdir if subdir else root
    if not base.exists():
        return []
    out: list[str] = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            out.append(str(p.relative_to(root)))
    return out


def _last_smoke_run(project_slug: str) -> dict | None:
    """Find the most recent SMOKE_RUN entry in orchestrator history."""
    snap = orchestrator.progress_snapshot(project_slug)
    for h in reversed(snap.get("history") or []):
        if h.get("step") == "SMOKE_RUN" and h.get("success"):
            return h
    return None


@app.post("/api/ai/test-writer/e2e")
async def ai_test_writer_e2e(
    payload: dict,
    user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """STEP 3 — generate the E2E journey suite from APP_INDEX.

    Pre-conditions:
      - APP_INDEX exists on disk (run /discover)
      - SMOKE_RUN passed the gate (orchestrator advanced past SMOKE_GATE).
        If smoke hasn't been run yet, this endpoint accepts a `force=true`
        flag to skip the gate (for development convenience).
    """
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    project_slug = app_index.project_slug_from(project)

    index = app_index.load(project_slug)
    if index is None:
        raise HTTPException(409, "no app_index found — run /discover first")

    framework_id = payload.get("framework") or "cypress-js"
    spec = frameworks.by_id(framework_id)
    if not spec:
        raise HTTPException(400, f"unknown framework: {framework_id}")

    snap = orchestrator.progress_snapshot(project_slug)
    if snap["current_state"] in ("INIT", "DISCOVERY", "SMOKE_GEN") and not payload.get("force"):
        raise HTTPException(
            409,
            f"orchestrator is at {snap['current_state']} — smoke must complete first "
            f"(or pass force=true to override)",
        )

    smoke_meta = _last_smoke_run(project_slug) or {}
    smoke_pass_rate = int(smoke_meta.get("pass_rate_pct") or 100)
    smoke_duration_s = float((smoke_meta.get("finished_at") or 0) - (smoke_meta.get("started_at") or 0))
    smoke_spec_count = int((smoke_meta.get("artifacts") or {}).get("specs_executed") or 0)

    env = (payload.get("env") or "").strip() or None
    bundle_root = _bundle_root(project_slug, framework_id, env)
    existing_artifacts = _list_relpaths(bundle_root)

    engine_for_prompt = {
        "cypress-js": "cypress", "cypress-ts": "cypress",
        "playwright-js": "playwright",
        "pytest-playwright": "pytest-playwright",
        "robot-py": "robot", "selenium-py": "selenium",
    }.get(framework_id, "cypress")
    base_url = (
        (index.get("application") or {}).get("base_url")
        or (index.get("source") or {}).get("url")
        or "http://localhost:3000"
    )

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY required for e2e generation")

    orchestrator.mark_step_started(project_slug, "E2E_GEN")
    await _broadcast({"type": "pipeline.step_started", "project": project_slug, "step": "E2E_GEN"})

    slice_ = app_index.slice_for_prompt(index, "e2e_gen")
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            lambda: ai_engine.run_e2e_generation(
                project_slug=project_slug,
                framework=engine_for_prompt, language=spec.language,
                framework_folder=spec.folder_name, base_url=base_url,
                app_index_json=slice_,
                smoke_pass_rate=smoke_pass_rate,
                smoke_duration_s=smoke_duration_s,
                smoke_spec_count=smoke_spec_count,
                existing_artifacts=existing_artifacts,
            ),
        )
    except Exception as e:
        orchestrator.record_step_result(project_slug, "E2E_GEN",
            success=False, error=f"llm_failed: {type(e).__name__}: {e}")
        await _broadcast({"type": "pipeline.step_failed", "project": project_slug,
                          "step": "E2E_GEN", "error": str(e)})
        raise HTTPException(502, f"e2e generation failed: {e}")

    files = data.get("files") or {}
    if not files:
        orchestrator.record_step_result(project_slug, "E2E_GEN",
            success=False, error="no_files_returned")
        raise HTTPException(502, "e2e generation returned no files")

    try:
        saved = test_writer.save_bundle(
            files={str(k): str(v) for k, v in files.items()},
            framework=framework_id, project=project_slug, env=env,
        )
    except ValueError as e:
        orchestrator.record_step_result(project_slug, "E2E_GEN",
            success=False, error=f"save_failed: {e}")
        raise HTTPException(400, str(e))

    state = orchestrator.record_step_result(
        project_slug, "E2E_GEN", success=True,
        summary=data.get("summary") or "",
        files_generated=len(files),
        artifacts={
            "bundle_root": saved.get("bundle_root"),
            "framework": framework_id,
            "expected_pass_rate_pct": data.get("expected_pass_rate_pct"),
            "journeys_covered": data.get("journeys_covered") or [],
            "skipped_journeys": data.get("skipped_journeys") or [],
        },
    )
    await _broadcast({"type": "pipeline.step_completed", "project": project_slug,
                      "step": "E2E_GEN", "state": state.current_state,
                      "files_count": len(files)})

    return {
        "project": project_slug, "framework": framework_id,
        "saved": saved,
        "summary": data.get("summary"),
        "journeys_covered": data.get("journeys_covered") or [],
        "skipped_journeys": data.get("skipped_journeys") or [],
        "expected_pass_rate_pct": data.get("expected_pass_rate_pct"),
        "fragility_notes": data.get("fragility_notes") or [],
        "files": list(files.keys()),
        "orchestrator": orchestrator.progress_snapshot(project_slug),
    }


@app.post("/api/ai/test-writer/negative")
async def ai_test_writer_negative(
    payload: dict,
    user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """STEP 4 — generate the negative/edge-case suite from APP_INDEX.

    Pre-conditions: APP_INDEX present. Soft-checked: E2E_GEN completed
    (so it can read existing e2e specs); pass force=true to override.
    """
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    project_slug = app_index.project_slug_from(project)

    index = app_index.load(project_slug)
    if index is None:
        raise HTTPException(409, "no app_index found — run /discover first")

    framework_id = payload.get("framework") or "cypress-js"
    spec = frameworks.by_id(framework_id)
    if not spec:
        raise HTTPException(400, f"unknown framework: {framework_id}")

    snap = orchestrator.progress_snapshot(project_slug)
    upstream_done = snap["current_state"] in (
        "NEGATIVE_GEN", "NEGATIVE_RUN", "API_DISCOVERY", "VALIDATION", "DONE",
    )
    if not upstream_done and not payload.get("force"):
        raise HTTPException(
            409,
            f"orchestrator is at {snap['current_state']} — e2e must complete first "
            f"(or pass force=true to override)",
        )

    env = (payload.get("env") or "").strip() or None
    bundle_root = _bundle_root(project_slug, framework_id, env)
    smoke_filenames = _list_relpaths(bundle_root, "smoke")
    e2e_filenames = _list_relpaths(bundle_root, "e2e")

    engine_for_prompt = {
        "cypress-js": "cypress", "cypress-ts": "cypress",
        "playwright-js": "playwright",
        "pytest-playwright": "pytest-playwright",
        "robot-py": "robot", "selenium-py": "selenium",
    }.get(framework_id, "cypress")
    base_url = (
        (index.get("application") or {}).get("base_url")
        or (index.get("source") or {}).get("url")
        or "http://localhost:3000"
    )

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY required for negative generation")

    orchestrator.mark_step_started(project_slug, "NEGATIVE_GEN")
    await _broadcast({"type": "pipeline.step_started", "project": project_slug,
                      "step": "NEGATIVE_GEN"})

    slice_ = app_index.slice_for_prompt(index, "negative_gen")
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            lambda: ai_engine.run_negative_generation(
                project_slug=project_slug,
                framework=engine_for_prompt, language=spec.language,
                framework_folder=spec.folder_name, base_url=base_url,
                app_index_json=slice_,
                smoke_filenames=smoke_filenames,
                e2e_filenames=e2e_filenames,
                discovered_apis=index.get("discovered_apis") or [],
            ),
        )
    except Exception as e:
        orchestrator.record_step_result(project_slug, "NEGATIVE_GEN",
            success=False, error=f"llm_failed: {type(e).__name__}: {e}")
        await _broadcast({"type": "pipeline.step_failed", "project": project_slug,
                          "step": "NEGATIVE_GEN", "error": str(e)})
        raise HTTPException(502, f"negative generation failed: {e}")

    files = data.get("files") or {}
    if not files:
        orchestrator.record_step_result(project_slug, "NEGATIVE_GEN",
            success=False, error="no_files_returned")
        raise HTTPException(502, "negative generation returned no files")

    try:
        saved = test_writer.save_bundle(
            files={str(k): str(v) for k, v in files.items()},
            framework=framework_id, project=project_slug, env=env,
        )
    except ValueError as e:
        orchestrator.record_step_result(project_slug, "NEGATIVE_GEN",
            success=False, error=f"save_failed: {e}")
        raise HTTPException(400, str(e))

    state = orchestrator.record_step_result(
        project_slug, "NEGATIVE_GEN", success=True,
        summary=data.get("summary") or "",
        files_generated=len(files),
        artifacts={
            "bundle_root": saved.get("bundle_root"),
            "framework": framework_id,
            "coverage_matrix": data.get("coverage_matrix") or {},
            "not_applicable": data.get("not_applicable") or [],
            "expected_pass_rate_pct": data.get("expected_pass_rate_pct"),
        },
    )
    await _broadcast({"type": "pipeline.step_completed", "project": project_slug,
                      "step": "NEGATIVE_GEN", "state": state.current_state,
                      "files_count": len(files)})

    return {
        "project": project_slug, "framework": framework_id,
        "saved": saved,
        "summary": data.get("summary"),
        "coverage_matrix": data.get("coverage_matrix") or {},
        "not_applicable": data.get("not_applicable") or [],
        "expected_pass_rate_pct": data.get("expected_pass_rate_pct"),
        "fragility_notes": data.get("fragility_notes") or [],
        "files": list(files.keys()),
        "orchestrator": orchestrator.progress_snapshot(project_slug),
    }


import bundle_runner  # noqa: E402


async def _run_bundle_step(
    project_slug: str,
    framework_id: str,
    step: str,                   # "smoke" | "e2e" | "negative"
    env: str | None,
    orchestrator_step: str,      # "SMOKE_RUN" | "E2E_RUN" | "NEGATIVE_RUN"
    require_gate: bool,          # whether pass_rate determines BLOCKED vs forward
) -> dict:
    """Shared dispatch for the three *_run endpoints.

    Executes the framework's runner against {bundle}/{step}/, captures
    pass_rate_pct, and reports back to the orchestrator. For SMOKE_RUN and
    E2E_RUN the gate triggers a BLOCKED state if pass rate is below threshold.
    """
    bundle_root = _bundle_root(project_slug, framework_id, env)

    orchestrator.mark_step_started(project_slug, orchestrator_step)
    await _broadcast({"type": "pipeline.step_started",
                      "project": project_slug, "step": orchestrator_step})

    loop = asyncio.get_running_loop()

    def _on_line(line: str) -> None:
        # Fire-and-forget WS broadcast; coroutine-safe via the captured loop.
        asyncio.run_coroutine_threadsafe(
            _broadcast({"type": "pipeline.run_log",
                        "project": project_slug, "step": orchestrator_step,
                        "line": line[:500]}),
            loop,
        )

    try:
        result = await loop.run_in_executor(
            None,
            lambda: bundle_runner.run(bundle_root, framework_id, step, _on_line),
        )
    except Exception as e:
        orchestrator.record_step_result(
            project_slug, orchestrator_step,
            success=False, error=f"runner_failed: {type(e).__name__}: {e}",
        )
        await _broadcast({"type": "pipeline.step_failed",
                          "project": project_slug, "step": orchestrator_step,
                          "error": str(e)})
        raise HTTPException(502, f"runner failed: {e}")

    # Auto-install on first run — same shortcut as the orchestrate chain uses.
    if result.get("install_required"):
        await _broadcast({"type": "pipeline.run_log",
                          "project": project_slug, "step": orchestrator_step,
                          "line": "[auto-install] bundle deps missing — installing once and retrying"})
        install_res = await loop.run_in_executor(
            None,
            lambda: bundle_runner.install_bundle_deps(bundle_root, framework_id),
        )
        if install_res.get("ok"):
            await _broadcast({"type": "pipeline.run_log",
                              "project": project_slug, "step": orchestrator_step,
                              "line": "[auto-install] deps installed — re-running tests"})
            result = await loop.run_in_executor(
                None,
                lambda: bundle_runner.run(bundle_root, framework_id, step, _on_line),
            )
        else:
            # Install itself failed — record and bail.
            orchestrator.record_step_result(
                project_slug, orchestrator_step,
                success=False, pass_rate_pct=0,
                error=f"install_failed: {(install_res.get('log') or '')[-200:]}",
                artifacts={"install_log_tail": (install_res.get("log") or "")[-2000:]},
            )
            await _broadcast({"type": "pipeline.step_failed",
                              "project": project_slug, "step": orchestrator_step,
                              "error": "install_failed"})
            return {"project": project_slug, "framework": framework_id, **result,
                    "install_failed": True,
                    "install_log_tail": (install_res.get("log") or "")[-2000:],
                    "orchestrator": orchestrator.progress_snapshot(project_slug)}

    pass_rate = int(result.get("pass_rate_pct") or 0)
    total = int(result.get("total") or 0)

    # If the retry STILL says install_required, surface that as a clear failure.
    if result.get("install_required"):
        orchestrator.record_step_result(
            project_slug, orchestrator_step,
            success=False, pass_rate_pct=0,
            error="install_required_after_retry",
            artifacts={"runner_log": result.get("log_tail")},
        )
        await _broadcast({"type": "pipeline.step_failed",
                          "project": project_slug, "step": orchestrator_step,
                          "error": "install_required_after_retry"})
        return {"project": project_slug, "framework": framework_id, **result,
                "orchestrator": orchestrator.progress_snapshot(project_slug)}

    if result.get("unsupported_framework"):
        # Don't block the pipeline — record neutral pass-through.
        orchestrator.record_step_result(
            project_slug, orchestrator_step,
            success=True, pass_rate_pct=100,
            summary=f"skipped: runner not implemented for {framework_id}",
            artifacts={"skipped": True},
        )
        return {"project": project_slug, "framework": framework_id, **result,
                "orchestrator": orchestrator.progress_snapshot(project_slug)}

    success = total > 0 and (not require_gate or pass_rate >= 60)  # gate threshold check is in orchestrator

    state = orchestrator.record_step_result(
        project_slug, orchestrator_step,
        success=success,
        pass_rate_pct=pass_rate,
        summary=f"passed={result.get('passed')}/{total} ({pass_rate}%)",
        artifacts={
            "specs_executed": total,
            "failed": result.get("failed"),
            "duration_s": result.get("duration_s"),
            "screenshots": result.get("screenshots") or [],
            "log_tail": result.get("log_tail"),
            "bundle_root": str(bundle_root),
        },
        error=None if success else (
            f"no_tests_executed" if total == 0 else
            f"pass_rate_below_threshold: {pass_rate}%"
        ),
    )
    await _broadcast({"type": "pipeline.step_completed",
                      "project": project_slug, "step": orchestrator_step,
                      "state": state.current_state,
                      "blocked_reason": state.blocked_reason,
                      "pass_rate_pct": pass_rate})

    return {
        "project": project_slug, "framework": framework_id,
        "step": orchestrator_step,
        "passed": result.get("passed"), "failed": result.get("failed"),
        "total": total, "pass_rate_pct": pass_rate,
        "duration_s": result.get("duration_s"),
        "screenshots": result.get("screenshots") or [],
        "tests": result.get("tests") or [],
        "log_tail": result.get("log_tail"),
        "orchestrator": orchestrator.progress_snapshot(project_slug),
    }


@app.post("/api/ai/test-writer/smoke-run")
async def ai_test_writer_smoke_run(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Execute the saved smoke bundle and report results — triggers SMOKE_GATE."""
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    framework_id = payload.get("framework") or "cypress-js"
    env = (payload.get("env") or "").strip() or None
    return await _run_bundle_step(
        project_slug=app_index.project_slug_from(project),
        framework_id=framework_id, step="smoke", env=env,
        orchestrator_step="SMOKE_RUN", require_gate=True,
    )


@app.post("/api/ai/test-writer/e2e-run")
async def ai_test_writer_e2e_run(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    framework_id = payload.get("framework") or "cypress-js"
    env = (payload.get("env") or "").strip() or None
    return await _run_bundle_step(
        project_slug=app_index.project_slug_from(project),
        framework_id=framework_id, step="e2e", env=env,
        orchestrator_step="E2E_RUN", require_gate=True,
    )


@app.post("/api/ai/test-writer/negative-run")
async def ai_test_writer_negative_run(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Execute negative suite. Coverage gate is checked at orchestrator level
    (any non-zero pass rate counts as 'ran') — true failures are surfaced
    in the per-spec results."""
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    framework_id = payload.get("framework") or "cypress-js"
    env = (payload.get("env") or "").strip() or None
    return await _run_bundle_step(
        project_slug=app_index.project_slug_from(project),
        framework_id=framework_id, step="negative", env=env,
        orchestrator_step="NEGATIVE_RUN", require_gate=False,
    )


@app.post("/api/ai/test-writer/install-bundle-deps")
async def ai_test_writer_install_bundle_deps(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """One-time install of framework deps inside a generated bundle.

    The smoke/e2e/negative *_run endpoints expect the bundle to already
    contain node_modules / .venv. Call this once per bundle to populate them.
    """
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    framework_id = payload.get("framework") or "cypress-js"
    env = (payload.get("env") or "").strip() or None
    project_slug = app_index.project_slug_from(project)
    bundle_root = _bundle_root(project_slug, framework_id, env)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: bundle_runner.install_bundle_deps(bundle_root, framework_id),
    )
    return {
        "project": project_slug, "framework": framework_id,
        "bundle_root": str(bundle_root),
        **result,
    }


@app.post("/api/ai/test-writer/api-discovery")
async def ai_test_writer_api_discovery(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """STEP 5 — synthesize openapi.yaml from APP_INDEX.discovered_apis + traffic dump."""
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    project_slug = app_index.project_slug_from(project)
    index = app_index.load(project_slug)
    if index is None:
        raise HTTPException(409, "no app_index found — run /discover first")

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY required for api discovery")

    # Pull cumulative traffic dump if present (written by /smoke-run etc).
    traffic_path = app_index.traffic_path(project_slug)
    traffic_dump: list[dict] = []
    if traffic_path.exists():
        for line in traffic_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                traffic_dump.append(json.loads(line))
            except Exception:
                continue
    traffic_dump = traffic_dump[-500:]   # tail to keep prompt bounded

    backend_stack = ((index.get("application") or {}).get("detected_stack") or {}).get("backend") or ""
    auth_type = ((index.get("application") or {}).get("detected_stack") or {}).get("auth_type") or "none"
    base_url = (index.get("application") or {}).get("base_url") or ""

    orchestrator.mark_step_started(project_slug, "API_DISCOVERY")
    await _broadcast({"type": "pipeline.step_started",
                      "project": project_slug, "step": "API_DISCOVERY"})

    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            lambda: ai_engine.run_api_discovery(
                project_slug=project_slug, base_url=base_url,
                backend_stack=backend_stack, auth_type=auth_type,
                discovered_apis=index.get("discovered_apis") or [],
                traffic_dump=traffic_dump,
            ),
        )
    except Exception as e:
        orchestrator.record_step_result(project_slug, "API_DISCOVERY",
            success=False, error=f"llm_failed: {type(e).__name__}: {e}")
        raise HTTPException(502, f"api discovery failed: {e}")

    yaml_text = (data.get("openapi_yaml") or "").strip()
    if not yaml_text:
        orchestrator.record_step_result(project_slug, "API_DISCOVERY",
            success=False, error="no_yaml_returned")
        raise HTTPException(502, "api discovery returned empty yaml")

    # Save next to APP_INDEX.
    out_path = app_index.index_path(project_slug).parent / "openapi.yaml"
    out_path.write_text(yaml_text)

    state = orchestrator.record_step_result(
        project_slug, "API_DISCOVERY", success=True,
        summary=f"operations={data.get('operations_count')}",
        artifacts={
            "openapi_path": str(out_path),
            "operations_count": data.get("operations_count"),
            "tag_counts": data.get("tag_counts") or {},
            "auth_schemes_detected": data.get("auth_schemes_detected") or [],
            "coverage_warnings": data.get("coverage_warnings") or [],
        },
    )
    await _broadcast({"type": "pipeline.step_completed",
                      "project": project_slug, "step": "API_DISCOVERY",
                      "state": state.current_state})

    return {
        "project": project_slug,
        "openapi_path": str(out_path),
        "operations_count": data.get("operations_count"),
        "tag_counts": data.get("tag_counts") or {},
        "auth_schemes_detected": data.get("auth_schemes_detected") or [],
        "coverage_warnings": data.get("coverage_warnings") or [],
        "yaml_preview": yaml_text[:2000],
        "orchestrator": orchestrator.progress_snapshot(project_slug),
    }


@app.post("/api/ai/test-writer/validate")
async def ai_test_writer_validate(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """STEP 6 — produce the final handover doc + GREEN/YELLOW/RED verdict."""
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    project_slug = app_index.project_slug_from(project)
    index = app_index.load(project_slug)
    if index is None:
        raise HTTPException(409, "no app_index found — run /discover first")

    framework_id = payload.get("framework") or "cypress-js"
    env = (payload.get("env") or "").strip() or None
    bundle_root = _bundle_root(project_slug, framework_id, env)
    bundle_inventory = _list_relpaths(bundle_root)

    # Pull summaries from orchestrator history.
    snap = orchestrator.progress_snapshot(project_slug)
    history = snap.get("history") or []

    def _last(step: str) -> dict:
        for h in reversed(history):
            if h.get("step") == step and h.get("success"):
                return h
        return {}

    smoke = _last("SMOKE_RUN") or _last("SMOKE_GEN")
    e2e = _last("E2E_RUN") or _last("E2E_GEN")
    neg = _last("NEGATIVE_RUN") or _last("NEGATIVE_GEN")
    apid = _last("API_DISCOVERY")

    base_url = (index.get("application") or {}).get("base_url") or ""

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY required for validation")

    orchestrator.mark_step_started(project_slug, "VALIDATION")
    await _broadcast({"type": "pipeline.step_started",
                      "project": project_slug, "step": "VALIDATION"})

    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            lambda: ai_engine.run_validation(
                project_slug=project_slug,
                generated_at=_now_iso(), base_url=base_url,
                app_index_obj=index,
                smoke_summary=smoke, e2e_summary=e2e,
                negative_summary=neg, api_discovery_summary=apid,
                bundle_inventory=bundle_inventory,
                risk_flags=index.get("risk_flags") or [],
            ),
        )
    except Exception as e:
        orchestrator.record_step_result(project_slug, "VALIDATION",
            success=False, error=f"llm_failed: {type(e).__name__}: {e}")
        raise HTTPException(502, f"validation failed: {e}")

    report_md = data.get("report_md") or ""
    if not report_md.strip():
        orchestrator.record_step_result(project_slug, "VALIDATION",
            success=False, error="no_report_returned")
        raise HTTPException(502, "validation returned empty report")

    out_path = app_index.index_path(project_slug).parent / "report.md"
    out_path.write_text(report_md)

    state = orchestrator.record_step_result(
        project_slug, "VALIDATION", success=True,
        summary=f"verdict={data.get('verdict')}",
        artifacts={
            "report_path": str(out_path),
            "verdict": data.get("verdict"),
            "verdict_reason": data.get("verdict_reason"),
            "totals": data.get("totals") or {},
            "top_risks": data.get("top_risks") or [],
            "expansion_plan_summary": data.get("expansion_plan_summary") or [],
        },
    )
    await _broadcast({"type": "pipeline.step_completed",
                      "project": project_slug, "step": "VALIDATION",
                      "state": state.current_state,
                      "verdict": data.get("verdict")})

    return {
        "project": project_slug,
        "report_path": str(out_path),
        "verdict": data.get("verdict"),
        "verdict_reason": data.get("verdict_reason"),
        "totals": data.get("totals") or {},
        "top_risks": data.get("top_risks") or [],
        "expansion_plan_summary": data.get("expansion_plan_summary") or [],
        "report_md": report_md,
        "orchestrator": orchestrator.progress_snapshot(project_slug),
    }


@app.post("/api/ai/test-writer/extend")
async def ai_test_writer_extend(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Incremental extension — user lists gaps, AI adds surgical coverage.

    Body:
      {
        "project":    "<slug — must have existing app_index.json>",
        "framework":  "cypress-js" ,
        "env":        "<optional>",
        "gaps":       ["/wallet not covered", "password reset missing", ...],
        "rescan_urls":["http://app/wallet", ...]   # optional — re-scan these
      }
    """
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    project_slug = app_index.project_slug_from(project)
    index = app_index.load(project_slug)
    if index is None:
        raise HTTPException(409, "no app_index found — run /discover first")

    gaps = payload.get("gaps") or []
    if not isinstance(gaps, list) or not gaps:
        raise HTTPException(400, "gaps must be a non-empty list of strings")
    framework_id = payload.get("framework") or "cypress-js"
    spec = frameworks.by_id(framework_id)
    if not spec:
        raise HTTPException(400, f"unknown framework: {framework_id}")
    env = (payload.get("env") or "").strip() or None

    # Optional delta scan for new pages the user pointed out.
    rescan_urls = payload.get("rescan_urls") or []
    delta_scan: dict = {"rescanned_pages": []}
    if rescan_urls:
        loop = asyncio.get_running_loop()
        auth_cfg = (index.get("auth_flow") or {}) if (index.get("auth_flow", {}).get("type") == "form") else None
        for url in rescan_urls[:5]:    # cap to keep prompt bounded
            try:
                scan = await loop.run_in_executor(
                    None,
                    lambda u=url: test_writer.scan_page(u, auth_cfg, False, True),
                )
                delta_scan["rescanned_pages"].append(scan)
            except Exception as e:
                delta_scan["rescanned_pages"].append({"url": url, "error": str(e)})

    bundle_root = _bundle_root(project_slug, framework_id, env)
    inventory = _list_relpaths(bundle_root)

    snap = orchestrator.progress_snapshot(project_slug)
    last_validation = next(
        (h for h in reversed(snap.get("history") or [])
         if h.get("step") == "VALIDATION" and h.get("success")),
        {},
    )

    base_url = (index.get("application") or {}).get("base_url") or ""

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY required for extend")

    orchestrator.mark_step_started(project_slug, "EXTEND")
    await _broadcast({"type": "pipeline.step_started",
                      "project": project_slug, "step": "EXTEND"})

    engine_for_prompt = {
        "cypress-js": "cypress", "cypress-ts": "cypress",
        "playwright-js": "playwright",
        "pytest-playwright": "pytest-playwright",
        "robot-py": "robot", "selenium-py": "selenium",
    }.get(framework_id, "cypress")

    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            lambda: ai_engine.run_extend(
                project_slug=project_slug,
                framework=engine_for_prompt, language=spec.language,
                framework_folder=spec.folder_name, base_url=base_url,
                app_index_obj=index, inventory=inventory, gaps=gaps,
                delta_scan=delta_scan,
                validation_summary=last_validation,
            ),
        )
    except Exception as e:
        orchestrator.record_step_result(project_slug, "EXTEND",
            success=False, error=f"llm_failed: {type(e).__name__}: {e}")
        raise HTTPException(502, f"extend failed: {e}")

    new_files = data.get("new_files") or {}
    modified_files = data.get("modified_files") or {}
    if not new_files and not modified_files:
        orchestrator.record_step_result(project_slug, "EXTEND",
            success=False, error="no_changes_emitted")
        raise HTTPException(502, "extend produced no file changes")

    # Write all the files. Both new and modified files use save_bundle's
    # write semantics — overwrite-on-collision.
    combined = {**new_files, **modified_files}
    try:
        saved = test_writer.save_bundle(
            files={str(k): str(v) for k, v in combined.items()},
            framework=framework_id, project=project_slug, env=env,
        )
    except ValueError as e:
        orchestrator.record_step_result(project_slug, "EXTEND",
            success=False, error=f"save_failed: {e}")
        raise HTTPException(400, str(e))

    # Merge APP_INDEX patch in place.
    patch = data.get("app_index_patch") or {}
    if patch:
        app_index.merge_extension(index, patch)
        app_index.save(project_slug, index)

    state = orchestrator.record_step_result(
        project_slug, "EXTEND", success=True,
        summary=data.get("summary") or "",
        files_generated=len(new_files),
        artifacts={
            "bundle_root": saved.get("bundle_root"),
            "new_files_count": len(new_files),
            "modified_files_count": len(modified_files),
            "spurious_gaps": data.get("spurious_gaps") or [],
            "coverage_documented_skip": data.get("coverage_documented_skip") or [],
        },
    )
    await _broadcast({"type": "pipeline.step_completed",
                      "project": project_slug, "step": "EXTEND",
                      "state": state.current_state})

    return {
        "project": project_slug, "framework": framework_id,
        "saved": saved,
        "summary": data.get("summary"),
        "new_files": list(new_files.keys()),
        "modified_files": list(modified_files.keys()),
        "spurious_gaps": data.get("spurious_gaps") or [],
        "coverage_documented_skip": data.get("coverage_documented_skip") or [],
        "fragility_notes": data.get("fragility_notes") or [],
        "orchestrator": orchestrator.progress_snapshot(project_slug),
    }


# ---------------------------------------------------------------------------
# Server-side full-pipeline orchestration.
#
# The frontend PipelineBoard can chain the per-step endpoints from the
# browser, but that chain dies the moment the user closes the tab. This
# endpoint runs the same sequence in a background task on the server so
# long-running pipelines (15+ minutes against a real app) survive page
# refreshes and tab closures. Progress is broadcast to anyone listening
# on /ws.
# ---------------------------------------------------------------------------

_orchestrate_tasks: dict[str, asyncio.Task] = {}


async def _run_pipeline_chain(
    project_slug: str,
    framework_id: str,
    mode: str,
    url: str | None,
    repo_url: str | None,
    pdf_path: str | None,
    auth_cfg: dict | None,
    test_users: list[dict],
    max_pages: int,
    env: str | None,
    stop_after: str | None,
) -> None:
    """Background task driving the full pipeline."""

    async def _bcast(event_type: str, **kwargs) -> None:
        await _broadcast({
            "type": event_type,
            "project": project_slug,
            **kwargs,
        })

    def _stopped_after(step_name: str) -> bool:
        return stop_after is not None and stop_after.upper() == step_name

    # Define the chain. Each item: (step_name, callable, gate_blocks?)
    spec = frameworks.by_id(framework_id)
    if not spec:
        await _bcast("pipeline.orchestrate_failed",
                     reason=f"unknown framework: {framework_id}")
        return

    engine_for_prompt = {
        "cypress-js": "cypress", "cypress-ts": "cypress",
        "playwright-js": "playwright",
        "pytest-playwright": "pytest-playwright",
        "robot-py": "robot", "selenium-py": "selenium",
        "appium-py": "appium",
    }.get(framework_id, "cypress")

    loop = asyncio.get_running_loop()
    await _bcast("pipeline.orchestrate_started", framework=framework_id, mode=mode)

    # ---- DISCOVERY -------------------------------------------------------
    try:
        scan: dict = {}
        crawl_pages_list: list[dict] = []
        git_index_text: str | None = None
        pdf_excerpt_text: str | None = None

        if mode == "product":
            scan = await loop.run_in_executor(
                None, lambda: test_writer.scan_page(url, auth_cfg, False, True),
            )
            crawl_pages_list = await loop.run_in_executor(
                None, lambda: test_writer.crawl_pages(url, max_pages, True, auth_cfg, True),
            )
        elif mode == "git" and repo_url:
            git_index_text = await loop.run_in_executor(
                None, lambda: source_extractors.extract_git_index(repo_url),
            )
        elif mode == "pdf" and pdf_path:
            pdf_excerpt_text = await loop.run_in_executor(
                None, lambda: source_extractors.extract_pdf(pdf_path),
            )

        orchestrator.mark_step_started(project_slug, "DISCOVERY")
        await _bcast("pipeline.step_started", step="DISCOVERY")
        index = await loop.run_in_executor(
            None,
            lambda: ai_engine.run_discovery(
                project_slug=project_slug, source_mode=mode, target_url=url,
                scan=scan, crawl_pages_list=crawl_pages_list,
                auth_config=auth_cfg, test_users=test_users,
                git_index=git_index_text, pdf_excerpt=pdf_excerpt_text,
                crawl_max_pages=max_pages,
            ),
        )
        index["project_slug"] = project_slug
        app_index.save(project_slug, index)
        seed_traffic = list(scan.get("network_requests") or [])
        for page in crawl_pages_list or []:
            seed_traffic.extend(page.get("network_requests") or [])
        app_index.append_traffic(project_slug, seed_traffic)
        state = orchestrator.record_step_result(
            project_slug, "DISCOVERY", success=True,
            summary=f"pages={len(index.get('pages') or [])}",
        )
        await _bcast("pipeline.step_completed", step="DISCOVERY",
                     state=state.current_state, blocked_reason=state.blocked_reason)
        if state.current_state == "BLOCKED":
            await _bcast("pipeline.orchestrate_blocked", at="DISCOVERY",
                         reason=state.blocked_reason)
            return
    except Exception as e:
        orchestrator.record_step_result(project_slug, "DISCOVERY",
            success=False, error=f"{type(e).__name__}: {e}")
        await _bcast("pipeline.step_failed", step="DISCOVERY", error=str(e))
        return

    if _stopped_after("DISCOVERY"):
        await _bcast("pipeline.orchestrate_done", stopped_at="DISCOVERY")
        return

    base_url = (
        (index.get("application") or {}).get("base_url")
        or url or "http://localhost:3000"
    )

    # ---- Helper to run a gen step ----------------------------------------
    async def _gen(step_name: str, runner_fn) -> tuple[bool, dict | None]:
        orchestrator.mark_step_started(project_slug, step_name)
        await _bcast("pipeline.step_started", step=step_name)
        try:
            data = await loop.run_in_executor(None, runner_fn)
        except Exception as e:
            orchestrator.record_step_result(project_slug, step_name,
                success=False, error=f"{type(e).__name__}: {e}")
            await _bcast("pipeline.step_failed", step=step_name, error=str(e))
            return (False, None)
        files = data.get("files") or {}
        if not files:
            orchestrator.record_step_result(project_slug, step_name,
                success=False, error="no_files_returned")
            await _bcast("pipeline.step_failed", step=step_name, error="no_files_returned")
            return (False, None)
        try:
            saved = test_writer.save_bundle(
                files={str(k): str(v) for k, v in files.items()},
                framework=framework_id, project=project_slug, env=env,
            )
        except ValueError as e:
            orchestrator.record_step_result(project_slug, step_name,
                success=False, error=f"save_failed: {e}")
            await _bcast("pipeline.step_failed", step=step_name, error=f"save_failed: {e}")
            return (False, None)
        st = orchestrator.record_step_result(
            project_slug, step_name, success=True,
            summary=data.get("summary") or "",
            files_generated=len(files),
            artifacts={"bundle_root": saved.get("bundle_root")},
        )
        await _bcast("pipeline.step_completed", step=step_name,
                     state=st.current_state, files_count=len(files))
        return (True, data)

    # ---- SMOKE_GEN -------------------------------------------------------
    ok, _ = await _gen("SMOKE_GEN", lambda: ai_engine.run_smoke_generation(
        project_slug=project_slug, framework=engine_for_prompt,
        language=spec.language, framework_folder=spec.folder_name,
        base_url=base_url,
        app_index_slice=app_index.slice_for_prompt(index, "smoke_gen"),
    ))
    if not ok: return
    if _stopped_after("SMOKE_GEN"):
        await _bcast("pipeline.orchestrate_done", stopped_at="SMOKE_GEN"); return

    # ---- SMOKE_RUN -------------------------------------------------------
    ok = await _run_via_bundle("SMOKE_RUN", "smoke", project_slug, framework_id, env, True)
    if not ok: return
    if _stopped_after("SMOKE_RUN"):
        await _bcast("pipeline.orchestrate_done", stopped_at="SMOKE_RUN"); return

    # ---- E2E_GEN ---------------------------------------------------------
    smoke_meta = _last_smoke_run(project_slug) or {}
    bundle_root = _bundle_root(project_slug, framework_id, env)
    existing_artifacts = _list_relpaths(bundle_root)
    ok, _ = await _gen("E2E_GEN", lambda: ai_engine.run_e2e_generation(
        project_slug=project_slug, framework=engine_for_prompt,
        language=spec.language, framework_folder=spec.folder_name,
        base_url=base_url,
        app_index_json=app_index.slice_for_prompt(index, "e2e_gen"),
        smoke_pass_rate=int(smoke_meta.get("pass_rate_pct") or 100),
        smoke_duration_s=float((smoke_meta.get("finished_at") or 0) - (smoke_meta.get("started_at") or 0)),
        smoke_spec_count=int((smoke_meta.get("artifacts") or {}).get("specs_executed") or 0),
        existing_artifacts=existing_artifacts,
    ))
    if not ok: return
    if _stopped_after("E2E_GEN"):
        await _bcast("pipeline.orchestrate_done", stopped_at="E2E_GEN"); return

    ok = await _run_via_bundle("E2E_RUN", "e2e", project_slug, framework_id, env, True)
    if not ok: return
    if _stopped_after("E2E_RUN"):
        await _bcast("pipeline.orchestrate_done", stopped_at="E2E_RUN"); return

    # ---- NEGATIVE_GEN + RUN ---------------------------------------------
    smoke_filenames = _list_relpaths(bundle_root, "smoke")
    e2e_filenames = _list_relpaths(bundle_root, "e2e")
    ok, _ = await _gen("NEGATIVE_GEN", lambda: ai_engine.run_negative_generation(
        project_slug=project_slug, framework=engine_for_prompt,
        language=spec.language, framework_folder=spec.folder_name,
        base_url=base_url,
        app_index_json=app_index.slice_for_prompt(index, "negative_gen"),
        smoke_filenames=smoke_filenames, e2e_filenames=e2e_filenames,
        discovered_apis=index.get("discovered_apis") or [],
    ))
    if not ok: return
    if _stopped_after("NEGATIVE_GEN"):
        await _bcast("pipeline.orchestrate_done", stopped_at="NEGATIVE_GEN"); return

    await _run_via_bundle("NEGATIVE_RUN", "negative", project_slug, framework_id, env, False)
    if _stopped_after("NEGATIVE_RUN"):
        await _bcast("pipeline.orchestrate_done", stopped_at="NEGATIVE_RUN"); return

    # ---- API_DISCOVERY ---------------------------------------------------
    orchestrator.mark_step_started(project_slug, "API_DISCOVERY")
    await _bcast("pipeline.step_started", step="API_DISCOVERY")
    try:
        traffic_path = app_index.traffic_path(project_slug)
        traffic_dump: list[dict] = []
        if traffic_path.exists():
            for line in traffic_path.read_text().splitlines():
                line = line.strip()
                if not line: continue
                try: traffic_dump.append(json.loads(line))
                except Exception: continue
        traffic_dump = traffic_dump[-500:]
        stack = ((index.get("application") or {}).get("detected_stack") or {})
        api_data = await loop.run_in_executor(
            None,
            lambda: ai_engine.run_api_discovery(
                project_slug=project_slug, base_url=base_url,
                backend_stack=stack.get("backend") or "",
                auth_type=stack.get("auth_type") or "none",
                discovered_apis=index.get("discovered_apis") or [],
                traffic_dump=traffic_dump,
            ),
        )
        yaml_text = (api_data.get("openapi_yaml") or "").strip()
        if yaml_text:
            (app_index.index_path(project_slug).parent / "openapi.yaml").write_text(yaml_text)
        st = orchestrator.record_step_result(project_slug, "API_DISCOVERY",
            success=True, summary=f"ops={api_data.get('operations_count')}")
        await _bcast("pipeline.step_completed", step="API_DISCOVERY", state=st.current_state)
    except Exception as e:
        orchestrator.record_step_result(project_slug, "API_DISCOVERY",
            success=False, error=f"{type(e).__name__}: {e}")
        await _bcast("pipeline.step_failed", step="API_DISCOVERY", error=str(e))

    if _stopped_after("API_DISCOVERY"):
        await _bcast("pipeline.orchestrate_done", stopped_at="API_DISCOVERY"); return

    # ---- VALIDATION ------------------------------------------------------
    orchestrator.mark_step_started(project_slug, "VALIDATION")
    await _bcast("pipeline.step_started", step="VALIDATION")
    try:
        snap = orchestrator.progress_snapshot(project_slug)

        def _last_h(name: str) -> dict:
            for h in reversed(snap.get("history") or []):
                if h.get("step") == name and h.get("success"):
                    return h
            return {}

        val_data = await loop.run_in_executor(
            None,
            lambda: ai_engine.run_validation(
                project_slug=project_slug, generated_at=_now_iso(),
                base_url=base_url, app_index_obj=index,
                smoke_summary=_last_h("SMOKE_RUN") or _last_h("SMOKE_GEN"),
                e2e_summary=_last_h("E2E_RUN") or _last_h("E2E_GEN"),
                negative_summary=_last_h("NEGATIVE_RUN") or _last_h("NEGATIVE_GEN"),
                api_discovery_summary=_last_h("API_DISCOVERY"),
                bundle_inventory=_list_relpaths(bundle_root),
                risk_flags=index.get("risk_flags") or [],
            ),
        )
        if (val_data.get("report_md") or "").strip():
            (app_index.index_path(project_slug).parent / "report.md").write_text(val_data["report_md"])
        st = orchestrator.record_step_result(project_slug, "VALIDATION",
            success=True, summary=f"verdict={val_data.get('verdict')}",
            artifacts={"verdict": val_data.get("verdict"),
                       "verdict_reason": val_data.get("verdict_reason")})
        await _bcast("pipeline.step_completed", step="VALIDATION",
                     state=st.current_state, verdict=val_data.get("verdict"))
    except Exception as e:
        orchestrator.record_step_result(project_slug, "VALIDATION",
            success=False, error=f"{type(e).__name__}: {e}")
        await _bcast("pipeline.step_failed", step="VALIDATION", error=str(e))

    await _bcast("pipeline.orchestrate_done", stopped_at=None)


async def _run_via_bundle(
    step_name: str, sub: str, project_slug: str, framework_id: str,
    env: str | None, require_gate: bool,
) -> bool:
    """Internal helper used by the background chain — runs the saved bundle's
    smoke/e2e/negative subdir and updates orchestrator. Returns False on BLOCKED.

    When the bundle hasn't had its dependencies installed yet (first run after
    AI generated the suite), the runner returns ``install_required``. We
    auto-install once and retry — the most common pipeline friction point.
    """
    bundle_root = _bundle_root(project_slug, framework_id, env)
    orchestrator.mark_step_started(project_slug, step_name)
    await _broadcast({"type": "pipeline.step_started",
                      "project": project_slug, "step": step_name})

    loop = asyncio.get_running_loop()
    def _on_line(line: str) -> None:
        asyncio.run_coroutine_threadsafe(
            _broadcast({"type": "pipeline.run_log", "project": project_slug,
                        "step": step_name, "line": line[:500]}),
            loop,
        )
    result = await loop.run_in_executor(
        None,
        lambda: bundle_runner.run(bundle_root, framework_id, sub, _on_line),
    )

    if result.get("install_required"):
        # Auto-install deps once, then retry. This is the dominant first-run
        # failure mode — the AI just generated a fresh bundle and node_modules
        # / .venv don't exist yet.
        await _broadcast({"type": "pipeline.run_log",
                          "project": project_slug, "step": step_name,
                          "line": "[auto-install] bundle deps missing — installing once and retrying"})
        install_res = await loop.run_in_executor(
            None,
            lambda: bundle_runner.install_bundle_deps(bundle_root, framework_id),
        )
        if not install_res.get("ok"):
            orchestrator.record_step_result(project_slug, step_name,
                success=False, pass_rate_pct=0,
                error=f"install_failed: {install_res.get('log', '')[-300:]}",
                artifacts={"install_log_tail": (install_res.get("log") or "")[-2000:]})
            await _broadcast({"type": "pipeline.step_failed",
                              "project": project_slug, "step": step_name,
                              "error": "install_failed"})
            return False

        # Retry the run now that deps are installed.
        await _broadcast({"type": "pipeline.run_log",
                          "project": project_slug, "step": step_name,
                          "line": "[auto-install] deps installed — re-running tests"})
        result = await loop.run_in_executor(
            None,
            lambda: bundle_runner.run(bundle_root, framework_id, sub, _on_line),
        )
        # If install still didn't suffice, surface the failure honestly.
        if result.get("install_required"):
            orchestrator.record_step_result(project_slug, step_name,
                success=False, pass_rate_pct=0,
                error="install_required_after_retry",
                artifacts={"runner_log": result.get("log_tail")})
            await _broadcast({"type": "pipeline.step_failed",
                              "project": project_slug, "step": step_name,
                              "error": "install_required_after_retry"})
            return False
    if result.get("unsupported_framework"):
        orchestrator.record_step_result(project_slug, step_name,
            success=True, pass_rate_pct=100,
            summary=f"skipped: runner not implemented for {framework_id}",
            artifacts={"skipped": True})
        return True

    pass_rate = int(result.get("pass_rate_pct") or 0)
    total = int(result.get("total") or 0)
    success = total > 0
    st = orchestrator.record_step_result(
        project_slug, step_name, success=success,
        pass_rate_pct=pass_rate,
        summary=f"passed={result.get('passed')}/{total} ({pass_rate}%)",
        artifacts={
            "specs_executed": total,
            "failed": result.get("failed"),
            "duration_s": result.get("duration_s"),
            "screenshots": result.get("screenshots") or [],
            "log_tail": result.get("log_tail"),
            "bundle_root": str(bundle_root),
        },
        error=None if success else "no_tests_executed",
    )
    await _broadcast({"type": "pipeline.step_completed", "project": project_slug,
                      "step": step_name, "state": st.current_state,
                      "blocked_reason": st.blocked_reason,
                      "pass_rate_pct": pass_rate})
    return st.current_state != "BLOCKED"


@app.post("/api/ai/test-writer/orchestrate")
async def ai_test_writer_orchestrate(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Kick off the full pipeline as a background server-side task.

    Returns immediately with the task handle. Caller polls /state/{project}
    or listens on /ws for progress events. Closing the browser does NOT
    interrupt the run.

    Body (most fields optional unless mode requires them):
      {
        "project":      "<required slug>",
        "framework":    "cypress-js" ,
        "env":          "<optional>",
        "mode":         "product" | "git" | "pdf",
        "url":          "<for product mode>",
        "repo_url":     "<for git mode>",
        "pdf_path":     "<for pdf mode>",
        "auth":         {...},
        "test_users":   [...],
        "max_pages":    8,
        "stop_after":   "DISCOVERY" | "SMOKE_RUN" | ...   # optional checkpoint
      }
    """
    payload = payload or {}
    project = (payload.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    project_slug = app_index.project_slug_from(project)

    mode = (payload.get("mode") or "product").strip()
    if mode not in ("product", "git", "pdf"):
        raise HTTPException(400, "mode must be one of: product, git, pdf")
    url = (payload.get("url") or "").strip() or None
    repo_url = (payload.get("repo_url") or "").strip() or None
    pdf_path = (payload.get("pdf_path") or "").strip() or None
    if mode == "product" and not (url and url.startswith(("http://", "https://"))):
        raise HTTPException(400, "product mode requires a url (http:// or https://)")
    if mode == "git" and not repo_url:
        raise HTTPException(400, "git mode requires repo_url")
    if mode == "pdf" and not pdf_path:
        raise HTTPException(400, "pdf mode requires pdf_path")

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY required for orchestration")

    framework_id = payload.get("framework") or "cypress-js"
    if not frameworks.by_id(framework_id):
        raise HTTPException(400, f"unknown framework: {framework_id}")

    # Cancel any in-flight orchestration for this project to avoid double-run.
    existing = _orchestrate_tasks.get(project_slug)
    if existing and not existing.done():
        existing.cancel()

    task = asyncio.create_task(_run_pipeline_chain(
        project_slug=project_slug,
        framework_id=framework_id,
        mode=mode, url=url, repo_url=repo_url, pdf_path=pdf_path,
        auth_cfg=payload.get("auth"),
        test_users=payload.get("test_users") or [],
        max_pages=int(payload.get("max_pages") or 8),
        env=(payload.get("env") or "").strip() or None,
        stop_after=payload.get("stop_after"),
    ))
    _orchestrate_tasks[project_slug] = task

    # Drop the entry once the task completes so the dict doesn't grow.
    # The callback runs on the loop, so dict mutation is safe.
    def _cleanup(t: asyncio.Task, slug: str = project_slug) -> None:
        if _orchestrate_tasks.get(slug) is t:
            _orchestrate_tasks.pop(slug, None)
    task.add_done_callback(_cleanup)

    return {
        "project": project_slug, "framework": framework_id, "mode": mode,
        "started": True,
        "orchestrator": orchestrator.progress_snapshot(project_slug),
        "subscribe_via": "/ws",
    }


# ---------------------------------------------------------------------------
# Fakemail bridge — test inbox provisioning + peek
# ---------------------------------------------------------------------------

@app.get("/api/ai/fakemail/info")
async def ai_fakemail_info(
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Return which fakemail provider QAFLOW will use given current env."""
    provider = fakemail.discover_default_provider()
    return {
        "provider": provider,
        "configured_via_env": {
            "MAILOSAUR_API_KEY": bool(os.environ.get("MAILOSAUR_API_KEY")),
            "IMAP_HOST": bool(os.environ.get("IMAP_HOST")),
        },
        "memory_fallback": provider == "memory",
    }


@app.get("/api/ai/fakemail/peek")
async def ai_fakemail_peek(
    to: str,
    timeout_s: float = 10.0,
    subject_contains: str | None = None,
    provider: str | None = None,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Block (up to timeout_s) until a message for `to` is available."""
    if not to:
        raise HTTPException(400, "to is required")
    try:
        bridge = fakemail.get_bridge(provider or fakemail.discover_default_provider())
    except Exception as e:
        raise HTTPException(400, f"bridge init failed: {e}")
    loop = asyncio.get_running_loop()
    mail = await loop.run_in_executor(
        None, lambda: bridge.peek(to, timeout_s, subject_contains),
    )
    if not mail:
        return JSONResponse(status_code=404, content={"detail": "no_mail_available"})
    return {
        "to": mail.to, "from": mail.from_addr,
        "subject": mail.subject,
        "text_body": mail.text_body[:8000],
        "html_body": mail.html_body[:8000],
        "links": mail.extract_links(),
        "otp": mail.extract_otp(),
        "received_at": mail.received_at,
        "provider": bridge.name,
    }


@app.post("/api/ai/fakemail/deliver")
async def ai_fakemail_deliver(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Push a synthetic email into the in-memory bridge.

    Useful for testing QAFLOW itself or building local demos that don't
    touch real email infrastructure. Real providers (mailosaur/imap)
    reject this with 400.
    """
    payload = payload or {}
    provider = (payload.get("provider") or "memory").strip()
    if provider != "memory":
        raise HTTPException(400, "deliver only supported by the memory bridge")
    to = (payload.get("to") or "").strip()
    if not to:
        raise HTTPException(400, "to is required")
    bridge = fakemail.get_bridge("memory")
    if not isinstance(bridge, fakemail.MemoryBridge):
        raise HTTPException(500, "memory bridge missing")
    mail = fakemail.TestMail(
        to=to,
        from_addr=(payload.get("from") or "noreply@qaflow.local"),
        subject=(payload.get("subject") or ""),
        text_body=(payload.get("text_body") or ""),
        html_body=(payload.get("html_body") or ""),
    )
    bridge.deliver(mail)
    return {"ok": True, "queued_for": to}


@app.post("/api/ai/fakemail/provision-users")
async def ai_fakemail_provision_users(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Build a test_users list for a given set of roles.

    Body: {"roles": ["admin", "viewer", ...], "domain": "<optional>"}

    Returns the list ready to be passed verbatim to /discover's test_users.
    """
    payload = payload or {}
    roles = payload.get("roles") or []
    if not isinstance(roles, list) or not roles:
        raise HTTPException(400, "roles must be a non-empty list of strings")
    specs = [{"role": r} for r in roles if isinstance(r, str)]
    users = fakemail.provision_test_users(specs, domain=payload.get("domain"))
    return {
        "test_users": users,
        "provider": fakemail.discover_default_provider(),
    }


# ---------------------------------------------------------------------------
# Performance runner — stdlib + locust modes
# ---------------------------------------------------------------------------

@app.post("/api/ai/perf/run")
async def ai_perf_run(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Drive throughput + latency against a URL.

    stdlib mode (default):
      {{ "mode": "stdlib", "url": "...", "method": "GET",
         "concurrency": 10, "duration_s": 30, "headers": {{}}, "body": {{}} }}

    locust mode (requires locust in the bundle .venv):
      {{ "mode": "locust", "project": "<slug>", "framework": "pytest-playwright",
         "locustfile": "locustfile.py",
         "target": "http://app", "users": 50, "spawn_rate": 5, "duration_s": 60 }}
    """
    payload = payload or {}
    mode = (payload.get("mode") or "stdlib").strip()

    if mode == "stdlib":
        url = (payload.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "url must start with http:// or https://")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: perf_runner.run_stdlib(
                url=url,
                method=payload.get("method") or "GET",
                body=payload.get("body"),
                headers=payload.get("headers") or {},
                concurrency=int(payload.get("concurrency") or 10),
                duration_s=int(payload.get("duration_s") or 30),
            ),
        )
        return result

    if mode == "locust":
        project = (payload.get("project") or "").strip()
        framework_id = payload.get("framework") or "pytest-playwright"
        env = (payload.get("env") or "").strip() or None
        if not project:
            raise HTTPException(400, "project is required for locust mode")
        bundle_root = _bundle_root(app_index.project_slug_from(project), framework_id, env)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: perf_runner.run_locust(
                bundle_root=bundle_root,
                locustfile=payload.get("locustfile") or "locustfile.py",
                target=(payload.get("target") or "").strip(),
                users=int(payload.get("users") or 50),
                spawn_rate=int(payload.get("spawn_rate") or 5),
                duration_s=int(payload.get("duration_s") or 60),
            ),
        )
        return result

    raise HTTPException(400, f"unknown mode: {mode}")


@app.post("/api/ai/test-writer/orchestrate-cancel")
async def ai_test_writer_orchestrate_cancel(
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    project_slug = app_index.project_slug_from((payload or {}).get("project") or "")
    task = _orchestrate_tasks.get(project_slug)
    if not task or task.done():
        return {"project": project_slug, "cancelled": False, "reason": "no_active_task"}
    task.cancel()
    return {"project": project_slug, "cancelled": True}


# ---------------------------------------------------------------------------
# Project inspection — APP_INDEX viewer + file tree + bundle download
# ---------------------------------------------------------------------------

_FILE_PREVIEW_CAP_BYTES = 100_000      # max chars returned per file in /files


@app.get("/api/ai/test-writer/projects/{project}/app-index")
async def ai_test_writer_project_app_index(
    project: str,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Return the persisted APP_INDEX for the project (raw JSON)."""
    slug = app_index.project_slug_from(project)
    idx = app_index.load(slug)
    if idx is None:
        raise HTTPException(404, f"no app_index for project: {slug}")
    return {
        "project": slug,
        "path": str(app_index.index_path(slug)),
        "app_index": idx,
    }


@app.get("/api/ai/test-writer/projects/{project}/files")
async def ai_test_writer_project_files(
    project: str,
    framework: str = "cypress-js",
    env: str | None = None,
    include_contents: bool = True,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Return the bundle's file tree + (optional) contents.

    Files larger than ``_FILE_PREVIEW_CAP_BYTES`` get content truncated to
    keep the response small. Binary files are reported but not previewed.
    """
    slug = app_index.project_slug_from(project)
    bundle_root = _bundle_root(slug, framework, env)
    if not bundle_root.exists():
        raise HTTPException(404, f"bundle not found at {bundle_root}")

    out: list[dict] = []
    for rel in _list_relpaths(bundle_root):
        p = bundle_root / rel
        size = p.stat().st_size
        entry: dict = {
            "path": rel,
            "size_bytes": size,
        }
        if include_contents:
            try:
                text = p.read_text()
                if len(text) > _FILE_PREVIEW_CAP_BYTES:
                    entry["contents"] = text[:_FILE_PREVIEW_CAP_BYTES] + "\n…(truncated)"
                    entry["truncated"] = True
                else:
                    entry["contents"] = text
            except UnicodeDecodeError:
                entry["binary"] = True
            except Exception as e:
                entry["read_error"] = f"{type(e).__name__}: {e}"
        out.append(entry)

    return {
        "project": slug,
        "framework": framework,
        "env": env,
        "bundle_root": str(bundle_root),
        "file_count": len(out),
        "files": out,
    }


@app.get("/api/ai/test-writer/projects/{project}/download")
async def ai_test_writer_project_download(
    project: str,
    framework: str = "cypress-js",
    env: str | None = None,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Stream the bundle as a ZIP archive."""
    import io
    import zipfile
    from fastapi.responses import StreamingResponse

    slug = app_index.project_slug_from(project)
    bundle_root = _bundle_root(slug, framework, env)
    if not bundle_root.exists():
        raise HTTPException(404, f"bundle not found at {bundle_root}")

    buf = io.BytesIO()
    archive_prefix = bundle_root.name
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Bundle files
        for rel in _list_relpaths(bundle_root):
            zf.write(bundle_root / rel, arcname=f"{archive_prefix}/{rel}")
        # Sidecar QAFLOW metadata (APP_INDEX, openapi, report) lives one
        # level up under .qaflow/ — include it for traceability.
        qaflow_dir = app_index.index_path(slug).parent
        if qaflow_dir.exists():
            for p in sorted(qaflow_dir.rglob("*")):
                if p.is_file():
                    zf.write(p, arcname=f"{archive_prefix}/.qaflow/{p.relative_to(qaflow_dir)}")

    buf.seek(0)
    filename = f"{slug}-{framework}-bundle.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/ai/test-writer/projects/{project}/report")
async def ai_test_writer_project_report(
    project: str,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Return the validation report.md as plain text (or 404 if not run yet)."""
    slug = app_index.project_slug_from(project)
    p = app_index.index_path(slug).parent / "report.md"
    if not p.exists():
        raise HTTPException(404, "validation report not generated yet")
    return {"project": slug, "path": str(p), "report_md": p.read_text()}


@app.get("/api/ai/test-writer/projects/{project}/openapi")
async def ai_test_writer_project_openapi(
    project: str,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Return the openapi.yaml synthesized by the api-discovery step."""
    slug = app_index.project_slug_from(project)
    p = app_index.index_path(slug).parent / "openapi.yaml"
    if not p.exists():
        raise HTTPException(404, "openapi.yaml not generated yet")
    return {"project": slug, "path": str(p), "openapi_yaml": p.read_text()}


@app.get("/api/ai/test-writer/state/{project}")
async def ai_test_writer_state(
    project: str,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Live orchestrator state for a project — polled by the UI progress board."""
    slug = app_index.project_slug_from(project)
    idx = app_index.load(slug)
    return {
        "project": slug,
        "orchestrator": orchestrator.progress_snapshot(slug),
        "has_app_index": idx is not None,
        "pages_count": len((idx or {}).get("pages") or []),
        "apis_count": len((idx or {}).get("discovered_apis") or []),
    }


@app.post("/api/ai/test-writer/state/{project}/retry")
async def ai_test_writer_state_retry(
    project: str,
    payload: dict,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    """Reset orchestrator so a specific step can be re-run."""
    slug = app_index.project_slug_from(project)
    step = (payload or {}).get("step", "DISCOVERY")
    state = orchestrator.retry_step(slug, step)
    return {"project": slug, "orchestrator": {
        "current_state": state.current_state,
        "blocked_reason": state.blocked_reason,
    }}


# ---------------------------------------------------------------------------
# AI infrastructure introspection (audit / cache / hooks)
# ---------------------------------------------------------------------------

@app.get("/api/ai/audit")
def ai_audit_log(
    limit: int = 50,
    event_type: str | None = None,
    _user: dict = Depends(auth.require_roles(
        "automation_engineer", "project_manager", "developer",
    )),
):
    return {
        "stats":  audit.stats(),
        "items":  audit.list_recent(limit=min(limit, 200), event_type=event_type),
    }


@app.get("/api/ai/cache")
def ai_cache_stats(
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    return llm_cache.stats()


@app.delete("/api/ai/cache")
def ai_cache_clear(
    _user: dict = Depends(auth.require_roles("automation_engineer")),
):
    return {"cleared": llm_cache.clear()}


@app.get("/api/ai/hooks")
def ai_hooks_list(
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    return hooks.list_subscribers()


@app.get("/api/ai/test-writer/frameworks")
async def ai_test_writer_frameworks(
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    return frameworks.list_with_status()


@app.post("/api/ai/test-writer/frameworks/{framework_id}/install")
async def ai_test_writer_install(
    framework_id: str,
    _user: dict = Depends(auth.require_roles("automation_engineer")),
):
    try:
        job = frameworks.start_install(framework_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {
        "framework_id": framework_id,
        "status": job.status,
        "started_at": job.started_at,
    }


@app.get("/api/ai/test-writer/frameworks/{framework_id}/install-status")
async def ai_test_writer_install_status(
    framework_id: str,
    _user: dict = Depends(auth.require_roles("automation_engineer", "project_manager")),
):
    if not frameworks.by_id(framework_id):
        raise HTTPException(404, f"unknown framework: {framework_id}")
    return frameworks.install_log(framework_id, tail=80)


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

    # Auto-fix every newly created cypress bug, serially in this thread so
    # parallel cypress runs don't fight for the same buggy-app server.
    for uid in run.get("bug_uids", []):
        _process_auto_fix(uid, loop)


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
