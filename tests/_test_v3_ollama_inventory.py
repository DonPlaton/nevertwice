#!/usr/bin/env python3
"""PREREG-V3 plan step A3.h: research/v3/ollama_inventory.py against a fake Ollama on loopback, offline.

* only GET, only /api/version and /api/tags, only 127.0.0.1 - any other method, path or host is refused (o1);
* a pin is the first 12 hex digits of the model's digest (`ollama list` shows those) - never its last 12 (o2);
* a pinned model that is missing, or present with another digest, is a named problem (o3); J2 is an ordered list,
  so a missing J2 candidate is recorded, not a problem, while one present with another digest is;
* the full inventory goes to the runs tree only; the record names the pins' results.

    python tests/_test_v3_ollama_inventory.py
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


O = _load("v3_ollama_inventory", ROOT / "research" / "v3" / "ollama_inventory.py")

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn, words: str = "") -> bool:
    try:
        fn()
    except O.InventoryRefused as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


def model(name: str, digest12: str, tail: str = "0" * 52) -> dict:
    return {"name": name, "model": name, "digest": digest12 + tail, "size": 1, "details": {"family": "x"}}


GOOD = [model("qwen3.6:27b", "a50eda8ed977"), model("glm-4.7-flash:q4_K_M", "d1a8a26252f1"),
        model("gemma3n:e4b", "15cb39fd9394"), model("hermes3-8b:latest", "1" * 12), model("llama3:latest", "2" * 12),
        model("qwen3-coder:30b", "06c1097efce0"), model("bge-m3:latest", "3" * 12), model("owner-private:7b", "4" * 12)]
SEEN: list = []


def serve(tags: list):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            SEEN.append(("GET", self.path))
            self._send({"version": "0.99.0"} if self.path == "/api/version" else {"models": tags})

        def do_POST(self):
            SEEN.append(("POST", self.path))
            self._send({})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


TMP = Path(tempfile.mkdtemp(prefix="nvt3_ollinv_"))
print("\n- the requests (o1) -")
check("the tool may ask Ollama only GET /api/version and GET /api/tags, on 127.0.0.1",
      O.ALLOWED == {("GET", "/api/version"), ("GET", "/api/tags")} and O.HOST == "127.0.0.1")
for label, (method, path, host) in (("a POST", ("POST", "/api/tags", "127.0.0.1")),
                                    ("another path", ("GET", "/api/ps", "127.0.0.1")),
                                    ("a generate call", ("POST", "/api/generate", "127.0.0.1")),
                                    ("another host", ("GET", "/api/tags", "10.0.0.5"))):
    check(f"o1: {label} is refused before any byte is sent",
          refused(lambda m=method, p=path, h=host: O.request(m, p, host=h, port=1), "refused"))

print("\n- the pins (o2, o3) -")
check("the pins are the PREREG's: J1, the J2 list in order, the S6L writer, the D1 base",
      O.PINS["qwen3.6:27b"]["digest12"] == "a50eda8ed977" and O.PINS["qwen3-coder:30b"]["digest12"] == "06c1097efce0"
      and [n for n, p in O.PINS.items() if p["role"].startswith("J2")] == ["glm-4.7-flash:q4_K_M", "gemma3n:e4b",
                                                                          "hermes3-8b", "llama3"]
      and O.PINS["bge-m3"]["role"] == "D1 base" and O.PINS["bge-m3"]["digest12"] is None)
res, probs = O.check_pins(GOOD)
check("a store holding every pin at its digest has no problem; hermes3-8b and llama3 get their digests recorded",
      probs == [] and res["hermes3-8b"]["found"] == "hermes3-8b:latest" and res["hermes3-8b"]["digest12"] == "1" * 12
      and res["llama3"]["digest12"] == "2" * 12 and res["bge-m3"]["present"], str((probs, res.get("hermes3-8b"))))
wrong_end = [model("qwen3.6:27b", "0" * 12, tail="0" * 40 + "a50eda8ed977"), *GOOD[1:]]
check("o2: a digest whose LAST 12 digits are the pin is not the pin",
      any(p.startswith("qwen3.6:27b (J1,") and "a50eda8ed977" in p for p in O.check_pins(wrong_end)[1]),
      str(O.check_pins(wrong_end)[1]))
for gone, role in (("qwen3.6:27b", "J1"), ("qwen3-coder:30b", "S6L"), ("bge-m3", "D1 base")):
    store = [m for m in GOOD if not m["name"].startswith(gone)]
    check(f"o3: a missing {gone} ({role}) is a named problem",
          any(p.startswith(f"{gone} ({role}") and "missing" in p for p in O.check_pins(store)[1]), str(O.check_pins(store)[1]))
no_glm = [m for m in GOOD if not m["name"].startswith("glm")]
res2, probs2 = O.check_pins(no_glm)
check("a missing J2 candidate is recorded, not a problem (J2 is an ordered list)",
      probs2 == [] and res2["glm-4.7-flash:q4_K_M"]["present"] is False, str(probs2))
glm_other = [model("glm-4.7-flash:q4_K_M", "9" * 12) if m["name"].startswith("glm") else m for m in GOOD]
check("a J2 candidate present with another digest is a named problem",
      any(p.startswith("glm-4.7-flash:q4_K_M (J2-1,") for p in O.check_pins(glm_other)[1]))
check("a name without a tag matches only its :latest, never another tag",
      O.check_pins([m for m in GOOD if not m["name"].startswith("llama3")] + [model("llama3:70b", "5" * 12)])[0]["llama3"]["present"]
      is False)

print("\n- a whole run against a fake Ollama -")
srv = serve(GOOD)
runs = TMP / "runs"
rec = O.run_inventory(runs, run="o1", port=srv.server_address[1])
check("the run asks exactly GET /api/version and GET /api/tags, nothing else",
      SEEN == [("GET", "/api/version"), ("GET", "/api/tags")], str(SEEN))
tags_f = runs / "_inventory" / "ollama" / "o1" / "tags.json"
full = json.loads(tags_f.read_bytes()) if tags_f.is_file() else {"models": []}
check("the owner's full inventory is kept in the runs tree only, byte for byte as Ollama gave it",
      [m["name"] for m in full["models"]] == [m["name"] for m in GOOD])
check("the record carries the version, the pins' results and no problem",
      rec["version"] == "0.99.0" and rec["problems"] == [] and rec["pins"]["qwen3.6:27b"]["digest12"] == "a50eda8ed977"
      and json.loads((runs / "_inventory" / "ollama" / "o1" / "record.json").read_bytes())["problems"] == [])
check("the record names no model outside the pins (the inventory stays in tags.json)",
      "owner-private:7b" not in json.dumps(rec))
check("a used run label is refused", refused(lambda: O.run_inventory(runs, run="o1", port=srv.server_address[1]), "used before"))
srv.shutdown()
rec_down = O.run_inventory(runs, run="o2", port=1)
check("an Ollama that does not answer is a named problem, and the record is still written",
      any("did not answer" in p for p in rec_down["problems"])
      and (runs / "_inventory" / "ollama" / "o2" / "record.json").is_file(), str(rec_down["problems"]))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 ollama inventory: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
