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
* the harness side (ArmClient) numbers every request and accepts only the answer with that number; a timeout, a line
  that is not a JSON object, or a wrong number POISONS the client - every later request raises ArmPoisoned until the
  harness kills the child - so a late answer can never be taken for the next question's (F1).
"""
from __future__ import annotations

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
