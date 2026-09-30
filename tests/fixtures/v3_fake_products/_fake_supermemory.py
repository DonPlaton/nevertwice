"""A FAKE supermemory-server, run as a child process by the v3 server suites (research/v3/sm_server.py): the port
and the data directory from the environment the spawn declares (SUPERMEMORY_PORT, SUPERMEMORY_DATA_DIR), a banner on
stdout whose "api key" row prints the key (sm_bundle's fact key_banner), the key stored in <data>/api-key (the
product's own file, sm_bundle's key_file), 503 "Service is starting" while it boots (the fact serve), then 200.
CTRL_BREAK (SIGTERM off Windows) stops it with exit 0.

Knobs: a JSON file named by NVT3_FAKE_SM - key (the value it prints and stores), boot_503_s (how long it answers 503),
stderr_key (the key printed once more on stderr, which the spawn merges into stdout), exit_at_boot (an exit code right
after the banner, before it serves), ignore_break (the stop signal ignored: only a tree kill ends it), child (a
sleeping child process in its tree), child_ignores_break (that child ignores the stop signal too: it outlives the
server's own exit).

This is NOT the product: nothing here is derived from the vendor's code beyond the facts named above.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STOP_SIGNAL = getattr(signal, "SIGBREAK", signal.SIGTERM)


def _knobs() -> dict:
    name = os.environ.get("NVT3_FAKE_SM")
    return json.loads(Path(name).read_text(encoding="utf-8")) if name else {}


def main() -> int:
    k = _knobs()
    port = int(os.environ.get("PORT") or os.environ["SUPERMEMORY_PORT"])
    data = Path(os.environ["SUPERMEMORY_DATA_DIR"])
    key = k.get("key", "sm_" + "F" * 30)
    stop = threading.Event()
    signal.signal(STOP_SIGNAL, signal.SIG_IGN if k.get("ignore_break") else (lambda *a: stop.set()))
    data.mkdir(parents=True, exist_ok=True)
    (data / "api-key").write_text(key, encoding="utf-8")
    print("+-----------+----------------------------------+", flush=True)
    print(f"| url       | http://127.0.0.1:{port} |", flush=True)
    print(f"| api key   | {key} |", flush=True)
    print("+-----------+----------------------------------+", flush=True)
    if k.get("stderr_key"):
        print(f"warn: the key {key} is shown once", file=sys.stderr, flush=True)
    if k.get("exit_at_boot") is not None:
        return int(k["exit_at_boot"])
    kid = None
    if k.get("child"):
        code = ("import signal, time\n"
                + ("signal.signal(getattr(signal, 'SIGBREAK', signal.SIGTERM), signal.SIG_IGN)\n"
                   if k.get("child_ignores_break") else "")
                + "time.sleep(600)\n")
        kid = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        print(f"child {kid.pid}", flush=True)
    t0 = time.monotonic()
    boot = float(k.get("boot_503_s", 0.0))

    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            starting = time.monotonic() - t0 < boot
            body = b"Service is starting" if starting else b'{"ok":true}'
            self.send_response(503 if starting else 200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            print("GET " + (args[0] if args else ""), flush=True)

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    while not stop.wait(0.05):
        pass
    srv.shutdown()
    print("stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
