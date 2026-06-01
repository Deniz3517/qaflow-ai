"""APP_INDEX — the shared application architecture document.

This is the single source of truth produced by STEP 1 (Discovery) and
consumed by every later step (smoke / e2e / negative / api-discovery /
validation / extend). All 3 source modes (product URL, git repo, PDF
spec) converge to this same schema so the downstream pipeline is
mode-agnostic.

Stored at:
    tests/{project_slug}/.qaflow/app_index.json

Helpers:
    - load / save                — disk I/O
    - slice_for_prompt           — return only the fields a given step needs
                                   so we don't blow the LLM context window
    - merge_extension            — apply an /extend patch from a follow-up run
    - validate                   — shallow structural check (typed access)

Kept intentionally as TypedDicts + plain dicts (not full pydantic models)
so the rest of the backend keeps the same dict-based ergonomics it uses
everywhere else.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Literal, TypedDict


SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Type sketches (informational — actual values are plain dicts at runtime)
# ---------------------------------------------------------------------------

SourceMode = Literal["product", "git", "pdf"]
PageImportance = Literal["critical", "high", "medium", "low"]
StepName = Literal[
    "discovery", "smoke_gen", "smoke_run", "smoke_gate",
    "e2e_gen", "e2e_run", "e2e_gate",
    "negative_gen", "negative_run",
    "api_discovery", "validation", "extend",
]


class _Source(TypedDict, total=False):
    mode: SourceMode
    url: str | None
    repo_url: str | None
    pdf_path: str | None


class _Application(TypedDict, total=False):
    name: str
    detected_stack: dict
    base_url: str | None
    environments: list[dict]


class _Page(TypedDict, total=False):
    id: str
    path: str
    purpose: str
    importance: PageImportance
    requires_auth: bool
    elements: dict
    apis_called: list[str]
    error_states_seen: list[str]
    load_metrics: dict
    accessibility_notes: list[str]
    test_recommendations: list[str]
    discovered_subpages: list[str]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _project_root(project_slug: str) -> Path:
    # Imported lazily to avoid a circular import — frameworks imports app_index? no.
    # Keep the dependency one-way: app_index -> frameworks (for QAFLOW_ROOT only).
    import frameworks as fwmod
    return fwmod.QAFLOW_ROOT / "tests" / _safe_slug(project_slug)


def _safe_slug(s: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9._-]+", "-", s or "").strip("-").lower()
    return out.lstrip(".") or "default"


def index_path(project_slug: str) -> Path:
    return _project_root(project_slug) / ".qaflow" / "app_index.json"


def state_path(project_slug: str) -> Path:
    """Path to the orchestrator's persisted state for this project."""
    return _project_root(project_slug) / ".qaflow" / "orchestrator_state.json"


def traffic_path(project_slug: str) -> Path:
    """Cumulative HTTP traffic dump produced by test runs — feeds api_discovery."""
    return _project_root(project_slug) / ".qaflow" / "traffic_dump.jsonl"


def append_traffic(project_slug: str, records: list[dict]) -> int:
    """Append observed request/response pairs to the project's traffic dump.

    Each line is one JSON object. The api_discovery prompt later tails the
    last N lines to keep its context bounded.
    Returns number of records written.
    """
    if not records:
        return 0
    p = traffic_path(project_slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with p.open("a", encoding="utf-8") as f:
        for rec in records:
            try:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
            except Exception:
                continue
    return written


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def empty(project_slug: str, source: dict) -> dict:
    """Return a freshly-initialized APP_INDEX skeleton.

    Useful for the orchestrator's INIT state so downstream code never has
    to handle the "no index yet" branch — it always sees the schema.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "project_slug": _safe_slug(project_slug),
        "generated_at": _now_iso(),
        "source": {
            "mode": source.get("mode", "product"),
            "url": source.get("url"),
            "repo_url": source.get("repo_url"),
            "pdf_path": source.get("pdf_path"),
        },
        "application": {
            "name": project_slug,
            "detected_stack": {},
            "base_url": source.get("url"),
            "environments": [],
        },
        "pages": [],
        "navigation": {"primary": [], "footer": []},
        "auth_flow": {"type": "none"},
        "test_users": [],
        "discovered_apis": [],
        "risk_flags": [],
        "mobile_relevant": False,
        "performance_budgets": {"page_load_p95_ms": 3000, "api_p95_ms": 500},
        "next_step_recommendation": "smoke",
        "blocker_reason": None,
        "generation_history": [],
    }


def load(project_slug: str) -> dict | None:
    p = index_path(project_slug)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def save(project_slug: str, index: dict) -> Path:
    p = index_path(project_slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    index = {**index, "generated_at": _now_iso()}
    p.write_text(json.dumps(index, indent=2, ensure_ascii=False))
    return p


def append_history(project_slug: str, entry: dict) -> dict:
    """Append a generation_history entry and persist. Returns updated index."""
    idx = load(project_slug)
    if idx is None:
        raise FileNotFoundError(f"no APP_INDEX for project: {project_slug}")
    history = idx.setdefault("generation_history", [])
    history.append({**entry, "completed_at": _now_iso()})
    save(project_slug, idx)
    return idx


# ---------------------------------------------------------------------------
# Slicing — keep prompts lean
# ---------------------------------------------------------------------------

def slice_for_prompt(index: dict, step: StepName) -> dict:
    """Return only the fields a given step needs in its prompt context.

    The full APP_INDEX can grow large (50+ pages, hundreds of APIs). Each
    LLM step only needs a focused slice; this keeps token usage bounded
    and makes prompt caching more effective.
    """
    common = {
        "project_slug": index.get("project_slug"),
        "application": index.get("application"),
        "auth_flow": index.get("auth_flow"),
        "test_users": index.get("test_users"),
        "risk_flags": index.get("risk_flags"),
        "performance_budgets": index.get("performance_budgets"),
        "mobile_relevant": index.get("mobile_relevant"),
    }

    if step == "smoke_gen":
        # Smoke covers ONLY critical pages.
        critical_pages = [
            p for p in (index.get("pages") or [])
            if (p.get("importance") in ("critical", "high"))
        ]
        return {
            **common,
            "pages": critical_pages,
            "navigation": index.get("navigation"),
        }

    if step == "e2e_gen":
        # E2E sees everything plus what smoke produced.
        return {
            **common,
            "pages": index.get("pages") or [],
            "navigation": index.get("navigation"),
            "discovered_apis": index.get("discovered_apis"),
        }

    if step == "negative_gen":
        return {
            **common,
            "pages": index.get("pages") or [],
            "discovered_apis": index.get("discovered_apis"),
        }

    if step == "api_discovery":
        return {"discovered_apis_preliminary": index.get("discovered_apis")}

    if step == "validation":
        # Validation gets the whole thing — it writes the handover doc.
        return index

    # Default: full index
    return index


# ---------------------------------------------------------------------------
# Extension merge
# ---------------------------------------------------------------------------

def merge_extension(index: dict, patch: dict) -> dict:
    """Apply an /extend output patch in-place. Returns the merged index.

    Patch shape (from extend.v1 prompt):
        {
          "pages_added":      [<Page>...],
          "history_appended": [<HistoryEntry>...],
          "apis_added":       [<Api>...]    # optional
        }
    """
    pages = index.setdefault("pages", [])
    existing_ids = {p.get("id") for p in pages if p.get("id")}
    for new_page in patch.get("pages_added") or []:
        if new_page.get("id") in existing_ids:
            continue
        pages.append(new_page)
        existing_ids.add(new_page.get("id"))

    history = index.setdefault("generation_history", [])
    for h in patch.get("history_appended") or []:
        history.append(h)

    if patch.get("apis_added"):
        apis = index.setdefault("discovered_apis", [])
        seen = {(a.get("method"), a.get("path")) for a in apis}
        for api in patch["apis_added"]:
            key = (api.get("method"), api.get("path"))
            if key in seen:
                continue
            apis.append(api)
            seen.add(key)

    return index


# ---------------------------------------------------------------------------
# Light validation — fail fast on malformed AI output
# ---------------------------------------------------------------------------

REQUIRED_TOP_LEVEL = (
    "schema_version", "application", "pages", "auth_flow",
    "next_step_recommendation",
)


class IndexValidationError(ValueError):
    pass


def validate(index: dict) -> None:
    """Shallow structural check — raises IndexValidationError if malformed.

    Deep schema enforcement is intentionally left to the LLM's JSON output
    discipline; we only check that the orchestrator can step on it without
    blowing up.
    """
    if not isinstance(index, dict):
        raise IndexValidationError("index must be a dict")
    for key in REQUIRED_TOP_LEVEL:
        if key not in index:
            raise IndexValidationError(f"missing required field: {key}")
    if not isinstance(index["pages"], list):
        raise IndexValidationError("pages must be a list")
    for i, page in enumerate(index["pages"]):
        if not isinstance(page, dict):
            raise IndexValidationError(f"pages[{i}] must be a dict")
        if "id" not in page or "path" not in page:
            raise IndexValidationError(f"pages[{i}] missing id or path")
    rec = index.get("next_step_recommendation")
    if rec not in ("smoke", "blocked"):
        raise IndexValidationError(
            f"next_step_recommendation must be 'smoke' or 'blocked', got {rec!r}"
        )


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_slug_from(name: str) -> str:
    """Public access to slugger — endpoints use this to normalize input."""
    return _safe_slug(name)
