import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, HttpError } from "../api";
import PipelineBoard from "../components/PipelineBoard";
import type {
  AuthConfig,
  CoverageEstimate,
  QualityScore,
  RegenDiff,
  RunResult,
  TestCoverScan,
  TestCoverGenerateResult,
  TestCoverProject,
  TestFramework,
  TestFrameworkInfo,
  TestLanguage,
  TestMode,
} from "../types";

// `framework` here is the runtime engine name we send to the generator —
// the backend's framework-id (e.g. "robot-py") collapses to "robot".
const FRAMEWORK_ID_TO_ENGINE: Record<string, TestFramework> = {
  "cypress-js": "cypress", "cypress-ts": "cypress",
  "playwright-js": "playwright",
  "robot-py": "robot",
  "pytest-playwright": "playwright",
  "selenium-py": "selenium",
};

const LANGUAGES: { value: TestLanguage; label: string }[] = [
  { value: "javascript", label: "JavaScript" },
  { value: "typescript", label: "TypeScript" },
  { value: "python",     label: "Python" },
];

// Aligned with the QAFLOW v4 spec test-coverage table.
const FOCUS_OPTIONS: { value: string; label: string; hint: string }[] = [
  { value: "ui_visual",      label: "UI / Visual",     hint: "Screenshot comparison, layout, responsive checks" },
  { value: "functional",     label: "Functional",      hint: "Element interactions, form validation, user flows" },
  { value: "api",            label: "API",             hint: "Network calls invoked from the page (XHR/fetch)" },
  { value: "smoke",          label: "Smoke",           hint: "Critical paths only — fast post-deploy gate" },
  { value: "regression",     label: "Regression",      hint: "Wide assertions to catch silent breakage" },
  { value: "cross_browser",  label: "Cross-browser",   hint: "Same suite, parallel against Chrome/FF/WebKit" },
  { value: "accessibility",  label: "Accessibility",   hint: "WCAG 2.1 AA: contrast, landmarks, aria, keyboard" },
  { value: "performance",    label: "Performance",     hint: "Load/response times under conservative budgets" },
];

const MODE_DEFINITIONS: Record<TestMode, { label: string; emoji: string; tagline: string; detail: string }> = {
  "black-box": {
    label: "Black-box",
    emoji: "🔒",
    tagline: "No source code — only the running UI",
    detail:
      "AI sees the rendered DOM via Playwright. It does NOT read your source. " +
      "Use this for vendor apps, security-sensitive code, or quick smoke coverage.",
  },
  "gray-box": {
    label: "Gray-box",
    emoji: "📝",
    tagline: "DOM + selected source files you paste",
    detail:
      "Paste page objects, API schemas, or component snippets. AI uses them " +
      "to write more accurate selectors and assertions.",
  },
  "white-box": {
    label: "White-box",
    emoji: "🔓",
    tagline: "DOM + full source from a public repo",
    detail:
      "AI clones the repo (read-only, shallow) and reads every relevant file " +
      "to build deeply-integrated tests. Heaviest mode — slowest generation.",
  },
};


export default function AITestCover() {
  const nav = useNavigate();

  const [url, setUrl] = useState("http://localhost:3001/login.html");
  const [language, setLanguage] = useState<TestLanguage>("javascript");
  // Selected backend framework-id (e.g. "cypress-js"). The runtime engine
  // ("cypress" / "playwright" / etc) is derived from it for the generate call.
  const [frameworkId, setFrameworkId] = useState<string>("cypress-js");
  const [mode, setMode] = useState<TestMode>("black-box");
  const [focus, setFocus] = useState<string[]>(["smoke"]);
  const [extraInstructions, setExtraInstructions] = useState("");
  const [sourcePaste, setSourcePaste] = useState("");
  const [sourceRepoUrl, setSourceRepoUrl] = useState("");

  // Frameworks discovered + install state.
  const [frameworks, setFrameworks] = useState<TestFrameworkInfo[]>([]);
  const [installLog, setInstallLog] = useState<string[]>([]);
  const [installingId, setInstallingId] = useState<string | null>(null);

  // Phase 2 features (this turn).
  const [authEnabled, setAuthEnabled] = useState(false);
  const [authCfg, setAuthCfg] = useState<AuthConfig>({
    kind: "form", login_url: "", username_field: "#email",
    password_field: "#password", submit: "#login-button",
    credentials: { username: "", password: "" },
  });
  const [captureBaseline, setCaptureBaseline] = useState(true);
  const [baselineB64, setBaselineB64] = useState<string | null>(null);
  const [coverage, setCoverage] = useState<CoverageEstimate | null>(null);
  const [scoreCard, setScoreCard] = useState<QualityScore | null>(null);
  const [diff, setDiff] = useState<RegenDiff | null>(null);
  const [runResult, setRunResult] = useState<RunResult | null>(null);

  const [scan, setScan] = useState<TestCoverScan | null>(null);
  const [generated, setGenerated] = useState<TestCoverGenerateResult | null>(null);
  // Editable, per-file content. Keyed by relative path.
  const [editedFiles, setEditedFiles] = useState<Record<string, string>>({});
  const [activeFile, setActiveFile] = useState<string | null>(null);

  // Project / env namespacing — multi-product orgs put each product's tests
  // into its own folder under cypress/e2e/.
  const [projects, setProjects] = useState<TestCoverProject[]>([]);
  const [projectMode, setProjectMode] = useState<"existing" | "new">("existing");
  const [selectedProject, setSelectedProject] = useState<string>("");
  const [newProjectName, setNewProjectName] = useState<string>("");
  const [env, setEnv] = useState<string>("");

  const [busy, setBusy] = useState<"scan" | "generate" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedPath, setSavedPath] = useState<string | null>(null);

  // v2 — multi-step pipeline toggle (turns on the PipelineBoard view).
  const [modeV2, setMode_v2] = useState(false);

  const projectName = projectMode === "new" ? newProjectName : selectedProject;

  // Load existing projects once on mount.
  useEffect(() => {
    api.listTestProjects().then((ps) => {
      setProjects(ps);
      const named = ps.find((p) => p.slug !== "");
      if (named) setSelectedProject(named.slug);
      else if (ps.length) setSelectedProject(ps[0].slug);
    }).catch(() => {});
    refreshFrameworks();
  }, []);

  const refreshFrameworks = () => {
    api.listFrameworks().then(setFrameworks).catch(() => {});
  };

  // When the engineer changes language, snap the framework selection to the
  // first framework available for that language.
  useEffect(() => {
    if (!frameworks.length) return;
    const candidates = frameworks.filter((f) => f.language === language);
    if (candidates.length === 0) return;
    if (!candidates.some((c) => c.id === frameworkId)) {
      setFrameworkId(candidates[0].id);
    }
  }, [language, frameworks, frameworkId]);

  // While an install is running, poll its log and re-list frameworks at the end.
  useEffect(() => {
    if (!installingId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const status = await api.installStatus(installingId);
        if (cancelled) return;
        setInstallLog(status.log);
        if (status.status === "succeeded" || status.status === "failed") {
          setInstallingId(null);
          refreshFrameworks();
          return;
        }
        setTimeout(tick, 1500);
      } catch {
        if (!cancelled) setInstallingId(null);
      }
    };
    tick();
    return () => { cancelled = true; };
  }, [installingId]);

  const onInstallFramework = async (id: string) => {
    setError(null);
    setInstallLog([`▶ kicking off install for ${id} …`]);
    setInstallingId(id);
    try {
      await api.installFramework(id);
    } catch (e: unknown) {
      setInstallingId(null);
      setError(e instanceof Error ? e.message : "install failed to start");
    }
  };

  const effectiveProject = projectMode === "new"
    ? newProjectName.trim()
    : selectedProject;

  // Pre-fill the env dropdown from the selected project's known envs.
  const knownEnvs = useMemo(() => {
    const p = projects.find((p) => p.slug === selectedProject);
    return p?.envs.map((e) => e.slug) ?? [];
  }, [projects, selectedProject]);

  // Auto-suggest a project name from the URL hostname when the engineer
  // hasn't picked one yet.
  useEffect(() => {
    if (projectMode !== "new" || newProjectName) return;
    try {
      const host = new URL(url).hostname;
      const base = host.split(".").filter((p) => p && !["www", "com", "io", "net", "org", "co", "app", "dev"].includes(p))[0];
      if (base) setNewProjectName(base);
    } catch { /* invalid URL — ignore */ }
  }, [url, projectMode, newProjectName]);

  const toggleFocus = (v: string) =>
    setFocus((f) => (f.includes(v) ? f.filter((x) => x !== v) : [...f, v]));

  const onScan = async () => {
    setBusy("scan"); setError(null); setScan(null);
    setCoverage(null); setBaselineB64(null);
    try {
      const r = await api.scanForTests(url, {
        auth: authEnabled ? authCfg : undefined,
        capture_baseline: captureBaseline,
      });
      // Pull the baseline out of the scan result so we don't drag it into prompts.
      const { baseline_screenshot_b64, ...rest } = r as TestCoverScan & { baseline_screenshot_b64?: string };
      setScan(rest);
      if (baseline_screenshot_b64) setBaselineB64(baseline_screenshot_b64);
      // Coverage preview based on current focus selection.
      try {
        const cov = await api.coverageEstimate(rest, focus);
        setCoverage(cov);
      } catch { /* non-fatal */ }
    } catch (e: unknown) {
      setError(e instanceof HttpError ? e.message : (e instanceof Error ? e.message : "scan failed"));
    } finally { setBusy(null); }
  };

  const onGenerate = async () => {
    setBusy("generate"); setError(null); setGenerated(null);
    setEditedFiles({}); setActiveFile(null); setSavedPath(null);
    try {
      const engine = FRAMEWORK_ID_TO_ENGINE[frameworkId] || "cypress";
      const r = await api.generateTests({
        url, framework: engine, language, mode,
        test_focus: focus,
        source_paste: mode === "gray-box" ? sourcePaste : undefined,
        source_repo_url: mode === "white-box" ? sourceRepoUrl : undefined,
        extra_instructions: extraInstructions || undefined,
        scan: scan || undefined,
      });
      setGenerated(r);
      setEditedFiles({ ...r.files });
      const firstFile = Object.keys(r.files)[0] || null;
      setActiveFile(firstFile);
      setRunResult(null);

      // Compute scorecard + diff in parallel.
      api.scoreFiles(r.files).then(setScoreCard).catch(() => {});
      const projectForDiff = (projectMode === "new" ? newProjectName : selectedProject).trim();
      if (projectForDiff) {
        api.diffAgainstBundle({
          files: r.files, framework: frameworkId,
          project: projectForDiff, env: env.trim() || undefined,
        }).then(setDiff).catch(() => {});
      }
    } catch (e: unknown) {
      setError(e instanceof HttpError ? e.message : (e instanceof Error ? e.message : "generation failed"));
    } finally { setBusy(null); }
  };

  const onRunNow = async () => {
    if (!savedPath) return;
    const projectForRun = (projectMode === "new" ? newProjectName : selectedProject).trim();
    setBusy("save"); setError(null); setRunResult(null);
    try {
      const r = await api.runSuite({
        framework: frameworkId, project: projectForRun, env: env.trim() || undefined,
      });
      setRunResult(r);
    } catch (e: unknown) {
      setError(e instanceof HttpError ? e.message : (e instanceof Error ? e.message : "run failed"));
    } finally { setBusy(null); }
  };

  const onSave = async () => {
    if (!Object.keys(editedFiles).length || !generated) return;
    setBusy("save"); setError(null); setSavedPath(null);
    try {
      const projectForSave = (projectMode === "new"
        ? newProjectName.trim()
        : selectedProject).trim();
      if (!projectForSave) {
        setError("Pick or name a project first.");
        setBusy(null); return;
      }
      const r = await api.saveBundle({
        files: editedFiles,
        framework: frameworkId,
        project: projectForSave,
        env: env.trim() || undefined,
        baseline_b64: baselineB64 || undefined,
      });
      setSavedPath(r.bundle_relative);
      api.listTestProjects().then(setProjects).catch(() => {});
      if (projectMode === "new" && newProjectName.trim()) {
        setProjectMode("existing");
        setSelectedProject(newProjectName.trim());
      }
    } catch (e: unknown) {
      setError(e instanceof HttpError ? e.message : (e instanceof Error ? e.message : "save failed"));
    } finally { setBusy(null); }
  };

  // The new bundle root: tests/{project}/{project}-{folder_name}/[{env}/].
  const previewBundle = useMemo(() => {
    const fw = frameworks.find((f) => f.id === frameworkId);
    if (!fw) return null;
    const projectSlug = (projectMode === "new" ? newProjectName : selectedProject)
      .trim().toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "")
      || "unnamed";
    const folder = `${projectSlug}-${fw.folder_name}`;
    const envSeg = env.trim() ? `/${env.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, "-")}` : "";
    return `tests/${projectSlug}/${folder}${envSeg}`;
  }, [frameworks, frameworkId, projectMode, newProjectName, selectedProject, env]);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink-100">AI Test Cover Engine</h1>
          <p className="text-sm text-ink-400 mt-0.5">
            Point at any URL, choose a framework + language, and the AI generates a
            full automation suite tailored to the page. Black-box by default; paste
            source or a repo URL to switch into gray/white-box.
          </p>
        </div>
        <div className="flex gap-1 rounded-md border border-ink-700 bg-ink-900/50 p-1 text-xs">
          <button
            type="button"
            onClick={() => setMode_v2(false)}
            className={
              "rounded px-3 py-1 " +
              (!modeV2 ? "bg-violet-500/15 text-violet-200" : "text-ink-400 hover:text-ink-100")
            }
          >
            v1 — Single-shot
          </button>
          <button
            type="button"
            onClick={() => setMode_v2(true)}
            className={
              "rounded px-3 py-1 " +
              (modeV2 ? "bg-emerald-500/15 text-emerald-200" : "text-ink-400 hover:text-ink-100")
            }
          >
            v2 — Multi-step Pipeline ✦
          </button>
        </div>
      </header>

      {modeV2 && (
        <PipelineBoard
          project={projectName || "demo"}
          framework={frameworkId}
          initialUrl={url}
          authJson={authEnabled ? JSON.stringify(authCfg) : undefined}
        />
      )}

      {!modeV2 && (<>
      <p className="hidden">{/* v1 UI below */}</p>

      {error && (
        <div className="rounded border border-rose-500/30 bg-rose-500/5 text-rose-300 px-4 py-2 text-sm">
          {error}
        </div>
      )}

      {/* ---- Step 1: scan ------------------------------------------------ */}
      <section className="rounded-lg border border-ink-700 bg-ink-800/40 p-5 space-y-3">
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-violet-500/20 border border-violet-500/40 text-violet-300 text-xs font-bold">1</span>
          <h2 className="text-sm font-semibold text-ink-100 uppercase tracking-wider">Scan the page</h2>
        </div>
        <div className="flex flex-col sm:flex-row gap-3 items-stretch">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://your.app/login"
            className="flex-1 bg-ink-950 border border-ink-700 rounded px-3 py-2 text-sm text-ink-100 outline-none focus:border-violet-500/50"
          />
          <button
            onClick={onScan}
            disabled={!url || busy !== null}
            className={`px-5 py-2 rounded font-semibold text-sm whitespace-nowrap ${
              busy === "scan"
                ? "bg-ink-700/50 text-ink-500 cursor-wait"
                : "bg-violet-500 hover:bg-violet-400 text-ink-950"
            }`}
          >
            {busy === "scan" ? "Scanning…" : "🔍 Analyze page"}
          </button>
        </div>

        {/* ---- Auth + capture toggles ------------------------------- */}
        <div className="flex items-center gap-3 flex-wrap text-xs">
          <label className="inline-flex items-center gap-1.5 text-ink-300">
            <input type="checkbox" checked={captureBaseline}
              onChange={(e) => setCaptureBaseline(e.target.checked)} />
            📸 Capture visual baseline
          </label>
          <label className="inline-flex items-center gap-1.5 text-ink-300">
            <input type="checkbox" checked={authEnabled}
              onChange={(e) => setAuthEnabled(e.target.checked)} />
            🔐 Login first (auth-protected pages)
          </label>
        </div>

        {authEnabled && (
          <div className="rounded border border-amber-500/30 bg-amber-500/5 p-3 grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
            <input className="bg-ink-950 border border-ink-700 rounded px-2 py-1 text-ink-100 font-mono"
              placeholder="login URL (e.g. https://app/login)" value={authCfg.login_url}
              onChange={(e) => setAuthCfg({ ...authCfg, login_url: e.target.value })} />
            <input className="bg-ink-950 border border-ink-700 rounded px-2 py-1 text-ink-100 font-mono"
              placeholder="submit selector (e.g. #login-button)" value={authCfg.submit}
              onChange={(e) => setAuthCfg({ ...authCfg, submit: e.target.value })} />
            <input className="bg-ink-950 border border-ink-700 rounded px-2 py-1 text-ink-100 font-mono"
              placeholder="username field selector (e.g. #email)" value={authCfg.username_field}
              onChange={(e) => setAuthCfg({ ...authCfg, username_field: e.target.value })} />
            <input className="bg-ink-950 border border-ink-700 rounded px-2 py-1 text-ink-100 font-mono"
              placeholder="password field selector (e.g. #password)" value={authCfg.password_field}
              onChange={(e) => setAuthCfg({ ...authCfg, password_field: e.target.value })} />
            <input className="bg-ink-950 border border-ink-700 rounded px-2 py-1 text-ink-100 font-mono"
              placeholder="username" value={authCfg.credentials.username}
              onChange={(e) => setAuthCfg({ ...authCfg, credentials: { ...authCfg.credentials, username: e.target.value } })} />
            <input type="password" className="bg-ink-950 border border-ink-700 rounded px-2 py-1 text-ink-100 font-mono"
              placeholder="password" value={authCfg.credentials.password}
              onChange={(e) => setAuthCfg({ ...authCfg, credentials: { ...authCfg.credentials, password: e.target.value } })} />
          </div>
        )}

        {scan && (
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2 pt-2">
            {Object.entries(scan.counts).map(([k, v]) => (
              <div key={k} className="rounded bg-ink-900/60 border border-ink-700 px-3 py-2">
                <div className="font-mono text-lg text-violet-300">{v}</div>
                <div className="text-[10px] uppercase tracking-wider text-ink-500">{k}</div>
              </div>
            ))}
          </div>
        )}

        {coverage && (
          <details className="text-xs text-ink-400 mt-2" open>
            <summary className="cursor-pointer hover:text-ink-200 select-none">
              Coverage preview — {coverage.covered_estimate}/{coverage.total} planned
              ({Math.round(coverage.ratio * 100)}%)
            </summary>
            <ul className="mt-2 space-y-0.5 max-h-56 overflow-y-auto pr-1">
              {coverage.items.map((it, i) => (
                <li key={i} className={`flex items-center gap-2 ${it.covered ? "text-ink-200" : "text-ink-600"}`}>
                  <span className={it.covered ? "text-emerald-400" : "text-rose-400"}>
                    {it.covered ? "✓" : "✗"}
                  </span>
                  <span className="text-[10px] font-mono uppercase tracking-wider text-ink-500 w-20 shrink-0">
                    {it.category}
                  </span>
                  <span className="truncate">{it.name}</span>
                  {it.note && <span className="text-amber-400 text-[10px]">— {it.note}</span>}
                </li>
              ))}
            </ul>
          </details>
        )}

        {scan && (
          <details className="text-xs text-ink-400 mt-2">
            <summary className="cursor-pointer hover:text-ink-200">Element inventory ({scan.title})</summary>
            <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-3 font-mono">
              <InventorySection title="Headings"  items={scan.headings.map(h => `${h.tag} · ${h.text}`)} />
              <InventorySection title="Buttons"   items={scan.buttons.map(b => `${b.id ? "#"+b.id : "—"} · ${b.text || "(no text)"}`)} />
              <InventorySection title="Links"     items={scan.links.map(l => `${l.text || "(no text)"} → ${l.href}`)} />
              <InventorySection title="Inputs"    items={scan.inputs.map(i => `${i.tag}${i.type ? "/"+i.type : ""} · ${i.id ? "#"+i.id : i.placeholder || "(no id)"}`)} />
              <InventorySection title="Forms"     items={scan.forms.map(f => `${f.id ? "#"+f.id : "(unnamed)"} ${f.method} · ${f.input_ids.join(", ")}`)} />
              <InventorySection title="Landmarks" items={scan.landmarks.map(l => l.landmark + (l.id ? "#"+l.id : ""))} />
            </div>
          </details>
        )}
      </section>

      {/* ---- Step 2: configure ----------------------------------------- */}
      <section className="rounded-lg border border-ink-700 bg-ink-800/40 p-5 space-y-4">
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-cyan-500/20 border border-cyan-500/40 text-cyan-300 text-xs font-bold">2</span>
          <h2 className="text-sm font-semibold text-ink-100 uppercase tracking-wider">Choose framework + mode</h2>
        </div>

        <div>
          <label className="text-xs text-ink-400 uppercase tracking-wider">Language</label>
          <div className="flex gap-2 mt-1">
            {LANGUAGES.map((l) => (
              <button
                key={l.value}
                onClick={() => setLanguage(l.value)}
                className={`text-xs px-3 py-1.5 rounded border ${
                  language === l.value
                    ? "bg-cyan-500/15 text-cyan-300 border-cyan-500/40"
                    : "bg-ink-900/40 text-ink-400 border-ink-700 hover:border-ink-500"
                }`}
              >{l.label}</button>
            ))}
          </div>
        </div>

        <div>
          <label className="text-xs text-ink-400 uppercase tracking-wider">
            Framework <span className="text-ink-600 normal-case">— filtered by {language}</span>
          </label>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mt-1">
            {frameworks.filter((f) => f.language === language).map((f) => {
              const selected = f.id === frameworkId;
              const installing = installingId === f.id;
              return (
                <div
                  key={f.id}
                  className={`rounded border p-3 transition cursor-pointer ${
                    selected
                      ? "border-cyan-500/50 bg-cyan-500/5"
                      : "border-ink-700 bg-ink-900/30 hover:border-ink-500"
                  }`}
                  onClick={() => setFrameworkId(f.id)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-semibold text-ink-100">{f.name}</div>
                    {f.installed ? (
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded border bg-emerald-500/10 text-emerald-300 border-emerald-500/30">
                        ✓ INSTALLED
                      </span>
                    ) : installing ? (
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded border bg-amber-500/10 text-amber-300 border-amber-500/30">
                        ⏳ INSTALLING
                      </span>
                    ) : (
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded border bg-rose-500/10 text-rose-300 border-rose-500/30">
                        ⬇ NOT INSTALLED
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-ink-400 mt-1">{f.description}</div>
                  <div className="text-[10px] font-mono text-ink-500 mt-2">
                    workspace: {f.workspace} · ext: {f.extension}
                    {f.version && <> · {f.version}</>}
                  </div>
                  {!f.installed && !installing && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onInstallFramework(f.id); }}
                      className="mt-2 px-3 py-1 rounded text-xs font-semibold bg-violet-500 hover:bg-violet-400 text-ink-950"
                    >
                      Install now
                    </button>
                  )}
                  {f.install_error && (
                    <div className="mt-2 text-[10px] text-rose-400 font-mono">⚠ {f.install_error}</div>
                  )}
                </div>
              );
            })}
            {frameworks.filter((f) => f.language === language).length === 0 && (
              <div className="text-xs text-ink-500 col-span-2">
                No frameworks registered for {language} yet.
              </div>
            )}
          </div>

          {(installingId || installLog.length > 0) && (
            <details open={!!installingId} className="mt-3">
              <summary className="cursor-pointer text-xs text-ink-400 hover:text-ink-200">
                Install log {installingId ? "(running…)" : ""}
              </summary>
              <pre className="mt-2 max-h-48 overflow-y-auto bg-ink-950 border border-ink-700 rounded p-2 text-[11px] font-mono text-ink-300 leading-snug">
                {installLog.join("\n") || "(empty)"}
              </pre>
            </details>
          )}
        </div>

        <div>
          <div className="text-xs text-ink-400 uppercase tracking-wider mb-2">
            Test category <span className="text-ink-600 normal-case">— one or more (PDF v4 alignment)</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
            {FOCUS_OPTIONS.map((o) => {
              const active = focus.includes(o.value);
              return (
                <button
                  key={o.value}
                  onClick={() => toggleFocus(o.value)}
                  title={o.hint}
                  className={`text-left rounded border px-3 py-2 transition ${
                    active
                      ? "bg-cyan-500/10 text-cyan-200 border-cyan-500/40"
                      : "bg-ink-900/40 text-ink-300 border-ink-700 hover:border-ink-500"
                  }`}
                >
                  <div className="text-sm font-semibold">{o.label}</div>
                  <div className="text-[10px] text-ink-500 leading-snug">{o.hint}</div>
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <div className="text-xs text-ink-400 uppercase tracking-wider mb-2">
            Knowledge model <span className="text-ink-600 normal-case">— how much source code AI may see</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            {(["black-box", "gray-box", "white-box"] as TestMode[]).map((m) => {
              const def = MODE_DEFINITIONS[m];
              const active = mode === m;
              return (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`text-left rounded border px-3 py-2 transition ${
                    active
                      ? "bg-emerald-500/10 text-emerald-200 border-emerald-500/40"
                      : "bg-ink-900/40 text-ink-300 border-ink-700 hover:border-ink-500"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-base">{def.emoji}</span>
                    <span className="text-sm font-semibold">{def.label}</span>
                  </div>
                  <div className="text-[11px] text-ink-400 leading-snug mt-1">{def.tagline}</div>
                  {active && (
                    <div className="text-[10px] text-ink-500 leading-snug mt-1">{def.detail}</div>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {mode === "gray-box" && (
          <div>
            <label className="text-xs text-ink-400 uppercase tracking-wider">Paste source files</label>
            <textarea value={sourcePaste} onChange={(e) => setSourcePaste(e.target.value)}
              rows={6}
              placeholder="### login.html&#10;<form>...</form>&#10;&#10;### app.js&#10;function login() { ... }"
              className="w-full bg-ink-950 border border-ink-700 rounded px-3 py-2 text-xs font-mono text-ink-100 mt-1" />
          </div>
        )}

        {mode === "white-box" && (
          <div>
            <label className="text-xs text-ink-400 uppercase tracking-wider">Public repo URL (cloned read-only)</label>
            <input type="url" value={sourceRepoUrl} onChange={(e) => setSourceRepoUrl(e.target.value)}
              placeholder="https://github.com/owner/repo.git"
              className="w-full bg-ink-950 border border-ink-700 rounded px-3 py-2 text-sm text-ink-100 mt-1" />
          </div>
        )}

        <div>
          <label className="text-xs text-ink-400 uppercase tracking-wider">Extra instructions (optional)</label>
          <textarea value={extraInstructions} onChange={(e) => setExtraInstructions(e.target.value)}
            rows={2}
            placeholder="e.g. use data-testid where available; cover the success path only; keep tests under 10 lines each"
            className="w-full bg-ink-950 border border-ink-700 rounded px-3 py-2 text-xs font-mono text-ink-100 mt-1" />
        </div>

        <button onClick={onGenerate} disabled={busy !== null}
          className={`px-5 py-2.5 rounded font-semibold text-sm ${
            busy === "generate"
              ? "bg-ink-700/50 text-ink-500 cursor-wait"
              : "bg-cyan-500 hover:bg-cyan-400 text-ink-950"
          }`}>
          {busy === "generate" ? "Generating…" : "🤖 Generate test suite"}
        </button>
      </section>

      {/* ---- Step 3: review + save ------------------------------------- */}
      {generated && (
        <section className="rounded-lg border border-ink-700 bg-ink-800/40 p-5 space-y-3">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 text-xs font-bold">3</span>
            <h2 className="text-sm font-semibold text-ink-100 uppercase tracking-wider">Review and save</h2>
            <span className="text-xs text-ink-500 font-mono">
              engine: <span className={generated.engine === "claude" ? "text-violet-300" : "text-amber-300"}>{generated.engine}</span>
              {" · "}
              {generated.framework}/{generated.language}
              {" · "}
              <span className="text-cyan-300">{Object.keys(editedFiles).length} file{Object.keys(editedFiles).length === 1 ? "" : "s"}</span>
            </span>
            {scoreCard && (
              <span className={`text-xs px-2 py-0.5 rounded-full border font-mono ${
                scoreCard.grade === "A" ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-300"
                : scoreCard.grade === "B" ? "bg-cyan-500/15 border-cyan-500/40 text-cyan-300"
                : scoreCard.grade === "C" ? "bg-amber-500/15 border-amber-500/40 text-amber-300"
                : "bg-rose-500/15 border-rose-500/40 text-rose-300"
              }`} title={scoreCard.notes.map(n => `${n.level}: ${n.msg}`).join("\n")}>
                Quality: {scoreCard.grade} ({scoreCard.score}/100)
              </span>
            )}
            {diff?.exists && (
              <span className="text-xs px-2 py-0.5 rounded-full border bg-ink-700/40 border-ink-600 text-ink-200 font-mono">
                regen: {diff.items.filter(i => i.status === "modified").length} mod ·{" "}
                {diff.items.filter(i => i.status === "added").length} new ·{" "}
                {diff.items.filter(i => i.status === "removed").length} del
              </span>
            )}
            {generated.fallback_reason && (
              <span className="text-xs text-amber-400 font-mono">⚠ {generated.fallback_reason}</span>
            )}
          </div>

          {scoreCard && scoreCard.notes.length > 0 && (
            <details className="text-[11px] text-ink-400">
              <summary className="cursor-pointer hover:text-ink-200">Quality notes</summary>
              <ul className="mt-1 space-y-0.5">
                {scoreCard.notes.map((n, i) => (
                  <li key={i} className={n.level === "ok" ? "text-emerald-400" : "text-amber-400"}>
                    {n.level === "ok" ? "✓" : "⚠"} {n.msg}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {/* ---- Project / env namespace --------------------------- */}
          <div className="rounded border border-ink-700 bg-ink-900/40 p-3 space-y-3">
            <div className="text-[10px] uppercase tracking-wider text-ink-500">Project / product</div>
            <div className="flex gap-2 text-xs">
              <button
                onClick={() => setProjectMode("existing")}
                className={`px-3 py-1 rounded border ${
                  projectMode === "existing"
                    ? "bg-cyan-500/15 text-cyan-300 border-cyan-500/40"
                    : "bg-ink-900/40 text-ink-400 border-ink-700"
                }`}
              >Existing</button>
              <button
                onClick={() => setProjectMode("new")}
                className={`px-3 py-1 rounded border ${
                  projectMode === "new"
                    ? "bg-cyan-500/15 text-cyan-300 border-cyan-500/40"
                    : "bg-ink-900/40 text-ink-400 border-ink-700"
                }`}
              >+ New project</button>
            </div>
            {projectMode === "existing" ? (
              <select
                value={selectedProject}
                onChange={(e) => setSelectedProject(e.target.value)}
                className="w-full bg-ink-950 border border-ink-700 rounded px-3 py-2 text-sm text-ink-100"
              >
                {projects.length === 0 && <option value="">— no projects yet — start a new one —</option>}
                {projects.map((p) => (
                  <option key={p.slug || "default"} value={p.slug}>
                    {p.name} · {p.spec_count} spec{p.spec_count === 1 ? "" : "s"}
                    {p.envs.length ? `, ${p.envs.length} env${p.envs.length === 1 ? "" : "s"}` : ""}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                placeholder="e.g. sporthub  (auto-suggested from URL)"
                className="w-full bg-ink-950 border border-ink-700 rounded px-3 py-2 text-sm font-mono text-ink-100"
              />
            )}
            <div>
              <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1">
                Environment <span className="text-ink-600 normal-case">(optional — e.g. mock / staging / prod)</span>
              </div>
              <input
                list="env-suggestions"
                value={env}
                onChange={(e) => setEnv(e.target.value)}
                placeholder="leave empty if you don't separate envs"
                className="w-full bg-ink-950 border border-ink-700 rounded px-3 py-2 text-sm font-mono text-ink-100"
              />
              <datalist id="env-suggestions">
                {["mock", "dev", "staging", "prod"].map((s) => <option key={s} value={s} />)}
                {knownEnvs.map((s) => <option key={s} value={s} />)}
              </datalist>
            </div>
            <div className="text-[11px] font-mono text-ink-500 break-all">
              bundle root:{" "}
              <span className="text-cyan-400">qaflow-ai/{previewBundle}/</span>
            </div>
          </div>

          {/* ---- File tabs ---------------------------------------------- */}
          <div className="rounded border border-ink-700 bg-ink-900/40 overflow-hidden">
            <div className="flex flex-wrap gap-px bg-ink-700/40 border-b border-ink-700">
              {Object.keys(editedFiles).map((path) => (
                <button
                  key={path}
                  onClick={() => setActiveFile(path)}
                  className={`px-3 py-1.5 text-[11px] font-mono ${
                    activeFile === path
                      ? "bg-ink-900 text-ink-100"
                      : "bg-ink-800/60 text-ink-400 hover:text-ink-200"
                  }`}
                  title={path}
                >
                  {path}
                </button>
              ))}
            </div>
            {activeFile && (
              <textarea
                value={editedFiles[activeFile] || ""}
                onChange={(e) =>
                  setEditedFiles((prev) => ({ ...prev, [activeFile]: e.target.value }))
                }
                rows={Math.min(28, Math.max(10, (editedFiles[activeFile] || "").split("\n").length + 1))}
                spellCheck={false}
                className="w-full bg-ink-950 px-3 py-2 text-xs font-mono text-ink-100 leading-relaxed border-0 outline-none resize-y"
              />
            )}
          </div>

          <div className="flex flex-col sm:flex-row gap-3 items-stretch">
            <button onClick={onSave} disabled={busy !== null || Object.keys(editedFiles).length === 0}
              className={`px-6 py-3 rounded font-bold text-sm whitespace-nowrap shadow-lg shadow-emerald-500/10 ${
                busy === "save"
                  ? "bg-ink-700/50 text-ink-500 cursor-wait"
                  : "bg-emerald-500 hover:bg-emerald-400 text-ink-950 ring-1 ring-emerald-400/40"
              }`}>
              {busy === "save" ? "Creating files…" : `✨ CREATE ${Object.keys(editedFiles).length} TEST FILE${Object.keys(editedFiles).length === 1 ? "" : "S"}`}
            </button>
            <button onClick={() => nav("/test-runner")}
              className="px-5 py-2 rounded font-semibold text-sm bg-ink-700/40 text-ink-200 hover:bg-ink-700/60 whitespace-nowrap">
              ▶ Go run it
            </button>
          </div>

          {savedPath && (
            <div className="rounded border border-emerald-500/30 bg-emerald-500/5 px-4 py-2.5 text-xs space-y-2">
              <div className="text-emerald-300 font-semibold">
                ✓ Test files written
              </div>
              <div className="text-ink-200 font-mono break-all">
                qaflow-ai/{savedPath}/
              </div>
              <div className="flex items-center gap-2 pt-1">
                <button
                  onClick={onRunNow}
                  disabled={busy !== null}
                  className={`px-4 py-2 rounded font-semibold text-xs ${
                    busy === "save"
                      ? "bg-ink-700/50 text-ink-500 cursor-wait"
                      : "bg-cyan-500 hover:bg-cyan-400 text-ink-950"
                  }`}
                >
                  {busy === "save" ? "Running…" : "▶ Run this suite now"}
                </button>
                <span className="text-ink-500">runs in {frameworkId}'s workspace using its native runner</span>
              </div>
            </div>
          )}

          {runResult && (
            <div className={`rounded border px-4 py-2.5 text-xs space-y-2 ${
              runResult.exit_code === 0
                ? "border-emerald-500/30 bg-emerald-500/5"
                : "border-rose-500/30 bg-rose-500/5"
            }`}>
              <div className={`font-semibold ${runResult.exit_code === 0 ? "text-emerald-300" : "text-rose-300"}`}>
                {runResult.exit_code === 0 ? "✓ All tests passed" : "✗ Run failed"}
                <span className="ml-2 text-ink-400 font-mono">
                  exit={runResult.exit_code} · {runResult.duration_s}s
                  {runResult.passed != null && ` · ${runResult.passed} passed`}
                  {runResult.failed != null && runResult.failed > 0 && ` · ${runResult.failed} failed`}
                  {runResult.timed_out && " · TIMED OUT"}
                </span>
              </div>
              <details>
                <summary className="cursor-pointer text-ink-400 hover:text-ink-200">Run log (last lines)</summary>
                <pre className="mt-1 max-h-64 overflow-y-auto bg-ink-950 border border-ink-700 rounded p-2 text-[10px] font-mono text-ink-300 leading-snug whitespace-pre-wrap">
                  {runResult.log_tail}
                </pre>
              </details>
            </div>
          )}
        </section>
      )}
      </>)}
    </div>
  );
}


function InventorySection({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1">{title} ({items.length})</div>
      {items.length === 0 ? (
        <div className="text-ink-600">—</div>
      ) : (
        <ul className="space-y-0.5 max-h-40 overflow-y-auto pr-1">
          {items.slice(0, 30).map((it, i) => (
            <li key={i} className="truncate text-ink-300">{it}</li>
          ))}
          {items.length > 30 && (
            <li className="text-ink-600 italic">…+{items.length - 30} more</li>
          )}
        </ul>
      )}
    </div>
  );
}
