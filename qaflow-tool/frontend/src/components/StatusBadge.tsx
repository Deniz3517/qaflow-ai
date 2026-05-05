import type { BugStatus } from "../types";

const MAP: Record<BugStatus, { label: string; cls: string }> = {
  DETECTED:          { label: "DETECTED",      cls: "bg-rose-500/10 text-rose-300 border-rose-500/30" },
  AI_ANALYZING:      { label: "AI ANALYZING",  cls: "bg-violet-500/10 text-violet-300 border-violet-500/30" },
  SANDBOX_APPLYING:  { label: "SANDBOX RUNNING",cls: "bg-amber-500/10 text-amber-300 border-amber-500/30" },
  FIX_READY:         { label: "IN REVIEW",     cls: "bg-cyan-500/10 text-cyan-300 border-cyan-500/30" },
  FIX_FAILED:        { label: "FIX FAILED",    cls: "bg-rose-700/15 text-rose-300 border-rose-700/40" },
  MERGED:            { label: "AUTO-FIXED",    cls: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30" },
  REJECTED:          { label: "REJECTED",      cls: "bg-ink-500/15 text-ink-300 border-ink-500/30" },
};

export default function StatusBadge({ status }: { status: BugStatus }) {
  const it = MAP[status] || { label: status, cls: "bg-ink-500/10 text-ink-300 border-ink-500/30" };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-[10px] font-mono font-semibold tracking-wide uppercase rounded border ${it.cls}`}>
      {it.label}
    </span>
  );
}
