"""Integration tests for the QAFLOW v2 multi-step pipeline.

Runs the full DISCOVERY → SMOKE_GEN → SMOKE_RUN → ... → VALIDATION → DONE
chain under QAFLOW_MOCK_MODE so we exercise every backend module without
calling Claude. These tests double as living documentation: a fresh dev
can read them top-to-bottom and learn the public surface of every module.

Usage:
    cd qaflow-tool/backend
    .venv/bin/pip install pytest
    QAFLOW_MOCK_MODE=1 .venv/bin/pytest tests/test_pipeline_mock.py -v

The tests redirect QAFLOW's tests/ root to a tmp_path so they never
pollute the real tests/{project}/ directory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# Put the backend dir on sys.path so the bare-name imports below resolve
# whether pytest is invoked from the backend dir or the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


@pytest.fixture
def isolated_tests_root(tmp_path, monkeypatch):
    """Redirect frameworks.QAFLOW_ROOT so on-disk artifacts land in tmp_path.

    This is the seam between the modules and the filesystem — every module
    that writes a file looks up QAFLOW_ROOT first, so pointing it at a
    tmp_path gives us a clean room for each test.
    """
    import frameworks
    monkeypatch.setattr(frameworks, "QAFLOW_ROOT", tmp_path)
    yield tmp_path


@pytest.fixture
def mock_mode(monkeypatch):
    """Activate QAFLOW_MOCK_MODE for the duration of the test."""
    monkeypatch.setenv("QAFLOW_MOCK_MODE", "1")
    yield


# ---------------------------------------------------------------------------
# APP_INDEX schema round-trip
# ---------------------------------------------------------------------------

def test_app_index_empty_skeleton_is_valid(isolated_tests_root):
    import app_index
    idx = app_index.empty("demo", {"mode": "product", "url": "http://x"})
    # No pages yet → validate() must complain because the gate rule says
    # "at least one critical page".  We're testing the lower-level call here.
    assert idx["schema_version"] == "1.0"
    assert idx["pages"] == []
    assert idx["auth_flow"]["type"] == "none"
    assert idx["next_step_recommendation"] == "smoke"


def test_app_index_save_load_round_trip(isolated_tests_root):
    import app_index
    idx = app_index.empty("demo", {"mode": "product", "url": "http://x"})
    idx["pages"] = [{
        "id": "login_page", "path": "/login", "purpose": "login",
        "importance": "critical", "requires_auth": False,
        "elements": {}, "apis_called": [],
    }]
    saved_path = app_index.save("demo", idx)
    assert saved_path.exists()
    reloaded = app_index.load("demo")
    assert reloaded is not None
    assert reloaded["pages"][0]["id"] == "login_page"
    app_index.validate(reloaded)


def test_app_index_validate_rejects_malformed(isolated_tests_root):
    import app_index
    bad = {"schema_version": "1.0", "application": {},
           "pages": "not-a-list", "auth_flow": {},
           "next_step_recommendation": "smoke"}
    with pytest.raises(app_index.IndexValidationError):
        app_index.validate(bad)


def test_app_index_slice_for_smoke_keeps_only_critical_high_pages(isolated_tests_root):
    import app_index
    idx = app_index.empty("demo", {"mode": "product"})
    idx["pages"] = [
        {"id": "a", "path": "/a", "importance": "critical"},
        {"id": "b", "path": "/b", "importance": "high"},
        {"id": "c", "path": "/c", "importance": "medium"},
        {"id": "d", "path": "/d", "importance": "low"},
    ]
    slice_ = app_index.slice_for_prompt(idx, "smoke_gen")
    assert {p["id"] for p in slice_["pages"]} == {"a", "b"}


def test_app_index_merge_extension_appends_pages_idempotently(isolated_tests_root):
    import app_index
    idx = app_index.empty("demo", {})
    idx["pages"] = [{"id": "login_page", "path": "/login", "importance": "critical"}]
    patch = {
        "pages_added": [
            {"id": "wallet_page", "path": "/wallet", "importance": "high"},
            {"id": "login_page",  "path": "/login",  "importance": "critical"},  # dup
        ],
        "apis_added": [],
        "history_appended": [{"step": "extend"}],
    }
    merged = app_index.merge_extension(idx, patch)
    ids = {p["id"] for p in merged["pages"]}
    assert ids == {"login_page", "wallet_page"}
    assert merged["generation_history"][-1]["step"] == "extend"


# ---------------------------------------------------------------------------
# Orchestrator state machine
# ---------------------------------------------------------------------------

def test_orchestrator_advances_through_happy_path(isolated_tests_root):
    import app_index, orchestrator
    slug = "happy-path"
    # Persist a valid APP_INDEX so discovery_gate passes.
    idx = app_index.empty(slug, {"mode": "product"})
    idx["pages"] = [{"id": "x", "path": "/x", "importance": "critical"}]
    app_index.save(slug, idx)

    orchestrator.mark_step_started(slug, "DISCOVERY")
    st = orchestrator.record_step_result(slug, "DISCOVERY", success=True)
    assert st.current_state == "SMOKE_GEN"

    orchestrator.mark_step_started(slug, "SMOKE_GEN")
    st = orchestrator.record_step_result(slug, "SMOKE_GEN", success=True, files_generated=3)
    assert st.current_state == "SMOKE_RUN"

    orchestrator.mark_step_started(slug, "SMOKE_RUN")
    st = orchestrator.record_step_result(slug, "SMOKE_RUN", success=True, pass_rate_pct=92)
    assert st.current_state == "E2E_GEN"   # SMOKE_GATE is implicit


def test_orchestrator_smoke_gate_blocks_below_80_pct(isolated_tests_root):
    import app_index, orchestrator
    slug = "low-smoke"
    idx = app_index.empty(slug, {})
    idx["pages"] = [{"id": "x", "path": "/x", "importance": "critical"}]
    app_index.save(slug, idx)

    orchestrator.mark_step_started(slug, "DISCOVERY")
    orchestrator.record_step_result(slug, "DISCOVERY", success=True)
    orchestrator.mark_step_started(slug, "SMOKE_GEN")
    orchestrator.record_step_result(slug, "SMOKE_GEN", success=True)

    orchestrator.mark_step_started(slug, "SMOKE_RUN")
    st = orchestrator.record_step_result(slug, "SMOKE_RUN", success=True, pass_rate_pct=60)
    assert st.current_state == "BLOCKED"
    assert "smoke_gate_failed" in (st.blocked_reason or "")


def test_orchestrator_e2e_gate_blocks_below_60_pct(isolated_tests_root):
    import app_index, orchestrator
    slug = "low-e2e"
    idx = app_index.empty(slug, {})
    idx["pages"] = [{"id": "x", "path": "/x", "importance": "critical"}]
    app_index.save(slug, idx)
    for step in ("DISCOVERY", "SMOKE_GEN"):
        orchestrator.mark_step_started(slug, step)
        orchestrator.record_step_result(slug, step, success=True)
    orchestrator.mark_step_started(slug, "SMOKE_RUN")
    orchestrator.record_step_result(slug, "SMOKE_RUN", success=True, pass_rate_pct=95)
    orchestrator.mark_step_started(slug, "E2E_GEN")
    orchestrator.record_step_result(slug, "E2E_GEN", success=True)
    orchestrator.mark_step_started(slug, "E2E_RUN")
    st = orchestrator.record_step_result(slug, "E2E_RUN", success=True, pass_rate_pct=40)
    assert st.current_state == "BLOCKED"


def test_orchestrator_retry_step_resets_predecessor(isolated_tests_root):
    import app_index, orchestrator
    slug = "retry-demo"
    idx = app_index.empty(slug, {})
    idx["pages"] = [{"id": "x", "path": "/x", "importance": "critical"}]
    app_index.save(slug, idx)
    orchestrator.mark_step_started(slug, "DISCOVERY")
    orchestrator.record_step_result(slug, "DISCOVERY", success=True)
    # current_state should now be SMOKE_GEN
    st = orchestrator.retry_step(slug, "SMOKE_GEN")
    assert st.current_state == "DISCOVERY"


def test_orchestrator_extend_returns_to_done(isolated_tests_root):
    import app_index, orchestrator
    slug = "extend-demo"
    idx = app_index.empty(slug, {})
    idx["pages"] = [{"id": "x", "path": "/x", "importance": "critical"}]
    app_index.save(slug, idx)
    # Mark as DONE via direct state file edit (we don't care HOW we got there).
    state = orchestrator.load_state(slug)
    state.current_state = "DONE"
    orchestrator.save_state(state)

    orchestrator.mark_step_started(slug, "EXTEND")
    st = orchestrator.record_step_result(slug, "EXTEND", success=True,
                                          files_generated=2)
    assert st.current_state == "DONE"


# ---------------------------------------------------------------------------
# Fakemail bridge
# ---------------------------------------------------------------------------

def test_fakemail_provision_users_is_deterministic():
    import fakemail
    a = fakemail.provision_test_users([{"role": "admin"}, {"role": "viewer"}])
    b = fakemail.provision_test_users([{"role": "admin"}, {"role": "viewer"}])
    # Stable UUID5 — same input → same emails on every call.
    assert [u["email"] for u in a] == [u["email"] for u in b]
    assert all("qaflow+" in u["email"] for u in a)


def test_fakemail_memory_bridge_round_trip():
    import fakemail
    bridge = fakemail.MemoryBridge()
    bridge.deliver(fakemail.TestMail(
        to="qaflow+admin@x", from_addr="noreply@app",
        subject="Verify your email",
        text_body="Click https://app/verify?token=abc123def456",
        html_body="",
    ))
    msg = bridge.peek("qaflow+admin@x", timeout_s=0.2)
    assert msg is not None
    assert msg.subject == "Verify your email"
    links = msg.extract_links()
    assert "https://app/verify?token=abc123def456" in links


def test_fakemail_memory_bridge_filters_by_subject():
    import fakemail
    bridge = fakemail.MemoryBridge()
    bridge.deliver(fakemail.TestMail(to="x@y", from_addr="", subject="Welcome",
                                      text_body="", html_body=""))
    bridge.deliver(fakemail.TestMail(to="x@y", from_addr="", subject="Reset password",
                                      text_body="", html_body=""))
    msg = bridge.peek("x@y", timeout_s=0.2, subject_contains="reset")
    assert msg is not None
    assert "Reset" in msg.subject


# ---------------------------------------------------------------------------
# Source extractors (PDF + Git modes)
# ---------------------------------------------------------------------------

def test_source_extractors_pdf_handles_missing_file():
    import source_extractors
    out = source_extractors.extract_pdf("/nonexistent.pdf")
    assert "not found" in out


def test_source_extractors_local_repo_handles_missing_dir():
    import source_extractors
    out = source_extractors.extract_local_repo_index("/nonexistent")
    assert "not found" in out


def test_source_extractors_local_repo_index_against_buggy_app():
    """Run the Git extractor against the project's own buggy-app to prove
    framework detection + file walking actually works end-to-end."""
    import source_extractors
    buggy_app = Path("/Users/deniz/qaflow-ai/buggy-app")
    if not buggy_app.exists():
        pytest.skip("buggy-app not present (this test is for local dev only)")
    out = source_extractors.extract_local_repo_index(buggy_app)
    assert "Git Source Index" in out
    assert "Detected framework" in out


# ---------------------------------------------------------------------------
# ai_engine mock-mode fixtures
# ---------------------------------------------------------------------------

def test_ai_engine_run_discovery_mock_returns_valid_index(mock_mode):
    import ai_engine, app_index
    idx = ai_engine.run_discovery(
        project_slug="mock-disc", source_mode="product",
        target_url="http://x", scan={}, crawl_pages_list=[],
    )
    # Mock fixture must satisfy the same validator the real path does.
    app_index.validate(idx)
    assert idx["next_step_recommendation"] == "smoke"
    assert any(p["importance"] == "critical" for p in idx["pages"])


def test_ai_engine_run_smoke_generation_mock_writes_files(mock_mode):
    import ai_engine
    data = ai_engine.run_smoke_generation(
        project_slug="mock-smoke", framework="cypress",
        language="javascript", framework_folder="cypress",
        base_url="http://x", app_index_slice={},
    )
    assert data["files"]
    assert all(isinstance(p, str) and isinstance(c, str) for p, c in data["files"].items())


def test_ai_engine_validation_mock_emits_verdict(mock_mode):
    import ai_engine
    data = ai_engine.run_validation(
        project_slug="mock-val", generated_at="2026-06-01",
        base_url="http://x", app_index_obj={},
        smoke_summary={}, e2e_summary={}, negative_summary={},
        api_discovery_summary={},
        bundle_inventory=[], risk_flags=[],
    )
    assert data["verdict"] in ("GREEN", "YELLOW", "RED")
    assert data["report_md"]


# ---------------------------------------------------------------------------
# bundle_runner
# ---------------------------------------------------------------------------

def test_bundle_runner_missing_bundle(isolated_tests_root):
    import bundle_runner
    res = bundle_runner.run(isolated_tests_root / "nope", "cypress-js", "smoke")
    assert res["missing_bundle"] is True
    assert res["total"] == 0


def test_bundle_runner_unsupported_framework(isolated_tests_root):
    import bundle_runner, frameworks
    # Carve a bundle directory so the missing_bundle short-circuit doesn't fire.
    bundle = isolated_tests_root / "tests" / "demo" / "demo-fake" / "smoke"
    bundle.mkdir(parents=True)
    (bundle / "x.txt").write_text("")
    res = bundle_runner.run(bundle.parent, "totally-unknown-fw", "smoke")
    assert res["unsupported_framework"] is True


# ---------------------------------------------------------------------------
# Full pipeline end-to-end (mock-mode + faked runs)
# ---------------------------------------------------------------------------

def test_full_pipeline_runs_end_to_end_in_mock_mode(isolated_tests_root, mock_mode):
    """The headline test — drives every state from INIT to DONE the way
    the CLI / endpoints would, just without the HTTP layer."""
    import ai_engine, app_index, orchestrator, test_writer, frameworks

    slug = "e2e-mock"
    framework_id = "cypress-js"

    # ---- Step 1: discovery (mock returns a fixture index) -----------------
    orchestrator.mark_step_started(slug, "DISCOVERY")
    index = ai_engine.run_discovery(
        project_slug=slug, source_mode="product",
        target_url="http://app/login", scan={}, crawl_pages_list=[],
    )
    app_index.save(slug, index)
    st = orchestrator.record_step_result(slug, "DISCOVERY", success=True)
    assert st.current_state == "SMOKE_GEN"

    # ---- Step 2: smoke gen ------------------------------------------------
    orchestrator.mark_step_started(slug, "SMOKE_GEN")
    smoke = ai_engine.run_smoke_generation(
        project_slug=slug, framework="cypress", language="javascript",
        framework_folder="cypress", base_url="http://app",
        app_index_slice=app_index.slice_for_prompt(index, "smoke_gen"),
    )
    saved = test_writer.save_bundle(
        files=smoke["files"], framework=framework_id, project=slug,
    )
    st = orchestrator.record_step_result(slug, "SMOKE_GEN",
        success=True, files_generated=len(smoke["files"]),
        artifacts={"bundle_root": saved["bundle_root"]})
    assert st.current_state == "SMOKE_RUN"
    assert Path(saved["bundle_root"]).exists()

    # ---- Step 3: simulate SMOKE_RUN passing the gate ----------------------
    orchestrator.mark_step_started(slug, "SMOKE_RUN")
    st = orchestrator.record_step_result(slug, "SMOKE_RUN",
        success=True, pass_rate_pct=92)
    assert st.current_state == "E2E_GEN"

    # ---- Step 4: e2e gen + run --------------------------------------------
    orchestrator.mark_step_started(slug, "E2E_GEN")
    e2e = ai_engine.run_e2e_generation(
        project_slug=slug, framework="cypress", language="javascript",
        framework_folder="cypress", base_url="http://app",
        app_index_json=app_index.slice_for_prompt(index, "e2e_gen"),
        smoke_pass_rate=92, smoke_duration_s=42, smoke_spec_count=5,
        existing_artifacts=[],
    )
    test_writer.save_bundle(files=e2e["files"], framework=framework_id, project=slug)
    orchestrator.record_step_result(slug, "E2E_GEN", success=True,
        files_generated=len(e2e["files"]))

    orchestrator.mark_step_started(slug, "E2E_RUN")
    st = orchestrator.record_step_result(slug, "E2E_RUN",
        success=True, pass_rate_pct=75)
    assert st.current_state == "NEGATIVE_GEN"

    # ---- Step 5: negative gen + run ---------------------------------------
    orchestrator.mark_step_started(slug, "NEGATIVE_GEN")
    neg = ai_engine.run_negative_generation(
        project_slug=slug, framework="cypress", language="javascript",
        framework_folder="cypress", base_url="http://app",
        app_index_json=app_index.slice_for_prompt(index, "negative_gen"),
        smoke_filenames=[], e2e_filenames=[], discovered_apis=[],
    )
    test_writer.save_bundle(files=neg["files"], framework=framework_id, project=slug)
    orchestrator.record_step_result(slug, "NEGATIVE_GEN", success=True,
        files_generated=len(neg["files"]))
    orchestrator.mark_step_started(slug, "NEGATIVE_RUN")
    orchestrator.record_step_result(slug, "NEGATIVE_RUN",
        success=True, pass_rate_pct=80)

    # ---- Step 6: api discovery (writes openapi.yaml) ----------------------
    orchestrator.mark_step_started(slug, "API_DISCOVERY")
    api_data = ai_engine.run_api_discovery(
        project_slug=slug, base_url="http://app",
        backend_stack="FastAPI", auth_type="form",
        discovered_apis=[], traffic_dump=[],
    )
    openapi_path = app_index.index_path(slug).parent / "openapi.yaml"
    openapi_path.write_text(api_data["openapi_yaml"])
    orchestrator.record_step_result(slug, "API_DISCOVERY", success=True)
    assert openapi_path.exists()
    assert "openapi: 3.1.0" in openapi_path.read_text()

    # ---- Step 7: validation (writes report.md) ----------------------------
    orchestrator.mark_step_started(slug, "VALIDATION")
    val = ai_engine.run_validation(
        project_slug=slug, generated_at="2026-06-01", base_url="http://app",
        app_index_obj=index, smoke_summary={}, e2e_summary={},
        negative_summary={}, api_discovery_summary={},
        bundle_inventory=[], risk_flags=[],
    )
    report_path = app_index.index_path(slug).parent / "report.md"
    report_path.write_text(val["report_md"])
    st = orchestrator.record_step_result(slug, "VALIDATION",
        success=True, summary=f"verdict={val['verdict']}")

    # ---- Final assertions on on-disk state --------------------------------
    assert st.current_state == "DONE"
    assert st.blocked_reason is None
    assert report_path.exists()
    assert "Test Suite Architecture" in report_path.read_text()

    # Orchestrator state should have every step recorded.
    snap = orchestrator.progress_snapshot(slug)
    completed_steps = {h["step"] for h in snap["history"] if h.get("success")}
    expected = {"DISCOVERY", "SMOKE_GEN", "SMOKE_RUN", "E2E_GEN", "E2E_RUN",
                "NEGATIVE_GEN", "NEGATIVE_RUN", "API_DISCOVERY", "VALIDATION"}
    assert expected.issubset(completed_steps)

    # Bundle directory should exist with all three subdirs populated.
    bundle = frameworks.QAFLOW_ROOT / "tests" / slug / f"{slug}-cypress"
    assert (bundle / "smoke").exists()
    assert (bundle / "e2e").exists()
    assert (bundle / "negative").exists()


# ---------------------------------------------------------------------------
# Discovery gate edge cases
# ---------------------------------------------------------------------------

def test_discovery_gate_blocks_when_no_critical_pages(isolated_tests_root):
    import app_index, orchestrator
    slug = "no-critical"
    idx = app_index.empty(slug, {})
    idx["pages"] = [{"id": "low_a", "path": "/a", "importance": "low"}]
    app_index.save(slug, idx)
    orchestrator.mark_step_started(slug, "DISCOVERY")
    st = orchestrator.record_step_result(slug, "DISCOVERY", success=True)
    assert st.current_state == "BLOCKED"
    assert st.blocked_reason == "no_critical_pages_detected"


def test_discovery_gate_blocks_when_recommendation_is_blocked(isolated_tests_root):
    import app_index, orchestrator
    slug = "blocked-disc"
    idx = app_index.empty(slug, {})
    idx["pages"] = [{"id": "x", "path": "/x", "importance": "critical"}]
    idx["next_step_recommendation"] = "blocked"
    idx["blocker_reason"] = "credentials_needed"
    app_index.save(slug, idx)
    orchestrator.mark_step_started(slug, "DISCOVERY")
    st = orchestrator.record_step_result(slug, "DISCOVERY", success=True)
    assert st.current_state == "BLOCKED"
    assert "credentials_needed" in (st.blocked_reason or "")
