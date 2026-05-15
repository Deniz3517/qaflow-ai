#!/usr/bin/env bash
# QAFLOW AI Demo v1.0 — one-click starter
# Boots: buggy-app (3001) + backend (8000) + frontend (3000)

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT/.logs"
PID_DIR="$ROOT/.pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

cleanup() {
  echo ""
  echo "[qaflow] stopping services..."
  for pidfile in "$PID_DIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  done
  exit 0
}
trap cleanup INT TERM

reset_buggy_app() {
  # Off by default so live bug commits + AI fixes survive across reboots.
  # Opt in for a clean demo: `QAFLOW_RESET_BUGGY_APP=1 ./start.sh`.
  if [ "${QAFLOW_RESET_BUGGY_APP:-0}" != "1" ]; then
    echo "[qaflow] keeping current buggy-app state (set QAFLOW_RESET_BUGGY_APP=1 to reset)"
    return
  fi
  echo "[qaflow] resetting buggy-app to seeded-bug state..."
  cd "$ROOT/buggy-app"
  git reset --hard "$(git rev-list --max-parents=0 HEAD)" -q
  cd "$ROOT"
}

start_buggy_app() {
  echo "[qaflow] starting buggy-app on http://localhost:3001"
  cd "$ROOT/buggy-app"
  node server.js > "$LOG_DIR/buggy-app.log" 2>&1 &
  echo $! > "$PID_DIR/buggy-app.pid"
  cd "$ROOT"
}

start_backend() {
  echo "[qaflow] starting backend on http://localhost:8000"
  cd "$ROOT/qaflow-tool/backend"
  if [ ! -d ".venv" ]; then
    echo "[qaflow] creating Python venv (first run)..."
    python3 -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
    .venv/bin/pip install --quiet -r requirements.txt
    .venv/bin/python -m playwright install chromium > /dev/null 2>&1
  fi
  .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 \
    > "$LOG_DIR/backend.log" 2>&1 &
  echo $! > "$PID_DIR/backend.pid"
  cd "$ROOT"
}

start_frontend() {
  echo "[qaflow] starting frontend on http://localhost:3000"
  cd "$ROOT/qaflow-tool/frontend"
  if [ ! -d "node_modules" ]; then
    echo "[qaflow] installing frontend deps (first run)..."
    npm install --cache /tmp/qaflow-npm-cache > /dev/null
  fi
  npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
  echo $! > "$PID_DIR/frontend.pid"
  cd "$ROOT"
}

reset_buggy_app
start_buggy_app
start_backend
start_frontend

sleep 3
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  QAFLOW AI Demo v1.0 — services are up"
echo "════════════════════════════════════════════════════════════"
echo "  ▶ Dashboard      http://localhost:3000"
echo "  ▶ Buggy app      http://localhost:3001"
echo "  ▶ Backend API    http://localhost:8000/api/health"
echo ""
echo "  AI Mode          $([ -n "$ANTHROPIC_API_KEY" ] && echo claude || echo mock)"
echo "  Logs             $LOG_DIR"
echo ""
echo "  Ctrl+C to stop everything."
echo "════════════════════════════════════════════════════════════"

wait
