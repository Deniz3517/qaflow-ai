import type { Role } from "./types";

export interface MenuItem {
  to: string;
  label: string;
  icon: string;
}

export const ROLE_LABELS: Record<Role, string> = {
  developer:           "Developer",
  automation_engineer: "Automation Engineer",
  project_manager:     "Project Manager",
  manual_tester:       "Manual Tester",
};

export const ROLE_BADGE_CLS: Record<Role, string> = {
  developer:           "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  automation_engineer: "bg-violet-500/15  text-violet-300  border-violet-500/30",
  project_manager:     "bg-amber-500/15   text-amber-300   border-amber-500/30",
  manual_tester:       "bg-rose-500/15    text-rose-300    border-rose-500/30",
};

export const MENUS: Record<Role, MenuItem[]> = {
  project_manager: [
    { to: "/",              label: "Overview",      icon: "▣" },
    { to: "/test-runner",   label: "Test Runner",   icon: "▶" },
    { to: "/cypress-runs",  label: "Cypress Runs",  icon: "≡" },
    { to: "/ai-bugs",       label: "AI Bugs",       icon: "✕" },
    { to: "/manual-bugs",   label: "Manual Bugs",   icon: "🛈" },
    { to: "/reports",       label: "Reports",       icon: "≡" },
    { to: "/team",          label: "Team",          icon: "◐" },
  ],
  developer: [
    { to: "/",              label: "Dashboard",     icon: "▣" },
    { to: "/ai-approvals",  label: "AI Approvals",  icon: "✓" },
    { to: "/my-tickets",    label: "My Tickets",    icon: "🛈" },
    { to: "/sandbox",       label: "Dev2 Sandbox",  icon: "◧" },
    { to: "/ai-assist",     label: "AI Assist",     icon: "✦" },
  ],
  automation_engineer: [
    { to: "/",              label: "Dashboard",     icon: "▣" },
    { to: "/test-runner",   label: "Test Runner",   icon: "▶" },
    { to: "/cypress-runs",  label: "Cypress Runs",  icon: "≡" },
    { to: "/ai-bugs",       label: "AI Bugs",       icon: "✕" },
    { to: "/test-scripts",  label: "Test Scripts",  icon: "≡" },
    { to: "/ai-prompts",    label: "AI Prompts",    icon: "✦" },
    { to: "/coverage",      label: "Coverage Map",  icon: "◐" },
  ],
  manual_tester: [
    { to: "/",              label: "Dashboard",     icon: "▣" },
    { to: "/report-bug",    label: "Report Bug",    icon: "+" },
    { to: "/my-reports",    label: "My Reports",    icon: "≡" },
    { to: "/ai-bug-qa",     label: "AI Bug QA",     icon: "✓" },
  ],
};
