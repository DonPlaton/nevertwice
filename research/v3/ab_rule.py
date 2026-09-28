#!/usr/bin/env python3
"""The recording-vs-raw-forward A/B tolerance rule of PREREG-V3 §4.6 (K87), and the proxy hop microbenchmark.

The A/B runs two raw-forward runs and two recording runs of the same arm on the same smoke haystacks (the harness
itself is TB4). This module decides whether they agree:

* each recording run's per-unit mean must lie inside the range of the two raw-forward runs, widened by
  - logical calls per unit: +/- 5 %;
  - input and output tokens per unit: +/- 10 %;
  - P1 lost_share: +/- 1 pp;
  - items stored per unit: +/- 10 %;
  - wall time per unit: + 10 %, plus 20 ms per call (upper side only: a faster recording run is not a failure);
* client_abandoned must be 0;
* the time to first byte the proxy adds must be p50 <= 20 ms and p95 <= 100 ms (percentiles, never a mean).

Out of tolerance means the proxy is fixed and the A/B repeated before the anchor. The hop microbenchmark measures
the added time to first byte against a local echo upstream, raw-forward and recording; it is informational and
recorded beside the A/B.

    python tests/_test_v3_ab_rule.py
"""
from __future__ import annotations

import importlib.util
import socket
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence

RELATIVE = {"calls": 0.05, "tokens_in": 0.10, "tokens_out": 0.10, "items": 0.10}
LOST_SHARE_PP = 0.01
WALL_REL, WALL_PER_CALL_S = 0.10, 0.020
TTFB_P50_MS, TTFB_P95_MS = 20.0, 100.0


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100) - defined for any non-empty sample, no interpolation."""
    s = sorted(values)
    if not s:
        return float("nan")
    k = max(0, min(len(s) - 1, int(-(-q * len(s) // 100)) - 1))
    return s[k]


def added_over(samples: Sequence[float], baseline: Sequence[float]) -> list[float]:
    """Each sample's excess over the baseline's median, never below 0 - the hop benchmark's rule, and the A/B's added
    time to first byte (Q-AB-2: the recording legs' own-hop samples over the raw legs')."""
    if not baseline:
        raise ValueError("no baseline samples - no median, so no added time")
    base = statistics.median(baseline)
    return [max(0.0, v - base) for v in samples]


def widened(metric: str, raw: Sequence[float], *, calls_per_unit: float = 0.0) -> tuple[float, float]:
    """The range of the raw-forward runs for one metric, widened as §4.6 fixes it."""
    lo, hi = min(raw), max(raw)
    if metric in RELATIVE:
        r = RELATIVE[metric]
        return lo * (1 - r), hi * (1 + r)
    if metric == "lost_share":
        return lo - LOST_SHARE_PP, hi + LOST_SHARE_PP
    if metric == "wall_s":
        return float("-inf"), hi * (1 + WALL_REL) + WALL_PER_CALL_S * calls_per_unit
    raise ValueError(f"no A/B tolerance is fixed for {metric!r}")


def ab_verdict(raw_runs: Sequence[Mapping[str, float]], recording_runs: Sequence[Mapping[str, float]], *,
               client_abandoned: int, ttfb_added_ms: Sequence[float]) -> dict:
    """In tolerance iff every recording run's every metric lies in the widened raw range, no client abandoned,
    and the added time to first byte meets both percentile bounds. Each failure is named."""
    if len(raw_runs) != 2 or len(recording_runs) != 2:
        raise ValueError("§4.6: two raw-forward runs and two recording runs")
    failures, ranges = [], {}
    calls_hi = max(r["calls"] for r in raw_runs)
    for metric in ("calls", "tokens_in", "tokens_out", "lost_share", "items", "wall_s"):
        lo, hi = widened(metric, [r[metric] for r in raw_runs], calls_per_unit=calls_hi)
        ranges[metric] = (lo, hi)
        for i, rec in enumerate(recording_runs, 1):
            if not lo <= rec[metric] <= hi:
                failures.append(f"recording run {i}: {metric} {rec[metric]} outside [{lo:.6g}, {hi:.6g}]")
    if client_abandoned:
        failures.append(f"client_abandoned {client_abandoned} (must be 0)")
    p50, p95 = percentile(ttfb_added_ms, 50), percentile(ttfb_added_ms, 95)
    if not p50 <= TTFB_P50_MS:
        failures.append(f"added time to first byte p50 {p50:.1f} ms > {TTFB_P50_MS} ms")
    if not p95 <= TTFB_P95_MS:
        failures.append(f"added time to first byte p95 {p95:.1f} ms > {TTFB_P95_MS} ms")
    return {"in_tolerance": not failures, "failures": failures, "ranges": ranges,
            "ttfb_added_ms": {"p50": p50, "p95": p95, "n": len(ttfb_added_ms)}}


# ── the hop microbenchmark ──────────────────────────────────────────────

class _Echo:
    """A local upstream that answers every request at once with a small fixed body, keep-alive."""

    BODY = b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}],"model":"deepseek-flash"}'

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    def _conn(self, c):
        buf = bytearray()
        try:
            while True:
                while b"\r\n\r\n" not in buf:
                    chunk = c.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                end = buf.index(b"\r\n\r\n") + 4
                length = 0
                for line in bytes(buf[:end]).split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                while len(buf) < end + length:
                    buf += c.recv(65536)
                del buf[:end + length]
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                          + str(len(self.BODY)).encode() + b"\r\n\r\n" + self.BODY)
        except OSError:
            return
        finally:
            c.close()

    def close(self):
        self.sock.close()


def _ttfb(port: int, token: str | None, n: int) -> list[float]:
    body = b'{"model":"deepseek-flash","messages":[]}'
    auth = f"Authorization: Bearer {token}\r\n" if token else ""
    req = (f"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\n{auth}Content-Type: application/json\r\n"
           f"Content-Length: {len(body)}\r\n\r\n").encode() + body
    s = socket.create_connection(("127.0.0.1", port))
    s.settimeout(10)
    out = []
    try:
        for _ in range(n):
            t0 = time.perf_counter()
            s.sendall(req)
            got = s.recv(65536)
            out.append((time.perf_counter() - t0) * 1000)
            while got.count(b"}") < 3 and not got.endswith(b'"deepseek-flash"}'):
                got += s.recv(65536)
    finally:
        s.close()
    return out


def hop_benchmark(n: int = 200, proxy_module_path: Path | None = None) -> dict:
    """Added time to first byte, in ms, of the proxy hop - raw-forward and recording - against a local echo."""
    path = proxy_module_path or Path(__file__).resolve().parents[1] / "_llm_proxy.py"
    spec = importlib.util.spec_from_file_location("v3_ab_proxy", path)
    P = importlib.util.module_from_spec(spec)
    sys.modules["v3_ab_proxy"] = P
    spec.loader.exec_module(P)
    import tempfile  # noqa: PLC0415
    tmp = Path(tempfile.mkdtemp(prefix="nvt3_hop_"))
    (tmp / "k.env").write_bytes(b"DEEPSEEK_API_KEY=nvt3-hop-benchmark-not-a-key\n")
    echo = _Echo()
    out = {}
    try:
        direct = _ttfb(echo.port, None, n)
        for mode in ("raw", "record"):
            arm = P.ArmConfig(arm="hop", mode=mode, token="nvt3-hop-token", pinned_model="deepseek-flash")
            cfg = P.ProxyConfig(arms=[arm], run_dir=tmp / mode, upstream_host="127.0.0.1", upstream_port=echo.port,
                                upstream_tls=False, control_token="c")
            px = P.Proxy(cfg, P.read_key(tmp / "k.env"), log=lambda m: None)
            ports = px.start()
            via = _ttfb(ports["arms"]["hop"]["write"], "nvt3-hop-token", n)
            px.stop()
            added = added_over(via, direct)
            out[mode] = {"p50_ms": round(percentile(added, 50), 3), "p95_ms": round(percentile(added, 95), 3), "n": n}
        out["direct_p50_ms"] = round(statistics.median(direct), 3)
    finally:
        echo.close()
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(hop_benchmark(), indent=1))
