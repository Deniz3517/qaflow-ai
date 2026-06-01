/**
 * PipelineBoard — multi-step AI test-generation pipeline driver.
 *
 * Drives the 6-step pipeline (discovery → smoke → e2e → negative →
 * api-discovery → validation) plus the out-of-band extend step.
 * Polls /state/{project} every 2s while running, and renders every
 * orchestrator history entry as a row in a live progress board.
 *
 * The board is intentionally self-contained: the parent only passes
 * a project slug + framework selection; everything else lives here.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, HttpError } from "../api";
import type {
  PipelineHistoryEntry,
  PipelineSnapshot,
  PipelineState,
} from "../types";
import MarkdownView from "./MarkdownView";

// Ordered display list — gate states are intentionally omitted from the
// progress strip; they are momentary forward-decisions, not work units.
const DISPLAY_STEPS: PipelineState[] = [
  "DISCOVERY",
  "SMOKE_GEN",
  "SMOKE_RUN",
  "E2E_GEN",
  "E2E_RUN",
  "NEGATIVE_GEN",
  "NEGATIVE_RUN",
  "API_DISCOVERY",
  "VALIDATION",
];

const STEP_LABELS: Record<PipelineState, string> = {
  INIT: "Init",
  DISCOVERY: "Discovery",
  SMOKE_GEN: "Smoke gen",
  SMOKE_RUN: "Smoke run",
  SMOKE_GATE: "Smoke gate",
  E2E_GEN: "E2E gen",
  E2E_RUN: "E2E run",
  E2E_GATE: "E2E gate",
  NEGATIVE_GEN: "Negative gen",
  NEGATIVE_RUN: "Negative run",
  API_DISCOVERY: "API discovery",
  VALIDATION: "Validation",
  DONE: "Done",
  BLOCKED: "Blocked",
  EXTEND: "Extend",
};

type Mode = "product" | "git" | "pdf";

interface Props {
  project: string;
  framework: string;          // backend framework-id (cypress-js, robot-py, ...)
  initialUrl?: string;
  authJson?: string;          // optional JSON-encoded auth config
  testUsersJson?: string;     // optional JSON-encoded test_users list
}

interface RunStatus {
  step: PipelineState;
  active: boolean;
  error: string | null;
}

export default function PipelineBoard({
  project, framework, initialUrl, authJson, testUsersJson,
}: Props) {
  const [mode, setMode] = useState<Mode>("product");
  const [url, setUrl] = useState(initialUrl || "");
  const [repoUrl, setRepoUrl] = useState("");
  const [pdfPath, setPdfPath] = useState("");
  const [gaps, setGaps] = useState("");

  // Server-side orchestration toggle — when true, "Run Full Pipeline"
  // fires a single /orchestrate call and the server drives the whole
  // chain in the background. When false, the browser chains the steps.
  const [serverSide, setServerSide] = useState(true);

  // Auto-provisioned test users (filled by the "Auto-provision" button).
  type ProvisionedUser = { role: string; email: string; password: string; inbox_url: string };
  const [testUsers, setTestUsers] = useState<ProvisionedUser[]>([]);
  const [fakemailProvider, setFakemailProvider] = useState<string | null>(null);

  const [snapshot, setSnapshot] = useState<PipelineSnapshot | null>(null);
  const [busy, setBusy] = useState<RunStatus>({ step: "INIT", active: false, error: null });
  const [poller, setPoller] = useState<number | null>(null);

  const [report, setReport] = useState<{ md: string; verdict: string; reason: string } | null>(null);
  const [openapiYaml, setOpenapiYaml] = useState<string | null>(null);

  // Inspection panels — lazily loaded when user clicks expand.
  const [appIndex, setAppIndex] = useState<Record<string, unknown> | null>(null);
  const [appIndexExpanded, setAppIndexExpanded] = useState(false);
  type FileEntry = { path: string; size_bytes: number; contents?: string; truncated?: boolean; binary?: boolean };
  const [bundleFiles, setBundleFiles] = useState<FileEntry[]>([]);
  const [bundleExpanded, setBundleExpanded] = useState(false);
  const [activeFile, setActiveFile] = useState<string | null>(null);

  // Audit log
  type AuditItem = {
    id: number; ts: number; event_type: string;
    engine?: string | null; cache_hit: number; success: number;
    duration_ms?: number | null; summary?: string | null; error?: string | null;
    framework_id?: string | null;
  };
  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [auditExpanded, setAuditExpanded] = useState(false);
  const [auditStats, setAuditStats] = useState<Record<string, unknown> | null>(null);

  // One-off install action for the bundle browser.
  const [installRunning, setInstallRunning] = useState(false);
  const [installLog, setInstallLog] = useState<string | null>(null);

  const expanded = useRef<HTMLDivElement | null>(null);

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  const refreshState = useCallback(async () => {
    if (!project) return;
    try {
      const r = await api.pipelineState(project);
      setSnapshot(r.orchestrator);
    } catch (e) {
      if (e instanceof HttpError && e.status !== 404) {
        // 404 just means no state yet for this project; ignore.
        // eslint-disable-next-line no-console
        console.warn("pipeline state poll failed:", e.message);
      }
    }
  }, [project]);

  // Initial load + cleanup of poller.
  useEffect(() => {
    refreshState();
    return () => {
      if (poller != null) window.clearInterval(poller);
    };
    // We intentionally only depend on project here — poller cleanup is
    // owned by the start/stop helpers below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);

  const startPolling = useCallback(() => {
    if (poller != null) return;
    const id = window.setInterval(refreshState, 2000);
    setPoller(id);
  }, [poller, refreshState]);

  const stopPolling = useCallback(() => {
    if (poller != null) {
      window.clearInterval(poller);
      setPoller(null);
    }
  }, [poller]);

  // Auto-stop polling once the orchestrator is terminal.
  useEffect(() => {
    if (snapshot?.is_terminal) stopPolling();
  }, [snapshot?.is_terminal, stopPolling]);

  // -------------------------------------------------------------------------
  // Step runners — each fires the endpoint, then refreshes state.
  // The end-to-end "Run Full Pipeline" chains them with gate checks.
  // -------------------------------------------------------------------------

  function parseJsonOrNull<T>(s: string | undefined): T | null {
    if (!s || !s.trim()) return null;
    try { return JSON.parse(s) as T; } catch { return null; }
  }

  const runDiscovery = useCallback(async () => {
    setBusy({ step: "DISCOVERY", active: true, error: null });
    startPolling();
    try {
      const auth = parseJsonOrNull<Record<string, unknown>>(authJson) || undefined;
      const testUsers = parseJsonOrNull<Array<Record<string, unknown>>>(testUsersJson) || undefined;
      await api.pipelineDiscover({
        project, mode,
        url: mode === "product" ? url : undefined,
        repo_url: mode === "git" ? repoUrl : undefined,
        pdf_path: mode === "pdf" ? pdfPath : undefined,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        auth: auth as any,
        test_users: testUsers,
      });
      await refreshState();
    } catch (e) {
      setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(b => ({ ...b, active: false }));
    }
  }, [project, mode, url, repoUrl, pdfPath, authJson, testUsersJson, refreshState, startPolling]);

  const runGen = useCallback(async (step: "SMOKE_GEN" | "E2E_GEN" | "NEGATIVE_GEN") => {
    setBusy({ step, active: true, error: null });
    startPolling();
    try {
      const payload = { project, framework, force: true as const };
      if (step === "SMOKE_GEN")       await api.pipelineSmoke({ project, framework });
      else if (step === "E2E_GEN")    await api.pipelineE2E(payload);
      else                            await api.pipelineNegative(payload);
      await refreshState();
    } catch (e) {
      setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(b => ({ ...b, active: false }));
    }
  }, [project, framework, refreshState, startPolling]);

  const runExec = useCallback(async (step: "SMOKE_RUN" | "E2E_RUN" | "NEGATIVE_RUN") => {
    setBusy({ step, active: true, error: null });
    startPolling();
    try {
      const payload = { project, framework };
      if (step === "SMOKE_RUN")       await api.pipelineSmokeRun(payload);
      else if (step === "E2E_RUN")    await api.pipelineE2ERun(payload);
      else                            await api.pipelineNegativeRun(payload);
      await refreshState();
    } catch (e) {
      setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(b => ({ ...b, active: false }));
    }
  }, [project, framework, refreshState, startPolling]);

  const runApiDiscovery = useCallback(async () => {
    setBusy({ step: "API_DISCOVERY", active: true, error: null });
    try {
      const r = await api.pipelineApiDiscovery({ project });
      setOpenapiYaml(r.yaml_preview);
      await refreshState();
    } catch (e) {
      setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(b => ({ ...b, active: false }));
    }
  }, [project, refreshState]);

  const runValidate = useCallback(async () => {
    setBusy({ step: "VALIDATION", active: true, error: null });
    try {
      const r = await api.pipelineValidate({ project, framework });
      setReport({ md: r.report_md, verdict: r.verdict, reason: r.verdict_reason });
      await refreshState();
    } catch (e) {
      setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(b => ({ ...b, active: false }));
    }
  }, [project, framework, refreshState]);

  const runExtend = useCallback(async () => {
    const list = gaps.split("\n").map(s => s.trim()).filter(Boolean);
    if (!list.length) {
      setBusy(b => ({ ...b, error: "Add at least one gap (one per line)" }));
      return;
    }
    setBusy({ step: "EXTEND", active: true, error: null });
    try {
      await api.pipelineExtend({ project, framework, gaps: list });
      await refreshState();
      setGaps("");
    } catch (e) {
      setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(b => ({ ...b, active: false }));
    }
  }, [project, framework, gaps, refreshState]);

  const runFullPipeline = useCallback(async () => {
    if (serverSide) {
      // Fire and forget — the server drives the chain in the background
      // and we poll for state. Closing the browser does not interrupt it.
      setBusy({ step: "DISCOVERY", active: true, error: null });
      startPolling();
      try {
        const auth = parseJsonOrNull<Record<string, unknown>>(authJson) || undefined;
        await api.pipelineOrchestrate({
          project, framework, mode,
          url: mode === "product" ? url : undefined,
          repo_url: mode === "git" ? repoUrl : undefined,
          pdf_path: mode === "pdf" ? pdfPath : undefined,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          auth: auth as any,
          test_users: testUsers.length ? testUsers : undefined,
        });
        await refreshState();
      } catch (e) {
        setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
      } finally {
        // Polling continues until orchestrator hits a terminal state.
        setBusy(b => ({ ...b, active: false }));
      }
      return;
    }

    // Browser-driven chain — each call honors orchestrator gates server-side
    // so a BLOCKED status will stop progression naturally. The board reflects
    // this by showing the last step as failed.
    setBusy({ step: "DISCOVERY", active: true, error: null });
    startPolling();
    try {
      await runDiscovery();
      const afterDisc = await api.pipelineState(project);
      if (afterDisc.orchestrator.current_state === "BLOCKED") return;

      await runGen("SMOKE_GEN");
      await runExec("SMOKE_RUN");
      const afterSmoke = await api.pipelineState(project);
      if (afterSmoke.orchestrator.current_state === "BLOCKED") return;

      await runGen("E2E_GEN");
      await runExec("E2E_RUN");
      const afterE2E = await api.pipelineState(project);
      if (afterE2E.orchestrator.current_state === "BLOCKED") return;

      await runGen("NEGATIVE_GEN");
      await runExec("NEGATIVE_RUN");

      await runApiDiscovery();
      await runValidate();
      await refreshState();
    } finally {
      setBusy(b => ({ ...b, active: false }));
    }
  }, [serverSide, project, framework, mode, url, repoUrl, pdfPath, authJson, testUsers,
      runDiscovery, runGen, runExec, runApiDiscovery, runValidate, refreshState, startPolling]);

  const provisionTestUsers = useCallback(async () => {
    try {
      const r = await api.fakemailProvisionUsers(["admin", "viewer"]);
      setTestUsers(r.test_users);
      setFakemailProvider(r.provider);
    } catch (e) {
      setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
    }
  }, []);

  const loadAppIndex = useCallback(async () => {
    setAppIndexExpanded(true);
    try {
      const r = await api.pipelineProjectAppIndex(project);
      setAppIndex(r.app_index);
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) {
        setAppIndex(null);
      } else {
        setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
      }
    }
  }, [project]);

  const loadBundleFiles = useCallback(async () => {
    setBundleExpanded(true);
    try {
      const r = await api.pipelineProjectFiles(project, framework);
      setBundleFiles(r.files);
      setActiveFile(r.files[0]?.path || null);
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) {
        setBundleFiles([]);
      } else {
        setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
      }
    }
  }, [project, framework]);

  const loadAudit = useCallback(async () => {
    setAuditExpanded(true);
    try {
      const r = await api.aiAuditLog(80);
      setAudit(r.items);
      setAuditStats(r.stats);
    } catch (e) {
      setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
    }
  }, []);

  const installBundleDeps = useCallback(async () => {
    setInstallRunning(true);
    setInstallLog(null);
    try {
      const r = await api.pipelineInstallBundleDeps({ project, framework });
      setInstallLog((r.ok ? "[ok] " : "[fail] ") + (r.log || ""));
    } catch (e) {
      setInstallLog("[error] " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setInstallRunning(false);
    }
  }, [project, framework]);

  // Refresh inspection panels when the orchestrator advances to a state
  // where new artifacts are expected.
  useEffect(() => {
    if (appIndexExpanded && snapshot?.current_state &&
        ["SMOKE_GEN", "E2E_GEN", "NEGATIVE_GEN", "API_DISCOVERY", "VALIDATION", "DONE"].includes(snapshot.current_state)) {
      loadAppIndex();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot?.current_state]);

  const retryStep = useCallback(async (step: PipelineState) => {
    try {
      await api.pipelineRetryStep(project, step);
      await refreshState();
    } catch (e) {
      setBusy(b => ({ ...b, error: e instanceof Error ? e.message : String(e) }));
    }
  }, [project, refreshState]);

  // -------------------------------------------------------------------------
  // Derived view state
  // -------------------------------------------------------------------------

  const stepStatuses = useMemo(() => {
    // Build a per-step status from the orchestrator history (most recent wins).
    const map = new Map<PipelineState, PipelineHistoryEntry>();
    for (const h of snapshot?.history || []) {
      map.set(h.step, h);
    }
    return DISPLAY_STEPS.map(step => ({
      step,
      entry: map.get(step) || null,
    }));
  }, [snapshot]);

  const currentBadge = (
    snapshot?.current_state || "INIT"
  ) as PipelineState;

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div className="space-y-4">
      <header className="flex items-baseline justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">
            AI Pipeline — <span className="text-emerald-300">{project || "(no project)"}</span>
          </h2>
          <p className="text-sm text-slate-400">
            6-step pipeline drives the AI through senior-level prompts with real gates between each phase.
          </p>
        </div>
        <StateBadge state={currentBadge} blockedReason={snapshot?.blocked_reason} />
      </header>

      {/* ============================== Mode + input ============================== */}
      <section className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
        <div className="mb-3 flex gap-2 text-sm">
          {(["product", "git", "pdf"] as Mode[]).map(m => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={
                "rounded-full border px-3 py-1 " +
                (mode === m
                  ? "border-emerald-500/60 bg-emerald-500/10 text-emerald-300"
                  : "border-slate-700 bg-slate-900/50 text-slate-400 hover:text-slate-200")
              }
            >
              {m === "product" ? "Product URL" : m === "git" ? "Git Repo" : "PDF Spec"}
            </button>
          ))}
        </div>
        {mode === "product" && (
          <input
            type="text"
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="https://app.example.com/login"
            className="w-full rounded-md border border-slate-700 bg-slate-950/40 px-3 py-2 text-sm text-slate-100 placeholder-slate-500"
          />
        )}
        {mode === "git" && (
          <input
            type="text"
            value={repoUrl}
            onChange={e => setRepoUrl(e.target.value)}
            placeholder="https://github.com/org/repo.git"
            className="w-full rounded-md border border-slate-700 bg-slate-950/40 px-3 py-2 text-sm text-slate-100 placeholder-slate-500"
          />
        )}
        {mode === "pdf" && (
          <input
            type="text"
            value={pdfPath}
            onChange={e => setPdfPath(e.target.value)}
            placeholder="/path/to/spec.pdf"
            className="w-full rounded-md border border-slate-700 bg-slate-950/40 px-3 py-2 text-sm text-slate-100 placeholder-slate-500"
          />
        )}
      </section>

      {/* ============================== Test users (fakemail bridge) ============== */}
      <section className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            Test users
          </h3>
          <div className="flex items-center gap-2">
            {fakemailProvider && (
              <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                provider: {fakemailProvider}
              </span>
            )}
            <button
              type="button"
              onClick={provisionTestUsers}
              className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800/50"
            >
              Auto-provision admin + viewer
            </button>
          </div>
        </div>
        {testUsers.length === 0 ? (
          <p className="text-xs text-slate-500">
            No test users yet — click <em>Auto-provision</em> to generate stable test
            credentials. They'll be injected into discovery so the AI can use them.
          </p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr>
                <th className="text-left text-slate-400">Role</th>
                <th className="text-left text-slate-400">Email</th>
                <th className="text-left text-slate-400">Password</th>
              </tr>
            </thead>
            <tbody>
              {testUsers.map(u => (
                <tr key={u.email} className="text-slate-300">
                  <td className="py-0.5 pr-3">{u.role}</td>
                  <td className="py-0.5 pr-3 font-mono">{u.email}</td>
                  <td className="py-0.5 pr-3 font-mono">{u.password}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* ============================== Step buttons ============================== */}
      <section className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={runFullPipeline}
            disabled={busy.active}
            className="rounded-md bg-emerald-500 px-3 py-1.5 text-sm font-medium text-emerald-950 hover:bg-emerald-400 disabled:opacity-50"
          >
            ▶ Run Full Pipeline
          </button>
          <label className="ml-1 flex items-center gap-1 text-xs text-slate-400">
            <input
              type="checkbox"
              checked={serverSide}
              onChange={e => setServerSide(e.target.checked)}
              className="accent-emerald-500"
            />
            server-side (survives tab close)
          </label>
          <span className="mx-2 text-slate-600">|</span>
          <StepButton label="1. Discovery"     active={busy.active && busy.step === "DISCOVERY"}    onClick={runDiscovery} />
          <StepButton label="2. Smoke gen"     active={busy.active && busy.step === "SMOKE_GEN"}    onClick={() => runGen("SMOKE_GEN")} />
          <StepButton label="3. Smoke run"     active={busy.active && busy.step === "SMOKE_RUN"}    onClick={() => runExec("SMOKE_RUN")} />
          <StepButton label="4. E2E gen"       active={busy.active && busy.step === "E2E_GEN"}      onClick={() => runGen("E2E_GEN")} />
          <StepButton label="5. E2E run"       active={busy.active && busy.step === "E2E_RUN"}      onClick={() => runExec("E2E_RUN")} />
          <StepButton label="6. Negative gen"  active={busy.active && busy.step === "NEGATIVE_GEN"} onClick={() => runGen("NEGATIVE_GEN")} />
          <StepButton label="7. Negative run"  active={busy.active && busy.step === "NEGATIVE_RUN"} onClick={() => runExec("NEGATIVE_RUN")} />
          <StepButton label="8. API discovery" active={busy.active && busy.step === "API_DISCOVERY"} onClick={runApiDiscovery} />
          <StepButton label="9. Validate"      active={busy.active && busy.step === "VALIDATION"}   onClick={runValidate} />
        </div>
        {busy.error && (
          <div className="mt-3 rounded border border-rose-700/50 bg-rose-950/30 px-3 py-2 text-sm text-rose-300">
            {busy.error}
          </div>
        )}
      </section>

      {/* ============================== Progress board ============================== */}
      <section className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
          Live progress
        </h3>
        <div className="space-y-1">
          {stepStatuses.map(({ step, entry }) => (
            <ProgressRow
              key={step}
              step={step}
              entry={entry}
              isCurrent={snapshot?.current_state === step}
              onRetry={() => retryStep(step)}
            />
          ))}
        </div>
        {snapshot && (
          <div className="mt-3 text-xs text-slate-500">
            Smoke gate ≥ {snapshot.thresholds.smoke_gate_pct}%  ·  E2E gate ≥ {snapshot.thresholds.e2e_gate_pct}%
          </div>
        )}
      </section>

      {/* ============================== Outputs ============================== */}
      {report && (
        <section className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Validation report
            </h3>
            <span
              className={
                "rounded-full px-2 py-0.5 text-xs font-medium " +
                (report.verdict === "GREEN"
                  ? "bg-emerald-500/20 text-emerald-300"
                  : report.verdict === "YELLOW"
                    ? "bg-amber-500/20 text-amber-300"
                    : "bg-rose-500/20 text-rose-300")
              }
            >
              {report.verdict} — {report.reason}
            </span>
          </div>
          <div
            ref={expanded}
            className="max-h-[32rem] overflow-auto rounded bg-slate-950/30 p-4"
          >
            <MarkdownView source={report.md} />
          </div>
        </section>
      )}

      {openapiYaml && (
        <section className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
          <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
            openapi.yaml (preview)
          </h3>
          <pre className="max-h-72 overflow-auto whitespace-pre rounded bg-slate-950/50 p-3 text-xs text-emerald-200">
            {openapiYaml}
          </pre>
        </section>
      )}

      {/* ============================== APP_INDEX viewer ============================== */}
      <section className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
        <button
          type="button"
          onClick={() => (appIndexExpanded ? setAppIndexExpanded(false) : loadAppIndex())}
          className="flex w-full items-center justify-between text-left"
        >
          <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            APP_INDEX (what the AI discovered)
          </h3>
          <span className="text-xs text-slate-500">{appIndexExpanded ? "▾" : "▸"}</span>
        </button>
        {appIndexExpanded && (
          appIndex ? <AppIndexView appIndex={appIndex} />
                   : <div className="mt-2 text-xs text-slate-500">No APP_INDEX yet — run discovery first.</div>
        )}
      </section>

      {/* ============================== Bundle file browser ============================== */}
      <section className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={() => (bundleExpanded ? setBundleExpanded(false) : loadBundleFiles())}
            className="flex flex-1 items-center justify-between text-left"
          >
            <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Generated bundle ({framework})
            </h3>
            <span className="text-xs text-slate-500">{bundleExpanded ? "▾" : "▸"}</span>
          </button>
          {bundleExpanded && bundleFiles.length > 0 && (
            <div className="ml-3 flex gap-2">
              <button
                type="button"
                onClick={installBundleDeps}
                disabled={installRunning}
                className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800/50 disabled:opacity-50"
              >
                {installRunning ? "Installing…" : "Install deps"}
              </button>
              <a
                href={api.pipelineProjectDownloadUrl(project, framework)}
                className="rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs text-emerald-300 hover:bg-emerald-500/20"
              >
                ↓ Download ZIP
              </a>
            </div>
          )}
        </div>
        {installLog && (
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-950/60 p-2 text-[10px] text-slate-300">
            {installLog}
          </pre>
        )}
        {bundleExpanded && (
          bundleFiles.length === 0
            ? <div className="mt-2 text-xs text-slate-500">No bundle yet — run smoke / e2e / negative first.</div>
            : <BundleFileBrowser
                files={bundleFiles}
                activeFile={activeFile}
                onPick={setActiveFile}
              />
        )}
      </section>

      {/* ============================== AI audit log ============================== */}
      <section className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
        <button
          type="button"
          onClick={() => (auditExpanded ? setAuditExpanded(false) : loadAudit())}
          className="flex w-full items-center justify-between text-left"
        >
          <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            AI call audit log
          </h3>
          <span className="text-xs text-slate-500">{auditExpanded ? "▾" : "▸"}</span>
        </button>
        {auditExpanded && (
          <div className="mt-3 space-y-2">
            {auditStats && (
              <div className="rounded bg-slate-950/40 px-3 py-2 text-xs text-slate-300">
                <span>calls last {Math.round(((auditStats.window_s as number) || 0) / 3600)}h: </span>
                <span className="font-semibold text-slate-100">{String(auditStats.calls ?? 0)}</span>
                <span className="ml-3">successes: <span className="text-emerald-300">{String(auditStats.successes ?? 0)}</span></span>
                <span className="ml-3">cache hits: <span className="text-violet-300">{String(auditStats.cache_hits ?? 0)}</span></span>
                <span className="ml-3">avg duration: <span className="text-amber-300">{Math.round((auditStats.avg_duration_ms as number) || 0)}ms</span></span>
              </div>
            )}
            {audit.length === 0 ? (
              <div className="text-xs text-slate-500">No AI calls audited yet.</div>
            ) : (
              <div className="max-h-80 overflow-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-slate-900/80 backdrop-blur">
                    <tr className="text-left text-slate-400">
                      <th className="py-1 pr-2">when</th>
                      <th className="pr-2">event</th>
                      <th className="pr-2">engine</th>
                      <th className="pr-2">ok?</th>
                      <th className="pr-2">ms</th>
                      <th>summary</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audit.map(a => (
                      <tr key={a.id} className="text-slate-300 hover:bg-slate-800/30">
                        <td className="py-0.5 pr-2 font-mono text-slate-500">
                          {new Date(a.ts * 1000).toLocaleTimeString()}
                        </td>
                        <td className="pr-2 font-mono">{a.event_type}</td>
                        <td className="pr-2">
                          {a.engine === "cache-hit"
                            ? <span className="text-violet-300">cache</span>
                            : <span className="text-slate-400">{a.engine || ""}</span>}
                        </td>
                        <td className="pr-2">
                          {a.success ? <span className="text-emerald-300">✓</span> : <span className="text-rose-300">✕</span>}
                        </td>
                        <td className="pr-2 text-slate-400">{a.duration_ms ?? "—"}</td>
                        <td className="truncate text-slate-400" title={a.summary || a.error || ""}>
                          {a.error
                            ? <span className="text-rose-300">{a.error.slice(0, 60)}</span>
                            : (a.summary || "")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="text-right">
              <button
                type="button"
                onClick={loadAudit}
                className="text-xs text-slate-500 hover:text-slate-200"
              >
                ↻ refresh
              </button>
            </div>
          </div>
        )}
      </section>

      {/* ============================== Extend panel ============================== */}
      <section className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-4">
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
          Extend coverage (incremental)
        </h3>
        <p className="mb-2 text-xs text-slate-500">
          One gap per line. Examples: <em>"/wallet not covered"</em>, <em>"password reset missing"</em>.
        </p>
        <textarea
          value={gaps}
          onChange={e => setGaps(e.target.value)}
          rows={3}
          className="w-full rounded-md border border-slate-700 bg-slate-950/40 px-3 py-2 text-sm text-slate-100 placeholder-slate-500"
          placeholder="/wallet not covered&#10;password reset missing"
        />
        <button
          type="button"
          onClick={runExtend}
          disabled={busy.active || !gaps.trim()}
          className="mt-2 rounded-md bg-violet-500 px-3 py-1.5 text-sm font-medium text-violet-950 hover:bg-violet-400 disabled:opacity-50"
        >
          ✦ Apply Extension
        </button>
      </section>
    </div>
  );
}

// -----------------------------------------------------------------------------
// Sub-components
// -----------------------------------------------------------------------------

function StepButton({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={active}
      className={
        "rounded border px-2 py-1 text-xs " +
        (active
          ? "border-amber-500/60 bg-amber-500/10 text-amber-300"
          : "border-slate-700 bg-slate-900/50 text-slate-300 hover:bg-slate-800/50")
      }
    >
      {active ? "⏳ " : ""}{label}
    </button>
  );
}

function StateBadge({ state, blockedReason }: { state: PipelineState; blockedReason?: string | null }) {
  const cls =
    state === "DONE"
      ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
      : state === "BLOCKED"
        ? "border-rose-500/40 bg-rose-500/15 text-rose-300"
        : "border-slate-600/40 bg-slate-700/30 text-slate-300";
  return (
    <div className={`rounded border px-2 py-1 text-xs font-medium ${cls}`} title={blockedReason || undefined}>
      {STEP_LABELS[state]}
      {blockedReason ? ` — ${blockedReason}` : null}
    </div>
  );
}

function ProgressRow({
  step, entry, isCurrent, onRetry,
}: {
  step: PipelineState;
  entry: PipelineHistoryEntry | null;
  isCurrent: boolean;
  onRetry: () => void;
}) {
  const icon =
    entry == null              ? "○"
      : entry.success === true ? "✓"
        : entry.success === false ? "✕"
          : "⏳";
  const cls =
    entry == null              ? "text-slate-500"
      : entry.success === true ? "text-emerald-400"
        : entry.success === false ? "text-rose-400"
          : "text-amber-400";
  return (
    <div
      className={
        "flex items-center justify-between rounded px-2 py-1.5 text-sm " +
        (isCurrent ? "bg-slate-800/40" : "")
      }
    >
      <div className="flex items-center gap-3">
        <span className={`w-4 text-center font-mono text-base ${cls}`}>{icon}</span>
        <span className="text-slate-200">{STEP_LABELS[step]}</span>
        {entry?.pass_rate_pct != null && (
          <span className="text-xs text-slate-400">{entry.pass_rate_pct}%</span>
        )}
        {entry?.files_generated != null && (
          <span className="text-xs text-slate-400">{entry.files_generated} files</span>
        )}
        {entry?.summary && (
          <span className="ml-2 truncate text-xs text-slate-500" title={entry.summary}>
            {entry.summary}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        {entry?.error && (
          <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-xs text-rose-300" title={entry.error}>
            error
          </span>
        )}
        {entry && entry.success === false && (
          <button
            type="button"
            onClick={onRetry}
            className="rounded border border-slate-700 px-1.5 py-0.5 text-xs text-slate-400 hover:text-slate-200"
          >
            retry
          </button>
        )}
      </div>
    </div>
  );
}


// -----------------------------------------------------------------------------
// APP_INDEX viewer — renders the key sections as structured tables.
// -----------------------------------------------------------------------------

function AppIndexView({ appIndex }: { appIndex: Record<string, unknown> }) {
  const app = (appIndex.application || {}) as Record<string, unknown>;
  const pages = (appIndex.pages as Array<Record<string, unknown>>) || [];
  const auth = (appIndex.auth_flow || {}) as Record<string, unknown>;
  const apis = (appIndex.discovered_apis as Array<Record<string, unknown>>) || [];
  const riskFlags = (appIndex.risk_flags as string[]) || [];
  const testUsers = (appIndex.test_users as Array<Record<string, unknown>>) || [];

  return (
    <div className="mt-3 space-y-4 text-xs">
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="rounded bg-slate-950/40 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wider text-slate-500">Application</div>
          <div className="text-slate-200">{String(app.name || "—")}</div>
          <div className="mt-1 text-slate-400">type: <span className="text-slate-200">{String(app.type || "?")}</span></div>
          <div className="text-slate-400">
            stack:&nbsp;
            <span className="text-slate-200">
              {String(((app.detected_stack as Record<string, unknown>) || {}).frontend || "?")}
            </span>
            {" / "}
            <span className="text-slate-200">
              {String(((app.detected_stack as Record<string, unknown>) || {}).backend || "?")}
            </span>
          </div>
          <div className="text-slate-400">base_url: <span className="font-mono text-slate-200">{String(app.base_url || "—")}</span></div>
        </div>
        <div className="rounded bg-slate-950/40 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wider text-slate-500">Auth flow</div>
          <div className="text-slate-200">{String(auth.type || "—")}</div>
          {auth.login_url && (
            <div className="text-slate-400">login_url: <span className="font-mono text-slate-200">{String(auth.login_url)}</span></div>
          )}
          {auth.blocker && (
            <div className="mt-1 text-rose-300">blocker: {String(auth.blocker)}</div>
          )}
        </div>
      </div>

      {riskFlags.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Risk flags</div>
          <div className="flex flex-wrap gap-1">
            {riskFlags.map(f => (
              <span key={f} className="rounded bg-amber-500/15 px-2 py-0.5 text-amber-300">{f}</span>
            ))}
          </div>
        </div>
      )}

      {pages.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Pages ({pages.length})</div>
          <table className="w-full">
            <thead>
              <tr className="text-left text-slate-400">
                <th className="py-1">id</th>
                <th>path</th>
                <th>imp.</th>
                <th>auth</th>
                <th>tests</th>
              </tr>
            </thead>
            <tbody>
              {pages.map((p, i) => (
                <tr key={i} className="text-slate-300">
                  <td className="py-1 pr-2 font-mono">{String(p.id || "")}</td>
                  <td className="pr-2 font-mono">{String(p.path || "")}</td>
                  <td className="pr-2">
                    <span className={
                      p.importance === "critical" ? "text-rose-300"
                        : p.importance === "high" ? "text-amber-300"
                          : "text-slate-400"
                    }>
                      {String(p.importance || "?")}
                    </span>
                  </td>
                  <td className="pr-2">{p.requires_auth ? "✓" : ""}</td>
                  <td className="pr-2 text-slate-500">
                    {((p.test_recommendations as string[]) || []).join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {apis.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Discovered APIs ({apis.length})</div>
          <table className="w-full">
            <thead>
              <tr className="text-left text-slate-400">
                <th className="py-1">method</th>
                <th>path</th>
                <th>auth?</th>
                <th>statuses</th>
              </tr>
            </thead>
            <tbody>
              {apis.map((a, i) => (
                <tr key={i} className="text-slate-300">
                  <td className="py-1 pr-2 font-mono text-emerald-300">{String(a.method || "")}</td>
                  <td className="pr-2 font-mono">{String(a.path || "")}</td>
                  <td className="pr-2">{a.auth_required ? "✓" : ""}</td>
                  <td className="pr-2 text-slate-500">
                    {((a.observed_status_codes as number[]) || []).join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {testUsers.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Test users ({testUsers.length})</div>
          <ul className="space-y-0.5">
            {testUsers.map((u, i) => (
              <li key={i} className="text-slate-400">
                <span className="text-slate-200">{String(u.role || "?")}</span>:&nbsp;
                <span className="font-mono">{String(u.email || "")}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}


// -----------------------------------------------------------------------------
// Bundle file browser — left tree, right contents.
// -----------------------------------------------------------------------------

function BundleFileBrowser({
  files, activeFile, onPick,
}: {
  files: Array<{ path: string; size_bytes: number; contents?: string; truncated?: boolean; binary?: boolean }>;
  activeFile: string | null;
  onPick: (p: string) => void;
}) {
  const active = files.find(f => f.path === activeFile) || files[0];
  return (
    <div className="mt-3 grid gap-2 lg:grid-cols-[260px_1fr]">
      <div className="max-h-[28rem] overflow-auto rounded bg-slate-950/40 p-2 text-xs">
        {files.map(f => (
          <button
            key={f.path}
            type="button"
            onClick={() => onPick(f.path)}
            className={
              "block w-full truncate rounded px-2 py-1 text-left font-mono " +
              (activeFile === f.path
                ? "bg-slate-800 text-emerald-200"
                : "text-slate-400 hover:bg-slate-800/50 hover:text-slate-200")
            }
            title={`${f.path} — ${f.size_bytes} bytes`}
          >
            {f.path}
          </button>
        ))}
      </div>
      <div className="max-h-[28rem] overflow-auto rounded bg-slate-950/60 p-3 text-xs">
        {!active ? (
          <span className="text-slate-500">Pick a file from the left.</span>
        ) : active.binary ? (
          <span className="text-slate-500">(binary file — {active.size_bytes} bytes)</span>
        ) : (
          <>
            <div className="mb-2 text-[10px] uppercase tracking-wider text-slate-500">
              {active.path} — {active.size_bytes} bytes{active.truncated ? " · TRUNCATED" : ""}
            </div>
            <pre className="whitespace-pre text-slate-200">{active.contents || ""}</pre>
          </>
        )}
      </div>
    </div>
  );
}
