import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../AuthContext";
import type { Bug } from "../types";

interface Notification {
  id: string;
  ts: string;
  kind: "fix-ready" | "bug-detected" | "merged";
  bugUid: string;
  bugId: number;
  title: string;
  target: "buggy-app" | "cypress-tests";
}

const STORAGE_KEY = "qaflow_notifications_v1";
const READ_KEY = "qaflow_notifications_read_v1";
const MAX = 30;

function loadStored(): Notification[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}
function saveStored(items: Notification[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, MAX)));
}
function loadReadIds(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(READ_KEY) || "[]"));
  } catch {
    return new Set();
  }
}
function saveReadIds(ids: Set<string>) {
  localStorage.setItem(READ_KEY, JSON.stringify(Array.from(ids).slice(-200)));
}

function targetOf(bug: Bug): "buggy-app" | "cypress-tests" {
  return bug.fix?.target_repo ?? "buggy-app";
}

function relevantTo(role: string | undefined, n: Notification): boolean {
  if (!role) return false;
  if (role === "developer") return n.target === "buggy-app";
  if (role === "automation_engineer") return n.target === "cypress-tests";
  // PM and manual tester see everything
  return true;
}

export default function NotificationBell() {
  const { user } = useAuth();
  const [items, setItems] = useState<Notification[]>(loadStored);
  const [readIds, setReadIds] = useState<Set<string>>(loadReadIds);
  const [open, setOpen] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!user) return;
    const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.addEventListener("message", (e) => {
      let evt: { type: string; bug?: Bug };
      try { evt = JSON.parse(e.data); } catch { return; }
      if (evt.type !== "bug.update" && evt.type !== "bug.created") return;
      const bug = evt.bug;
      if (!bug) return;

      let kind: Notification["kind"] | null = null;
      if (bug.status === "FIX_READY") kind = "fix-ready";
      else if (bug.status === "DETECTED") kind = "bug-detected";
      else if (bug.status === "MERGED") kind = "merged";
      if (!kind) return;

      const note: Notification = {
        id: `${bug.uid}-${kind}-${bug.status}`,
        ts: new Date().toISOString(),
        kind,
        bugUid: bug.uid,
        bugId: bug.id,
        title: bug.title,
        target: targetOf(bug),
      };

      setItems((prev) => {
        const without = prev.filter((p) => p.id !== note.id);
        const next = [note, ...without];
        saveStored(next);
        return next;
      });
    });

    return () => ws.close();
  }, [user]);

  const visible = items.filter((n) => relevantTo(user?.role, n));
  const unread = visible.filter((n) => !readIds.has(n.id));

  const markAllRead = () => {
    const next = new Set(readIds);
    visible.forEach((n) => next.add(n.id));
    setReadIds(next);
    saveReadIds(next);
  };

  return (
    <div className="relative">
      <button
        onClick={() => {
          setOpen((v) => !v);
          if (!open) setTimeout(markAllRead, 800); // mark read shortly after opening
        }}
        className="relative inline-flex items-center justify-center w-9 h-9 rounded-full hover:bg-ink-700/40 text-ink-300"
        aria-label="Notifications"
        title="Notifications"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {unread.length > 0 && (
          <span className="absolute -top-0.5 -right-0.5 inline-flex items-center justify-center min-w-[18px] h-[18px] rounded-full bg-rose-500 text-[10px] font-bold text-white px-1">
            {unread.length > 9 ? "9+" : unread.length}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-11 w-96 max-h-[480px] overflow-y-auto rounded-lg border border-ink-700 bg-ink-900/95 backdrop-blur shadow-xl z-50">
          <div className="px-4 py-3 border-b border-ink-700 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-ink-200">Notifications</h3>
            <span className="text-xs text-ink-500 font-mono">{visible.length} total</span>
          </div>
          {visible.length === 0 ? (
            <div className="p-8 text-center text-ink-400 text-sm">No notifications yet.</div>
          ) : (
            <ul className="divide-y divide-ink-700/60">
              {visible.slice(0, 20).map((n) => (
                <li key={n.id + n.ts}>
                  <Link
                    to={`/bugs/${n.bugUid}`}
                    onClick={() => setOpen(false)}
                    className={`block px-4 py-3 hover:bg-ink-700/30 ${readIds.has(n.id) ? "opacity-60" : ""}`}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <KindBadge kind={n.kind} />
                      <span className="text-[10px] font-mono text-ink-500 uppercase tracking-wide">
                        {n.target}
                      </span>
                      <span className="text-[10px] text-ink-500 ml-auto">{relTime(n.ts)}</span>
                    </div>
                    <div className="text-sm text-ink-100 truncate">
                      <span className="font-mono text-ink-400">#{n.bugId}</span> · {n.title}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function KindBadge({ kind }: { kind: Notification["kind"] }) {
  const map: Record<Notification["kind"], { label: string; cls: string }> = {
    "fix-ready":    { label: "FIX READY",    cls: "bg-cyan-500/15 text-cyan-300 border-cyan-500/40" },
    "bug-detected": { label: "NEW BUG",      cls: "bg-rose-500/15 text-rose-300 border-rose-500/40" },
    "merged":       { label: "MERGED",       cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40" },
  };
  const it = map[kind];
  return (
    <span className={`inline-flex px-1.5 py-0.5 text-[10px] font-mono font-semibold tracking-wider border rounded ${it.cls}`}>
      {it.label}
    </span>
  );
}

function relTime(iso: string): string {
  const ms = Date.now() - +new Date(iso);
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(iso).toLocaleDateString();
}
