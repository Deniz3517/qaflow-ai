import { useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../AuthContext";
import type { Bug } from "../types";
import StatusBadge from "../components/StatusBadge";

function DiffView({ diff }: { diff: string }) {
  if (!diff) return null;
  return (
    <pre className="text-xs font-mono leading-relaxed bg-ink-950/60 border border-ink-700 rounded p-4 overflow-x-auto">
      {diff.split("\n").map((line, i) => {
        let cls = "text-ink-300";
        if (line.startsWith("+++") || line.startsWith("---")) cls = "text-ink-500";
        else if (line.startsWith("@@")) cls = "text-cyan-400";
        else if (line.startsWith("+")) cls = "text-emerald-400 bg-emerald-500/5";
        else if (line.startsWith("-")) cls = "text-rose-400 bg-rose-500/5";
        else if (line.startsWith("diff ")) cls = "text-violet-300";
        return <div key={i} className={cls}>{line || " "}</div>;
      })}
    </pre>
  );
}

export default function BugDetail() {
  const { uid } = useParams();
  const nav = useNavigate();
  const { user } = useAuth();
  const [bug, setBug] = useState<Bug | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!uid) return;
    let cancelled = false;
    const tick = () => api.getBug(uid).then((b) => !cancelled && setBug(b)).catch(() => {});
    tick();
    const id = setInterval(tick, 1500);
    return () => { cancelled = true; clearInterval(id); };
  }, [uid]);

  if (!bug) {
    return <div className="text-ink-400 text-sm">Loading bug…</div>;
  }

  const onApprove = async () => {
    setBusy("approve");
    setError(null);
    try {
      const updated = await api.approve(bug.uid);
      setBug(updated);
      setTimeout(() => nav("/"), 1200);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "approve failed");
    } finally {
      setBusy(null);
    }
  };

  const onReject = async () => {
    setBusy("reject");
    setError(null);
    try {
      const updated = await api.reject(bug.uid);
      setBug(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "reject failed");
    } finally {
      setBusy(null);
    }
  };

  const onAutoFix = async () => {
    setBusy("autofix");
    setError(null);
    try {
      await api.autoFix(bug.uid);
      // status will move via WS / polling; nothing to do here
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "auto-fix failed");
    } finally {
      setBusy(null);
    }
  };

  const target = bug.fix?.target_repo ?? "buggy-app";
  const requiredRole: "automation_engineer" | "developer" =
    target === "cypress-tests" ? "automation_engineer" : "developer";
  const roleMatches = user?.role === requiredRole;
  const canDecide = bug.status === "FIX_READY" && roleMatches;
  const canAutoFix = bug.status === "DETECTED" && bug.source === "cypress";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <Link to="/" className="text-xs text-ink-400 hover:text-ink-200">← Back to dashboard</Link>
          <h1 className="text-2xl font-semibold text-ink-100 mt-1">
            Bug #{bug.id} — {bug.title}
          </h1>
          <div className="flex items-center gap-3 mt-2 text-xs">
            <StatusBadge status={bug.status} />
            <span className="text-ink-500 font-mono">branch: <span className="text-cyan-400">{bug.branch}</span></span>
            {bug.fix && (
              <span className="text-ink-500 font-mono">
                confidence: <span className="text-violet-300">{bug.fix.confidence}%</span>
              </span>
            )}
            {bug.fix && (
              <span className="text-ink-500 font-mono">
                file: <span className="text-ink-300">{bug.fix.file}</span>
              </span>
            )}
          </div>
        </div>
      </div>

      <section className="rounded-lg border border-ink-700 bg-ink-800/40 p-5">
        <h3 className="text-xs uppercase tracking-wider text-ink-400 mb-2">Test Evidence</h3>
        <p className="text-sm text-ink-200 font-mono">{bug.evidence}</p>
        {bug.source === "cypress" && (
          <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs font-mono">
            {bug.spec && (
              <div className="text-ink-400">
                spec: <span className="text-cyan-400">{bug.spec}</span>
              </div>
            )}
            {bug.test_name && (
              <div className="text-ink-400">
                test: <span className="text-ink-200">{bug.test_name}</span>
              </div>
            )}
          </div>
        )}
        {bug.verification && (
          <div className="mt-3 text-xs font-mono">
            verification:{" "}
            <span className={bug.verification.passed ? "text-emerald-400" : "text-rose-400"}>
              {bug.verification.passed ? "✓ test passes after fix" : "✗ test still fails"}
            </span>{" "}
            <span className="text-ink-500">
              ({bug.verification.duration_s}s)
            </span>
          </div>
        )}
      </section>

      {bug.fix && (
        <section className="rounded-lg border border-violet-500/30 bg-violet-500/5 p-5">
          <h3 className="text-xs uppercase tracking-wider text-violet-300 mb-2">
            AI Analysis · mode={bug.fix.mode}
          </h3>
          <p className="text-sm text-ink-200 leading-relaxed">{bug.fix.analysis}</p>
          {bug.fix.fallback_reason && (
            <p className="text-xs text-amber-400 mt-2 font-mono">⚠ {bug.fix.fallback_reason}</p>
          )}
        </section>
      )}

      <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="rounded-lg border-2 border-rose-500/30 bg-ink-800/30 overflow-hidden">
          <div className="px-4 py-2 bg-rose-500/10 text-rose-300 text-xs font-semibold uppercase tracking-wider">
            BEFORE
          </div>
          {bug.before_screenshot ? (
            <img src={api.screenshotUrl(bug.before_screenshot)} alt="before" className="w-full block" />
          ) : (
            <div className="p-12 text-center text-ink-500 text-sm">No screenshot</div>
          )}
        </div>
        <div className="rounded-lg border-2 border-emerald-500/30 bg-ink-800/30 overflow-hidden">
          <div className="px-4 py-2 bg-emerald-500/10 text-emerald-300 text-xs font-semibold uppercase tracking-wider">
            AFTER (AI Fix)
          </div>
          {bug.after_screenshot ? (
            <img src={api.screenshotUrl(bug.after_screenshot)} alt="after" className="w-full block" />
          ) : (
            <div className="p-12 text-center text-ink-500 text-sm">{bug.error || "No screenshot yet"}</div>
          )}
        </div>
      </section>

      {bug.diff && (
        <section>
          <h3 className="text-xs uppercase tracking-wider text-ink-400 mb-2">Code Diff</h3>
          <DiffView diff={bug.diff} />
        </section>
      )}

      {error && (
        <div className="rounded border border-rose-500/30 bg-rose-500/5 text-rose-300 px-4 py-2 text-sm">
          {error}
        </div>
      )}

      {bug.status === "FIX_READY" && !roleMatches && (
        <div className="rounded border border-amber-500/30 bg-amber-500/5 text-amber-300 px-4 py-2 text-sm">
          ⚠ Only <span className="font-mono">{requiredRole.replace("_", " ")}</span>s can approve fixes that touch <span className="font-mono">{target}</span>.
          {requiredRole === "automation_engineer"
            ? " Log in as auto1 to approve this test fix."
            : " Log in as dev1 to approve this app fix."}
        </div>
      )}

      <section className="flex flex-wrap gap-3 pt-2">
        {canAutoFix && (
          <button
            onClick={onAutoFix}
            disabled={busy !== null}
            className={`px-5 py-2.5 rounded font-semibold text-sm transition ${
              busy === null
                ? "bg-violet-500 hover:bg-violet-400 text-ink-950"
                : "bg-ink-700/50 text-ink-500 cursor-not-allowed"
            }`}
          >
            {busy === "autofix" ? "Analyzing…" : "🤖 AUTO-FIX (AI)"}
          </button>
        )}
        <button
          onClick={onApprove}
          disabled={!canDecide || busy !== null}
          className={`px-5 py-2.5 rounded font-semibold text-sm transition ${
            canDecide && busy !== "approve"
              ? "bg-emerald-500 hover:bg-emerald-400 text-ink-950"
              : "bg-ink-700/50 text-ink-500 cursor-not-allowed"
          }`}
        >
          {busy === "approve" ? "Merging…" : "✓ APPROVE & MERGE"}
        </button>
        <button
          onClick={onReject}
          disabled={!canDecide || busy !== null}
          className={`px-5 py-2.5 rounded font-semibold text-sm border transition ${
            canDecide && busy !== "reject"
              ? "border-rose-500/50 text-rose-300 hover:bg-rose-500/10"
              : "border-ink-700 text-ink-500 cursor-not-allowed"
          }`}
        >
          ✕ REJECT
        </button>
        <a
          href="http://localhost:3001"
          target="_blank"
          rel="noreferrer"
          className="px-5 py-2.5 rounded font-semibold text-sm border border-cyan-500/50 text-cyan-300 hover:bg-cyan-500/10"
        >
          ▣ OPEN BUGGY-APP
        </a>
      </section>
    </div>
  );
}
