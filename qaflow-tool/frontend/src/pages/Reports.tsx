import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useLiveData } from "../useLiveData";
import type { Bug, CypressRun, Run } from "../types";

interface UnifiedRun {
  id: string;
  kind: "ai" | "cypress";
  status: string;
  started_at: string;
  finished_at?: string;
  triggered_by?: string;
  total: number;
  passed: number;
  failed: number;
  pending: number;
  duration_s?: number;
  bug_uids: string[];
  detail_path: string;
  raw: Run | CypressRun;
}

function unifyAi(r: Run): UnifiedRun {
  return {
    id: r.id,
    kind: "ai",
    status: r.status,
    started_at: r.started_at,
    finished_at: r.finished_at,
    triggered_by: undefined,
    total: r.total ?? 0,
    passed: r.passed ?? 0,
    failed: r.failed ?? 0,
    pending: 0,
    duration_s: r.finished_at && r.started_at
      ? Math.max(0, Math.round((+new Date(r.finished_at) - +new Date(r.started_at)) / 1000))
      : undefined,
    bug_uids: r.bug_uids || [],
    detail_path: "/ai-bugs",
    raw: r,
  };
}

function unifyCypress(r: CypressRun): UnifiedRun {
  return {
    id: r.id,
    kind: "cypress",
    status: r.status,
    started_at: r.started_at,
    finished_at: r.finished_at,
    triggered_by: r.triggered_by,
    total: r.total,
    passed: r.passed,
    failed: r.failed,
    pending: r.pending,
    duration_s: r.duration_s,
    bug_uids: [],  // populated by cross-reference below
    detail_path: `/cypress-runs/${r.id}`,
    raw: r,
  };
}

function summarize(run: UnifiedRun, bugs: Bug[]): string {
  if (run.status !== "COMPLETED") {
    return `${run.kind === "cypress" ? "Cypress run" : "AI scan"} is currently ${run.status.replace("_", " ").toLowerCase()}.`;
  }
  if (run.kind === "ai") {
    const merged = bugs.filter((b) => b.status === "MERGED").length;
    const ready  = bugs.filter((b) => b.status === "FIX_READY").length;
    return `AI Visual Regression scanned ${run.total} test points and surfaced ${run.bug_uids.length} bug${run.bug_uids.length === 1 ? "" : "s"}. ` +
      `${merged} already merged, ${ready} awaiting developer review.`;
  }
  // cypress
  if (run.failed === 0) {
    return `Cypress suite is fully green — ${run.passed}/${run.total} passing.`;
  }
  const newBugs = bugs.length;
  return `Cypress run ${run.id} detected ${run.failed} failing test${run.failed === 1 ? "" : "s"} across ${guessSpecCount(run.raw as CypressRun)} spec${guessSpecCount(run.raw as CypressRun) === 1 ? "" : "s"}. ` +
    `${newBugs} bug${newBugs === 1 ? "" : "s"} routed to the dashboard for AI auto-fix.`;
}

function guessSpecCount(c: CypressRun): number {
  const seen = new Set<string>();
  c.tests?.forEach((t) => { if (t.spec) seen.add(t.spec); });
  return seen.size || 1;
}

export default function Reports() {
  const { runs: aiRuns, bugs: bugMap } = useLiveData();
  const [cypressRuns, setCypressRuns] = useState<CypressRun[]>([]);
  const [, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const fetchOnce = () =>
      api.listCypressRuns()
        .then((rs) => { if (!cancelled) setCypressRuns(rs); })
        .catch(() => {});
    fetchOnce();
    const id = setInterval(fetchOnce, 3000);
    const tickId = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => { cancelled = true; clearInterval(id); clearInterval(tickId); };
  }, []);

  const allBugs = Object.values(bugMap);

  const unified: UnifiedRun[] = useMemo(() => {
    const ai = aiRuns.map(unifyAi);
    const cyp = cypressRuns.map(unifyCypress);
    cyp.forEach((u) => {
      u.bug_uids = allBugs
        .filter((b) => b.run_id === u.id && b.source === "cypress")
        .map((b) => b.uid);
    });
    return [...ai, ...cyp].sort(
      (a, b) => +new Date(b.started_at) - +new Date(a.started_at),
    );
  }, [aiRuns, cypressRuns, allBugs]);

  // Last-24h aggregate
  const cutoff = Date.now() - 24 * 60 * 60 * 1000;
  const recent = unified.filter((r) => +new Date(r.started_at) >= cutoff);
  const totalTests   = recent.reduce((s, r) => s + r.total,  0);
  const totalFails   = recent.reduce((s, r) => s + r.failed, 0);
  const totalBugs    = recent.reduce((s, r) => s + r.bug_uids.length, 0);
  const passRate     = totalTests > 0 ? Math.round(((totalTests - totalFails) / totalTests) * 100) : 0;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-ink-100">Reports</h1>
        <p className="text-sm text-ink-400 mt-0.5">
          Combined timeline of every Cypress and AI scan with auto-generated summaries.
        </p>
      </header>

      <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat value={recent.length} label="Runs · 24h" cls="border-cyan-500/30 bg-cyan-500/5 text-cyan-300" />
        <Stat value={totalTests} label="Tests executed" cls="border-violet-500/30 bg-violet-500/5 text-violet-300" />
        <Stat value={totalFails} label="Failures" cls="border-rose-500/30 bg-rose-500/5 text-rose-300" />
        <Stat value={`${passRate}%`} label="Pass rate" cls="border-emerald-500/30 bg-emerald-500/5 text-emerald-300" />
      </section>

      <div className="text-xs text-ink-500">
        {totalBugs > 0
          ? `${totalBugs} bug${totalBugs === 1 ? "" : "s"} surfaced from the last 24h. AI auto-fix attempts route to dev / automation engineer based on file target.`
          : "No bugs surfaced in the last 24h — suite green."}
      </div>

      {unified.length === 0 ? (
        <div className="p-12 text-center text-ink-400 text-sm rounded-lg border border-ink-700 bg-ink-800/40">
          No runs yet — head to <Link to="/test-runner" className="text-emerald-400">Test Runner</Link>.
        </div>
      ) : (
        <ul className="space-y-3">
          {unified.map((run) => {
            const runBugs = allBugs.filter((b) => b.run_id === run.id);
            return (
              <li key={`${run.kind}-${run.id}`} className="rounded-lg border border-ink-700 bg-ink-800/40 overflow-hidden">
                <div className="px-5 py-3 flex items-center gap-3 flex-wrap">
                  <KindChip kind={run.kind} />
                  <span className="font-mono text-cyan-400 text-sm">{run.id}</span>
                  <span className="text-xs uppercase tracking-wider font-mono text-ink-400">{run.status.replace("_", " ")}</span>
                  <span className="text-xs text-ink-500 font-mono">{new Date(run.started_at).toLocaleString()}</span>
                  {run.duration_s !== undefined && (
                    <span className="text-xs text-ink-500 font-mono">{run.duration_s}s</span>
                  )}
                  {run.triggered_by && (
                    <span className="text-xs text-ink-500 font-mono">by {run.triggered_by}</span>
                  )}
                  <Link to={run.detail_path} className="ml-auto text-xs text-emerald-400 hover:text-emerald-300 font-semibold">
                    Open →
                  </Link>
                </div>
                <div className="px-5 py-3 border-t border-ink-700/60 grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
                  <Mini label="Total"   value={run.total} />
                  <Mini label="Passed"  value={run.passed} cls="text-emerald-400" />
                  <Mini label="Failed"  value={run.failed} cls="text-rose-400" />
                  <Mini label="Pending" value={run.pending} cls="text-amber-400" />
                  <Mini label="Bugs"    value={runBugs.length} cls="text-violet-300" />
                </div>
                <div className="px-5 py-3 border-t border-ink-700/60 text-xs text-ink-300 leading-relaxed">
                  <span className="text-ink-500 uppercase tracking-wider mr-2">Summary</span>
                  {summarize(run, runBugs)}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function Stat({ value, label, cls }: { value: number | string; label: string; cls: string }) {
  return (
    <div className={`rounded-lg border p-4 ${cls}`}>
      <div className="text-3xl font-bold tracking-tight">{value}</div>
      <div className="text-[10px] uppercase tracking-wider text-ink-400 mt-1">{label}</div>
    </div>
  );
}

function Mini({ label, value, cls = "text-ink-200" }: { label: string; value: number | string; cls?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-ink-500">{label}</div>
      <div className={`font-mono text-sm ${cls}`}>{value}</div>
    </div>
  );
}

function KindChip({ kind }: { kind: "ai" | "cypress" }) {
  const cls = kind === "ai"
    ? "bg-violet-500/15 text-violet-300 border-violet-500/40"
    : "bg-cyan-500/15 text-cyan-300 border-cyan-500/40";
  return (
    <span className={`inline-flex px-2 py-0.5 text-[10px] font-mono font-semibold tracking-wider border rounded ${cls}`}>
      {kind === "ai" ? "AI VISUAL" : "CYPRESS"}
    </span>
  );
}
