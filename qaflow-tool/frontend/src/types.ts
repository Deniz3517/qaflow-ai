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
