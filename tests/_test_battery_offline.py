#!/usr/bin/env python3
"""(б) hermeticity: the battery reaches no real model, and writes no tracked file.

Stage D, the auditor's two witnesses on one battery: the Ollama server log counted 277 POST
/api/embed and 54 GET /api/tags from the test run - fixtures that forgot to stub the embedder,
`doctor` probes, examples started as children - and a before/after snapshot of every tracked file
found two suites writing into the repository (`research/results/draw_divergence.json` rewritten
byte for byte; `nevertwice/api.py` edited and put back in a `finally`).

Now `tests/_env_guard.py` calls `sandbox_guard.forbid_ollama()`: in the test process a socket to
the machine's Ollama port is refused, and every child inherits endpoint variables that point at a
closed port. `tests/test_self_checks.py` compares the (mtime, size) of every tracked file around
each suite and fails the suite that wrote one.

    python tests/_test_battery_offline.py
"""
from __future__ import annotations

import asyncio
import errno
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  - the guard under test
import sandbox_guard  # noqa: E402
import test_self_checks as tsc  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


print("\n- no socket from a test reaches the machine's Ollama -")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    s.connect(("127.0.0.1", sandbox_guard.OLLAMA_PORT))
    refused, why = False, "connected"
except ConnectionRefusedError as e:
    refused, why = True, str(e)
finally:
    s.close()
check("a raw socket to 127.0.0.1:11434 is refused inside a test process", refused, why)
check("... and the refusal names the guard, not a server that happened to be down",
      "sandbox_guard" in why, why)

#: Every check below dials a LISTENING fake "Ollama": the guard's port is pointed at a server this
#: test starts, so a refusal can only come from the guard - with the real server down (CI) a bare
#: refusal from 11434 would prove nothing. The port is restored before the fake-server section.
_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
_listener.bind(("127.0.0.1", 0))
_listener.listen(16)
_fake_port = _listener.getsockname()[1]
_real_port = sandbox_guard.OLLAMA_PORT
sandbox_guard.OLLAMA_PORT = _fake_port
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    check("connect_ex to a LISTENING Ollama port reports a refusal, with this platform's ECONNREFUSED",
          s.connect_ex(("localhost", _fake_port)) == errno.ECONNREFUSED)
    s.close()
    try:
        socket.create_connection(("localhost", _fake_port), timeout=2).close()
        through = "connected"
    except OSError as e:
        through = str(e)
    check("socket.create_connection by name (localhost) is refused by the guard", "sandbox_guard" in through, through)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{_fake_port}/api/tags", timeout=2)
        through = "answered"
    except (urllib.error.URLError, OSError) as e:
        through = str(getattr(e, "reason", e))
    check("a client library going through the socket is refused the same way", "sandbox_guard" in through, through)

    async def _dial() -> str:
        try:
            _r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", _fake_port), 5)
            w.close()
            return "connected"
        except OSError as e:
            return str(e)

    #: The default loop - the proactor on Windows, which connects through ConnectEx and never
    #: calls socket.socket.connect (the auditor's G1: it went straight through the first seam).
    through = asyncio.run(_dial())
    _default = asyncio.new_event_loop()
    _default_name = type(_default).__name__
    _default.close()
    check(f"asyncio on the default loop ({_default_name}) is refused by the guard",
          "sandbox_guard" in through, through)
    _sel = asyncio.SelectorEventLoop()
    try:
        through = _sel.run_until_complete(_dial())
    finally:
        _sel.close()
    check("asyncio on a selector loop is refused by the guard", "sandbox_guard" in through, through)
finally:
    sandbox_guard.OLLAMA_PORT = _real_port
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.connect(("127.0.0.1", _fake_port))
    reach = "connected"
except OSError as e:
    reach = str(e)
finally:
    s.close()
    _listener.close()
check("control: the same listener is reachable once it is not the Ollama port (the refusals were the guard's)",
      reach == "connected", reach)

print("\n- every endpoint name a child reads is closed -")
#: The class, not a list: every OLLAMA_* name that code under nevertwice/, research/, examples/ or
#: tools/ reads from the environment must be in CLOSED_OLLAMA, or a child stand started by a test
#: inherits the live endpoint (the auditor's G2: OLLAMA_BASE_URL, OLLAMA_OPENAI_BASE, OLLAMA_API_BASE
#: were missing). OLLAMA_MODEL names a model, not an endpoint.
_ENV_READ = re.compile(r"""(?:environ(?:\.get|\.setdefault)?\(\s*|environ\[\s*|getenv\(\s*)["'](OLLAMA_[A-Z_]+)["']""")
_NOT_ENDPOINTS = {"OLLAMA_MODEL"}
read_names: dict[str, str] = {}
for sub in ("nevertwice", "research", "examples", "tools"):
    for p in (ROOT / sub).rglob("*.py"):
        if "data" in p.relative_to(ROOT).parts:
            continue
        for name in _ENV_READ.findall(p.read_text(encoding="utf-8", errors="replace")):
            read_names.setdefault(name, p.relative_to(ROOT).as_posix())
unclosed = {n: f for n, f in read_names.items() if n not in sandbox_guard.CLOSED_OLLAMA and n not in _NOT_ENDPOINTS}
check("every OLLAMA_* endpoint name read from the environment is pointed at the closed port",
      not unclosed, str(unclosed))
check("... and the scan sees the names the stands actually read (not vacuous)",
      {"OLLAMA_BASE_URL", "OLLAMA_OPENAI_BASE", "OLLAMA_EMBED_URL", "OLLAMA_HOST"} <= set(read_names),
      str(sorted(read_names)))

print("\n- children inherit a closed endpoint -")
check("the endpoint variables point at a closed port in this process",
      all(os.environ.get(k) == v for k, v in sandbox_guard.CLOSED_OLLAMA.items()),
      str({k: os.environ.get(k) for k in sandbox_guard.CLOSED_OLLAMA}))
child = subprocess.run(
    [sys.executable, "-c",
     "import os, sys, json; sys.path.insert(0, sys.argv[1]); import sandbox_guard; sandbox_guard.isolate(); "
     "sys.path.insert(0, sys.argv[2]); import memory_hook as m; "
     "print(json.dumps({'embed': m.OLLAMA_EMBED_URL, 'tags': m.OLLAMA_TAGS_URL}))",
     str(ROOT), str(ROOT / "nevertwice")],
    capture_output=True, text=True, encoding="utf-8", timeout=300)
try:
    seen = json.loads(child.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    seen = {"error": child.stderr[-300:]}
check("a child that never imports _env_guard (an example, a stand) resolves the closed endpoints",
      ":0/" in str(seen.get("embed")) and ":0/" in str(seen.get("tags")), str(seen))
#: The closed endpoint must fail FAST: a refused loopback connect on Windows costs ~2 s per attempt,
#: which a battery pays on every retry of every call a child makes (measured: 127.0.0.1:9 2037 ms).
_t0 = time.perf_counter()
try:
    urllib.request.urlopen(sandbox_guard.CLOSED_OLLAMA["OLLAMA_EMBED_URL"], timeout=5)
    _how = "answered"
except (urllib.error.URLError, OSError) as e:
    _how = type(e).__name__
_ms = (time.perf_counter() - _t0) * 1000
check("the closed endpoint fails at once, not after a connect timeout (< 500 ms)",
      _how != "answered" and _ms < 500, f"{_how} after {_ms:.0f} ms")
#: Only the address is closed: the pacer and the stand suites' fakes classify a call by its PATH,
#: and a made-up path turned every failed embed into an unclassified call - five stand suites lost
#: their P0(a) invalidity at once (battery e170629).
sys.path.insert(0, str(ROOT / "research"))
import _ollama_pacer as _pacer  # noqa: E402
check("the closed embed URL is still an embed call to the pacer, and the generate URL an LLM call",
      _pacer._is_embed_path(sandbox_guard.CLOSED_OLLAMA["OLLAMA_EMBED_URL"])
      and _pacer._is_llm_path(sandbox_guard.CLOSED_OLLAMA["OLLAMA_URL"]),
      str(sandbox_guard.CLOSED_OLLAMA))
check("... and its host is one the pacer recognises as Ollama (the failure is counted, not passed by)",
      _pacer.is_ollama_host(*_pacer._host_port(sandbox_guard.CLOSED_OLLAMA["OLLAMA_EMBED_URL"])))

print("\n- a fake server a test starts is still reachable -")


class _Ok(http.server.BaseHTTPRequestHandler):
    def do_GET(self):          # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), _Ok)
threading.Thread(target=srv.serve_forever, daemon=True).start()
try:
    body = urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/", timeout=5).read()
except OSError as e:
    body = str(e).encode()
srv.shutdown()
check("a server on a random port answers", body == b"ok", repr(body))

print("\n- the battery's tracked-file guard can fail -")
with tempfile.TemporaryDirectory(prefix="nevertwice_tracked_") as td:
    repo = Path(td)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "a.json").write_text("{}\n", encoding="utf-8")
    (repo / "b.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, env=env)
    files = tsc.tracked_files(repo)
    before = tsc.tracked_state(repo, files)
    time.sleep(0.05)
    (repo / "a.json").write_bytes((repo / "a.json").read_bytes())     # same bytes, new mtime
    check("a byte-for-byte rewrite of a tracked file is caught (the draw_divergence shape)",
          tsc.touched(before, tsc.tracked_state(repo, files)) == ["a.json"],
          str(tsc.touched(before, tsc.tracked_state(repo, files))))
    before = tsc.tracked_state(repo, files)
    (repo / "c.tmp").write_text("scratch\n", encoding="utf-8")        # untracked: not the guard's business
    check("an untracked file is not a tracked write", tsc.touched(before, tsc.tracked_state(repo, files)) == [])

print("\n- and the battery actually applies it to every suite (the wiring, not only the helpers) -")
#: The auditor's D5 on e170629: `changed = []` in test_standalone_suite left this suite green - the
#: helpers were tested, the call site was not. Read the function itself: a snapshot before the
#: suite's subprocess, one after, their difference assigned, and that difference asserted empty.
import ast  # noqa: E402
_fn = next(n for n in ast.walk(ast.parse((HERE / "test_self_checks.py").read_text(encoding="utf-8")))
           if isinstance(n, ast.FunctionDef) and n.name == "test_standalone_suite")
_body = [ast.unparse(s) for s in _fn.body]
_run_at = next((i for i, s in enumerate(_body) if "subprocess.run(" in s), None)
_before_at = next((i for i, s in enumerate(_body) if s.startswith("before = tracked_state(")), None)
_diff = next(((i, s) for i, s in enumerate(_body) if s.startswith("changed = touched(before, tracked_state(")), None)
_assert = next((i for i, s in enumerate(_body) if s.startswith("assert not changed")), None)
check("test_standalone_suite snapshots the tracked files BEFORE it runs the suite",
      _before_at is not None and _run_at is not None and _before_at < _run_at, str((_before_at, _run_at)))
check("... takes the difference AFTER the run, from a fresh snapshot",
      _diff is not None and _run_at is not None and _diff[0] > _run_at, str(_diff))
check("... and asserts that difference is empty", _assert is not None and _diff is not None and _assert > _diff[0],
      str(_assert))

print(f"\nbattery offline: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
