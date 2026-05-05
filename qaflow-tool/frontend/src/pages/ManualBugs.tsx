import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../AuthContext";
import type { ManualBug } from "../types";
import { ManualStatusBadge } from "./Dashboard";

interface Props {
  scope?: "mine" | undefined;  // when undefined, server applies role default
  title?: string;
  subtitle?: string;
}

export default function ManualBugs({ scope, title, subtitle }: Props = {}) {
  const { user } = useAuth();
  const [bugs, setBugs] = useState<ManualBug[]>([]);

  useEffect(() => {
    let cancelled = false;
    const tick = () => api.listManualBugs(scope).then((b) => !cancelled && setBugs(b)).catch(() => {});
    tick();
    const id = setInterval(tick, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, [scope]);

  const heading = title || (
    user?.role === "developer" ? "Tickets assigned to me" :
    user?.role === "manual_tester" ? "My reports" :
    "Manual bug reports"
  );

  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-ink-100">{heading}</h1>
          {subtitle && <p className="text-sm text-ink-400 mt-1">{subtitle}</p>}
        </div>
        {user?.role === "manual_tester" && (
          <Link
            to="/report-bug"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-semibold bg-rose-500 hover:bg-rose-400 text-ink-950"
          >
            + Report Bug
          </Link>
        )}
      </header>

      <div className="rounded-lg border border-ink-700 bg-ink-800/40 overflow-hidden">
        {bugs.length === 0 ? (
          <div className="p-12 text-center text-ink-400 text-sm">No bugs in this view yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-ink-900/40 text-xs uppercase tracking-wider text-ink-400">
              <tr>
                <th className="text-left px-5 py-2 font-medium">ID</th>
                <th className="text-left px-5 py-2 font-medium">Title</th>
                <th className="text-left px-5 py-2 font-medium">Severity</th>
                <th className="text-left px-5 py-2 font-medium">Status</th>
                <th className="text-left px-5 py-2 font-medium">Reporter</th>
                <th className="text-left px-5 py-2 font-medium">Assignee</th>
                <th className="text-left px-5 py-2 font-medium">Updated</th>
                <th className="text-right px-5 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {bugs.map((b) => (
                <tr key={b.id} className="border-t border-ink-700/60 hover:bg-ink-700/20">
                  <td className="px-5 py-2.5 font-mono text-ink-400">#{b.id}</td>
                  <td className="px-5 py-2.5 text-ink-100">{b.title}</td>
                  <td className="px-5 py-2.5"><Sev s={b.severity} /></td>
                  <td className="px-5 py-2.5"><ManualStatusBadge s={b.status} /></td>
                  <td className="px-5 py-2.5 text-ink-400 font-mono text-xs">@{b.reporter_username}</td>
                  <td className="px-5 py-2.5 text-ink-400 font-mono text-xs">
                    {b.assignee_username ? `@${b.assignee_username}` : <span className="text-ink-600">unassigned</span>}
                  </td>
                  <td className="px-5 py-2.5 text-ink-500 font-mono text-[11px]">{b.updated_at}</td>
                  <td className="px-5 py-2.5 text-right">
                    <Link to={`/manual-bugs/${b.id}`} className="text-emerald-400 hover:text-emerald-300 text-xs font-semibold">Open →</Link>
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

function Sev({ s }: { s: string }) {
  const map: Record<string, string> = {
    low:      "bg-cyan-500/10 text-cyan-300 border-cyan-500/30",
    medium:   "bg-amber-500/10 text-amber-300 border-amber-500/30",
    high:     "bg-rose-500/10 text-rose-300 border-rose-500/30",
    critical: "bg-rose-700/15 text-rose-200 border-rose-700/40",
  };
  return <span className={`px-2 py-0.5 rounded font-mono text-[10px] uppercase border ${map[s]}`}>{s}</span>;
}
