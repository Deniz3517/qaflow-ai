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
