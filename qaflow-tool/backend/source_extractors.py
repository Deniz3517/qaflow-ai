"""Source extractors for non-Product discovery modes.

Mode B (Git) and Mode C (PDF) feed the discovery prompt with structured
text describing the application. This module turns each input format
into a token-bounded block the prompt can consume.

Public API:
    extract_pdf(pdf_path: str)             -> str
    extract_git_index(repo_url: str)        -> str
    extract_local_repo_index(local_path: str) -> str
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

_PDF_MAX_PAGES = 50
_PDF_MAX_CHARS_PER_PAGE = 4000
_PDF_TOTAL_BUDGET = 30_000


def extract_pdf(pdf_path: str | Path) -> str:
    """Extract structured text from a PDF spec document.

    Returns a markdown-ish block with:
      - per-page headers
      - URL / endpoint references called out
      - rough heading detection (lines short + title-cased + followed by paragraph)

    Best-effort — if pypdf cannot read the file, returns an error string the
    discovery prompt can still consume.
    """
    p = Path(pdf_path)
    if not p.exists():
        return f"(pdf not found: {p})"

    try:
        from pypdf import PdfReader
    except ImportError:
        return f"(pypdf not installed — install pypdf to enable PDF mode)"

    try:
        reader = PdfReader(str(p))
    except Exception as e:
        return f"(pdf read failed: {type(e).__name__}: {e})"

    total_pages = len(reader.pages)
    budget = _PDF_TOTAL_BUDGET
    chunks: list[str] = [f"# PDF Source: {p.name}", f"Total pages: {total_pages}", ""]

    endpoints_seen: set[str] = set()
    urls_seen: set[str] = set()

    for i, page in enumerate(reader.pages[:_PDF_MAX_PAGES]):
        if budget <= 0:
            chunks.append(f"\n…(remaining {total_pages - i} pages truncated for prompt budget)")
            break
        try:
            text = page.extract_text() or ""
        except Exception as e:
            text = f"(page extract failed: {e})"

        text = text[:_PDF_MAX_CHARS_PER_PAGE]

        # Detect URL + endpoint mentions inline (these are the most useful
        # signal for the discovery prompt's API surface inference).
        for u in re.findall(r"https?://[^\s)>\]]+", text):
            urls_seen.add(u.rstrip(".,;:"))
        for ep in re.findall(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_/{}\-\.]+)", text):
            endpoints_seen.add(f"{ep[0]} {ep[1]}")

        # Detect rough headings — short lines, title-cased, followed by content.
        headings: list[str] = []
        lines = text.splitlines()
        for j, line in enumerate(lines):
            stripped = line.strip()
            if 4 <= len(stripped) <= 80 and stripped[:1].isupper() and stripped.count(" ") <= 8:
                # Followed by a longer line within 3 lines?
                if any(len((lines[k] if k < len(lines) else "").strip()) > 40
                       for k in range(j + 1, min(j + 4, len(lines)))):
                    headings.append(stripped)

        block = f"\n## Page {i + 1}\n"
        if headings:
            block += "Headings detected:\n" + "\n".join(f"  - {h}" for h in headings[:8]) + "\n"
        block += "\n" + text + "\n"
        chunks.append(block)
        budget -= len(block)

    if urls_seen:
        chunks.append("\n## URLs mentioned in document")
        chunks.extend(f"  - {u}" for u in sorted(urls_seen)[:30])
    if endpoints_seen:
        chunks.append("\n## API endpoint patterns mentioned")
        chunks.extend(f"  - {e}" for e in sorted(endpoints_seen)[:40])

    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

_GIT_CLONE_TIMEOUT_S = 60
_GIT_INDEX_BUDGET = 30_000

# File-extension allowlist for content snippets we feed back to the prompt.
_SOURCE_EXT = (".html", ".js", ".jsx", ".ts", ".tsx", ".py", ".vue", ".svelte")


def extract_git_index(repo_url: str) -> str:
    """Shallow-clone repo_url to a temp dir and produce a structured index.

    Returns a multi-section markdown block:
      - detected framework
      - route table (Next.js / FastAPI / Express / React Router style)
      - API handlers
      - component names
      - selected source excerpts

    Cleans up the temp clone on exit.
    """
    tmp = Path(tempfile.mkdtemp(prefix="qaflow-git-src-"))
    try:
        try:
            subprocess.run(
                ["git", "clone", "--depth=1", "--quiet", repo_url, str(tmp)],
                check=True, capture_output=True, text=True, timeout=_GIT_CLONE_TIMEOUT_S,
            )
        except subprocess.CalledProcessError as e:
            return f"(git clone failed: rc={e.returncode}\n{(e.stderr or '')[:1000]})"
        except subprocess.TimeoutExpired:
            return f"(git clone timed out after {_GIT_CLONE_TIMEOUT_S}s)"

        return extract_local_repo_index(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def extract_local_repo_index(local_path: str | Path) -> str:
    """Same as extract_git_index but against an already-cloned/local repo dir.

    Useful for testing the extractor without hitting the network.
    """
    root = Path(local_path)
    if not root.exists():
        return f"(local repo not found: {root})"

    framework = _detect_framework(root)
    routes = _extract_routes(root, framework)
    api_handlers = _extract_api_handlers(root, framework)
    components = _extract_components(root)
    snippets = _select_source_snippets(root)

    chunks: list[str] = []
    chunks.append(f"# Git Source Index — {root.name}")
    chunks.append(f"Detected framework: {framework or 'unknown'}")
    chunks.append("")

    if routes:
        chunks.append("## Routes detected")
        chunks.extend(f"  - {r}" for r in routes[:80])
        chunks.append("")

    if api_handlers:
        chunks.append("## API handlers")
        chunks.extend(f"  - {h}" for h in api_handlers[:80])
        chunks.append("")

    if components:
        chunks.append("## Component / page module names")
        chunks.extend(f"  - {c}" for c in components[:60])
        chunks.append("")

    if snippets:
        chunks.append("## Selected source excerpts (token-bounded)")
        chunks.append(snippets)

    text = "\n".join(chunks)
    return text[:_GIT_INDEX_BUDGET]


def _detect_framework(root: Path) -> str | None:
    """Detect the dominant framework from manifest files."""
    pkg = root / "package.json"
    if pkg.exists():
        try:
            content = pkg.read_text()
        except Exception:
            content = ""
        if '"next"' in content:        return "Next.js"
        if '"@remix-run/' in content:  return "Remix"
        if '"vite"' in content:        return "Vite"
        if '"@angular/core"' in content: return "Angular"
        if '"vue"' in content:         return "Vue"
        if '"react"' in content and '"react-router' in content: return "React + React Router"
        if '"react"' in content:       return "React"
        if '"express"' in content:     return "Express (Node)"
        if '"fastify"' in content:     return "Fastify (Node)"
        return "Node.js"
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        files = " ".join(p.name for p in root.iterdir())
        try:
            req_content = ""
            for f in ("requirements.txt", "pyproject.toml"):
                fp = root / f
                if fp.exists():
                    req_content += fp.read_text()
            if "fastapi" in req_content.lower():  return "FastAPI"
            if "django" in req_content.lower():   return "Django"
            if "flask" in req_content.lower():    return "Flask"
        except Exception:
            pass
        return "Python"
    if (root / "Cargo.toml").exists():            return "Rust"
    if (root / "go.mod").exists():                return "Go"
    return None


_RX_FASTAPI_ROUTE  = re.compile(r"@\w+\.(get|post|put|patch|delete)\(['\"]([^'\"]+)['\"]")
_RX_FLASK_ROUTE    = re.compile(r"@\w+\.route\(['\"]([^'\"]+)['\"](?:.*?methods\s*=\s*\[([^\]]+)\])?")
_RX_DJANGO_PATH    = re.compile(r"path\(['\"]([^'\"]+)['\"]")
_RX_EXPRESS_ROUTE  = re.compile(r"\b(?:app|router)\.(get|post|put|patch|delete)\(['\"]([^'\"]+)['\"]")
_RX_REACT_ROUTE    = re.compile(r"<Route\s+path=['\"]([^'\"]+)['\"]")


def _extract_routes(root: Path, framework: str | None) -> list[str]:
    """Walk the repo and pull route definitions matching common frameworks."""
    routes: list[str] = []

    # Next.js / Remix: filesystem-based routing → pages/* or app/*
    for dir_name in ("pages", "app"):
        d = root / dir_name
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix not in (".jsx", ".tsx", ".js", ".ts"):
                continue
            if p.name.startswith("_") or p.name in ("layout.tsx", "layout.js"):
                continue
            rel = p.relative_to(d)
            url_path = "/" + "/".join(rel.with_suffix("").parts)
            url_path = re.sub(r"\[(\.\.\.)?(\w+)\]", r"{\2}", url_path)  # [id] → {id}
            url_path = url_path.replace("/index", "/")
            routes.append(f"FILE-ROUTE {url_path}  ({rel})")

    # Walk source files for explicit-route patterns.
    for p in _iter_source_files(root):
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue

        for m in _RX_FASTAPI_ROUTE.finditer(text):
            routes.append(f"{m.group(1).upper()} {m.group(2)}  ({p.relative_to(root)})")
        for m in _RX_FLASK_ROUTE.finditer(text):
            methods = (m.group(2) or "GET").replace("'", "").replace('"', "")
            routes.append(f"{methods.strip()} {m.group(1)}  ({p.relative_to(root)})")
        for m in _RX_DJANGO_PATH.finditer(text):
            routes.append(f"DJANGO {m.group(1)}  ({p.relative_to(root)})")
        for m in _RX_EXPRESS_ROUTE.finditer(text):
            routes.append(f"{m.group(1).upper()} {m.group(2)}  ({p.relative_to(root)})")
        for m in _RX_REACT_ROUTE.finditer(text):
            routes.append(f"REACT-ROUTE {m.group(1)}  ({p.relative_to(root)})")

    # Dedup, keep ordering.
    seen: set[str] = set()
    out: list[str] = []
    for r in routes:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def _extract_api_handlers(root: Path, framework: str | None) -> list[str]:
    """Pull names + signatures of API handler functions.

    Cheap heuristic: any function preceded by a route decorator OR named
    `handler_*` / placed under `routes/` `api/` `endpoints/`.
    """
    handlers: list[str] = []
    for p in _iter_source_files(root):
        rel = p.relative_to(root)
        if not any(part in str(rel) for part in ("api/", "routes/", "endpoints/", "handlers/")):
            continue
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        for m in re.finditer(r"^\s*(async\s+def|def|export\s+(?:async\s+)?function)\s+(\w+)\s*\(([^)]{0,200})\)",
                             text, re.MULTILINE):
            handlers.append(f"{rel}: {m.group(2)}({m.group(3).strip()[:80]})")
    return handlers


def _extract_components(root: Path) -> list[str]:
    """Component / page module names — the rough page graph."""
    out: list[str] = []
    for dir_name in ("src", "app", "pages", "components"):
        d = root / dir_name
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file() or p.suffix not in _SOURCE_EXT:
                continue
            stem = p.stem
            if stem[:1].isupper() or stem in ("index", "page", "layout"):
                out.append(f"{p.relative_to(root)}")
        if len(out) > 60:
            break
    return out


def _iter_source_files(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix not in _SOURCE_EXT:
            continue
        # Skip vendored / generated directories.
        parts = set(p.parts)
        if parts & {"node_modules", ".next", "dist", "build", ".venv", "__pycache__"}:
            continue
        if p.stat().st_size > 200_000:   # skip oversized files
            continue
        yield p


def _select_source_snippets(root: Path) -> str:
    """Pick a handful of files most likely to inform the discovery prompt."""
    candidates: list[Path] = []
    priority_patterns = (
        "App.tsx", "App.jsx", "App.vue", "main.tsx", "main.ts", "main.py",
        "router.tsx", "router.ts", "routes.py", "urls.py", "index.html",
        "schema.py", "models.py", "auth.py", "settings.py",
    )
    for p in _iter_source_files(root):
        if p.name in priority_patterns or any(part in str(p) for part in ("router", "routes")):
            candidates.append(p)
        if len(candidates) >= 8:
            break

    out_parts: list[str] = []
    budget = 8000
    for p in candidates:
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        snippet = text[:1500]
        block = f"\n### FILE: {p.relative_to(root)}\n```\n{snippet}\n```\n"
        if len(block) > budget:
            break
        budget -= len(block)
        out_parts.append(block)
    return "".join(out_parts)
