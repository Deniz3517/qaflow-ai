import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../AuthContext";
import type { ManualBug, ManualBugStatus, User } from "../types";
import { ManualStatusBadge } from "./Dashboard";

const STATUSES: ManualBugStatus[] = ["OPEN", "IN_PROGRESS", "RESOLVED", "REJECTED"];

export default function ManualBugDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const [bug, setBug] = useState<ManualBug | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [devs, setDevs] = useState<User[]>([]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const tick = () => api.getManualBug(Number(id)).then((b) => !cancelled && setBug(b)).catch((e) => setError(String(e)));
    tick();
    const i = setInterval(tick, 3000);
    return () => { cancelled = true; clearInterval(i); };
  }, [id]);

  useEffect(() => {
    if (user?.role === "project_manager" || user?.role === "manual_tester") {
      api.listUsers("developer").then(setDevs).catch(() => {});
    }
  }, [user]);

  if (!bug) return <div className="text-ink-400 text-sm">{error || "Loading…"}</div>;

  const canStatus = user?.role === "developer" || user?.role === "project_manager" || user?.id === bug.reporter_id;
  const canAssign = user?.role === "project_manager" || user?.id === bug.reporter_id;

  const setStatus = async (s: ManualBugStatus) => {
    setBusy("status");
    try {
      const updated = await api.setManualBugStatus(bug.id, s);
      setBug(updated);
    } finally { setBusy(null); }
  };
  const setAssignee = async (assigneeId: string) => {
    setBusy("assign");
    try {
      const updated = await api.assignManualBug(bug.id, assigneeId ? Number(assigneeId) : null);
      setBug(updated);
    } finally { setBusy(null); }
  };
  const submitComment = async () => {
    if (!comment.trim()) return;
    setBusy("comment");
    try {
      const updated = await api.commentManualBug(bug.id, comment.trim());
      setBug(updated);
      setComment("");
    } finally { setBusy(null); }
  };

  return (
    <div className="space-y-5">
      <div>
        <Link to="/" className="text-xs text-ink-400 hover:text-ink-200">← Back</Link>
        <h1 className="text-2xl font-semibold text-ink-100 mt-1">
          Manual Bug #{bug.id} — {bug.title}
        </h1>
        <div className="flex items-center gap-3 mt-2 text-xs flex-wrap">
          <ManualStatusBadge s={bug.status} />
          <span className="text-ink-500 font-mono">severity: <span className="text-ink-200 uppercase">{bug.severity}</span></span>
          <span className="text-ink-500 font-mono">reporter: <span className="text-ink-200">@{bug.reporter_username}</span></span>
          <span className="text-ink-500 font-mono">
            assignee: <span className="text-ink-200">{bug.assignee_username ? `@${bug.assignee_username}` : "unassigned"}</span>
          </span>
        </div>
      </div>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="md:col-span-2 space-y-4">
          <div className="rounded-lg border border-ink-700 bg-ink-800/40 p-5">
            <h3 className="text-xs uppercase tracking-wider text-ink-400 mb-2">Description</h3>
            <p className="text-sm text-ink-200 whitespace-pre-wrap">{bug.description}</p>
          </div>
          {bug.steps_to_reproduce && (
            <div className="rounded-lg border border-ink-700 bg-ink-800/40 p-5">
              <h3 className="text-xs uppercase tracking-wider text-ink-400 mb-2">Steps to reproduce</h3>
              <pre className="text-sm text-ink-200 font-mono whitespace-pre-wrap">{bug.steps_to_reproduce}</pre>
            </div>
          )}
          {bug.page_url && (
            <div className="rounded-lg border border-ink-700 bg-ink-800/40 p-5">
              <h3 className="text-xs uppercase tracking-wider text-ink-400 mb-2">Affected page</h3>
              <a href={bug.page_url} target="_blank" rel="noreferrer" className="text-cyan-400 text-sm font-mono hover:underline">
                {bug.page_url} ↗
              </a>
            </div>
          )}

          <div className="rounded-lg border border-ink-700 bg-ink-800/40 p-5">
            <h3 className="text-xs uppercase tracking-wider text-ink-400 mb-3">Comments</h3>
            <div className="space-y-3">
              {(bug.comments || []).length === 0 && (
                <p className="text-xs text-ink-500">No comments yet.</p>
              )}
              {(bug.comments || []).map((c) => (
                <div key={c.id} className="border-l-2 border-ink-700 pl-3">
                  <div className="text-xs text-ink-400">
                    <span className="text-ink-200 font-medium">{c.author_name}</span>
                    <span className="text-ink-500 font-mono ml-2">@{c.author_username}</span>
                    <span className="text-ink-600 font-mono ml-2">{c.created_at}</span>
                  </div>
                  <p className="text-sm text-ink-100 mt-1 whitespace-pre-wrap">{c.body}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 flex gap-2">
              <input
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="Add a comment…"
                className="flex-1 bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-sm text-ink-100 outline-none focus:border-brand"
                onKeyDown={(e) => { if (e.key === "Enter") submitComment(); }}
              />
              <button
                onClick={submitComment}
                disabled={!comment.trim() || busy !== null}
                className="px-4 py-2 rounded-md bg-emerald-500 hover:bg-emerald-400 text-ink-950 text-sm font-semibold disabled:bg-ink-700/50 disabled:text-ink-500 disabled:cursor-not-allowed"
              >
                Post
              </button>
            </div>
          </div>
        </div>

        <aside className="space-y-4">
          {canStatus && (
            <div className="rounded-lg border border-ink-700 bg-ink-800/40 p-4">
              <h3 className="text-xs uppercase tracking-wider text-ink-400 mb-2">Update status</h3>
              <div className="grid grid-cols-2 gap-2">
                {STATUSES.map((s) => (
                  <button
                    key={s}
                    onClick={() => setStatus(s)}
                    disabled={busy === "status" || bug.status === s}
                    className={`text-xs uppercase font-mono py-1.5 rounded border transition ${
                      bug.status === s
                        ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10"
                        : "border-ink-700 text-ink-300 hover:border-brand hover:text-brand"
                    }`}
                  >
                    {s.replace("_", " ")}
                  </button>
                ))}
              </div>
            </div>
          )}

          {canAssign && (
            <div className="rounded-lg border border-ink-700 bg-ink-800/40 p-4">
              <h3 className="text-xs uppercase tracking-wider text-ink-400 mb-2">Assign to developer</h3>
              <select
                value={bug.assignee_id || ""}
                onChange={(e) => setAssignee(e.target.value)}
                disabled={busy === "assign"}
                className="w-full bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-sm text-ink-100 outline-none focus:border-brand"
              >
                <option value="">— unassigned —</option>
                {devs.map((d) => (
                  <option key={d.id} value={d.id}>{d.full_name} (@{d.username})</option>
                ))}
              </select>
            </div>
          )}

          <div className="rounded-lg border border-ink-700 bg-ink-800/40 p-4 text-xs space-y-1.5 text-ink-400">
            <div>Created <span className="text-ink-300 font-mono">{bug.created_at}</span></div>
            <div>Updated <span className="text-ink-300 font-mono">{bug.updated_at}</span></div>
          </div>
        </aside>
      </section>
    </div>
  );
}
