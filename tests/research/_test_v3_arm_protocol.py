#!/usr/bin/env python3
"""PREREG-V3 TB4.5a (A6): research/v3/arms/base.py - the harness <-> arm-child protocol.

* the child runs a COPY of base.py from a temp directory (ruling Q9 O-b: copied byte-identical into the runs tree, the
  repository on no competitor child's path) - so the module must be stdlib-only, which an AST check also asserts;
* hello / write / end_write / read / counters / bye round-trip; a product's stray print (sys.stdout or raw fd 1) lands
  on stderr and never corrupts the stream;
* malformed JSON, an unknown op, a handler exception, a non-dict result: an ok:false line, and the child goes on;
* the client raises by name: ArmError (ok:false), ArmTimeout (no line in time), ArmDied (stdout closed);
* bye ends the child with exit 0.

    python tests/research/_test_v3_arm_protocol.py
"""
from __future__ import annotations

import ast
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

BASE = ROOT / "research" / "v3" / "arms" / "base.py"
_spec = importlib.util.spec_from_file_location("v3_arm_base", BASE)
B = importlib.util.module_from_spec(_spec)
sys.modules["v3_arm_base"] = B
_spec.loader.exec_module(B)
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def raises(fn, exc, words: str = "") -> bool:
    try:
        fn()
    except exc as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


CHILD = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
import base
class H:
    def hello(self):
        return {"protocol": base.PROTOCOL, "system": "fake"}
    def write(self, item, date=None):
        print("STRAY PRINT FROM THE PRODUCT")
        os.write(1, b"RAW FD1 WRITE\n")
        return {"op_id": item["item_id"], "date": date}
    def end_write(self, die=False):
        if die:
            os._exit(3)
        return {"footprint": {"items": 2}}
    def read(self, qid, query, k):
        if query == "sleep":
            time.sleep(5)
        return {"items": [{"text": "hit for " + query, "rank": 1}], "k": k}
    def counters(self, bad=False):
        if bad:
            return ["not", "a", "dict"]
        raise RuntimeError("boom")
sys.exit(base.serve(H()))
'''

print("\n- self-contained (Q9 O-b: copied into the runs tree) -")
tree = ast.parse(BASE.read_text(encoding="utf-8"))
mods = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])}
check("base.py imports the standard library only", mods <= {"__future__", "json", "os", "queue", "sys", "threading",
                                                             "typing"}, str(sorted(mods)))

with tempfile.TemporaryDirectory(prefix="v3arm_") as td:
    d = Path(td)
    shutil.copyfile(BASE, d / "base.py")
    (d / "child.py").write_text(CHILD, encoding="utf-8")

    def spawn():
        return subprocess.Popen([sys.executable, "-I", "-B", str(d / "child.py"), str(d)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=td)

    print("\n- the round trip -")
    p = spawn()
    c = B.ArmClient(p, default_timeout=30)
    h = c.request("hello")
    check("hello answers with the protocol id", h == {"ok": True, "op": "hello", "protocol": B.PROTOCOL, "system": "fake"},
          str(h))
    w = c.request("write", item={"item_id": "u1:0", "text": "é\u2028x"}, date="2023-05-20")
    check("write round-trips despite a stray print and a raw fd-1 write inside the handler",
          w.get("op_id") == "u1:0" and w.get("date") == "2023-05-20", str(w))
    r = c.request("read", qid="q1", query="where", k=5)
    check("read returns the handler's items", r.get("items") == [{"text": "hit for where", "rank": 1}] and r.get("k") == 5)
    check("a handler exception is ArmError by name, and the child lives on",
          raises(lambda: c.request("counters"), B.ArmError, "RuntimeError: boom") and c.request("hello")["ok"])
    check("a non-dict result is ArmError", raises(lambda: c.request("counters", bad=True), B.ArmError, "not a dict"))
    check("a request field the op does not take is ArmError (TypeError), not a crash",
          raises(lambda: c.request("read", qid="q", query="x", k=1, extra=1), B.ArmError, "TypeError"))
    p.stdin.write(b"this is not json\n")
    p.stdin.flush()
    bad = c._q.get(timeout=10)
    check("a malformed line gets an ok:false answer", b'"ok":false' in bad, bad[:80].decode())
    p.stdin.write(b'{"op": "delete_everything"}\n')
    p.stdin.flush()
    unk = c._q.get(timeout=10)
    check("an unknown op gets an ok:false answer naming it", b"unknown op" in unk and b"delete_everything" in unk)
    check("the client refuses an unknown op before sending it", raises(lambda: c.request("drop"), ValueError, "unknown op"))
    check("no answer in time is ArmTimeout", raises(lambda: c.request("read", qid="q", query="sleep", k=1, timeout=1),
                                                    B.ArmTimeout, "no answer"))
    c._q.get(timeout=10)                                  # drain the late answer to the timed-out read
    rc = c.close()
    err = p.stderr.read().decode("utf-8", "replace")
    check("bye ends the child with exit 0", rc == 0, str(rc))
    check("the product's stray output went to stderr, not into the protocol stream",
          "STRAY PRINT FROM THE PRODUCT" in err and "RAW FD1 WRITE" in err, err[-200:])
    p.stdout.close()
    p.stderr.close()

    print("\n- a child that dies -")
    p2 = spawn()
    c2 = B.ArmClient(p2, default_timeout=30)
    check("a child that exits mid-request is ArmDied, never an empty result",
          raises(lambda: c2.request("end_write", die=True), B.ArmDied, "stdout closed"))
    p2.wait(timeout=30)
    for s in (p2.stdin, p2.stdout, p2.stderr):
        try:
            s.close()
        except OSError:
            pass

print(f"\nv3 arm protocol: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
