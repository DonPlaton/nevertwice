#!/usr/bin/env python3
"""PREREG-V3 A8, supermemory-local (the probe plan v2, C5; the auditor's rulings 2026-09-30 17:32-17:42): the server
child's pieces the probe (probe_sm.py) drives - nothing here starts a process by itself.

* server_env: what the spawn declares - the port, the data directory, the embedder (ours, bge-m3 through the Ollama
  leg, openai-compatible, 1024 dimensions: the auditor's SM3 17:34) and the LLM (the proxy, OPENAI_MODEL); never a
  vendor switch (TELEMETRY_OFF carries them, a declared one would clash) nor WORKFLOW_ENGINE nor another NOT_DECLARED
  name; every name checked against the bundle's own env facts (sm_bundle.declared_problems) and no vendor default
  reached (sm_bundle.resolve) - else refused by name, before any spawn.
* launch_spec: the scheduler's LaunchSpec for the server - stdio "server" (C2), the proxy token as the one declared
  token - after the binary is hashed again against the pin (SF1): another sha256 is refused by name.
* HeldKey: the server's key in this process's memory only - never in a repr, a str, a format, a pickle, a copy or JSON;
  its sha256[:12] and its form stand for it.
* RedactingPump: the server's stdout (stderr merged into it) read to its end by a daemon thread and appended to a file,
  every key-shaped token and the held value replaced by <sm-key:sha12> before a byte is written; the banner's key is
  captured into a HeldKey. A line longer than LINE_MAX is cut CARRY bytes short of its end, and never inside a token.
* wait_ready: the server's port asked on 127.0.0.1 until an answer that is not its 503 "Service is starting", under a
  ceiling; a server that exits first is named.
* tree / stop: the server's process tree (pid, name, exe, sha256); stop sends CTRL_BREAK to its process group
  (launch.spawn starts it in its own), waits STOP_GRACE_S, then kills the whole tree - and whatever the tree held
  before the stop that is still alive after it.
* data_digest: the data directory's files, by relative path and sha256.
* key_search (SF31, Q-A8S-KEY2 = a): the held key searched in UTF-8 and UTF-16LE in every file under the given roots;
  a hit inside the server's data directory is product_stored, anywhere else a leak; links are listed and never
  followed, an unreadable file is listed and never skipped.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import importlib.util
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

HERE = Path(__file__).resolve().parent
#: a key-shaped token (the docs: sm_ and 30 characters; any sm_ token of 8+ is redacted - an over-redaction is counted,
#: never a leak)
KEY_TOKEN = re.compile(rb"sm_[A-Za-z0-9_\-]{8,}")
#: the banner row that prints the key (sm_bundle's fact key_banner: ["api key", $.apiKey])
BANNER_MARK = b"api key"
LINE_MAX = 1 << 20
CARRY = 96
KEY_HEADER = "x-supermemory-api-key"
READY_CEILING_S = 120.0
READY_POLL_S = 0.25
STOP_GRACE_S = 10.0
KILL_WAIT_S = 10.0
#: the embedder and the LLM the spawn declares (the auditor's SM3, 2026-09-30 17:34)
EMBED_PROVIDER = "openai-compatible"
EMBED_DIMENSIONS = 1024
TOKEN_NAME = "OPENAI_API_KEY"
_LOAD_LOCK = threading.RLock()     # B-LOAD-RACE: the pump thread exists while the probe's main thread may load


class ServerRefused(RuntimeError):
    """A refusal before any spawn, by name."""


def _load(name: str, path: Path):
    """A sibling module by path; the lookup and the load are one step under _LOAD_LOCK (B-LOAD-RACE)."""
    with _LOAD_LOCK:
        mod = sys.modules.get(name)
        if mod is None:
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop(name, None)
                raise
        return mod


def _bundle():
    return _load("v3_sm_bundle", HERE / "sm_bundle.py")


def _sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


# ── the spawn ───────────────────────────────────────────────────────────

def server_env(env_rec: Mapping, *, port: int, data_dir: str | os.PathLike, llm_base_url: str, llm_model: str,
               embed_base_url: str, embed_model: str, token: str) -> dict:
    """The names the server spawn declares (see the module docstring). Refused by name when the bundle's env facts
    (sm_bundle.env_facts, in ``env_rec``) find a problem with a name, or when a vendor default would be reached."""
    declared = {"SUPERMEMORY_PORT": str(int(port)), "SUPERMEMORY_DATA_DIR": os.fspath(data_dir),
                "SUPERMEMORY_EMBEDDING_PROVIDER": EMBED_PROVIDER, "SUPERMEMORY_EMBEDDING_MODEL": embed_model,
                "SUPERMEMORY_EMBEDDING_BASE_URL": embed_base_url,
                "SUPERMEMORY_EMBEDDING_DIMENSIONS": str(EMBED_DIMENSIONS),
                TOKEN_NAME: token, "OPENAI_BASE_URL": llm_base_url, "OPENAI_MODEL": llm_model}
    SB = _bundle()
    problems = SB.declared_problems(declared, env_rec)
    try:
        reached = SB.resolve(declared, env_rec)["vendor_defaults"]
    except (KeyError, AttributeError, TypeError) as e:
        reached = [f"the bundle's env facts cannot resolve the spawn ({type(e).__name__}: {e})"]
    problems += [f"the spawn would reach a vendor default: {d}" for d in reached]
    if problems:
        raise ServerRefused("; ".join(problems))
    return declared


def launch_spec(SC: Any, binary: str | os.PathLike, *, pin_sha256: str, declared: Mapping[str, str]) -> Any:
    """SC.LaunchSpec for the server (SC: the scheduler module): the binary hashed again against the pin first (SF1;
    R13 - launch.spawn hashes it once more and records it)."""
    path = Path(binary)
    if not path.is_absolute() or not path.is_file():
        raise ServerRefused(f"the server binary is not an existing absolute file: {path}")
    got = _sha256_file(path)
    if got != pin_sha256:
        raise ServerRefused(f"the server binary's sha256 {got[:12]} is not the pin {str(pin_sha256)[:12]} (SF1) - "
                            f"nothing is spawned")
    return SC.LaunchSpec(argv=(str(path),), declared=dict(declared), token_names=(TOKEN_NAME,), stdio="server",
                         requirement="required")


def free_port() -> int:
    """A port no one holds on 127.0.0.1 now (Q-A8S-PORT: the win32 default runs no Rivet child)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── the key ─────────────────────────────────────────────────────────────

def key_form(value: bytes) -> str:
    """The form of a key, never its characters: prefix, length, alphabet."""
    body = value[3:] if value.startswith(b"sm_") else value
    alpha = ("alnum" if body.isalnum() and body.isascii()
             else "alnum_-" if re.fullmatch(rb"[A-Za-z0-9_\-]+", body) else "other")
    return f"{'sm_' if value.startswith(b'sm_') else 'no sm_'}+{len(body)} {alpha}"


class HeldKey:
    """The server's key, held in this process's memory only (the plan's C): sent only by the probe's loopback requests
    (header()); never an env, an argv, a file, a log line, an exception or a record - sha12 and form stand for it."""
    __slots__ = ("_v", "sha12", "form")

    def __init__(self, value: bytes) -> None:
        self._v = bytes(value)
        self.sha12 = sha12(self._v)
        self.form = key_form(self._v)

    def __repr__(self) -> str:
        return f"<HeldKey sha12={self.sha12} {self.form}>"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __reduce_ex__(self, protocol: int):
        raise TypeError("a HeldKey is never pickled")

    def __copy__(self):
        raise TypeError("a HeldKey is never copied")

    def __deepcopy__(self, memo: dict):
        raise TypeError("a HeldKey is never copied")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, HeldKey) and hmac.compare_digest(self._v, other._v)

    def __hash__(self) -> int:
        return hash(self.sha12)

    def matches(self, data: bytes) -> bool:
        """The key compared with bytes (the product's key file, stripped) in constant time."""
        return hmac.compare_digest(self._v, bytes(data).strip())

    def header(self) -> dict:
        """The one place the value leaves this object: a loopback request's header (probe_sm.sm_request)."""
        return {KEY_HEADER: self._v.decode("ascii", "strict")}

    def needles(self) -> tuple[tuple[str, bytes], ...]:
        """The value as key_search looks for it: UTF-8 and UTF-16LE."""
        return (("utf-8", self._v), ("utf-16le", self._v.decode("ascii", "replace").encode("utf-16-le")))


# ── the pump ────────────────────────────────────────────────────────────

class RedactingPump:
    """The server's stdout read to its end by a daemon thread (unread, a pipe fills and the server blocks: B-PIPE)
    and appended to ``path`` - redacted before a byte is written. The first key-shaped token on a banner line (one
    holding BANNER_MARK) becomes ``key``; a later banner key is counted in ``banner_keys`` (sha12 each: SF28)."""

    def __init__(self, stream: Any, path: str | os.PathLike, *, held: HeldKey | None = None) -> None:
        self.stream, self.path = stream, Path(path)
        self.key: HeldKey | None = held
        self.key_seen = threading.Event()
        if held is not None:
            self.key_seen.set()
        self.done = threading.Event()
        self.lines = self.redacted = self.long_lines = 0
        self.banner_keys: list[str] = []
        self.error: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "RedactingPump":
        self._thread = threading.Thread(target=self._run, name="sm-redacting-pump", daemon=True)
        self._thread.start()
        return self

    def join(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
        return self.done.is_set()

    def _marker(self, token: bytes) -> bytes:
        self.redacted += 1
        return b"<sm-key:" + sha12(token).encode("ascii") + b">"

    def _redact(self, data: bytes) -> bytes:
        out = KEY_TOKEN.sub(lambda m: self._marker(m.group(0)), data)
        if self.key is not None and self.key._v in out:
            n = out.count(self.key._v)
            out = out.replace(self.key._v, b"<sm-key:" + self.key.sha12.encode("ascii") + b">")
            self.redacted += n
        return out

    def _capture(self, line: bytes) -> None:
        if BANNER_MARK not in line.lower():
            return
        m = KEY_TOKEN.search(line)
        if m is None:
            return
        self.banner_keys.append(sha12(m.group(0)))
        if self.key is None:
            self.key = HeldKey(m.group(0))
            self.key_seen.set()

    def _cut(self, buf: bytes) -> int:
        """Where a line longer than LINE_MAX is cut: CARRY bytes stay behind, and never inside a key-shaped token or
        the held value - a key cut in two reaches the file as two halves no pattern matches."""
        cut = len(buf) - CARRY
        for m in KEY_TOKEN.finditer(buf):
            if m.start() < cut < m.end():
                cut = m.start()
        if self.key is not None:
            v, i = self.key._v, buf.find(self.key._v)
            while i != -1:
                if i < cut < i + len(v):
                    cut = i
                i = buf.find(v, i + 1)
        return cut if cut > 0 else len(buf) - CARRY

    def _run(self) -> None:
        f = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            f = open(self.path, "ab")  # noqa: SIM115 - held until the pipe ends
        except OSError as e:
            self.error = f"{type(e).__name__}"
        buf = b""
        try:
            while True:
                chunk = self.stream.readline(LINE_MAX)
                if not chunk:
                    break
                buf += chunk
                if buf.endswith(b"\n"):
                    self.lines += 1
                    self._capture(buf)
                    out, buf = buf, b""
                else:
                    self.long_lines += 1
                    cut = self._cut(buf)
                    out, buf = buf[:cut], buf[cut:]
                self._write(f, self._redact(out))
            if buf:
                self.lines += 1
                self._capture(buf)
                self._write(f, self._redact(buf))
        except (OSError, ValueError) as e:
            self.error = self.error or f"{type(e).__name__}"
        finally:
            if f is not None:
                f.close()
            self.done.set()

    def _write(self, f: Any, data: bytes) -> None:
        if f is None or not data:
            return
        try:
            f.write(data)
            f.flush()
        except OSError as e:
            self.error = self.error or f"{type(e).__name__}"


# ── the running server ──────────────────────────────────────────────────

def wait_ready(port: int, *, process: Any, ceiling_s: float = READY_CEILING_S, poll_s: float = READY_POLL_S,
               clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> dict:
    """{"ready", "after_s", "polls", "status", "why"}: GET / on 127.0.0.1:port until a status that is not 503 (the
    server's "Service is starting"); an exit first, or the ceiling, is not ready - by name."""
    t0, polls, status = clock(), 0, None
    while True:
        rc = process.poll()
        if rc is not None:
            return {"ready": False, "after_s": round(clock() - t0, 3), "polls": polls, "status": status,
                    "why": f"the server exited with {rc} before it answered"}
        polls += 1
        status = _status(port, timeout=max(poll_s, 1.0))
        if status is not None and status != 503:
            return {"ready": True, "after_s": round(clock() - t0, 3), "polls": polls, "status": status, "why": None}
        if clock() - t0 >= ceiling_s:
            return {"ready": False, "after_s": round(clock() - t0, 3), "polls": polls, "status": status,
                    "why": f"no answer other than 503 within {ceiling_s} s"}
        sleep(poll_s)


def _status(port: int, *, timeout: float) -> int | None:
    conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
    try:
        conn.request("GET", "/", headers={"Host": f"127.0.0.1:{int(port)}"})
        return conn.getresponse().status
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def tree(pid: int) -> list[dict]:
    """SF9: the process tree under ``pid``, the root first - pid, create_time, parent, name, exe and its sha256 (None where psutil
    cannot say; the error's type is kept)."""
    import psutil  # noqa: PLC0415 - scheduler.tree_kill_route requires it or a job object before any spawn
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.Error as e:
        return [{"pid": pid, "error": type(e).__name__}]
    out = []
    for p in procs:
        row: dict = {"pid": p.pid}
        try:
            row.update(create_time=p.create_time(), ppid=p.ppid(), name=p.name(), exe=p.exe())
            row["sha256"] = _sha256_file(row["exe"]) if row["exe"] else None
        except (psutil.Error, OSError) as e:
            row["error"] = type(e).__name__
        out.append(row)
    return out


def stop(child: Any, *, grace_s: float = STOP_GRACE_S, clock: Callable[[], float] = time.monotonic) -> dict:
    """SF26: CTRL_BREAK to the server's process group (SIGTERM off Windows), ``grace_s`` for it to exit, then the
    whole tree killed (child.kill_tree: its job object, else psutil); the tree as it was before the stop is killed
    too where any of it outlived the server. {"how": "ctrl_break" | "killed", "exit_code", "after_s", "send_error",
    "outlived", "alive", "reused", "unverified"}. Already exited roots use only their retained job handle;
    numeric PID fallback is unsafe after exit and the unavailable tree is recorded explicitly."""
    import psutil  # noqa: PLC0415
    p = child.process
    before, reused, unverified = [], [], []
    initial_rc = p.poll()
    if initial_rc is not None:
        witness = getattr(child, "witness", None)
        job_terminated = witness is not None and witness.kill_tree(p.pid)
        return {"how": "already-exited", "exit_code": initial_rc, "after_s": 0.0, "send_error": None,
                "outlived": [], "alive": False, "reused": [], "job_terminated": bool(job_terminated),
                "unverified": [{"pid": p.pid, "error": "root-exited-before-snapshot"}]}
    snapshot = tree(p.pid)
    if not snapshot or snapshot[0].get("error"):
        unverified.append({"pid": p.pid, "error": "tree:" + str(snapshot[0].get("error")
                                                                  if snapshot else "empty")})
    for row in snapshot[1:]:
        pid = row["pid"]
        try:
            born = row.get("create_time")
            if not isinstance(born, (int, float)) or isinstance(born, bool):
                unverified.append({"pid": pid, "error": "identity-unmeasured"})
                continue
            process = psutil.Process(pid)
            if process.create_time() != born:
                reused.append(pid)
                continue
            before.append((process, born))
        except psutil.Error as e:
            unverified.append({"pid": pid, "error": type(e).__name__})
    t0, send_error, how = clock(), None, "ctrl_break"
    try:
        if os.name == "nt":
            os.kill(p.pid, signal.CTRL_BREAK_EVENT)
        else:
            p.send_signal(signal.SIGTERM)
    except OSError as e:
        send_error = type(e).__name__
    rc = None
    if send_error is None:
        try:
            rc = p.wait(grace_s)
        except subprocess.TimeoutExpired:
            rc = None
    if rc is None:
        how = "killed"
        child.kill_tree()
        try:
            rc = p.wait(KILL_WAIT_S)
        except subprocess.TimeoutExpired:
            rc = None
    outlived = []
    for q, born in before:
        try:
            if psutil.Process(q.pid).create_time() != born:
                reused.append(q.pid)
                continue
            if q.is_running() and q.status() != psutil.STATUS_ZOMBIE:
                outlived.append(q.pid)
                q.kill()  # the retained psutil object also checks its cached process identity
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as e:
            unverified.append({"pid": q.pid, "error": type(e).__name__})
    return {"how": how, "exit_code": rc, "after_s": round(clock() - t0, 3), "send_error": send_error,
            "outlived": outlived, "alive": p.poll() is None, "reused": reused, "unverified": unverified}


def data_digest(data_dir: str | os.PathLike) -> dict:
    """The data directory's files: {relative path: sha256}, links listed as "link" and never followed."""
    root, out = Path(data_dir), {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in list(dirnames):
            p = Path(dirpath, name)
            if _is_link(p):
                out[p.relative_to(root).as_posix()] = "link"
                dirnames.remove(name)
        for name in filenames:
            p = Path(dirpath, name)
            try:
                out[p.relative_to(root).as_posix()] = "link" if _is_link(p) else _sha256_file(p)
            except OSError as e:
                out[p.relative_to(root).as_posix()] = f"unreadable: {type(e).__name__}"
    return dict(sorted(out.items()))


# ── the key's traces ────────────────────────────────────────────────────

def _is_link(p: Path) -> bool:
    return p.is_symlink() or bool(getattr(os.path, "isjunction", lambda _: False)(p))


def _inside(child: Path, root: Path) -> bool:
    c, r = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath([c, r]) == r
    except ValueError:
        return False


def _scan(path: Path, needles: tuple[tuple[str, bytes], ...], opener: Callable) -> list[str]:
    """The encodings whose needle occurs in the file, read in 1 MiB chunks with an overlap one byte short of the
    longest needle."""
    keep = max(len(n) for _, n in needles) - 1
    found: set[str] = set()
    tail = b""
    with opener(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            buf = tail + chunk
            found.update(enc for enc, n in needles if n in buf)
            tail = buf[-keep:] if keep else b""
    return sorted(found)


def key_search(roots: Mapping[str, str | os.PathLike], held: HeldKey, *, data_dir: str | os.PathLike,
               opener: Callable = open) -> dict:
    """SF31 (see the module docstring): {"files", "product_stored", "leaks", "unreadable", "links"}; each hit is
    {"root", "path", "encodings"} - a path relative to its root, never the value."""
    needles, data = held.needles(), Path(data_dir)
    out: dict = {"files": 0, "product_stored": [], "leaks": [], "unreadable": [], "links": []}
    for label, root in sorted(roots.items()):
        root = Path(root)
        if _is_link(root):
            out["links"].append({"root": label, "path": "."})
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            for name in list(dirnames):
                p = Path(dirpath, name)
                if _is_link(p):
                    out["links"].append({"root": label, "path": p.relative_to(root).as_posix()})
                    dirnames.remove(name)
            for name in filenames:
                p = Path(dirpath, name)
                rel = p.relative_to(root).as_posix()
                if _is_link(p):
                    out["links"].append({"root": label, "path": rel})
                    continue
                out["files"] += 1
                try:
                    enc = _scan(p, needles, opener)
                except OSError as e:
                    out["unreadable"].append({"root": label, "path": rel, "error": type(e).__name__})
                    continue
                if enc:
                    hit = {"root": label, "path": rel, "encodings": enc}
                    (out["product_stored"] if _inside(p, data) else out["leaks"]).append(hit)
    return out
