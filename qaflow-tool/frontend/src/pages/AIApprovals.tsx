import { Link } from "react-router-dom";
import { useLiveData } from "../useLiveData";
import StatusBadge from "../components/StatusBadge";

export default function AIApprovals() {
  const { bugs } = useLiveData();
  const pending = Object.values(bugs)
    .filter((b) => b.status === "FIX_READY")
    .sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at));
  const recent = Object.values(bugs)
    .filter((b) => b.status === "MERGED" || b.status === "REJECTED")
    .sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))
    .slice(0, 6);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-ink-100">AI Fix Approvals</h1>
        <p className="text-sm text-ink-400 mt-1">
          AI has prepared these patches in Dev2 sandbox. Approve or reject each.
        </p>
      </header>

      <section>
        <h2 className="text-sm font-semibold text-ink-200 mb-2">Pending review</h2>
        {pending.length === 0 ? (
          <div className="rounded-lg border border-ink-700 bg-ink-800/30 p-12 text-center text-ink-400 text-sm">
            <div className="text-3xl mb-2 opacity-30">✓</div>
            All AI fixes are processed. Nothing is waiting on you.
          </div>
        ) : (
          <div className="space-y-2">
            {pending.map((b) => (
              <Link
                key={b.uid}
                to={`/bugs/${b.uid}`}
                className="block rounded-lg border border-cyan-500/30 bg-cyan-500/5 hover:bg-cyan-500/10 p-4 transition"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <div className="flex items-center gap-2 text-xs">
                      <span className="font-mono text-ink-400">#{b.id}</span>
                      <StatusBadge status={b.status} />
                      {b.fix && (
                        <span className="px-2 py-0.5 rounded font-mono text-[10px] bg-violet-500/15 text-violet-300 border border-violet-500/30">
                          {b.fix.confidence}% confidence
                        </span>
                      )}
                    </div>
                    <h3 className="text-ink-100 font-semibold mt-1.5">{b.title}</h3>
                    {b.fix && <p className="text-xs text-ink-400 mt-1 font-mono">{b.fix.file}</p>}
                  </div>
                  <span className="text-emerald-400 text-sm font-semibold">Review →</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      {recent.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-ink-200 mb-2">Recent decisions</h2>
          <div className="rounded-lg border border-ink-700 bg-ink-800/40 overflow-hidden">
            <table className="w-full text-sm">
              <tbody>
                {recent.map((b) => (
                  <tr key={b.uid} className="border-t border-ink-700/60">
                    <td className="px-5 py-2.5 font-mono text-ink-400 text-xs">#{b.id}</td>
                    <td className="px-5 py-2.5 text-ink-200">{b.title}</td>
                    <td className="px-5 py-2.5"><StatusBadge status={b.status} /></td>
                    <td className="px-5 py-2.5 text-right">
                      <Link to={`/bugs/${b.uid}`} className="text-emerald-400 hover:text-emerald-300 text-xs">View →</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
