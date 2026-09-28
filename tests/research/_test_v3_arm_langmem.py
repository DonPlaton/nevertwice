#!/usr/bin/env python3
"""PREREG-V3 TB4.6a (A6): research/v3/arms/arm_langmem.py - the langmem and langmem-store arms, driven as real children
laid out as Q9 lays out an arm directory, against FAKE langmem / langgraph / langchain_openai / langchain_ollama
packages (tests/fixtures/v3_fake_products) that record every call, a fake proxy and a fake Ollama on loopback.

* Q-46-1: the adapter imports the standard library, its three siblings and the products only; a decoy langmem in the
  arm directory is refused;
* Q-46-5: ChatOpenAI(model deepseek-flash, base_url /u/<run>.<unit>/v1, api_key the token, extra_body thinking
  disabled), temperature NOT set - the proxy sees thinking disabled and no temperature field; the manager gets store and
  namespace ("memories", <unit>) and nothing else (the product's defaults); InMemoryStore at 1024 dims, OllamaEmbeddings
  with the tag;
* one session thread per invoke, role kept, "<speaker>: <text>", the §5.3 header on the first message only, on a dated
  stand only; a dated write without its date is refused;
* Q-46-2: one process for both stages - a read before end_write and a write after it are refused; write and read times
  are kept apart; store_persistence memory;
* read: store.search(namespace, query=q, limit=k) explicit, "- item" lines;
* langmem-store: put(("items", <unit>), "<index>", {"text": <item bytes>}), no LLM call, item_sha256 / items_sha256,
  index and bytes back; a hit that is not one of the unit's items is refused by name;
* refusals: langmem without its token, langmem-store with one, a stage other than "both", a tag Ollama lacks.

    python tests/research/_test_v3_arm_langmem.py
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

ARMS_DIR = ROOT / "research" / "v3" / "arms"
ADAPTER = ARMS_DIR / "arm_langmem.py"
FAKES = ROOT / "tests" / "fixtures" / "v3_fake_products"
_spec = importlib.util.spec_from_file_location("v3_arm_base_for_langmem", ARMS_DIR / "base.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)
PASSED = FAILED = 0
TAG = "nvt3-bge-m3-d1"
TOKEN = "nvt3-langmem-" + "f" * 32


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
    for w in str(text).lower().replace(":", " ").replace('"', " ").split():
        v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % 32] += 1.0
    return v if any(v) else [1.0] + [0.0] * 31


class Server:
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
                self._send({"choices": [{"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}]})

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def paths(self, suffix: str) -> list[dict]:
        return [c for c in self.calls if c["path"].endswith(suffix)]

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()


TMP = Path(tempfile.mkdtemp(prefix="v3langmem_"))
PROXY = Server([])
OLLAMA = Server([TAG])
LOG = TMP / "fake_calls.jsonl"
children: list = []


def arm_dir(name: str) -> Path:
    d = TMP / "armdirs" / name
    d.mkdir(parents=True)
    for src in (ADAPTER, ARMS_DIR / "base.py", ARMS_DIR / "_http_count.py", ROOT / "research" / "_ollama_pacer.py"):
        shutil.copyfile(src, d / src.name)
    return d


def child_env(*, token: str | None = TOKEN) -> dict:
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
                "NVT3_FAKE_LOG": str(LOG)})
    if token is not None:
        env["DEEPSEEK_API_KEY"] = token
    return env


def spec_for(name: str, arm: str, *, dated=True, unit="u1", stage="both") -> dict:
    return {"arm": arm, "stage": stage, "stand": "s1", "run": "r1", "unit": unit,
            "unit_dir": str(TMP / "runs" / arm / unit), "port": PROXY.port if arm == "langmem" else None,
            "embed_tag": TAG, "ollama_url": f"http://127.0.0.1:{OLLAMA.port}", "dated": dated,
            "record_path": str(TMP / f"{name}.start.json")}


def start(name: str, spec: dict, *, env: dict | None = None, adir: Path | None = None):
    sp = TMP / f"{name}.spec.json"
    sp.write_text(json.dumps(spec), encoding="utf-8")
    d = adir or arm_dir(name)
    errf = TMP / f"{name}.err.txt"
    wd = TMP / f"cwd_{name}"
    wd.mkdir()
    p = subprocess.Popen([sys.executable, "-B", str(d / "arm_langmem.py"), str(sp)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=open(errf, "wb"), cwd=str(wd),
                         env=env or child_env(token=TOKEN if spec["arm"] == "langmem" else None))
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


def logged(event: str) -> list[dict]:
    if not LOG.exists():
        return []
    return [r for r in (json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x) if r["event"] == event]


try:
    print("\n- Q-46-1 and Q9: the adapter's imports -")
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    check("arm_langmem imports the standard library, base, _http_count, _ollama_pacer and the products only",
          mods <= {"__future__", "json", "os", "re", "sys", "time", "urllib", "pathlib", "typing", "base",
                   "_http_count", "_ollama_pacer", "langmem", "langchain_ollama", "langgraph", "langchain_openai"},
          str(sorted(mods)))

    print("\n- the langmem arm: one process, both stages -")
    c, err = start("l1", spec_for("l1", "langmem"))
    h = safely(lambda: c.request("hello"), {})
    _st = h.get("start") or {}
    check("R-EMBED-PATH (c): the start record - the pacer in observe mode (the proxy leg paces and retries) and the "
          "spec's Ollama URL as the arm's one route", _st.get("pacer") == "observe"
          and _st.get("ollama_route") == f"http://127.0.0.1:{OLLAMA.port}",
          str({k: _st.get(k) for k in ("pacer", "ollama_route")}))
    check("hello: the venv's langmem, the DeepSeek label, the tag",
          h.get("system") == "langmem" and str(FAKES) in str(((h.get("start") or {}).get("product") or {}).get("file"))
          and h.get("llm_label") == "deepseek:deepseek-flash" and h.get("embedder") == TAG,
          str(h)[:300] + err.read_bytes().decode("utf-8", "replace")[-400:])
    co = (logged("ChatOpenAI") or [{}])[-1]
    check("ChatOpenAI: deepseek-flash at /u/r1.u1/v1, the token, thinking disabled through extra_body, temperature NOT "
          "set, nothing else", co.get("model") == "deepseek-flash"
          and co.get("base_url") == f"http://127.0.0.1:{PROXY.port}/u/r1.u1/v1" and co.get("api_key_set") is True
          and co.get("extra_body") == {"thinking": {"type": "disabled"}} and co.get("temperature") == "<default>"
          and co.get("extra") == [], str(co))
    mg = (logged("create_memory_store_manager") or [{}])[-1]
    check("the manager gets store and namespace only - the product's defaults (no enable_deletes of v2)",
          mg.get("kwargs") == ["namespace", "store"] and mg.get("namespace") == ["memories", "u1"], str(mg))
    ims = (logged("InMemoryStore") or [{}])[-1]
    emb = (logged("OllamaEmbeddings") or [{}])[-1]
    check("the product's index is the store's default fields ['$'] (L3)", ims.get("fields") == ["$"], str(ims))
    check("InMemoryStore at 1024 dims, OllamaEmbeddings with the tag at the Ollama URL",
          ims.get("dims") == 1024 and emb.get("model") == TAG and emb.get("base_url") == f"http://127.0.0.1:{OLLAMA.port}",
          f"{ims} {emb}")
    check("a read before end_write is refused (Q25: the write stage is still open)",
          raises(lambda: c.request("read", qid="q", query="x", k=3), B.ArmError, "before end_write"))
    sess = [{"role": "user", "speaker": "Caroline", "text": "I adopted a dog named Rex"},
            {"role": "assistant", "speaker": "Melanie", "text": "What a lovely name"}]
    w = safely(lambda: c.request("write", item={"item_id": "u1:s0", "session_id": "s0", "messages": sess},
                                 date="2023-05-20T09:00:00"), {})
    inv = (logged("invoke") or [{}])[-1]
    want = [{"role": "user", "content": "Conversation from 2023-05-20:\nCaroline: I adopted a dog named Rex"},
            {"role": "assistant", "content": "Melanie: What a lovely name"}]
    check("one session thread per invoke: roles kept, '<speaker>: <text>', the header on the FIRST message only",
          inv.get("messages") == want, str(inv.get("messages")))
    check("the write names the bytes it handed over (text_sha256 of the message list)",
          w.get("text_sha256") == B.text_sha256(json.dumps(want, ensure_ascii=False, sort_keys=True)))
    chat = (PROXY.paths("/chat/completions") or [{}])[-1]
    body = chat.get("body") or {}
    check("the proxy sees thinking disabled, NO temperature field, the token, at /u/r1.u1/v1/chat/completions",
          chat.get("path") == "/u/r1.u1/v1/chat/completions" and body.get("thinking") == {"type": "disabled"}
          and "temperature" not in body and chat.get("auth") == f"Bearer {TOKEN}", str(chat)[:300])
    check("a dated write without its date is refused",
          raises(lambda: c.request("write", item={"item_id": "u1:s1", "session_id": "s1", "messages": sess}),
                 B.ArmError, "carries its date"))
    e = safely(lambda: c.request("end_write"), {})
    check("end_write: the footprint, store_persistence memory, no seal",
          (e.get("footprint") or {}).get("retrievable") == 1 and e.get("store_persistence") == "memory"
          and "seal" not in e, str(e)[:200])
    check("a write after end_write is refused (Q25)",
          raises(lambda: c.request("write", item={"item_id": "u1:s2", "session_id": "s2", "messages": sess},
                                   date="2023-05-21"), B.ArmError, "write stage is closed"))
    r = safely(lambda: c.request("read", qid="q1", query="Caroline dog Rex", k=4), {})
    srch = (logged("search") or [{}])[-1]
    check("read: store.search(('memories', unit), query=q, limit=k) with the limit explicit",
          srch.get("namespace_prefix") == ["memories", "u1"] and srch.get("query") == "Caroline dog Rex"
          and srch.get("limit") == 4, str(srch))
    check("read: '- item' lines of the memory content",
          (r.get("items") or [{}])[0] == {"text": "- " + want[0]["content"], "rank": 1}, str(r.get("items"))[:200])
    cn = safely(lambda: c.request("counters"), {})
    tw, tr = (cn.get("stage_times") or {}).get("write") or [None, None], (cn.get("stage_times") or {}).get("read") or [
        None, None]
    check("the write and the read times are kept apart (Q-46-2)",
          None not in tw + tr and tw[0] <= tw[1] <= tr[0] <= tr[1], str(cn.get("stage_times")))
    check("counters: one LLM call per session, through the proxy", cn.get("llm_calls") == 1, str(cn)[:200])
    c.close()

    print("\n- an undated stand -")
    c, _ = start("l2", spec_for("l2", "langmem", dated=False, unit="u2"))
    safely(lambda: c.request("write", item={"item_id": "u2:s0", "session_id": "s0", "messages": sess[:1]}), {})
    check("an undated stand's thread carries no header",
          (logged("invoke") or [{}])[-1].get("messages") == [{"role": "user",
                                                               "content": "Caroline: I adopted a dog named Rex"}])
    c.close()

    print("\n- the langmem-store arm (retrieval tier) -")
    chats_before = len(PROXY.paths("/chat/completions"))
    c, _ = start("st", spec_for("st", "langmem-store"))
    ITEMS = {3: "Conversation from 2023-05-20:\nthe cat sat on the mat", 0: "a bounded retry fixed the upload",
             8: "  \n\tspaces and newlines at both ends \n\n  "}
    ans = {i: safely(lambda i=i, t=t: c.request("write", item={"item_id": f"u1:{i}", "index": i, "text": t}), {})
           for i, t in ITEMS.items()}
    check("the store arm's index is the item's bytes only: fields ['text'] (L3)",
          (logged("InMemoryStore") or [{}])[-1].get("fields") == ["text"], str((logged("InMemoryStore") or [{}])[-1]))
    puts = logged("put")[-3:]
    check("langmem-store: put(('items', unit), '<index>', {'text': <the item's bytes EXACTLY, whitespace kept>}) - no "
          "header added", [(p.get("namespace"), p.get("key"), p.get("value")) for p in puts]
          == [(["items", "u1"], "3", {"text": ITEMS[3]}), (["items", "u1"], "0", {"text": ITEMS[0]}),
              (["items", "u1"], "8", {"text": ITEMS[8]})], str(puts))
    es = safely(lambda: c.request("end_write"), {})
    check("langmem-store: item_sha256 per item and items_sha256 per unit",
          all(ans[i].get("item_sha256") == B.text_sha256(t) for i, t in ITEMS.items())
          and es.get("items_sha256") == B.items_digest({i: B.text_sha256(t) for i, t in ITEMS.items()}))
    rs = safely(lambda: c.request("read", qid="q", query="bounded retry upload", k=2), {})
    check("langmem-store read: index and bytes back, rank order",
          (rs.get("items") or [{}])[0] == {"index": 0, "text": ITEMS[0], "rank": 1}, str(rs.get("items")))
    cs = safely(lambda: c.request("counters"), {})
    check("langmem-store: no LLM call (llm_calls 0, none at the proxy)",
          cs.get("llm_calls") == 0 and len(PROXY.paths("/chat/completions")) == chats_before, str(cs)[:200])
    c.close()

    print("\n- a langmem-store hit that is not the unit's (in-process, on a fake store) -")
    sys.path.insert(0, str(ARMS_DIR))
    _aspec = importlib.util.spec_from_file_location("v3_arm_langmem_inproc", ADAPTER)
    AL = importlib.util.module_from_spec(_aspec)
    _aspec.loader.exec_module(AL)

    def store_read(keys):
        hits = [NS(key=k, value={"text": "t"}, namespace=("items", "u1")) for k in keys]
        hh = AL.Handler(dict(spec_for("inp", "langmem-store"), stage="both"),
                        {"store": NS(search=lambda *a, **k: hits), "manager": None, "pacer": None}, {})
        hh.item_shas = {1: "x", 2: "y"}
        hh.phase = "read"
        return hh.read(qid="q", query="x", k=3)
    check("the unit's own keys read back", safely(lambda: store_read(["2", "1"]), {}).get("items")
          == [{"index": 2, "text": "t", "rank": 1}, {"index": 1, "text": "t", "rank": 2}])
    for bad in ("7", "a", "-1", "01x"):
        check(f"a hit keyed {bad!r} is not one of the unit's items - refused by name",
              raises(lambda bad=bad: store_read([bad]), RuntimeError, "not one of the unit's items"))
    check("render_memory: langmem's Memory shape, a plain string, anything else as sorted JSON",
          [AL.render_memory(v) for v in ({"kind": "Memory", "content": {"content": "a"}}, {"content": "b"}, {"x": 1})]
          == ["- a", "- b", '- {"x": 1}'])

    print("\n- refusals by name -")
    msg = hello_error("x1", spec_for("x1", "langmem", unit="u9"), env=child_env(token=None))
    check("langmem without its proxy token is refused", "proxy token" in msg, msg[:200])
    msg = hello_error("x2", spec_for("x2", "langmem-store", unit="u9"), env=child_env(token=TOKEN))
    check("langmem-store with a token is refused (it has no LLM)", "has no LLM" in msg, msg[:200])
    msg = hello_error("x3", spec_for("x3", "langmem", unit="u9", stage="write"))
    check("a stage other than 'both' is refused (the store lives in memory, Q-46-2)", "stage 'both'" in msg, msg[:200])
    OLLAMA.tags = ["bge-m3:latest"]
    msg = hello_error("x4", spec_for("x4", "langmem", unit="u9"))
    check("a tag Ollama does not have is refused by name", "not in Ollama" in msg, msg[:200])
    OLLAMA.tags = [TAG]
    decoy = arm_dir("decoy")
    (decoy / "langmem.py").write_text("__version__ = 'decoy'\ndef create_memory_store_manager(*a, **k): pass\n",
                                      encoding="utf-8")
    msg = hello_error("x5", spec_for("x5", "langmem", unit="u9"), adir=decoy)
    check("a langmem module inside the arm directory is refused by name (Q-46-1)", "arm's directory" in msg, msg[:200])
    src = ADAPTER.read_text(encoding="utf-8")
    check("the counter is installed before the pacer (Q-46-4)", 0 < src.index("HC.install(") < src.index("P.install("))
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

print(f"\nv3 arm langmem: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
