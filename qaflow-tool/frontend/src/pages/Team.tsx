import { useEffect, useState } from "react";
import { api } from "../api";
import type { ManualBug, User } from "../types";
import { ROLE_BADGE_CLS, ROLE_LABELS } from "../menus";

export default function Team() {
  const [users, setUsers] = useState<User[]>([]);
  const [bugs, setBugs] = useState<ManualBug[]>([]);

  useEffect(() => {
    api.listUsers().then(setUsers).catch(() => {});
    api.listManualBugs().then(setBugs).catch(() => {});
  }, []);

  const workload = (uid: number) => bugs.filter((b) => b.assignee_id === uid && (b.status === "OPEN" || b.status === "IN_PROGRESS")).length;
  const reported = (uid: number) => bugs.filter((b) => b.reporter_id === uid).length;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-ink-100">Team</h1>
        <p className="text-sm text-ink-400 mt-1">Roles, current workload, and bug reports per member.</p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {users.map((u) => (
          <div key={u.id} className="rounded-lg border border-ink-700 bg-ink-800/40 p-5">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-brand/20 text-brand flex items-center justify-center font-bold">
                {u.full_name.slice(0, 1).toUpperCase()}
              </div>
              <div>
                <div className="text-ink-100 font-semibold">{u.full_name}</div>
                <div className="text-[11px] text-ink-500 font-mono">@{u.username}</div>
              </div>
            </div>
            <span className={`mt-3 inline-block px-2 py-0.5 text-[10px] font-mono uppercase tracking-wide rounded border ${ROLE_BADGE_CLS[u.role]}`}>
              {ROLE_LABELS[u.role]}
            </span>
            <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
              <div className="rounded bg-ink-900/40 px-3 py-2">
                <div className="text-ink-500">Open assigned</div>
                <div className="text-ink-100 text-lg font-semibold">{workload(u.id)}</div>
              </div>
              <div className="rounded bg-ink-900/40 px-3 py-2">
                <div className="text-ink-500">Reported</div>
                <div className="text-ink-100 text-lg font-semibold">{reported(u.id)}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
