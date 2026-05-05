#!/usr/bin/env bash
# QAFLOW AI — stop any service started by start.sh
ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$ROOT/.pids"
for pidfile in "$PID_DIR"/*.pid; do
  [ -f "$pidfile" ] || continue
  pid="$(cat "$pidfile")"
  name="$(basename "$pidfile" .pid)"
  if kill -0 "$pid" 2>/dev/null; then
    echo "[qaflow] stopping $name (pid $pid)"
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
done

# Belt-and-braces — also kill anything bound to our ports
for port in 3000 3001 8000; do
  pids=$(lsof -ti :$port 2>/dev/null || true)
  for pid in $pids; do
    kill "$pid" 2>/dev/null && echo "[qaflow] killed leftover process on :$port (pid $pid)"
  done
done
echo "[qaflow] done."
