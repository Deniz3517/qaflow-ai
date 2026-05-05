import { useState } from "react";
import { api } from "../api";

export default function RunTestsButton({ suite = "ui", label = "Run Tests" }: { suite?: string; label?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const click = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.triggerRun(suite);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-3">
      <button
        onClick={click}
        disabled={busy}
        className={`inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-semibold transition ${
          busy
            ? "bg-emerald-500/30 text-emerald-200 cursor-wait"
            : "bg-emerald-500 hover:bg-emerald-400 text-ink-950"
        }`}
      >
        <span className="text-xs">▶</span>
        {busy ? "Starting..." : label}
      </button>
      {error && <span className="text-xs text-rose-400">{error}</span>}
    </div>
  );
}
