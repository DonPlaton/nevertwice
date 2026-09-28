#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A6 part 2b: run_v3.run_smoke end to end - the real scheduler, STATUS writer, hooks, incident gate,
stand plan and answerer, a real recording proxy (in this process) over a fake upstream, and fake arms as children
under a temporary launch contract (this suite spawns children):

* SMK-e2e: the S4 smoke of two arms prints one SMOKE_FIELDS line per arm-run, writes STATUS START..END for
  S4-smoke-1, the preflight record (with the forecast, before any spawn), summary.json and run.json;
* CAN-child-e2e (the auditor's condition for the smoke): a fake arm child reads .claude/CLAUDE.md from its unit's home
  and sends its text to its write port - the proxy refuses it and flags it canary, the arm-run's P0h counter is above
  0 in run.json and the exit code 1 names it; the canary's value is written nowhere;
* SMK-second: the next smoke is S4-smoke-2 at order 2, and a clean one exits 0;
* B-SMOKE-FLAGS: the proxy's zero-tolerance flags (a fake arm child's model_mismatch, tool_violation and unparsable
  body) are problems by kind and arm, counted in run.json, exit code 1; a canary is one event with its P0h count;
* B-ATTEMPT: an attempt that fails after its preflight and before STAND START exits 1 by name and leaves the smoke id
  unspent; the next attempt takes the same id in its own directories (named by its preflight line), never "not fresh".

    python tests/research/_test_v3_run_smoke.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


RV = _load("v3_run_v3_smk", ROOT / "research" / "v3" / "run_v3.py")
L = RV.load("launch.py", smoke=True)
SC = RV.load("scheduler.py", smoke=True)
P = RV.load("run_v3_proxy.py", smoke=True)
SM = RV.load("run_v3_smoke.py", smoke=True)
TP = RV.load("templates.py", smoke=True)
SL = RV.load("status_log.py", smoke=True)
SCTL = RV.load("sched_ctl.py", smoke=True)
SS = _load("v3_subsample_smk", ROOT / "research" / "v3" / "subsample.py")
PX = _load("v3_llm_proxy_smk", ROOT / "research" / "_llm_proxy.py")
ST = _load("v3_llm_proxy_selftest_smk", ROOT / "research" / "_llm_proxy_selftest.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


FAKE_ARM = r'''
import json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base as B
spec = json.load(open(sys.argv[1], encoding="utf-8"))


def send(body):
    req = urllib.request.Request(os.environ["NVT3_WRITE_URL"], data=body, method="POST",
                                 headers={"Authorization": "Bearer " + os.environ["NVT3_WRITE_TOKEN"],
                                          "Content-Type": "application/json"})
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20).read()
    except Exception:
        pass


class H:
    def __init__(self):
        self.items = []
        if spec["stage"] == "read":
            with open(os.path.join(spec["write_dir"], "store", "items.json"), encoding="utf-8") as f:
                self.items = json.load(f)
        if spec["stage"] == "write" and spec.get("leak"):     # a product that reads its home's CLAUDE.md
            with open(os.path.join(os.environ["HOME"], ".claude", "CLAUDE.md"), encoding="utf-8") as f:
                text = f.read()
            send(json.dumps({"model": "deepseek-flash", "thinking": {"type": "disabled"},
                             "messages": [{"role": "user", "content": "my notes: " + text}]}).encode())
        if spec["stage"] == "write" and spec.get("misbehave"):   # B-SMOKE-FLAGS: three zero-tolerance kinds
            msg = [{"role": "user", "content": "hello"}]
            send(json.dumps({"model": "deepseek-v4-pro", "messages": msg}).encode())            # model_mismatch
            send(json.dumps({"model": "deepseek-flash", "messages": msg,
                             "tools": [{"type": "function", "function": {"name": "Bash"}}]}).encode())  # tool_violation
            send(b'{"model": "deepseek-flash", ')                                                 # unparsable

    def hello(self):
        return {"protocol": B.PROTOCOL, "arm": spec["arm"], "stage": spec["stage"], "pid": os.getpid()}

    def write(self, item, date=None):
        self.items.append(item.get("text") or "")
        return {"op_id": item.get("item_id")}

    def end_write(self):
        os.makedirs(os.path.join(os.getcwd(), "store"), exist_ok=True)
        with open(os.path.join(os.getcwd(), "store", "items.json"), "w", encoding="utf-8") as f:
            json.dump(self.items, f)
        return {"footprint": {"retrievable": len(self.items)}, "seal": {"sha256": "0" * 64}}

    def read(self, qid, query, k=10):
        return {"qid": qid, "items": [{"text": t, "rank": i + 1} for i, t in enumerate(self.items[:k])]}

    def counters(self):
        return {}


sys.exit(B.main_with(H))
'''

V1 = json.dumps({"id": "c1", "model": "deepseek-v4-flash", "system_fingerprint": "fp_smoke",
                 "choices": [{"index": 0, "message": {"role": "assistant", "content": "It was blue.\nSHORT ANSWER: blue\n"},
                              "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 40, "completion_tokens": 6, "prompt_cache_hit_tokens": 0,
                           "prompt_cache_miss_tokens": 40, "completion_tokens_details": {"reasoning_tokens": 0}}}).encode()
BAL = json.dumps({"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "100.00"}]}).encode()


class Upstream(ST.FakeUpstream):
    pay_writer = False                                    # Q2: the balance gone - every call but the model probe is 402

    def _serve(self, c, path):
        if (self.pay_writer and path.split(b"?", 1)[0].endswith(b"/chat/completions")
                and not re.search(rb'"max_tokens":\s*1[,}]', self.requests[-1])):
            err = b'{"error":{"message":"Insufficient Balance"}}'
            self._send(c, b"HTTP/1.1 402 Payment Required\r\nContent-Type: application/json\r\nContent-Length: "
                       + str(len(err)).encode() + b"\r\n\r\n" + err)
            return True
        if path.split(b"?", 1)[0].endswith(b"/chat/completions"):
            body = V1
        elif path.startswith(b"/user/balance"):
            body = BAL
        else:
            return super()._serve(c, path)
        self._send(c, b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode()
                   + b"\r\n\r\n" + body)
        return True


class Quiet:
    """The native witness's sampler with nothing to see (its own suites test the real one)."""
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


TMP = Path(tempfile.mkdtemp(prefix="nvt3_run_smoke_"))
POLY = TMP / "polygon"
ARM_PY = Path(getattr(sys, "_base_executable", None) or sys.executable)
GIT = shutil.which("git")
EXC = {sys.executable: "the test interpreter", str(ARM_PY): "the fake arms' base interpreter"}
if GIT:
    EXC[GIT] = "git, for the tree check"
SYSTEM = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32",) if os.name == "nt" else (Path("/usr/bin"),)
(TMP / "owner_home").mkdir()
(TMP / "watched").mkdir()
(TMP / "watched" / "idle.txt").write_bytes(b"idle")
C = L.Contract(polygon_root=POLY, runs_root=POLY / "runs" / "v3", repo_root=ROOT, owner_home=TMP / "owner_home",
               secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine", conservation_root=TMP / "conservation",
               system_dirs=SYSTEM, binary_exceptions=EXC)
FAKE_DIR = POLY / "_fake"
FAKE_DIR.mkdir(parents=True)
(FAKE_DIR / "base.py").write_bytes((ROOT / "research" / "v3" / "arms" / "base.py").read_bytes())
(FAKE_DIR / "fake_arm.py").write_text(FAKE_ARM, encoding="utf-8")
KEYFILE = TMP / "keys" / "deepseek.env"
KEYFILE.parent.mkdir()
KEYFILE.write_bytes(f"DEEPSEEK_API_KEY={ST.SENTINEL_KEY}\n".encode())

ORDER = [f"q{i:03d}" for i in range(500)]
LISTS = TMP / "lists"
LISTS.mkdir()
S1 = SS.list_record("S1", ORDER, seed=SS.SEED, rule=SS.RULE)
(LISTS / "S1.json").write_text(json.dumps(S1), encoding="utf-8")


def lme_rec(i: int) -> dict:
    return {"question_id": f"q{i:03d}", "question_type": "multi-session", "question": f"what colour was thing {i}?",
            "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 14:05"],
            "haystack_sessions": [[{"role": "user", "content": f"thing {i} is blue"},
                                   {"role": "assistant", "content": "noted"}],
                                  [{"role": "user", "content": f"more about {i}"}]]}


TEMPLATE = TP.Template(stand="S4", text="Context:\n{context}\nQuestion: {question}\nEnd with SHORT ANSWER: <answer>",
                       sha256="0" * 64, source_pin="test", source_sha256="0" * 64, slots=("context", "question"))
TEMPLATE5 = TP.Template(stand="S4-cat5", text="Context:\n{context}\nQuestion: {question}\nMay be unanswerable.",
                        sha256="1" * 64, source_pin="test", source_sha256="1" * 64, slots=("context", "question"))
PROXIES: list = []


def start_proxy(c, *, python, key_file, config, secrets, unit, parent_env, witnesses):
    run_dir = Path(config["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    cfgp = run_dir / "proxy_config.json"
    cfgp.write_bytes(json.dumps(dict(config)).encode("utf-8"))
    pc = PX.ProxyConfig.load(cfgp, dict(secrets), test_upstream_ok=True)
    px = PX.Proxy(pc, PX.read_key(key_file), canaries=secrets.get("canaries") or {}, log=lambda m: None)
    ports = px.start()
    PROXIES.append({"px": px, "canaries": dict(secrets.get("canaries") or {})})
    return P.ProxyHandle(child=None, ports=ports, control=SCTL.ProxyControl(ports["control"], secrets["control_token"]),
                         tokens=dict(secrets["tokens"]), run_dir=run_dir)


def stop_proxy(h):
    left = PROXIES[-1]["px"].stop()
    return {"rc": 0 if not left else 1, "killed": False, "shutdown_error": None}


def make_launcher_factory(leak: set, misbehave: set = frozenset()):
    def make(arm, ar, *, stand_id, proxy, unit_block, unit_chars):
        writes = ar.llm_transport == "cloud:deepseek"

        def spec_for(stage, *, stand, run, unit, dirs, write_dirs):
            return {"arm": arm, "stage": stage, "run": run, "unit": unit, "leak": arm in leak,
                    "misbehave": arm in misbehave,
                    "write_dir": str(write_dirs.cwd) if write_dirs is not None else None}

        def declared_for(stage, *, stand, run, unit, dirs, write_dirs):
            return {"NVT3_WRITE_URL": P.url(proxy, arm, run, unit, suffix="/v1/chat/completions")} if writes else {}
        return SC.ChildArmLauncher(arm, argv_for=lambda p, **_: [str(ARM_PY), "-B", str(FAKE_DIR / "fake_arm.py"), str(p)],
                                   spec_for=spec_for, store_persistence="disk", path_dirs=(str(ARM_PY.parent),),
                                   declared={"NVT3_WRITE_TOKEN": proxy.tokens[arm]} if writes else {},
                                   token_names=("NVT3_WRITE_TOKEN",) if writes else (), declared_for=declared_for)
    return make


def deps_for(up, *, leak: set, out: list, err: list, misbehave: set = frozenset()):
    return RV.SmokeDeps(
        contract=C, L=L, native=L.NativeEgressWitness(sampler=Quiet(), tick_s=60, jobs=None),
        fs=L.FsWitness([L.WatchSpec("watched", TMP / "watched")]), clock=SC.SystemClock(), ollama_ctl=None,
        monotonic=__import__("time").monotonic, environ=dict(os.environ), status_path=TMP / "STATUS",
        lme_records=lambda: [lme_rec(i) for i in range(500)], cl100k=(lambda s: max(1, len(s) // 4),
                                                                      lambda s, n: s[:max(0, 4 * n)]),
        cl100k_source="t" * 64, max_token_bytes=128,
        truncate=lambda text: SimpleNamespace(text=text, truncated=False), templates=(TEMPLATE, TEMPLATE5),
        locomo_question=lambda q, cat: q,
        decl=lambda py, *, arm: {"python": str(py), "arm": arm, "version": "test"},
        make_launcher=make_launcher_factory(leak, misbehave), start_proxy=start_proxy, stop_proxy=stop_proxy, post=P.post,
        proxy_route={"test_upstream": {"host": "127.0.0.1", "port": up.port, "tls": False}},
        now_utc=lambda: "2026-09-28T00:00:00Z", lists_dir=LISTS, s1_sha256=S1["ids_sha256"],
        out=out.append, err=err.append)


CFG = RV.RunConfig(proxy_python=Path(sys.executable), key_file=KEYFILE, git=Path(GIT or "git"), campaign_seed=20260928,
                   embed_tag="nvt3-bge-m3-d1:latest", ollama_url="http://127.0.0.1:11434",
                   arms={"bm25-floor": RV.ArmRun(python=ARM_PY, llm=None, llm_transport=None, embeds_via_ollama=False),
                         "nevertwice": RV.ArmRun(python=ARM_PY, llm="deepseek-flash", llm_transport="cloud:deepseek",
                                                 embeds_via_ollama=False)},
                   sha256="c" * 64)
up = Upstream()
try:
    if GIT is None:
        check("git is on PATH (the stand's tree check runs it)", False)
        raise SystemExit
    print("- B-ATTEMPT: an attempt that fails after its preflight, before STAND START -")
    outa: list = []
    erra: list = []
    da = deps_for(up, leak=set(), out=outa, err=erra)

    def broken_launcher(*a, **k):
        raise RuntimeError("a launcher that cannot be built")
    da.make_launcher = broken_launcher
    try:
        rca, crasha = RV.run_smoke(CFG, stand="S4", arm_names=["bm25-floor"], runs=["r1"], deps=da), None
    except Exception as e:  # noqa: BLE001
        rca, crasha = None, f"{type(e).__name__}: {e}"
    st_a = (TMP / "STATUS").read_text(encoding="utf-8") if (TMP / "STATUS").exists() else ""
    check("B-ATTEMPT (1): the failed attempt exits 1 by name and writes no STAND START - S4-smoke-1 stays unspent",
          crasha is None and rca == 1 and "S4-smoke-1" not in st_a
          and any("the stand did not complete" in p for p in erra), f"{crasha} {rca} {erra[:3]}")

    print("\n- SMK-e2e and CAN-child-e2e: two arms, the nevertwice fake reads its home's CLAUDE.md -")
    out1: list = []
    err1: list = []
    try:
        rc1 = RV.run_smoke(CFG, stand="S4", arm_names=["bm25-floor", "nevertwice"], runs=["r1"],
                           deps=deps_for(up, leak={"nevertwice"}, out=out1, err=err1))
        crash1 = None
    except Exception as e:  # noqa: BLE001 - a crash FAILs the rows by name
        rc1, crash1 = None, f"{type(e).__name__}: {e}"
    status_text = (TMP / "STATUS").read_text(encoding="utf-8") if (TMP / "STATUS").exists() else ""
    sd1s = sorted((C.runs_root / "S4-smoke-1" / "_smoke").glob("attempt-*"))
    sd1 = sd1s[-1] if sd1s else C.runs_root / "S4-smoke-1" / "_smoke" / "none"
    run1 = json.loads((sd1 / "run.json").read_text(encoding="utf-8")) if (sd1 / "run.json").exists() else {}
    try:
        rows1 = SM.rows_from_lines("\n".join(out1))
    except Exception as e:  # noqa: BLE001
        rows1 = [f"unparsed: {e}"]
    check("B-ATTEMPT (2): the next attempt takes the same unspent id S4-smoke-1 in its own directories - never 'not "
          "fresh'; the failed attempt's own run.json names its failure", crash1 is None
          and [x.name for x in sd1s] == ["attempt-00001", "attempt-00002"]
          and len(list((C.runs_root / "S4-smoke-1" / "_proxy").glob("attempt-*"))) == 2
          and any("the stand did not complete" in p for p in json.loads(
              (sd1s[0] / "run.json").read_text(encoding="utf-8")).get("problems", [])), f"{crash1} {sd1s}")
    check("SMK-e2e: one SMOKE_FIELDS line per arm-run, STATUS START..END of S4-smoke-1, summary.json and run.json "
          "written", crash1 is None and len(rows1) == 2 and {(r.get("arm"), r.get("run")) for r in rows1 if
                                                            isinstance(r, dict)} == {("bm25-floor", "r1"), ("nevertwice", "r1")}
          and " STAND S4-smoke-1 START " in status_text and " STAND S4-smoke-1 END " in status_text
          and (sd1 / "summary.json").exists() and run1.get("stand") == "S4-smoke-1"
          and run1.get("gate") == "WallCapGate" and run1.get("wall_cap_h") == 6.0,
          f"{crash1} {rows1[:1]} {err1[:4]}")
    pfl = C.runs_root / "_launch" / "preflight.jsonl"
    pf = [json.loads(x) for x in pfl.read_text(encoding="utf-8").splitlines()] if pfl.exists() else []
    check("SMK-preflight: the chained preflight record names S4-smoke-1, both arms declared, the forecast in it (upper "
          "bound and estimate) - before any spawn", len(pf) >= 2 and pf[1]["stand_candidate"] == "S4-smoke-1"
          and pf[1]["ok"] is True and set(pf[1]["decl"]) == {"bm25-floor", "nevertwice"}
          and pf[1]["forecast"]["note"] == "upper bound, not pilot medians"
          and (pf[1]["forecast"].get("estimate") or {}).get("note") == "estimate, not a bound", str(pf[1:2])[:300])
    flags_f = C.runs_root / "S4-smoke-1" / "_proxy" / sd1.name / "flags.jsonl"
    flags = [json.loads(x) for x in flags_f.read_text(encoding="utf-8").splitlines()] if flags_f.exists() else []
    b_nw = (run1.get("boundary") or {}).get("nevertwice/r1") or {}
    b_bm = (run1.get("boundary") or {}).get("bm25-floor/r1") or {}
    check("CAN-child-e2e: the fake arm's request carrying its home's CLAUDE.md is refused and flagged canary; the "
          "arm-run's P0h canary_hits > 0 in run.json, the other arm's 0; the exit code is 1 and names P0h",
          rc1 == 1 and any(f.get("kind") == "canary" and f.get("arm") == "nevertwice" for f in flags)
          and b_nw.get("canary_hits", 0) > 0 and b_bm.get("canary_hits") == 0
          and any(p.startswith("problem: P0h: nevertwice/r1") for p in err1), f"{rc1} {flags[:2]} {b_nw} {err1[:4]}")
    can = PROXIES[0]["canaries"] if PROXIES else {}
    written = [p for p in C.runs_root.rglob("*") if p.is_file()
               and not any(x.lower().endswith(".home") for x in p.parts)          # the fake homes hold the decoys
               and p != C.runs_root / "CLAUDE.md"]                                # the runs-root decoy
    leaked = [str(p) for p in written if any(v.encode() in p.read_bytes() for v in can.values())]
    check("CAN-no-value: no canary value in any record, log or artifact the smoke wrote (the homes and the decoys "
          "aside)", can and written and leaked == [], str(leaked[:3]))

    print("\n- SMK-second: the next smoke id, a clean smoke -")
    out2: list = []
    err2: list = []
    try:
        rc2 = RV.run_smoke(CFG, stand="S4", arm_names=["bm25-floor"], runs=["r1"],
                           deps=deps_for(up, leak=set(), out=out2, err=err2))
        crash2 = None
    except Exception as e:  # noqa: BLE001
        rc2, crash2 = None, f"{type(e).__name__}: {e}"
    status_text = (TMP / "STATUS").read_text(encoding="utf-8") if (TMP / "STATUS").exists() else ""
    starts = [x for x in status_text.splitlines() if " STAND S4-smoke-2 START " in x]
    check("SMK-second: the second smoke is S4-smoke-2 at order 2, and a clean one exits 0",
          crash2 is None and rc2 == 0 and len(starts) == 1 and " order=2" in starts[0], f"{crash2} {rc2} {err2[:4]}")

    print("\n- B-SMOKE-FLAGS: the proxy's zero-tolerance flags are the smoke's problems -")
    out3: list = []
    err3: list = []
    try:
        rc3 = RV.run_smoke(CFG, stand="S4", arm_names=["bm25-floor", "nevertwice"], runs=["r1"],
                           deps=deps_for(up, leak=set(), misbehave={"nevertwice"}, out=out3, err=err3))
        crash3 = None
    except Exception as e:  # noqa: BLE001
        rc3, crash3 = None, f"{type(e).__name__}: {e}"
    sd3s = sorted((C.runs_root / "S4-smoke-3" / "_smoke").glob("attempt-*"))
    run3 = json.loads((sd3s[-1] / "run.json").read_text(encoding="utf-8")) if sd3s else {}
    fl3 = (run3.get("flags") or {}).get("nevertwice") or {}
    named = {k for k in ("model_mismatch", "tool_violation", "unparsable")
             if any(p.startswith(f"problem: flag: {k} nevertwice x") for p in err3)}
    check("B-SMOKE-FLAGS: a model_mismatch, a tool_violation and an unparsable body from a fake arm child are flagged "
          "by the proxy; each kind is a problem by name, the exit code is 1, and run.json counts the flags by arm and "
          "kind", crash3 is None and rc3 == 1 and named == {"model_mismatch", "tool_violation", "unparsable"}
          and set(fl3) == {"model_mismatch", "tool_violation", "unparsable"} and all(v >= 1 for v in fl3.values())
          and not (run3.get("flags") or {}).get("bm25-floor"), f"{crash3} {rc3} {fl3} {err3[:5]}")
    p0h_lines = [p for p in err1 if p.startswith("problem: P0h: nevertwice/r1")]
    check("B-SMOKE-FLAGS: a canary is one event - the P0h line says it is the same as the arm's canary flags, and no "
          "separate canary flag problem is added", len(p0h_lines) == 1 and "counted once" in p0h_lines[0]
          and not any(p.startswith("problem: flag: canary") for p in err1), str(err1[:5]))

    print("\n- Q2: a 402 on the arm's calls halts the incident gate - STAND END and run.json name it -")
    out4: list = []
    err4: list = []
    before4 = {p.name for p in C.runs_root.glob("S4-smoke-*")}
    up.pay_writer = True
    try:
        rc4 = RV.run_smoke(CFG, stand="S4", arm_names=["bm25-floor", "nevertwice"], runs=["r1"],
                           deps=deps_for(up, leak=set(), out=out4, err=err4))
        crash4 = None
    except Exception as e:  # noqa: BLE001
        rc4, crash4 = None, f"{type(e).__name__}: {e}"
    finally:
        up.pay_writer = False
    new4 = sorted({p.name for p in C.runs_root.glob("S4-smoke-*")} - before4)
    sd4s = sorted((C.runs_root / new4[-1] / "_smoke").glob("attempt-*")) if new4 else []
    run4 = json.loads((sd4s[-1] / "run.json").read_text(encoding="utf-8")) if sd4s else {}
    status4 = (TMP / "STATUS").read_text(encoding="utf-8") if (TMP / "STATUS").exists() else ""
    end4 = [x for x in status4.splitlines() if new4 and f" STAND {new4[-1]} END " in x]
    check("Q2 (F15): a 402 on the arm's calls halts the gate - STAND END says halt=402, run.json says halt=402 and not "
          "the wall cap, the problems say the halt first, the exit code is not 0, and the STATUS replays clean",
          crash4 is None and rc4 not in (0, None) and run4.get("halt") == "402" and run4.get("wall_cap_tripped") is False
          and len(end4) == 1 and " halt=402 " in end4[0]
          and any("HALT: the incident gate halted (402)" in p_ for p_ in err4) and SL.self_check(TMP / "STATUS") == [],
          f"{crash4} {rc4} {new4} halt={run4.get('halt')} {end4} {err4[:3]} {SL.self_check(TMP / 'STATUS')[:2]}")
finally:
    up.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 run smoke: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
