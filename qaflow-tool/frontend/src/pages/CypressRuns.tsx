import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { CypressRun } from "../types";

export default function CypressRuns() {
  const [runs, setRuns] = useState<CypressRun[]>([]);

  useEffect(() => {
    let cancelled = false;
    const tick = () => api.listCypressRuns().then((r) => !cancelled && setRuns(r)).catch(() => {});
    tick();
    const id = setInterval(tick, 2500);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold text-ink-100">Cypress test runs</h1>
        <p className="text-sm text-ink-400 mt-1">All UI test executions against the SportHub site.</p>
      </header>

      <div className="rounded-lg border border-ink-700 bg-ink-800/40 overflow-hidden">
        {runs.length === 0 ? (
          <div className="p-12 text-center text-ink-400 text-sm">No runs yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-ink-900/40 text-xs uppercase tracking-wider text-ink-400">
              <tr>
                <th className="text-left px-5 py-2 font-medium">Run</th>
                <th className="text-left px-5 py-2 font-medium">Status</th>
                <th className="text-left px-5 py-2 font-medium">Started</th>
                <th className="text-left px-5 py-2 font-medium">Duration</th>
                <th className="text-left px-5 py-2 font-medium">Result</th>
                <th className="text-right px-5 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-t border-ink-700/60 hover:bg-ink-700/20">
                  <td className="px-5 py-2.5 font-mono text-cyan-400">{r.id}</td>
                  <td className="px-5 py-2.5 text-xs font-mono">{r.status}</td>
                  <td className="px-5 py-2.5 text-xs font-mono text-ink-500">{r.started_at}</td>
                  <td className="px-5 py-2.5 text-xs font-mono text-ink-300">{r.duration_s ? `${r.duration_s}s` : "—"}</td>
                  <td className="px-5 py-2.5 font-mono text-xs">
                    <span className="text-emerald-400">{r.passed}</span>
                    <span className="text-ink-500">/{r.total}</span>
                    {r.failed ? <span className="text-rose-400 ml-2">{r.failed} failed</span> : null}
                  </td>
                  <td className="px-5 py-2.5 text-right">
                    <Link to={`/cypress-runs/${r.id}`} className="text-emerald-400 hover:text-emerald-300 text-xs font-semibold">Open →</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
