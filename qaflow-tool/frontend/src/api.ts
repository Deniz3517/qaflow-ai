import type { Bug, CypressRun, DashboardSummary, ManualBug, Run, User } from "./types";

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
  screenshotUrl: (name: string) => `/api/screenshots/${name}`,

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
