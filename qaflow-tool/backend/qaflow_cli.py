"""QAFLOW v2 CLI — drive the full pipeline from the terminal or CI.

Same logic as the REST endpoints but synchronous and stdout-friendly.
Auth is bypassed (CLI = automation_engineer caller).

Examples
--------

  qaflow discover --project demo --url http://localhost:3001/login.html
  qaflow smoke    --project demo --framework cypress-js
  qaflow smoke-run --project demo --framework cypress-js
  qaflow state    --project demo
  qaflow run-all  --project demo --url http://localhost:3001/login.html

  # Git mode
  qaflow discover --project mysite --mode git --repo-url https://github.com/foo/bar.git

  # PDF mode
  qaflow discover --project mysite --mode pdf --pdf-path ./spec.pdf

  # Output JSON for CI:
  qaflow state --project demo --format json

Exit codes
----------
  0   success
  1   LLM/runtime failure (network, JSON parse, install, etc.)
  2   gate failed (orchestrator state is BLOCKED after the step)
  3   bad input (missing env, bad args)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Make sure we run from the backend dir so relative imports resolve.
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import app_index           # noqa: E402
import orchestrator        # noqa: E402
import ai_engine           # noqa: E402
import bundle_runner       # noqa: E402
import test_writer         # noqa: E402
import frameworks          # noqa: E402
import source_extractors   # noqa: E402
import fakemail            # noqa: E402


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _emit(obj: Any, fmt: str) -> None:
    """Pretty-print JSON or terse human-readable depending on --format."""
    if fmt == "json":
        print(json.dumps(obj, indent=2, default=str))
        return
    # Human mode — opinionated condensation per shape.
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                print(f"{k}:")
                lines = json.dumps(v, indent=2, default=str).splitlines()
                for ln in lines[:40]:
                    print(f"  {ln}")
                if len(lines) > 40:
                    print(f"  …({len(lines) - 40} more lines, use --format json to see all)")
            else:
                print(f"{k}: {v}")
    else:
        print(obj)


def _die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _require_key() -> None:
    if os.environ.get("QAFLOW_MOCK_MODE", "").strip() in ("1", "true", "yes"):
        return    # mock mode satisfies the key requirement with fixtures
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _die("ANTHROPIC_API_KEY is not set (or set QAFLOW_MOCK_MODE=1 for dry-run)", code=3)


def _slug(p: str) -> str:
    return app_index.project_slug_from(p or "")


def _engine_for_prompt(framework_id: str) -> str:
    return {
        "cypress-js": "cypress", "cypress-ts": "cypress",
        "playwright-js": "playwright",
        "pytest-playwright": "pytest-playwright",
        "robot-py": "robot", "selenium-py": "selenium",
        "appium-py": "appium",
    }.get(framework_id, "cypress")


def _bundle_root(slug: str, framework_id: str, env: str | None) -> Path:
    spec = frameworks.by_id(framework_id) or _die(f"unknown framework: {framework_id}", 3)
    root = frameworks.QAFLOW_ROOT / "tests" / slug / f"{slug}-{spec.folder_name}"
    if env:
        root = root / env
    return root


def _list_relpaths(root: Path, subdir: str | None = None) -> list[str]:
    base = root / subdir if subdir else root
    if not base.exists():
        return []
    out: list[str] = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            out.append(str(p.relative_to(root)))
    return out


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_discover(args) -> int:
    _require_key()
    slug = _slug(args.project)

    if args.mode == "product" and not args.url:
        _die("--url is required for product mode", 3)
    if args.mode == "git" and not args.repo_url:
        _die("--repo-url is required for git mode", 3)
    if args.mode == "pdf" and not args.pdf_path:
        _die("--pdf-path is required for pdf mode", 3)

    scan: dict = {}
    crawl_pages: list[dict] = []
    git_index_text: str | None = None
    pdf_excerpt_text: str | None = None

    mock_active = os.environ.get("QAFLOW_MOCK_MODE", "").strip() in ("1", "true", "yes")

    print(f"[discover] mode={args.mode} project={slug}"
          + (" (MOCK)" if mock_active else ""), file=sys.stderr)
    if mock_active:
        print("[discover] mock mode — skipping real scan/crawl/clone, "
              "ai_engine will return a fixture APP_INDEX", file=sys.stderr)
    elif args.mode == "product":
        print(f"[discover] scanning {args.url} (with network capture)…", file=sys.stderr)
        scan = test_writer.scan_page(args.url, None, False, True)
        if args.max_pages > 1:
            print(f"[discover] crawling up to {args.max_pages} pages…", file=sys.stderr)
            crawl_pages = test_writer.crawl_pages(
                args.url, args.max_pages, True, None, True,
            )
    elif args.mode == "git":
        print(f"[discover] cloning {args.repo_url}…", file=sys.stderr)
        git_index_text = source_extractors.extract_git_index(args.repo_url)
    elif args.mode == "pdf":
        print(f"[discover] extracting pdf {args.pdf_path}…", file=sys.stderr)
        pdf_excerpt_text = source_extractors.extract_pdf(args.pdf_path)

    test_users: list[dict] = []
    if args.auto_users:
        roles = (args.auto_users or "").split(",")
        test_users = fakemail.provision_test_users([{"role": r.strip()} for r in roles if r.strip()])
        print(f"[discover] auto-provisioned test users: {[u['email'] for u in test_users]}", file=sys.stderr)

    orchestrator.mark_step_started(slug, "DISCOVERY")
    try:
        index = ai_engine.run_discovery(
            project_slug=slug, source_mode=args.mode, target_url=args.url,
            scan=scan, crawl_pages_list=crawl_pages,
            auth_config=None, test_users=test_users,
            git_index=git_index_text, pdf_excerpt=pdf_excerpt_text,
            crawl_max_pages=args.max_pages,
        )
    except Exception as e:
        orchestrator.record_step_result(slug, "DISCOVERY",
            success=False, error=f"{type(e).__name__}: {e}")
        _die(f"discovery LLM call failed: {e}", 1)

    index["project_slug"] = slug
    app_index.save(slug, index)

    seed_traffic = list(scan.get("network_requests") or [])
    for page in crawl_pages or []:
        seed_traffic.extend(page.get("network_requests") or [])
    app_index.append_traffic(slug, seed_traffic)

    state = orchestrator.record_step_result(
        slug, "DISCOVERY", success=True,
        summary=f"pages={len(index.get('pages') or [])}",
    )

    _emit({
        "project": slug,
        "pages": len(index.get("pages") or []),
        "apis_detected": len(index.get("discovered_apis") or []),
        "risk_flags": index.get("risk_flags") or [],
        "next_step_recommendation": index.get("next_step_recommendation"),
        "current_state": state.current_state,
    }, args.format)

    return 0 if state.current_state != "BLOCKED" else 2


def cmd_smoke(args) -> int:
    return _do_gen(args, "SMOKE_GEN", "smoke_gen")


def cmd_e2e(args) -> int:
    return _do_gen(args, "E2E_GEN", "e2e_gen")


def cmd_negative(args) -> int:
    return _do_gen(args, "NEGATIVE_GEN", "negative_gen")


def _do_gen(args, step_name: str, slice_key: str) -> int:
    _require_key()
    slug = _slug(args.project)
    index = app_index.load(slug) or _die(f"no app_index for {slug} — run discover first", 3)
    spec = frameworks.by_id(args.framework) or _die(f"unknown framework: {args.framework}", 3)
    base_url = (index.get("application") or {}).get("base_url") or args.url or ""

    orchestrator.mark_step_started(slug, step_name)
    print(f"[{step_name.lower()}] generating with {spec.name}…", file=sys.stderr)
    try:
        slice_ = app_index.slice_for_prompt(index, slice_key)
        if step_name == "SMOKE_GEN":
            data = ai_engine.run_smoke_generation(
                project_slug=slug, framework=_engine_for_prompt(args.framework),
                language=spec.language, framework_folder=spec.folder_name,
                base_url=base_url, app_index_slice=slice_,
            )
        elif step_name == "E2E_GEN":
            bundle = _bundle_root(slug, args.framework, args.env)
            snap = orchestrator.progress_snapshot(slug)
            smoke_meta: dict = next(
                (h for h in reversed(snap.get("history") or [])
                 if h.get("step") == "SMOKE_RUN" and h.get("success")),
                {},
            )
            data = ai_engine.run_e2e_generation(
                project_slug=slug, framework=_engine_for_prompt(args.framework),
                language=spec.language, framework_folder=spec.folder_name,
                base_url=base_url,
                app_index_json=app_index.slice_for_prompt(index, "e2e_gen"),
                smoke_pass_rate=int(smoke_meta.get("pass_rate_pct") or 100),
                smoke_duration_s=float((smoke_meta.get("finished_at") or 0) - (smoke_meta.get("started_at") or 0)),
                smoke_spec_count=int((smoke_meta.get("artifacts") or {}).get("specs_executed") or 0),
                existing_artifacts=_list_relpaths(bundle),
            )
        else:   # NEGATIVE_GEN
            bundle = _bundle_root(slug, args.framework, args.env)
            data = ai_engine.run_negative_generation(
                project_slug=slug, framework=_engine_for_prompt(args.framework),
                language=spec.language, framework_folder=spec.folder_name,
                base_url=base_url,
                app_index_json=app_index.slice_for_prompt(index, "negative_gen"),
                smoke_filenames=_list_relpaths(bundle, "smoke"),
                e2e_filenames=_list_relpaths(bundle, "e2e"),
                discovered_apis=index.get("discovered_apis") or [],
            )
    except Exception as e:
        orchestrator.record_step_result(slug, step_name,
            success=False, error=f"{type(e).__name__}: {e}")
        _die(f"{step_name} LLM call failed: {e}", 1)

    files = data.get("files") or {}
    if not files:
        orchestrator.record_step_result(slug, step_name, success=False, error="no_files")
        _die("LLM returned no files", 1)

    saved = test_writer.save_bundle(
        files={str(k): str(v) for k, v in files.items()},
        framework=args.framework, project=slug, env=args.env,
    )
    state = orchestrator.record_step_result(
        slug, step_name, success=True,
        summary=data.get("summary") or "",
        files_generated=len(files),
        artifacts={"bundle_root": saved.get("bundle_root")},
    )

    _emit({
        "project": slug, "step": step_name,
        "files_generated": len(files),
        "bundle_root": saved.get("bundle_root"),
        "summary": data.get("summary"),
        "expected_pass_rate_pct": data.get("expected_pass_rate_pct"),
        "current_state": state.current_state,
    }, args.format)
    return 0


def cmd_run(args, step_name: str, sub: str, require_gate: bool) -> int:
    slug = _slug(args.project)
    bundle = _bundle_root(slug, args.framework, args.env)
    if not bundle.exists():
        _die(f"bundle not found at {bundle} — generate the suite first", 3)

    print(f"[{step_name.lower()}] running {sub} suite under {bundle}…", file=sys.stderr)
    orchestrator.mark_step_started(slug, step_name)

    def _on_line(line: str) -> None:
        if args.verbose:
            print(line, file=sys.stderr)

    result = bundle_runner.run(bundle, args.framework, sub, _on_line)

    # Auto-install on first run.
    if result.get("install_required"):
        print(f"[{step_name.lower()}] deps missing — installing once…", file=sys.stderr)
        inst = bundle_runner.install_bundle_deps(bundle, args.framework)
        if not inst.get("ok"):
            orchestrator.record_step_result(slug, step_name,
                success=False, pass_rate_pct=0, error="install_failed")
            _emit({"error": "install_failed", "log_tail": (inst.get("log") or "")[-2000:]},
                  args.format)
            return 1
        result = bundle_runner.run(bundle, args.framework, sub, _on_line)

    if result.get("unsupported_framework"):
        orchestrator.record_step_result(slug, step_name,
            success=True, pass_rate_pct=100, summary="skipped: runner not implemented")
        _emit({"step": step_name, "skipped": True, "reason": "runner_not_implemented"},
              args.format)
        return 0

    pass_rate = int(result.get("pass_rate_pct") or 0)
    total = int(result.get("total") or 0)
    success = total > 0
    state = orchestrator.record_step_result(
        slug, step_name, success=success, pass_rate_pct=pass_rate,
        summary=f"passed={result.get('passed')}/{total} ({pass_rate}%)",
        artifacts={"specs_executed": total, "failed": result.get("failed"),
                   "duration_s": result.get("duration_s")},
        error=None if success else "no_tests_executed",
    )

    _emit({
        "project": slug, "step": step_name,
        "passed": result.get("passed"), "failed": result.get("failed"),
        "total": total, "pass_rate_pct": pass_rate,
        "duration_s": result.get("duration_s"),
        "current_state": state.current_state,
        "blocked_reason": state.blocked_reason,
    }, args.format)

    if state.current_state == "BLOCKED":
        return 2
    return 0


def cmd_api_discovery(args) -> int:
    _require_key()
    slug = _slug(args.project)
    index = app_index.load(slug) or _die(f"no app_index for {slug}", 3)

    traffic_path = app_index.traffic_path(slug)
    traffic: list[dict] = []
    if traffic_path.exists():
        for line in traffic_path.read_text().splitlines():
            line = line.strip()
            if not line: continue
            try: traffic.append(json.loads(line))
            except Exception: continue
    traffic = traffic[-500:]

    stack = ((index.get("application") or {}).get("detected_stack") or {})
    base_url = (index.get("application") or {}).get("base_url") or ""

    orchestrator.mark_step_started(slug, "API_DISCOVERY")
    print(f"[api-discovery] synthesizing OpenAPI from {len(traffic)} traffic records…",
          file=sys.stderr)
    try:
        data = ai_engine.run_api_discovery(
            project_slug=slug, base_url=base_url,
            backend_stack=stack.get("backend") or "",
            auth_type=stack.get("auth_type") or "none",
            discovered_apis=index.get("discovered_apis") or [],
            traffic_dump=traffic,
        )
    except Exception as e:
        orchestrator.record_step_result(slug, "API_DISCOVERY",
            success=False, error=f"{type(e).__name__}: {e}")
        _die(f"api discovery failed: {e}", 1)

    yaml_text = (data.get("openapi_yaml") or "").strip()
    if yaml_text:
        out_path = app_index.index_path(slug).parent / "openapi.yaml"
        out_path.write_text(yaml_text)
    orchestrator.record_step_result(slug, "API_DISCOVERY",
        success=True, summary=f"ops={data.get('operations_count')}")

    _emit({
        "project": slug,
        "operations_count": data.get("operations_count"),
        "tag_counts": data.get("tag_counts") or {},
        "coverage_warnings": data.get("coverage_warnings") or [],
        "openapi_path": str(app_index.index_path(slug).parent / "openapi.yaml"),
    }, args.format)
    return 0


def cmd_validate(args) -> int:
    _require_key()
    slug = _slug(args.project)
    index = app_index.load(slug) or _die(f"no app_index for {slug}", 3)
    bundle = _bundle_root(slug, args.framework, args.env)

    snap = orchestrator.progress_snapshot(slug)
    def _last(name: str) -> dict:
        for h in reversed(snap.get("history") or []):
            if h.get("step") == name and h.get("success"):
                return h
        return {}

    orchestrator.mark_step_started(slug, "VALIDATION")
    print("[validate] producing handover doc + verdict…", file=sys.stderr)
    from datetime import datetime, timezone
    try:
        data = ai_engine.run_validation(
            project_slug=slug,
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            base_url=(index.get("application") or {}).get("base_url") or "",
            app_index_obj=index,
            smoke_summary=_last("SMOKE_RUN") or _last("SMOKE_GEN"),
            e2e_summary=_last("E2E_RUN") or _last("E2E_GEN"),
            negative_summary=_last("NEGATIVE_RUN") or _last("NEGATIVE_GEN"),
            api_discovery_summary=_last("API_DISCOVERY"),
            bundle_inventory=_list_relpaths(bundle),
            risk_flags=index.get("risk_flags") or [],
        )
    except Exception as e:
        orchestrator.record_step_result(slug, "VALIDATION",
            success=False, error=f"{type(e).__name__}: {e}")
        _die(f"validation failed: {e}", 1)

    report_md = data.get("report_md") or ""
    if report_md.strip():
        out = app_index.index_path(slug).parent / "report.md"
        out.write_text(report_md)
    orchestrator.record_step_result(slug, "VALIDATION",
        success=True, summary=f"verdict={data.get('verdict')}")

    _emit({
        "project": slug,
        "verdict": data.get("verdict"),
        "verdict_reason": data.get("verdict_reason"),
        "totals": data.get("totals") or {},
        "top_risks": data.get("top_risks") or [],
        "report_path": str(app_index.index_path(slug).parent / "report.md"),
    }, args.format)
    return 0 if data.get("verdict") != "RED" else 2


def cmd_state(args) -> int:
    slug = _slug(args.project)
    _emit(orchestrator.progress_snapshot(slug), args.format)
    return 0


def cmd_retry(args) -> int:
    slug = _slug(args.project)
    st = orchestrator.retry_step(slug, args.step)
    _emit({"project": slug, "current_state": st.current_state,
           "blocked_reason": st.blocked_reason}, args.format)
    return 0


def cmd_run_all(args) -> int:
    """Convenience: discovery → smoke → smoke-run → e2e → e2e-run → negative
                  → negative-run → api-discovery → validate."""
    chain = [
        ("discovery",  cmd_discover, {"mode": args.mode, "url": args.url,
                                       "repo_url": args.repo_url, "pdf_path": args.pdf_path,
                                       "max_pages": args.max_pages, "auto_users": args.auto_users}),
        ("smoke",      cmd_smoke,    {}),
        ("smoke-run",  lambda a: cmd_run(a, "SMOKE_RUN", "smoke", True), {}),
        ("e2e",        cmd_e2e,      {}),
        ("e2e-run",    lambda a: cmd_run(a, "E2E_RUN", "e2e", True), {}),
        ("negative",   cmd_negative, {}),
        ("negative-run", lambda a: cmd_run(a, "NEGATIVE_RUN", "negative", False), {}),
        ("api-discovery", cmd_api_discovery, {}),
        ("validate",   cmd_validate, {}),
    ]
    for name, fn, extra in chain:
        print(f"\n=== {name} ===", file=sys.stderr)
        sub_args = argparse.Namespace(**vars(args))
        for k, v in extra.items():
            setattr(sub_args, k, v)
        code = fn(sub_args)
        if code != 0:
            print(f"[run-all] {name} returned {code} — stopping chain", file=sys.stderr)
            return code
    return 0


def cmd_provision_users(args) -> int:
    """Generate stable test users for a project (fakemail bridge)."""
    roles = [r.strip() for r in (args.roles or "admin,viewer").split(",") if r.strip()]
    users = fakemail.provision_test_users([{"role": r} for r in roles])
    _emit({
        "provider": fakemail.discover_default_provider(),
        "test_users": users,
    }, args.format)
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _add_project_args(p, with_framework: bool = True, with_env: bool = True):
    p.add_argument("--project", required=True, help="Project slug")
    p.add_argument("--format", choices=["json", "human"], default="human")
    if with_framework:
        p.add_argument("--framework", default="cypress-js",
                       help="Backend framework id (cypress-js, playwright-js, robot-py, ...)")
    if with_env:
        p.add_argument("--env", default=None, help="Optional sub-folder under {project}-{framework}/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qaflow",
        description="QAFLOW v2 multi-step AI test-generation pipeline (CLI)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # discover
    p = sub.add_parser("discover", help="Step 1 — produce APP_INDEX")
    _add_project_args(p, with_framework=False, with_env=False)
    p.add_argument("--mode", choices=["product", "git", "pdf"], default="product")
    p.add_argument("--url")
    p.add_argument("--repo-url", dest="repo_url")
    p.add_argument("--pdf-path", dest="pdf_path")
    p.add_argument("--max-pages", dest="max_pages", type=int, default=8)
    p.add_argument("--auto-users", dest="auto_users",
                   help="Comma-separated roles to auto-provision (e.g. admin,viewer)")
    p.set_defaults(func=cmd_discover)

    # smoke / e2e / negative — generate-only
    for name, fn in (("smoke", cmd_smoke), ("e2e", cmd_e2e), ("negative", cmd_negative)):
        p = sub.add_parser(name, help=f"Step — generate {name} tests")
        _add_project_args(p)
        p.add_argument("--url", help="Override base url (defaults to APP_INDEX.application.base_url)")
        p.set_defaults(func=fn)

    # *-run — execute the bundle
    for cli, step, sub_dir, gate in (
        ("smoke-run", "SMOKE_RUN", "smoke", True),
        ("e2e-run",   "E2E_RUN",   "e2e",   True),
        ("negative-run", "NEGATIVE_RUN", "negative", False),
    ):
        p = sub.add_parser(cli, help=f"Step — execute the {sub_dir} bundle")
        _add_project_args(p)
        p.add_argument("--verbose", "-v", action="store_true", help="Stream runner stdout")
        p.set_defaults(func=lambda a, s=step, d=sub_dir, g=gate: cmd_run(a, s, d, g))

    # api-discovery
    p = sub.add_parser("api-discovery", help="Step 5 — synthesize openapi.yaml")
    _add_project_args(p, with_framework=False, with_env=False)
    p.set_defaults(func=cmd_api_discovery)

    # validate
    p = sub.add_parser("validate", help="Step 6 — handover report + verdict")
    _add_project_args(p)
    p.set_defaults(func=cmd_validate)

    # state
    p = sub.add_parser("state", help="Show orchestrator state for a project")
    _add_project_args(p, with_framework=False, with_env=False)
    p.set_defaults(func=cmd_state)

    # retry
    p = sub.add_parser("retry", help="Reset orchestrator to re-run a step")
    _add_project_args(p, with_framework=False, with_env=False)
    p.add_argument("--step", required=True,
                   help="Step name to retry (DISCOVERY, SMOKE_RUN, ...)")
    p.set_defaults(func=cmd_retry)

    # provision-users
    p = sub.add_parser("provision-users", help="Generate stable test users (fakemail)")
    p.add_argument("--roles", default="admin,viewer",
                   help="Comma-separated role names (default: admin,viewer)")
    p.add_argument("--format", choices=["json", "human"], default="human")
    p.set_defaults(func=cmd_provision_users)

    # run-all
    p = sub.add_parser("run-all", help="Run the entire pipeline end-to-end")
    _add_project_args(p)
    p.add_argument("--mode", choices=["product", "git", "pdf"], default="product")
    p.add_argument("--url")
    p.add_argument("--repo-url", dest="repo_url")
    p.add_argument("--pdf-path", dest="pdf_path")
    p.add_argument("--max-pages", dest="max_pages", type=int, default=8)
    p.add_argument("--auto-users", dest="auto_users")
    p.add_argument("--verbose", "-v", action="store_true")
    p.set_defaults(func=cmd_run_all)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
