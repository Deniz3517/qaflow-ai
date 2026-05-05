import { useLiveData } from "../useLiveData";

export default function Reports() {
  const { runs } = useLiveData();

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-ink-100">Reports</h1>
        <p className="text-sm text-ink-400 mt-0.5">Test run history with pass / fail summary.</p>
      </header>

      <div className="rounded-lg border border-ink-700 bg-ink-800/40 overflow-hidden">
        {runs.length === 0 ? (
          <div className="p-12 text-center text-ink-400 text-sm">No runs yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-ink-900/40 text-xs uppercase tracking-wider text-ink-400">
              <tr>
                <th className="text-left px-5 py-2 font-medium">Run</th>
                <th className="text-left px-5 py-2 font-medium">Suite</th>
                <th className="text-left px-5 py-2 font-medium">Status</th>
                <th className="text-left px-5 py-2 font-medium">Started</th>
                <th className="text-left px-5 py-2 font-medium">Result</th>
                <th className="text-left px-5 py-2 font-medium">Bugs</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-t border-ink-700/60">
                  <td className="px-5 py-2.5 font-mono text-cyan-400">{r.id}</td>
                  <td className="px-5 py-2.5 uppercase text-xs tracking-wider text-ink-300">{r.suite}</td>
                  <td className="px-5 py-2.5 text-xs font-mono">{r.status}</td>
                  <td className="px-5 py-2.5 text-xs text-ink-400 font-mono">{r.started_at}</td>
                  <td className="px-5 py-2.5">
                    {r.total !== undefined ? (
                      <span className="font-mono text-xs">
                        <span className="text-emerald-400">{r.passed}</span>
                        <span className="text-ink-500">/{r.total}</span>
                        {r.failed ? <span className="text-rose-400 ml-2">{r.failed} failed</span> : null}
                      </span>
                    ) : "—"}
                  </td>
                  <td className="px-5 py-2.5 font-mono text-xs text-ink-300">{r.bug_uids?.length || 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
