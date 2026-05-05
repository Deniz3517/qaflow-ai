"""Local Dev2 sandbox.

Replaces the production VM model with a local file/process equivalent:
- A working copy of the buggy-app is kept under sandbox_workspace/bug-{id}/.
- Fix is applied there on a `bug/{id}` git branch.
- A short-lived Node server is started on a free port to capture the
  "after" screenshot.
- On approval the same fix is applied to the live buggy-app and committed.
"""

import os
import shutil
import socket
import subprocess
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BUGGY_APP = ROOT / "buggy-app"
SANDBOX_ROOT = Path(__file__).resolve().parent / "sandbox_workspace"


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _git(cwd: Path, *args: str, env_extra: dict | None = None):
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "QAFLOW AI")
    env.setdefault("GIT_AUTHOR_EMAIL", "ai@qaflow.local")
    env.setdefault("GIT_COMMITTER_NAME", "QAFLOW AI")
    env.setdefault("GIT_COMMITTER_EMAIL", "ai@qaflow.local")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def prepare_branch(bug_id: int) -> Path:
    """Create a fresh sandbox clone of the buggy-app on branch bug/{bug_id}.

    Returns the path to the sandbox working copy.
    """
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    workdir = SANDBOX_ROOT / f"bug-{bug_id}"
    if workdir.exists():
        shutil.rmtree(workdir)

    # Clone via filesystem path
    subprocess.run(
        ["git", "clone", "--quiet", str(BUGGY_APP), str(workdir)],
        check=True,
        capture_output=True,
        text=True,
    )

    branch = f"bug/{bug_id}"
    _git(workdir, "checkout", "-q", "-b", branch)
    return workdir


def apply_fix(workdir: Path, file_rel: str, old: str, new: str) -> dict:
    """Apply a literal substring replacement to a file in the sandbox.

    Returns the path of the modified file.
    Raises ValueError if `old` is not found or appears more than once.
    """
    target = workdir / file_rel
    if not target.exists():
        raise FileNotFoundError(f"file not in sandbox: {file_rel}")

    text = target.read_text()
    occurrences = text.count(old)
    if occurrences == 0:
        raise ValueError(f"`old` substring not found in {file_rel}")
    if occurrences > 1:
        raise ValueError(
            f"`old` substring is ambiguous in {file_rel} (found {occurrences} times)"
        )
    new_text = text.replace(old, new, 1)
    target.write_text(new_text)
    return {"file": file_rel, "before_bytes": len(text), "after_bytes": len(new_text)}


def commit_fix(workdir: Path, bug_id: int, message: str):
    _git(workdir, "add", "-A")
    _git(workdir, "commit", "-q", "-m", f"fix(bug/{bug_id}): {message}")


def serve_sandbox(workdir: Path) -> tuple[subprocess.Popen, int]:
    """Spawn the buggy-app server pointing at workdir, on a free port.

    Returns (process, port).
    """
    port = _free_port()
    env = os.environ.copy()
    env["PORT"] = str(port)
    proc = subprocess.Popen(
        ["node", "server.js"],
        cwd=str(workdir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Tiny wait for boot
    deadline = time.time() + 5
    while time.time() < deadline:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.connect(("127.0.0.1", port))
                return proc, port
            except OSError:
                time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("sandbox server failed to start")


def stop_sandbox(proc: subprocess.Popen):
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def merge_to_main(bug_id: int, file_rel: str, old: str, new: str, message: str):
    """Apply the same fix to the live buggy-app on main and commit.

    If the repo has an `origin` remote, the new commit is pushed too.
    Push failures don't fail the merge — the local commit still stands.
    """
    target = BUGGY_APP / file_rel
    text = target.read_text()
    if old not in text:
        # Already applied or drifted — do not double-apply
        return False
    target.write_text(text.replace(old, new, 1))
    _git(BUGGY_APP, "add", "-A")
    _git(BUGGY_APP, "commit", "-q", "-m", f"fix(bug/{bug_id}): {message}")
    _push_if_remote_configured(BUGGY_APP)
    return True


def _push_if_remote_configured(repo: Path) -> None:
    """Push HEAD to origin if it exists. Best-effort — never raises.

    If a plain push is rejected (the remote has diverged because the local
    state was reset between demos), fetch + rebase onto origin and push
    again. This keeps GitHub history monotonically growing.
    """
    try:
        remotes = subprocess.run(
            ["git", "remote"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.split()
    except subprocess.CalledProcessError:
        return
    if "origin" not in remotes:
        return

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo, capture_output=True, text=True,
    ).stdout.strip() or "main"

    push = subprocess.run(
        ["git", "push", "origin", "HEAD"],
        cwd=repo, capture_output=True, text=True,
    )
    if push.returncode == 0:
        return

    # Diverged — try fetch + rebase + push.
    try:
        subprocess.run(
            ["git", "fetch", "-q", "origin"],
            cwd=repo, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "rebase", f"origin/{branch}"],
            cwd=repo, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "push", "origin", "HEAD"],
            cwd=repo, check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError:
        # Conflict during rebase or push still failed — bail out cleanly.
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=repo, capture_output=True,
        )


def diff_for_branch(workdir: Path) -> str:
    out = _git(workdir, "diff", "main", "--unified=3")
    return out.stdout
