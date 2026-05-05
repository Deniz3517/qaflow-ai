import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

interface Suite {
  key: string;
  title: string;
  desc: string;
  ring: string;
  enabled: boolean;
  status: "READY" | "DEFERRED";
  kind: "ai" | "cypress";
}

const SUITES: Suite[] = [
  {
    key: "cypress", title: "Cypress UI Suite",
    desc: "~95 end-to-end tests across home, products, cart, login (catches the seeded UI bugs)",
    ring: "border-cyan-500/40 hover:border-cyan-400",
    enabled: true, status: "READY", kind: "cypress",
  },
  {
    key: "ui", title: "AI Visual Regression",
    desc: "Playwright + AI bug detection (drives the auto-fix loop)",
    ring: "border-violet-500/40 hover:border-violet-400",
    enabled: true, status: "READY", kind: "ai",
  },
  {
    key: "functional", title: "Functional Testing",
    desc: "Form validation, user flows",
    ring: "border-emerald-500/40", enabled: false, status: "DEFERRED", kind: "cypress",
  },
  {
    key: "api", title: "API Testing",
    desc: "Schema + response validation",
    ring: "border-yellow-500/40", enabled: false, status: "DEFERRED", kind: "cypress",
  },
  {
    key: "cross", title: "Cross-Browser",
    desc: "Chrome / Firefox / WebKit",
    ring: "border-violet-500/40", enabled: false, status: "DEFERRED", kind: "cypress",
  },
  {
    key: "smoke", title: "Smoke / Sanity",
    desc: "Critical paths post-deploy",
    ring: "border-orange-500/40", enabled: false, status: "DEFERRED", kind: "cypress",
  },
  {
    key: "a11y", title: "Accessibility",
    desc: "Axe + WCAG 2.1 scan",
    ring: "border-rose-500/40", enabled: false, status: "DEFERRED", kind: "cypress",
  },
];

export default function TestRunner() {
  const nav = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const run = async (suite: Suite) => {
    if (!suite.enabled) return;
    setBusy(suite.key);
    setMsg(null);
    try {
      if (suite.kind === "cypress") {
        const r = await api.triggerCypressRun();
        nav(`/cypress-runs/${r.id}`);
      } else {
        const r = await api.triggerRun(suite.key);
        setMsg(`AI run ${r.id} started — see Overview for live status.`);
      }
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-ink-100">Test Runner</h1>
        <p className="text-sm text-ink-400 mt-0.5">
          Launch any test suite with 1 click — no CI/CD knowledge required.
        </p>
      </header>

      {msg && (
        <div className="rounded border border-emerald-500/30 bg-emerald-500/5 text-emerald-300 px-4 py-2 text-sm">
          {msg}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {SUITES.map((s) => (
          <div
            key={s.key}
            className={`rounded-lg border-2 ${s.ring} bg-ink-800/40 p-5 transition ${
              !s.enabled ? "opacity-50" : ""
            }`}
          >
            <div className="flex items-center justify-between mb-1">
              <h3 className="font-semibold text-ink-100">{s.title}</h3>
              <span className={`text-[10px] font-mono uppercase tracking-wider px-2 py-0.5 rounded border ${
                s.status === "READY"
                  ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10"
                  : "border-ink-500/40 text-ink-400 bg-ink-500/10"
              }`}>{s.status}</span>
            </div>
            <p className="text-xs text-ink-400 mb-4 min-h-[36px]">{s.desc}</p>
            <button
              onClick={() => run(s)}
              disabled={!s.enabled || busy === s.key}
              className={`w-full text-sm py-2 rounded font-semibold transition ${
                !s.enabled
                  ? "bg-ink-700/50 text-ink-500 cursor-not-allowed"
                  : busy === s.key
                  ? "bg-emerald-500/30 text-emerald-200 cursor-wait"
                  : "bg-emerald-500 hover:bg-emerald-400 text-ink-950"
              }`}
            >
              {busy === s.key ? "Starting…" : "▶ RUN"}
            </button>
          </div>
        ))}
      </div>

      <p className="text-xs text-ink-500">
        Demo v1.1: Cypress and AI Visual Regression are wired. The other suites are scaffolded for future iterations.
      </p>
    </div>
  );
}
