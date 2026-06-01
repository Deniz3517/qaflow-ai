"""Performance runner — drive throughput + latency tests against a target.

Two modes:
  - stdlib   asyncio + aiohttp-like stdlib http client. No external deps,
             good for smoke-level load (≤ 200 RPS, ≤ 5 min).
  - locust   shells out to `locust` if installed in the bundle's .venv;
             reads a user-provided `locustfile.py` and produces a json
             report. Use this for serious load (>200 RPS).

Result shape (both modes):
  {{
    "mode": "stdlib" ,
    "target": "<url>",
    "duration_s": float,
    "requests_total": int,
    "requests_per_sec": float,
    "errors": int,
    "p50_ms": int, "p95_ms": int, "p99_ms": int,
    "log_tail": str,
  }}
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Stdlib mode — async event loop hammering urllib via run_in_executor
# ---------------------------------------------------------------------------

async def _stdlib_worker(
    url: str, method: str, body: bytes | None, headers: dict,
    deadline: float, latencies: list[float], errors: list[int],
) -> None:
    while time.time() < deadline:
        t0 = time.time()
        try:
            req = urllib.request.Request(url, method=method.upper(),
                                          data=body, headers=headers)
            # Run blocking call in executor so each worker doesn't serialize
            # network I/O on the event loop.
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: _do_request(req),
            )
            latencies.append((time.time() - t0) * 1000.0)
        except Exception:
            errors.append(1)


def _do_request(req: urllib.request.Request) -> None:
    with urllib.request.urlopen(req, timeout=10) as r:
        # Drain at most 64 KB.
        r.read(65536)


async def _run_stdlib_async(
    url: str, method: str, body: dict | None, headers: dict,
    concurrency: int, duration_s: int,
    on_line: Callable[[str], None],
) -> dict:
    body_bytes = None
    final_headers = {**headers, "User-Agent": "QAFLOW-perf-runner/1.0"}
    if body is not None:
        body_bytes = json.dumps(body).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/json")

    started = time.time()
    deadline = started + max(1, duration_s)
    latencies: list[float] = []
    errors: list[int] = []

    on_line(f"[stdlib] starting concurrency={concurrency} duration={duration_s}s url={url}")

    workers = [
        asyncio.create_task(_stdlib_worker(
            url, method, body_bytes, final_headers, deadline, latencies, errors,
        ))
        for _ in range(max(1, concurrency))
    ]
    # Heartbeat log every 2s.
    last_beat = time.time()
    while True:
        await asyncio.sleep(0.5)
        now = time.time()
        if now - last_beat >= 2.0:
            on_line(f"[stdlib] elapsed={int(now-started)}s reqs={len(latencies)} errs={len(errors)}")
            last_beat = now
        if all(w.done() for w in workers) or now >= deadline + 2:
            break
    for w in workers:
        if not w.done():
            w.cancel()
    duration = time.time() - started

    return _summarize("stdlib", url, duration, latencies, len(errors))


def _summarize(mode: str, url: str, duration: float,
               latencies: list[float], errors: int) -> dict:
    total = len(latencies)
    rps = total / duration if duration > 0 else 0.0
    if latencies:
        sorted_ms = sorted(latencies)
        def _pct(p: float) -> int:
            k = max(0, min(len(sorted_ms) - 1, int(round(p * (len(sorted_ms) - 1)))))
            return int(sorted_ms[k])
        return {
            "mode": mode, "target": url,
            "duration_s": round(duration, 1),
            "requests_total": total,
            "requests_per_sec": round(rps, 1),
            "errors": errors,
            "p50_ms": _pct(0.50),
            "p95_ms": _pct(0.95),
            "p99_ms": _pct(0.99),
            "p_max_ms": int(max(latencies)),
            "p_min_ms": int(min(latencies)),
            "mean_ms": int(statistics.mean(latencies)),
            "stdev_ms": int(statistics.pstdev(latencies)) if len(latencies) > 1 else 0,
        }
    return {
        "mode": mode, "target": url,
        "duration_s": round(duration, 1),
        "requests_total": 0, "requests_per_sec": 0.0,
        "errors": errors,
        "p50_ms": None, "p95_ms": None, "p99_ms": None,
    }


def run_stdlib(
    url: str, *,
    method: str = "GET",
    body: dict | None = None,
    headers: dict | None = None,
    concurrency: int = 10,
    duration_s: int = 30,
    on_line: Callable[[str], None] | None = None,
) -> dict:
    """Synchronous entry point — wraps the async stdlib loader."""
    cb = on_line or (lambda _l: None)
    result = asyncio.run(_run_stdlib_async(
        url=url, method=method, body=body, headers=headers or {},
        concurrency=concurrency, duration_s=duration_s,
        on_line=cb,
    ))
    return result


# ---------------------------------------------------------------------------
# Locust mode — shell out to bundle .venv's locust
# ---------------------------------------------------------------------------

def run_locust(
    bundle_root: str | Path,
    locustfile: str = "locustfile.py",
    target: str = "",
    users: int = 50,
    spawn_rate: int = 5,
    duration_s: int = 60,
    on_line: Callable[[str], None] | None = None,
) -> dict:
    """Run locust against a user-provided locustfile in the bundle."""
    cb = on_line or (lambda _l: None)
    bundle = Path(bundle_root)
    locust_bin = bundle / ".venv" / "bin" / "locust"
    file_path = bundle / locustfile
    if not locust_bin.exists():
        return {
            "mode": "locust", "target": target,
            "error": f"locust not installed at {locust_bin}",
            "install_required": True,
        }
    if not file_path.exists():
        return {
            "mode": "locust", "target": target,
            "error": f"locustfile missing at {file_path}",
        }

    started = time.time()
    json_report = bundle / ".qaflow_locust_report.json"
    if json_report.exists():
        json_report.unlink()

    cmd = [
        str(locust_bin), "-f", str(file_path),
        "--headless",
        "-u", str(users),
        "-r", str(spawn_rate),
        "-t", f"{duration_s}s",
        "--host", target,
        "--json",
    ]
    proc = subprocess.Popen(
        cmd, cwd=bundle, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    log_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        log_lines.append(line)
        cb(line)
    proc.wait()
    duration = time.time() - started

    summary = _parse_locust_log("\n".join(log_lines))
    summary.update({
        "mode": "locust", "target": target,
        "duration_s": round(duration, 1),
        "exit_code": proc.returncode,
        "log_tail": "\n".join(log_lines[-80:]),
    })
    return summary


_RX_LOC_TOTAL = re.compile(r"Aggregated\s+(\d+)\s+(\d+)\(([0-9.]+)%\)\s+(\d+)\s+(\d+)\s+(\d+)\s+\|")


def _parse_locust_log(log: str) -> dict:
    """Best-effort parse of locust's stdout final aggregated row.

    Locust output schema changes between versions — when JSON output is
    available (--json), prefer that path. This regex covers the v2 text
    summary as a fallback.
    """
    m = _RX_LOC_TOTAL.search(log)
    if not m:
        return {"requests_total": None, "errors": None,
                "p50_ms": None, "p95_ms": None}
    return {
        "requests_total": int(m.group(1)),
        "errors": int(m.group(2)),
        "error_pct": float(m.group(3)),
        "p50_ms": int(m.group(4)),
        "p95_ms": int(m.group(5)),
        "p99_ms": None,
        "mean_ms": int(m.group(6)),
    }
