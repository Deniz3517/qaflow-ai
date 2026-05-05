import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { useAuth } from "../AuthContext";
import { useLiveData } from "../useLiveData";
import { api } from "../api";
import StatusBadge from "../components/StatusBadge";
import RunTestsButton from "../components/RunTestsButton";
import type { ManualBug } from "../types";

function Kpi({ value, label, color, to }: { value: string | number; label: string; color: string; to?: string }) {
  const inner = (
    <div className={`rounded-lg border p-4 ${color}`}>
      <div className="text-3xl font-bold tracking-tight">{value}</div>
      <div className="text-xs uppercase tracking-wider text-ink-400 mt-1">{label}</div>
    </div>
  );
  return to ? <Link to={to}>{inner}</Link> : inner;
}

export default function Dashboard() {
  const { user } = useAuth();
  const { summary, bugs } = useLiveData();
  const [manualBugs, setManualBugs] = useState<ManualBug[]>([]);

  useEffect(() => {
    api.listManualBugs().then(setManualBugs).catch(() => {});
    const id = setInterval(() => {
      api.listManualBugs().then(setManualBugs).catch(() => {});
    }, 5000);
    return () => clearInterval(id);
  }, []);

  const aiBugs = Object.values(bugs).sort(
    (a, b) => +new Date(b.created_at) - +new Date(a.created_at),
  );

  const role = user?.role;

  const roleHeading: Record<string, { title: string; sub: string; cta?: React.ReactNode }> = {
    project_manager: {
      title: "Project Dashboard",
      sub: "Real-time bug status, sprint KPIs and one-click test launch.",
      cta: <RunTestsButton />,
    },
    developer: {
      title: "Developer Dashboard",
      sub: "AI-generated fixes awaiting your review and tickets assigned to you.",
    },
    automation_engineer: {
      title: "Automation Engineer Dashboard",
      sub: "Test runs, AI engine health and coverage metrics.",
      cta: <RunTestsButton />,
    },
    manual_tester: {
      title: "Manual Tester Dashboard",
      sub: "Report new bugs, follow your reports, and triage AI findings.",
      cta: (
        <Link
          to="/report-bug"
          className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-semibold bg-rose-500 hover:bg-rose-400 text-ink-950"
        >
          + Report Bug
        </Link>
      ),
    },
  };
  const head = role ? roleHeading[role] : { title: "Dashboard", sub: "" };

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-ink-100">{head.title}</h1>
          <p className="text-sm text-ink-400 mt-0.5">{head.sub}</p>
        </div>
        {head.cta}
      </header>

      {/* KPI cards — role-specific */}
      {role === "project_manager" && (
        <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Kpi value={summary?.active_bugs ?? "—"} label="Active AI Bugs" color="border-rose-500/30 bg-rose-500/5 text-rose-300" to="/ai-bugs" />
          <Kpi value={summary?.auto_fixed ?? "—"} label="Auto-Fixed"    color="border-emerald-500/30 bg-emerald-500/5 text-emerald-300" to="/ai-bugs" />
          <Kpi value={summary?.manual_open ?? manualBugs.length} label="Manual Reports" color="border-amber-500/30 bg-amber-500/5 text-amber-300" to="/manual-bugs" />
          <Kpi value={`${summary?.pass_rate ?? 0}%`} label="Pass Rate"  color="border-cyan-500/30 bg-cyan-500/5 text-cyan-300" />
        </section>
      )}

      {role === "developer" && (
        <section className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <Kpi value={summary?.pending ?? "—"} label="AI Approvals Pending" color="border-cyan-500/30 bg-cyan-500/5 text-cyan-300" to="/ai-approvals" />
          <Kpi value={summary?.manual_assigned_to_me ?? manualBugs.length} label="Tickets Assigned" color="border-rose-500/30 bg-rose-500/5 text-rose-300" to="/my-tickets" />
          <Kpi value={summary?.auto_fixed ?? "—"} label="Merged Today" color="border-emerald-500/30 bg-emerald-500/5 text-emerald-300" />
        </section>
      )}

      {role === "automation_engineer" && (
        <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Kpi value={summary?.active_bugs ?? "—"} label="Active AI Bugs" color="border-rose-500/30 bg-rose-500/5 text-rose-300" to="/ai-bugs" />
          <Kpi value={summary?.last_run?.total ?? "—"} label="Tests in last run" color="border-cyan-500/30 bg-cyan-500/5 text-cyan-300" />
          <Kpi value={summary?.last_run?.failed ?? "—"} label="Failed last run" color="border-amber-500/30 bg-amber-500/5 text-amber-300" />
          <Kpi value={`${summary?.pass_rate ?? 0}%`} label="Pass Rate" color="border-emerald-500/30 bg-emerald-500/5 text-emerald-300" />
        </section>
      )}

      {role === "manual_tester" && (
        <section className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <Kpi value={summary?.manual_reported_by_me ?? manualBugs.length} label="My Reports" color="border-rose-500/30 bg-rose-500/5 text-rose-300" to="/my-reports" />
          <Kpi value={summary?.active_bugs ?? "—"} label="AI Findings to QA" color="border-violet-500/30 bg-violet-500/5 text-violet-300" to="/ai-bug-qa" />
          <Kpi value={summary?.auto_fixed ?? "—"} label="AI Auto-Fixed" color="border-emerald-500/30 bg-emerald-500/5 text-emerald-300" />
        </section>
      )}

      {/* AI Bugs table — visible to PM, dev, automation engineer; hidden for manual tester (they have their own AI Bug QA page) */}
      {(role === "project_manager" || role === "automation_engineer" || role === "developer") && (
        <section className="rounded-lg border border-ink-700 bg-ink-800/40 overflow-hidden">
          <div className="px-5 py-3 border-b border-ink-700 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink-200">Recent AI Bugs</h2>
            <span className="text-xs text-ink-500 font-mono">{aiBugs.length} total</span>
          </div>
          {aiBugs.length === 0 ? (
            <div className="p-12 text-center text-ink-400 text-sm">
              {role === "developer" ? "No AI fixes awaiting review." : "No AI bugs yet — click Run Tests to scan."}
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-ink-900/40 text-xs uppercase tracking-wider text-ink-400">
                <tr>
                  <th className="text-left px-5 py-2 font-medium">ID</th>
                  <th className="text-left px-5 py-2 font-medium">Description</th>
                  <th className="text-left px-5 py-2 font-medium">Type</th>
                  <th className="text-left px-5 py-2 font-medium">Status</th>
                  <th className="text-left px-5 py-2 font-medium">Branch</th>
                  <th className="text-right px-5 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {aiBugs.slice(0, 8).map((b) => (
                  <tr key={b.uid} className="border-t border-ink-700/60 hover:bg-ink-700/20">
                    <td className="px-5 py-2.5 font-mono text-ink-400">#{b.id}</td>
                    <td className="px-5 py-2.5 text-ink-100">{b.title}</td>
                    <td className="px-5 py-2.5 text-ink-400 text-xs uppercase tracking-wide">{b.type}</td>
                    <td className="px-5 py-2.5"><StatusBadge status={b.status} /></td>
                    <td className="px-5 py-2.5 font-mono text-xs text-cyan-400">{b.branch}</td>
                    <td className="px-5 py-2.5 text-right">
                      <Link to={`/bugs/${b.uid}`} className="text-emerald-400 hover:text-emerald-300 text-xs font-semibold">Open →</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {/* Manual bugs table — visible to PM, dev, manual tester; hidden for auto eng */}
      {(role === "project_manager" || role === "developer" || role === "manual_tester") && (
        <section className="rounded-lg border border-ink-700 bg-ink-800/40 overflow-hidden">
          <div className="px-5 py-3 border-b border-ink-700 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink-200">
              {role === "developer" ? "Tickets assigned to me" :
               role === "manual_tester" ? "My reports" :
               "Manual bug reports"}
            </h2>
            <span className="text-xs text-ink-500 font-mono">{manualBugs.length} total</span>
          </div>
          {manualBugs.length === 0 ? (
            <div className="p-12 text-center text-ink-400 text-sm">No manual bug reports yet.</div>
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
                  <th className="text-right px-5 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {manualBugs.slice(0, 8).map((b) => (
                  <tr key={b.id} className="border-t border-ink-700/60 hover:bg-ink-700/20">
                    <td className="px-5 py-2.5 font-mono text-ink-400">#{b.id}</td>
                    <td className="px-5 py-2.5 text-ink-100">{b.title}</td>
                    <td className="px-5 py-2.5 text-xs uppercase tracking-wider">
                      <SeverityChip s={b.severity} />
                    </td>
                    <td className="px-5 py-2.5 text-xs">
                      <ManualStatusBadge s={b.status} />
                    </td>
                    <td className="px-5 py-2.5 text-ink-400 font-mono text-xs">@{b.reporter_username}</td>
                    <td className="px-5 py-2.5 text-ink-400 font-mono text-xs">
                      {b.assignee_username ? `@${b.assignee_username}` : <span className="text-ink-600">unassigned</span>}
                    </td>
                    <td className="px-5 py-2.5 text-right">
                      <Link to={`/manual-bugs/${b.id}`} className="text-emerald-400 hover:text-emerald-300 text-xs font-semibold">Open →</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </div>
  );
}

function SeverityChip({ s }: { s: string }) {
  const map: Record<string, string> = {
    low:      "bg-cyan-500/10 text-cyan-300 border-cyan-500/30",
    medium:   "bg-amber-500/10 text-amber-300 border-amber-500/30",
    high:     "bg-rose-500/10 text-rose-300 border-rose-500/30",
    critical: "bg-rose-700/15 text-rose-200 border-rose-700/40",
  };
  const cls = map[s] || "bg-ink-500/10 text-ink-300 border-ink-500/30";
  return <span className={`px-2 py-0.5 rounded font-mono text-[10px] uppercase border ${cls}`}>{s}</span>;
}

export function ManualStatusBadge({ s }: { s: string }) {
  const map: Record<string, string> = {
    OPEN:        "bg-rose-500/10 text-rose-300 border-rose-500/30",
    IN_PROGRESS: "bg-amber-500/10 text-amber-300 border-amber-500/30",
    RESOLVED:    "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
    REJECTED:    "bg-ink-500/15 text-ink-300 border-ink-500/30",
  };
  return <span className={`px-2 py-0.5 rounded font-mono text-[10px] uppercase border ${map[s]}`}>{s.replace("_", " ")}</span>;
}
