#!/usr/bin/env python3
"""PREREG-V3 TB4.7 (A6): research/v3/arms/_rest.py - the declared-call REST client of the server arms (Letta,
supermemory-local) and its conformance check against the pinned server's OpenAPI document (the auditor's Q-47-2: the
adapter's calls - method, path, fields - are checked automatically; a mismatch is a blocker).

* conformance(calls, doc): every call's path is in the document with its method; its path parameters are declared
  "in: path"; each query key it may send is a declared query parameter and every REQUIRED query parameter is among
  them; each body key is a property of the request schema and every required property is among them; every response
  field it reads exists in the 2xx schema - through $ref, allOf, anyOf / oneOf (any variant) and arrays ("[]"); a
  schema it cannot see into is a problem, not a pass. Each problem names the call.
* the document here is written by hand, independently of any adapter's table, so a clean check is not a tautology;
* Rest.call: an undeclared call, a query or body key outside the call's declaration, a missing or extra path
  parameter and a GET with a body are refused BEFORE a byte is sent; path values are quoted; an HTTP error raises
  RestError naming the call and the status and is counted; the environment's proxies are never used; the base URL is
  loopback only.

    python tests/research/_test_v3_rest.py
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

REST_PATH = ROOT / "research" / "v3" / "arms" / "_rest.py"
_spec = importlib.util.spec_from_file_location("v3_rest", REST_PATH)
R = importlib.util.module_from_spec(_spec)
sys.modules["v3_rest"] = R
_spec.loader.exec_module(R)
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def raises(fn, exc) -> str | None:
    """The message of the expected exception, or None when fn did not raise it."""
    try:
        fn()
    except exc as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return None if exc is not Exception else str(e)
    return None


# -- a hand-written document: pets and their notes (never derived from a table below) --
DOC = {
    "openapi": "3.1.0",
    "paths": {
        "/v1/pets/": {
            "post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/PetCreate"}}}},
                     "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}}}}},
            "get": {"parameters": [{"name": "limit", "in": "query"}, {"name": "after", "in": "query"}],
                    "responses": {"200": {"content": {"application/json": {"schema": {
                        "type": "array", "items": {"$ref": "#/components/schemas/Pet"}}}}}}},
        },
        "/v1/pets/{pet_id}/notes": {
            "parameters": [{"$ref": "#/components/parameters/PetId"}],
            "post": {"requestBody": {"content": {"application/json": {"schema": {
                         "type": "object", "required": ["notes"],
                         "properties": {"notes": {"type": "array", "items": {"oneOf": [
                             {"type": "object", "required": ["kind", "text"],
                              "properties": {"kind": {"type": "string"}, "text": {"type": "string"}}},
                             {"type": "object", "required": ["kind"],
                              "properties": {"kind": {"type": "string"}, "tool": {"type": "string"}}}]}},
                                        "stream": {"type": "boolean"}}}}}},
                     "responses": {"201": {"content": {"application/json": {"schema": {
                         "$ref": "#/components/schemas/NoteResponse"}}}}}},
            "get": {"parameters": [{"name": "q", "in": "query", "required": True},
                                   {"name": "top_k", "in": "query"}],
                    "responses": {"200": {"content": {"application/json": {"schema": {
                        "type": "object", "properties": {"results": {"type": "array", "items": {
                            "type": "object", "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}},
                            "count": {"type": "integer"}}}}}}}},
        },
        "/v1/pets/{pet_id}/free": {
            "parameters": [{"name": "pet_id", "in": "path", "required": True}],
            "get": {"responses": {"200": {"content": {"application/json": {"schema": {
                "type": "object", "additionalProperties": True}}}}}},
        },
    },
    "components": {
        "parameters": {"PetId": {"name": "pet_id", "in": "path", "required": True}},
        "schemas": {
            "PetCreate": {"allOf": [{"$ref": "#/components/schemas/PetBase"},
                                    {"type": "object", "properties": {
                                        "tags": {"type": "array"},
                                        "collar": {"anyOf": [{"$ref": "#/components/schemas/Collar"},
                                                             {"type": "null"}]},
                                        "toys": {"type": "array", "items": {"$ref": "#/components/schemas/Toy"}}}}]},
            "Collar": {"type": "object", "required": ["size"],
                       "properties": {"size": {"type": "integer"}, "colour": {"type": "string"}}},
            "Toy": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"},
                                                                           "squeaks": {"type": "boolean"}}},
            "PetBase": {"type": "object", "required": ["name"],
                        "properties": {"name": {"type": "string"}, "kind": {"type": "string"}}},
            "Pet": {"allOf": [{"$ref": "#/components/schemas/PetBase"},
                              {"type": "object", "properties": {"id": {"type": "string"},
                                                                "owner": {"$ref": "#/components/schemas/Owner"}}}]},
            "Owner": {"type": "object", "properties": {"email": {"type": "string"}}},
            "NoteResponse": {"type": "object", "properties": {
                "notes": {"type": "array", "items": {"anyOf": [{"$ref": "#/components/schemas/TextNote"},
                                                               {"$ref": "#/components/schemas/ToolNote"}]}},
                "usage": {"type": "object", "properties": {"prompt_tokens": {"type": "integer"}}}}},
            "TextNote": {"type": "object", "properties": {"kind": {"type": "string"}, "text": {"type": "string"}}},
            "ToolNote": {"type": "object", "properties": {"kind": {"type": "string"}, "tool": {"type": "string"}}},
        },
    },
}

C = R.Call
STR_SCHEMA = {"type": "string"}
CALLS = {
    "create": C("POST", "/v1/pets/", body=("name", "kind", "tags", "collar.size", "collar.colour", "toys[].name",
                                           "toys[].squeaks"), response=("id", "name", "owner.email")),
    "list": C("GET", "/v1/pets/", query=("limit", "after"), response=("[].id", "[].name")),
    "add_notes": C("POST", "/v1/pets/{pet_id}/notes", body=("notes",),
                   response=("notes[].kind", "notes[].text", "notes[].tool", "usage.prompt_tokens")),
    "search": C("GET", "/v1/pets/{pet_id}/notes", query=("q", "top_k"), response=("results[].id", "results[].text",
                                                                                     "count")),
}

print("- conformance against a hand-written document -")
check("a table that matches the document is clean ($ref, allOf, anyOf, arrays, path-level and $ref parameters)",
      R.conformance(CALLS, DOC) == [], str(R.conformance(CALLS, DOC)))


def probs(**changes) -> list[str]:
    calls = dict(CALLS)
    calls.update(changes)
    return R.conformance(calls, DOC)


def named(ps: list[str], *needles: str) -> bool:
    return len(ps) >= 1 and all(any(n in p for p in ps) for n in needles)


cases = [
    ("a path the document does not have", {"x": C("GET", "/v1/pets")}, ("x", "/v1/pets", "not in the document")),
    ("a method the path does not have", {"create": C("PUT", "/v1/pets/", body=("name",))}, ("create", "PUT")),
    ("a path parameter under another name",
     {"add_notes": C("POST", "/v1/pets/{id}/notes", body=("notes",))}, ("add_notes",)),
    ("an undeclared query key", {"list": C("GET", "/v1/pets/", query=("limit", "offset"))}, ("list", "offset")),
    ("a required query parameter the call never sends",
     {"search": C("GET", "/v1/pets/{pet_id}/notes", query=("top_k",))}, ("search", "q", "required")),
    ("a body key outside the schema", {"create": C("POST", "/v1/pets/", body=("name", "colour"))}, ("create", "colour")),
    ("a required body field the call never sends",
     {"create": C("POST", "/v1/pets/", body=("kind",))}, ("create", "name", "required")),
    ("a body on an operation with no request body", {"list": C("GET", "/v1/pets/", body=("limit",))}, ("list",)),
    ("a nested body key outside its object's schema",
     {"create": C("POST", "/v1/pets/", body=("name", "collar.size", "collar.shape"))}, ("create", "collar.shape")),
    ("a nested required property the call never sends",
     {"create": C("POST", "/v1/pets/", body=("name", "collar.colour"))}, ("create", "collar.size", "required")),
    ("a required property of an array's items the call never sends",
     {"create": C("POST", "/v1/pets/", body=("name", "toys[].squeaks"))}, ("create", "toys[].name", "required")),
    ("an array step into a body field that is not an array",
     {"create": C("POST", "/v1/pets/", body=("name", "collar[].size"))}, ("create", "collar", "not an array")),
    ("a field under an array's items that the items lack",
     {"create": C("POST", "/v1/pets/", body=("name", "toys[].name", "toys[].colour"))}, ("create", "toys[].colour")),
    ("a response field the schema lacks", {"create": C("POST", "/v1/pets/", body=("name",), response=("colour",))},
     ("create", "colour")),
    ("a nested response field the schema lacks",
     {"create": C("POST", "/v1/pets/", body=("name",), response=("owner.phone",))}, ("create", "owner.phone")),
    ("an array step on a field that is not an array",
     {"create": C("POST", "/v1/pets/", body=("name",), response=("owner[].email",))}, ("create", "owner[].email")),
    ("a field no union variant carries",
     {"add_notes": C("POST", "/v1/pets/{pet_id}/notes", body=("notes",), response=("notes[].colour",))},
     ("add_notes", "notes[].colour")),
    ("a response field behind additionalProperties (unverifiable is not a pass)",
     {"free": C("GET", "/v1/pets/{pet_id}/free", response=("anything",))}, ("free", "anything")),
]
for label, change, needles in cases:
    ps = probs(**change)
    check(f"refused by name: {label}", named(ps, *needles), str(ps))

ps = probs(add_notes=C("POST", "/v1/pets/{pet_id}/notes", body=("notes[].text",)))
check("a property EVERY object variant of a union requires is required",
      named(ps, "add_notes", "notes[].kind", "required"), str(ps))
ps = probs(add_notes=C("POST", "/v1/pets/{pet_id}/notes", body=("notes[].kind",)))
check("a property only one variant requires is not binding on the call", ps == [], str(ps))
d2 = copy.deepcopy(DOC)
d2["components"]["schemas"]["PetBase"]["properties"].pop("kind")
check("a field removed from a $ref-ed base schema is caught through allOf",
      named(R.conformance(CALLS, d2), "create", "kind"), str(R.conformance(CALLS, d2)))
d3 = copy.deepcopy(DOC)
d3["paths"]["/v1/pets/{pet_id}/notes"]["parameters"] = []
check("a path parameter the document does not declare is refused",
      named(R.conformance(CALLS, d3), "pet_id"), str(R.conformance(CALLS, d3)))
d4 = copy.deepcopy(DOC)
d4["components"]["schemas"]["Owner"] = {"$ref": "#/components/schemas/Owner"}
check("a $ref cycle is a named problem, not a hang", named(R.conformance(CALLS, d4), "create"),
      str(R.conformance(CALLS, d4)))
d5 = copy.deepcopy(DOC)
d5["paths"]["/v1/pets/"]["post"]["responses"] = {"422": {}}
check("an operation without a 2xx JSON response cannot have its fields checked",
      named(R.conformance(CALLS, d5), "create", "no 2xx JSON response schema"), str(R.conformance(CALLS, d5)))
for label, responses in (("only a 204", {"204": {"description": "no content"}}),
                         ("a 200 in text/plain only", {"200": {"content": {"text/plain": {"schema": STR_SCHEMA}}}})):
    d6 = copy.deepcopy(DOC)
    d6["paths"]["/v1/pets/"]["post"]["responses"] = responses
    ps = R.conformance(CALLS, d6)
    check(f"R8 a call that reads response fields from an operation with {label} is a named problem",
          named(ps, "create", "no 2xx JSON response schema") and not any("is not in the 2xx schema" in p for p in ps),
          str(ps))
d7 = copy.deepcopy(DOC)
d7["paths"]["/v1/pets/"]["post"]["responses"] = {"204": {"description": "no content"}}
check("a call that reads no response fields needs no 2xx JSON schema",
      R.conformance({"create": C("POST", "/v1/pets/", body=("name",))}, d7) == [],
      str(R.conformance({"create": C("POST", "/v1/pets/", body=("name",))}, d7)))
d8 = copy.deepcopy(DOC)
d8["paths"]["/v1/pets/{pet_id}/notes"]["post"]["requestBody"]["required"] = True
ps = R.conformance(dict(CALLS, add_notes=C("POST", "/v1/pets/{pet_id}/notes")), d8)
check("R5 an operation whose request body is required, called without a body declaration, is a named problem",
      named(ps, "add_notes", "requires a request body"), str(ps))
ps = R.conformance(dict(CALLS, add_notes=C("POST", "/v1/pets/{pet_id}/notes")), DOC)
check("an optional request body may be left out", not any("add_notes" in p for p in ps), str(ps))
check("a call declared with an unknown method is refused at construction",
      raises(lambda: C("FETCH", "/v1/pets/"), ValueError) is not None)
check("a path without a leading slash is refused at construction",
      raises(lambda: C("GET", "v1/pets/"), ValueError) is not None)


print("\n- Rest.call: refusals before a byte, quoting, errors, proxies -")


class Fake:
    """A loopback server recording every request; /v1/pets/boom answers 500, everything else echoes."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, bytes]] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _answer(self) -> None:
                n = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(n) if n else b""
                fake.seen.append((self.command, self.path, body))
                if self.path.startswith("/v1/pets/boom"):
                    out, code = b'{"detail": "exploded"}', 500
                elif self.path == "/openapi.json":
                    out, code = json.dumps(DOC).encode(), 200
                else:
                    out, code = json.dumps({"method": self.command, "path": self.path,
                                            "body": json.loads(body) if body else None}).encode(), 200
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = do_DELETE = do_PATCH = _answer

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


FAKE = Fake()
try:
    calls = dict(CALLS, boom=C("GET", "/v1/pets/boom"), notes_of=C("GET", "/v1/pets/{pet_id}/notes",
                                                                    query=("q", "top_k")))
    rest = R.Rest(f"http://127.0.0.1:{FAKE.port}", calls)
    n0 = len(FAKE.seen)
    for label, fn in (("an undeclared call", lambda: rest.call("delete_all")),
                      ("a query key outside the declaration", lambda: rest.call("list", query={"offset": 1})),
                      ("a body key outside the declaration", lambda: rest.call("create", body={"colour": "red"})),
                      ("a missing path parameter", lambda: rest.call("add_notes", body={"notes": []})),
                      ("an extra path parameter", lambda: rest.call("list", path={"pet_id": "p"})),
                      ("a GET with a body, even an empty one", lambda: rest.call("list", body={}))):
        msg = raises(fn, R.RestError)
        check(f"refused before any request: {label}", msg is not None and len(FAKE.seen) == n0, str(msg))
    got = rest.call("add_notes", path={"pet_id": "a/b c"}, body={"notes": [{"kind": "text"}]})
    check("a path value is quoted whole (a slash cannot open another route)",
          FAKE.seen[-1][1] == "/v1/pets/a%2Fb%20c/notes" and got["body"] == {"notes": [{"kind": "text"}]},
          str(FAKE.seen[-1]))
    got = rest.call("list", query={"limit": 5, "after": "x y"})
    check("query values are encoded and sent", FAKE.seen[-1][1] == "/v1/pets/?limit=5&after=x+y", FAKE.seen[-1][1])
    check("the body goes as JSON with its declared keys only",
          rest.call("create", body={"name": "n", "kind": "k"})["body"] == {"name": "n", "kind": "k"})
    nested = {"name": "n", "collar": {"size": 3}, "toys": [{"name": "ball", "squeaks": True}, {"name": "rope"}]}
    check("a body with declared nested objects and arrays goes through whole",
          rest.call("create", body=nested)["body"] == nested)
    n1 = len(FAKE.seen)
    for label, bad in (("an undeclared key inside a declared object", {"name": "n", "collar": {"shape": "round"}}),
                       ("an undeclared key inside an array's items", {"name": "n", "toys": [{"name": "b", "x": 1}]}),
                       ("an object where a list is declared", {"name": "n", "toys": {"name": "b"}}),
                       ("a list where an object is declared", {"name": "n", "collar": [{"size": 1}]}),
                       ("a POST body key where the call declares no body", None)):
        fn = (lambda b=bad: rest.call("create", body=b)) if bad is not None else              (lambda: rest.call("add_notes", path={"pet_id": "p"}, body={"notes": [], "stream": True}))
        msg = raises(fn, R.RestError)
        check(f"refused before any request: {label}", msg is not None and len(FAKE.seen) == n1, str(msg))
    msg = raises(lambda: rest.call("boom"), R.RestError)
    check("an HTTP error names the call and the status, with the server's detail",
          msg is not None and "boom" in msg and "500" in msg and "exploded" in msg, str(msg))
    snap = rest.snapshot()
    check("calls and errors are counted per declared name (refusals are not calls)",
          snap["calls"] == {"add_notes": 1, "list": 1, "create": 2, "boom": 1} and snap["errors"] == {"boom": 1},
          json.dumps(snap))
    raw = rest.raw_get("/openapi.json")
    canon = hashlib.sha256(json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False).encode("utf-8")).hexdigest()
    pretty = json.dumps(json.loads(raw), indent=3).encode("utf-8")
    try:
        doc_shas = (R.document_sha256(raw), R.document_sha256(pretty))
        not_json = raises(lambda: R.document_sha256(b"<html>not the document</html>"), R.RestError)
    except Exception as e:  # noqa: BLE001 - the row below FAILs by name
        doc_shas, not_json = (repr(e), None), None
    check("raw_get returns the document's bytes; document_sha256 is the sha256 over canonical JSON (R-C5-1: sort_keys, "
          "no whitespace) - key order and layout do not move it, and a body that is not JSON is refused by name",
          json.loads(raw) == DOC and doc_shas == (canon, canon) and canon != hashlib.sha256(raw).hexdigest()
          and not_json is not None and "not JSON" in not_json, str((doc_shas, not_json)))
    pin = canon
    ok = R.verify_server(rest, pin, CALLS)
    check("verify_server passes the pinned document that carries every declared call",
          ok == {"openapi_sha256": pin, "calls_checked": len(CALLS)}, str(ok))
    msg = raises(lambda: R.verify_server(rest, "0" * 64, CALLS), R.RestError)
    check("verify_server refuses a document that is not the pinned one, naming both digests",
          msg is not None and pin in msg and "0" * 64 in msg, str(msg))
    check("verify_server refuses to run without a pin",
          all(raises(lambda p=p: R.verify_server(rest, p, CALLS), R.RestError) is not None for p in ("", None, "abc")))
    msg = raises(lambda: R.verify_server(rest, pin, calls), R.RestError)
    check("verify_server refuses a pinned document that lacks a declared call, naming it",
          msg is not None and "boom" in msg and "notes_of" not in msg, str(msg))
    saved = {k: os.environ.get(k) for k in ("HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")}
    try:
        os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = "http://127.0.0.1:9"
        os.environ.pop("NO_PROXY", None)
        os.environ.pop("no_proxy", None)
        fresh = R.Rest(f"http://127.0.0.1:{FAKE.port}", calls)        # built AFTER the dead proxy is in the env
        try:
            via = fresh.call("list", query={"limit": 1})
        except R.RestError as e:
            via = {"path": f"refused: {e}"}
        check("the environment's proxy is never used (a dead proxy in HTTP_PROXY changes nothing)",
              via["path"] == "/v1/pets/?limit=1", via["path"])
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    for base in ("http://10.0.0.5:8283", "https://127.0.0.1:8283", "http://example.com", "http://127.0.0.1:8283/v1"):
        check(f"a base URL that is not plain loopback http host:port is refused: {base}",
              raises(lambda b=base: R.Rest(b, calls), ValueError) is not None)
    check("localhost and 127.0.0.1 are accepted",
          all(raises(lambda b=b: R.Rest(b, calls), Exception) is None
              for b in ("http://localhost:8283", "http://127.0.0.1:8283")))
    tree = ast.parse(REST_PATH.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    check("_rest imports the standard library only",
          mods <= {"__future__", "dataclasses", "hashlib", "json", "re", "typing", "urllib"}, str(sorted(mods)))
finally:
    FAKE.close()

print(f"\nv3 rest: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
