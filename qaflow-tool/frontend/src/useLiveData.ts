import { useEffect, useState, useCallback } from "react";
import { api } from "./api";
import type { Bug, DashboardSummary, Run } from "./types";

export function useLiveData() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [bugs, setBugs] = useState<Record<string, Bug>>({});
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [connected, setConnected] = useState(false);

  const refreshSummary = useCallback(() => {
    api.dashboard().then(setSummary).catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listRuns(), api.listBugs(), api.dashboard()])
      .then(([rs, bs, s]) => {
        if (cancelled) return;
        setRuns(rs);
        setBugs(Object.fromEntries(bs.map((b) => [b.uid, b])));
        setSummary(s);
      })
      .catch(() => { /* unauthenticated or backend down — silent */ });

    const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    const ws = new WebSocket(wsUrl);
    ws.addEventListener("open", () => setConnected(true));
    ws.addEventListener("close", () => setConnected(false));
    ws.addEventListener("message", (e) => {
      const evt = JSON.parse(e.data);
      switch (evt.type) {
        case "snapshot":
          setRuns(evt.runs);
          setBugs(Object.fromEntries(evt.bugs.map((b: Bug) => [b.uid, b])));
          break;
        case "run.created":
        case "run.update":
          setRuns((prev) => {
            const idx = prev.findIndex((r) => r.id === evt.run.id);
            const next = idx === -1 ? [evt.run, ...prev] : [...prev];
            if (idx !== -1) next[idx] = evt.run;
            return next;
          });
          refreshSummary();
          break;
        case "bug.created":
        case "bug.update":
          setBugs((prev) => ({ ...prev, [evt.bug.uid]: evt.bug }));
          refreshSummary();
          break;
      }
    });

    return () => {
      cancelled = true;
      ws.close();
    };
  }, [refreshSummary]);

  return { runs, bugs, summary, connected, refreshSummary };
}
