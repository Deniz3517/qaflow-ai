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
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import ai_engine
import auth
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


@app.get("/api/dashboard")
def dashboard(_user: dict = Depends(auth.current_user)):
    bugs = list(state["bugs"].values())
    active = sum(1 for b in bugs if b["status"] in ("DETECTED", "FIX_READY"))
    fixed = sum(1 for b in bugs if b["status"] == "MERGED")
    pending = sum(1 for b in bugs if b["status"] == "FIX_READY")
    rejected = sum(1 for b in bugs if b["status"] == "REJECTED")
    last_run = state["runs"][-1] if state["runs"] else None
    pass_rate = 0
    if last_run:
        total = last_run.get("total", 0)
        passed = last_run.get("passed", 0)
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


@app.post("/api/bugs/{bug_uid}/approve")
async def approve_bug(
    bug_uid: str,
    user: dict = Depends(auth.require_roles("developer")),
):
    bug = state["bugs"].get(bug_uid)
    if not bug:
        raise HTTPException(404, "bug not found")
    if bug["status"] != "FIX_READY":
        raise HTTPException(400, f"cannot approve in status {bug['status']}")
    fix = bug["fix"]
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
    return bug


@app.post("/api/bugs/{bug_uid}/reject")
async def reject_bug(
    bug_uid: str,
    user: dict = Depends(auth.require_roles("developer")),
):
    bug = state["bugs"].get(bug_uid)
    if not bug:
        raise HTTPException(404, "bug not found")
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
