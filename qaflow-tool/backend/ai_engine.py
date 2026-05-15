"""AI engine — bug analysis and auto-fix generation.

Runs in two modes:
- mock (default): uses BUG_CATALOG to return a deterministic fix.
- claude: calls the Anthropic API when ANTHROPIC_API_KEY is set.

Both modes return the same shape so the rest of the system is identical.
"""

import os
from pathlib import Path

from bug_catalog import BUG_CATALOG


def _mock_analyze(bug_id: int, source_file: Path) -> dict:
    spec = BUG_CATALOG[bug_id]
    return {
        "mode": "mock",
        "bug_id": bug_id,
        "title": spec["title"],
        "type": spec["type"],
        "severity": spec["severity"],
        "file": spec["file"],
        "analysis": spec["analysis"],
        "old": spec["old"],
        "new": spec["new"],
        "confidence": spec["confidence"],
    }


def _claude_analyze(bug_id: int, source_file: Path) -> dict:
    """Real Claude API call. Asks the model to return a JSON patch."""
    import json
    import anthropic

    spec = BUG_CATALOG[bug_id]
    source = source_file.read_text()

    client = anthropic.Anthropic()
    prompt = f"""You are a senior front-end engineer reviewing a UI bug.

Bug title: {spec["title"]}
Bug type: {spec["type"]}
File under review: {spec["file"]}

Full source of the file:
---
{source}
---

Return ONLY a single JSON object with this exact shape:
{{
  "analysis": "<2-3 sentence root cause explanation>",
  "old": "<exact substring currently in the file that must be replaced>",
  "new": "<the replacement substring>",
  "confidence": <integer 0-100>
}}

Constraints:
- "old" must appear verbatim in the source above (whitespace exact).
- Make the smallest change that fixes the bug.
- Do not refactor unrelated code.
- No prose outside the JSON.
"""

    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    data = json.loads(raw)

    return {
        "mode": "claude",
        "bug_id": bug_id,
        "title": spec["title"],
        "type": spec["type"],
        "severity": spec["severity"],
        "file": spec["file"],
        "analysis": data["analysis"],
        "old": data["old"],
        "new": data["new"],
        "confidence": int(data["confidence"]),
    }


def analyze_and_fix(bug_id: int, source_file: Path) -> dict:
    """Returns a fix proposal for the given bug."""
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            return _claude_analyze(bug_id, source_file)
        except Exception as e:
            # Fall back to mock if API fails — demo must keep working
            result = _mock_analyze(bug_id, source_file)
            result["fallback_reason"] = f"claude_failed: {type(e).__name__}: {e}"
            return result
    return _mock_analyze(bug_id, source_file)


_CYPRESS_FIX_MODEL = "claude-opus-4-7"


def analyze_cypress_with_claude(
    bug_title: str,
    spec_file: Path,
    buggy_app_dir: Path,
    cypress_tests_dir: Path,
    *,
    bug_uid: str | None = None,
) -> dict:
    """Propose a fix for an arbitrary Cypress failure using Claude.

    Pipeline:
      1. Build the prompt from the versioned ``cypress_fix.v1`` template.
      2. Hash (model + prompt) and consult the LLM cache — early-return on hit.
      3. Otherwise call Claude, store the response, audit the call.
    """
    import json
    import time
    import anthropic
    import audit
    import hooks
    import llm_cache
    import prompt_loader

    spec_text = spec_file.read_text() if spec_file.exists() else ""

    def _read(p: Path, max_chars: int = 8000) -> str:
        try:
            text = p.read_text()
        except Exception:
            return ""
        return text if len(text) <= max_chars else text[:max_chars] + "\n…(truncated)"

    sources: dict[str, str] = {}
    support_dir = cypress_tests_dir / "cypress" / "support"
    if support_dir.exists():
        for p in sorted(support_dir.rglob("*.js")):
            rel = p.relative_to(cypress_tests_dir)
            sources[f"cypress-tests/{rel.as_posix()}"] = _read(p)

    public_dir = buggy_app_dir / "public"
    if public_dir.exists():
        for p in sorted(public_dir.rglob("*")):
            if p.is_file() and p.suffix in (".html", ".css", ".js"):
                rel = p.relative_to(buggy_app_dir)
                sources[f"buggy-app/{rel.as_posix()}"] = _read(p)

    files_block = "\n\n".join(
        f"### FILE: {path}\n```\n{content}\n```"
        for path, content in sources.items()
    )

    prompt = prompt_loader.load(
        "cypress_fix", "v1",
        bug_title=bug_title,
        spec_file_name=spec_file.name,
        spec_text=spec_text,
        files_block=files_block,
    )

    # ---- 1. cache lookup -----------------------------------------------
    started = time.time()
    cached = llm_cache.get(_CYPRESS_FIX_MODEL, prompt)
    if cached is not None:
        raw = cached
        was_cache_hit = True
    else:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=_CYPRESS_FIX_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        llm_cache.put(_CYPRESS_FIX_MODEL, prompt, raw)
        was_cache_hit = False

    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except Exception as e:
        audit.record(
            "cypress_fix", success=False,
            bug_uid=bug_uid, engine="claude", cache_hit=was_cache_hit,
            duration_ms=int((time.time() - started) * 1000),
            error=f"json_parse: {e}",
        )
        raise

    audit.record(
        "cypress_fix", success=True,
        bug_uid=bug_uid,
        engine="cache-hit" if was_cache_hit else "claude",
        cache_hit=was_cache_hit,
        duration_ms=int((time.time() - started) * 1000),
        summary=f"target={data.get('target_repo')} file={data.get('file')} confidence={data.get('confidence')}",
    )
    hooks.fire("on_ai_call", {
        "event_type": "cypress_fix", "bug_uid": bug_uid,
        "cache_hit": was_cache_hit,
        "duration_ms": int((time.time() - started) * 1000),
    })

    return {
        "mode": "claude",
        "target_repo": data["target_repo"],
        "file": data["file"],
        "old": data["old"],
        "new": data["new"],
        "analysis": data["analysis"],
        "confidence": int(data["confidence"]),
        "title": f"AI fix: {bug_title[:80]}",
        "type": "Cypress",
        "severity": "medium",
    }
