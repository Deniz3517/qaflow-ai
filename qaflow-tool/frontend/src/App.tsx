import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import { useAuth } from "./AuthContext";
import { useLiveData } from "./useLiveData";
import Sidebar from "./components/Sidebar";
import ProtectedRoute from "./components/ProtectedRoute";

import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import TestRunner from "./pages/TestRunner";
import Bugs from "./pages/Bugs";
import BugDetail from "./pages/BugDetail";
import Reports from "./pages/Reports";
import CypressRuns from "./pages/CypressRuns";
import CypressRunDetail from "./pages/CypressRunDetail";
import ManualBugs from "./pages/ManualBugs";
import ManualBugDetail from "./pages/ManualBugDetail";
import ReportBug from "./pages/ReportBug";
import AIApprovals from "./pages/AIApprovals";
import AIBugQA from "./pages/AIBugQA";
import Team from "./pages/Team";
import Placeholder from "./pages/Placeholder";

export default function App() {
  const { user, loading } = useAuth();
  const loc = useLocation();

  // The data hook needs to remount when the user changes (so headers refresh)
  const liveDataKey = user?.id ?? "anon";

  if (loading) {
    return <div className="min-h-full flex items-center justify-center text-ink-400">Loading…</div>;
  }

  // Login route is always available
  if (loc.pathname === "/login") {
    if (user) return <Navigate to="/" replace />;
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
      </Routes>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  }

  return <AppShell key={liveDataKey} />;
}

function AppShell() {
  const { summary, connected } = useLiveData();
  return (
    <div className="flex h-full">
      <Sidebar aiMode={summary?.ai_mode || "mock"} connected={connected} />
      <main className="flex-1 overflow-y-auto px-8 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />

          {/* AI auto-fix routes */}
          <Route path="/ai-bugs" element={<ProtectedRoute><Bugs /></ProtectedRoute>} />
          <Route path="/bugs/:uid" element={<ProtectedRoute><BugDetail /></ProtectedRoute>} />
          <Route path="/test-runner" element={<ProtectedRoute roles={["project_manager", "automation_engineer"]}><TestRunner /></ProtectedRoute>} />
          <Route path="/cypress-runs" element={<ProtectedRoute><CypressRuns /></ProtectedRoute>} />
          <Route path="/cypress-runs/:id" element={<ProtectedRoute><CypressRunDetail /></ProtectedRoute>} />
          <Route path="/reports" element={<ProtectedRoute roles={["project_manager"]}><Reports /></ProtectedRoute>} />
          <Route path="/ai-approvals" element={<ProtectedRoute roles={["developer"]}><AIApprovals /></ProtectedRoute>} />

          {/* Manual bug routes */}
          <Route path="/manual-bugs" element={<ManualBugs />} />
          <Route path="/manual-bugs/:id" element={<ManualBugDetail />} />
          <Route path="/my-tickets" element={<ProtectedRoute roles={["developer"]}><ManualBugs title="Tickets assigned to me" /></ProtectedRoute>} />
          <Route path="/my-reports" element={<ProtectedRoute roles={["manual_tester"]}><ManualBugs scope="mine" title="My bug reports" /></ProtectedRoute>} />
          <Route path="/report-bug" element={<ProtectedRoute roles={["manual_tester"]}><ReportBug /></ProtectedRoute>} />
          <Route path="/ai-bug-qa" element={<ProtectedRoute roles={["manual_tester"]}><AIBugQA /></ProtectedRoute>} />

          {/* Team / PM */}
          <Route path="/team" element={<ProtectedRoute roles={["project_manager"]}><Team /></ProtectedRoute>} />

          {/* Placeholders for v2 features */}
          <Route path="/sandbox" element={
            <Placeholder
              title="Dev2 Sandbox"
              description="Inspect the isolated sandbox where AI applies and tests fixes."
              bullets={[
                "Live shell into the running sandbox container",
                "Branch / commit history per bug",
                "Re-run tests against current sandbox state",
                "Manual override to apply or revert a patch",
              ]}
            />
          } />
          <Route path="/ai-assist" element={
            <Placeholder
              title="AI Assist (Dev2)"
              description="Prompt-driven coding assistant inside the Dev2 sandbox."
              bullets={[
                "Ask Claude to scaffold a feature, refactor, or review code",
                "Generated changes run in the sandbox first",
                "Review diffs before merging",
                "Per-developer prompt history",
              ]}
            />
          } />
          <Route path="/test-scripts" element={
            <Placeholder
              title="Test Scripts"
              description="Browse, edit, and version Playwright/Robot Framework scripts."
              bullets={[
                "Direct repository edit for the Automation Engineer",
                "AI auto-scan to propose updated scripts from a project URL",
                "Coverage diff per commit",
              ]}
            />
          } />
          <Route path="/ai-prompts" element={
            <Placeholder
              title="AI Prompt Studio"
              description="Tune the AI engine's bug-analysis prompts and track quality."
              bullets={[
                "Per-template versioned prompts",
                "A/B comparison of confidence and false-positive rate",
                "Quality metric dashboard",
              ]}
            />
          } />
          <Route path="/coverage" element={
            <Placeholder
              title="Coverage Map"
              description="Visualize where tests exist and where coverage is thin."
              bullets={[
                "Per-page coverage heatmap",
                "Untested user flows surfaced first",
                "Trend over the last 4 sprints",
              ]}
            />
          } />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
