import type {
  AuthConfig,
  Bug,
  CoverageEstimate,
  CrawlResult,
  CypressRun,
  DashboardSummary,
  InstallStatus,
  ManualBug,
  PipelineRunResult,
  PipelineSnapshot,
  PipelineStepResult,
  QualityScore,
  RegenDiff,
  Run,
  RunResult,
  SavedBundle,
  TestCoverGenerateRequest,
  TestCoverGenerateResult,
  TestCoverProject,
  TestCoverScan,
  TestFrameworkInfo,
  User,
} from "./types";

const TOKEN_KEY = "qaflow_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

class HttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string>) || {}),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j.detail) detail = j.detail;
    } catch { /* ignore */ }
    throw new HttpError(res.status, detail);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

export { HttpError };

export const api = {
  // ---- auth
  login: (username: string, password: string) =>
    http<{ token: string; user: User }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => http<User>("/api/auth/me"),
  logout: () => http<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  listUsers: (role?: string) =>
    http<User[]>(role ? `/api/users?role=${encodeURIComponent(role)}` : "/api/users"),

  // ---- AI auto-fix
  health: () => http<{ status: string; ai_mode: string }>("/api/health"),
  dashboard: () => http<DashboardSummary>("/api/dashboard"),
  listRuns: () => http<Run[]>("/api/runs"),
  listBugs: () => http<Bug[]>("/api/bugs"),
  getBug: (uid: string) => http<Bug>(`/api/bugs/${uid}`),
  triggerRun: (suite: string) =>
    http<Run>("/api/runs", { method: "POST", body: JSON.stringify({ suite }) }),
  approve: (uid: string) => http<Bug>(`/api/bugs/${uid}/approve`, { method: "POST" }),
  reject:  (uid: string) => http<Bug>(`/api/bugs/${uid}/reject`,  { method: "POST" }),
  autoFix: (uid: string) =>
    http<{ status: string; uid: string }>(`/api/bugs/${uid}/auto-fix`, { method: "POST" }),

  // AI Test Cover Engine
  scanForTests: (url: string, opts?: { auth?: AuthConfig; capture_baseline?: boolean }) =>
    http<TestCoverScan & { baseline_screenshot_b64?: string }>(
      "/api/ai/test-writer/scan",
      { method: "POST", body: JSON.stringify({ url, ...(opts || {}) }) },
    ),
  crawlForTests: (payload: {
    url: string; max_pages?: number; same_origin?: boolean; auth?: AuthConfig;
  }) =>
    http<CrawlResult>("/api/ai/test-writer/crawl", {
      method: "POST", body: JSON.stringify(payload),
    }),
  coverageEstimate: (scan: TestCoverScan, focus: string[]) =>
    http<CoverageEstimate>("/api/ai/test-writer/coverage", {
      method: "POST", body: JSON.stringify({ scan, test_focus: focus }),
    }),
  scoreFiles: (files: Record<string, string>) =>
    http<QualityScore>("/api/ai/test-writer/score", {
      method: "POST", body: JSON.stringify({ files }),
    }),
  diffAgainstBundle: (payload: {
    files: Record<string, string>; framework: string; project: string; env?: string;
  }) =>
    http<RegenDiff>("/api/ai/test-writer/diff", {
      method: "POST", body: JSON.stringify(payload),
    }),
  runSuite: (payload: { framework: string; project: string; env?: string }) =>
    http<RunResult>("/api/ai/test-writer/run", {
      method: "POST", body: JSON.stringify(payload),
    }),
  generateTests: (payload: TestCoverGenerateRequest) =>
    http<TestCoverGenerateResult>("/api/ai/test-writer/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  saveBundle: (payload: {
    files: Record<string, string>;
    framework: string;
    project: string;
    env?: string;
    baseline_b64?: string;
  }) =>
    http<SavedBundle>("/api/ai/test-writer/save", {
      method: "POST", body: JSON.stringify(payload),
    }),
  listTestProjects: () =>
    http<TestCoverProject[]>("/api/ai/test-writer/projects"),

  listFrameworks: () =>
    http<TestFrameworkInfo[]>("/api/ai/test-writer/frameworks"),
  installFramework: (id: string) =>
    http<{ framework_id: string; status: string }>(
      `/api/ai/test-writer/frameworks/${id}/install`,
      { method: "POST" },
    ),
  installStatus: (id: string) =>
    http<InstallStatus>(`/api/ai/test-writer/frameworks/${id}/install-status`),
  screenshotUrl: (name: string) => `/api/screenshots/${name}`,

  // ---- AI Test Cover v2 — multi-step pipeline (discovery/smoke/e2e/negative/api/validate/extend)
  pipelineDiscover: (payload: {
    project: string; mode: "product" | "git" | "pdf";
    url?: string; repo_url?: string; pdf_path?: string;
    auth?: AuthConfig; test_users?: Array<Record<string, unknown>>;
    max_pages?: number;
  }) =>
    http<{
      project: string; app_index: Record<string, unknown>;
      orchestrator: PipelineSnapshot;
    }>("/api/ai/test-writer/discover", { method: "POST", body: JSON.stringify(payload) }),

  pipelineSmoke: (payload: { project: string; framework: string; env?: string }) =>
    http<PipelineStepResult>("/api/ai/test-writer/smoke", {
      method: "POST", body: JSON.stringify(payload),
    }),
  pipelineE2E: (payload: { project: string; framework: string; env?: string; force?: boolean }) =>
    http<PipelineStepResult>("/api/ai/test-writer/e2e", {
      method: "POST", body: JSON.stringify(payload),
    }),
  pipelineNegative: (payload: { project: string; framework: string; env?: string; force?: boolean }) =>
    http<PipelineStepResult>("/api/ai/test-writer/negative", {
      method: "POST", body: JSON.stringify(payload),
    }),

  pipelineSmokeRun: (payload: { project: string; framework: string; env?: string }) =>
    http<PipelineRunResult>("/api/ai/test-writer/smoke-run", {
      method: "POST", body: JSON.stringify(payload),
    }),
  pipelineE2ERun: (payload: { project: string; framework: string; env?: string }) =>
    http<PipelineRunResult>("/api/ai/test-writer/e2e-run", {
      method: "POST", body: JSON.stringify(payload),
    }),
  pipelineNegativeRun: (payload: { project: string; framework: string; env?: string }) =>
    http<PipelineRunResult>("/api/ai/test-writer/negative-run", {
      method: "POST", body: JSON.stringify(payload),
    }),

  pipelineApiDiscovery: (payload: { project: string }) =>
    http<{
      project: string; openapi_path: string; operations_count: number;
      tag_counts: Record<string, number>; auth_schemes_detected: string[];
      coverage_warnings: string[]; yaml_preview: string;
      orchestrator: PipelineSnapshot;
    }>("/api/ai/test-writer/api-discovery", { method: "POST", body: JSON.stringify(payload) }),

  pipelineValidate: (payload: { project: string; framework: string; env?: string }) =>
    http<{
      project: string; report_path: string; verdict: "GREEN" | "YELLOW" | "RED";
      verdict_reason: string;
      totals: Record<string, Record<string, number>>;
      top_risks: string[];
      expansion_plan_summary: Array<{ priority: string; description: string; effort: string }>;
      report_md: string;
      orchestrator: PipelineSnapshot;
    }>("/api/ai/test-writer/validate", { method: "POST", body: JSON.stringify(payload) }),

  pipelineExtend: (payload: {
    project: string; framework: string; env?: string;
    gaps: string[]; rescan_urls?: string[];
  }) =>
    http<PipelineStepResult & {
      new_files: string[]; modified_files: string[];
      spurious_gaps: Array<{ gap: string; reason: string }>;
      coverage_documented_skip: Array<{ gap: string; reason: string }>;
    }>("/api/ai/test-writer/extend", { method: "POST", body: JSON.stringify(payload) }),

  pipelineState: (project: string) =>
    http<{
      project: string;
      orchestrator: PipelineSnapshot;
      has_app_index: boolean;
      pages_count: number;
      apis_count: number;
    }>(`/api/ai/test-writer/state/${encodeURIComponent(project)}`),

  pipelineProjectAppIndex: (project: string) =>
    http<{ project: string; path: string; app_index: Record<string, unknown> }>(
      `/api/ai/test-writer/projects/${encodeURIComponent(project)}/app-index`,
    ),

  pipelineProjectFiles: (project: string, framework: string, env?: string) =>
    http<{
      project: string; framework: string; env: string | null;
      bundle_root: string; file_count: number;
      files: Array<{
        path: string; size_bytes: number;
        contents?: string; truncated?: boolean; binary?: boolean; read_error?: string;
      }>;
    }>(
      `/api/ai/test-writer/projects/${encodeURIComponent(project)}/files` +
      `?framework=${encodeURIComponent(framework)}${env ? `&env=${encodeURIComponent(env)}` : ""}`,
    ),

  pipelineProjectDownloadUrl: (project: string, framework: string, env?: string): string =>
    `/api/ai/test-writer/projects/${encodeURIComponent(project)}/download` +
    `?framework=${encodeURIComponent(framework)}${env ? `&env=${encodeURIComponent(env)}` : ""}`,

  pipelineProjectReport: (project: string) =>
    http<{ project: string; path: string; report_md: string }>(
      `/api/ai/test-writer/projects/${encodeURIComponent(project)}/report`,
    ),

  pipelineProjectOpenapi: (project: string) =>
    http<{ project: string; path: string; openapi_yaml: string }>(
      `/api/ai/test-writer/projects/${encodeURIComponent(project)}/openapi`,
    ),

  aiAuditLog: (limit = 50, event_type?: string) =>
    http<{
      stats: {
        calls?: number; successes?: number; cache_hits?: number;
        avg_duration_ms?: number;
        oldest?: number; newest?: number; window_s?: number;
        by_engine?: Record<string, number>;
      };
      items: Array<{
        id: number; ts: number; event_type: string;
        bug_uid?: string | null;
        framework_id?: string | null;
        engine?: string | null;
        cache_hit: number;
        success: number;
        duration_ms?: number | null;
        summary?: string | null;
        error?: string | null;
      }>;
    }>(
      `/api/ai/audit?limit=${limit}${event_type ? `&event_type=${encodeURIComponent(event_type)}` : ""}`,
    ),

  pipelineRetryStep: (project: string, step: string) =>
    http<{ project: string; orchestrator: { current_state: string; blocked_reason: string | null } }>(
      `/api/ai/test-writer/state/${encodeURIComponent(project)}/retry`,
      { method: "POST", body: JSON.stringify({ step }) },
    ),

  pipelineInstallBundleDeps: (payload: { project: string; framework: string; env?: string }) =>
    http<{ project: string; framework: string; bundle_root: string; ok: boolean; log: string }>(
      "/api/ai/test-writer/install-bundle-deps",
      { method: "POST", body: JSON.stringify(payload) },
    ),

  pipelineOrchestrate: (payload: {
    project: string;
    framework: string;
    mode: "product" | "git" | "pdf";
    url?: string; repo_url?: string; pdf_path?: string;
    auth?: AuthConfig;
    test_users?: Array<Record<string, unknown>>;
    max_pages?: number;
    env?: string;
    stop_after?: string;
  }) =>
    http<{
      project: string; framework: string; mode: string; started: boolean;
      orchestrator: PipelineSnapshot; subscribe_via: string;
    }>("/api/ai/test-writer/orchestrate", { method: "POST", body: JSON.stringify(payload) }),

  pipelineOrchestrateCancel: (project: string) =>
    http<{ project: string; cancelled: boolean }>(
      "/api/ai/test-writer/orchestrate-cancel",
      { method: "POST", body: JSON.stringify({ project }) },
    ),

  // ---- Fakemail bridge
  fakemailInfo: () =>
    http<{ provider: string; configured_via_env: Record<string, boolean>; memory_fallback: boolean }>(
      "/api/ai/fakemail/info",
    ),
  fakemailProvisionUsers: (roles: string[], domain?: string) =>
    http<{
      test_users: Array<{ role: string; email: string; password: string; inbox_url: string }>;
      provider: string;
    }>("/api/ai/fakemail/provision-users", {
      method: "POST", body: JSON.stringify({ roles, domain }),
    }),
  fakemailPeek: (to: string, opts?: { timeout_s?: number; subject_contains?: string }) =>
    http<{
      to: string; from: string; subject: string;
      text_body: string; html_body: string;
      links: string[]; otp: string | null;
      received_at: number; provider: string;
    }>(
      `/api/ai/fakemail/peek?to=${encodeURIComponent(to)}${
        opts?.timeout_s ? `&timeout_s=${opts.timeout_s}` : ""
      }${opts?.subject_contains ? `&subject_contains=${encodeURIComponent(opts.subject_contains)}` : ""}`,
    ),

  // ---- Performance runner
  perfRun: (payload: {
    mode?: "stdlib" | "locust";
    url?: string;
    method?: string;
    body?: Record<string, unknown>;
    headers?: Record<string, string>;
    concurrency?: number;
    duration_s?: number;
    project?: string;
    framework?: string;
    locustfile?: string;
    target?: string;
    users?: number;
    spawn_rate?: number;
  }) =>
    http<{
      mode: string; target: string; duration_s: number;
      requests_total: number; requests_per_sec: number;
      errors: number;
      p50_ms: number | null; p95_ms: number | null; p99_ms: number | null;
    }>("/api/ai/perf/run", { method: "POST", body: JSON.stringify(payload) }),

  // ---- manual bugs
  listManualBugs: (scope?: string) =>
    http<ManualBug[]>(scope ? `/api/manual-bugs?scope=${scope}` : "/api/manual-bugs"),
  getManualBug: (id: number) => http<ManualBug>(`/api/manual-bugs/${id}`),
  createManualBug: (data: Partial<ManualBug>) =>
    http<ManualBug>("/api/manual-bugs", { method: "POST", body: JSON.stringify(data) }),
  setManualBugStatus: (id: number, status: string) =>
    http<ManualBug>(`/api/manual-bugs/${id}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),
  assignManualBug: (id: number, assignee_id: number | null) =>
    http<ManualBug>(`/api/manual-bugs/${id}/assign`, {
      method: "POST",
      body: JSON.stringify({ assignee_id }),
    }),
  commentManualBug: (id: number, body: string) =>
    http<ManualBug>(`/api/manual-bugs/${id}/comments`, {
      method: "POST",
      body: JSON.stringify({ body }),
    }),

  // ---- cypress
  listCypressRuns: () => http<CypressRun[]>("/api/cypress/runs"),
  getCypressRun:   (id: string) => http<CypressRun>(`/api/cypress/runs/${id}`),
  triggerCypressRun: (specs?: string[]) =>
    http<CypressRun>("/api/cypress/runs", {
      method: "POST",
      body: JSON.stringify({ specs: specs && specs.length ? specs : null }),
    }),
  cypressScreenshotUrl: (relPath: string) =>
    `/api/cypress/screenshots/${relPath.replace(/^\/+/, "")}`,
};
