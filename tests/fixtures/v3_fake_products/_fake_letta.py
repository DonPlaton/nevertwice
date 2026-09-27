"""A FAKE Letta server for the v3 adapter suite (research/v3/arms/arm_letta.py): the REST routes the adapter declares,
an in-memory agent that "edits its memory" deterministically, and its own OpenAPI document at /openapi.json.

The document below is written by hand in the shape of Letta's published schema (agents, messages, core-memory blocks,
archival passages, the context overview); it is NOT derived from the adapter's call table, so the adapter's start-up
conformance check runs against something it did not write. The real document is the pinned image's (A8).

Fault knobs, set on the instance by the suite: extra_tool_after (a tool the agent grows after N messages),
rogue_call (a tool name the agent calls in its next response), page_cap (the server caps every list at this many),
ignore_after (list endpoints ignore the cursor - a pagination that never ends), overlap (each page starts AT the
cursor's row - pages that move but repeat), limit_counts_rows (the limit counts typed rows, so a page edge can cut a
stored message and the after-cursor then skips its remaining rows), context_extra (the context overview
reports more archival passages than the list returns), drop_route (a path left out of /openapi.json).
"""
from __future__ import annotations

import copy
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

S = "#/components/schemas/"


def _obj(props: dict, required=()) -> dict:
    return {"type": "object", "properties": props, **({"required": list(required)} if required else {})}


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def _ref(name: str) -> dict:
    return {"$ref": S + name}


def _json(schema: dict) -> dict:
    return {"content": {"application/json": {"schema": schema}}}


STR, INT, BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
AGENT_ID = {"name": "agent_id", "in": "path", "required": True, "schema": STR}

DOC = {
    "openapi": "3.1.0",
    "info": {"title": "Letta (fake)", "version": "0.0.0-fake"},
    "paths": {
        "/v1/agents/": {
            "get": {"parameters": [{"name": "name", "in": "query", "schema": STR},
                                   {"name": "limit", "in": "query", "schema": INT},
                                   {"name": "after", "in": "query", "schema": STR}],
                    "responses": {"200": _json(_arr(_ref("AgentState")))}},
            "post": {"requestBody": {"required": True, **_json(_ref("CreateAgentRequest"))},
                     "responses": {"200": _json(_ref("AgentState"))}},
        },
        "/v1/agents/{agent_id}": {
            "parameters": [AGENT_ID],
            "get": {"responses": {"200": _json(_ref("AgentState"))}},
        },
        "/v1/agents/{agent_id}/messages": {
            "parameters": [AGENT_ID],
            "get": {"parameters": [{"name": "after", "in": "query", "schema": STR},
                                   {"name": "before", "in": "query", "schema": STR},
                                   {"name": "limit", "in": "query", "schema": INT}],
                    "responses": {"200": _json(_arr(_ref("LettaMessageUnion")))}},
            "post": {"requestBody": {"required": True, **_json(_ref("LettaRequest"))},
                     "responses": {"200": _json(_ref("LettaResponse"))}},
        },
        "/v1/agents/{agent_id}/core-memory/blocks": {
            "parameters": [AGENT_ID],
            "get": {"responses": {"200": _json(_arr(_ref("Block")))}},
        },
        "/v1/agents/{agent_id}/archival-memory": {
            "parameters": [AGENT_ID],
            "get": {"parameters": [{"name": "after", "in": "query", "schema": STR},
                                   {"name": "limit", "in": "query", "schema": INT},
                                   {"name": "ascending", "in": "query", "schema": BOOL}],
                    "responses": {"200": _json(_arr(_ref("Passage")))}},
        },
        "/v1/agents/{agent_id}/archival-memory/search": {
            "parameters": [AGENT_ID],
            "get": {"parameters": [{"name": "query", "in": "query", "required": True, "schema": STR},
                                   {"name": "top_k", "in": "query", "schema": INT}],
                    "responses": {"200": _json(_ref("ArchivalMemorySearchResponse"))}},
        },
        "/v1/agents/{agent_id}/context": {
            "parameters": [AGENT_ID],
            "get": {"responses": {"200": _json(_ref("ContextWindowOverview"))}},
        },
    },
    "components": {"schemas": {
        "LLMConfig": _obj({"model": STR, "model_endpoint_type": STR, "model_endpoint": STR, "context_window": INT,
                           "temperature": {"type": "number"}, "max_tokens": INT, "enable_reasoner": BOOL},
                          required=("model", "model_endpoint_type", "context_window")),
        "EmbeddingConfig": _obj({"embedding_endpoint_type": STR, "embedding_endpoint": STR, "embedding_model": STR,
                                 "embedding_dim": INT, "embedding_chunk_size": INT},
                                required=("embedding_endpoint_type", "embedding_model", "embedding_dim")),
        "CreateBlock": _obj({"label": STR, "value": STR, "limit": INT, "description": STR}, required=("label", "value")),
        "MessageCreate": _obj({"role": {"type": "string", "enum": ["user", "system", "assistant"]},
                               "content": {"anyOf": [STR, _arr({"type": "object"})]}, "name": STR},
                              required=("role", "content")),
        "CreateAgentRequest": _obj({
            "name": STR, "agent_type": STR, "memory_blocks": {"anyOf": [_arr(_ref("CreateBlock")), {"type": "null"}]},
            "tools": {"anyOf": [_arr(STR), {"type": "null"}]}, "tool_ids": _arr(STR), "include_base_tools": BOOL,
            "include_multi_agent_tools": BOOL, "llm_config": {"anyOf": [_ref("LLMConfig"), {"type": "null"}]},
            "embedding_config": {"anyOf": [_ref("EmbeddingConfig"), {"type": "null"}]}, "model": STR,
            "embedding": STR, "system": STR}),
        "Tool": _obj({"id": STR, "name": STR, "tool_type": STR}),
        "Block": _obj({"id": STR, "label": STR, "value": STR, "limit": INT}),
        "Memory": _obj({"blocks": _arr(_ref("Block"))}),
        "AgentState": _obj({"id": STR, "name": STR, "agent_type": STR, "tools": _arr(_ref("Tool")),
                            "llm_config": _ref("LLMConfig"), "embedding_config": _ref("EmbeddingConfig"),
                            "memory": _ref("Memory")}),
        "LettaRequest": _obj({"messages": _arr(_ref("MessageCreate")), "use_assistant_message": BOOL},
                             required=("messages",)),
        "ToolCall": _obj({"name": STR, "arguments": STR, "tool_call_id": STR}),
        "UserMessage": _obj({"id": STR, "message_type": STR, "content": STR, "date": STR}),
        "AssistantMessage": _obj({"id": STR, "message_type": STR, "content": STR, "date": STR}),
        "ReasoningMessage": _obj({"id": STR, "message_type": STR, "reasoning": STR, "date": STR}),
        "ToolCallMessage": _obj({"id": STR, "message_type": STR, "tool_call": _ref("ToolCall"), "date": STR}),
        "ToolReturnMessage": _obj({"id": STR, "message_type": STR, "tool_return": STR, "status": STR, "date": STR}),
        "SystemMessage": _obj({"id": STR, "message_type": STR, "content": STR, "date": STR}),
        "LettaMessageUnion": {"oneOf": [_ref("SystemMessage"), _ref("UserMessage"), _ref("ReasoningMessage"),
                                        _ref("ToolCallMessage"), _ref("ToolReturnMessage"),
                                        _ref("AssistantMessage")]},
        "LettaUsageStatistics": _obj({"completion_tokens": INT, "prompt_tokens": INT, "total_tokens": INT,
                                      "step_count": INT}),
        "LettaStopReason": _obj({"stop_reason": STR}),
        "LettaResponse": _obj({"messages": _arr(_ref("LettaMessageUnion")), "usage": _ref("LettaUsageStatistics"),
                               "stop_reason": _ref("LettaStopReason")}),
        "Passage": _obj({"id": STR, "text": STR, "agent_id": STR, "created_at": STR}),
        "ArchivalMemorySearchResult": _obj({"timestamp": STR, "content": STR, "tags": _arr(STR)}),
        "ArchivalMemorySearchResponse": _obj({"results": _arr(_ref("ArchivalMemorySearchResult")), "count": INT}),
        "ContextWindowOverview": _obj({"context_window_size_max": INT, "num_messages": INT,
                                       "num_archival_memory": INT, "num_recall_memory": INT}),
    }},
}

#: The fake agent's default tool families: what include_base_tools=True adds.
BASE_TOOLS = ("send_message", "conversation_search", "archival_memory_insert", "archival_memory_search",
              "core_memory_append", "core_memory_replace")


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


class FakeLetta:
    def __init__(self) -> None:
        self.doc = copy.deepcopy(DOC)
        self.requests: list[tuple[str, str, object]] = []
        self.agents: dict[str, dict] = {}
        self.n = 0
        self.extra_tool_after: tuple[int, str] | None = None
        self.rogue_call: str | None = None
        self.page_cap: int | None = None
        self.ignore_after = False
        self.overlap = False
        self.limit_counts_rows = False
        self.context_extra = 0
        self.drop_route: str | None = None
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _send(self, code: int, obj) -> None:
                data = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self) -> None:
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                u = urllib.parse.urlsplit(self.path)
                q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
                with fake.lock:
                    fake.requests.append((self.command, self.path, body))
                    try:
                        code, out = fake.route(self.command, urllib.parse.unquote(u.path), q, body)
                    except KeyError as e:
                        code, out = 404, {"detail": f"not found: {e}"}
                self._send(code, out)

            do_GET = do_POST = _handle

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def doc_bytes(self) -> bytes:
        d = copy.deepcopy(self.doc)
        if self.drop_route:
            d["paths"].pop(self.drop_route, None)
        return json.dumps(d, sort_keys=True).encode("utf-8")

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()

    def _id(self, kind: str) -> str:
        self.n += 1
        return f"{kind}-{self.n:05d}"

    def _page(self, rows: list, q: dict) -> list:
        if q.get("after") and self.overlap:                     # a cursor off by one: the page repeats its row
            ids = [r["id"] for r in rows]
            rows = rows[ids.index(q["after"]):] if q["after"] in ids and q["after"] != ids[-1] else []
        elif q.get("after") and not self.ignore_after:          # after a stored message: past ALL its typed rows
            ids = [r["id"] for r in rows]
            rows = rows[len(ids) - ids[::-1].index(q["after"]):] if q["after"] in ids else []
        limit = int(q.get("limit") or 50)
        if self.page_cap:
            limit = min(limit, self.page_cap)
        if self.limit_counts_rows:                           # a limit over typed rows: a page edge can cut a message
            return rows[:limit]
        out, ids = [], []                                  # the limit counts stored messages, never splitting one
        for r in rows:
            if r["id"] not in ids:
                if len(ids) == limit:
                    break
                ids.append(r["id"])
            out.append(r)
        return out

    def _state(self, a: dict) -> dict:
        return {"id": a["id"], "name": a["name"], "agent_type": a["agent_type"],
                "tools": [{"id": f"tool-{t}", "name": t, "tool_type": "letta_core"} for t in a["tools"]],
                "llm_config": a["llm_config"], "embedding_config": a["embedding_config"],
                "memory": {"blocks": a["blocks"]}}

    def route(self, method: str, path: str, q: dict, body):
        if method == "GET" and path == "/openapi.json":
            return 200, self.doc_bytes()
        if path == "/v1/agents/":
            if method == "POST":
                a = {"id": self._id("agent"), "name": body.get("name"), "agent_type": body.get("agent_type"),
                     "tools": list(body.get("tools") or []) + (list(BASE_TOOLS) if body.get("include_base_tools", True)
                                                             else []),
                     "llm_config": body.get("llm_config"), "embedding_config": body.get("embedding_config"),
                     "blocks": [{"id": self._id("block"), "label": b["label"], "value": b["value"],
                                 "limit": b.get("limit", 5000)} for b in body.get("memory_blocks") or []],
                     "passages": [], "messages": [], "sent": 0}
                a["tools"] = list(dict.fromkeys(a["tools"]))
                self.agents[a["id"]] = a
                return 200, self._state(a)
            rows = [self._state(a) for a in self.agents.values() if not q.get("name") or a["name"] == q["name"]]
            return 200, rows
        m = re.fullmatch(r"/v1/agents/([^/]+)(/.*)?", path)
        if not m:
            return 404, {"detail": "no route"}
        a = self.agents[m.group(1)]
        rest = m.group(2) or ""
        if rest == "" and method == "GET":
            return 200, self._state(a)
        if rest == "/messages" and method == "POST":
            return 200, self._step(a, body)
        if rest == "/messages" and method == "GET":
            return 200, self._page(a["messages"], q)
        if rest == "/core-memory/blocks":
            return 200, a["blocks"]
        if rest == "/archival-memory":
            return 200, self._page(a["passages"], q)
        if rest == "/archival-memory/search":
            qw = _words(q["query"])
            ranked = sorted(a["passages"], key=lambda p: (-len(qw & _words(p["text"])), p["id"]))
            ranked = [p for p in ranked if qw & _words(p["text"])][:int(q.get("top_k") or 10)]
            return 200, {"results": [{"timestamp": p["created_at"], "content": p["text"], "tags": []} for p in ranked],
                         "count": len(ranked)}
        if rest == "/context":
            ids = {x["id"] for x in a["messages"]}
            return 200, {"context_window_size_max": 32000, "num_messages": len(ids),
                         "num_archival_memory": len(a["passages"]) + self.context_extra, "num_recall_memory": len(ids)}
        return 404, {"detail": "no route"}

    def _step(self, a: dict, body: dict) -> dict:
        (msg,) = body["messages"]
        text = msg["content"]
        user_id = self._id("message")
        a["messages"].append({"id": user_id, "message_type": "user_message", "content": text, "date": "2026-01-01"})
        a["sent"] += 1
        tool = self.rogue_call or "archival_memory_insert"
        self.rogue_call = None
        step_id = self._id("message")
        pid = self._id("passage")
        a["passages"].append({"id": pid, "text": text.splitlines()[-1], "agent_id": a["id"], "created_at": "2026-01-01"})
        if a["blocks"]:
            a["blocks"][0]["value"] += "\n" + text.splitlines()[-1]
        out = [{"id": step_id, "message_type": "reasoning_message", "reasoning": "store it", "date": "2026-01-01"},
               {"id": step_id, "message_type": "tool_call_message",
                "tool_call": {"name": tool, "arguments": "{}", "tool_call_id": "c1"}, "date": "2026-01-01"},
               {"id": self._id("message"), "message_type": "tool_return_message", "tool_return": "ok",
                "status": "success", "date": "2026-01-01"},
               {"id": self._id("message"), "message_type": "assistant_message", "content": "noted",
                "date": "2026-01-01"}]
        a["messages"] += out
        if self.extra_tool_after and a["sent"] >= self.extra_tool_after[0]:
            a["tools"].append(self.extra_tool_after[1])
            self.extra_tool_after = None
        return {"messages": out, "usage": {"completion_tokens": 20, "prompt_tokens": 100, "total_tokens": 120,
                                           "step_count": 2}, "stop_reason": {"stop_reason": "end_turn"}}
