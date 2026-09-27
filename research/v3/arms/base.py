#!/usr/bin/env python3
"""PREREG-V3 TB4.5a (A6): the harness <-> arm-child protocol - JSON lines over stdin/stdout, one child per (arm, run,
unit) and stage (ruling Q25: a unit process exits after the write stage; the question stage opens the same on-disk store
in a new process; an arm whose store lives in memory keeps its process and says so in arm_decl).

This module is self-contained (stdlib only) on purpose: by ruling Q9 O-b it is COPIED byte-identical, with its sha256
recorded, into each competitor arm's runs-tree directory at block start, so no competitor or judge child ever has the
repository on its argv or path.

Requests (one JSON object per LF line):  {"id": <int>, "op": "hello" | "write" | "end_write" | "read" | "counters" |
                                          "bye", ...}
Responses (one per request):             {"id": <the same int>, "ok": true, "op": ..., ...}
                                         or {"id": ..., "ok": false, "error": "<Type>: <message>"}

* the protocol takes the child's stdio FIRST (claim_stdio), before the product is imported or the handler is built:
  private duplicates of the original stdin and stdout carry the stream, then fd 0 reads the null device and fd 1 and
  sys.stdout point at stderr, so a product that prints - at import or later - cannot corrupt it (the auditor's F2), and
  a subprocess it starts can neither eat a request line nor write into an answer (his advice at the aea444b gate);
* stdin is read as bytes and each line decoded as strict UTF-8, whatever the locale; a response is encoded as strict
  UTF-8 - an undecodable request or an unencodable answer (a lone surrogate) is an ok:false line, never a dead child or
  mojibake handed to the product (F3);
* the child never dies on a bad request: malformed JSON, an unknown op, a handler exception, a non-dict result, or a
  result that would overwrite ok/op/id (F4) is an ok:false line (its text capped at ERROR_MAX), and the child goes on;
  "bye" ends it;
* the store between the stages (Q25, the auditor's Q-45-5 condition): end_write seals it - the store's absolute path and
  a digest of its tree (sorted relative paths and bytes) in store.seal.json beside it - and the read stage checks the
  seal before the product opens the store; another path or another byte is SealError by name;
* the harness side (ArmClient) numbers every request and accepts only the answer with that number; a timeout, a line
  that is not a JSON object, or a wrong number POISONS the client - every later request raises ArmPoisoned until the
  harness kills the child - so a late answer can never be taken for the next question's (F1).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import sys
import threading
from typing import Any, Callable, Mapping

PROTOCOL = "nvt3-arm-2"
OPS = ("hello", "write", "end_write", "read", "counters", "bye")
RESERVED = ("id", "ok", "op")
ERROR_MAX = 500


class ArmError(RuntimeError):
    """The child answered ok:false, or the stream broke the protocol."""


class ArmPoisoned(ArmError):
    """A request after a timeout or a protocol violation: the stream can no longer be trusted."""


class ArmTimeout(ArmError):
    """No answer line within the timeout."""


class ArmDied(ArmError):
    """The child's stdout closed before it answered."""


def _encode(obj: Mapping) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8", "strict")


def _error(rid, e: BaseException) -> bytes:
    msg = f"{type(e).__name__}: {e}"[:ERROR_MAX]
    return (json.dumps({"id": rid, "ok": False, "error": msg}, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("ascii")


def claim_stdio():
    """Take the protocol's streams before anything else runs; returns (binary protocol in, binary protocol out).
    Both are private duplicates (not inheritable); fd 0 then reads the null device and fd 1 writes to stderr, so neither
    the product nor a subprocess it starts (which inherits fds 0-2) can eat a request line or write into the stream."""
    sys.stdout.flush()
    fin = os.fdopen(os.dup(0), "rb")
    out = os.fdopen(os.dup(1), "wb", buffering=0)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)                           # a subprocess that reads stdin gets end-of-file, never a request
    os.close(null)
    os.dup2(2, 1)                              # a product's stray print, even at import, lands on stderr
    sys.stdout = sys.stderr
    return fin, out


def serve(handler: Any, fin, fout) -> int:
    """Answer requests from the binary stream ``fin`` on the binary stream ``fout`` until "bye" or end of input.
    ``handler`` has one method per op (hello, write, end_write, read, counters); each takes the request's other fields
    as keyword arguments and returns a dict. Returns 0."""
    for raw in iter(fin.readline, b""):
        if not raw.strip():
            continue
        rid = None
        try:
            try:
                text = raw.decode("utf-8", "strict")
            except UnicodeDecodeError as e:
                raise ValueError(f"the request is not UTF-8 ({e.reason} at byte {e.start})") from None
            req = json.loads(text)
            if not isinstance(req, dict):
                raise ValueError("a request is a JSON object")
            rid = req.pop("id", None)
            op = req.pop("op", None)
            if op not in OPS:
                raise ValueError(f"unknown op {op!r}")
            if op == "bye":
                fout.write(_encode({"id": rid, "ok": True, "op": "bye"}))
                fout.flush()
                return 0
            out = getattr(handler, op)(**req)
            if not isinstance(out, dict):
                raise TypeError(f"{op} returned {type(out).__name__}, not a dict")
            clash = [k for k in RESERVED if k in out]
            if clash:
                raise ValueError(f"{op} returned reserved keys {clash}")
            line = _encode({"id": rid, "ok": True, "op": op, **out})
        except Exception as e:  # noqa: BLE001 - the child never dies on one request
            line = _error(rid, e)
        fout.write(line)
        fout.flush()
    return 0


def main_with(handler_factory: Callable[[], Any]) -> int:
    """An arm child's entry point: claim the stdio FIRST, then build the handler (its imports and environment checks),
    then serve."""
    fin, fout = claim_stdio()
    return serve(handler_factory(), fin, fout)


# ── the bytes each arm was given (Q-45-4 and the auditor's retrieval-tier rule) ─────────────────────────────────────

def text_sha256(text: str) -> str:
    """The sha256 of the exact text an arm was handed (UTF-8): a session's text for a text-API arm, an item's bytes for a
    retrieval arm. Every arm writes it, and the harness checks that the arms of one unit were given equal bytes."""
    return hashlib.sha256(text.encode("utf-8", "strict")).hexdigest()


def items_digest(item_shas: Mapping[int, str]) -> str:
    """One digest per unit over the (index, item sha256) pairs in index order - equal across the retrieval arms exactly
    when they stored the same bytes under the same indices."""
    pairs = [[int(i), str(s)] for i, s in sorted(item_shas.items())]
    return hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode("ascii")).hexdigest()


# ── the store between the stages (Q25, with the auditor's Q-45-5 condition) ──────────────────────────────────────────

SEAL_NAME = "store.seal.json"


class SealError(RuntimeError):
    """The read stage's store is not - path for path and byte for byte - the one the write stage sealed."""


def tree_manifest(root) -> tuple[str, dict]:
    """The store's tree: (sha256 over every file's relative path (POSIX form) and bytes, in sorted path order, each
    length-prefixed; {relative path: sha256 of its bytes}) - the manifest names what changed, never its content."""
    base = os.path.abspath(os.fspath(root))
    rels = []
    for d, _dirs, files in os.walk(base):
        rels += [os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/") for f in files]
    h = hashlib.sha256()
    manifest = {}
    for rel in sorted(rels):
        with open(os.path.join(base, rel), "rb") as fh:
            data = fh.read()
        name = rel.encode("utf-8")
        h.update(len(name).to_bytes(8, "big") + name + len(data).to_bytes(8, "big") + data)
        manifest[rel] = hashlib.sha256(data).hexdigest()
    return h.hexdigest(), manifest


def tree_digest(root) -> tuple[str, int]:
    """(the tree's digest, its file count)."""
    digest, manifest = tree_manifest(root)
    return digest, len(manifest)


def _same_path(a, b) -> bool:
    """B-SEAL83: canonical paths (realpath) - a runner's 8.3 name and the long name are one path."""
    return os.path.normcase(os.path.realpath(os.fspath(a))) == os.path.normcase(os.path.realpath(os.fspath(b)))


def write_seal(unit_dir, store, **meta) -> dict:
    """The end of the write stage: the store's canonical path (realpath - B-SEAL83: abspath kept a runner's 8.3 name,
    so records of the same store differed by how the caller wrote it) and digest, written beside the store (never
    inside it)."""
    digest, manifest = tree_manifest(store)
    seal = {**meta, "store": os.path.realpath(os.fspath(store)), "sha256": digest, "files": len(manifest),
            "manifest": manifest}
    path = os.path.join(os.fspath(unit_dir), SEAL_NAME)
    with open(path + ".tmp", "wb") as fh:
        fh.write(_encode(seal))
    os.replace(path + ".tmp", path)
    return seal


def check_seal(unit_dir, store) -> dict:
    """The start of the read stage, before the product opens the store: the sealed path, the sealed bytes."""
    try:
        with open(os.path.join(os.fspath(unit_dir), SEAL_NAME), "rb") as fh:
            seal = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise SealError(f"the write stage's seal is missing or unreadable ({type(e).__name__})") from None
    if not _same_path(seal.get("store", ""), store):
        raise SealError("the read stage's store path is not the one the write stage sealed")
    digest, manifest = tree_manifest(store)
    if digest != seal.get("sha256"):
        was = seal.get("manifest") or {}
        changed = sorted(k for k in set(was) | set(manifest) if was.get(k) != manifest.get(k))
        raise SealError(f"the store changed between the stages: {len(changed)} path(s) differ, first "
                        f"{changed[:5]}"[:ERROR_MAX])
    return seal


def write_state_seal(unit_dir, store_uri: str, digest: str, **meta) -> dict:
    """The end of the write stage for a store that is not a directory - a graph in a server (the auditor's Q-46b-1): its
    URI and a digest of its state, computed by the adapter from the product's own reads, written beside the unit."""
    seal = {**meta, "store": store_uri, "sha256": digest, "kind": "state"}
    path = os.path.join(os.fspath(unit_dir), SEAL_NAME)
    with open(path + ".tmp", "wb") as fh:
        fh.write(_encode(seal))
    os.replace(path + ".tmp", path)
    return seal


def check_state_seal(unit_dir, store_uri: str, digest: str) -> dict:
    """The start of the read stage for such a store: the sealed URI, the sealed state."""
    try:
        with open(os.path.join(os.fspath(unit_dir), SEAL_NAME), "rb") as fh:
            seal = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise SealError(f"the write stage's seal is missing or unreadable ({type(e).__name__})") from None
    if seal.get("kind") != "state" or seal.get("store") != store_uri:
        raise SealError("the read stage's store is not the one the write stage sealed")
    if digest != seal.get("sha256"):
        raise SealError("the store changed between the stages (its state digest differs)")
    return seal


class ArmClient:
    """The harness side of one child process (stdin/stdout pipes in binary mode)."""

    def __init__(self, process, *, default_timeout: float = 600.0) -> None:
        self.p = process
        self.timeout = default_timeout
        self._next = 0
        self.poisoned: str | None = None
        self._q: "queue.Queue[bytes | None]" = queue.Queue()
        self._t = threading.Thread(target=self._pump, daemon=True)
        self._t.start()

    def _pump(self) -> None:
        for raw in iter(self.p.stdout.readline, b""):
            self._q.put(raw)
        self._q.put(None)

    def _poison(self, exc: ArmError) -> ArmError:
        self.poisoned = str(exc)
        return exc

    def request(self, op: str, *, timeout: float | None = None, **fields) -> dict:
        if self.poisoned:
            raise ArmPoisoned(f"{op}: the client is poisoned ({self.poisoned}) - kill the child")
        if op not in OPS:
            raise ValueError(f"unknown op {op!r}")
        clash = [k for k in RESERVED if k in fields]
        if clash:
            raise ValueError(f"reserved request fields {clash}")
        self._next += 1
        rid = self._next
        try:
            self.p.stdin.write(_encode({"id": rid, "op": op, **fields}))
            self.p.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise self._poison(ArmDied(f"{op}: the child's stdin is closed ({type(e).__name__})")) from None
        wait = self.timeout if timeout is None else timeout
        try:
            raw = self._q.get(timeout=wait)
        except queue.Empty:
            raise self._poison(ArmTimeout(f"{op}: no answer within {wait} s")) from None
        if raw is None:
            raise self._poison(ArmDied(f"{op}: the child's stdout closed (exit {self.p.poll()})"))
        try:
            resp = json.loads(raw.decode("utf-8", "strict"))
        except (UnicodeDecodeError, ValueError):
            raise self._poison(ArmError(f"protocol: {op} got a line that is not JSON: {raw[:80]!r}")) from None
        if not isinstance(resp, dict):
            raise self._poison(ArmError(f"protocol: {op} got a {type(resp).__name__}, not an object"))
        if resp.get("id") != rid:
            raise self._poison(ArmError(f"protocol: {op} (request {rid}) got the answer to request {resp.get('id')}"))
        if not resp.get("ok"):
            raise ArmError(f"{op}: {resp.get('error')}")
        return resp

    def kill(self) -> None:
        try:
            self.p.kill()
        except OSError:
            pass

    def close(self, *, timeout: float = 30.0) -> int:
        """Say bye (unless poisoned), then end the child; returns its exit code. A poisoned child is killed."""
        if not self.poisoned:
            try:
                self.request("bye", timeout=timeout)
            except ArmError:
                pass
        if self.poisoned:
            self.kill()
        try:
            self.p.stdin.close()
        except OSError:
            pass
        return self.p.wait(timeout=timeout)
