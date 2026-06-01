export type BugStatus =
  | "DETECTED"
  | "AI_ANALYZING"
  | "SANDBOX_APPLYING"
  | "VERIFYING_FIX"
  | "FIX_READY"
  | "FIX_FAILED"
  | "MERGED"
  | "REJECTED";

export interface Fix {
  mode: "mock" | "claude" | "rule-based";
  bug_id: number;
  title: string;
  type: string;
  severity: string;
  file: string;
  target_repo?: "buggy-app" | "cypress-tests";
  analysis: string;
  old: string;
  new: string;
  confidence: number;
  fallback_reason?: string;
}

export interface Bug {
  uid: string;
  id: number;
  title: string;
  type: string;
  evidence: string;
  run_id: string;
  status: BugStatus;
  created_at: string;
  branch: string;
  fix?: Fix;
  before_screenshot?: string;
  after_screenshot?: string;
  diff?: string;
  error?: string;
  merged_at?: string;
  rejected_at?: string;
  merge_skipped?: boolean;
  source?: "ai" | "cypress";
  spec?: string;
  test_name?: string;
  failure_screenshot?: string | null;
  verification?: { passed: boolean; duration_s?: number };
}

export type RunStatus =
  | "QUEUED"
  | "RUNNING_TESTS"
  | "COMPLETED"
  | "FAILED";

export interface Run {
  id: string;
  suite: string;
  status: RunStatus;
  started_at: string;
  finished_at?: string;
  bug_uids: string[];
  before_screenshot?: string;
  passed?: number;
  failed?: number;
  total?: number;
  error?: string;
}

export interface DashboardSummary {
  active_bugs: number;
  auto_fixed: number;
  pending: number;
  rejected: number;
  pass_rate: number;
  ai_mode: "mock" | "claude";
  last_run: Run | null;
  last_cypress_run?: CypressRun | null;
  app_approvals_pending?: number;
  test_approvals_pending?: number;
  manual_open: number;
  manual_assigned_to_me: number;
  manual_reported_by_me: number;
}

export type Role = "developer" | "automation_engineer" | "project_manager" | "manual_tester";

export interface User {
  id: number;
  username: string;
  role: Role;
  full_name: string;
}

export type ManualBugStatus = "OPEN" | "IN_PROGRESS" | "RESOLVED" | "REJECTED";
export type Severity = "low" | "medium" | "high" | "critical";

export interface ManualBugComment {
  id: number;
  body: string;
  created_at: string;
  author_username: string;
  author_name: string;
}

export interface ManualBug {
  id: number;
  title: string;
  description: string;
  severity: Severity;
  page_url?: string;
  steps_to_reproduce?: string;
  reporter_id: number;
  reporter_username: string;
  reporter_name: string;
  assignee_id?: number;
  assignee_username?: string;
  assignee_name?: string;
  status: ManualBugStatus;
  created_at: string;
  updated_at: string;
  comments?: ManualBugComment[];
}

// ---------------------------------------------------------------------------
// AI Test Cover Engine
// ---------------------------------------------------------------------------

export interface TestCoverScan {
  url: string;
  title: string;
  counts: {
    headings: number;
    buttons: number;
    links: number;
    inputs: number;
    forms: number;
    images: number;
    landmarks: number;
  };
  headings: { tag: string; text: string }[];
  buttons: { id: string | null; text: string; type?: string | null }[];
  links: { href: string; text: string }[];
  inputs: { tag: string; id: string | null; type?: string | null; placeholder?: string | null }[];
  forms: { id: string | null; method: string; input_ids: string[] }[];
  landmarks: { landmark: string; id: string | null }[];
}

export type TestFramework = "cypress" | "playwright" | "selenium" | "robot";
export type TestLanguage = "javascript" | "typescript" | "python";
export type TestMode = "black-box" | "gray-box" | "white-box";

export interface TestCoverGenerateRequest {
  url: string;
  framework: TestFramework;
  language: TestLanguage;
  mode: TestMode;
  test_focus: string[];
  source_paste?: string;
  source_repo_url?: string;
  extra_instructions?: string;
  scan?: TestCoverScan;
}

export interface TestCoverGenerateResult {
  files: Record<string, string>;
  engine: "claude" | "mock";
  framework: TestFramework;
  language: TestLanguage;
  fallback_reason?: string;
  scan_summary?: TestCoverScan["counts"];
  url: string;
}

export interface SavedBundle {
  bundle_root: string;
  bundle_relative: string;
  framework: string;
  framework_name: string;
  project: string;
  env: string | null;
  files: { path: string; abs_path: string; size_bytes: number }[];
  saved_by: string;
  saved_at: string;
  baseline_path?: string;
}

export interface CoverageEstimate {
  items: { category: string; name: string; covered: boolean; note?: string }[];
  total: number;
  covered_estimate: number;
  ratio: number;
}

export interface QualityScore {
  score: number;
  grade: "A" | "B" | "C" | "D" | "F";
  tests_estimate: number;
  files: number;
  notes: { level: "ok" | "warn"; msg: string }[];
}

export interface RegenDiff {
  exists: boolean;
  bundle_root?: string;
  items: {
    path: string;
    status: "added" | "removed" | "modified" | "unchanged";
    old_bytes: number;
    new_bytes: number;
    diff: string;
  }[];
}

export interface RunResult {
  framework_id: string;
  bundle: string;
  cmd: string;
  exit_code: number;
  duration_s: number;
  timed_out: boolean;
  passed: number | null;
  failed: number | null;
  log_tail: string;
}

export interface AuthConfig {
  kind: "form";
  login_url: string;
  username_field: string;
  password_field: string;
  submit: string;
  credentials: { username: string; password: string };
}

export interface CrawlResult {
  pages: TestCoverScan[];
  count: number;
}

export interface TestCoverProject {
  name: string;
  slug: string;
  spec_count: number;
  direct_specs?: number;
  envs: { slug: string; spec_count: number }[];
}

export interface TestFrameworkInfo {
  id: string;
  name: string;
  language: TestLanguage;
  extension: string;
  workspace: string;
  description: string;
  installed: boolean;
  version: string | null;
  install_status: "pending" | "running" | "succeeded" | "failed" | null;
  install_error: string | null;
}

export interface InstallStatus {
  status: "not-started" | "pending" | "running" | "succeeded" | "failed";
  started_at?: number;
  finished_at?: number | null;
  error?: string | null;
  log: string[];
  duration_s?: number;
}

// ---------------------------------------------------------------------------
// AI Test Cover v2 — multi-step pipeline
// ---------------------------------------------------------------------------

export type PipelineState =
  | "INIT" | "DISCOVERY"
  | "SMOKE_GEN" | "SMOKE_RUN" | "SMOKE_GATE"
  | "E2E_GEN"   | "E2E_RUN"   | "E2E_GATE"
  | "NEGATIVE_GEN" | "NEGATIVE_RUN"
  | "API_DISCOVERY" | "VALIDATION"
  | "DONE" | "BLOCKED" | "EXTEND";

export interface PipelineHistoryEntry {
  step: PipelineState;
  success: boolean | null;
  started_at: number;
  finished_at: number | null;
  summary?: string;
  pass_rate_pct?: number | null;
  files_generated?: number | null;
  error?: string | null;
  artifacts?: Record<string, unknown>;
}

export interface PipelineSnapshot {
  project_slug: string;
  current_state: PipelineState;
  blocked_reason: string | null;
  is_terminal: boolean;
  history: PipelineHistoryEntry[];
  thresholds: { smoke_gate_pct: number; e2e_gate_pct: number };
}

export interface PipelineStepResult {
  project: string;
  framework: string;
  saved: SavedBundle;
  summary?: string;
  files: string[];
  expected_pass_rate_pct?: number;
  fragility_notes?: string[];
  orchestrator: PipelineSnapshot;
}

export interface PipelineRunResult {
  project: string;
  framework: string;
  step: PipelineState;
  passed: number;
  failed: number;
  total: number;
  pass_rate_pct: number;
  duration_s?: number;
  screenshots: string[];
  tests: Array<{ name: string; status: "pass" | "fail" | "pending"; duration_ms?: number | null }>;
  log_tail?: string;
  orchestrator: PipelineSnapshot;
}

export type CypressTestStatus = "pass" | "fail" | "pending";
export interface CypressTest {
  spec: string | null;
  name: string;
  status: CypressTestStatus;
  duration_ms?: number;
}
export type CypressRunStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
export interface CypressRun {
  id: string;
  specs: string[] | "all";
  status: CypressRunStatus;
  started_at: string;
  finished_at?: string;
  triggered_by?: string;
  passed: number;
  failed: number;
  pending: number;
  total: number;
  tests: CypressTest[];
  screenshots: string[];
  log_tail?: string[];
  log?: string;
  duration_s?: number;
  exit_code?: number;
  error?: string;
}
