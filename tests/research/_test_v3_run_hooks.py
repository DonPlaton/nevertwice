#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A2: research/v3/run_v3_hooks.py - the scheduler's hooks, with a REAL git child through the
scheduler's one spawn path on a temporary repository, and a loopback fake standing in for the proxy's scheduler port:

* HK-tree-argv: git's one fixed argv, the repository its one argv exception (Q10);
* HK-tree-commit: a clean tree - the verdict clean, the stand plan's commit = HEAD and dirty = False (Q2);
* HK-tree-dirty: an untracked file under research/ makes it dirty, and the plan says so;
* HK-tree-anchor: a scored stand needs FREEZE-V3's anchor; a HEAD that is not the anchor is dirty;
* HK-tree-scored-dirty: a real scored Scheduler refuses the stand before STAND START (D10);
* HK-tree-rc: git exiting non-zero is refused by name - no verdict from no listing;
* HK-probe-scheduler-port: D8's probe - 1 token, thinking disabled, the pinned model, the scheduler's token, no /u/;
* HK-probe-model-nospace: an answer without a one-word model, or not 200, is refused;
* HK-preflight-refuse: a 402, a balance below 2x the projection, a scored stand without a projection - each halts;
* HK-preflight-record: every answer is appended to <runs>/_launch/balance.jsonl, chained; an unavailable endpoint is
  recorded and does not halt (§4.5: the 402 rule alone);
* HK-barrier: without a change-log reader the read gives no change log (the scheduler writes "unread" / refuses).

    python tests/research/_test_v3_run_hooks.py
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import
import psutil  # noqa: F401,E402  R-LAUNCHER: git on Windows is a launcher too (cmd\git.exe); its tree dies through psutil


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


HK = _load("v3_run_hooks_t", ROOT / "research" / "v3" / "run_v3_hooks.py")
SC = _load("v3_scheduler_for_hooks_t", ROOT / "research" / "v3" / "scheduler.py")
L = _load("v3_launch_for_hooks_t", ROOT / "research" / "v3" / "launch.py")
SL = _load("v3_status_log_for_hooks_t", ROOT / "research" / "v3" / "status_log.py")
TC = HK._tree_mod()
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def attempt(fn):
    """(result, None) or (None, the exception) - a row's failure is a named FAIL, never a traceback."""
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, e


class FakePort:
    """The proxy's scheduler port, in place: records (method, path, Authorization, body); answers routes[path]."""

    def __init__(self) -> None:
        self.seen: list[tuple] = []
        self.routes: dict[str, tuple[int, object]] = {}
        fake = self

        class H(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                fake.seen.append((self.command, self.path, self.headers.get("Authorization"), body))
                status, obj = fake.routes.get(self.path, (404, {"error": "no route"}))
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _answer

            def log_message(self, *a) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


class StubNative:
    """The native egress witness in place (its own suite, A2.2): registers any child; kills nothing itself."""
    jobs = None

    def register(self, pid, handle=None, label=None) -> bool:
        return True

    def kill_tree(self, root) -> bool:
        return False


class Clock:
    @staticmethod
    def utc():
        return dt.datetime.now(dt.timezone.utc)

    @staticmethod
    def monotonic():
        import time
        return time.monotonic()


class Witnesses:
    native = StubNative()

    def begin_check(self, cid):
        pass

    def end_check(self, cid):
        return {"check_id": cid, "complete": True}


GIT = shutil.which("git")
TMP = Path(tempfile.mkdtemp(prefix="nvt3_hooks_"))
fake = None
try:
    if not GIT:
        raise SystemExit("git is not on PATH - the tree check has nothing to run")
    REPO = TMP / "repo"
    (REPO / "research").mkdir(parents=True)
    (REPO / "research" / "a.py").write_text("x = 1\n", encoding="utf-8")
    genv = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "c"]):
        subprocess.run([GIT, "-C", str(REPO), *cmd], check=True, env=genv, capture_output=True)
    HEAD = subprocess.run([GIT, "-C", str(REPO), "rev-parse", "HEAD"], check=True, env=genv, capture_output=True,
                          text=True).stdout.strip()
    EXC = {sys.executable: "the test interpreter", GIT: "the system Git (the tree check)"}
    if getattr(sys, "_base_executable", sys.executable) != sys.executable:
        EXC[sys._base_executable] = "the test interpreter's base"
    C = L.Contract(polygon_root=TMP / "polygon", runs_root=TMP / "polygon" / "runs" / "v3", repo_root=REPO,
                   owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine",
                   conservation_root=TMP / "conservation", binary_exceptions=EXC,
                   system_dirs=(Path(sys.executable).parent, Path(GIT).parent))
    fake = FakePort()
    PROXY = SimpleNamespace(ports={"scheduler": fake.port}, tokens={"scheduler": "sched-token"})
    argvs: list[list[str]] = []

    def spy(args, **kw):
        argvs.append([str(a) for a in args])
        return subprocess.Popen(args, **kw)

    def world(tag="smoke", *, sfile="STATUS", repo=REPO, anchor=None, projected=None, changelog=None, fallback=False):
        st = SL.StatusLog(TMP / sfile, local_tz=dt.timezone.utc)
        h = HK.Hooks(repo=repo, git=GIT, proxy=PROXY, anchor=anchor, projected_cost=projected, changelog=changelog,
                     balance_fallback=fallback)
        s = SC.Scheduler(C, None, st, L, Clock(), None, tag=tag, witnesses=Witnesses(), parent_env=dict(os.environ),
                         catcher_url="http://127.0.0.1:47001", popen=spy, hooks=h,
                         home_canaries=L.Canaries.generate() if tag == "scored" else None)
        sp = SC.StandPlan(stand="SH", runs=("r1",), launchers={}, campaign_seed=1, unit_tokens={}, medians={},
                          write_ops=lambda *a: [], read_plan=lambda a, u: [], answer=lambda *a: {}, embed_tag=None,
                          commit="0" * 40, dirty=True)
        h.bind(s, sp)
        return h, s, sp, st

    print("- the tree check (Q10, D10) -")
    h0 = HK.Hooks(repo=REPO, git=GIT, proxy=PROXY)
    _r, e = attempt(h0.tree_check)
    _r, e_mp = attempt(h0.model_probe)
    check("the hooks refuse to run unbound (the scheduler and the stand plan come first) - the tree check and the "
          "model probe alike", isinstance(e, HK.HookError) and "bind" in str(e) and isinstance(e_mp, HK.HookError)
          and "bind" in str(e_mp),
          f"{e!r} {e_mp!r}")
    h, s, sp, st = world()
    v, e = attempt(h.tree_check)
    check("HK-tree-argv: git's one fixed argv - git -C <repo> + tree_check.ARGV_TAIL",
          argvs and argvs[-1][1:] == ["-C", str(REPO), *TC.ARGV_TAIL], f"{e!r} {argvs[-1:] if argvs else argvs}")
    rec = [json.loads(x) for x in L.spawns_log(C).read_text(encoding="utf-8").splitlines()][-1]
    check("HK-tree-argv: the repository is its one argv exception, at index 2, and the spawn is the tree check's",
          rec.get("argv_exception") == {"2": str(REPO)} and rec.get("role") == "tree-check" and not rec.get("refused"),
          str({k: rec.get(k) for k in ("argv_exception", "role", "refused", "reasons")}))
    check("HK-tree-commit: a clean tree - the verdict is clean, the plan's commit is HEAD and dirty is False (Q2)",
          e is None and v["clean"] is True and sp.commit == HEAD and sp.dirty is False and v["anchor_source"] == "head",
          f"{e!r} {v} {sp.commit} {sp.dirty}")
    (REPO / "research" / "scratch.txt").write_text("s", encoding="utf-8")
    v, e = attempt(h.tree_check)
    check("HK-tree-dirty: an untracked file under research/ - dirty, and the plan says dirty",
          e is None and v["clean"] is False and sp.dirty is True and any("research" in p for p in v["problems"]),
          f"{e!r} {v}")
    (REPO / "research" / "scratch.txt").unlink()
    h, s, sp, st = world("scored", sfile="STATUS2")
    _r, e = attempt(h.tree_check)
    check("HK-tree-anchor: a scored stand's tree check needs FREEZE-V3's anchor",
          isinstance(e, HK.HookError) and "anchor" in str(e), repr(e))
    h, s, sp, st = world("scored", sfile="STATUS3", anchor="f" * 40)
    v, e = attempt(h.tree_check)
    check("HK-tree-anchor: a HEAD that is not the anchor is dirty", e is None and v["clean"] is False
          and v["anchor_source"] == "freeze" and sp.dirty is True, f"{e!r} {v}")
    notrepo = TMP / "notrepo"
    notrepo.mkdir()
    h, s, sp, st = world(sfile="STATUS4", repo=notrepo)
    _r, e = attempt(h.tree_check)
    check("HK-tree-rc: git exiting non-zero is refused by name - no verdict from no listing",
          isinstance(e, HK.HookError) and "exited" in str(e) and "0" not in str(e).split("exited")[1][:3],
          repr(e))
    repo2 = TMP / "repo2"                                  # a repository of its own: this row moves its HEAD
    (repo2 / "research").mkdir(parents=True)
    (repo2 / "research" / "a.py").write_text("x = 1\n", encoding="utf-8")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "c"]):
        subprocess.run([GIT, "-C", str(repo2), *cmd], check=True, env=genv, capture_output=True)
    h, s, sp, st = world(sfile="STATUS4b", repo=repo2)
    v1, e1m = attempt(h.tree_check)
    (repo2 / "research" / "a.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run([GIT, "-C", str(repo2), "-c", "commit.gpgsign=false", "commit", "-q", "-am", "moved"], check=True,
                   env=genv, capture_output=True)
    v2, e2m = attempt(h.tree_check)
    v2b, e2bm = attempt(h.tree_check)                      # a third check of the same stand: still its FIRST HEAD
    check("B-HEAD-MOVE: with no FREEZE anchor the stand's later tree checks compare with its FIRST HEAD - a commit made "
          "during the stand is dirty at STAND END (and at every check after it), never read as clean against the new "
          "HEAD", e1m is None and e2m is None and e2bm is None and v1["clean"] is True and v2["clean"] is False
          and v2b["clean"] is False and v2["anchor_source"] == v2b["anchor_source"] == "stand-start-head"
          and any("is not the anchor" in p for p in v2["problems"]),
          f"{e1m!r} {e2m!r} {v1 and v1.get('problems')} {v2 and v2.get('problems')} {v2b and v2b.get('problems')}")
    h.bind(s, sp)                                          # the next stand these hooks serve
    v3, e3m = attempt(h.tree_check)
    check("B-HEAD-MOVE: bind() starts a new stand - its first tree check anchors on the HEAD it finds, never on the "
          "previous stand's first HEAD", e3m is None and v3["clean"] is True and v3["anchor_source"] == "head",
          f"{e3m!r} {v3 and v3.get('problems')} {v3 and v3.get('anchor_source')}")

    class _Hung:
        """A tree-check child that never finishes and outlives its kill."""

        def __init__(self):
            self.process = SimpleNamespace(communicate=self._timeout, wait=self._timeout, returncode=None)

        @staticmethod
        def _timeout(*a, **k):
            raise subprocess.TimeoutExpired("git", 1)

        def kill_tree(self):
            pass

    h, s, sp, st = world(sfile="STATUS4c")
    s.spawn_child = lambda build, **kw: (_Hung(), None)
    _r, e_hung = attempt(h.tree_check)
    check("the tree check's git that hangs and outlives its kill is a HookError by name - never a raw TimeoutExpired",
          isinstance(e_hung, HK.HookError) and "outlived its kill" in str(e_hung), repr(e_hung))

    print("\n- the model probe (D8) -")
    fake.routes = {"/chat/completions": (200, {"model": "deepseek-v4-flash", "choices": []})}
    h, s, sp, st = world(sfile="STATUS5")
    got, e = attempt(h.model_probe)
    m, path, auth, body = fake.seen[-1] if fake.seen else (None,) * 4
    sent = json.loads(body) if body else {}
    check("HK-probe-scheduler-port: 1 token, thinking disabled, the pinned model, the scheduler's token, no /u/ prefix",
          e is None and got == "deepseek-v4-flash" and (m, path, auth) == ("POST", "/chat/completions", "Bearer sched-token")
          and sent.get("max_tokens") == 1 and sent.get("thinking") == {"type": "disabled"}
          and sent.get("model") == "deepseek-flash", f"{e!r} {got} {fake.seen[-1:]}")
    refusals = []
    for label, route in (("a model with a space", (200, {"model": "deepseek v4"})), ("no model", (200, {"x": 1})),
                         ("a 500 that names a model", (500, {"model": "deepseek-v4-flash"}))):
        fake.routes = {"/chat/completions": route}
        _r, e = attempt(h.model_probe)
        refusals.append((label, isinstance(e, HK.HookError)))
    check("HK-probe-model-nospace: an answer with no one-word model, or not 200, is refused",
          all(ok for _l, ok in refusals), str(refusals))

    print("\n- the balance preflight (§4.5) -")
    bal = lambda x: (200, {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": str(x)}]})  # noqa: E731
    fake.routes = {"/user/balance": bal(5.0)}
    h, s, sp, st = world(sfile="STATUS6", projected=1.0)
    rec, e = attempt(lambda: h.preflight("SH"))
    check("HK-preflight-record: 5.0 USD against a projected 1.0 passes, and the answer is recorded",
          e is None and rec["balance"] == 5.0 and rec["balance_ok"] is True and fake.seen[-1][:3]
          == ("GET", "/user/balance", "Bearer sched-token"), f"{e!r} {rec}")
    fake.routes = {"/user/balance": bal(1.5)}
    _r, e1 = attempt(lambda: h.preflight("SH"))
    fake.routes = {"/user/balance": (402, {"error": "insufficient"})}
    _r, e2 = attempt(lambda: h.preflight("SH"))
    h2, s2, sp2, st2 = world("scored", sfile="STATUS7", anchor=HEAD)
    fake.routes = {"/user/balance": bal(100.0)}
    _r, e3 = attempt(lambda: h2.preflight("SH"))
    check("HK-preflight-refuse: below 2x the projection, a 402, a scored stand without a projection - each halts",
          all(isinstance(x, HK.HookError) for x in (e1, e2, e3)) and "below 2x" in str(e1) and "402" in str(e2)
          and "projected" in str(e3), f"{e1!r} | {e2!r} | {e3!r}")
    halts = []
    for code in (401, 403):
        fake.routes = {"/user/balance": (code, {"error": "the key is refused"})}
        _r, e_k = attempt(lambda: h.preflight("SH"))
        halts.append((code, isinstance(e_k, HK.HookError) and str(code) in str(e_k)))
    check("B-HALT-SCHED: a 401 or a 403 on the balance halts a smoke stand too (§4.5's three) - never an 'unread' "
          "balance the stand goes on past", all(ok for _c, ok in halts), str(halts))
    fake.routes = {}
    rec4, e4 = attempt(lambda: h.preflight("SH"))
    check("HK-preflight-record: an unavailable endpoint is recorded by name as unread (no balance) and does not halt - "
          "the 402 rule alone", e4 is None and rec4["status"] == 404 and rec4["balance"] is None
          and rec4["balance_ok"] is None and rec4["balance_read"] == "unread: status 404"
          and rec["balance_read"] == "ok", f"{e4!r} {rec4}")
    fake.routes = {"/user/balance": (200, {"is_available": True, "balance_infos": [{"currency": "CNY",
                                                                                     "total_balance": "999"}]})}
    rec_cny, e_cny = attempt(lambda: h.preflight("SH"))
    check("HKk: a balance in another currency than the projection's is not a balance - unread, and it says why",
          e_cny is None and rec_cny["balance"] is None and rec_cny["balance_read"] == "unread: status 200 without a USD balance",
          f"{e_cny!r} {rec_cny}")
    hs, ss, sps, sts = world("scored", sfile="STATUS6b", anchor=HEAD, projected=1.0)
    _r, e_nofb = attempt(lambda: hs.preflight("SH"))
    fake.routes = {}
    _r, e_nofb404 = attempt(lambda: hs.preflight("SH"))
    hf, sf_, spf, stf = world("scored", sfile="STATUS6c", anchor=HEAD, projected=1.0, fallback=True)
    rec_fb, e_fb = attempt(lambda: hf.preflight("SH"))
    check("R-BAL: a scored stand whose balance was not read (another currency, no endpoint) halts without FREEZE's "
          "fallback", isinstance(e_nofb, HK.HookError) and "R-BAL" in str(e_nofb) and isinstance(e_nofb404, HK.HookError),
          f"{e_nofb!r} | {e_nofb404!r}")
    check("R-BAL: with the fallback FREEZE declares (the pilot's finding) it is recorded and the 402 rule alone applies",
          e_fb is None and rec_fb["balance"] is None and rec_fb["balance_read"] == "unread: status 404", f"{e_fb!r} {rec_fb}")
    bj = C.runs_root / "_launch" / "balance.jsonl"
    lines = [json.loads(x) for x in bj.read_text(encoding="utf-8").splitlines()] if bj.exists() else []
    check("HK-preflight-record: every answer is in balance.jsonl, chained (11 asks, the refused ones too - the 401 and "
          "403 halts included)",
          len(lines) == 11 and L.verify_chain(bj), f"{len(lines)} {L.verify_chain(bj) if bj.exists() else 'none'}")

    print("\n- the barrier read, and the scored stand refused on a dirty tree -")
    h, s, sp, st = world(sfile="STATUS8")
    check("HK-barrier: without a change-log reader the read gives no change log", h.barrier_read("SH", "b01")
          == {"changelog": None, "reader": None})
    h, s, sp, st = world(sfile="STATUS9", changelog=lambda stand, block: {"changelog": "2026-09-20", "reader": "r"})
    check("... with one, its answer", h.barrier_read("SH", "b02") == {"changelog": "2026-09-20", "reader": "r"})
    (REPO / "research" / "scratch.txt").write_text("s", encoding="utf-8")
    fake.routes = {"/user/balance": bal(100.0), "/chat/completions": (200, {"model": "deepseek-v4-flash"})}
    h, s, sp, st = world("scored", sfile="STATUS10", anchor=HEAD, projected=1.0)
    _r, e = attempt(lambda: s.run_stand(sp, [SC.BlockPlan(block="b01", units=("u1",))], judges=(), order=1))
    text = (TMP / "STATUS10").read_text(encoding="utf-8") if (TMP / "STATUS10").exists() else ""
    check("HK-tree-scored-dirty: a real scored Scheduler with these hooks refuses the dirty stand before STAND START",
          isinstance(e, SC.SchedulerError) and "not clean" in str(e) and "STAND SH START" not in text, f"{e!r} {text[-200:]}")
finally:
    if fake is not None:
        fake.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 run hooks: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
