# QAFLOW AI — Demo v1.0

AI-powered QA automation platform. Detects UI bugs in a target web app, asks an
AI engine to propose a minimal fix, applies the fix in an isolated sandbox,
captures before/after screenshots, and lets a developer approve the change with
one click. On approval the same patch is committed to the live app.

This demo runs entirely on your machine — no VM, no cloud, no Docker. The
production architecture from the spec replaces the local sandbox with a
dedicated VM + Docker container, but every other piece (AI engine, test runner,
approval flow, real-time dashboard) is implemented here.

## What's inside

```
qaflow-ai/
├── buggy-app/              # Sample web app with seeded UI bugs (port 3001)
├── qaflow-tool/            # The AI tool itself
│   ├── backend/            #   FastAPI + Playwright + AI engine + git sandbox (port 8000)
│   └── frontend/           #   React + TypeScript + Tailwind dashboard (port 3000)
├── cypress-tests/          # Cypress E2E test suite targeting buggy-app
├── start.sh                # Boots buggy-app + backend + frontend
└── stop.sh                 # Stops them
```

## Seeded bugs

| ID    | File                 | Type           | Buggy state                       | AI fix                             |
|-------|----------------------|----------------|-----------------------------------|------------------------------------|
| #3492 | `public/styles.css`  | UI             | `.login-button { margin-left:12px }` | `margin: 0 auto`                |
| #3493 | `public/index.html`  | Functional     | `<input id="email">` no `type`      | adds `type="email"`              |
| #3494 | `public/styles.css`  | Accessibility  | `.status.error { color:#475569 }`   | `color: #ef4444` (WCAG AA)       |
| #3495 | `public/styles.css`  | UI             | `.app-title` no `text-align`        | adds `text-align: center`        |

## Running it

```bash
./start.sh
```

First run installs Python deps, downloads Chromium, and runs `npm install` —
takes 1–2 minutes. Subsequent runs are instant.

Then open **http://localhost:3000** and:

1. Click **Run Tests** (top right of the dashboard)
2. Watch bugs appear in the table as they are detected, analyzed, fixed
3. Click **Open** on any bug to see the AI's analysis, before/after screenshots, and code diff
4. Click **APPROVE & MERGE** — the fix is committed to `buggy-app` on `main`
5. Refresh **http://localhost:3001** to see the bug actually fixed

`./stop.sh` cleans up.

## AI mode

Default is `mock` — uses a deterministic fix table so the demo runs offline.
To use the real Claude API:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
./start.sh
```

The dashboard sidebar shows the active mode. If a Claude call fails, the
backend automatically falls back to mock — the demo never breaks.

## How the bug-fix loop works

```
  Run Tests (Playwright on :3001)
        ↓
  Bug detected with evidence
        ↓
  AI engine proposes patch (mock | Claude)
        ↓
  Sandbox: clone buggy-app → branch bug/XXXX → apply patch → commit
        ↓
  Spin up sandboxed Node server on a free port → after-screenshot
        ↓
  Dashboard shows BEFORE / AFTER / diff / confidence
        ↓
  Developer clicks APPROVE
        ↓
  merge_to_main: same patch applied to live buggy-app + git commit
        ↓
  Refresh :3001 → bug is gone
```

## Maps to the QAFLOW AI v2.0 spec

| Spec module           | Demo equivalent                                          |
|-----------------------|----------------------------------------------------------|
| AI Engine             | `qaflow-tool/backend/ai_engine.py`                                   |
| Dev2 Sandbox (VM)     | `qaflow-tool/backend/sandbox.py` (local clone + ephemeral Node srv)  |
| GitHub CI/CD          | local git repo + commit on approve                                   |
| Test Runner           | `qaflow-tool/backend/test_runner.py` (Playwright)                    |
| PM Dashboard          | `qaflow-tool/frontend/src/pages/Dashboard.tsx`                       |
| Developer Approval    | `qaflow-tool/frontend/src/pages/BugDetail.tsx`                       |
| WebSocket live updates| `qaflow-tool/backend/main.py /ws` + `qaflow-tool/frontend/src/useLiveData.ts` |

## Resetting between demos

`./start.sh` already resets `buggy-app` to its seeded-bug commit on every boot
so the four bugs reappear and the demo is repeatable.

## Tech stack

- **Frontend**: React 19 · TypeScript · Tailwind CSS · Vite · React Router
- **Backend**: FastAPI · WebSockets · Playwright (async)
- **AI**: Anthropic Claude (optional) · deterministic mock fallback
- **Sandbox**: git + Node http server, no Docker
