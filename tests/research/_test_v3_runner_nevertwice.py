#!/usr/bin/env python3
"""PREREG-V3 TB4.5b (A6): research/v3/arms/runner_nevertwice.py - our four arms' child runner, driven as a real child
process through base.py's protocol against a fake DeepSeek upstream and a fake Ollama on loopback (no mock inside the
child: the binding it proves is a property of a fresh process).

* the declared variables (rev1 §2.2): the URL is the proxy port's /u/<run>.<unit>/v1/chat/completions on 127.0.0.1, no
  dots in ids, fallback 0, XRERANK 0, deepseek-flash; the ranker has no LLM (CLOUD none, no port); only the ablation
  sets the window (Q14);
* the auditor's Q-45-1 O-c: the start record names isolate()'s temporary store, the unit store, config as the only
  module at the reload, CLOUD replaced (none -> deepseek) and XRERANK kept (0 = rev1's off), the embed tag re-set
  after isolate() scrubbed it; the unit store is where the product wrote; with a fake key, a password and the proxy
  token in the environment the record's bytes hold none of their values - each such name is "<set>" (09:27);
* refusals by name, before any write: an environment that differs from the declared values, a missing or sk- token,
  a token on the ranker, a write stage on an existing store, a read stage without one;
* the write goes out as rev1 declares it (F-N2): the proxy path /u/r1.u1/..., model deepseek-flash, thinking disabled,
  temperature 0.2, max_tokens 4096, the proxy token as the bearer; capture_session with project, session_id and date;
* Q25: the write stage's process exits; a new read-stage process opens the same store, reads with xrerank=False
  (xrerank_calls 0) and renders format_note; a write in the read stage and a read in the write stage are refused;
* footprint: typed notes only (the Session note is not retrievable); counters carry recall_degraded;
* S7 through the hook's reader on the renderer's JSONL; the ablation's variant reaches the upstream (no
  project_relevant field) and its record is in the start record; the ranker writes "item <index>" lessons without an
  LLM and reads back index and raw text;
* F-N3: the S7 wrapper's engine call sequence equals capture_session's (AST); every recall passes xrerank=False and
  project=, every capture_session passes project=, session_id= and date= (AST).

    python tests/research/_test_v3_runner_nevertwice.py
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

RUNNER = ROOT / "research" / "v3" / "arms" / "runner_nevertwice.py"
_spec = importlib.util.spec_from_file_location("v3_runner_nevertwice", RUNNER)
RN = importlib.util.module_from_spec(_spec)
sys.modules["v3_runner_nevertwice"] = RN
_spec.loader.exec_module(RN)
B = RN.B
_rspec = importlib.util.spec_from_file_location("v3_render_ama", ROOT / "research" / "v3" / "render_ama_jsonl.py")
RA = importlib.util.module_from_spec(_rspec)
sys.modules["v3_render_ama"] = RA
_rspec.loader.exec_module(RA)
PASSED = FAILED = 0
TAG = "nvt3-bge-m3-d1"


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


# ── the fake upstreams ────────────────────────────────────────────────────────────────────────────────────────────

EXTRACTION = {"project": "s1", "project_relevant": True,
              "patterns": [{"title": "bounded retry for the flaky upload",
                            "description": "Wrap the upload in a bounded retry with backoff; it fixed the flake.",
                            "facts": [], "supersedes": "", "contradicts": "", "resolves": "",
                            "entities": ["upload"], "relations": [], "confidence": 0.9}],
              "mistakes": [], "decisions": [], "context_update": "",
              "session_summary": "The session fixed the flaky upload with a bounded retry.", "tags": ["upload"]}


def vec(text: str) -> list[float]:
    """A deterministic bag-of-words vector: texts that share words point the same way."""
    v = [0.0] * 32
    for w in str(text).lower().split():
        v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % 32] += 1.0
    return v if any(v) else [1.0] + [0.0] * 31


class Fake:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, obj) -> None:
                data = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.calls.append({"method": "GET", "path": self.path})
                if self.path.endswith("/api/tags"):
                    return self._send(200, {"models": [{"name": TAG, "model": TAG}]})
                return self._send(404, {"error": "not here"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                fake.calls.append({"method": "POST", "path": self.path, "body": body,
                                   "auth": self.headers.get("Authorization")})
                if self.path.endswith("/api/embed"):
                    inp = body.get("input")
                    texts = inp if isinstance(inp, list) else [inp]
                    return self._send(200, {"model": body.get("model"), "embeddings": [vec(t) for t in texts],
                                            "prompt_eval_count": 7})
                if self.path.endswith("/v1/chat/completions"):
                    return self._send(200, {"id": "c1", "object": "chat.completion", "model": body.get("model"),
                                            "choices": [{"index": 0, "finish_reason": "stop",
                                                         "message": {"role": "assistant",
                                                                     "content": json.dumps(EXTRACTION)}}],
                                            "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                                                      "total_tokens": 150}})
                return self._send(404, {"error": "not here"})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def chats(self) -> list[dict]:
        return [c for c in self.calls if c["path"].endswith("/v1/chat/completions")]

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


# ── children ──────────────────────────────────────────────────────────────────────────────────────────────────────

TMP = Path(tempfile.mkdtemp(prefix="v3runner_"))
FAKE = Fake()
TOKEN = "nvt3-nevertwice-" + "c" * 32
PLANTED = {"NEVERTWICE_FAKE_API_KEY": "planted-fake-key-value-0101", "NEVERTWICE_DB_PASSWORD": "planted-password-0202"}
children: list = []


def child_env(declared: dict, *, token: str | None = TOKEN, extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in ("SystemRoot", "SystemDrive", "windir", "ComSpec", "PATHEXT",
                                       "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS") if k in os.environ}
    home = TMP / "homes" / uuid.uuid4().hex[:8]
    for d in (home / "Temp", home / "AppData" / "Roaming", home / "AppData" / "Local"):
        d.mkdir(parents=True, exist_ok=True)
    (home / "gitconfig").write_bytes(b"")
    git = shutil.which("git")
    dirs = [str(Path(sys.executable).parent)] + ([str(Path(git).parent)] if git else [])
    if os.name == "nt":
        dirs.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
    else:
        dirs += ["/usr/bin", "/bin"]
    env.update({"PATH": os.pathsep.join(dirs), "HOME": str(home), "USERPROFILE": str(home),
                "TEMP": str(home / "Temp"), "TMP": str(home / "Temp"), "TMPDIR": str(home / "Temp"),
                "APPDATA": str(home / "AppData" / "Roaming"), "LOCALAPPDATA": str(home / "AppData" / "Local"),
                "PYTHONPYCACHEPREFIX": str(TMP / "pycache"), "NO_PROXY": "127.0.0.1,localhost",
                "GIT_CONFIG_GLOBAL": str(home / "gitconfig"), "GIT_CONFIG_SYSTEM": str(home / "gitconfig"),
                "OLLAMA_URL": f"http://127.0.0.1:{FAKE.port}/api/generate",
                "OLLAMA_TAGS_URL": f"http://127.0.0.1:{FAKE.port}/api/tags",
                "OLLAMA_EMBED_URL": f"http://127.0.0.1:{FAKE.port}/api/embed"})
    env.update(declared)
    if token is not None:
        env[RN.TOKEN_NAME] = token
    env.update(extra or {})
    return env


def make_spec(name: str, arm: str, stage: str, unit_dir: Path, *, run="r1", unit="u1", s7=False,
              max_transcript=None, extract_temp=None) -> dict:
    return {"arm": arm, "stage": stage, "stand": "s1", "run": run, "unit": unit, "unit_dir": str(unit_dir),
            "port": None if arm == RN.RANKER else FAKE.port, "embed_tag": TAG, "extract_temp": extract_temp,
            "max_transcript": max_transcript, "s7": s7, "record_path": str(TMP / f"{name}.start.json")}


def start(name: str, spec: dict, *, env: dict | None = None, cwd: Path | None = None):
    sp = TMP / f"{name}.spec.json"
    sp.write_text(json.dumps(spec), encoding="utf-8")
    if env is None:
        env = child_env(RN.declared_env(spec["arm"], run=spec["run"], unit=spec["unit"], port=spec["port"],
                                        embed_tag=TAG, extract_temp=spec["extract_temp"],
                                        max_transcript=spec["max_transcript"]),
                        token=None if spec["arm"] == RN.RANKER else TOKEN)
    wd = cwd or TMP / f"cwd_{name}"
    wd.mkdir(parents=True, exist_ok=True)
    errf = TMP / f"{name}.err.txt"
    p = subprocess.Popen([sys.executable, "-B", str(RUNNER), str(sp)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=open(errf, "wb"), cwd=str(wd), env=env)
    children.append(p)
    return B.ArmClient(p, default_timeout=180), p, errf


def record(name: str) -> dict:
    return safely(lambda: json.loads((TMP / f"{name}.start.json").read_bytes().decode("utf-8")), {})


def refused_hello(name: str, spec: dict, env: dict | None = None) -> str:
    c, p, _ = start(name, spec, env=env)
    try:
        c.request("hello")
        return "no refusal"
    except B.ArmError as e:
        return str(e)
    finally:
        c.close()


try:
    print("\n- the declared variables (rev1 §2.2) -")
    d = RN.declared_env("nevertwice", run="r1", unit="u1", port=41000, embed_tag=TAG)
    check("the URL is the proxy port's /u/<run>.<unit>/v1/chat/completions on 127.0.0.1",
          d.get("DEEPSEEK_URL") == "http://127.0.0.1:41000/u/r1.u1/v1/chat/completions", str(d.get("DEEPSEEK_URL")))
    check("deepseek-flash, fallback 0, XRERANK 0, the embed tag, CLOUD deepseek",
          d.get("NEVERTWICE_DEEPSEEK_MODEL") == "deepseek-flash" and d.get("NEVERTWICE_CLOUD_FALLBACK") == "0"
          and d.get("NEVERTWICE_XRERANK") == "0" and d.get("NEVERTWICE_EMBED_MODEL") == TAG
          and d.get("NEVERTWICE_CLOUD") == "deepseek", str(d))
    check("the proxy token is never a declared value (it travels in the environment only)", RN.TOKEN_NAME not in d)
    check("a run or unit id with a dot is refused (Q3)",
          raises(lambda: RN.declared_env("nevertwice", run="r.1", unit="u1", port=41000, embed_tag=TAG), ValueError,
                 "no dots"))
    dr = RN.declared_env(RN.RANKER, run="r1", unit="u1", port=None, embed_tag=TAG)
    check("the ranker has no LLM: CLOUD none, no URL, no fallback variable",
          dr.get("NEVERTWICE_CLOUD") == "none" and "DEEPSEEK_URL" not in dr and "NEVERTWICE_CLOUD_FALLBACK" not in dr,
          str(dr))
    check("... and a proxy port for the ranker is refused",
          raises(lambda: RN.declared_env(RN.RANKER, run="r1", unit="u1", port=41000, embed_tag=TAG), ValueError,
                 "no LLM"))
    check("the ablation needs its window (Q14), and only the ablation sets one",
          raises(lambda: RN.declared_env("nevertwice-ablation", run="r1", unit="u1", port=1, embed_tag=TAG), ValueError,
                 "Q14")
          and raises(lambda: RN.declared_env("nevertwice", run="r1", unit="u1", port=1, embed_tag=TAG,
                                             max_transcript=100), ValueError, "only the ablation")
          and RN.declared_env("nevertwice-ablation", run="r1", unit="u1", port=1, embed_tag=TAG,
                              max_transcript=5000).get("NEVERTWICE_MAX_TRANSCRIPT") == "5000")
    check("the extraction temperature is declared only when set (the sensitivity row), as a float",
          "NEVERTWICE_EXTRACT_TEMP" not in d and RN.declared_env("nevertwice", run="r1", unit="u1", port=1,
                                                                 embed_tag=TAG, extract_temp=0)
          .get("NEVERTWICE_EXTRACT_TEMP") == "0.0")
    check("masked(): KEY / TOKEN / SECRET / PASSWORD in any case -> <set>; other names keep their value",
          [RN.masked(n, "v") for n in ("DEEPSEEK_API_KEY", "x_token", "NEVERTWICE_Secret_x", "db_password",
                                       "NEVERTWICE_CLOUD")] == ["<set>"] * 4 + ["v"])

    print("\n- the positive checks of Q-45-1 step 5, each by name (in-process, on fakes) -")
    PC = TMP / "pc"
    ST, TR, TEMP_STORE = PC / "store", PC / "transcripts", PC / "tempstore"
    URL = RN.proxy_url(41000, "r1", "u1")

    class FakeSG:
        class SandboxEscape(RuntimeError):
            pass

        def __init__(self, mods, live):
            self.mods, self.live = mods, live

        def _loaded_project_modules(self):
            return dict(self.mods)

        def store(self):
            return TEMP_STORE

        def verify_no_live_paths(self):
            if self.live:
                raise self.SandboxEscape("a module constant points inside a real store:\n  - x.EMBED_CACHE=...")

    def pc(arm="nevertwice", m_over=None, cfg_over=None, extra_mods=None, live=False, xrerank="0", api_other=False,
           token=TOKEN, max_transcript=None):
        spec = {"arm": arm, "run": "r1", "unit": "u1", "port": None if arm == RN.RANKER else 41000, "embed_tag": TAG,
                "extract_temp": None, "max_transcript": max_transcript}
        declared = RN.declared_env(arm, run="r1", unit="u1", port=spec["port"], embed_tag=TAG,
                                   max_transcript=max_transcript)
        md = dict(VAULT=ST, EMBED_MODEL=TAG, ACTIVE_CLOUD="none" if arm == RN.RANKER else "deepseek",
                  DEEPSEEK_MODEL="deepseek-flash", DEEPSEEK_URL=URL, provider_key=lambda _p: TOKEN,
                  cloud_fallback_enabled=lambda: False, extract_temperature=lambda: 0.2, EXTRACT_NUM_PREDICT=4096,
                  MAX_TRANSCRIPT_CHARS=12000 if max_transcript is None else max_transcript)
        md.update(m_over or {})
        m = NS(**md)
        cfg = NS(**{"VAULT": ST, "PROJECTS_ROOT": TR, **(cfg_over or {})})
        mods = {"config": cfg, "memory_hook": m, **(extra_mods or {})}
        return RN.positive_checks(spec, declared, token, cfg=cfg, m=m, api=NS(m=NS() if api_other else m),
                                  SG=FakeSG(mods, live), store=ST, transcripts=TR,
                                  environ={"NEVERTWICE_XRERANK": xrerank})

    check("a correct binding has no problem - for the full arm, the ranker and the ablation",
          pc() == [] and pc(RN.RANKER, token=None) == [] and pc("nevertwice-ablation", max_transcript=4321) == [],
          str((pc(), pc(RN.RANKER, token=None), pc("nevertwice-ablation", max_transcript=4321))))
    CASES = [
        ("config.VAULT on isolate()'s store (no reload)", dict(cfg_over={"VAULT": TEMP_STORE}),
         "config.VAULT is not the unit store"),
        ("the engine's VAULT elsewhere", dict(m_over={"VAULT": PC / "elsewhere"}), "the engine's VAULT"),
        ("another loaded module's VAULT elsewhere", dict(extra_mods={"graph": NS(VAULT=PC / "elsewhere")}),
         "graph.VAULT is not the unit store"),
        ("a Path constant baked on isolate()'s store", dict(extra_mods={"graph": NS(INDEX=TEMP_STORE / "i.json")}),
         "graph.INDEX points into isolate()'s temporary store"),
        ("a Path constant inside a real store", dict(live=True), "verify_no_live_paths"),
        ("PROJECTS_ROOT elsewhere", dict(cfg_over={"PROJECTS_ROOT": PC / "home" / ".claude" / "projects"}),
         "PROJECTS_ROOT is not the unit's transcripts"),
        ("api on another engine namespace", dict(api_other=True), "api drives another engine namespace"),
        ("the embed tag scrubbed by isolate()", dict(m_over={"EMBED_MODEL": "bge-m3"}), "EMBED_MODEL is not the declared"),
        ("XRERANK not 0", dict(xrerank="1"), "NEVERTWICE_XRERANK is not 0"),
        ("CLOUD left at isolate()'s none", dict(m_over={"ACTIVE_CLOUD": "none"}), "ACTIVE_CLOUD is not deepseek"),
        ("the legacy DeepSeek model", dict(m_over={"DEEPSEEK_MODEL": "deepseek-v4-flash"}), "DEEPSEEK_MODEL is not"),
        ("the base URL only", dict(m_over={"DEEPSEEK_URL": "http://127.0.0.1:41000/v1/chat/completions"}),
         "DEEPSEEK_URL is not the declared"),
        ("the engine reading another key", dict(m_over={"provider_key": lambda _p: "nvt3-other"}),
         "the DeepSeek key read by the engine"),
        ("the local fallback on", dict(m_over={"cloud_fallback_enabled": lambda: True}), "the local fallback is on"),
        ("another extraction temperature", dict(m_over={"extract_temperature": lambda: 0.7}),
         "extract_temperature() is not"),
        ("another output cap", dict(m_over={"EXTRACT_NUM_PREDICT": 8192}), "EXTRACT_NUM_PREDICT is not 4096"),
    ]
    for label, kw, words in CASES:
        got = pc(**kw)
        # a moved VAULT is named by every check that sees it (config, the engine, each loaded module) - all of them VAULT
        ok = any(words in g for g in got) and (all("VAULT" in g for g in got) if "VAULT" in words else len(got) == 1)
        check(f"refused by name: {label}", ok, str(got))
    got = pc(RN.RANKER, token=None, m_over={"ACTIVE_CLOUD": "deepseek"})
    check("refused by name: the ranker with a cloud LLM", len(got) == 1 and "the ranker's ACTIVE_CLOUD" in got[0], str(got))
    got = pc("nevertwice-ablation", max_transcript=4321, m_over={"MAX_TRANSCRIPT_CHARS": 12000})
    check("refused by name: the ablation's window not the unit's length",
          len(got) == 1 and "MAX_TRANSCRIPT_CHARS" in got[0], str(got))
    check("check_token: an LLM arm needs nvt3-, so neither none nor an sk- key passes; the ranker needs none",
          raises(lambda: RN.check_token("nevertwice", None), RN.Refused, "proxy token")
          and raises(lambda: RN.check_token("nevertwice", "sk-" + "a" * 30), RN.Refused, "proxy token")
          and RN.check_token("nevertwice", TOKEN) is None and RN.check_token(RN.RANKER, None) is None
          and raises(lambda: RN.check_token(RN.RANKER, TOKEN), RN.Refused, "ranker has no LLM"))
    check("only_config: config alone passes; anything else loaded before the reload is refused by name",
          RN.only_config(["config"]) is None
          and raises(lambda: RN.only_config(["config", "memory_hook"]), RN.Refused, "other than config"))

    print("\n- bind refuses unless isolate() ran first (in-process, on a fake sandbox_guard) -")
    real_sg = RN.sandbox_guard
    RN.sandbox_guard = NS(mode=lambda: None, store=lambda: None, _loaded_project_modules=lambda: {})
    UI = TMP / "iso" / "u1"
    UI.mkdir(parents=True)
    env_i = dict(RN.declared_env("nevertwice", run="r1", unit="u1", port=41000, embed_tag=TAG), **{RN.TOKEN_NAME: TOKEN})
    saved_env = dict(os.environ)
    try:
        msg_i = ""
        try:
            RN.bind({"arm": "nevertwice", "stage": "write", "stand": "s1", "run": "r1", "unit": "u1",
                     "unit_dir": str(UI), "port": 41000, "embed_tag": TAG, "extract_temp": None,
                     "max_transcript": None, "s7": False, "record_path": str(TMP / "iso.json")}, env_i)
        except Exception as e:  # noqa: BLE001
            msg_i = f"{type(e).__name__}: {e}"
        check("bind refuses by name when sandbox_guard.isolate() did not run, before it touches the environment",
              "Refused" in msg_i and "isolate() did not run" in msg_i and dict(os.environ) == saved_env, msg_i[:200])
    finally:
        RN.sandbox_guard = real_sg
        os.environ.clear()
        os.environ.update(saved_env)

    print("\n- the footprint: typed notes of the unit's project only (in-process, on a fake cache) -")
    fake_cache = {"p1": {"ntype": "pattern", "project": "s1", "title": "t", "desc": "d", "prevention": "", "vec": [1.0]},
                  "m1": {"ntype": "mistake", "project": "s1", "title": "t2", "desc": "d2", "prevention": "p", "vec": None},
                  "ss": {"ntype": "session", "project": "s1", "title": "Session", "desc": "summary", "vec": [1.0]},
                  "pr": {"ntype": "principle", "project": "_universal", "title": "x", "desc": "y", "vec": [1.0]},
                  "op": {"ntype": "pattern", "project": "other", "title": "o", "desc": "o", "vec": [1.0]}}
    fm = NS(slug_project=lambda p: p, load_embed_cache=lambda: fake_cache, TYPED_TYPES=("pattern", "mistake", "decision"))
    fapi = NS(format_note=lambda r: "x" * 10)
    fh = RN.Handler({"arm": "nevertwice", "stage": "write", "stand": "s1", "unit": "u1"},
                    {"m": fm, "api": fapi, "pacer": None}, {})
    fp = safely(lambda: fh._footprint(), {})
    check("footprint: a Session note, a principle and another project's note are never retrievable; embedded counts "
          "vectors", fp == {"retrievable": 2, "embedded": 1, "chars": 20}, str(fp))

    print("\n- the ranker's read and end_write on fakes (the auditor's N15, N16) -")
    UF = TMP / "fake_ranker_unit"
    (UF / "store").mkdir(parents=True)
    spec_f = {"arm": RN.RANKER, "stage": "read", "stand": "s1", "run": "r1", "unit": "u1", "unit_dir": str(UF), "s7": False}

    def ranker_read(titles):
        hits = [{"title": ti, "description": "d", "ntype": "pattern"} for ti in titles]
        fh = RN.Handler(spec_f, {"m": NS(), "api": NS(recall=lambda *a, **k: hits), "pacer": None}, {})
        return fh.read(qid="q", query="x", k=5)
    check("N15: the ranker reads back its own items by index", safely(lambda: ranker_read(["item 3", "item 12"]), {})
          .get("items") == [{"index": 3, "text": "d", "rank": 1}, {"index": 12, "text": "d", "rank": 2}])
    for bad in ("note about 42", "item 12 extra", "Item 3", "item", "an item 7"):
        check(f"N15: a hit titled {bad!r} is not one of the unit's items - refused by name",
              raises(lambda bad=bad: ranker_read(["item 1", bad]), RuntimeError, "not one of the unit's items"))
    written = {}

    def remember(lessons, *, project):
        written["lessons"], written["project"] = lessons, project
        return ["s0", None, "s2"]
    fmw = NS(slug_project=lambda p: p, load_embed_cache=lambda: {}, TYPED_TYPES=("pattern",), _LLM_STATS={})
    fw = RN.Handler(dict(spec_f, stage="write"), {"m": fmw, "api": NS(remember_lessons_aligned=remember,
                                                                        format_note=lambda r: "",
                                                                        recall_stats=lambda: {}),
                                                   "pacer": NS(attach=lambda out: None)}, {})
    for i in (12, 5, 9):                                  # sparse indices: position 1 is index 9, never index 1
        safely(lambda i=i: fw.write({"item_id": f"u1:{i}", "index": i, "text": f"text {i}"}), {})
    ew = safely(lambda: fw.end_write(), {})
    check("N16: an item remember_lessons_aligned did not write (None) is in not_written, by its index",
          ew.get("not_written") == [9], str(ew)[:200])
    check("N16: ... and counted in counters", safely(lambda: fw.counters(), {}).get("not_written") == 1)
    check("the ranker's lessons: \"item <index>\" titles in index order, the raw text, type pattern (Q-45-3)",
          written.get("lessons") == [{"title": f"item {i}", "description": f"text {i}", "type": "pattern"}
                                     for i in (5, 9, 12)] and written.get("project") == "s1", str(written)[:200])

    print("\n- the write stage of the nevertwice arm (Q-45-1 O-c, F-N2) -")
    U1 = TMP / "runs" / "s1" / "r1" / "nevertwice" / "u1"
    U1.mkdir(parents=True)
    spec_w = make_spec("w1", "nevertwice", "write", U1)
    env_w = child_env(RN.declared_env("nevertwice", run="r1", unit="u1", port=FAKE.port, embed_tag=TAG),
                      extra=PLANTED)
    c, p, err_w = start("w1", spec_w, env=env_w, cwd=U1)
    h = safely(lambda: c.request("hello"), {})
    st = h.get("start") or {}
    check("hello: the protocol, our system, the stage, the LLM and embedder labels",
          h.get("protocol") == B.PROTOCOL and h.get("system") == "nevertwice" and h.get("stage") == "write"
          and h.get("llm_label") == "deepseek:deepseek-flash" and h.get("embedder") == TAG,
          str({k: h.get(k) for k in ("protocol", "llm_label", "embedder", "error")})[:300]
          + " | " + err_w.read_bytes().decode("utf-8", "replace")[-400:])
    check("the start record names isolate()'s temporary store and the unit store (not the same)",
          bool(st.get("temp_store")) and st.get("unit_store") == str(U1 / "store")
          and not RN._same(st.get("temp_store") or ".", U1 / "store"), str({k: st.get(k) for k in
                                                                            ("temp_store", "unit_store")}))
    check("config was the only project module at the reload", st.get("modules_at_reload") == ["config"],
          str(st.get("modules_at_reload")))
    check("CLOUD from isolate() (none) was replaced by the declared deepseek, and the embed tag set again",
          (st.get("isolate_values") or {}).get("NEVERTWICE_CLOUD") == "none"
          and "NEVERTWICE_EMBED_MODEL" in (st.get("isolate_values") or {})
          and (st.get("isolate_values") or {}).get("NEVERTWICE_EMBED_MODEL") is None
          and (st.get("nevertwice_env") or {}).get("NEVERTWICE_CLOUD") == "deepseek"
          and (st.get("nevertwice_env") or {}).get("NEVERTWICE_EMBED_MODEL") == TAG, str(st.get("isolate_values")))
    check("XRERANK=0 from isolate() equals rev1's off: kept and recorded",
          (st.get("kept_from_isolate") or {}).get("NEVERTWICE_XRERANK") == "0", str(st.get("kept_from_isolate")))
    check("the positive checks all ran", st.get("checks") == ["declared values and token",
                                                               "reload with only config loaded",
                                                               "stores, paths and backend"], str(st.get("checks")))
    rbytes = (TMP / "w1.start.json").read_bytes() if (TMP / "w1.start.json").exists() else b""
    check("the start record file holds no planted key or password value and no proxy token (09:27)",
          rbytes and all(v.encode() not in rbytes for v in (*PLANTED.values(), TOKEN)), f"{len(rbytes)} bytes")
    check("... each such name is recorded as <set>",
          all((st.get("nevertwice_env") or {}).get(n) == "<set>" for n in PLANTED) and RN.TOKEN_NAME in
          (st.get("env_names") or []), str({n: (st.get("nevertwice_env") or {}).get(n) for n in PLANTED}))
    w = safely(lambda: c.request("write", item={"item_id": "u1:0", "session_id": "0",
                                                "text": "user: the upload is flaky\nassistant: add a bounded retry"},
                                 date="2023-05-20"), {})
    out = w.get("outcome") or {}
    check("write: capture_session stored the session (one pattern)", out.get("stored") is True
          and out.get("patterns") == 1 and out.get("session_id") == "u1-0", str(w)[:300])
    ch = FAKE.chats()
    last = ch[-1] if ch else {}
    body = last.get("body") or {}
    check("F-N2: the extraction went to the proxy path /u/r1.u1/v1/chat/completions",
          last.get("path") == "/u/r1.u1/v1/chat/completions", str(last.get("path")))
    check("F-N2: model deepseek-flash, thinking disabled, temperature 0.2, max_tokens 4096, JSON mode",
          body.get("model") == "deepseek-flash" and body.get("thinking") == {"type": "disabled"}
          and body.get("temperature") == 0.2 and body.get("max_tokens") == 4096
          and body.get("response_format") == {"type": "json_object"}, str({k: body.get(k) for k in
                                                                          ("model", "thinking", "temperature",
                                                                           "max_tokens")}))
    check("F-N2: the bearer is the arm's proxy token", last.get("auth") == f"Bearer {TOKEN}")
    W1_PROMPT = ((body.get("messages") or [{}])[0].get("content")) or ""
    check("the session's date reached the product (date=)",
          any("2023-05-20" in f.name for f in (U1 / "store").rglob("*.md")),
          str(sorted(f.name for f in (U1 / "store").rglob("*.md"))[:6]))
    embeds = [x for x in FAKE.calls if x["path"].endswith("/api/embed")]
    check("the product embedded with the declared tag", embeds and all(x["body"].get("model") == TAG for x in embeds),
          str({x["body"].get("model") for x in embeds}))
    e = safely(lambda: c.request("end_write"), {})
    check("footprint: typed notes only - the Session note is not retrievable",
          (e.get("footprint") or {}).get("retrievable") == 1 and (e.get("footprint") or {}).get("embedded") == 1,
          str(e.get("footprint")))
    check("a read in the write stage is refused by name (Q25)",
          raises(lambda: c.request("read", qid="q", query="upload", k=5), B.ArmError, "read stage"))
    cn = safely(lambda: c.request("counters"), {})
    check("counters: the LLM calls, recall's degraded / empty-store counters, the write outcomes, the transport",
          (cn.get("llm_stats") or {}).get("cloud") == 1 and "recall_degraded" in (cn.get("recall") or {})
          and "recall_empty_store" in (cn.get("recall") or {}) and (cn.get("outcomes") or {}).get("stored") == 1
          and ((cn.get("ollama_transport") or {}).get("calls") or 0) >= 1, str(cn)[:300])
    rc = c.close()
    check("the write stage's process exits after bye (Q25)", rc == 0, str(rc))
    check("the product wrote into the unit store", any((U1 / "store").rglob("*.md")))

    print("\n- the read stage: a new process on the same store (Q25) -")
    spec_r = make_spec("r1", "nevertwice", "read", U1)
    c, p, err_r = start("r1", spec_r)
    hr = safely(lambda: c.request("hello"), {})
    check("the read stage binds to the existing store", hr.get("ok") is True and hr.get("stage") == "read",
          str(hr)[:200] + err_r.read_bytes().decode("utf-8", "replace")[-300:])
    r = safely(lambda: c.request("read", qid="q1", query="how was the flaky upload fixed", k=5), {})
    items = r.get("items") or []
    check("read: recall's hit rendered by format_note, in rank order",
          items and items[0].get("rank") == 1 and items[0]["text"].startswith("PATTERN - bounded retry"),
          str(items)[:200])
    cr = safely(lambda: c.request("counters"), {})
    check("xrerank_calls is 0 (every read passes xrerank=False) and nothing degraded",
          (cr.get("recall") or {}).get("xrerank_calls") == 0 and (cr.get("recall") or {}).get("recall_degraded") == 0,
          str(cr.get("recall")))
    check("a write in the read stage is refused by name (Q25)",
          raises(lambda: c.request("write", item={"item_id": "x", "session_id": "9", "text": "t"}), B.ArmError,
                 "write stage"))
    c.close()

    print("\n- refusals by name, before any write -")
    U2 = TMP / "runs" / "s1" / "r1" / "nevertwice" / "u2"
    U2.mkdir(parents=True)
    s2 = make_spec("x1", "nevertwice", "write", U2, unit="u2")
    base_env = RN.declared_env("nevertwice", run="r1", unit="u2", port=FAKE.port, embed_tag=TAG)
    bad_url = dict(base_env, DEEPSEEK_URL=f"http://127.0.0.1:{FAKE.port}/v1/chat/completions")
    msg = refused_hello("x1", s2, env=child_env(bad_url))
    check("an environment whose DEEPSEEK_URL is the base URL only is refused by name",
          "refused to start" in msg and "DEEPSEEK_URL" in msg, msg[:200])
    check("... the refusal is recorded (ok false, the reason)",
          record("x1").get("ok") is False and "DEEPSEEK_URL" in (record("x1").get("error") or ""))
    msg = refused_hello("x2", dict(s2, record_path=str(TMP / "x2.start.json")), env=child_env(base_env, token=None))
    check("an LLM arm without its proxy token is refused", "proxy token" in msg, msg[:200])
    msg = refused_hello("x3", dict(s2, record_path=str(TMP / "x3.start.json")),
                        env=child_env(base_env, token="sk-" + "z" * 30))
    check("an sk- value in the token variable is refused", "proxy token" in msg, msg[:200])
    fb_on = dict(base_env, NEVERTWICE_CLOUD_FALLBACK="1")
    msg = refused_hello("x4", dict(s2, record_path=str(TMP / "x4.start.json")), env=child_env(fb_on))
    check("an environment with the local fallback on is refused", "NEVERTWICE_CLOUD_FALLBACK" in msg, msg[:200])
    msg = refused_hello("x5", make_spec("x5", "nevertwice", "write", U1))
    check("a write stage on an existing store is refused (a fresh store per unit)", "fresh store" in msg, msg[:200])
    U3 = TMP / "runs" / "s1" / "r1" / "nevertwice" / "u3"
    U3.mkdir(parents=True)
    msg = refused_hello("x6", make_spec("x6", "nevertwice", "read", U3, unit="u3"))
    check("a read stage without a store is refused", "there is none" in msg, msg[:200])
    UR = TMP / "runs" / "s1" / "r1" / RN.RANKER / "u9"
    UR.mkdir(parents=True)
    rk = make_spec("x7", RN.RANKER, "write", UR, unit="u9")
    msg = refused_hello("x7", rk, env=child_env(RN.declared_env(RN.RANKER, run="r1", unit="u9", port=None,
                                                               embed_tag=TAG), token=TOKEN))
    check("the ranker with a proxy token set is refused (it has no LLM)", "ranker has no LLM" in msg, msg[:200])
    msg = refused_hello("x8", dict(s2, record_path=str(TMP / "x8.start.json"), stage="erase"))
    check("an unknown stage in the spec is refused", "unknown arm" in msg, msg[:200])

    print("\n- the ranker: api.remember_lessons_aligned without an LLM, then recall (Q21, Q-45-3) -")
    UK = TMP / "runs" / "s1" / "r1" / RN.RANKER / "u1"
    UK.mkdir(parents=True)
    c, p, err_k = start("k1", make_spec("k1", RN.RANKER, "write", UK), cwd=UK)
    hk = safely(lambda: c.request("hello"), {})
    check("the ranker binds with CLOUD none", hk.get("llm_label") == "none",
          str(hk)[:200] + err_k.read_bytes().decode("utf-8", "replace")[-300:])
    n_chat = len(FAKE.chats())
    texts = {0: "the cat sat on the mat", 1: "a bounded retry fixed the flaky upload", 2: "paris is in france"}
    for i, t in texts.items():
        safely(lambda i=i, t=t: c.request("write", item={"item_id": f"u1:{i}", "index": i, "text": t}), {})
    check("a repeated ranker index is refused",
          raises(lambda: c.request("write", item={"item_id": "u1:1b", "index": 1, "text": "x"}), B.ArmError, "index"))
    ek = safely(lambda: c.request("end_write"), {})
    check("end_write writes the three items as typed notes, none refused",
          (ek.get("footprint") or {}).get("retrievable") == 3 and ek.get("not_written") == [], str(ek)[:200])
    check("no LLM call was made for the ranker", len(FAKE.chats()) == n_chat)
    c.close()
    c, p, _ = start("k2", make_spec("k2", RN.RANKER, "read", UK))
    rk = safely(lambda: c.request("read", qid="q", query="flaky upload retry", k=2), {})
    ki = rk.get("items") or []
    check("read returns the unit's index and the raw text (\"#<index>\" is rendered by the harness, Q21)",
          ki and ki[0] == {"index": 1, "text": texts[1], "rank": 1}, str(ki)[:200])
    c.close()

    print("\n- S7 through the hook's reader, and the ablation -")
    US = TMP / "runs" / "s7" / "r1" / "nevertwice" / "u1"
    US.mkdir(parents=True)
    run7 = {"episode_id": "e1", "task": "make the upload reliable", "num_turns": 1,
            "trajectory": [{"turn_idx": 0, "action": "retry --bounded upload", "observation": "OBS-SECRET-LINE"}]}
    tp = TMP / "s7_e1.jsonl"
    RA.write_jsonl(RA.render(run7, unit_dir=US, ingest_utc="2026-09-27T00:00:00Z"), tp)
    c, p, err_s = start("s7", make_spec("s7", "nevertwice", "write", US, s7=True), cwd=US)
    n_chat = len(FAKE.chats())
    s7w = safely(lambda: c.request("write", item={"item_id": "e1", "session_id": "e1",
                                                  "transcript_path": str(tp)}), {})
    check("S7: the hook path stored the trajectory", (s7w.get("outcome") or {}).get("stored") is True,
          str(s7w)[:300] + err_s.read_bytes().decode("utf-8", "replace")[-300:])
    prompt = ""
    if len(FAKE.chats()) > n_chat:
        prompt = FAKE.chats()[-1]["body"]["messages"][0]["content"]
    check("S7: the action reached the extractor and the observation did not (the reader drops tool_result)",
          "retry --bounded upload" in prompt and "OBS-SECRET-LINE" not in prompt, prompt[-200:])
    c.close()
    UA = TMP / "runs" / "s1" / "r1" / "nevertwice-ablation" / "u1"
    UA.mkdir(parents=True)
    c, p, err_a = start("a1", make_spec("a1", "nevertwice-ablation", "write", UA, max_transcript=4321), cwd=UA)
    ha = safely(lambda: c.request("hello"), {})
    abl = ((ha.get("start") or {}).get("ablation")) or {}
    check("the ablation's record is in the start record", abl.get("ablation") == "c1-gate-off",
          str(abl)[:200] + err_a.read_bytes().decode("utf-8", "replace")[-300:])
    n_chat = len(FAKE.chats())
    safely(lambda: c.request("write", item={"item_id": "u1:0", "session_id": "0", "text": "user: hi\nassistant: ok"}),
           {})
    ap = FAKE.chats()[-1]["body"]["messages"][0]["content"] if len(FAKE.chats()) > n_chat else ""
    check("the ablation's variant reached the upstream: no project_relevant block, no trivial-session rule",
          ap and "FIELD project_relevant" not in ap and "If the session is trivial" not in ap, ap[:120])
    check("... while the full arm's prompt carried both (the contrast)",
          "FIELD project_relevant" in W1_PROMPT and "If the session is trivial" in W1_PROMPT, W1_PROMPT[:120])
    c.close()

    print("\n- F-N3 and the call shapes (AST) -")
    api_src = (ROOT / "nevertwice" / "api.py").read_text(encoding="utf-8")
    run_src = RUNNER.read_text(encoding="utf-8")

    def fn(src: str, name: str):
        return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == name)

    def m_calls(f) -> list[str]:
        out = []
        for n in ast.walk(f):
            if isinstance(n, ast.Call):
                t = n.func
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "m":
                    out.append((n.lineno, n.col_offset, t.attr))
                elif isinstance(t, ast.Attribute) and isinstance(t.value, ast.Attribute) \
                        and isinstance(t.value.value, ast.Name) and t.value.value.id == "m":
                    out.append((n.lineno, n.col_offset, f"{t.value.attr}.{t.attr}"))
        return [a for _, _, a in sorted(out)]
    cap = m_calls(fn(api_src, "capture_session"))
    s7c = m_calls(fn(run_src, "s7_capture"))
    check("F-N3: the S7 wrapper's engine calls equal capture_session's, in order", cap == s7c and len(cap) >= 10,
          f"{cap} vs {s7c}")

    def kw_calls(src: str, attr: str) -> list[dict]:
        out = []
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr:
                out.append({k.arg: (k.value.value if isinstance(k.value, ast.Constant) else "<expr>")
                            for k in n.keywords})
        return out
    rec_calls = kw_calls(run_src, "recall")
    check("every recall passes xrerank=False and project=",
          rec_calls and all(k.get("xrerank") is False and "project" in k for k in rec_calls), str(rec_calls))
    caps = kw_calls(run_src, "capture_session")
    check("every capture_session passes project=, session_id= and date=",
          caps and all({"project", "session_id", "date"} <= set(k) for k in caps), str(caps))
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
    FAKE.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 runner nevertwice: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
