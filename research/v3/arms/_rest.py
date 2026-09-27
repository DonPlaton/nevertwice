"""PREREG-V3 TB4.7 (A6): the declared-call REST client of the server arms (Letta, supermemory-local), and the check of
those calls against the pinned server's OpenAPI document (the auditor's Q-47-2: the adapter's REST calls - method,
path, fields - are checked automatically against the document of the image pinned by digest; a mismatch is a blocker).

Copied beside base.py into the arm's directory (Q9 O-b); standard library only.

* A Call declares one operation: method, path template, the query keys and the body fields the adapter may send, and
  the response fields it reads. Body and response fields are dotted, "[]" steps into arrays
  ("llm_config.model_endpoint", "memory_blocks[].label", "messages[].message_type", "[].label"); a body field declared
  with children is checked to its leaves, a leaf takes any value.
* conformance(calls, doc) lists every problem, each naming its call: a path or method the document lacks, an
  undeclared path parameter, a query key the operation does not declare or a required one the call never sends, a body
  field outside the request schema or a required property the call never sends (at every object level it declares
  children for), a response field no 2xx schema carries.
  $ref, allOf, anyOf and oneOf are followed (a union carries a field when any variant does); a schema it cannot see
  into - additionalProperties, a $ref cycle - is a problem, never a pass.
* Rest.call refuses an undeclared call, a query key or a body field (at any declared depth) outside the declaration,
  a list where an object is declared or the reverse, a missing or extra path parameter and a GET with a body before a
  byte is sent; quotes each path value whole; never uses the environment's proxies; talks to a
  loopback http host:port only; counts attempts and errors per declared name.
* verify_server fetches the server's own document, checks its sha256 against the A8 pin, then runs conformance.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_PARAM = re.compile(r"\{([^{}]+)\}")
_OK_CODES = ("200", "201", "202", "2XX", "2xx")
_LOOPBACK = ("127.0.0.1", "localhost")


class RestError(RuntimeError):
    """A call refused before sending, an HTTP error, or a server whose document does not carry the declared calls."""


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    query: tuple = ()
    body: tuple = ()
    response: tuple = ()

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            raise ValueError(f"unknown method {self.method!r}")
        if not self.path.startswith("/"):
            raise ValueError(f"a call's path starts with '/': {self.path!r}")
        for f in ("query", "body", "response"):
            object.__setattr__(self, f, tuple(getattr(self, f)))

    @property
    def params(self) -> tuple:
        return tuple(_PARAM.findall(self.path))


# -- the document side --

class _Opaque(Exception):
    pass


def _deref(node: Any, doc: Mapping, seen: tuple = ()) -> Any:
    while isinstance(node, Mapping) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen:
            raise _Opaque(f"a $ref cycle at {ref}")
        seen = (*seen, ref)
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise _Opaque(f"a $ref outside the document: {ref!r}")
        cur: Any = doc
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(cur, Mapping) or part not in cur:
                raise _Opaque(f"a $ref to nothing: {ref}")
            cur = cur[part]
        node = cur
    return node


def _variants(schema: Any, doc: Mapping, seen: tuple = ()) -> list:
    """The object schemas a value of this schema can be, $refs followed and allOf / anyOf / oneOf flattened."""
    s = _deref(schema, doc, seen)
    if not isinstance(s, Mapping):
        return []
    out = [s]
    for key in ("allOf", "anyOf", "oneOf"):
        for sub in s.get(key) or ():
            out += _variants(sub, doc, seen)
    return out


def _required(schema: Any, doc: Mapping) -> set:
    """What an object sent for this schema must carry: its own required list, every allOf member's, and what EVERY
    object variant of an anyOf / oneOf requires (a null variant is not an object; a union binds only on common ground)."""
    s = _deref(schema, doc)
    if not isinstance(s, Mapping):
        return set()
    req = set(s.get("required") or ())
    for sub in s.get("allOf") or ():
        req |= _required(sub, doc)
    for key in ("anyOf", "oneOf"):
        objs = [v for v in (_deref(x, doc) for x in s.get(key) or ()) if isinstance(v, Mapping)
                and v.get("type") != "null"]
        if objs:
            common = _required(objs[0], doc)
            for v in objs[1:]:
                common &= _required(v, doc)
            req |= common
    return req


def _props(schema: Any, doc: Mapping) -> tuple[set, set]:
    """(property names, required names) of a request schema."""
    s = _deref(schema, doc)
    names: set = set()
    for v in _variants(s, doc):
        names |= set((v.get("properties") or {}).keys())
    return names, _required(s, doc)


def _steps(field: str) -> list:
    steps: list = []
    for seg in field.split("."):
        if seg == "[]":
            steps.append(None)
            continue
        name, arrays = seg, 0
        while name.endswith("[]"):
            name, arrays = name[:-2], arrays + 1
        if name.startswith("[]"):
            steps.append(None)
            name = name[2:]
        if name:
            steps.append(name)
        steps += [None] * arrays
    return steps


def _tree(fields) -> dict:
    """Dotted field paths as a tree: {"llm_config": {"model": {}}, "memory_blocks": {"[]": {"label": {}}}}."""
    root: dict = {}
    for f in fields:
        node = root
        for step in _steps(f):
            node = node.setdefault("[]" if step is None else step, {})
    return root


def _join(prefix: str, key: str) -> str:
    return prefix + "[]" if key == "[]" else (f"{prefix}.{key}" if prefix else key)


def _check_body(schema: Any, tree: Mapping, doc: Mapping, prefix: str, name: str, problems: list) -> None:
    for key, sub in tree.items():
        path = _join(prefix, key)
        if key == "[]":
            items = [v["items"] for v in _variants(schema, doc) if "items" in v]
            if not items:
                problems.append(f"{name}: the body field {prefix or '(the body)'!r} is not an array in the schema")
            elif sub:
                _check_body({"anyOf": items}, sub, doc, path, name, problems)
            continue
        cands = [v["properties"][key] for v in _variants(schema, doc) if key in (v.get("properties") or {})]
        if not cands:
            problems.append(f"{name}: the body key {path!r} is not in the request schema")
        elif sub:
            _check_body({"anyOf": cands}, sub, doc, path, name, problems)
    named = {k for k in tree if k != "[]"}
    if named:
        for r in sorted(_props(schema, doc)[1] - named):
            problems.append(f"{name}: the required body property {_join(prefix, r)!r} is never sent")


def _value_error(value: Any, tree: Mapping, path: str) -> str | None:
    """Why a body value is outside its declaration tree, or None."""
    if not tree:
        return None
    if "[]" in tree:
        if not isinstance(value, list):
            return f"{path or 'the body'} is declared a list"
        for i, v in enumerate(value):
            err = _value_error(v, tree["[]"], f"{path}[{i}]")
            if err:
                return err
        return None
    if not isinstance(value, Mapping):
        return f"{path or 'the body'} is declared an object"
    extra = sorted(set(value) - set(tree))
    if extra:
        return f"keys {extra} under {path or 'the body'} are outside the declaration"
    for k, v in value.items():
        err = _value_error(v, tree[k], f"{path}.{k}" if path else k)
        if err:
            return err
    return None


def _has_field(schema: Any, field: str, doc: Mapping) -> bool:
    current = [schema]
    for step in _steps(field):
        nxt = []
        for c in current:
            for v in _variants(c, doc):
                if step is None:
                    if v.get("type") == "array" or "items" in v:
                        if "items" in v:
                            nxt.append(v["items"])
                elif step in (v.get("properties") or {}):
                    nxt.append(v["properties"][step])
        if not nxt:
            return False
        current = nxt
    return True


def _json_schema(container: Any, doc: Mapping) -> Any:
    c = _deref(container, doc)
    content = (c or {}).get("content") or {}
    media = content.get("application/json")
    return None if media is None else media.get("schema")


def conformance(calls: Mapping[str, Call], doc: Mapping) -> list[str]:
    """Every way the declared calls differ from the document; empty = they are all carried as declared."""
    problems: list[str] = []
    paths = doc.get("paths") or {}
    for name, c in sorted(calls.items()):
        item = paths.get(c.path)
        if item is None:
            problems.append(f"{name}: {c.method} {c.path} - the path is not in the document")
            continue
        op = item.get(c.method.lower())
        if op is None:
            problems.append(f"{name}: {c.method} {c.path} - the document has no {c.method} on this path")
            continue
        try:
            params = [_deref(p, doc) for p in (item.get("parameters") or [])] + \
                     [_deref(p, doc) for p in (op.get("parameters") or [])]
            declared = {(p.get("in"), p.get("name")): p for p in params if isinstance(p, Mapping)}
            for pp in c.params:
                if ("path", pp) not in declared:
                    problems.append(f"{name}: the path parameter {pp!r} is not declared in: path")
            for q in c.query:
                if ("query", q) not in declared:
                    problems.append(f"{name}: the query key {q!r} is not a declared query parameter")
            for (where, pname), p in sorted(declared.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
                if where == "query" and p.get("required") and pname not in c.query:
                    problems.append(f"{name}: the required query parameter {pname!r} is never sent")
            if c.body:
                schema = _json_schema(op.get("requestBody"), doc) if op.get("requestBody") else None
                if schema is None:
                    problems.append(f"{name}: the operation takes no JSON request body, and the call sends {c.body}")
                else:
                    _check_body(schema, _tree(c.body), doc, "", name, problems)
            elif op.get("requestBody") and (_deref(op["requestBody"], doc) or {}).get("required"):
                problems.append(f"{name}: the operation requires a request body, and the call declares none")
            if c.response:
                responses = op.get("responses") or {}
                schema = next((s for s in (_json_schema(responses[code], doc) for code in _OK_CODES
                                           if code in responses) if s is not None), None)
                if schema is None:
                    problems.append(f"{name}: no 2xx JSON response schema, so the fields {c.response} cannot be checked")
                else:
                    for f in c.response:
                        if not _has_field(schema, f, doc):
                            problems.append(f"{name}: the response field {f!r} is not in the 2xx schema")
        except _Opaque as e:
            problems.append(f"{name}: {e}")
    return problems


def document_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# -- the client side --

class Rest:
    def __init__(self, base_url: str, calls: Mapping[str, Call], *, timeout: float = 600.0) -> None:
        u = urllib.parse.urlsplit(base_url)
        if (u.scheme != "http" or u.hostname not in _LOOPBACK or u.port is None or u.path not in ("", "/")
                or u.query or u.fragment or u.username or u.password):
            raise ValueError(f"a server arm talks to a loopback http host:port only, not {base_url!r}")
        self.base = f"http://{u.hostname}:{u.port}"
        self.calls = dict(calls)
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never the env's proxies
        self.counts: dict[str, int] = {}
        self.errors: dict[str, int] = {}
        self.raw: dict[str, int] = {}

    def _send(self, key: str, counts: dict, url: str, method: str, data: bytes | None, timeout: float | None) -> bytes:
        counts[key] = counts.get(key, 0) + 1
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"accept": "application/json",
                                              **({"content-type": "application/json"} if data is not None else {})})
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            self.errors[key] = self.errors.get(key, 0) + 1
            detail = e.read()[:300].decode("utf-8", "replace")
            raise RestError(f"{key}: HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, OSError) as e:
            self.errors[key] = self.errors.get(key, 0) + 1
            raise RestError(f"{key}: {type(e).__name__}: {e}") from None

    def call(self, name: str, *, path: Mapping[str, Any] | None = None, query: Mapping[str, Any] | None = None,
             body: Mapping[str, Any] | None = None, timeout: float | None = None) -> Any:
        c = self.calls.get(name)
        if c is None:
            raise RestError(f"{name}: not a declared call")
        given = set(path or {})
        if given != set(c.params):
            raise RestError(f"{name}: path parameters {sorted(given)} are not exactly {list(c.params)}")
        extra_q = sorted(set(query or {}) - set(c.query))
        if extra_q:
            raise RestError(f"{name}: query keys {extra_q} are outside the declaration {list(c.query)}")
        if body is not None:
            if c.method == "GET":
                raise RestError(f"{name}: a GET sends no body")
            err = _value_error(body, _tree(c.body) or {"": {}}, "")
            if err:
                raise RestError(f"{name}: {err} ({list(c.body)})")
        url = self.base + _PARAM.sub(lambda m: urllib.parse.quote(str((path or {})[m.group(1)]), safe=""), c.path)
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        raw = self._send(name, self.counts, url, c.method, data, timeout)
        return json.loads(raw) if raw.strip() else None

    def raw_get(self, path: str) -> bytes:
        if not path.startswith("/") or "?" in path:
            raise RestError(f"raw_get takes a plain absolute path, not {path!r}")
        return self._send(f"raw:{path}", self.raw, self.base + path, "GET", None, None)

    def snapshot(self) -> dict:
        return {"calls": dict(self.counts), "errors": dict(self.errors), "raw": dict(self.raw)}


def verify_server(rest: Rest, pinned_sha256: str, calls: Mapping[str, Call] | None = None,
                  path: str = "/openapi.json") -> dict:
    """The server's own OpenAPI document: its sha256 must be the A8 pin, and it must carry every declared call."""
    if not re.fullmatch(r"[0-9a-f]{64}", pinned_sha256 or ""):
        raise RestError("the server's OpenAPI document has no sha256 pin (A8)")
    raw = rest.raw_get(path)
    got = document_sha256(raw)
    if got != pinned_sha256:
        raise RestError(f"the server's OpenAPI document is {got}, not the pinned {pinned_sha256}")
    problems = conformance(calls if calls is not None else rest.calls, json.loads(raw))
    if problems:
        raise RestError(f"the declared REST calls do not match the pinned OpenAPI ({len(problems)}): "
                        + "; ".join(problems))
    return {"openapi_sha256": got, "calls_checked": len(calls if calls is not None else rest.calls)}
