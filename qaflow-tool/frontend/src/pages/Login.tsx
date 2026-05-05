import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../AuthContext";

const DEMO_USERS = [
  { username: "pm1",   role: "Project Manager",      icon: "▣" },
  { username: "dev1",  role: "Developer",            icon: "✓" },
  { username: "auto1", role: "Automation Engineer",  icon: "▶" },
  { username: "m1",    role: "Manual Tester",        icon: "✕" },
];

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const next = (loc.state as { from?: string } | null)?.from || "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password);
      nav(next, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  };

  const fill = (u: string) => {
    setUsername(u);
    setPassword("12345678");
    setError(null);
  };

  return (
    <div className="min-h-full flex items-center justify-center px-4 py-12 bg-gradient-to-br from-ink-900 to-ink-950">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="inline-block px-3 py-1 rounded-full bg-brand/10 border border-brand/30 text-brand text-[10px] tracking-[3px] mb-3">
            QAFLOW AI
          </div>
          <h1 className="text-3xl font-semibold text-ink-100">Sign in</h1>
          <p className="text-sm text-ink-400 mt-1">AI-powered QA platform · v1.1 demo</p>
        </div>

        <form onSubmit={submit} className="bg-ink-800 border border-ink-700 rounded-xl p-6 shadow-2xl space-y-4">
          <div>
            <label className="block text-xs text-ink-400 mb-1.5 font-medium">Username</label>
            <input
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="pm1, dev1, auto1, m1"
              className="w-full bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-sm text-ink-100 outline-none focus:border-brand"
            />
          </div>
          <div>
            <label className="block text-xs text-ink-400 mb-1.5 font-medium">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="w-full bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-sm text-ink-100 outline-none focus:border-brand"
            />
          </div>
          {error && (
            <div className="text-rose-400 text-xs px-3 py-2 rounded bg-rose-500/10 border border-rose-500/30">
              {error}
            </div>
          )}
          <button
            type="submit"
            disabled={busy || !username || !password}
            className={`w-full py-2.5 rounded-md font-semibold text-sm transition ${
              busy || !username || !password
                ? "bg-ink-700/50 text-ink-500 cursor-not-allowed"
                : "bg-emerald-500 hover:bg-emerald-400 text-ink-950"
            }`}
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <div className="mt-6 text-center">
          <p className="text-[11px] uppercase tracking-wider text-ink-500 mb-3">Demo accounts · password: 12345678</p>
          <div className="grid grid-cols-2 gap-2">
            {DEMO_USERS.map((u) => (
              <button
                key={u.username}
                onClick={() => fill(u.username)}
                className="text-left p-2.5 rounded-md border border-ink-700 bg-ink-800/40 hover:border-brand transition group"
              >
                <div className="flex items-center gap-2">
                  <span className="w-6 h-6 rounded-md bg-brand/15 text-brand inline-flex items-center justify-center text-xs">
                    {u.icon}
                  </span>
                  <div>
                    <div className="text-xs font-mono text-ink-100 group-hover:text-brand">{u.username}</div>
                    <div className="text-[10px] text-ink-500">{u.role}</div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
