"""Multi-step test-generation orchestrator.

Coordinates the DISCOVERY → SMOKE → E2E → NEGATIVE → API_DISCOVERY →
VALIDATION pipeline. Each step is idempotent and persisted to disk so a
crashed/interrupted run can resume from the last completed state.

State lives at: tests/{project_slug}/.qaflow/orchestrator_state.json

The orchestrator does NOT call the LLM itself — it tells main.py which
step to invoke next. Endpoints implement the actual generation by calling
ai_engine helpers; they report results back via record_step_result().

State machine:

    INIT
     │
     ▼
    DISCOVERY         (LLM call 1)
     │  ── gate: APP_INDEX validates, next_step_recommendation == "smoke"
     ▼
    SMOKE_GEN         (LLM call 2)
     │
     ▼
    SMOKE_RUN         (real test runner)
     │
     ▼
    SMOKE_GATE        (pass_rate >= 80 → continue; else BLOCKED)
     │
     ▼
    E2E_GEN
     │
     ▼
    E2E_RUN
     │
     ▼
    E2E_GATE          (pass_rate >= 60 → continue; else BLOCKED)
     │
     ▼
    NEGATIVE_GEN
     │
     ▼
    NEGATIVE_RUN      (syntax/compile check + dry run)
     │
     ▼
    API_DISCOVERY     (synthesize openapi.yaml from accumulated traffic)
     │
     ▼
    VALIDATION        (handover doc + verdict)
     │
     ▼
    DONE
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import app_index


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------

State = Literal[
    "INIT",
    "DISCOVERY",
    "SMOKE_GEN",
    "SMOKE_RUN",
    "SMOKE_GATE",
    "E2E_GEN",
    "E2E_RUN",
    "E2E_GATE",
    "NEGATIVE_GEN",
    "NEGATIVE_RUN",
    "API_DISCOVERY",
    "VALIDATION",
    "DONE",
    "BLOCKED",
    "EXTEND",      # out-of-band step; can fire from DONE or any other state
]


# Ordered forward-progress map. Branches (gate failures) are handled in step().
_FORWARD: dict[State, State] = {
    "INIT":          "DISCOVERY",
    "DISCOVERY":     "SMOKE_GEN",
    "SMOKE_GEN":     "SMOKE_RUN",
    "SMOKE_RUN":     "SMOKE_GATE",
    "SMOKE_GATE":    "E2E_GEN",
    "E2E_GEN":       "E2E_RUN",
    "E2E_RUN":       "E2E_GATE",
    "E2E_GATE":      "NEGATIVE_GEN",
    "NEGATIVE_GEN":  "NEGATIVE_RUN",
    "NEGATIVE_RUN":  "API_DISCOVERY",
    "API_DISCOVERY": "VALIDATION",
    "VALIDATION":    "DONE",
}


SMOKE_GATE_PASS_PCT = 80
E2E_GATE_PASS_PCT = 60


# ---------------------------------------------------------------------------
# Persisted state record
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    step: State
    success: bool
    started_at: float
    finished_at: float | None = None
    summary: str = ""
    pass_rate_pct: int | None = None      # for *_RUN steps
    files_generated: int | None = None    # for *_GEN steps
    error: str | None = None
    artifacts: dict = field(default_factory=dict)


@dataclass
class OrchestratorState:
    project_slug: str
    current_state: State = "INIT"
    blocked_reason: str | None = None
    history: list[dict] = field(default_factory=list)   # list of StepResult dicts

    def to_dict(self) -> dict:
        return {
            "project_slug": self.project_slug,
            "current_state": self.current_state,
            "blocked_reason": self.blocked_reason,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OrchestratorState":
        return cls(
            project_slug=d["project_slug"],
            current_state=d.get("current_state", "INIT"),
            blocked_reason=d.get("blocked_reason"),
            history=d.get("history") or [],
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_state(project_slug: str) -> OrchestratorState:
    """Load orchestrator state from disk, creating a fresh one if absent."""
    p = app_index.state_path(project_slug)
    if not p.exists():
        return OrchestratorState(project_slug=app_index.project_slug_from(project_slug))
    try:
        return OrchestratorState.from_dict(json.loads(p.read_text()))
    except Exception:
        # Don't crash on corrupted state — start over.
        return OrchestratorState(project_slug=app_index.project_slug_from(project_slug))


def save_state(state: OrchestratorState) -> Path:
    p = app_index.state_path(state.project_slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False))
    return p


# ---------------------------------------------------------------------------
# Step planning — which step to execute next
# ---------------------------------------------------------------------------

def next_step(state: OrchestratorState) -> State:
    """Return the next state to execute, or the current state if terminal."""
    if state.current_state in ("DONE", "BLOCKED"):
        return state.current_state
    return _FORWARD.get(state.current_state, "DONE")


def is_terminal(s: State) -> bool:
    return s in ("DONE", "BLOCKED")


# ---------------------------------------------------------------------------
# Gates — pure functions; endpoints call these explicitly
# ---------------------------------------------------------------------------

def smoke_gate_passed(pass_rate_pct: int) -> bool:
    return pass_rate_pct >= SMOKE_GATE_PASS_PCT


def e2e_gate_passed(pass_rate_pct: int) -> bool:
    return pass_rate_pct >= E2E_GATE_PASS_PCT


def discovery_gate_passed(index: dict) -> tuple[bool, str | None]:
    """APP_INDEX-level gate: structurally valid + recommends moving on."""
    try:
        app_index.validate(index)
    except app_index.IndexValidationError as e:
        return (False, f"index_invalid: {e}")
    if index.get("next_step_recommendation") != "smoke":
        return (False, f"discovery_blocked: {index.get('blocker_reason')}")
    if not any(p.get("importance") == "critical" for p in index.get("pages") or []):
        return (False, "no_critical_pages_detected")
    return (True, None)


# ---------------------------------------------------------------------------
# Step result recording — single entry point endpoints call after each step
# ---------------------------------------------------------------------------

def record_step_result(
    project_slug: str,
    step: State,
    *,
    success: bool,
    summary: str = "",
    pass_rate_pct: int | None = None,
    files_generated: int | None = None,
    error: str | None = None,
    artifacts: dict | None = None,
) -> OrchestratorState:
    """Append a step result and advance state.

    Advancement rules:
      - success=False  → state becomes BLOCKED, blocked_reason = error|step.
      - DISCOVERY → run discovery_gate (loads APP_INDEX from disk).
      - SMOKE_RUN → run smoke_gate.
      - E2E_RUN   → run e2e_gate.
      - otherwise advance to _FORWARD[step].
    """
    import time

    state = load_state(project_slug)

    entry = {
        "step": step,
        "success": success,
        "started_at": _last_started_or_now(state, step),
        "finished_at": time.time(),
        "summary": summary,
        "pass_rate_pct": pass_rate_pct,
        "files_generated": files_generated,
        "error": error,
        "artifacts": artifacts or {},
    }
    state.history.append(entry)

    if not success:
        state.current_state = "BLOCKED"
        state.blocked_reason = error or f"{step}_failed"
        save_state(state)
        return state

    # Gate logic on successful completion.
    if step == "DISCOVERY":
        idx = app_index.load(project_slug)
        if idx is None:
            state.current_state = "BLOCKED"
            state.blocked_reason = "discovery_completed_but_index_missing"
            save_state(state)
            return state
        ok, reason = discovery_gate_passed(idx)
        if not ok:
            state.current_state = "BLOCKED"
            state.blocked_reason = reason
            save_state(state)
            return state
        state.current_state = _FORWARD["DISCOVERY"]
        save_state(state)
        return state

    if step == "SMOKE_RUN":
        if pass_rate_pct is None or not smoke_gate_passed(pass_rate_pct):
            state.current_state = "BLOCKED"
            state.blocked_reason = (
                f"smoke_gate_failed: pass_rate={pass_rate_pct}% "
                f"required>={SMOKE_GATE_PASS_PCT}%"
            )
            save_state(state)
            return state
        state.current_state = _FORWARD["SMOKE_RUN"]
        save_state(state)
        # Walk through SMOKE_GATE virtual state (we don't pause on gate states).
        state.current_state = _FORWARD[state.current_state]
        save_state(state)
        return state

    if step == "E2E_RUN":
        if pass_rate_pct is None or not e2e_gate_passed(pass_rate_pct):
            state.current_state = "BLOCKED"
            state.blocked_reason = (
                f"e2e_gate_failed: pass_rate={pass_rate_pct}% "
                f"required>={E2E_GATE_PASS_PCT}%"
            )
            save_state(state)
            return state
        state.current_state = _FORWARD["E2E_RUN"]
        save_state(state)
        state.current_state = _FORWARD[state.current_state]
        save_state(state)
        return state

    # EXTEND is out-of-band: completing it returns the project to DONE
    # (the suite is once again "complete" with the new coverage merged).
    if step == "EXTEND":
        state.current_state = "DONE"
        save_state(state)
        return state

    # Default forward progression.
    state.current_state = _FORWARD.get(step, "DONE")
    save_state(state)
    return state


def _last_started_or_now(state: OrchestratorState, step: State) -> float:
    """Find when this step was started — fallback to now."""
    import time
    for h in reversed(state.history):
        if h.get("step") == step and h.get("started_at"):
            return h["started_at"]
    return time.time()


def mark_step_started(project_slug: str, step: State) -> OrchestratorState:
    """Set current_state to step and record a started_at marker."""
    import time
    state = load_state(project_slug)
    state.current_state = step
    state.blocked_reason = None
    state.history.append({
        "step": step,
        "success": None,
        "started_at": time.time(),
        "finished_at": None,
    })
    save_state(state)
    return state


# ---------------------------------------------------------------------------
# Reset — used by retry_step
# ---------------------------------------------------------------------------

def retry_step(project_slug: str, step: State) -> OrchestratorState:
    """Reset state to JUST BEFORE the given step so it can be re-run."""
    # Find which state leads INTO this step.
    predecessor = None
    for src, dst in _FORWARD.items():
        if dst == step:
            predecessor = src
            break
    state = load_state(project_slug)
    state.current_state = predecessor or "INIT"
    state.blocked_reason = None
    save_state(state)
    return state


# ---------------------------------------------------------------------------
# Reporting / introspection
# ---------------------------------------------------------------------------

def progress_snapshot(project_slug: str) -> dict:
    """Compact dict for the live progress board in the frontend."""
    state = load_state(project_slug)
    return {
        "project_slug": state.project_slug,
        "current_state": state.current_state,
        "blocked_reason": state.blocked_reason,
        "is_terminal": is_terminal(state.current_state),
        "history": state.history[-20:],   # last 20 entries
        "thresholds": {
            "smoke_gate_pct": SMOKE_GATE_PASS_PCT,
            "e2e_gate_pct": E2E_GATE_PASS_PCT,
        },
    }
