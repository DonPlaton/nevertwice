#!/usr/bin/env python3
"""PREREG-V3 TB4.6a (A6): research/v3/arms/arm_mem0.py - the mem0 and mem0-store arms, driven as real children laid out
as Q9 lays out an arm directory (copies of the adapter, base.py, _http_count.py and _ollama_pacer.py), against a FAKE
mem0 package (tests/fixtures/v3_fake_products/mem0) that records every call, a fake proxy and a fake Ollama on loopback.

* Q-46-1: `import mem0` from the arm directory finds the venv's package, never the adapter; a decoy mem0.py in the arm
  directory is refused by name; the adapter imports the standard library, its three siblings and mem0 only;
* the config (rev1 §2.2, Q-46-6): DeepSeek at /u/<run>.<unit>/v1, model deepseek-flash, NO temperature, no key and no
  thinking field in it; Ollama embedder with the tag at 1024 dims; qdrant and the history database under <unit>/store;
* the write: one message per add, role kept, "<speaker>: <text>", the §5.3 header first on a dated stand only,
  timestamp never passed, user_id = unit, infer=True; the LLM call reaches the proxy path with the arm's token;
* the read: search with filters={"user_id": unit}, top_k=k and threshold=0.1 explicit; memory lines, no add-time;
* mem0-store: add(item bytes, infer=False, metadata index), item_sha256 and items_sha256, no LLM call (llm_calls 0),
  index and text back; a hit without an index is refused by name;
* the preflight: a tag Ollama does not have is refused by name BEFORE the product is built - no pull is ever sent;
* refusals: mem0 without its proxy token, mem0-store with a token, a write stage on an existing store, a dated write
  without its date; Q25 and the seal: a store touched between the stages is refused.

    python tests/research/_test_v3_arm_mem0.py
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

ARMS_DIR = ROOT / "research" / "v3" / "arms"
ADAPTER = ARMS_DIR / "arm_mem0.py"
FAKES = ROOT / "tests" / "fixtures" / "v3_fake_products"
_spec = importlib.util.spec_from_file_location("v3_arm_base_for_mem0", ARMS_DIR / "base.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)
PASSED = FAILED = 0
TAG = "nvt3-bge-m3-d1"
TOKEN = "nvt3-mem0-" + "e" * 32


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


def vec(text: str) -> list[float]:
    v = [0.0] * 32
    for w in str(text).lower().replace(":", " ").split():
        v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % 32] += 1.0
    return v if any(v) else [1.0] + [0.0] * 31


class Server:
    """The fake proxy and the fake Ollama: one loopback server each, recording every request."""

    def __init__(self, tags: list[str]) -> None:
        self.calls: list[dict] = []
        self.tags = tags
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj) -> None:
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                srv.calls.append({"method": "GET", "path": self.path})
                self._send({"models": [{"name": t, "model": t} for t in srv.tags]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                srv.calls.append({"method": "POST", "path": self.path, "body": body,
                                  "auth": self.headers.get("Authorization")})
                if self.path.endswith("/api/embed"):
                    inp = body.get("input")
                    return self._send({"embeddings": [vec(t) for t in (inp if isinstance(inp, list) else [inp])]})
                if self.path.endswith("/chat/completions"):
                    return self._send({"choices": [{"message": {"role": "assistant", "content": "{}"},
                                                    "finish_reason": "stop"}],
                                       "usage": {"prompt_tokens": 40, "completion_tokens": 4}})
                self._send({"status": "success"})

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def paths(self, suffix: str) -> list[dict]:
        return [c for c in self.calls if c["path"].endswith(suffix)]

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()


TMP = Path(tempfile.mkdtemp(prefix="v3mem0_"))
PROXY = Server([])
OLLAMA = Server([TAG])
LOG = TMP / "fake_calls.jsonl"
children: list = []


def arm_dir(name: str) -> Path:
    """The Q9 layout: byte-identical copies of the adapter and its three siblings."""
    d = TMP / "armdirs" / name
    d.mkdir(parents=True)
    for src in (ADAPTER, ARMS_DIR / "base.py", ARMS_DIR / "_http_count.py", ROOT / "research" / "_ollama_pacer.py"):
        shutil.copyfile(src, d / src.name)
    return d


def child_env(*, token: str | None = TOKEN, extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in ("SystemRoot", "SystemDrive", "windir", "ComSpec", "PATHEXT",
                                       "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS") if k in os.environ}
    home = TMP / "homes" / uuid.uuid4().hex[:8]
    (home / "Temp").mkdir(parents=True)
    env.update({"PATH": os.pathsep.join([str(Path(sys.executable).parent)] + (
                    [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")] if os.name == "nt"
                    else ["/usr/bin", "/bin"])),
                "HOME": str(home), "USERPROFILE": str(home), "TEMP": str(home / "Temp"), "TMP": str(home / "Temp"),
                "TMPDIR": str(home / "Temp"), "PYTHONPYCACHEPREFIX": str(TMP / "pyc"), "PYTHONPATH": str(FAKES),
                "NO_PROXY": "127.0.0.1,localhost", "OLLAMA_HOST": f"http://127.0.0.1:{OLLAMA.port}",
                "NVT3_FAKE_LOG": str(LOG), "MEM0_DIR": str(home / "mem0")})
    if token is not None:
        env["DEEPSEEK_API_KEY"] = token
    env.update(extra or {})
    return env


def spec_for(name: str, arm: str, stage: str, unit_dir: Path, *, dated=True, unit="u1") -> dict:
    return {"arm": arm, "stage": stage, "stand": "s1", "run": "r1", "unit": unit, "unit_dir": str(unit_dir),
            "port": PROXY.port if arm == "mem0" else None, "embed_tag": TAG,
            "ollama_url": f"http://127.0.0.1:{OLLAMA.port}", "dated": dated,
            "record_path": str(TMP / f"{name}.start.json")}


def start(name: str, spec: dict, *, env: dict | None = None, adir: Path | None = None):
    sp = TMP / f"{name}.spec.json"
    sp.write_text(json.dumps(spec), encoding="utf-8")
    d = adir or arm_dir(name)
    errf = TMP / f"{name}.err.txt"
    wd = TMP / f"cwd_{name}"
    wd.mkdir()
    p = subprocess.Popen([sys.executable, "-B", str(d / "arm_mem0.py"), str(sp)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=open(errf, "wb"), cwd=str(wd),
                         env=env or child_env(token=TOKEN if spec["arm"] == "mem0" else None))
    children.append(p)
    return B.ArmClient(p, default_timeout=120), errf


def hello_error(name: str, spec: dict, *, env: dict | None = None, adir: Path | None = None) -> str:
    c, _ = start(name, spec, env=env, adir=adir)
    try:
        c.request("hello")
        return "no refusal"
    except B.ArmError as e:
        return str(e)
    finally:
        c.close()


LINES3 = ("fastembed not installed", "Failed to load BM25 encoder", "predates v3 hybrid search")   # A5's three lines


def logged(event: str) -> list[dict]:
    if not LOG.exists():
        return []
    return [r for r in (json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x) if r["event"] == event]


try:
    print("\n- Q-46-1 and Q9: the adapter's imports, the product found in the venv -")
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    check("arm_mem0 imports the standard library, base, _http_count, _ollama_pacer and mem0 - nothing of the repo "
          "(importlib: NLP-3b's installed-version read, B-MEM0-IMP; logging: M35's watch of the store's log)",
          mods <= {"__future__", "importlib", "json", "logging", "os", "re", "sys", "threading", "time", "urllib", "pathlib",
                   "typing", "base", "_http_count", "_ollama_pacer", "mem0"}, str(sorted(mods)))

    print("\n- a mem0-store hit without an item index (in-process, on a fake product) -")
    sys.path.insert(0, str(ARMS_DIR))
    _aspec = importlib.util.spec_from_file_location("v3_arm_mem0_inproc", ADAPTER)
    AM = importlib.util.module_from_spec(_aspec)
    _aspec.loader.exec_module(AM)
    from types import SimpleNamespace as NS  # noqa: E402

    def store_read(hits):
        h = AM.Handler({"arm": "mem0-store", "stage": "read", "unit": "u1", "unit_dir": str(TMP / "fake_unit"),
                        "run": "r1", "stand": "s1"},
                       {"mem": NS(search=lambda *a, **k: {"results": hits}), "pacer": None,
                        "bm25": getattr(AM, "BM25Watch", lambda: None)()}, {})     # built as bind builds it (M35)
        return h.read(qid="q", query="x", k=3)
    check("a mem0-store hit with its index reads back as index and bytes",
          safely(lambda: store_read([{"id": "a", "memory": "m", "metadata": {"index": 2}}]), {}).get("items")
          == [{"index": 2, "text": "m", "rank": 1}])
    for bad in ({}, {"index": "2"}, {"index": True}, None):
        check(f"a mem0-store hit whose metadata is {bad!r} is refused by name (not one of the unit's items)",
              raises(lambda bad=bad: store_read([{"id": "a", "memory": "m", "metadata": bad}]), RuntimeError,
                     "carries no item index"))

    print("\n- the mem0 arm: write stage -")
    U1 = TMP / "runs" / "s1" / "r1" / "mem0" / "u1"
    U1.mkdir(parents=True)
    c, err = start("w1", spec_for("w1", "mem0", "write", U1))
    h = safely(lambda: c.request("hello"), {})
    _st = h.get("start") or {}
    check("R-EMBED-PATH (c): the start record - the pacer in observe mode (the proxy leg paces and retries) and the "
          "spec's Ollama URL as the arm's one route", _st.get("pacer") == "observe"
          and _st.get("ollama_route") == f"http://127.0.0.1:{OLLAMA.port}",
          str({k: _st.get(k) for k in ("pacer", "ollama_route")}))
    st = h.get("start") or {}
    check("hello: the product is the venv's mem0 (found through PYTHONPATH, not in the arm directory)",
          h.get("system") == "mem0" and str(FAKES) in str((st.get("product") or {}).get("file"))
          and h.get("llm_label") == "deepseek:deepseek-flash",
          str(h)[:300] + err.read_bytes().decode("utf-8", "replace")[-400:])
    cfg = (logged("from_config") or [{}])[-1].get("config") or {}
    llm = (cfg.get("llm") or {}).get("config") or {}
    check("config: DeepSeek at the proxy's /u/r1.u1/v1, model deepseek-flash",
          (cfg.get("llm") or {}).get("provider") == "deepseek" and llm.get("model") == "deepseek-flash"
          and llm.get("deepseek_base_url") == f"http://127.0.0.1:{PROXY.port}/u/r1.u1/v1", str(cfg.get("llm")))
    check("config: no temperature, no key, no thinking field (the product's defaults; M-ALL-adapter-sets-temperature)",
          not ({"temperature", "api_key", "thinking", "extra_body"} & set(llm)) and TOKEN not in json.dumps(cfg),
          str(sorted(llm)))
    emb = (cfg.get("embedder") or {}).get("config") or {}
    vs = (cfg.get("vector_store") or {}).get("config") or {}
    check("config: Ollama embedder with the tag at 1024 dims; qdrant and history.db under <unit>/store",
          emb.get("model") == TAG and emb.get("embedding_dims") == 1024
          and str(vs.get("path", "")).startswith(str(U1 / "store")) and cfg.get("history_db_path") == str(
              U1 / "store" / "history.db"), str(cfg)[:300])
    w = safely(lambda: c.request("write", item={"item_id": "u1:0", "session_id": "0", "role": "user",
                                                "speaker": "Caroline", "text": "I went hiking on Sunday"},
                                 date="2023-05-20T10:00:00"), {})
    add = (logged("add") or [{}])[-1]
    want = "Conversation from 2023-05-20:\nCaroline: I went hiking on Sunday"
    check("write: ONE message per add, role kept, the §5.3 header first, '<speaker>: <text>' (Q-46-6)",
          add.get("messages") == [{"role": "user", "content": want}], str(add.get("messages")))
    check("write: user_id = unit, infer=True, and timestamp never passed (M-MEM0-timestamp-kw)",
          add.get("user_id") == "u1" and add.get("infer") is True and add.get("timestamp") is None, str(add)[:200])
    check("write: text_sha256 names the exact content handed to mem0", w.get("text_sha256") == B.text_sha256(want))
    chat = PROXY.paths("/chat/completions")
    check("write: the LLM call reaches the proxy path /u/r1.u1/v1/chat/completions with the arm's token",
          chat and chat[-1]["path"] == "/u/r1.u1/v1/chat/completions" and chat[-1]["auth"] == f"Bearer {TOKEN}",
          str(chat[-1:])[:200])
    safely(lambda: c.request("write", item={"item_id": "u1:1", "session_id": "0", "role": "assistant",
                                            "speaker": "Melanie", "text": "That sounds lovely"},
                             date="2023-05-20T10:01:00"), {})
    check("an assistant message keeps its role",
          (logged("add") or [{}])[-1].get("messages") == [{"role": "assistant", "content":
                                                          "Conversation from 2023-05-20:\nMelanie: That sounds lovely"}])
    check("a dated stand's write without its date is refused by name",
          raises(lambda: c.request("write", item={"item_id": "u1:2", "session_id": "0", "role": "user",
                                                  "speaker": "C", "text": "t"}), B.ArmError, "carries its date"))
    cu = safely(lambda: c.request("counters"), {})
    usage = cu.get("llm_usage") or {}
    check("Q-AB-1: mem0's own LLM client is counted in the child - calls, and prompt and completion tokens read from "
          "response.usage (the upstream says 40 and 4 per call)",
          usage == {"calls": 2, "failed": 0, "no_usage": 0, "prompt_tokens": 80, "completion_tokens": 8}, str(usage))
    check("Q-AB-1: ... one logical call per HTTP call here - the counter and the HTTP door agree",
          usage.get("calls") == ((cu.get("http") or {}).get("counts") or {}).get("proxy:chat", {}).get("attempts"),
          str(cu.get("http"))[:200])
    calls = logged("llm_call")
    check("Q-AB-1 (1): the counter hands mem0 the SDK's own response object and passes the request it was given, "
          "unchanged", len(calls) == 2 and all(x.get("same_response") and x.get("same_params") for x in calls),
          str(calls))
    raised = safely(lambda: c.request("write", item={"item_id": "u1:3", "session_id": "0", "role": "user",
                                                     "speaker": "C", "text": "NVT3-RAISE-LLM please"},
                                      date="2023-05-20T10:02:00"), {})
    cu = safely(lambda: c.request("counters"), {})
    usage = cu.get("llm_usage") or {}
    rl = logged("llm_raised")
    check("Q-AB-1 (2): an SDK exception reaches mem0 as it was raised, and the failed call is counted",
          rl and rl[-1].get("same") is True and rl[-1].get("type") == "_FakeAPIError"
          and usage.get("calls") == 3 and usage.get("failed") == 1 and usage.get("prompt_tokens") == 80
          and raised.get("results") == 0, f"{rl[-1:]} {usage} {raised}")
    e = safely(lambda: c.request("end_write"), {})
    check("end_write: the footprint counts the unit's memories, and the store is sealed",
          (e.get("footprint") or {}).get("retrievable") == 2 and (U1 / B.SEAL_NAME).is_file(), str(e)[:200])
    cn = safely(lambda: c.request("counters"), {})
    check("counters: two LLM calls through the proxy, counted inside the pacer",
          cn.get("llm_calls") == 2 and ((cn.get("http") or {}).get("counts") or {}).get("proxy:chat", {}).get(
              "attempts") == 2, str(cn)[:300])
    check("B-NLP NLP-3b: the live counters carry spaCy's state, read - the fake mem0 has no spacy_models, so the module "
          "is not imported and every state is unknown (probe_a8 turns that into blocked:nlp-off, never a pass)",
          (cn.get("nlp") or {}).get("module") is False and (cn.get("nlp") or {}).get("nlp_full") is None
          and (cn.get("nlp") or {}).get("names", {}).get("model") == "en_core_web_sm", str(cn.get("nlp")))
    check("no pull request ever reached Ollama", not OLLAMA.paths("/api/pull"))
    c.close()

    print("\n- the mem0 arm: read stage (a new process, the sealed store) -")
    c, err = start("r1", spec_for("r1", "mem0", "read", U1))
    r = safely(lambda: c.request("read", qid="q1", query="Caroline hiking Sunday", k=7), {})
    srch = (logged("search") or [{}])[-1]
    check("read: search(filters={'user_id': unit}, top_k=k, threshold=0.1), both explicit (M-MEM0-topk-default, "
          "M-MEM0-threshold-implicit)", srch.get("filters") == {"user_id": "u1"} and srch.get("top_k") == 7
          and srch.get("threshold") == 0.1 and srch.get("extra") == [], str(srch)[:200])
    items = r.get("items") or []
    check("read: the memory lines in rank order, no add-time rendered (M-MEM0-addtime-date)",
          items and items[0]["text"] == want and items[0]["rank"] == 1 and all("2026-09-27" not in x["text"]
                                                                              for x in items), str(items)[:200])
    check("a write in the read stage is refused (Q25)",
          raises(lambda: c.request("write", item={"item_id": "x", "role": "user", "speaker": "a", "text": "b"},
                                   date="2023-01-01"), B.ArmError, "write stage"))
    cb = safely(lambda: c.request("counters"), {}).get("bm25") or {}
    ksr = logged("keyword_search")
    check("M35 (A5, T34): the read's counters carry the unit's BM25 - the encoder loaded, the bm25 slot, the store's log "
          "watched with none of A5's three lines, keyword_search called once by the product's own search and answering "
          "(not None), and the returned memory that shares the query's words counted BM25-positive",
          cb.get("encoder") == "loaded" and cb.get("slot") is True and cb.get("log_watched") is True
          and cb.get("lines") == {x: 0 for x in LINES3} and cb.get("keyword_search", {}).get("calls") == 1
          and cb["keyword_search"].get("not_none") == 1 and cb.get("results_bm25_positive", 0) >= 1
          and ksr and ksr[-1].get("filters") == {"user_id": "u1"}, str(cb)[:400])
    c.close()

    print("\n- M35: a store whose BM25 is off is counted so (the verdict is artifact.p0k's) -")
    off = {}
    for mode in ("noslot", "noencoder"):
        UM = TMP / "runs" / "s1" / "r1" / "mem0" / f"u{mode}"
        UM.mkdir(parents=True)
        c, _ = start(f"w{mode}", spec_for(f"w{mode}", "mem0", "write", UM, unit=f"u{mode}"),
                     env=child_env(extra={"NVT3_FAKE_BM25": mode}))
        safely(lambda: c.request("write", item={"item_id": f"u{mode}:0", "session_id": "0", "role": "user",
                                                "speaker": "Caroline", "text": "I went hiking on Sunday"},
                                 date="2023-05-20T10:00:00"), {})
        safely(lambda: c.request("end_write"), {})
        c.close()
        c, errf = start(f"r{mode}", spec_for(f"r{mode}", "mem0", "read", UM, unit=f"u{mode}"),
                        env=child_env(extra={"NVT3_FAKE_BM25": mode}))
        safely(lambda: c.request("read", qid="q", query="Caroline hiking", k=3), {})
        off[mode] = safely(lambda: c.request("counters"), {}).get("bm25") or {}
        c.close()
        off[mode + "_err"] = errf.read_text(encoding="utf-8", errors="replace") if errf.is_file() else ""
    ns_, ne_ = off["noslot"], off["noencoder"]
    check("M35: a collection without the bm25 slot reads slot False, its 'predates v3 hybrid search' line counted, "
          "keyword_search called but never not None; an encoder that fails reads 'failed', its 'Failed to load BM25 "
          "encoder' line counted - each line still in the child's own stderr (the filter drops nothing)",
          ns_.get("slot") is False and ns_.get("lines", {}).get("predates v3 hybrid search") == 1
          and ns_.get("keyword_search", {}).get("calls") == 1 and ns_["keyword_search"].get("not_none") == 0
          and ne_.get("encoder") == "failed" and ne_.get("lines", {}).get("Failed to load BM25 encoder") == 1
          and ne_.get("keyword_search", {}).get("not_none") == 0
          and "predates v3 hybrid search" in off["noslot_err"] and "Failed to load BM25 encoder" in off["noencoder_err"],
          str({k: v for k, v in off.items() if not k.endswith("_err")})[:500])

    print("\n- an undated stand: no header -")
    U2 = TMP / "runs" / "s1" / "r1" / "mem0" / "u2"
    U2.mkdir(parents=True)
    c, _ = start("w2", spec_for("w2", "mem0", "write", U2, dated=False, unit="u2"))
    safely(lambda: c.request("write", item={"item_id": "u2:0", "session_id": "0", "role": "user", "speaker": "user",
                                            "text": "the flag is on"}), {})
    check("an undated stand's message carries no header",
          (logged("add") or [{}])[-1].get("messages") == [{"role": "user", "content": "user: the flag is on"}])
    c.close()

    print("\n- the mem0-store arm (retrieval tier) -")
    U3 = TMP / "runs" / "s1" / "r1" / "mem0-store" / "u1"
    U3.mkdir(parents=True)
    chats_before = len(PROXY.paths("/chat/completions"))
    c, err = start("s1", spec_for("s1", "mem0-store", "write", U3))
    ITEMS = {4: "Conversation from 2023-05-20:\nthe cat sat on the mat", 1: "a bounded retry fixed the upload",
             6: "  \n\tspaces and newlines at both ends \n\n  "}
    ans = {i: safely(lambda i=i, t=t: c.request("write", item={"item_id": f"u1:{i}", "index": i, "text": t}), {})
           for i, t in ITEMS.items()}
    adds = logged("add")[-3:]
    check("mem0-store: add(the item's bytes EXACTLY - whitespace at both ends kept, infer=False, metadata index) - no "
          "header added by the adapter", [(a.get("messages"), a.get("infer"), a.get("metadata")) for a in adds]
          == [(ITEMS[4], False, {"index": 4}), (ITEMS[1], False, {"index": 1}), (ITEMS[6], False, {"index": 6})],
          str(adds)[:300])
    check("mem0-store: item_sha256 per item and items_sha256 per unit",
          all(ans[i].get("item_sha256") == B.text_sha256(t) for i, t in ITEMS.items())
          and safely(lambda: c.request("end_write"), {}).get("items_sha256")
          == B.items_digest({i: B.text_sha256(t) for i, t in ITEMS.items()}))
    cs = safely(lambda: c.request("counters"), {})
    check("mem0-store: no LLM call at all (llm_calls 0 by the counter, none at the proxy)",
          cs.get("llm_calls") == 0 and len(PROXY.paths("/chat/completions")) == chats_before, str(cs)[:200])
    check("Q-AB-1: mem0-store wraps no LLM client - its llm_usage is None, never a zero that looks measured",
          "llm_usage" in cs and cs["llm_usage"] is None, str(cs.get("llm_usage")))
    scfg = (logged("from_config") or [{}])[-1].get("config") or {}
    check("mem0-store: its LLM is an Ollama model on a closed loopback port (never called)",
          scfg.get("llm") == {"provider": "ollama", "config": {"model": "nvt3-no-llm",
                                                               "ollama_base_url": "http://127.0.0.1:0"}})
    c.close()
    c, _ = start("s2", spec_for("s2", "mem0-store", "read", U3))
    rs = safely(lambda: c.request("read", qid="q", query="bounded retry upload", k=5), {})
    check("mem0-store read: the item index and its bytes, rank order",
          (rs.get("items") or [{}])[0] == {"index": 1, "text": ITEMS[1], "rank": 1}, str(rs.get("items"))[:200])
    c.close()

    print("\n- refusals by name -")
    U4 = TMP / "runs" / "s1" / "r1" / "mem0" / "u4"
    U4.mkdir(parents=True)
    msg = hello_error("x1", spec_for("x1", "mem0", "write", U4, unit="u4"), env=child_env(token=None))
    check("mem0 without its proxy token is refused", "proxy token" in msg, msg[:200])
    msg = hello_error("x2", spec_for("x2", "mem0-store", "write", U4, unit="u4"), env=child_env(token=TOKEN))
    check("mem0-store with a token is refused (it has no LLM)", "has no LLM" in msg, msg[:200])
    msg = hello_error("x3", spec_for("x3", "mem0", "write", U1))
    check("a write stage on an existing store is refused", "fresh store" in msg, msg[:200])
    OLLAMA.tags = ["bge-m3:latest"]
    pulls_before = len(OLLAMA.paths("/api/pull"))
    msg = hello_error("x4", spec_for("x4", "mem0", "write", U4, unit="u4"))
    check("a tag Ollama does not have is refused by name BEFORE the product is built - no pull is sent",
          "would pull it" in msg and len(OLLAMA.paths("/api/pull")) == pulls_before, msg[:200])
    OLLAMA.tags = [TAG]
    decoy = arm_dir("decoy")
    (decoy / "mem0").mkdir()
    (decoy / "mem0" / "__init__.py").write_text("__version__ = 'decoy'\nclass Memory: pass\n", encoding="utf-8")
    msg = hello_error("x5", spec_for("x5", "mem0", "write", U4, unit="u4"), adir=decoy)
    check("a mem0 package inside the arm directory is refused by name (Q-46-1)", "arm's directory" in msg, msg[:200])
    decoy2 = arm_dir("decoy2")
    (decoy2 / "mem0.py").write_text("__version__ = 'decoy'\nclass Memory: pass\n", encoding="utf-8")
    msg = hello_error("x5b", spec_for("x5b", "mem0", "write", U4, unit="u4"), adir=decoy2)
    check("a mem0.py module inside the arm directory is refused by name too", "arm's directory" in msg, msg[:200])
    decoy3 = arm_dir("decoy3")
    (decoy3 / "mem0.py").write_text("__version__ = 'decoy without Memory'\n", encoding="utf-8")
    msg = hello_error("x5c", spec_for("x5c", "mem0", "write", U4, unit="u4"), adir=decoy3)
    check("MI1: a decoy mem0.py WITHOUT Memory is refused by name for its place, never by an ImportError",
          "arm's directory" in msg and "ImportError" not in msg, msg[:200])
    msg = hello_error("x7", spec_for("x7", "mem0", "write", U4, unit="u4"),
                      env=child_env(extra={"NVT3_FAKE_PULL_ALWAYS": "1"}))
    check("a product that pulls anyway while it is built is refused by name (the counter saw the pull)",
          "pull request" in msg, msg[:200])
    U5 = TMP / "runs" / "s1" / "r1" / "mem0" / "u5"
    U5.mkdir(parents=True)
    msg = hello_error("x8", spec_for("x8", "mem0", "write", U5, unit="u5"),
                      env=child_env(extra={"NVT3_FAKE_NO_CLIENT": "1"}))
    check("Q-AB-1: a mem0 whose LLM has no OpenAI client (chat.completions.create) is refused by name - the K87 "
          "check-2 source would be missing", "K87 check-2 source" in msg, msg[:200])
    src = ADAPTER.read_text(encoding="utf-8")
    check("the counter is installed before the pacer, so it sits inside it (Q-46-4)",
          0 < src.index("HC.install(") < src.index("P.install("))
    victim = next(f for f in sorted((U3 / "store").rglob("*")) if f.is_file())
    victim.write_bytes(victim.read_bytes() + b" ")
    msg = hello_error("x6", spec_for("x6", "mem0-store", "read", U3))
    check("a store touched between the stages is refused by name (Q-45-5)", "changed between the stages" in msg,
          msg[:200])
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
    PROXY.close()
    OLLAMA.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 arm mem0: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
