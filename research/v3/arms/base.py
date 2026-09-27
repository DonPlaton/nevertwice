#!/usr/bin/env python3
"""PREREG-V3 TB4.5a (A6): the harness <-> arm-child protocol - JSON lines over stdin/stdout, one child per (arm, run,
unit) and stage (ruling Q25: a unit process exits after the write stage; the question stage opens the same on-disk store
in a new process; an arm whose store lives in memory keeps its process and says so in arm_decl).

This module is self-contained (stdlib only) on purpose: by ruling Q9 O-b it is COPIED byte-identical, with its sha256
recorded, into each competitor arm's runs-tree directory at block start, so no competitor or judge child ever has the
repository on its argv or path.

Requests (one JSON object per LF line):  {"op": "hello" | "write" | "end_write" | "read" | "counters" | "bye", ...}
Responses (one per request):             {"ok": true, ...} or {"ok": false, "error": "<Type>: <message>"}

* the child never dies on a bad request: malformed JSON, an unknown op, or a handler exception is an ok:false line, and
  the child goes on reading;
* the protocol owns the child's stdout: serve() writes on a duplicate of the original stdout and points fd 1 (and
  sys.stdout) at stderr, so a product that prints cannot corrupt the stream;
* the harness side (ArmClient) sends one request and waits for exactly one line, with a timeout; a timeout, a dead child
  or an ok:false answer is raised by name - never read as an empty result.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
from typing import Any, Callable, Mapping

PROTOCOL = "nvt3-arm-1"
OPS = ("hello", "write", "end_write", "read", "counters", "bye")
ERROR_MAX = 500


class ArmError(RuntimeError):
    """The child answered ok:false."""


class ArmTimeout(RuntimeError):
    """No answer line within the timeout."""


class ArmDied(RuntimeError):
    """The child's stdout closed before it answered."""


def _line(obj: Mapping) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def serve(handler: Any, fin=None, fout=None, *, take_stdout: bool = True) -> int:
    """Answer requests until "bye" or end of input. ``handler`` has one method per op (hello, write, end_write, read,
    counters); each takes the request's other fields as keyword arguments and returns a dict. Returns 0."""
    if fout is None:
        if take_stdout:
            sys.stdout.flush()
            fout = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
            os.dup2(2, 1)                      # a product's stray print lands on stderr, never in the stream
            sys.stdout = sys.stderr
        else:
            fout = sys.stdout
    fin = fin if fin is not None else sys.stdin
    for raw in fin:
        if not raw.strip():
            continue
        try:
            req = json.loads(raw)
            if not isinstance(req, dict):
                raise ValueError("a request is a JSON object")
            op = req.pop("op", None)
            if op not in OPS:
                raise ValueError(f"unknown op {op!r}")
            if op == "bye":
                fout.write(_line({"ok": True, "op": "bye"}))
                fout.flush()
                return 0
            out = getattr(handler, op)(**req)
            if not isinstance(out, dict):
                raise TypeError(f"{op} returned {type(out).__name__}, not a dict")
            resp = {"ok": True, "op": op, **out}
        except Exception as e:  # noqa: BLE001 - the child never dies on one request
            resp = {"ok": False, "error": f"{type(e).__name__}: {e}"[:ERROR_MAX]}
        fout.write(_line(resp))
        fout.flush()
    return 0


class ArmClient:
    """The harness side of one child process (stdin/stdout pipes in binary mode)."""

    def __init__(self, process, *, default_timeout: float = 600.0) -> None:
        self.p = process
        self.timeout = default_timeout
        self._q: "queue.Queue[bytes | None]" = queue.Queue()
        self._t = threading.Thread(target=self._pump, daemon=True)
        self._t.start()

    def _pump(self) -> None:
        for raw in iter(self.p.stdout.readline, b""):
            self._q.put(raw)
        self._q.put(None)

    def request(self, op: str, *, timeout: float | None = None, **fields) -> dict:
        if op not in OPS:
            raise ValueError(f"unknown op {op!r}")
        try:
            self.p.stdin.write(_line({"op": op, **fields}).encode("utf-8"))
            self.p.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise ArmDied(f"{op}: the child's stdin is closed ({type(e).__name__})") from None
        try:
            raw = self._q.get(timeout=self.timeout if timeout is None else timeout)
        except queue.Empty:
            raise ArmTimeout(f"{op}: no answer within {self.timeout if timeout is None else timeout} s") from None
        if raw is None:
            raise ArmDied(f"{op}: the child's stdout closed (exit {self.p.poll()})")
        resp = json.loads(raw.decode("utf-8"))
        if not resp.get("ok"):
            raise ArmError(f"{op}: {resp.get('error')}")
        return resp

    def close(self, *, timeout: float = 30.0) -> int:
        """Say bye, wait for the child, return its exit code."""
        try:
            self.request("bye", timeout=timeout)
        except (ArmDied, ArmTimeout, ArmError):
            pass
        try:
            self.p.stdin.close()
        except OSError:
            pass
        return self.p.wait(timeout=timeout)


def main_with(handler_factory: Callable[[], Any]) -> int:
    """An arm child's entry point: build the handler (all its environment checks first), then serve."""
    return serve(handler_factory())
