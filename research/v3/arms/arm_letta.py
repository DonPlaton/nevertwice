#!/usr/bin/env python3
"""PREREG-V3 TB4.7b (A6): the letta adapter - the product arm "letta", one child per (run, unit) and stage (Q25),
speaking base.py's protocol. It runs on the polygon's base Python from the arm's runs-tree directory beside
byte-identical copies of base.py and _rest.py (Q9 O-b, Q-47-1); it imports the standard library only and talks to the
(arm, run, block) Letta server - the pinned image in Docker - over its REST API on loopback.

    <polygon python> -B arm_letta.py <spec.json>

rev1 §2.2 and §2.6.6 as the auditor's Q-47-1..8 read them:
* at every start the server's own /openapi.json must be the A8 pin (sha256), and must carry every call this adapter
  declares - method, path, parameters, nested body fields, response fields (Q-47-2, Q-47-8a); anything else refuses;
* one agent per unit, named by the unit id; the write stage needs none of that name yet;
* the agent's type, memory blocks and context window are the pinned version's defaults (A8), given in the spec; its
  tools are the default set of that agent type intersected with §2.6.6's allowed set (Q-47-8d): the spec gives the
  set and the include_base_tools / tools that produce exactly it, and every removed default is declared. Nothing the
  vendor's default agent lacks is added. The agent's tools are checked equal to the set at creation and after every
  step, and a tool called outside it is a tool_violation;
* the model is deepseek-flash through the arm's proxy port as the container sees it
  (http://host.docker.internal:<port>/u/<run>.<unit>/v1, an openai endpoint - the unit prefix attributes the calls);
  no temperature, max_tokens or reasoning field of ours (§5.5; thinking: none known, Q-47-8e); the proxy token is the
  server's (its container environment), never this child's;
* the embedder is Letta's ollama endpoint type on the arm's Ollama leg as the container sees it, with the same unit
  prefix (http://host.docker.internal:<leg port>/u/<run>.<unit> - B-A3: its embedding calls are the unit's, Q-A4-6 (2)),
  the v3 tag, 1024 dims;
* write: one message per call, role "user" for every speaker (Q-47-8c: an assistant-role message would be the
  agent's own words), content "<speaker>: <text>", on a dated stand after the §5.3 header "Conversation from
  <YYYY-MM-DD>:"; Letta's message API is given no date;
* the state between the stages (Q-47-5, Q-47-8f): end_write reads the core blocks, every archival passage and every
  recall message, paginated to the end (a page that repeats or a cursor that never advances refuses), checks the
  counts against the agent's context overview, lists the messages a second time at another page size and requires
  the same (id, message_type) rows (A8-L2: a limit that counts typed rows would cut a message at a page edge), and
  writes a state seal over (id, text) of each; the read stage recomputes it before the first search;
* read (Q-47-8h): the core blocks always ("<label>: <value>"), then archival search hits interleaved with recall
  search hits (archival 1, recall 1, archival 2, ...); at Point K the first k hits, at Point B up to k (the harness's
  budget cuts). The pinned OpenAPI has no recall search route unless A8 names one (Q-47-8b): until then the reader is
  core + archival, a declared deviation that may understate Letta (an E5 row). Point V (core + archival at the
  pinned default page size, no recall) refuses until A8 pins that size.

The spec: arm ("letta"), stage, stand, run, unit, unit_dir, server_url, openapi_sha256, port, ollama_leg_port,
container_host, embed_tag, dated, agent {agent_type, memory_blocks, context_window, tools, include_base_tools,
removed_defaults}, record_path.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import base as B  # noqa: E402 - the protocol module, copied beside this file
import _rest as R  # noqa: E402 - the declared-call REST client, copied beside this file

ARM = "letta"
STAGES = ("write", "read")
SPEC_KEYS = ("arm", "stage", "stand", "run", "unit", "unit_dir", "server_url", "openapi_sha256", "port",
             "ollama_leg_port", "container_host", "embed_tag", "dated", "agent", "record_path")
AGENT_KEYS = ("agent_type", "memory_blocks", "context_window", "tools", "include_base_tools", "removed_defaults")
CONTAINER_HOST = "host.docker.internal"
DEEPSEEK_MODEL = "deepseek-flash"
EMBED_DIMS = 1024
TOKEN_NAME = "DEEPSEEK_API_KEY"
#: rev1 §2.6.6: the tools a Letta agent may have - an upper bound, not a list to attach (Q-47-8d).
ALLOWED_TOOLS = frozenset({"send_message", "conversation_search", "archival_memory_insert", "archival_memory_search",
                           "core_memory_append", "core_memory_replace", "memory_insert", "memory_replace",
                           "memory_rethink", "memory_finish_edits"})
POINTS = ("K", "B")
PAGE = 100
#: A8-L2 (the auditor): a second, coprime page size for the messages listing - a server whose limit counts typed rows
#: can cut a stored message at a page edge and lose a row; the two listings must hold the same (id, message_type) rows.
PAGE_ALT = 7
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

C = R.Call
#: Every REST call this adapter makes. Checked at every start against the pinned server's /openapi.json (Q-47-2).
CALLS = {
    "list_agents": C("GET", "/v1/agents/", query=("name", "limit"), response=("[].id", "[].name")),
    "create_agent": C("POST", "/v1/agents/",
                      body=("name", "agent_type", "memory_blocks[].label", "memory_blocks[].value",
                            "memory_blocks[].limit", "tools", "include_base_tools", "llm_config.model",
                            "llm_config.model_endpoint_type", "llm_config.model_endpoint", "llm_config.context_window",
                            "embedding_config.embedding_endpoint_type", "embedding_config.embedding_endpoint",
                            "embedding_config.embedding_model", "embedding_config.embedding_dim"),
                      response=("id", "name", "tools[].name")),
    "get_agent": C("GET", "/v1/agents/{agent_id}",
                   response=("id", "name", "tools[].name", "llm_config.model", "llm_config.model_endpoint",
                             "embedding_config.embedding_endpoint", "embedding_config.embedding_model")),
    "send_message": C("POST", "/v1/agents/{agent_id}/messages", body=("messages[].role", "messages[].content"),
                      response=("messages[].message_type", "messages[].tool_call.name", "usage.step_count",
                                "usage.prompt_tokens", "usage.completion_tokens", "usage.total_tokens",
                                "stop_reason.stop_reason")),
    "list_blocks": C("GET", "/v1/agents/{agent_id}/core-memory/blocks", response=("[].id", "[].label", "[].value")),
    "list_passages": C("GET", "/v1/agents/{agent_id}/archival-memory", query=("after", "limit"),
                       response=("[].id", "[].text")),
    "search_passages": C("GET", "/v1/agents/{agent_id}/archival-memory/search", query=("query", "top_k"),
                         response=("results[].content",)),
    "list_messages": C("GET", "/v1/agents/{agent_id}/messages", query=("after", "limit"),
                       response=("[].id", "[].message_type", "[].content", "[].reasoning", "[].tool_call.name",
                                 "[].tool_return")),
    "context": C("GET", "/v1/agents/{agent_id}/context", response=("num_archival_memory", "num_recall_memory")),
}
#: Q-47-8b: the recall (conversation) search route of the pinned version, or None - A8 names it from the pinned
#: OpenAPI. None: the reader is core + archival, declared.
RECALL_SEARCH = None
_MESSAGE_TEXT = ("message_type", "content", "reasoning", "tool_call", "tool_return")
_FORBIDDEN = re.compile(r"bash|shell|powershell|cmd|terminal|exec|run_code|code_interpreter|python|computer|browser|"
                        r"web|fetch|http|url|download|task|agent|notebook|kill", re.I)


class Refused(RuntimeError):
    """The arm may not start, or may not take this request."""


def load_spec(path: str | os.PathLike) -> dict:
    spec = json.loads(Path(path).read_bytes().decode("utf-8"))
    missing = [k for k in SPEC_KEYS if k not in spec]
    extra = sorted(set(spec) - set(SPEC_KEYS))
    if missing or extra:
        raise Refused(f"the spec lacks {missing} or carries unknown keys {extra}")
    if spec["arm"] != ARM or spec["stage"] not in STAGES:
        raise Refused(f"unknown arm {spec['arm']!r} or stage {spec['stage']!r}")
    if not (_ID.fullmatch(str(spec["run"])) and _ID.fullmatch(str(spec["unit"]))):
        raise Refused("run and unit ids are [A-Za-z0-9_-]{1,64}, no dots (Q3)")
    for k in ("port", "ollama_leg_port"):
        if not isinstance(spec[k], int) or isinstance(spec[k], bool):
            raise Refused(f"{k} is the proxy's port number for this arm")
    if spec["container_host"] != CONTAINER_HOST:
        raise Refused(f"the container reaches the host's proxy through {CONTAINER_HOST} (rev1 §2.2), "
                      f"not {spec['container_host']!r}")
    a = spec["agent"]
    if not isinstance(a, dict) or sorted(a) != sorted(AGENT_KEYS):
        raise Refused(f"the agent config is exactly {list(AGENT_KEYS)} (the pinned defaults, A8)")
    tools = a["tools"]
    if not (isinstance(tools, list) and tools and all(isinstance(t, str) for t in tools)
            and len(set(tools)) == len(tools)):
        raise Refused("the agent's tool set is a non-empty list of distinct names")
    outside = sorted(set(tools) - ALLOWED_TOOLS)
    if outside:
        raise Refused(f"tools outside rev1 §2.6.6's allowed set: {outside} (Q-47-8d)")
    if any(_FORBIDDEN.search(t) and t not in ALLOWED_TOOLS for t in tools):
        raise Refused("a forbidden tool pattern (§2.6.6)")
    if not isinstance(a["include_base_tools"], bool) or not isinstance(a["removed_defaults"], list):
        raise Refused("include_base_tools is a bool and removed_defaults a list (every removed default declared)")
    return spec


def llm_endpoint(spec: Mapping[str, Any]) -> str:
    return f"http://{spec['container_host']}:{spec['port']}/u/{spec['run']}.{spec['unit']}/v1"


def embedding_endpoint(spec: Mapping[str, Any]) -> str:
    """B-A3: the arm's Ollama leg with the unit prefix - the proxy attributes each embedding call to its unit."""
    return f"http://{spec['container_host']}:{spec['ollama_leg_port']}/u/{spec['run']}.{spec['unit']}"


def create_body(spec: Mapping[str, Any]) -> dict:
    a = spec["agent"]
    return {"name": spec["unit"], "agent_type": a["agent_type"], "memory_blocks": a["memory_blocks"],
            "tools": sorted(a["tools"]), "include_base_tools": a["include_base_tools"],
            "llm_config": {"model": DEEPSEEK_MODEL, "model_endpoint_type": "openai",
                           "model_endpoint": llm_endpoint(spec), "context_window": a["context_window"]},
            "embedding_config": {"embedding_endpoint_type": "ollama", "embedding_endpoint": embedding_endpoint(spec),
                                 "embedding_model": spec["embed_tag"], "embedding_dim": EMBED_DIMS}}


def store_uri(spec: Mapping[str, Any], agent_id: str) -> str:
    u = urllib.parse.urlsplit(spec["server_url"])
    return f"letta://{u.hostname}:{u.port}/agents/{agent_id}"


def declared(spec: Mapping[str, Any]) -> dict:
    a = spec["agent"]
    return {"date_route": "header" if spec["dated"] else "none",
            "roles": "every speaker as role 'user', '<speaker>: <text>' (Q-47-8c)",
            "renderer": {"name": "core blocks '<label>: <value>', then archival and recall hits interleaved"},
            "recall": ("none: the pinned OpenAPI has no recall search route - core + archival (Q-47-8b; may "
                       "understate Letta, an E5 row)") if RECALL_SEARCH is None else f"route {RECALL_SEARCH}",
            "threshold": "n/a", "limit": "Point K: core + the first k hits; Point B: core + up to k hits",
            "namespace": "one agent per unit, named by the unit id, in the (arm, run, block) server",
            "write_granularity": "one message per call",
            "llm_params": {"temperature": "product default (value in effect: the proxy capture, §5.5)",
                           "max_tokens": "product default", "thinking_route": "none known (Q-47-8e)"},
            "agent": {"agent_type": a["agent_type"], "context_window": a["context_window"],
                      "memory_block_labels": [b.get("label") for b in a["memory_blocks"]],
                      "include_base_tools": a["include_base_tools"], "removed_defaults": a["removed_defaults"]},
            "tools_allowed": sorted(a["tools"]), "store_persistence": "server (state seal)"}


def interleave(archival: list, recall: list) -> list:
    """archival 1, recall 1, archival 2, recall 2, ... then the longer list's rest."""
    out = []
    for i in range(max(len(archival), len(recall))):
        if i < len(archival):
            out.append(("archival", archival[i]))
        if i < len(recall):
            out.append(("recall", recall[i]))
    return out


def paginate(rest: R.Rest, name: str, agent_id: str, page: int = PAGE) -> list:
    """Every row of a list endpoint, following its after-cursor (the last row's id) until an empty page. A row is
    (id, message_type): Letta serialises one stored message as several typed rows sharing its id. A row seen twice,
    or a cursor that does not move, refuses - the listing would otherwise be cut short or never end."""
    rows: list = []
    seen: set = set()
    after = None
    while True:
        got = rest.call(name, path={"agent_id": agent_id},
                        query={"limit": page, **({"after": after} if after else {})}) or []
        if not got:
            return rows
        keys = [(r["id"], r.get("message_type")) for r in got]
        if got[-1]["id"] == after or seen.intersection(keys) or len(set(keys)) != len(keys):
            raise Refused(f"{name}: the pagination does not advance past {after!r} - the listing would be cut short")
        rows += got
        seen.update(keys)
        after = got[-1]["id"]


def agent_state(rest: R.Rest, agent_id: str) -> tuple[str, dict]:
    """The agent's memory as the product lists it - complete by its own context overview - and its digest."""
    blocks = rest.call("list_blocks", path={"agent_id": agent_id}) or []
    passages = paginate(rest, "list_passages", agent_id)
    messages = paginate(rest, "list_messages", agent_id)
    again = paginate(rest, "list_messages", agent_id, page=PAGE_ALT)
    if [(m["id"], m.get("message_type")) for m in messages] != [(m["id"], m.get("message_type")) for m in again]:
        raise Refused(f"the messages listing depends on the page size ({PAGE} vs {PAGE_ALT}): a page edge cuts a stored "
                      "message (A8-L2)")
    ctx = rest.call("context", path={"agent_id": agent_id}) or {}
    got = {"blocks": len(blocks), "passages": len(passages), "messages": len({m["id"] for m in messages})}
    if got["passages"] != ctx.get("num_archival_memory") or got["messages"] != ctx.get("num_recall_memory"):
        raise Refused(f"the listings are cut short: they hold {got}, the agent's context overview says "
                      f"{ctx.get('num_archival_memory')} archival and {ctx.get('num_recall_memory')} recall (Q-47-8f)")
    canon = {"blocks": sorted([b["id"], b.get("label"), b.get("value")] for b in blocks),
             "passages": sorted([p["id"], p.get("text")] for p in passages),
             "messages": sorted(json.dumps([m["id"]] + [m.get(k) for k in _MESSAGE_TEXT], ensure_ascii=False,
                                           sort_keys=True, default=str) for m in messages)}
    digest = hashlib.sha256(json.dumps(canon, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return digest, got


def check_tools(state: Mapping[str, Any], want: set) -> None:
    have = {t.get("name") for t in state.get("tools") or []}
    if have != want:
        raise Refused(f"tool_violation: the agent's tools are not the set - extra {sorted(have - want)}, "
                      f"missing {sorted(want - have)} (Q-47-8d)")


def bind(spec: Mapping[str, Any], env: Mapping[str, str]) -> tuple[dict, dict]:
    rec: dict[str, Any] = {"arm": ARM, "stage": spec["stage"], "stand": spec["stand"], "run": spec["run"],
                           "unit": spec["unit"], "declared": declared(spec)}
    if TOKEN_NAME in env:
        raise Refused(f"{TOKEN_NAME} is in the environment: the proxy token is the Letta server's, never this child's")
    rest = R.Rest(spec["server_url"], CALLS)
    rec["openapi"] = R.verify_server(rest, spec["openapi_sha256"])       # Q-47-2, at every start
    want = set(spec["agent"]["tools"])
    same = rest.call("list_agents", query={"name": spec["unit"]}) or []
    same = [a for a in same if a.get("name") == spec["unit"]]
    if spec["stage"] == "write":
        if same:
            raise Refused(f"the write stage needs a fresh agent, and {len(same)} named {spec['unit']!r} exist")
        state = rest.call("create_agent", body=create_body(spec))
        agent_id = state["id"]
        state = rest.call("get_agent", path={"agent_id": agent_id})
        check_tools(state, want)
        llm, emb = state.get("llm_config") or {}, state.get("embedding_config") or {}
        if llm.get("model") != DEEPSEEK_MODEL or llm.get("model_endpoint") != llm_endpoint(spec):
            raise Refused(f"the agent's model is not deepseek-flash at the arm's endpoint: {llm.get('model')!r} at "
                          f"{llm.get('model_endpoint')!r}")
        if emb.get("embedding_endpoint") != embedding_endpoint(spec) or emb.get("embedding_model") != spec["embed_tag"]:
            raise Refused("the agent's embedder is not the v3 tag on the arm's Ollama leg")
    else:
        if len(same) != 1:
            raise Refused(f"the read stage needs exactly one agent named {spec['unit']!r}, found {len(same)}")
        agent_id = same[0]["id"]
        check_tools(rest.call("get_agent", path={"agent_id": agent_id}), want)
        digest, _counts = agent_state(rest, agent_id)
        rec["seal"] = B.check_state_seal(Path(spec["unit_dir"]), store_uri(spec, agent_id), digest)
    rec["agent_id"] = agent_id
    rec["store"] = store_uri(spec, agent_id)
    rec["env_names"] = sorted(env)
    rec["python"] = sys.version.split()[0]
    return {"rest": rest, "agent_id": agent_id, "want": want}, rec


class Handler:
    def __init__(self, spec: Mapping[str, Any], ns: Mapping[str, Any], rec: Mapping[str, Any]) -> None:
        self.spec, self.rec = spec, rec
        self.rest, self.agent_id, self.want = ns["rest"], ns["agent_id"], ns["want"]
        self.stage, self.unit = spec["stage"], spec["unit"]
        self.usage = {"step_count": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.steps = {"messages_sent": 0, "step_errors": 0, "tool_checks": 0, "tool_violations": 0}
        self.reads = {"reads": 0, "items_returned": 0}

    def _stage(self, want: str, op: str) -> None:
        if self.stage != want:
            raise RuntimeError(f"{op} belongs to the {want} stage; this process is the {self.stage} stage (Q25)")

    def hello(self) -> dict:
        return {"protocol": B.PROTOCOL, "system": "letta", "arm": ARM, "stage": self.stage,
                "version": self.rec["openapi"]["openapi_sha256"], "python": self.rec["python"],
                "llm_label": f"deepseek:{DEEPSEEK_MODEL}", "embedder": self.spec["embed_tag"],
                "env_names": self.rec["env_names"], "start": self.rec}

    def _violation(self, why: str) -> Refused:
        self.steps["tool_violations"] += 1
        return Refused(f"tool_violation: {why}")

    def write(self, item: dict, date: str | None = None) -> dict:
        self._stage("write", "write")
        t0 = time.time()
        content = f"{item['speaker']}: {item['text']}"
        if self.spec["dated"]:
            if not date:
                raise ValueError("a dated stand's write carries its date (§5.3 header)")
            content = f"Conversation from {date[:10]}:\n{content}"
        self.steps["messages_sent"] += 1
        resp = self.rest.call("send_message", path={"agent_id": self.agent_id},
                              body={"messages": [{"role": "user", "content": content}]}) or {}
        for k in self.usage:
            self.usage[k] += int((resp.get("usage") or {}).get(k) or 0)
        stop = (resp.get("stop_reason") or {}).get("stop_reason")
        if stop not in (None, "end_turn"):
            self.steps["step_errors"] += 1
        called = [m.get("tool_call", {}).get("name") for m in resp.get("messages") or []
                  if m.get("message_type") == "tool_call_message"]
        outside = sorted({c for c in called if c not in self.want})
        if outside:
            raise self._violation(f"the agent called {outside}, outside its tool set")
        self.steps["tool_checks"] += 1
        try:
            check_tools(self.rest.call("get_agent", path={"agent_id": self.agent_id}), self.want)
        except Refused as e:
            raise self._violation(str(e).removeprefix("tool_violation: ")) from None
        return {"op_id": item["item_id"], "text_sha256": B.text_sha256(content), "stop_reason": stop,
                "tool_calls": called, "t0": t0, "t1": time.time()}

    def end_write(self) -> dict:
        self._stage("write", "end_write")
        t0 = time.time()
        digest, counts = agent_state(self.rest, self.agent_id)
        seal = B.write_state_seal(self.spec["unit_dir"], store_uri(self.spec, self.agent_id), digest, arm=ARM,
                                  run=self.spec["run"], unit=self.unit, counts=counts)
        return {"footprint": {"retrievable": counts["blocks"] + counts["passages"], **counts}, "seal": seal,
                "t0": t0, "t1": time.time()}

    def read(self, qid: str, query: str, k: int, point: str | None = None) -> dict:
        self._stage("read", "read")
        if point not in POINTS:
            raise ValueError("point is K or B; Point V refuses until A8 pins Letta's default archival page size "
                             "(Q-47-8h)")
        t0 = time.time()
        blocks = self.rest.call("list_blocks", path={"agent_id": self.agent_id}) or []
        found = self.rest.call("search_passages", path={"agent_id": self.agent_id},
                               query={"query": query, "top_k": int(k)}) or {}
        archival = [r.get("content") for r in found.get("results") or []]
        hits = interleave(archival[:int(k)], [])
        texts = [("core", f"{b.get('label')}: {b.get('value')}") for b in blocks] + hits[:int(k)]
        items = [{"kind": kind, "text": text, "rank": r} for r, (kind, text) in enumerate(texts, 1)]
        self.reads["reads"] += 1
        self.reads["items_returned"] += len(items)
        return {"qid": qid, "items": items, "items_returned": len(items), "k": int(k), "point": point,
                "core_blocks": len(blocks), "t0": t0, "t1": time.time()}

    def counters(self) -> dict:
        return {"http": self.rest.snapshot(), "usage": dict(self.usage), **self.steps, **self.reads}


class RefusedHandler:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __getattr__(self, op: str):
        if op not in B.OPS:
            raise AttributeError(op)

        def refuse(**_kw):
            raise Refused(f"the arm refused to start: {self.reason}")
        return refuse


def _record(path: str, rec: Mapping[str, Any]) -> None:
    Path(path).write_bytes((json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=1, default=str) + "\n")
                           .encode("utf-8"))


def build(spec_path: str, env: Mapping[str, str]) -> Any:
    try:
        spec = load_spec(spec_path)
    except (Refused, OSError, ValueError, KeyError, TypeError) as e:
        return RefusedHandler(f"{type(e).__name__}: {e}")
    try:
        ns, rec = bind(spec, env)
        handler = Handler(spec, ns, rec)
    except Exception as e:  # noqa: BLE001 - every failure to bind is a named refusal, recorded
        reason = f"{type(e).__name__}: {e}"
        _record(spec["record_path"], {"ok": False, "error": reason[:B.ERROR_MAX], "arm": ARM,
                                      "stage": spec["stage"], "unit": spec["unit"]})
        return RefusedHandler(reason)
    _record(spec["record_path"], {"ok": True, **rec})
    return handler


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: arm_letta.py <spec.json>\n")
        sys.exit(2)
    ENV_AT_START = dict(os.environ)
    sys.exit(B.main_with(lambda: build(sys.argv[1], ENV_AT_START)))
