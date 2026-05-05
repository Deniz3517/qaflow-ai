import { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api";
import type { CypressRun } from "../types";

function StatusPill({ status }: { status: CypressRun["status"] }) {
  const map: Record<string, string> = {
    QUEUED:    "bg-amber-500/10  text-amber-300  border-amber-500/30",
    RUNNING:   "bg-cyan-500/10   text-cyan-300   border-cyan-500/30 animate-pulse",
    COMPLETED: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
    FAILED:    "bg-rose-500/10   text-rose-300   border-rose-500/30",
  };
  return (
    <span className={`px-2 py-0.5 rounded font-mono text-[10px] uppercase border ${map[status]}`}>
      {status}
    </span>
  );
}

function TestRow({ status, name, duration, spec }: { status: string; name: string; duration?: number; spec?: string | null }) {
  const map: Record<string, { icon: string; cls: string }> = {
    pass:    { icon: "✓", cls: "text-emerald-400" },
    fail:    { icon: "✗", cls: "text-rose-400" },
    pending: { icon: "○", cls: "text-ink-500" },
  };
  const m = map[status] || { icon: "?", cls: "text-ink-300" };
  return (
    <div className="flex items-center gap-3 px-4 py-2 border-t border-ink-700/60 hover:bg-ink-700/15">
      <span className={`font-mono text-base ${m.cls}`}>{m.icon}</span>
      <div className="flex-1 min-w-0">
        <div className="text-sm text-ink-100 truncate">{name}</div>
        {spec && <div className="text-[11px] font-mono text-ink-500">{spec}</div>}
      </div>
      {duration !== undefined && (
        <div className="text-[11px] font-mono text-ink-500">{duration} ms</div>
      )}
    </div>
  );
}

export default function CypressRunDetail() {
  const { id } = useParams();
  const [run, setRun] = useState<CypressRun | null>(null);
  const [liveLines, setLiveLines] = useState<string[]>([]);
  const liveRef = useRef<HTMLDivElement | null>(null);

  // Poll while running, then once after completion
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const tick = () => api.getCypressRun(id).then((r) => {
      if (cancelled) return;
      setRun(r);
    }).catch(() => {});
    tick();
    const interval = setInterval(() => {
      if (run?.status === "COMPLETED" || run?.status === "FAILED") return;
      tick();
    }, 1500);
    return () => { cancelled = true; clearInterval(interval); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, run?.status]);

  // Subscribe to live lines via WebSocket
  useEffect(() => {
    if (!id) return;
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.addEventListener("message", (e) => {
      try {
        const evt = JSON.parse(e.data);
        if (evt.type === "cypress.line" && evt.run_id === id) {
          setLiveLines((prev) => [...prev.slice(-300), evt.line]);
        }
      } catch { /* ignore */ }
    });
    return () => ws.close();
  }, [id]);

  useEffect(() => {
    if (liveRef.current) liveRef.current.scrollTop = liveRef.current.scrollHeight;
  }, [liveLines]);

  if (!run) return <div className="text-ink-400 text-sm">Loading run…</div>;

  const failedTests = run.tests.filter((t) => t.status === "fail");
  const passedTests = run.tests.filter((t) => t.status === "pass");

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <Link to="/test-runner" className="text-xs text-ink-400 hover:text-ink-200">← Back to Test Runner</Link>
          <h1 className="text-2xl font-semibold text-ink-100 mt-1">
            Cypress run <span className="font-mono text-cyan-400">{run.id}</span>
          </h1>
          <div className="flex items-center gap-3 mt-2 text-xs">
            <StatusPill status={run.status} />
            <span className="text-ink-500 font-mono">started: <span className="text-ink-300">{run.started_at}</span></span>
            {run.finished_at && <span className="text-ink-500 font-mono">finished: <span className="text-ink-300">{run.finished_at}</span></span>}
            {run.duration_s !== undefined && <span className="text-ink-500 font-mono">{run.duration_s}s</span>}
            {run.triggered_by && <span className="text-ink-500 font-mono">by @{run.triggered_by}</span>}
          </div>
        </div>
      </header>

      <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-4 text-center">
          <div className="text-3xl font-bold text-emerald-300">{run.passed}</div>
          <div className="text-xs uppercase tracking-wider text-ink-400 mt-1">Passed</div>
        </div>
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-4 text-center">
          <div className="text-3xl font-bold text-rose-300">{run.failed}</div>
          <div className="text-xs uppercase tracking-wider text-ink-400 mt-1">Failed</div>
        </div>
        <div className="rounded-lg border border-ink-500/30 bg-ink-500/5 p-4 text-center">
          <div className="text-3xl font-bold text-ink-300">{run.pending}</div>
          <div className="text-xs uppercase tracking-wider text-ink-400 mt-1">Pending</div>
        </div>
        <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-4 text-center">
          <div className="text-3xl font-bold text-cyan-300">{run.total}</div>
          <div className="text-xs uppercase tracking-wider text-ink-400 mt-1">Total</div>
        </div>
      </section>

      {run.error && (
        <div className="rounded border border-rose-500/30 bg-rose-500/5 text-rose-300 px-4 py-3 text-sm font-mono">
          {run.error}
        </div>
      )}

      {failedTests.length > 0 && (
        <section className="rounded-lg border border-rose-500/30 bg-ink-800/40 overflow-hidden">
          <div className="px-4 py-2 bg-rose-500/10 text-rose-300 text-xs font-semibold uppercase tracking-wider">
            Failed tests ({failedTests.length})
          </div>
          <div>
            {failedTests.map((t, i) => (
              <TestRow key={i} status={t.status} name={t.name} duration={t.duration_ms} spec={t.spec} />
            ))}
          </div>
        </section>
      )}

      {passedTests.length > 0 && (
        <section className="rounded-lg border border-emerald-500/30 bg-ink-800/40 overflow-hidden">
          <div className="px-4 py-2 bg-emerald-500/10 text-emerald-300 text-xs font-semibold uppercase tracking-wider">
            Passed tests ({passedTests.length})
          </div>
          <div className="max-h-96 overflow-y-auto">
            {passedTests.map((t, i) => (
              <TestRow key={i} status={t.status} name={t.name} duration={t.duration_ms} spec={t.spec} />
            ))}
          </div>
        </section>
      )}

      {run.screenshots && run.screenshots.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-ink-200 mb-2">Failure screenshots</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {run.screenshots.map((s) => (
              <a key={s} href={api.cypressScreenshotUrl(s)} target="_blank" rel="noreferrer"
                 className="block rounded border border-ink-700 hover:border-brand overflow-hidden">
                <img src={api.cypressScreenshotUrl(s)} alt={s} className="w-full block" />
                <div className="px-2 py-1 text-[10px] font-mono text-ink-500 bg-ink-900/40 truncate">{s}</div>
              </a>
            ))}
          </div>
        </section>
      )}

      <section>
        <h3 className="text-sm font-semibold text-ink-200 mb-2">Live log</h3>
        <div ref={liveRef}
             className="rounded-lg border border-ink-700 bg-ink-950 max-h-72 overflow-y-auto font-mono text-[11px] p-3 leading-relaxed">
          {(liveLines.length === 0 && (run.log_tail || []).length === 0) && (
            <div className="text-ink-500">No log output yet…</div>
          )}
          {(liveLines.length > 0 ? liveLines : (run.log_tail || [])).map((l, i) => {
            let cls = "text-ink-300";
            if (/✓|passing/.test(l)) cls = "text-emerald-400";
            else if (/✗|failing|FAIL|Error/.test(l)) cls = "text-rose-400";
            else if (/Running|Spec/.test(l)) cls = "text-cyan-400";
            return <div key={i} className={cls}>{l || " "}</div>;
          })}
        </div>
      </section>

      {run.log && (
        <details className="rounded-lg border border-ink-700 bg-ink-800/40">
          <summary className="px-4 py-2 cursor-pointer text-sm text-ink-300 hover:text-ink-100">
            Full log ({Math.round(run.log.length / 1024)} KB)
          </summary>
          <pre className="px-4 py-3 font-mono text-[11px] text-ink-300 overflow-x-auto max-h-96 overflow-y-auto">
            {run.log}
          </pre>
        </details>
      )}
    </div>
  );
}
