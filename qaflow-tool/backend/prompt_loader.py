"""Versioned prompt loader.

Prompts live in ``prompts/{name}.{version}.md`` so we can:
  - track changes in git as ordinary diffs,
  - A/B test prompt versions without touching Python,
  - regression-test prompt outputs (paste the file into an evaluator).

The header lines starting with ``#`` are treated as YAML-ish metadata and
stripped from the prompt body before substitution.
"""

from __future__ import annotations

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load(name: str, version: str = "v1", **fmt_vars: str) -> str:
    """Return the prompt body for ``{name}.{version}.md`` with vars substituted.

    Vars use ``{var_name}`` style; only top-level keys you pass in are
    substituted, double-braces stay literal so JSON examples survive.
    """
    path = PROMPTS_DIR / f"{name}.{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    raw = path.read_text()

    # Strip header comments — anything before the first non-blank, non-`#` line.
    lines = raw.splitlines()
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            body_start = i
            break
    body = "\n".join(lines[body_start:])

    # Use `.format()` semantics. Callers escape literal braces by doubling them
    # in the markdown file ({{ and }}).
    if fmt_vars:
        body = body.format(**fmt_vars)
    return body


def list_prompts() -> list[dict]:
    """List every available prompt + version for the audit / admin UI."""
    out = []
    if not PROMPTS_DIR.exists():
        return out
    for p in sorted(PROMPTS_DIR.glob("*.md")):
        m = re.match(r"^(?P<name>[^.]+)\.(?P<version>v\d+)\.md$", p.name)
        if not m:
            continue
        out.append({
            "name":    m.group("name"),
            "version": m.group("version"),
            "path":    str(p),
            "bytes":   p.stat().st_size,
        })
    return out
