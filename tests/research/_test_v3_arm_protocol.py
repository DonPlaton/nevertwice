#!/usr/bin/env python3
"""PREREG-V3 TB4.5a (A6): research/v3/arms/base.py - the harness <-> arm-child protocol, with the auditor's F1-F4.

* the child runs a COPY of base.py from a temp directory (ruling Q9 O-b), so the module must be stdlib-only (AST);
* round trip of every op; a product's stray print and raw fd-1 write - inside a handler AND while the handler is being
  built (F2) - land on stderr, never in the stream;
* F1: a request that times out poisons the client - the next request raises ArmPoisoned instead of taking the late
  answer - and an answer carrying another request's id, or a line that is not a JSON object, is a protocol error by name;
* F3: with PYTHONUTF8 / PYTHONIOENCODING removed, non-ASCII text (Cyrillic, CJK, an emoji, U+2028) round-trips exactly;
  a request that is not UTF-8 and an answer holding a lone surrogate are ok:false by name, and the child lives on;
* F4: a handler result that would overwrite ok / op / id is ok:false;
* an error text is capped at ERROR_MAX; "bye" ends the child even with its stdin still open; a child that dies
  mid-request is ArmDied. Every child is killed in a finally block.

    python tests/research/_test_v3_arm_protocol.py
"""
from __future__ import annotations

import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
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


def safely(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a crash is a named FAIL of the row that reads it
        return default if default is not None else repr(e)


CHILD = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
import base
MODE = sys.argv[2]
class H:
    def __init__(self):
        if MODE == "noisy-build":
            print("PRINT WHILE BUILDING THE HANDLER")
            os.write(1, b"RAW FD1 WHILE BUILDING\n")
    def hello(self):
        return {"protocol": base.PROTOCOL, "system": "fake"}
    def write(self, item, date=None):
        print("STRAY PRINT FROM THE PRODUCT")
        os.write(1, b"RAW FD1 WRITE\n")
        return {"op_id": item["item_id"], "echo": item.get("text"), "date": date}
    def end_write(self, die=False):
        if die:
            os._exit(3)
        return {"footprint": {"items": 2}}
    def read(self, qid, query, k):
        if query == "sleep":
            time.sleep(3)
            return {"answer": "slept 3"}
        if query == "hang":
            time.sleep(120)
            return {"answer": "slept 120"}
        return {"items": [{"text": "hit for " + query, "rank": 1}], "k": k}
    def counters(self, mode="boom"):
        if mode == "not-dict":
            return ["not", "a", "dict"]
        if mode == "surrogate":
            return {"text": "\udc98"}
        if mode == "reserved":
            return {"ok": False, "x": 1}
        if mode == "long":
            raise RuntimeError("x" * 5000)
        raise RuntimeError("boom")
sys.exit(base.main_with(H))
'''
LIAR = r'''
import sys
for line in sys.stdin.buffer:
    mode = sys.argv[1]
    if mode == "wrong-id":
        sys.stdout.write('{"id": 999, "ok": true, "op": "hello"}\n')
    elif mode == "garbage":
        sys.stdout.write("this is not json\n")
    else:
        sys.stdout.write('[1, 2, 3]\n')
    sys.stdout.flush()
'''

print("\n- self-contained (Q9 O-b: copied into the runs tree) -")
tree = ast.parse(BASE.read_text(encoding="utf-8"))
mods = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])}
check("base.py imports the standard library only",
      mods <= {"__future__", "io", "json", "os", "queue", "sys", "threading", "typing"}, str(sorted(mods)))

children: list = []
with tempfile.TemporaryDirectory(prefix="v3arm_") as td:
    d = Path(td)
    shutil.copyfile(BASE, d / "base.py")
    (d / "child.py").write_text(CHILD, encoding="utf-8")
    (d / "liar.py").write_text(LIAR, encoding="utf-8")
    ENV = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}

    def spawn(mode="plain", script="child.py", args=None):
        argv = [sys.executable, "-I", "-B", str(d / script)] + (args if args is not None else [str(d), mode])
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=open(d / f"err_{len(children)}.txt", "wb"), cwd=td, env=ENV)
        children.append(p)
        return p, d / f"err_{len(children) - 1}.txt"

    try:
        print("\n- the round trip, stray output, F4, error cap -")
        p, errf = spawn()
        c = B.ArmClient(p, default_timeout=30)
        h = safely(lambda: c.request("hello"), {})
        check("hello answers with the protocol id and the request's id",
              h.get("protocol") == B.PROTOCOL and h.get("id") == 1 and h.get("ok") is True, str(h))
        w = safely(lambda: c.request("write", item={"item_id": "u1:0", "text": "t"}, date="2023-05-20"), {})
        check("write round-trips despite a stray print and a raw fd-1 write inside the handler",
              w.get("op_id") == "u1:0" and w.get("date") == "2023-05-20", str(w))
        r = safely(lambda: c.request("read", qid="q1", query="where", k=5), {})
        check("read returns the handler's items", r.get("items") == [{"text": "hit for where", "rank": 1}])
        check("a handler exception is ArmError by name, and the child lives on",
              raises(lambda: c.request("counters"), B.ArmError, "RuntimeError: boom") and safely(
                  lambda: c.request("hello"), {}).get("ok") is True)
        check("a non-dict result is ArmError", raises(lambda: c.request("counters", mode="not-dict"), B.ArmError,
                                                      "not a dict"))
        check("F4: a result that would overwrite ok/op/id is ArmError (reserved keys)",
              raises(lambda: c.request("counters", mode="reserved"), B.ArmError, "reserved keys"))
        long_err = ""
        try:
            c.request("counters", mode="long")
        except B.ArmError as e:
            long_err = str(e)
        check("an error text is capped at ERROR_MAX", 0 < len(long_err) <= len("counters: ") + B.ERROR_MAX
              and long_err.endswith("x"), str(len(long_err)))
        check("a request field the op does not take is ArmError (TypeError), not a crash",
              raises(lambda: c.request("read", qid="q", query="x", k=1, extra=1), B.ArmError, "TypeError"))
        check("the client refuses an unknown op and reserved request fields before sending",
              raises(lambda: c.request("drop"), ValueError, "unknown op")
              and raises(lambda: c.request("hello", id=5), ValueError, "reserved"))

        print("\n- F3: UTF-8 whatever the locale -")
        uni = "é — " + "".join(map(chr, (0x41F, 0x440, 0x438, 0x432, 0x435, 0x442))) + " " + chr(0x4F60) + chr(0x597D) \
            + " " + chr(0x2028) + " " + chr(0x1F600)
        e3 = safely(lambda: c.request("write", item={"item_id": "u", "text": uni}), {})
        check("with PYTHONUTF8/PYTHONIOENCODING removed, Cyrillic, CJK, U+2028 and an emoji round-trip exactly",
              e3.get("echo") == uni, repr(e3)[:120])
        check("an answer holding a lone surrogate is ok:false by name, and the child lives on",
              raises(lambda: c.request("counters", mode="surrogate"), B.ArmError, "UnicodeEncodeError")
              and safely(lambda: c.request("hello"), {}).get("ok") is True)
        p.stdin.write(b'{"id": 77, "op": "hello", "x": "\xff\xfe"}\n')
        p.stdin.flush()
        bad = c._q.get(timeout=10)
        check("a request that is not UTF-8 is ok:false by name ('not UTF-8')",
              b'"ok":false' in bad and b"not UTF-8" in bad, bad[:120].decode("ascii", "replace"))
        p.stdin.write(b"this is not json\n")
        p.stdin.flush()
        bad2 = c._q.get(timeout=10)
        check("a malformed line gets an ok:false answer, and the child lives on",
              b'"ok":false' in bad2 and safely(lambda: c.request("hello"), {}).get("ok") is True)

        print("\n- bye with stdin open -")
        byer = safely(lambda: c.request("bye"), {})
        try:
            rc = p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            rc = "still running"
        check("bye ends the child with exit 0 while its stdin is still open", byer.get("ok") is True and rc == 0, str(rc))
        err = errf.read_bytes().decode("utf-8", "replace")
        check("the product's stray output went to stderr, not into the protocol stream",
              "STRAY PRINT FROM THE PRODUCT" in err and "RAW FD1 WRITE" in err, err[-200:])

        print("\n- F2: a product that prints while the handler is built -")
        p2, err2 = spawn("noisy-build")
        c2 = B.ArmClient(p2, default_timeout=30)
        h2 = safely(lambda: c2.request("hello"), {})
        check("printing (print and raw fd 1) during the handler's construction does not reach the stream",
              h2.get("ok") is True and h2.get("id") == 1, str(h2))
        c2.close()
        e2 = err2.read_bytes().decode("utf-8", "replace")
        check("... it lands on stderr", "PRINT WHILE BUILDING THE HANDLER" in e2 and "RAW FD1 WHILE BUILDING" in e2)

        print("\n- F1: timeouts and ids -")
        p3, _ = spawn()
        c3 = B.ArmClient(p3, default_timeout=30)
        check("no answer in time is ArmTimeout", raises(lambda: c3.request("read", qid="q", query="sleep", k=1, timeout=1),
                                                        B.ArmTimeout, "no answer"))
        time.sleep(3)                                    # the late answer to the timed-out read arrives now
        check("F1: after a timeout the client is poisoned - the next request raises ArmPoisoned, never the late answer",
              raises(lambda: c3.request("write", item={"item_id": "second"}), B.ArmPoisoned, "poisoned"))
        rc3 = c3.close()
        check("... and close() ends the poisoned child", p3.poll() is not None, str(rc3))
        p5, _ = spawn()
        c5 = B.ArmClient(p5, default_timeout=30)
        hung = raises(lambda: c5.request("read", qid="q", query="hang", k=1, timeout=1), B.ArmTimeout, "no answer")
        t0 = time.monotonic()
        rc5 = safely(lambda: c5.close(timeout=20), "close raised")
        took = time.monotonic() - t0
        check("F1: close() KILLS a poisoned child still inside its handler - it returns at once, not after the handler",
              hung and isinstance(rc5, int) and rc5 != 0 and took < 15, f"{rc5!r} after {took:.1f} s")
        for mode, words in (("wrong-id", "got the answer to request 999"), ("garbage", "not JSON"),
                            ("array", "not an object")):
            pl, _ = spawn(script="liar.py", args=[mode])
            cl = B.ArmClient(pl, default_timeout=30)
            check(f"F1: a {mode} answer is a protocol error by name, and poisons the client",
                  raises(lambda: cl.request("hello"), B.ArmError, words) and raises(lambda: cl.request("hello"),
                                                                                  B.ArmPoisoned, "poisoned"))
            cl.close()

        print("\n- a child that dies -")
        p4, _ = spawn()
        c4 = B.ArmClient(p4, default_timeout=30)
        check("a child that exits mid-request is ArmDied, never an empty result",
              raises(lambda: c4.request("end_write", die=True), B.ArmDied, "stdout closed"))
    finally:
        for ch in children:
            try:
                ch.kill()
            except OSError:
                pass
            try:
                ch.wait(timeout=30)
            except Exception:  # noqa: BLE001
                pass
            for s in (ch.stdin, ch.stdout):
                try:
                    s.close()
                except OSError:
                    pass
            try:
                ch.stderr.close()
            except (OSError, AttributeError):
                pass

print(f"\nv3 arm protocol: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
