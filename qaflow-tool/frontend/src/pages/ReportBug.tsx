import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Severity, User } from "../types";

const SEVERITIES: Severity[] = ["low", "medium", "high", "critical"];

const TEMPLATE_HINTS = [
  "Search button does nothing",
  "Cart total doesn't multiply by quantity",
  "Wrong product price displayed",
  "Mobile menu doesn't open",
  "Image broken on product card",
];

export default function ReportBug() {
  const nav = useNavigate();
  const [devs, setDevs] = useState<User[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [severity, setSeverity] = useState<Severity>("medium");
  const [pageUrl, setPageUrl] = useState("http://localhost:3001/");
  const [steps, setSteps] = useState("");
  const [assigneeId, setAssigneeId] = useState<string>("");

  useEffect(() => {
    api.listUsers("developer").then(setDevs).catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const bug = await api.createManualBug({
        title,
        description,
        severity,
        page_url: pageUrl || undefined,
        steps_to_reproduce: steps || undefined,
        assignee_id: assigneeId ? Number(assigneeId) : undefined,
      } as Partial<import("../types").ManualBug>);
      nav(`/manual-bugs/${bug.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "submit failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-2xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold text-ink-100">Report a bug</h1>
        <p className="text-sm text-ink-400 mt-1">
          File a manual finding from your exploratory testing. The developer you assign will see it instantly.
        </p>
      </header>

      <form onSubmit={submit} className="space-y-4 bg-ink-800/40 border border-ink-700 rounded-lg p-5">
        <div>
          <label className="block text-xs font-medium text-ink-400 mb-1.5">Title</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Short, specific summary"
            className="w-full bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-sm text-ink-100 outline-none focus:border-brand"
            required
          />
          <div className="flex flex-wrap gap-1.5 mt-2">
            {TEMPLATE_HINTS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTitle(t)}
                className="text-[11px] px-2 py-0.5 rounded border border-ink-700 text-ink-400 hover:border-brand hover:text-brand"
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-ink-400 mb-1.5">Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What did you observe? What did you expect?"
            rows={3}
            className="w-full bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-sm text-ink-100 outline-none focus:border-brand"
            required
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-ink-400 mb-1.5">Severity</label>
            <select
              value={severity}
              onChange={(e) => setSeverity(e.target.value as Severity)}
              className="w-full bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-sm text-ink-100 outline-none focus:border-brand"
            >
              {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-ink-400 mb-1.5">Page URL</label>
            <input
              value={pageUrl}
              onChange={(e) => setPageUrl(e.target.value)}
              placeholder="http://localhost:3001/cart.html"
              className="w-full bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-sm text-ink-100 outline-none focus:border-brand"
            />
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-ink-400 mb-1.5">Steps to reproduce</label>
          <textarea
            value={steps}
            onChange={(e) => setSteps(e.target.value)}
            placeholder="1. Open cart\n2. Increase quantity to 3\n3. Subtotal still shows 1×price"
            rows={4}
            className="w-full bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-sm text-ink-100 outline-none focus:border-brand font-mono"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-ink-400 mb-1.5">Assign to (developer)</label>
          <select
            value={assigneeId}
            onChange={(e) => setAssigneeId(e.target.value)}
            className="w-full bg-ink-900 border border-ink-700 rounded-md px-3 py-2 text-sm text-ink-100 outline-none focus:border-brand"
          >
            <option value="">— unassigned —</option>
            {devs.map((d) => (
              <option key={d.id} value={d.id}>{d.full_name} (@{d.username})</option>
            ))}
          </select>
        </div>

        {error && (
          <div className="text-rose-400 text-xs px-3 py-2 rounded bg-rose-500/10 border border-rose-500/30">{error}</div>
        )}

        <div className="flex items-center gap-3 pt-1">
          <button
            type="submit"
            disabled={busy || !title || !description}
            className={`px-5 py-2 rounded-md font-semibold text-sm transition ${
              busy || !title || !description
                ? "bg-ink-700/50 text-ink-500 cursor-not-allowed"
                : "bg-rose-500 hover:bg-rose-400 text-ink-950"
            }`}
          >
            {busy ? "Submitting…" : "Submit bug report"}
          </button>
          <button
            type="button"
            onClick={() => nav(-1)}
            className="text-sm text-ink-400 hover:text-ink-200"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
