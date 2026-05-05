import { Link } from "react-router-dom";
import { useLiveData } from "../useLiveData";
import StatusBadge from "../components/StatusBadge";

export default function AIBugQA() {
  const { bugs } = useLiveData();
  const list = Object.values(bugs).sort(
    (a, b) => +new Date(b.created_at) - +new Date(a.created_at),
  );

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold text-ink-100">AI Bug QA Queue</h1>
        <p className="text-sm text-ink-400 mt-1">
          Review AI-detected bugs for quality before they reach developers. Spot false positives and request better evidence.
        </p>
      </header>

      <div className="rounded-lg border border-ink-700 bg-ink-800/40 overflow-hidden">
        {list.length === 0 ? (
          <div className="p-12 text-center text-ink-400 text-sm">No AI findings yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-ink-900/40 text-xs uppercase tracking-wider text-ink-400">
              <tr>
                <th className="text-left px-5 py-2 font-medium">ID</th>
                <th className="text-left px-5 py-2 font-medium">Title</th>
                <th className="text-left px-5 py-2 font-medium">Type</th>
                <th className="text-left px-5 py-2 font-medium">AI Confidence</th>
                <th className="text-left px-5 py-2 font-medium">Status</th>
                <th className="text-right px-5 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {list.map((b) => (
                <tr key={b.uid} className="border-t border-ink-700/60 hover:bg-ink-700/20">
                  <td className="px-5 py-2.5 font-mono text-ink-400">#{b.id}</td>
                  <td className="px-5 py-2.5 text-ink-100">{b.title}</td>
                  <td className="px-5 py-2.5 text-ink-400 text-xs uppercase tracking-wide">{b.type}</td>
                  <td className="px-5 py-2.5 font-mono text-xs text-violet-300">{b.fix?.confidence ?? "—"}%</td>
                  <td className="px-5 py-2.5"><StatusBadge status={b.status} /></td>
                  <td className="px-5 py-2.5 text-right">
                    <Link to={`/bugs/${b.uid}`} className="text-emerald-400 hover:text-emerald-300 text-xs font-semibold">QA →</Link>
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
