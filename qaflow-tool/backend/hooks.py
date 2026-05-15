"""Lightweight hook bus for AI-loop extension points.

Goal: make customer-specific behaviour (Slack notify, Jira ticket, custom
approval policy, audit shipping) pluggable without touching the core AI flow.

Hook points emitted by the QAFLOW core today:

  on_bug_detected   (bug_dict)            — cypress flagged a new fail
  before_apply      (bug, fix) -> bool    — chance to veto a patch
  on_fix_ready      (bug, fix)            — patch verified, awaits approval
  on_merged         (bug, fix, merged)    — approval landed on main
  on_rejected       (bug, fix)            — dev rejected the AI fix
  on_ai_call        (event_dict)          — every LLM call (cache hit too)

Subscribers register at boot via ``hooks.register("on_fix_ready", callback)``.
Registration is idempotent — same callable registered twice is stored once.

All callbacks are best-effort: an exception in one subscriber never blocks
the loop or other subscribers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("qaflow.hooks")

_registry: dict[str, list[Callable[..., Any]]] = {}


def register(event: str, fn: Callable[..., Any]) -> None:
    bucket = _registry.setdefault(event, [])
    if fn not in bucket:
        bucket.append(fn)


def unregister(event: str, fn: Callable[..., Any]) -> None:
    bucket = _registry.get(event)
    if bucket and fn in bucket:
        bucket.remove(fn)


def fire(event: str, *args: Any, **kwargs: Any) -> list[Any]:
    """Fire ``event`` and return each subscriber's return value (or its exception).

    For veto-style hooks (e.g. ``before_apply``) callers inspect the return
    list and treat any explicit ``False`` as a veto.
    """
    out: list[Any] = []
    for fn in list(_registry.get(event, [])):
        try:
            out.append(fn(*args, **kwargs))
        except Exception as e:  # never let a hook break the loop
            log.exception("hook %s subscriber %s failed: %s", event, fn, e)
            out.append(e)
    return out


def list_subscribers() -> dict[str, list[str]]:
    """For the admin UI — show what's wired."""
    return {
        event: [getattr(fn, "__name__", repr(fn)) for fn in fns]
        for event, fns in _registry.items()
    }


# ---------------------------------------------------------------------------
# Built-in subscribers (always-on, enabled by default).
# Customer plugins typically add to this list at startup.
# ---------------------------------------------------------------------------

def _log_bug_detected(bug: dict) -> None:
    print(f"[hook] bug detected #{bug.get('id')} — {bug.get('title')}", flush=True)


def _log_fix_ready(bug: dict, fix: dict) -> None:
    print(
        f"[hook] fix ready for #{bug.get('id')} on {fix.get('file')} "
        f"(engine={fix.get('mode')}, confidence={fix.get('confidence')})",
        flush=True,
    )


def _log_merged(bug: dict, fix: dict, merged: bool) -> None:
    print(
        f"[hook] bug #{bug.get('id')} "
        f"{'MERGED' if merged else 'merge skipped'} — branch={bug.get('branch')}",
        flush=True,
    )


def install_defaults() -> None:
    register("on_bug_detected", _log_bug_detected)
    register("on_fix_ready",    _log_fix_ready)
    register("on_merged",       _log_merged)
