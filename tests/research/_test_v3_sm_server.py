#!/usr/bin/env python3
"""PREREG-V3 A8, supermemory-local (the probe plan v2, C5): research/v3/sm_server.py - the server child's pieces.

* the spawn's env: the port, the data directory, our embedder through the Ollama leg (openai-compatible, 1024) and the
  proxy's LLM - never a vendor switch, WORKFLOW_ENGINE or another NOT_DECLARED name, no vendor default reached; a name
  the bundle's env facts do not read, or another provider's, is refused by name (SMS-ENV-1/2);
* the LaunchSpec: stdio "server", the proxy token the one token name - after the binary is hashed again against the
  pin (SMS-PIN-1);
* the key: a HeldKey never shows its value in a repr, str or format, and is never pickled, copied or JSON (SMS-KEY-1/2);
  key_search finds it in UTF-8 and UTF-16LE, a hit inside the data directory product_stored and anywhere else - a
  sibling named like it included - a leak; links are listed and never followed, an unreadable file is listed
  (SMS-KEY-3/4);
* the pump: every key-shaped token and the held value redacted before a byte is written, the key taken from the
  banner row only (SMS-PUMP-1); a line longer than LINE_MAX never cut inside a key (SMS-PUMP-2);
* the running server (a FAKE, tests/fixtures/v3_fake_products/_fake_supermemory.py, as a child process): ready after
  its 503s, an exit before it answers named, a ceiling kept (SMS-READY-1); its tree with exe and sha256 (SMS-TREE-1);
  stop by CTRL_BREAK, else the tree killed, and what outlived the server killed too (SMS-STOP-1).

Children: the fake server and its own child.

    python tests/research/_test_v3_sm_server.py
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

# the stop kills a tree whose root may be gone: psutil is the only route here (no job object)
import psutil  # noqa: E402

PASSED = FAILED = 0
FAKE = ROOT / "tests" / "fixtures" / "v3_fake_products" / "_fake_supermemory.py"


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _try(fn):
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


ROWS = ("SMS-ENV-1", "SMS-ENV-2", "SMS-PIN-1", "SMS-KEY-1", "SMS-KEY-2", "SMS-KEY-3", "SMS-KEY-4", "SMS-PUMP-1",
        "SMS-PUMP-2", "SMS-READY-1", "SMS-TREE-1", "SMS-STOP-1")
SS, load_err = _try(lambda: _load("v3_sm_server_for_sms_t", ROOT / "research" / "v3" / "sm_server.py"))
if SS is None:                     # every row by name: a module that does not load is each row's failure
    for r in ROWS:
        check(f"{r}: research/v3/sm_server.py loads", False, str(load_err))
    print(f"\nv3 sm server: {PASSED} passed, {FAILED} failed")
    sys.exit(1)
SB = _load("v3_sm_bundle", ROOT / "research" / "v3" / "sm_bundle.py")
SC = _load("v3_scheduler_for_sms_t", ROOT / "research" / "v3" / "scheduler.py")
L = _load("v3_launch_for_sms_t", ROOT / "research" / "v3" / "launch.py")


def refused(fn, *words: str) -> tuple[bool, str]:
    """fn raises sm_server's own refusal, naming every word."""
    try:
        fn()
    except SS.ServerRefused as e:
        return all(w in str(e) for w in words), str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    return False, "no refusal"


def _v(*values: str) -> dict:
    return {"values": list(values)}


#: sm_bundle's facts, in the shape run_facts records them, with the server-v0.0.8 names and defaults (the literal
#: values are the bundle's; only env_facts' inputs are given)
FACTS = {
    "embed_provider": _v("SUPERMEMORY_EMBEDDING_PROVIDER", "local", "openai-compatible"),
    "embed_model": _v("SUPERMEMORY_EMBEDDING_MODEL", "SUPERMEMORY_LOCAL_EMBEDDING_MODEL", "Xenova/bge-m3",
                      "text-embedding-3-small"),
    "embed_base_url": _v("SUPERMEMORY_EMBEDDING_BASE_URL", "OPENAI_BASE_URL"),
    "embed_dimensions": _v("SUPERMEMORY_EMBEDDING_DIMENSIONS"),
    "embed_api_key": _v("SUPERMEMORY_EMBEDDING_API_KEY", "OPENAI_API_KEY"),
    "llm_openai_client": _v("OPENAI_API_KEY", "OPENAI_BASE_URL"),
    "llm_models": _v("OPENAI_FAST_MODEL", "OPENAI_MODEL", "gpt-5-nano", "OPENAI_TEXT_MODEL", "gpt-5-mini"),
    "port": _v("6767"),
    "data_dir": _v("~/.supermemory"),
    "workflow_direct_win32": _v("win32", "WORKFLOW_ENGINE"),
    "workflow_direct_set": _v("direct"),
    "rivet_port_formula": _v("1e4", "3", "50000", "30"),
    "llm_other_providers": _v("anthropic", "ANTHROPIC_API_KEY", "gemini", "GEMINI_API_KEY", "groq", "GROQ_API_KEY",
                              "workers-ai", "WORKERS_AI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "vertex",
                              "GOOGLE_VERTEX_PROJECT_ID", "GOOGLE_VERTEX_LOCATION"),
}
ENV_REC = SB.env_facts(FACTS)
TOKEN = "nvt3-" + "a" * 40
KW = dict(port=40123, data_dir="D:/runs/_a8/p1/supermemory-local/u1.home/sm-data",
          llm_base_url="http://127.0.0.1:41000/v1", llm_model="deepseek-flash",
          embed_base_url="http://127.0.0.1:41001/v1", embed_model="nvt3-bge-m3", token=TOKEN)
KEY = b"sm_" + b"K7q2" * 7 + b"Zz"          # the docs' form: sm_ and 30 characters
TMP = Path(tempfile.mkdtemp(prefix="nvt3_sm_server_"))


def window_leak(data: bytes, value: bytes, n: int = 6) -> bool:
    """Any n bytes of the value's body in the data - a key cut in two still shows as halves."""
    body = value[3:]
    return any(body[i:i + n] in data for i in range(len(body) - n + 1))


def fake_env(port: int, data: Path, knobs: dict) -> dict:
    kfile = TMP / f"knobs_{port}.json"
    kfile.write_text(json.dumps(knobs), encoding="utf-8")
    env = {k: os.environ[k] for k in ("SYSTEMROOT", "PATH", "TEMP", "TMP") if k in os.environ}
    env.update(SUPERMEMORY_PORT=str(port), SUPERMEMORY_DATA_DIR=str(data), NVT3_FAKE_SM=str(kfile),
               PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    return env


def start_fake(name: str, knobs: dict):
    """The fake server with a server's stdio (C2: stdin DEVNULL, stdout a PIPE, stderr into it) in its own process
    group (launch.spawn's creationflags), its stdout through a RedactingPump."""
    port, data = SS.free_port(), TMP / name / "sm-data"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    p = subprocess.Popen([sys.executable, "-B", str(FAKE)], env=fake_env(port, data, knobs), cwd=str(TMP),
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         creationflags=flags, start_new_session=os.name != "nt")
    pump = SS.RedactingPump(p.stdout, TMP / name / "server.log").start()
    return L.Child(spawn_id=name, process=p), pump, port, data


def cleanup(child) -> None:
    try:
        if child.process.poll() is None:
            child.kill_tree()
            child.process.wait(10)
    except Exception:  # noqa: BLE001 - the rows already said what failed
        pass


try:
    print("- the spawn's env -")
    env, err = _try(lambda: SS.server_env(ENV_REC, **KW))
    bad = sorted(set(env or {}) & (set(SB.NOT_DECLARED) | set(SB.SPAWN_ENV) | {"WORKFLOW_ENGINE"}))
    res = SB.resolve(env, ENV_REC) if env else None
    check("SMS-ENV-1: the server spawn declares the port, the data directory, our embedder through the Ollama leg "
          "(openai-compatible, 1024) and the proxy's LLM - no vendor switch, no WORKFLOW_ENGINE, no NOT_DECLARED name, "
          "no vendor default reached",
          err is None and not bad and env["SUPERMEMORY_EMBEDDING_PROVIDER"] == "openai-compatible"
          and env["SUPERMEMORY_EMBEDDING_DIMENSIONS"] == "1024" and env["OPENAI_API_KEY"] == TOKEN
          and env["SUPERMEMORY_EMBEDDING_BASE_URL"] == KW["embed_base_url"]
          and env["OPENAI_BASE_URL"] == KW["llm_base_url"] and env["OPENAI_MODEL"] == "deepseek-flash"
          and env["SUPERMEMORY_PORT"] == "40123" and res["vendor_defaults"] == []
          and res["llm"] == {"fast": "deepseek-flash", "text": "deepseek-flash", "base_url": KW["llm_base_url"],
                             "client": "openai-compatible", "path": "/chat/completions"},
          str(err or bad or res))
    no_dims = json.loads(json.dumps(ENV_REC))
    del no_dims["reads"]["SUPERMEMORY_EMBEDDING_DIMENSIONS"]
    other = json.loads(json.dumps(ENV_REC))
    other["must_be_absent"].append("OPENAI_MODEL")
    r1, r2 = (refused(lambda: SS.server_env(no_dims, **KW), "SUPERMEMORY_EMBEDDING_DIMENSIONS", "reads no such"),
              refused(lambda: SS.server_env(other, **KW), "OPENAI_MODEL", "another provider"))
    check("SMS-ENV-2: a declared name the bundle's env facts do not read, or another provider's, is refused by name "
          "before any spawn", r1[0] and r2[0], f"{r1} {r2}")

    print("\n- the LaunchSpec and the pin -")
    binary = TMP / "bin" / "supermemory-server-windows-x64.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"MZ fake server bytes")
    pin = hashlib.sha256(binary.read_bytes()).hexdigest()
    spec, serr = _try(lambda: SS.launch_spec(SC, binary, pin_sha256=pin, declared=env or {}))
    wrong = refused(lambda: SS.launch_spec(SC, binary, pin_sha256="0" * 64, declared=env or {}), "is not the pin",
                    "SF1")
    rel = refused(lambda: SS.launch_spec(SC, "bin/x.exe", pin_sha256=pin, declared=env or {}), "absolute")
    check("SMS-PIN-1: the server's LaunchSpec is stdio 'server' with the proxy token its one token name - only after "
          "the binary is hashed again against the pin; another sha256, or a relative path, is refused by name",
          serr is None and spec.stdio == "server" and spec.argv == (str(binary),) and spec.token_names ==
          ("OPENAI_API_KEY",) and dict(spec.declared) == env and spec.requirement == "required" and not spec.pipes
          and spec.stderr_path is None and wrong[0] and rel[0], str(serr or (wrong, rel)))

    print("\n- the held key -")
    h = SS.HeldKey(KEY)
    shows = [repr(h), str(h), f"{h}", f"{h!r}", "%s" % h, format(h, ">80"), json.dumps({"k": h}, default=str)]
    check("SMS-KEY-1: a HeldKey shows its sha12 and form, never its value - repr, str, format, %-format, JSON by str",
          not any(KEY.decode() in s or window_leak(s.encode(), KEY) for s in shows)
          and all(h.sha12 in s for s in shows) and h.sha12 == hashlib.sha256(KEY).hexdigest()[:12]
          and h.form == "sm_+30 alnum", str(shows))
    blocked = []
    for what, fn in (("pickle", lambda: pickle.dumps(h)), ("copy", lambda: copy.copy(h)),
                     ("deepcopy", lambda: copy.deepcopy({"k": h})), ("json", lambda: json.dumps({"k": h}))):
        got, e = _try(fn)
        blocked.append((what, e is not None and e.startswith("TypeError"), e))
    check("SMS-KEY-2: a HeldKey is never pickled, copied or written as JSON - each a TypeError",
          all(b for _, b, _ in blocked) and h.matches(KEY + b"\n") and not h.matches(KEY[:-1] + b"y")
          and h.header() == {"x-supermemory-api-key": KEY.decode()}, str(blocked))

    print("\n- the key's traces -")
    unit, home = TMP / "ks" / "u1", TMP / "ks" / "u1.home"
    data = home / "sm-data"
    for d in (unit, data, home / ".cache", home / "sm-data-old"):
        d.mkdir(parents=True)
    (data / "api-key").write_bytes(KEY)
    (home / "leak.log").write_bytes(b"started with " + KEY + b"\n")
    (home / ".cache" / "k.bin").write_bytes(b"\x00\x01" + KEY.decode().encode("utf-16-le") + b"\x00")
    (home / "sm-data-old" / "api-key").write_bytes(KEY)
    (unit / "clean.txt").write_bytes(b"nothing here but sm_ and " + KEY[:20])
    big = bytearray(b"x" * ((1 << 20) + 50))
    big[(1 << 20) - 10:(1 << 20) - 10 + len(KEY)] = KEY               # across the 1 MiB read boundary
    (unit / "big.bin").write_bytes(bytes(big))
    ks, kerr = _try(lambda: SS.key_search({"unit": unit, "home": home}, h, data_dir=data))
    hits = lambda k: sorted((x["root"], x["path"], tuple(x["encodings"])) for x in (ks or {}).get(k, []))  # noqa: E731
    check("SMS-KEY-3: key_search finds the key in UTF-8 and UTF-16LE, across a read boundary; a hit inside the data "
          "directory is product_stored, anywhere else a leak - a sibling named like the data directory included",
          kerr is None and hits("product_stored") == [("home", "sm-data/api-key", ("utf-8",))]
          and hits("leaks") == [("home", ".cache/k.bin", ("utf-16le",)), ("home", "leak.log", ("utf-8",)),
                                ("home", "sm-data-old/api-key", ("utf-8",)), ("unit", "big.bin", ("utf-8",))]
          and ks["files"] == 6 and ks["unreadable"] == [] and ks["links"] == [], str(kerr or ks))
    outside = TMP / "ks" / "outside"
    outside.mkdir()
    (outside / "owner.txt").write_bytes(KEY)
    linked = False
    try:
        if os.name == "nt":
            import _winapi  # noqa: PLC0415
            _winapi.CreateJunction(str(outside), str(home / "j"))
        else:
            os.symlink(outside, home / "j", target_is_directory=True)
        linked = True
    except OSError as e:
        print(f"  (no link could be made here: {type(e).__name__})")
    locked = (home / "leak.log").resolve()

    def opener(path, mode):
        if Path(path).resolve() == locked:
            raise PermissionError(13, "denied by the row")
        return open(path, mode)  # noqa: SIM115

    k4, k4err = _try(lambda: SS.key_search({"unit": unit, "home": home}, h, data_dir=data, opener=opener))
    check("SMS-KEY-4: a link is listed and never followed (the key behind it is not searched), an unreadable file is "
          "listed by name and error, never skipped - and the rest is still searched",
          k4err is None and linked and k4["links"] == [{"root": "home", "path": "j"}]
          and k4["unreadable"] == [{"root": "home", "path": "leak.log", "error": "PermissionError"}]
          and sorted(x["path"] for x in k4["leaks"]) == [".cache/k.bin", "big.bin", "sm-data-old/api-key"]
          and not any("owner" in x["path"] for x in k4["leaks"] + k4["product_stored"]), str(k4err or k4))

    print("\n- the pump -")
    early = b"sm_NOTTHEKEY12345"
    stream = io.BytesIO(b"boot " + early + b" early\n"
                        b"+-----------+------+\n| url       | http://127.0.0.1:1 |\n| api key   | " + KEY + b" |\n"
                        b"log: using " + KEY + b" for the owner\r\nlast line without a newline " + KEY)
    p1 = SS.RedactingPump(stream, TMP / "pump1" / "out.log").start()
    p1.join(10)
    out1 = (TMP / "pump1" / "out.log").read_bytes()
    held = SS.HeldKey(b"plain-held-value-0042")
    p1b = SS.RedactingPump(io.BytesIO(b"x plain-held-value-0042 y\n"), TMP / "pump1" / "held.log", held=held).start()
    p1b.join(10)
    out1b = (TMP / "pump1" / "held.log").read_bytes()
    check("SMS-PUMP-1: every key-shaped token and the held value are replaced by <sm-key:sha12> before a byte is "
          "written; the key is taken from the banner row only, never from an earlier line",
          p1.done.is_set() and p1.key is not None and p1.key == SS.HeldKey(KEY) and p1.banner_keys == [h.sha12]
          and KEY not in out1 and early not in out1 and not window_leak(out1, KEY)
          and out1.count(b"<sm-key:" + h.sha12.encode() + b">") == 3 and b"early" in out1 and b"for the owner" in out1
          and p1.redacted == 4 and b"plain-held-value-0042" not in out1b
          and b"<sm-key:" + held.sha12.encode() + b">" in out1b and p1.error is None,
          f"{p1.key!r} {p1.banner_keys} {p1.redacted} {out1[:300]!r} {out1b!r}")
    cut_at = SS.LINE_MAX - SS.CARRY
    line = bytearray(b"." * (2 * SS.LINE_MAX + 100))              # no token character: a key ends where it ends
    line[cut_at - 4:cut_at - 4 + len(KEY)] = KEY                        # across the first cut
    line[2 * SS.LINE_MAX - 10:2 * SS.LINE_MAX - 10 + len(KEY)] = KEY    # across the second read's end
    line += b"\n"
    p2 = SS.RedactingPump(io.BytesIO(bytes(line)), TMP / "pump2" / "out.log").start()
    p2.join(30)
    out2 = (TMP / "pump2" / "out.log").read_bytes()
    marker = b"<sm-key:" + h.sha12.encode() + b">"
    check("SMS-PUMP-2: a line longer than LINE_MAX is cut CARRY bytes short of its end and never inside a key - the "
          "key never reaches the file in two halves",
          p2.long_lines >= 2 and not window_leak(out2, KEY) and out2.count(marker) == 2
          and len(out2) == len(line) - 2 * len(KEY) + 2 * len(marker), f"long={p2.long_lines} len={len(out2)}")

    print("\n- server stop: a reused descendant PID belongs to a different process -")

    class IdentityProcess:
        def __init__(self, pid, born):
            self.pid, self.born = pid, born
        def create_time(self):
            return self.born
        def is_running(self):
            return True
        def status(self):
            return "running"
        def kill(self):
            identity_killed.append((self.pid, self.born))

    class IdentityRoot:
        pid = 91001
        def poll(self):
            return 0 if identity_after[0] else None
        def wait(self, timeout):
            identity_after[0] = True
            return 0
        def send_signal(self, sig):
            pass

    class IdentityChild:
        process = IdentityRoot()
        def kill_tree(self):
            raise AssertionError("the root exits during its grace")

    def process_identity(pid):
        return IdentityProcess(pid, 20.0 if pid == 91002 and identity_after[0] else 10.0)

    identity_killed, identity_after = [], [False]
    try:
        with mock.patch.object(SS, "tree", return_value=[{"pid": 91001, "create_time": 10.0},
                                                        {"pid": 91002, "create_time": 10.0},
                                                        {"pid": 91003, "create_time": 10.0}]), \
             mock.patch.object(psutil, "Process", side_effect=process_identity), mock.patch.object(SS.os, "kill"):
            identity_stop = SS.stop(IdentityChild(), grace_s=1)
        check("SMS-STOP-ID-1: a descendant PID reused during grace is never killed; the original surviving sibling is",
              identity_killed == [(91003, 10.0)] and identity_stop.get("reused") == [91002]
              and identity_stop["outlived"] == [91003], str((identity_killed, identity_stop)))
    except Exception as e:  # noqa: BLE001 - never hide the other server rows with a crash
        check("SMS-STOP-ID-1: identity-bound cleanup runs", False, repr(e))

    identity_after[0] = False
    try:
        with mock.patch.object(SS, "tree", return_value=[{"pid": 91001, "error": "AccessDenied"}]), \
             mock.patch.object(SS.os, "kill"):
            incomplete_stop = SS.stop(IdentityChild(), grace_s=1)
        check("SMS-STOP-ID-2: failed root/children enumeration is recorded as unverified, never a clean empty tree",
              incomplete_stop.get("unverified") == [{"pid": 91001, "error": "tree:AccessDenied"}], str(incomplete_stop))
    except Exception as e:  # noqa: BLE001
        check("SMS-STOP-ID-2: incomplete enumeration is reported", False, repr(e))

    identity_after[0] = False
    identity_killed.clear()
    try:
        with mock.patch.object(SS, "tree", return_value=[{"pid": 91001}, {"pid": 91002, "create_time": 10.0}]), \
             mock.patch.object(psutil, "Process", side_effect=lambda pid: IdentityProcess(pid, 20.0)), mock.patch.object(SS.os, "kill"):
            pre_reuse = SS.stop(IdentityChild(), grace_s=1)
        check("SMS-STOP-ID-3: reuse between tree snapshot and identity capture is also skipped, never adopted as a descendant",
              identity_killed == [] and pre_reuse.get("reused") == [91002], str(pre_reuse))
    except Exception as e:  # noqa: BLE001
        check("SMS-STOP-ID-3: pre-capture reuse is reported", False, repr(e))

    identity_after[0] = True
    try:
        with mock.patch.object(SS, "tree", return_value=[] ) as looked_up, mock.patch.object(SS.os, "kill") as signalled:
            exited_stop = SS.stop(IdentityChild(), grace_s=1)
        check("SMS-STOP-ID-4: an already exited root's PID is neither looked up nor signalled; missing tree identity is explicit",
              not looked_up.called and not signalled.called and exited_stop["exit_code"] == 0
              and exited_stop.get("unverified") == [{"pid": 91001, "error": "root-exited-before-snapshot"}], str(exited_stop))
    except Exception as e:  # noqa: BLE001
        check("SMS-STOP-ID-4: an exited root is not touched by PID", False, repr(e))

    original_job = mock.Mock()
    original_job.kill_tree.return_value = True
    exited_child = IdentityChild()
    exited_child.witness = original_job
    try:
        with mock.patch.object(SS, "tree") as looked_up, mock.patch.object(SS.os, "kill") as signalled:
            job_stop = SS.stop(exited_child, grace_s=1)
        check("SMS-STOP-ID-5: an exited root's original job still terminates surviving descendants without numeric PID fallback",
              original_job.kill_tree.call_args == mock.call(91001) and not looked_up.called and not signalled.called
              and job_stop.get("job_terminated") is True, str(job_stop))
    except Exception as e:  # noqa: BLE001
        check("SMS-STOP-ID-5: original job cleanup survives root exit", False, repr(e))

    print("\n- the running server (the fake) -")
    ch, pump, port, sdata = start_fake("ready", {"key": KEY.decode(), "boot_503_s": 1.0, "stderr_key": True})
    try:
        rd = SS.wait_ready(port, process=ch.process, ceiling_s=30, poll_s=0.2)
        pump.key_seen.wait(10)
        stored = (sdata / "api-key").read_bytes()
        st = SS.stop(ch, grace_s=10)
        pump.join(10)
        log = (TMP / "ready" / "server.log").read_bytes()
    finally:
        cleanup(ch)
    ch2, pump2, port2, _ = start_fake("exits", {"key": KEY.decode(), "exit_at_boot": 3})
    try:
        ex = SS.wait_ready(port2, process=ch2.process, ceiling_s=30, poll_s=0.2)
    finally:
        cleanup(ch2)
    ch3, pump3, port3, _ = start_fake("starting", {"key": KEY.decode(), "boot_503_s": 600})
    try:
        slow = SS.wait_ready(port3, process=ch3.process, ceiling_s=1.5, poll_s=0.2)
    finally:
        SS.stop(ch3, grace_s=5)
        cleanup(ch3)
    check("SMS-READY-1: ready only after the server's 503s end (its key from the banner equal to its key file, the "
          "merged stderr redacted too); an exit before any answer, and 503 up to the ceiling, are not ready - by name",
          rd["ready"] and rd["status"] == 200 and rd["after_s"] >= 1.0 and rd["polls"] >= 2
          and pump.key is not None and pump.key.matches(stored) and KEY not in log and not window_leak(log, KEY)
          and log.count(b"<sm-key:") == 2 and st["how"] == "ctrl_break"
          and not ex["ready"] and "exited with 3" in (ex["why"] or "")
          and not slow["ready"] and slow["status"] == 503 and "within 1.5 s" in (slow["why"] or ""),
          f"{rd} {st} {ex} {slow} {log[:200]!r}")

    ch4, pump4, port4, _ = start_fake("tree", {"key": KEY.decode(), "child": True})
    try:
        SS.wait_ready(port4, process=ch4.process, ceiling_s=30, poll_s=0.2)
        kid = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            txt = (TMP / "tree" / "server.log").read_bytes() if (TMP / "tree" / "server.log").exists() else b""
            if b"child " in txt:
                kid = int(txt.split(b"child ", 1)[1].split()[0])
                break
            time.sleep(0.1)
        tr = SS.tree(ch4.process.pid)
        st4 = SS.stop(ch4, grace_s=10)
    finally:
        cleanup(ch4)
    pids = [r.get("pid") for r in tr]
    check("SMS-TREE-1: the server's tree, the root first - each member with its exe and that file's sha256",
          len(tr) >= 2 and pids[0] == ch4.process.pid and kid in pids
          and all(r.get("exe") and len(r.get("sha256") or "") == 64 for r in tr)
          and st4["how"] == "ctrl_break" and not st4["alive"], f"{tr} kid={kid} {st4}")

    ch5, pump5, port5, _ = start_fake("deaf", {"key": KEY.decode(), "ignore_break": True})
    try:
        SS.wait_ready(port5, process=ch5.process, ceiling_s=30, poll_s=0.2)
        s5 = SS.stop(ch5, grace_s=1.0)
    finally:
        alive5 = ch5.process.poll() is None
        cleanup(ch5)
    before6: list = []
    ch6, pump6, port6, _ = start_fake("orphan", {"key": KEY.decode(), "child": True, "child_ignores_break": True})
    try:
        SS.wait_ready(port6, process=ch6.process, ceiling_s=30, poll_s=0.2)
        before6 = [r["pid"] for r in SS.tree(ch6.process.pid)[1:]]
        s6 = SS.stop(ch6, grace_s=10)
        time.sleep(0.3)
        left6 = [pid for pid in before6 if psutil.pid_exists(pid)
                 and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE]
    finally:
        cleanup(ch6)
        for pid in before6:
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
    check("SMS-STOP-1: a server that ignores CTRL_BREAK is killed with its tree after the grace; what the tree held "
          "before the stop and outlived the server's own exit is killed too",
          s5["how"] == "killed" and not s5["alive"] and not alive5 and s5["after_s"] >= 1.0
          and s6["how"] == "ctrl_break" and s6["exit_code"] == 0 and before6 and set(s6["outlived"]) == set(before6)
          and left6 == [], f"{s5} {s6} before={before6} left={left6}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 sm server: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
