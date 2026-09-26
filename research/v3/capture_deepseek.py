#!/usr/bin/env python3
"""PREREG-V3 plan step A2.5: DeepSeek response captures through the proxy's raw-forward mode.

Seven fixed calls. Each one's response bytes (status line, headers, body with its SSE and chunked framing) become a
test fixture for the recording mode (A2.6), the Ollama leg (A2.7) and the A/B rule (A2.8):

* /v1 json_object, thinking off; /v1 SSE with include_usage, thinking off; /v1 SSE, thinking explicitly on with a
  small max_tokens; /v1 tool call, non-stream and SSE; /anthropic non-stream; /anthropic SSE. No balance call.
  The prompts are synthetic and fixed here; the manifest records each request body and its sha256.

Two roles in one file, standard library only (the auditor's rulings on Q2-Q4, 2026-09-26):

* The proxy reaches api.deepseek.com through the declared hop (the auditor's R4 ruling): the harness puts
  {"via": {"host": "127.0.0.1", "port": N}} into the proxy's config, N read from the one declared value
  (<runs>\\_config\\network.json, launch.network_via_port) - never from the environment. A real-key run without it
  is refused by name before anything is spawned (C1). The proxy's pre-READY TLS probe (issuer, notAfter) goes into
  the manifest as proxy_upstream_via.
* ``harness`` (the default) runs OUTSIDE the launch contract. Inside one boundary check (native egress witness +
  file-system witness) it starts research/_llm_proxy.py through ``launch.spawn_proxy`` (optional, with its recorded
  reason: its upstream is pinned) and this same file in the ``client`` role through ``launch.spawn``: a fresh unit
  directory, the contract's environment with HTTP(S)_PROXY on the arm's catcher, the script at argv index 1 as its
  only read exception, requirement "required" - the client may talk to the loopback proxy only, any other remote
  is a hit. Then it asks /scan-files whether any file holds the key (0), runs the provider-key regex over every
  file (0), reads the proxy's counters (7 requests, nothing abandoned or refused, no catcher host), shuts the proxy
  down and closes the check. Only after that does it write the fixtures: every response header value except the
  seven kept names replaced by as many ``x`` (framing and offsets unchanged), the names listed in MANIFEST.json.
* ``client`` runs UNDER the contract. It reads {write_port, token, unit} as one JSON line on stdin, sends each call
  to 127.0.0.1 over a raw socket, frames the response itself (Content-Length or chunked) so it never closes a
  connection the proxy is still streaming on, and writes each response's bytes into its unit directory.

The harness never reads the key; only the proxy does.

    python research/v3/capture_deepseek.py --python D:\\Coding\\_nevertwice_polygon\\py314\\python.exe --run r1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets as _secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PROXY_SCRIPT = REPO / "research" / "_llm_proxy.py"
DEFAULT_KEY_FILE = Path(r"D:\Coding\_secrets\deepseek.env")
#: The real secrets directory: a key file under it is the real key (the proxy decides the same way, X2).
SECRETS_ROOT = Path(r"D:\Coding\_secrets")
DEFAULT_OUT = REPO / "tests" / "fixtures" / "v3_captures"
STAND = "a2_5"
ARM = "capture"
MODEL = "deepseek-flash"
#: Q3: the response headers kept verbatim; every other header's value is replaced length-preservingly.
KEEP_HEADERS = frozenset({"content-type", "content-length", "transfer-encoding", "connection", "content-encoding",
                          "date", "cache-control"})
#: B5 (launch._PROVIDER_KEY), over bytes: no capture or run file may hold a provider-key-shaped value.
PROVIDER_KEY = re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")

_TOOL = {"type": "function", "function": {
    "name": "get_weather", "description": "Current weather for a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}

#: The calls, fixed before any response is seen (Q4). Parser fixtures, not measurements: temperature 0.
CALLS: list[dict] = [
    {"name": "v1_json_object", "path": "/v1/chat/completions", "stream": False, "body": {
        "model": MODEL, "temperature": 0, "max_tokens": 64, "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": "Reply with a JSON object only."},
                     {"role": "user", "content": 'Return {"fruit": <one fruit name>} as JSON.'}]}},
    {"name": "v1_sse_usage", "path": "/v1/chat/completions", "stream": True, "body": {
        "model": MODEL, "temperature": 0, "max_tokens": 64, "thinking": {"type": "disabled"}, "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "Name three primary colours, comma-separated."}]}},
    {"name": "v1_sse_thinking_on", "path": "/v1/chat/completions", "stream": True, "body": {
        "model": MODEL, "temperature": 0, "max_tokens": 256, "thinking": {"type": "enabled"}, "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "What is 17 + 25? Answer with the number."}]}},
    {"name": "v1_tool_call", "path": "/v1/chat/completions", "stream": False, "body": {
        "model": MODEL, "temperature": 0, "max_tokens": 64, "thinking": {"type": "disabled"},
        "tools": [_TOOL], "tool_choice": "auto",
        "messages": [{"role": "user", "content": "What is the weather in Oslo? Use the tool."}]}},
    {"name": "v1_tool_call_sse", "path": "/v1/chat/completions", "stream": True, "body": {
        "model": MODEL, "temperature": 0, "max_tokens": 64, "thinking": {"type": "disabled"}, "stream": True,
        "stream_options": {"include_usage": True}, "tools": [_TOOL], "tool_choice": "auto",
        "messages": [{"role": "user", "content": "What is the weather in Oslo? Use the tool."}]}},
    {"name": "anthropic_messages", "path": "/anthropic/v1/messages", "stream": False, "body": {
        "model": MODEL, "max_tokens": 64, "temperature": 0,
        "messages": [{"role": "user", "content": "Say hello in one word."}]}},
    {"name": "anthropic_messages_sse", "path": "/anthropic/v1/messages", "stream": True, "body": {
        "model": MODEL, "max_tokens": 64, "temperature": 0, "stream": True,
        "messages": [{"role": "user", "content": "Say hello in one word."}]}},
]


def body_bytes(call: dict) -> bytes:
    """The exact request body sent for a call (and hashed in the manifest)."""
    return json.dumps(call["body"], separators=(",", ":"), sort_keys=True).encode("utf-8")


# ── the client role (under the contract) ─────────────────────────────────

class FrameError(Exception):
    pass


def read_response(sock: socket.socket, *, timeout: float = 120.0) -> tuple[bytes, list[tuple[float, int]]]:
    """One HTTP/1.1 response, read to its framed end and no further: (bytes, arrivals). Interim 1xx heads stay in
    the bytes. ``arrivals`` is (ms since the read began, bytes) per recv - a stream's real pacing, for replay."""
    sock.settimeout(timeout)
    t0 = time.perf_counter()
    buf = bytearray()
    arrivals: list[tuple[float, int]] = []

    def more() -> None:
        chunk = sock.recv(65536)
        if not chunk:
            raise FrameError("connection closed inside a response")
        arrivals.append((round((time.perf_counter() - t0) * 1000, 3), len(chunk)))
        buf.extend(chunk)

    start = 0
    while True:
        while b"\r\n\r\n" not in buf[start:]:
            more()
        end = buf.index(b"\r\n\r\n", start) + 4
        status_line, *lines = bytes(buf[start:end - 4]).decode("latin-1").split("\r\n")
        parts = status_line.split(" ", 2)
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if not 100 <= status < 200:
            break
        start = end
    headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines)}
    if status in (204, 304):
        return bytes(buf[:end]), arrivals
    if "chunked" in headers.get("transfer-encoding", "").lower():
        pos = end
        while True:
            while b"\r\n" not in buf[pos:]:
                more()
            eol = buf.index(b"\r\n", pos)
            size = int(bytes(buf[pos:eol]).split(b";", 1)[0].strip() or b"0", 16)
            pos = eol + 2
            if size == 0:
                while True:                          # trailers, then the empty line
                    while b"\r\n" not in buf[pos:]:
                        more()
                    eol = buf.index(b"\r\n", pos)
                    empty = eol == pos
                    pos = eol + 2
                    if empty:
                        return bytes(buf[:pos]), arrivals
            while len(buf) < pos + size + 2:
                more()
            if bytes(buf[pos + size:pos + size + 2]) != b"\r\n":
                raise FrameError("chunk not followed by CRLF")
            pos += size + 2
    if "content-length" in headers:
        total = end + int(headers["content-length"])
        while len(buf) < total:
            more()
        return bytes(buf[:total]), arrivals
    while True:                                      # read until close: the server ends it
        try:
            more()
        except FrameError:
            return bytes(buf), arrivals


def build_request(call: dict, *, port: int, unit: str, token: str) -> bytes:
    body = body_bytes(call)
    auth = (f"x-api-key: {token}\r\nanthropic-version: 2023-06-01\r\n" if call["path"].startswith("/anthropic/")
            else f"Authorization: Bearer {token}\r\n")
    accept = "text/event-stream" if call["stream"] else "application/json"
    return (f"POST /u/{unit}{call['path']} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{auth}"
            f"Content-Type: application/json\r\nAccept: {accept}\r\nUser-Agent: nvt3-capture/1\r\n"
            f"Content-Length: {len(body)}\r\n\r\n").encode("latin-1") + body


def client_main() -> int:
    """The spawned child: one connection per call, the response framed and saved, then a summary file and line."""
    cfg = json.loads(sys.stdin.readline() or "{}")
    port, token, unit = int(cfg["write_port"]), str(cfg["token"]), str(cfg["unit"])
    out_dir = Path.cwd()
    summary = []
    for call in CALLS:
        raw, arrivals, error = b"", [], None
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=30)
            try:
                s.sendall(build_request(call, port=port, unit=unit, token=token))
                raw, arrivals = read_response(s)
            finally:
                s.close()
        except (FrameError, OSError, ValueError) as e:
            error = type(e).__name__
        (out_dir / f"{call['name']}.bin").write_bytes(raw)
        status = raw.split(b" ", 2)[1].decode("latin-1") if raw.startswith(b"HTTP/") else None
        summary.append({"name": call["name"], "bytes": len(raw), "status": status, "error": error,
                        "arrivals": arrivals})
    data = json.dumps(summary, sort_keys=True).encode("utf-8")
    tmp = out_dir / "client_summary.json.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, out_dir / "client_summary.json")
    print(f"CAPTURED {sum(1 for x in summary if x['error'] is None)} of {len(summary)}", flush=True)
    return 0 if all(x["error"] is None for x in summary) else 3


# ── the harness role (outside the contract) ───────────────────────────────

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def redact_headers(raw: bytes) -> tuple[bytes, list[str]]:
    """Q3: in every response head (interim ones included), each header whose name is not in KEEP_HEADERS keeps its
    name, colon and leading whitespace; every byte of its value becomes ``x``. The body is untouched and the byte
    count is unchanged. Returns (bytes, the redacted header names, lower-cased, first-seen order, unique)."""
    out = bytearray()
    names: list[str] = []
    pos = 0
    while True:
        if not raw.startswith(b"HTTP/", pos) or b"\r\n\r\n" not in raw[pos:]:
            out += raw[pos:]
            break
        end = raw.index(b"\r\n\r\n", pos)
        lines = raw[pos:end].split(b"\r\n")
        for i, line in enumerate(lines[1:], 1):
            name, sep, value = line.partition(b":")
            key = name.strip().lower().decode("latin-1")
            if sep and key not in KEEP_HEADERS:
                lead = len(value) - len(value.lstrip(b" \t"))
                lines[i] = name + sep + value[:lead] + b"x" * (len(value) - lead)
                if key not in names:
                    names.append(key)
        out += b"\r\n".join(lines) + b"\r\n\r\n"
        status = lines[0].split(b" ", 2)
        pos = end + 4
        if not (len(status) > 1 and status[1].isdigit() and 100 <= int(status[1]) < 200):
            out += raw[pos:]                         # the final head: the rest is the body
            break
    return bytes(out), names


def _real_key(key_file: Path) -> bool:
    """By the written path only - nothing under the secrets directory is opened or resolved here."""
    a = os.path.abspath(os.fspath(key_file)).replace("/", "\\").rstrip("\\").lower()
    r = os.path.abspath(os.fspath(SECRETS_ROOT)).replace("/", "\\").rstrip("\\").lower()
    return a == r or a.startswith(r + "\\")


#: The proxy counters that must be 0 after the seven calls.
ZERO_COUNTERS = ("client_abandoned", "refused_auth", "refused_path", "refused_header", "refused_chunked",
                 "refused_pipelined", "upstream_errors", "connect_refused", "thinking_injected")


def judge(*, spawn_error, rc, scan: dict, counters: dict, check: dict, key_shaped: list, n_calls: int) -> list[str]:
    """Every result check of the harness, each failing one named. Empty = clean (C2)."""
    problems: list[str] = []
    if spawn_error:
        problems.append(f"the contract refused a spawn: {'; '.join(spawn_error)}")
    if rc != 0:
        problems.append(f"the capture client exited with {rc}")
    if scan.get("key_hits") != 0:
        problems.append("the proxy did not report key_hits 0 over the capture and run files")
    if counters.get("requests") != n_calls:
        problems.append(f"the proxy saw {counters.get('requests')} requests, not {n_calls}")
    for name in ZERO_COUNTERS:
        if counters.get(name):
            problems.append(f"proxy counter {name} = {counters.get(name)}")
    if counters.get("catcher_hosts"):
        problems.append(f"the catcher saw {len(counters['catcher_hosts'])} host(s)")
    native_rec = check.get("native") or {}
    if native_rec.get("hits") != 0:
        problems.append(f"the native witness counted {native_rec.get('hits')} egress hit(s)")
    if not native_rec.get("complete"):
        problems.append("the native witness's check is not complete")
    if (check.get("fs") or {}).get("fs_hits") != 0:
        problems.append(f"the file-system witness counted {(check.get('fs') or {}).get('fs_hits')} change(s) in the watched set")
    if key_shaped:
        problems.append(f"{len(key_shaped)} file(s) hold a provider-key-shaped value")
    return problems


def upstream_via(pdir: Path, via_port: int | None) -> dict | None:
    """The proxy's pre-READY probe, as recorded in its run directory: hop, issuer (O, CN), notAfter, TLS version."""
    f = pdir / "upstream_tls.jsonl"
    if via_port is None or not f.is_file():
        return None
    last = json.loads(f.read_bytes().decode("utf-8").splitlines()[-1])
    return {"host": "127.0.0.1", "port": via_port, **{k: last.get(k) for k in ("issuer_o", "issuer_cn", "not_after",
                                                                                "tls_version")}}


def _control(port: int, token: str, path: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    s = socket.create_connection(("127.0.0.1", port), timeout=30)
    try:
        s.sendall(f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {token}\r\n"
                  f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n".encode("latin-1") + data)
        out = bytearray()
        s.settimeout(30)
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
    finally:
        s.close()
    head, _, payload = bytes(out).partition(b"\r\n\r\n")
    if not head.startswith(b"HTTP/1.1 200"):
        raise RuntimeError(f"proxy control {path} did not answer 200")
    return json.loads(payload)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    for attempt in range(10):                        # WP16: a scanner may hold the file for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, path)


def run_captures(c, python: Path, *, key_file: Path, run: str, out_dir: Path, parent_env, launch,
                 via_port: int | None = None, test_upstream: dict | None = None, native=None, fs=None,
                 client_timeout: float = 600.0) -> dict:
    """The whole step. Returns the manifest (also written to ``out_dir``); ``manifest["problems"]`` empty = clean.
    ``via_port`` is the declared hop (R4). ``test_upstream`` (tests only) goes into the proxy config, which the proxy
    accepts only when the key file is not under the real secrets directory (X2). ``native``/``fs`` default to the
    real witnesses."""
    L = launch
    calls = CALLS                                    # the client sends exactly these
    if via_port is None and _real_key(key_file):
        raise RuntimeError("(C1) a real-key run needs the declared hop (network.json): direct DNS fails here (R4)")
    if via_port is not None and test_upstream is not None:
        raise ValueError("the hop serves only the TLS upstream; a test upstream cannot be combined with it")
    pdir = c.runs_root / STAND / run / "_proxy"
    if pdir.exists():
        raise RuntimeError("this run label was used before: pick a fresh --run")
    pdir.mkdir(parents=True)
    proxy_unit = L.make_unit_dirs(c, STAND, run, "proxy", "p1")
    client_unit = L.make_unit_dirs(c, STAND, run, ARM, "c1")
    cfg = {"arms": [{"arm": ARM, "mode": "raw"}], "run_dir": str(pdir), "thinking_branch": "unset",
           "scan_roots": [str(client_unit.cwd)]}
    if test_upstream is not None:
        cfg["upstream"] = test_upstream
    if via_port is not None:
        cfg["via"] = {"host": "127.0.0.1", "port": via_port}
    cfg_path = pdir / "proxy_config.json"
    cfg_path.write_bytes(json.dumps(cfg, sort_keys=True).encode("utf-8"))
    token, control_token = L.new_token(ARM), "ctl-" + _secrets.token_hex(24)
    W = L.Witnesses(c, native=native if native is not None else L.NativeEgressWitness(),
                    fs=fs if fs is not None else L.FsWitness(L.watched_set(c)))
    check_id = f"{STAND}-{run}"
    W.begin_check(check_id)
    rc: int | None = None
    c_out = c_err = ""
    scan: dict = {}
    counters: dict = {}
    spawn_error = None
    proxy = None
    try:
        proxy, ports = L.spawn_proxy(c, python, script=PROXY_SCRIPT, config_path=cfg_path, key_file=key_file,
                                     stdin_secrets={"tokens": {ARM: token}, "control_token": control_token},
                                     unit=proxy_unit, parent_env=parent_env, witnesses=W)
        ctl = ports["control"]
        try:
            arm_ports = ports["arms"][ARM]
            catcher_url = f"http://127.0.0.1:{arm_ports['catcher']}"
            env = L.build_env(c, parent_env=parent_env, unit=client_unit, path_dirs=[Path(python).parent],
                              declared={}, catcher_url=catcher_url)
            script = Path(__file__).resolve()
            child = L.spawn(c, [os.fspath(python), os.fspath(script), "client"], env=env, cwd=client_unit.cwd,
                            record={"role": "capture", "stand": None, "run": run, "arm": ARM, "unit": "c1"},
                            parent_env=parent_env, catcher_url=catcher_url, argv_exception={1: script},
                            witnesses=W, requirement="required",
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            child.process.stdin.write((json.dumps({"write_port": arm_ports["write"], "token": token, "unit": "c1"})
                                       + "\n").encode("utf-8"))
            child.process.stdin.close()
            try:
                rc = child.process.wait(timeout=client_timeout)
            except subprocess.TimeoutExpired:
                child.kill_tree()
            c_out = child.process.stdout.read().decode("utf-8", "replace").strip()
            c_err = child.process.stderr.read().decode("utf-8", "replace")
            files = [p for p in (*client_unit.cwd.iterdir(), *pdir.iterdir()) if p.is_file()]
            scan = _control(ctl, control_token, "/scan-files", "POST", {"paths": [str(p) for p in files]})
            counters = _control(ctl, control_token, "/counters")[ARM]
        finally:
            try:
                _control(ctl, control_token, "/shutdown", "POST", {})
            except (OSError, RuntimeError, ValueError):
                pass
    except L.ContractViolation as e:
        spawn_error = e.reasons
    finally:
        if proxy is not None:
            try:
                proxy.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proxy.kill_tree()
        check = W.end_check(check_id)                # before anything is written into the repository
    native_rec = check.get("native") or {}
    key_shaped = [p.name for d in (client_unit.cwd, pdir) for p in d.iterdir()
                  if p.is_file() and PROVIDER_KEY.search(p.read_bytes())]
    problems = judge(spawn_error=spawn_error, rc=rc, scan=scan, counters=counters, check=check,
                     key_shaped=key_shaped, n_calls=len(calls))
    summary_path = client_unit.cwd / "client_summary.json"
    by_name = {x["name"]: x for x in json.loads(summary_path.read_bytes())} if summary_path.is_file() else {}
    entries = []
    if not key_shaped and scan.get("key_hits") == 0:
        out_dir.mkdir(parents=True, exist_ok=True)
        for call in calls:
            src = client_unit.cwd / f"{call['name']}.bin"
            raw = src.read_bytes() if src.is_file() else b""
            info = by_name.get(call["name"], {})
            if not raw or info.get("error"):
                problems.append(f"{call['name']}: no complete response ({info.get('error')})")
            clean, redacted = redact_headers(raw)
            _atomic_write(out_dir / f"{call['name']}.bin", clean)
            entries.append({"name": call["name"], "file": f"{call['name']}.bin", "sha256": _sha256(clean),
                            "bytes": len(clean), "http_status": info.get("status"), "endpoint": call["path"],
                            "stream": call["stream"], "request_body": call["body"],
                            "request_sha256": _sha256(body_bytes(call)), "redacted_headers": redacted,
                            "arrivals": info.get("arrivals", [])})
    manifest = {
        "step": "A2.5", "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "run": run, "model": MODEL,
        "proxy_mode": "raw", "proxy_script_sha256": _sha256(PROXY_SCRIPT.read_bytes()),
        "capture_script_sha256": _sha256(Path(__file__).resolve().read_bytes()),
        "python": {"path": os.fspath(python), "sha256": _sha256(Path(python).read_bytes())},
        "test_upstream": test_upstream is not None, "check_id": check_id,
        "proxy_upstream_via": upstream_via(pdir, via_port),
        "check": {"complete": check.get("complete"), "native_hits": native_rec.get("hits"),
                  "native_complete": native_rec.get("complete"), "fs_hits": (check.get("fs") or {}).get("fs_hits")},
        "scan_key_hits": scan.get("key_hits"), "provider_key_shaped_files": len(key_shaped),
        "proxy_counters": {k: v for k, v in counters.items() if k != "catcher_hosts"},
        "catcher_hosts": len(counters.get("catcher_hosts") or []), "client_rc": rc, "client_stdout": c_out,
        "client_stderr_tail": c_err[-400:], "kept_headers": sorted(KEEP_HEADERS), "captures": entries,
        "problems": problems,
    }
    if entries:
        _atomic_write(out_dir / "MANIFEST.json",
                      (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return manifest


def _load_launch():
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("v3_launch", HERE / "launch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v3_launch"] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None, *, _launch=None, _contract=None, _run=None) -> int:
    """``_launch``/``_contract``/``_run`` are test seams; the real run uses launch.py, Contract.default() and
    run_captures, with the hop from the contract's network.json."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["client"]:
        return client_main()
    ap = argparse.ArgumentParser(description="A2.5: DeepSeek captures through the raw-forward proxy")
    ap.add_argument("--python", required=True, help="the polygon-local interpreter for the proxy and the client")
    ap.add_argument("--run", required=True, help="a fresh run label, [A-Za-z0-9._-]")
    ap.add_argument("--key-file", default=str(DEFAULT_KEY_FILE))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    L = _launch or _load_launch()
    c = _contract or L.Contract.default()
    manifest = (_run or run_captures)(c, Path(args.python), key_file=Path(args.key_file), run=args.run,
                                      out_dir=Path(args.out), parent_env=os.environ, launch=L,
                                      via_port=L.network_via_port(c))
    print(json.dumps({k: manifest.get(k) for k in ("check", "proxy_upstream_via", "scan_key_hits",
                                                   "provider_key_shaped_files", "catcher_hosts", "client_rc",
                                                   "problems")}))
    print(json.dumps([{k: e[k] for k in ("name", "http_status", "bytes", "sha256", "redacted_headers")}
                      for e in manifest.get("captures", [])], indent=1))
    return 0 if not manifest["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
