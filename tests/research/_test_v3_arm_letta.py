#!/usr/bin/env python3
"""PREREG-V3 TB4.7b (A6): research/v3/arms/arm_letta.py - the letta adapter, driven as a real child laid out as Q9
lays out an arm directory (arm_letta.py, base.py, _rest.py), against a FAKE Letta server on loopback
(tests/fixtures/v3_fake_products/_fake_letta.py) whose /openapi.json is written by hand, not from the adapter's table.

* start (Q-47-2, Q-47-8a): the server's document must be the pinned sha256 and carry every declared call; a document
  that lacks one refuses; a token in the child's environment refuses;
* the agent (Q-47-8d): named by the unit, created with the spec's pinned defaults and exactly the spec's tool set,
  deepseek-flash at the arm's proxy port as the container sees it (/u/<run>.<unit>/v1), no temperature / max_tokens /
  reasoning field of ours, the v3 embedder on the Ollama leg; a tool outside §2.6.6 in the spec, a server that gives
  the agent more tools than the set, an existing agent of that name at the write stage - each refuses by name;
* write (Q-47-8c): one message per call, role "user" whatever the speaker's role, "<speaker>: <text>" after the §5.3
  header on a dated stand; the tools re-checked after every step; a tool called outside the set, or grown by the
  agent, is a tool_violation, counted;
* the state seal (Q-47-5, Q-47-8f): paginated to the end past a server page cap, counts checked against the context
  overview (a gap refuses), a cursor that never advances refuses; the read stage recomputes it - a change between
  the stages refuses;
* read (Q-47-8b, Q-47-8h): core blocks always, then archival hits; at K the first k hits; V refuses; recall declared
  absent.

    python tests/research/_test_v3_arm_letta.py
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
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

ARMS_DIR = ROOT / "research" / "v3" / "arms"
ADAPTER = ARMS_DIR / "arm_letta.py"
FAKES = ROOT / "tests" / "fixtures" / "v3_fake_products"
_spec = importlib.util.spec_from_file_location("v3_arm_base_for_letta", ARMS_DIR / "base.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)
_fspec = importlib.util.spec_from_file_location("v3_fake_letta", FAKES / "_fake_letta.py")
FL = importlib.util.module_from_spec(_fspec)
sys.modules["v3_fake_letta"] = FL
_fspec.loader.exec_module(FL)
_aspec = importlib.util.spec_from_file_location("v3_arm_letta_inproc", ADAPTER)
AL = importlib.util.module_from_spec(_aspec)
sys.modules["v3_arm_letta_inproc"] = AL
_aspec.loader.exec_module(AL)
PASSED = FAILED = 0
TAG = "nvt3-bge-m3-d1"
PORT, LEG = 47001, 47002
TOOLS = ["send_message", "conversation_search", "archival_memory_insert", "archival_memory_search",
         "memory_insert", "memory_replace"]
AGENT = {"agent_type": "memgpt_v2_agent",
         "memory_blocks": [{"label": "human", "value": "", "limit": 5000},
                           {"label": "persona", "value": "I am a memory keeper.", "limit": 5000}],
         "context_window": 32000, "tools": TOOLS, "include_base_tools": False,
         "removed_defaults": ["run_code"]}


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


TMP = Path(tempfile.mkdtemp(prefix="v3letta_"))
children: list = []


def arm_dir(name: str) -> Path:
    d = TMP / "armdirs" / name
    d.mkdir(parents=True)
    for src in (ADAPTER, ARMS_DIR / "base.py", ARMS_DIR / "_rest.py"):
        shutil.copyfile(src, d / src.name)
    return d


def child_env(extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in ("SystemRoot", "SystemDrive", "windir", "ComSpec", "PATHEXT",
                                       "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS") if k in os.environ}
    home = TMP / "homes" / uuid.uuid4().hex[:8]
    (home / "Temp").mkdir(parents=True)
    env.update({"PATH": os.pathsep.join([str(Path(sys.executable).parent)] + (
                    [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")] if os.name == "nt"
                    else ["/usr/bin", "/bin"])),
                "HOME": str(home), "USERPROFILE": str(home), "TEMP": str(home / "Temp"), "TMP": str(home / "Temp"),
                "TMPDIR": str(home / "Temp"), "PYTHONPYCACHEPREFIX": str(TMP / "pyc"),
                "NO_PROXY": "127.0.0.1,localhost"})
    env.update(extra or {})
    return env


def pin(fake) -> str:
    return hashlib.sha256(fake.doc_bytes()).hexdigest()


def spec_for(name: str, stage: str, unit_dir: Path, fake, *, dated: bool = True, **over) -> dict:
    s = {"arm": "letta", "stage": stage, "stand": "s1", "run": "r1", "unit": unit_dir.name, "unit_dir": str(unit_dir),
         "server_url": fake.url, "openapi_sha256": pin(fake), "port": PORT, "ollama_leg_port": LEG,
         "container_host": "host.docker.internal", "embed_tag": TAG, "dated": dated,
         "agent": json.loads(json.dumps(AGENT)), "record_path": str(TMP / f"{name}.start.json")}
    s.update(over)
    return s


def start(name: str, spec: dict, *, env: dict | None = None):
    sp = TMP / f"{name}.spec.json"
    sp.write_text(json.dumps(spec), encoding="utf-8")
    d = arm_dir(name)
    wd = TMP / f"cwd_{name}"
    wd.mkdir()
    p = subprocess.Popen([sys.executable, "-B", str(d / "arm_letta.py"), str(sp)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=open(TMP / f"{name}.err.txt", "wb"), cwd=str(wd),
                         env=env or child_env())
    children.append(p)
    return B.ArmClient(p, default_timeout=120)


def hello_error(name: str, spec: dict, *, env: dict | None = None) -> str:
    c = start(name, spec, env=env)
    try:
        c.request("hello")
        return "no refusal"
    except B.ArmError as e:
        return str(e)
    finally:
        c.close()


def unit(name: str) -> Path:
    d = TMP / "units" / name
    d.mkdir(parents=True)
    return d


def msg(i: int, speaker: str = "Alice", role: str = "user", text: str | None = None) -> dict:
    return {"item_id": f"u:{i}", "session_id": "0", "role": role, "speaker": speaker,
            "text": text if text is not None else f"fact number {i} about the blue kettle"}


fakes: list = []


def err_of(fn) -> str:
    """The error a request raises, or 'no error'."""
    try:
        fn()
        return "no error"
    except Exception as e:  # noqa: BLE001
        return str(e)


def new_fake():
    f = FL.FakeLetta()
    fakes.append(f)
    return f


try:
    print("\n- Q9 and Q-47-1: the adapter's imports -")
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    check("arm_letta imports the standard library, base and _rest only",
          mods <= {"__future__", "hashlib", "json", "os", "re", "sys", "time", "urllib", "pathlib", "typing", "base",
                   "_rest"}, str(sorted(mods)))
    check("the declared calls conform to the fake's hand-written document (and the document is not the table)",
          AL.R.conformance(AL.CALLS, FL.DOC) == [], str(AL.R.conformance(AL.CALLS, FL.DOC)))

    print("\n- start: the pinned document, the agent, the tool set -")
    F1 = new_fake()
    u1 = unit("u1")
    c = start("w1", spec_for("w1", "write", u1, F1))
    h = safely(lambda: c.request("hello"), {})
    rec = json.loads((TMP / "w1.start.json").read_text(encoding="utf-8")) if (TMP / "w1.start.json").exists() else {}
    check("the write stage starts against the pinned document, every declared call checked",
          h.get("ok") and rec.get("ok") and rec.get("openapi") == {"openapi_sha256": pin(F1),
                                                                     "calls_checked": len(AL.CALLS)}, str(rec)[:300])
    creates = [b for (m, p, b) in F1.requests if m == "POST" and p == "/v1/agents/"]
    body = creates[0] if creates else {}
    check("one agent is created, named by the unit", len(creates) == 1 and body.get("name") == "u1", str(creates)[:200])
    check("Q-47-8d the agent gets exactly the spec's tool set with the spec's include_base_tools, and its pinned "
          "type, blocks and context window",
          body.get("tools") == sorted(TOOLS) and body.get("include_base_tools") is False
          and body.get("agent_type") == "memgpt_v2_agent" and body.get("memory_blocks") == AGENT["memory_blocks"],
          json.dumps(body)[:300])
    check("the model: deepseek-flash, openai type, the arm's proxy port as the container sees it with the unit prefix; "
          "no temperature, max_tokens or reasoning field of ours",
          body.get("llm_config") == {"model": "deepseek-flash", "model_endpoint_type": "openai",
                                     "model_endpoint": f"http://host.docker.internal:{PORT}/u/r1.u1/v1",
                                     "context_window": 32000}, json.dumps(body.get("llm_config")))
    check("the embedder: ollama type on the arm's Ollama leg as the container sees it, the v3 tag, 1024 dims",
          body.get("embedding_config") == {"embedding_endpoint_type": "ollama",
                                           "embedding_endpoint": f"http://host.docker.internal:{LEG}",
                                           "embedding_model": TAG, "embedding_dim": 1024},
          json.dumps(body.get("embedding_config")))
    check("the create body carries exactly the declared top-level fields",
          sorted(body) == sorted(["name", "agent_type", "memory_blocks", "tools", "include_base_tools", "llm_config",
                                  "embedding_config"]), str(sorted(body)))
    decl = rec.get("declared") or {}
    check("declared: roles, recall absent (Q-47-8b, an E5 row), the removed defaults, the tool set",
          "user" in decl.get("roles", "") and "Q-47-8b" in decl.get("recall", "")
          and decl.get("agent", {}).get("removed_defaults") == ["run_code"] and decl.get("tools_allowed") == sorted(TOOLS),
          json.dumps(decl)[:400])

    print("\n- write: one user message per call, the header, the tools after every step -")
    w = safely(lambda: c.request("write", item=msg(0, role="assistant", speaker="Bob", text="I moved to Oslo"),
                                 date="2023-05-08T10:00:00"), {})
    sent = [b for (m, p, b) in F1.requests if m == "POST" and p.endswith("/messages")]
    want_content = "Conversation from 2023-05-08:\nBob: I moved to Oslo"
    check("Q-47-8c role 'user' even for the assistant-side speaker; the §5.3 header first; '<speaker>: <text>'",
          sent == [{"messages": [{"role": "user", "content": want_content}]}], json.dumps(sent))
    check("the op answers the content's sha256", w.get("text_sha256") == hashlib.sha256(want_content.encode()).hexdigest()
          and w.get("tool_calls") == ["archival_memory_insert"], str(w)[:200])
    check("a dated stand's write without a date is refused",
          raises(lambda: c.request("write", item=msg(1)), B.ArmError, "date"))
    for i in range(1, 5):
        safely(lambda i=i: c.request("write", item=msg(i), date="2023-05-09"))
    agent_id = rec.get("agent_id", "")
    gets = [p for (m, p, b) in F1.requests if m == "GET" and p == f"/v1/agents/{agent_id}"]
    check("the agent's tools are re-checked after every step (one GET of the agent per message, plus the start's)",
          len(gets) == 1 + 5, str(len(gets)))
    check("read belongs to the read stage (Q25)", raises(lambda: c.request("read", qid="q", query="x", k=3, point="K"),
                                                         B.ArmError, "read stage"))
    cn = safely(lambda: c.request("counters"), {})
    check("usage is summed from the product's per-step usage (K87 check 2)",
          cn.get("usage") == {"step_count": 10, "prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600}
          and cn.get("messages_sent") == 5 and cn.get("tool_checks") == 5, str(cn)[:300])
    F1.page_cap = 2                                      # the server caps every list at 2 rows: paginate to the end
    e = safely(lambda: c.request("end_write"), {})
    a = F1.agents.get(agent_id, {})
    check("the state seal is written past a 2-row page cap, with the product's counts",
          e.get("footprint", {}).get("passages") == len(a.get("passages", [])) == 5
          and e.get("footprint", {}).get("messages") == len({m["id"] for m in a.get("messages", [])})
          and e.get("footprint", {}).get("blocks") == 2 and (u1 / B.SEAL_NAME).exists(), str(e)[:300])
    seal = json.loads((u1 / B.SEAL_NAME).read_text(encoding="utf-8")) if (u1 / B.SEAL_NAME).exists() else {}
    check("the seal names the agent's store URI", seal.get("store") == f"letta://127.0.0.1:{F1.port}/agents/{agent_id}",
          str(seal)[:200])
    c.close()
    F1.page_cap = None

    print("\n- read: a new process, the seal first, core blocks then archival hits -")
    r1 = start("r1", spec_for("r1", "read", u1, F1))
    h = safely(lambda: r1.request("hello"), {})
    rrec = json.loads((TMP / "r1.start.json").read_text(encoding="utf-8")) if (TMP / "r1.start.json").exists() else {}
    check("the read stage finds the agent by name and checks the seal before the first search",
          h.get("ok") and rrec.get("agent_id") == agent_id and rrec.get("seal"), str(rrec)[:300])
    n0 = len([1 for (m, p, b) in F1.requests if m == "POST"])
    r = safely(lambda: r1.request("read", qid="q1", query="blue kettle fact", k=3, point="K"), {})
    items = r.get("items") or []
    check("Q-47-8h Point K: the core blocks always, then the first k archival hits",
          [i["kind"] for i in items] == ["core", "core", "archival", "archival", "archival"]
          and items[0]["text"].startswith("human: ") and items[1]["text"] == "persona: I am a memory keeper."
          and [i["rank"] for i in items] == [1, 2, 3, 4, 5], json.dumps(items)[:400])
    searches = [p for (m, p, b) in F1.requests if "/archival-memory/search" in p]
    check("the archival search asks for top_k = k with the query", searches
          and "top_k=3" in searches[-1] and "query=blue+kettle+fact" in searches[-1], str(searches[-1:]))
    rb = safely(lambda: r1.request("read", qid="q2", query="kettle Oslo", k=200, point="B"), {})
    check("Point B: core + up to k hits (all five here)", [i["kind"] for i in rb.get("items") or []].count("archival") == 5
          and rb.get("core_blocks") == 2, str(rb)[:300])
    check("Point V refuses by name until A8 pins the default page size",
          raises(lambda: r1.request("read", qid="q3", query="x", k=10, point="V"), B.ArmError, "Q-47-8h"))
    check("write belongs to the write stage (Q25)", raises(lambda: r1.request("write", item=msg(9), date="2023-01-01"),
                                                           B.ArmError, "write stage"))
    check("a read sends nothing that writes (no POST)", len([1 for (m, p, b) in F1.requests if m == "POST"]) == n0)
    rc = safely(lambda: r1.request("counters"), {})
    check("counters: every REST call by its declared name", set((rc.get("http") or {}).get("calls", {})) <=
          set(AL.CALLS) and rc.get("reads") == 2, str(rc)[:300])
    r1.close()

    print("\n- in-process: the interleaving rule -")
    check("archival 1, recall 1, archival 2, ... then the rest",
          AL.interleave(["a1", "a2", "a3"], ["r1"]) == [("archival", "a1"), ("recall", "r1"), ("archival", "a2"),
                                                       ("archival", "a3")]
          and AL.interleave([], ["r1", "r2"]) == [("recall", "r1"), ("recall", "r2")])

    print("\n- refusals at start -")
    F2 = new_fake()
    check("a document that is not the pinned one refuses (sha256)",
          "not the pinned" in hello_error("x1", spec_for("x1", "write", unit("x1"), F2, openapi_sha256="0" * 64)))
    F3 = new_fake()
    F3.drop_route = "/v1/agents/{agent_id}/context"
    err = hello_error("x2", spec_for("x2", "write", unit("x2"), F3))
    check("a pinned document that lacks a declared call refuses, naming it", "context" in err and "not in the document"
          in err, err[:300])
    check("no agent is created when the document does not conform",
          not [1 for (m, p, b) in F3.requests if m == "POST"])
    F4 = new_fake()
    check("a proxy token in the child's environment refuses",
          "DEEPSEEK_API_KEY" in hello_error("x3", spec_for("x3", "write", unit("x3"), F4),
                                            env=child_env({"DEEPSEEK_API_KEY": "nvt3-test-token"})))
    ag = dict(AGENT, tools=TOOLS + ["run_code"])
    check("a spec tool outside §2.6.6 refuses (Q-47-8d)",
          "outside" in hello_error("x4", spec_for("x4", "write", unit("x4"), F4, agent=ag)))
    ag = dict(AGENT, include_base_tools=True)
    err = hello_error("x5", spec_for("x5", "write", unit("x5"), F4, agent=ag))
    check("a server that gives the agent tools beyond the set refuses by name (tool_violation)",
          "tool_violation" in err and "core_memory_append" in err, err[:300])
    F5 = new_fake()
    ux = unit("x6")
    c6 = start("x6a", spec_for("x6a", "write", ux, F5))
    safely(lambda: c6.request("hello"))
    c6.close()
    check("the write stage refuses when an agent of the unit's name exists",
          "fresh agent" in hello_error("x6b", spec_for("x6b", "write", ux, F5)))
    for label, over in (("a non-loopback server", {"server_url": "http://10.0.0.5:8283"}),
                        ("another container host", {"container_host": "127.0.0.1"}),
                        ("an agent config with an extra key", {"agent": dict(AGENT, temperature=0.7)}),
                        ("a missing agent key", {"agent": {k: v for k, v in AGENT.items() if k != "tools"}})):
        check(f"refused at start: {label}", "no refusal" not in hello_error(f"x7{label[:4]}", spec_for(
            "x7", "write", unit(f"x7-{uuid.uuid4().hex[:6]}"), F5, **over)))

    print("\n- tool violations during writes -")
    F6 = new_fake()
    u6 = unit("t6")
    c = start("t6", spec_for("t6", "write", u6, F6))
    safely(lambda: c.request("hello"))
    F6.rogue_call = "web_search"
    err = err_of(lambda: c.request("write", item=msg(0), date="2023-01-01"))
    check("a tool the agent calls outside its set is a tool_violation, by name", "tool_violation" in str(err)
          and "web_search" in str(err), str(err)[:200])
    F6.extra_tool_after = (2, "memory_rethink")       # grown during the unit's second message
    err = err_of(lambda: c.request("write", item=msg(1), date="2023-01-01"))
    check("a tool the agent grows during a step is a tool_violation of that step, by name",
          "tool_violation" in err and "memory_rethink" in err, err[:200])
    err = err_of(lambda: c.request("write", item=msg(2), date="2023-01-01"))
    check("... and of every step after it", "tool_violation" in err and "memory_rethink" in err, err[:200])
    cn = safely(lambda: c.request("counters"), {})
    check("tool violations are counted, one per violating step",
          cn.get("tool_violations") == 3 and cn.get("tool_checks") == 2, str({k: cn.get(k) for k in
                                                                            ("tool_violations", "tool_checks")}))
    c.close()

    print("\n- the state seal: gaps, a cursor that never moves, a change between stages -")
    F7 = new_fake()
    u7 = unit("s7")
    c = start("s7", spec_for("s7", "write", u7, F7, dated=False))
    safely(lambda: c.request("hello"))
    safely(lambda: c.request("write", item=msg(0)))
    undated = [b for (m, p, b) in F7.requests if m == "POST" and p.endswith("/messages")]
    check("an undated stand carries no header", undated and undated[0]["messages"][0]["content"] ==
          "Alice: fact number 0 about the blue kettle", json.dumps(undated)[:200])
    F7.context_extra = 1
    check("listings shorter than the context overview refuse (cut short)",
          raises(lambda: c.request("end_write"), B.ArmError, "cut short"))
    F7.context_extra = 0
    F7.ignore_after = True
    F7.page_cap = 2
    check("a cursor the server ignores refuses instead of looping",
          raises(lambda: c.request("end_write"), B.ArmError, "does not advance"))
    F7.ignore_after = False
    F7.overlap = True                                    # with a 2-message cap the pages move, repeat, then end
    F7.page_cap = 2
    err = err_of(lambda: c.request("end_write"))
    check("pages that move but repeat a row refuse as not advancing (never counted twice)",
          "does not advance" in err, err[:200])
    F7.overlap = False
    F7.page_cap = None
    e = safely(lambda: c.request("end_write"), {})
    check("with the server whole again the seal is written", bool(e.get("seal")), str(e)[:200])
    c.close()
    aid7 = next(iter(F7.agents))
    F7.agents[aid7]["passages"][0]["text"] = "tampered between the stages"
    check("a change to the agent's memory between the stages refuses the read stage (the state seal)",
          "no refusal" not in hello_error("s7r", spec_for("s7r", "read", u7, F7, dated=False)))
    F8 = new_fake()
    u8 = unit("d8")
    for n in ("d8a", "d8b"):
        cc = start(n, spec_for(n, "write", unit(f"{n}-dir"), F8))
        safely(lambda: cc.request("hello"))
        cc.close()
    for a in F8.agents.values():
        a["name"] = "d8"
    check("the read stage refuses two agents of the unit's name",
          "exactly one agent" in hello_error("d8r", spec_for("d8r", "read", u8, F8)))
finally:
    for p in children:
        if p.poll() is None:
            p.kill()
    for f in fakes:
        f.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 arm letta: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
