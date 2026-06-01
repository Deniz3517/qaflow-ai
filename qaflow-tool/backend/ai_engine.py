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
_DISCOVERY_MODEL = "claude-opus-4-7"
_SMOKE_MODEL = "claude-opus-4-7"
_E2E_MODEL = "claude-opus-4-7"
_NEGATIVE_MODEL = "claude-opus-4-7"
_API_DISCOVERY_MODEL = "claude-opus-4-7"
_VALIDATION_MODEL = "claude-opus-4-7"
_EXTEND_MODEL = "claude-opus-4-7"


def _strip_json_fences(raw: str) -> str:
    """Tolerate AI responses wrapped in ```json fences or with leading prose."""
    s = raw.strip()
    if s.startswith("```"):
        # remove opening fence + optional language tag
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
    # If there's leading text before the first { or [, drop it.
    for i, ch in enumerate(s):
        if ch in "{[":
            return s[i:].strip()
    return s.strip()


def run_discovery(
    project_slug: str,
    source_mode: str,
    target_url: str | None,
    scan: dict,
    crawl_pages_list: list[dict] | None = None,
    *,
    auth_config: dict | None = None,
    test_users: list[dict] | None = None,
    git_index: str | None = None,
    pdf_excerpt: str | None = None,
    crawl_max_pages: int = 8,
) -> dict:
    """STEP 1 — produce APP_INDEX via the discovery.v1 prompt.

    Caller is responsible for persisting the returned index via app_index.save().
    Raises on JSON parse error so the orchestrator can mark DISCOVERY blocked.
    """
    import json as _json
    import time
    import anthropic
    import audit
    import hooks
    import llm_cache
    import prompt_loader

    def _j(v) -> str:
        return _json.dumps(v or [], ensure_ascii=False)[:8000]

    prompt = prompt_loader.load(
        "discovery", "v1",
        project_slug=project_slug,
        source_mode=source_mode,
        target_url=target_url or "",
        crawl_max_pages=str(crawl_max_pages),
        scan_title=str(scan.get("title") or ""),
        scan_headings_json=_j(scan.get("headings")),
        scan_buttons_json=_j(scan.get("buttons")),
        scan_links_json=_j(scan.get("links")),
        scan_inputs_json=_j(scan.get("inputs")),
        scan_forms_json=_j(scan.get("forms")),
        scan_landmarks_json=_j(scan.get("landmarks")),
        scan_images_json=_j(scan.get("images")),
        console_messages_json=_j(scan.get("console_messages")),
        page_errors_json=_j(scan.get("page_errors")),
        network_requests_json=_j(scan.get("network_requests")),
        crawl_pages_json=_j(crawl_pages_list),
        git_index_block=(git_index or "(not applicable — source mode is not 'git')"),
        pdf_excerpt_block=(pdf_excerpt or "(not applicable — source mode is not 'pdf')"),
        auth_config_block=_json.dumps(auth_config or {}, ensure_ascii=False),
        test_users_block=_json.dumps(test_users or [], ensure_ascii=False),
    )

    started = time.time()
    cached = llm_cache.get(_DISCOVERY_MODEL, prompt)
    if cached is not None:
        raw = cached
        was_cache_hit = True
    else:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=_DISCOVERY_MODEL,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text
        llm_cache.put(_DISCOVERY_MODEL, prompt, raw)
        was_cache_hit = False

    try:
        index = _json.loads(_strip_json_fences(raw))
    except Exception as e:
        audit.record(
            "discovery", success=False,
            engine="cache-hit" if was_cache_hit else "claude",
            cache_hit=was_cache_hit,
            duration_ms=int((time.time() - started) * 1000),
            error=f"json_parse: {e}",
        )
        raise

    audit.record(
        "discovery", success=True,
        engine="cache-hit" if was_cache_hit else "claude",
        cache_hit=was_cache_hit,
        duration_ms=int((time.time() - started) * 1000),
        summary=(
            f"pages={len(index.get('pages') or [])} "
            f"apis={len(index.get('discovered_apis') or [])} "
            f"next={index.get('next_step_recommendation')}"
        ),
    )
    hooks.fire("on_ai_call", {
        "event_type": "discovery",
        "project_slug": project_slug,
        "cache_hit": was_cache_hit,
        "duration_ms": int((time.time() - started) * 1000),
    })

    return index


def run_smoke_generation(
    project_slug: str,
    framework: str,
    language: str,
    framework_folder: str,
    base_url: str,
    app_index_slice: dict,
) -> dict:
    """STEP 2 — produce the smoke test suite via the smoke.v1 prompt.

    Returns: {files: {path: contents}, summary, specs_generated,
              expected_pass_rate_pct, fragility_notes, deferred_to_e2e}
    """
    import json as _json
    import time
    import anthropic
    import audit
    import hooks
    import llm_cache
    import prompt_loader

    prompt = prompt_loader.load(
        "smoke", "v1",
        framework=framework,
        language=language,
        project_slug=project_slug,
        framework_folder=framework_folder,
        base_url=base_url,
        app_index_slice_json=_json.dumps(app_index_slice, ensure_ascii=False)[:20000],
    )

    started = time.time()
    cached = llm_cache.get(_SMOKE_MODEL, prompt)
    if cached is not None:
        raw = cached
        was_cache_hit = True
    else:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=_SMOKE_MODEL,
            max_tokens=16_384,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text
        llm_cache.put(_SMOKE_MODEL, prompt, raw)
        was_cache_hit = False

    try:
        data = _json.loads(_strip_json_fences(raw))
    except Exception as e:
        audit.record(
            "smoke_gen", success=False,
            engine="cache-hit" if was_cache_hit else "claude",
            cache_hit=was_cache_hit,
            duration_ms=int((time.time() - started) * 1000),
            error=f"json_parse: {e}",
        )
        raise

    files = data.get("files") or {}
    audit.record(
        "smoke_gen", success=True,
        framework_id=framework,
        engine="cache-hit" if was_cache_hit else "claude",
        cache_hit=was_cache_hit,
        duration_ms=int((time.time() - started) * 1000),
        summary=(
            f"files={len(files)} "
            f"expected_pass={data.get('expected_pass_rate_pct')}% "
            f"fragilities={len(data.get('fragility_notes') or [])}"
        ),
    )
    hooks.fire("on_ai_call", {
        "event_type": "smoke_gen",
        "project_slug": project_slug,
        "cache_hit": was_cache_hit,
        "duration_ms": int((time.time() - started) * 1000),
    })

    return data


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


def _run_test_gen(
    *,
    event_type: str,
    model: str,
    prompt: str,
    framework: str | None = None,
    project_slug: str | None = None,
) -> dict:
    """Shared LLM dispatch for smoke / e2e / negative generation.

    Wraps cache lookup, Anthropic call, JSON-fence stripping, audit, and
    hook firing in one place so the per-step helpers stay tiny.
    """
    import json as _json
    import time
    import anthropic
    import audit
    import hooks
    import llm_cache

    started = time.time()
    cached = llm_cache.get(model, prompt)
    if cached is not None:
        raw = cached
        was_cache_hit = True
    else:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model, max_tokens=16_384,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text
        llm_cache.put(model, prompt, raw)
        was_cache_hit = False

    try:
        data = _json.loads(_strip_json_fences(raw))
    except Exception as e:
        audit.record(
            event_type, success=False,
            framework_id=framework,
            engine="cache-hit" if was_cache_hit else "claude",
            cache_hit=was_cache_hit,
            duration_ms=int((time.time() - started) * 1000),
            error=f"json_parse: {e}",
        )
        raise

    audit.record(
        event_type, success=True,
        framework_id=framework,
        engine="cache-hit" if was_cache_hit else "claude",
        cache_hit=was_cache_hit,
        duration_ms=int((time.time() - started) * 1000),
        summary=(
            f"files={len(data.get('files') or {})} "
            f"specs={data.get('specs_generated')} "
            f"expected_pass={data.get('expected_pass_rate_pct')}%"
        ),
    )
    hooks.fire("on_ai_call", {
        "event_type": event_type,
        "project_slug": project_slug,
        "cache_hit": was_cache_hit,
        "duration_ms": int((time.time() - started) * 1000),
    })
    return data


def run_e2e_generation(
    project_slug: str,
    framework: str,
    language: str,
    framework_folder: str,
    base_url: str,
    app_index_json: dict,
    smoke_pass_rate: int,
    smoke_duration_s: float,
    smoke_spec_count: int,
    existing_artifacts: list[str] | None = None,
) -> dict:
    """STEP 3 — produce the E2E journey suite via the e2e.v1 prompt."""
    import json as _json
    import prompt_loader

    prompt = prompt_loader.load(
        "e2e", "v1",
        framework=framework, language=language,
        project_slug=project_slug, framework_folder=framework_folder,
        base_url=base_url,
        app_index_json=_json.dumps(app_index_json, ensure_ascii=False)[:24_000],
        smoke_pass_rate=str(smoke_pass_rate),
        smoke_duration_s=str(int(smoke_duration_s)),
        smoke_spec_count=str(smoke_spec_count),
        existing_artifacts_json=_json.dumps(existing_artifacts or [], ensure_ascii=False),
    )

    return _run_test_gen(
        event_type="e2e_gen", model=_E2E_MODEL, prompt=prompt,
        framework=framework, project_slug=project_slug,
    )


def run_negative_generation(
    project_slug: str,
    framework: str,
    language: str,
    framework_folder: str,
    base_url: str,
    app_index_json: dict,
    smoke_filenames: list[str],
    e2e_filenames: list[str],
    discovered_apis: list[dict],
) -> dict:
    """STEP 4 — produce the negative + edge-case suite via the negative.v1 prompt."""
    import json as _json
    import prompt_loader

    prompt = prompt_loader.load(
        "negative", "v1",
        framework=framework, language=language,
        project_slug=project_slug, framework_folder=framework_folder,
        base_url=base_url,
        app_index_json=_json.dumps(app_index_json, ensure_ascii=False)[:24_000],
        smoke_filenames_json=_json.dumps(smoke_filenames, ensure_ascii=False),
        e2e_filenames_json=_json.dumps(e2e_filenames, ensure_ascii=False),
        discovered_apis_json=_json.dumps(discovered_apis, ensure_ascii=False)[:12_000],
    )

    return _run_test_gen(
        event_type="negative_gen", model=_NEGATIVE_MODEL, prompt=prompt,
        framework=framework, project_slug=project_slug,
    )


def run_api_discovery(
    project_slug: str,
    base_url: str,
    backend_stack: str,
    auth_type: str,
    discovered_apis: list[dict],
    traffic_dump: list[dict],
) -> dict:
    """STEP 5 — produce an OpenAPI 3.1 YAML from observed traffic.

    Returns {openapi_yaml, operations_count, tag_counts, auth_schemes_detected,
             coverage_warnings, redactions_count}.
    """
    import json as _json
    import prompt_loader

    prompt = prompt_loader.load(
        "api_discovery", "v1",
        project_slug=project_slug, base_url=base_url,
        backend_stack=backend_stack or "(unknown)",
        auth_type=auth_type or "none",
        discovered_apis_json=_json.dumps(discovered_apis, ensure_ascii=False)[:12_000],
        traffic_dump_json=_json.dumps(traffic_dump, ensure_ascii=False)[:24_000],
    )

    return _run_test_gen(
        event_type="api_discovery", model=_API_DISCOVERY_MODEL, prompt=prompt,
        project_slug=project_slug,
    )


def run_validation(
    project_slug: str,
    generated_at: str,
    base_url: str,
    app_index_obj: dict,
    smoke_summary: dict,
    e2e_summary: dict,
    negative_summary: dict,
    api_discovery_summary: dict,
    bundle_inventory: list[str],
    risk_flags: list[str],
) -> dict:
    """STEP 6 — final handover doc + GREEN/YELLOW/RED verdict."""
    import json as _json
    import prompt_loader

    prompt = prompt_loader.load(
        "validation", "v1",
        project_slug=project_slug, generated_at=generated_at, base_url=base_url,
        app_index_json=_json.dumps(app_index_obj, ensure_ascii=False)[:24_000],
        smoke_summary_json=_json.dumps(smoke_summary, ensure_ascii=False),
        e2e_summary_json=_json.dumps(e2e_summary, ensure_ascii=False),
        negative_summary_json=_json.dumps(negative_summary, ensure_ascii=False),
        api_discovery_summary_json=_json.dumps(api_discovery_summary, ensure_ascii=False),
        bundle_inventory_json=_json.dumps(bundle_inventory, ensure_ascii=False)[:8_000],
        risk_flags_json=_json.dumps(risk_flags, ensure_ascii=False),
    )

    return _run_test_gen(
        event_type="validation", model=_VALIDATION_MODEL, prompt=prompt,
        project_slug=project_slug,
    )


def run_extend(
    project_slug: str,
    framework: str,
    language: str,
    framework_folder: str,
    base_url: str,
    app_index_obj: dict,
    inventory: list[str],
    gaps: list[str],
    delta_scan: dict,
    validation_summary: dict,
) -> dict:
    """Incremental extension — accepts user-reported gaps + delta scan,
    returns new_files / modified_files / app_index_patch."""
    import json as _json
    import prompt_loader

    prompt = prompt_loader.load(
        "extend", "v1",
        project_slug=project_slug, framework=framework, language=language,
        framework_folder=framework_folder, base_url=base_url,
        app_index_json=_json.dumps(app_index_obj, ensure_ascii=False)[:24_000],
        inventory_json=_json.dumps(inventory, ensure_ascii=False)[:8_000],
        gaps_json=_json.dumps(gaps, ensure_ascii=False),
        delta_scan_json=_json.dumps(delta_scan, ensure_ascii=False)[:16_000],
        validation_summary_json=_json.dumps(validation_summary, ensure_ascii=False),
    )

    return _run_test_gen(
        event_type="extend", model=_EXTEND_MODEL, prompt=prompt,
        framework=framework, project_slug=project_slug,
    )
