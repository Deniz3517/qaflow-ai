import { NavLink } from "react-router-dom";
import { useAuth } from "../AuthContext";
import { MENUS, ROLE_BADGE_CLS, ROLE_LABELS } from "../menus";

export default function Sidebar({ aiMode, connected }: { aiMode: string; connected: boolean }) {
  const { user, logout } = useAuth();
  if (!user) return null;
  const items = MENUS[user.role];

  return (
    <aside className="w-60 shrink-0 border-r border-ink-700 bg-ink-800 flex flex-col">
      <div className="px-5 py-4 border-b border-ink-700">
        <div className="text-brand font-bold tracking-wider text-lg">QAFLOW AI</div>
        <div className="text-xs text-ink-400 mt-0.5">AI-Powered QA Platform</div>
      </div>

      <div className="px-5 py-3 border-b border-ink-700">
        <div className="text-xs text-ink-400">Signed in as</div>
        <div className="text-sm text-ink-100 font-semibold mt-0.5">{user.full_name}</div>
        <div className="text-[11px] text-ink-500 font-mono">@{user.username}</div>
        <span className={`mt-2 inline-block px-2 py-0.5 text-[10px] font-mono uppercase tracking-wide rounded border ${ROLE_BADGE_CLS[user.role]}`}>
          {ROLE_LABELS[user.role]}
        </span>
      </div>

      <nav className="flex-1 py-3">
        {items.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.to === "/"}
            className={({ isActive }) =>
              `flex items-center gap-3 px-5 py-2.5 text-sm transition border-l-2 -ml-px pl-[19px] ${
                isActive
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-400"
                  : "text-ink-300 hover:text-ink-100 hover:bg-ink-700/40 border-transparent"
              }`
            }
          >
            <span className="text-base opacity-70 w-4">{n.icon}</span>
            <span>{n.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="px-5 py-4 border-t border-ink-700 text-xs space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-ink-400">AI Mode</span>
          <span className={`px-2 py-0.5 rounded font-mono text-[10px] uppercase ${
            aiMode === "claude"
              ? "bg-violet-500/15 text-violet-300 border border-violet-500/30"
              : "bg-amber-500/10 text-amber-300 border border-amber-500/25"
          }`}>{aiMode}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-ink-400">Live</span>
          <span className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${connected ? "bg-emerald-400 animate-pulse" : "bg-rose-400"}`} />
            <span className={connected ? "text-emerald-400" : "text-rose-400"}>
              {connected ? "connected" : "offline"}
            </span>
          </span>
        </div>
        <button
          onClick={logout}
          className="mt-2 w-full text-left px-2 py-1.5 rounded text-ink-300 hover:bg-ink-700/40 hover:text-rose-300 transition text-xs"
        >
          ⏻ Sign out
        </button>
      </div>
    </aside>
  );
}
