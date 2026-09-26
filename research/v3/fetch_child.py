#!/usr/bin/env python3
"""PREREG-V3 plan step A3.b: the fetch child - every A3 download is this process, spawned under the launch contract.

The auditor's O1 ruling (2026-09-26): fetches are CHILDREN, never the harness. This script runs on the polygon's
py314 interpreter, standard library only, with the contract's environment: HTTPS_PROXY is its arm's catcher, which
tunnels only the window's exact hosts through the declared hop. It reads one job as a JSON line on stdin and writes
only inside its own working directory (a fresh unit directory); the harness verifies and places the files after the
window closes.

A job: {"hosts": [exact host names], "max_redirects": 0|1, "requests": [
          {"id": str, "method": "GET"|"HEAD", "url": "https://<host>/...", "save": "relative/path" | null,
           "max_bytes": int, "expect": {"sha256"?: hex, "git_blob_sha1"?: hex, "size"?: int} | null}]}

* Only https, only a host in the job's list, for the first URL and for a redirect target alike; at most
  ``max_redirects`` redirects; a HEAD never follows one - it reports the redirect's host (discovery).
* Only through the catcher: the proxy is the contract's HTTPS_PROXY and must be http://127.0.0.1:<port>; no other
  proxy, no direct connection. TLS is verified end to end (the default context).
* Identity encoding only. A body larger than ``max_bytes`` is abandoned. A saved file is streamed to
  ``<save>.partial`` with running sha256 and git-blob sha1, checked against ``expect``, and only then renamed.
* A rate limit stops the job: a 429 from any host, or a 403 from api.github.com (its unauthenticated limit), marks
  the request ``rate_limited`` and every later request of the job is recorded as not sent - never retried.
* The summary - one JSON object per request: id, status, final host, redirect host, bytes, sha256, git_blob_sha1,
  the peer certificate's issuer (O, CN; R-A3-7: the catcher relays ciphertext, so only the child can see it), ok,
  error - is written to fetch_summary.json in the working directory and printed as one line. No body is printed.

    <py314>\\python.exe research\\v3\\fetch_child.py   (the job on stdin)
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import ssl
import sys
import urllib.parse
from pathlib import Path, PurePosixPath

_LOOPBACK_PROXY = re.compile(r"http://127\.0\.0\.1:(\d{1,5})/?")
CHUNK = 1 << 20


class Refused(Exception):
    """A request the job's rules do not allow - reported, never worked around."""


def catcher_port(environ=os.environ) -> int:
    """The contract's HTTPS_PROXY, which must be the loopback catcher."""
    m = _LOOPBACK_PROXY.fullmatch(environ.get("HTTPS_PROXY") or environ.get("https_proxy") or "")
    if not m:
        raise Refused("HTTPS_PROXY is not the loopback catcher")
    return int(m.group(1))


def _safe_rel(save: str) -> PurePosixPath:
    rel = PurePosixPath(save.replace("\\", "/"))
    if rel.is_absolute() or not rel.parts or ".." in rel.parts or any(":" in p for p in rel.parts):
        raise Refused("a save path must stay inside the working directory")
    return rel


def _check_url(url: str, hosts: frozenset) -> tuple[str, str]:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname not in hosts or u.port not in (None, 443) or u.username or u.password:
        raise Refused(f"not an allowed https host: {u.hostname!r}")
    return u.hostname, (u.path or "/") + (f"?{u.query}" if u.query else "")


def _open(host: str, port: int, ctx: ssl.SSLContext, timeout: float) -> http.client.HTTPSConnection:
    conn = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=timeout)
    conn.set_tunnel(host, 443)
    return conn


def run_request(req: dict, *, hosts: frozenset, max_redirects: int, port: int, cwd: Path,
                ctx: ssl.SSLContext | None = None, timeout: float = 300.0) -> dict:
    """One request of the job. Returns its summary; never raises for a refusal or a network error."""
    out = {"id": req.get("id"), "status": None, "final_host": None, "redirect_host": None, "bytes": 0, "bytes_received": 0,
           "sha256": None, "git_blob_sha1": None, "issuer_o": None, "issuer_cn": None, "rate_limited": False,
           "ok": False, "error": None}
    ctx = ctx or ssl.create_default_context()
    method = req.get("method", "GET")
    try:
        if method not in ("GET", "HEAD"):
            raise Refused("only GET and HEAD")
        rel = _safe_rel(req["save"]) if req.get("save") else None
        host, path = _check_url(req["url"], hosts)
        hops = 0
        while True:
            conn = _open(host, port, ctx, timeout)
            try:
                conn.request(method, path, headers={"User-Agent": "nvt3-fetch/1", "Accept-Encoding": "identity"})
                r = conn.getresponse()
                out["status"], out["final_host"] = r.status, host
                if isinstance(conn.sock, ssl.SSLSocket):  # the last host's certificate (a redirect's target wins)
                    issuer = dict(x[0] for x in conn.sock.getpeercert().get("issuer", ()))
                    out["issuer_o"], out["issuer_cn"] = issuer.get("organizationName"), issuer.get("commonName")
                if r.status in (301, 302, 303, 307, 308):
                    loc = r.getheader("Location") or ""
                    target = urllib.parse.urljoin(f"https://{host}{path}", loc)
                    out["redirect_host"] = urllib.parse.urlsplit(target).hostname
                    r.read(65536)                    # a redirect body is short: drain it
                    if method == "HEAD":                 # discovery: report where it points, never follow
                        out["ok"] = True
                        return out
                    if hops >= max_redirects:
                        raise Refused("more redirects than the job allows")
                    host, path = _check_url(target, hosts)
                    hops += 1
                    continue
                if r.status == 429 or (r.status == 403 and host == "api.github.com"):
                    out["rate_limited"] = True
                    raise Refused(f"rate-limited: status {r.status}")
                if r.status != 200:
                    raise Refused(f"status {r.status}")
                if (r.getheader("Content-Encoding") or "identity").lower() != "identity":
                    raise Refused("an encoded body")
                if method == "HEAD":
                    out["ok"] = True
                    return out
                length = r.getheader("Content-Length")
                limit = int(req.get("max_bytes") or 0)
                if not limit:
                    raise Refused("a GET needs max_bytes")
                if length is not None and int(length) > limit:
                    raise Refused("the body is larger than max_bytes")
                h256 = hashlib.sha256()
                sha1 = hashlib.sha1(b"blob " + length.encode() + b"\0") if length is not None else None
                dest = part = None
                if rel is not None:
                    dest = cwd.joinpath(*rel.parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    part = dest.with_name(dest.name + ".partial")
                # No length: the git blob header needs the size, so the sha1 is taken afterwards - from the saved
                # file, or from the (small, unsaved) body kept in memory.
                chunks_for_sha1 = [] if (length is None and part is None) else None
                total = 0
                f = open(part, "wb") if part else None
                try:
                    while True:
                        data = r.read(CHUNK)
                        if not data:
                            break
                        total += len(data)
                        out["bytes_received"] = total    # what arrived, even if the request then fails
                        if total > limit:
                            raise Refused("the body is larger than max_bytes")
                        h256.update(data)
                        if sha1 is not None:
                            sha1.update(data)
                        elif chunks_for_sha1 is not None:
                            chunks_for_sha1.append(data)
                        if f:
                            f.write(data)
                finally:
                    if f:
                        f.close()
                if length is not None and total != int(length):
                    raise Refused("the body ended before its Content-Length")
                if sha1 is None:
                    sha1 = hashlib.sha1(b"blob " + str(total).encode() + b"\0")
                    if part is not None:
                        with open(part, "rb") as g:
                            for block in iter(lambda: g.read(CHUNK), b""):
                                sha1.update(block)
                    else:
                        sha1.update(b"".join(chunks_for_sha1))
                out.update(bytes=total, sha256=h256.hexdigest(), git_blob_sha1=sha1.hexdigest())
                exp = req.get("expect") or {}
                bad = [k for k, v in (("sha256", out["sha256"]), ("git_blob_sha1", out["git_blob_sha1"]), ("size", total))
                       if k in exp and exp[k] != v]
                if bad:
                    raise Refused("does not match the expected " + ", ".join(bad))
                if part:
                    os.replace(part, dest)
                out["ok"] = True
                return out
            finally:
                conn.close()
    except (Refused, OSError, ssl.SSLError, http.client.HTTPException, ValueError, KeyError) as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        out["bytes"] = None                          # no file: bytes_received says how much came before the error
        if req.get("save"):
            try:
                p = cwd.joinpath(*_safe_rel(req["save"]).parts)
                p.with_name(p.name + ".partial").unlink(missing_ok=True)
            except (Refused, OSError):
                pass
        return out


def run_job(job: dict, *, cwd: Path, port: int, ctx: ssl.SSLContext | None = None) -> list[dict]:
    hosts = frozenset(job.get("hosts") or ())
    max_redirects = int(job.get("max_redirects", 0))
    if max_redirects not in (0, 1):
        raise Refused("max_redirects is 0 or 1")
    out: list[dict] = []
    stopped = False
    for r in job.get("requests") or []:
        if stopped:                                      # a rate limit stops the job: nothing more is sent
            out.append({"id": r.get("id"), "ok": False, "rate_limited": False, "error": "not sent: rate-limited earlier"})
            continue
        res = run_request(r, hosts=hosts, max_redirects=max_redirects, port=port, cwd=cwd, ctx=ctx)
        stopped = bool(res.get("rate_limited"))
        out.append(res)
    return out


def main() -> int:
    job = json.loads(sys.stdin.readline() or "{}")
    cwd = Path.cwd()
    try:
        port = catcher_port()
        results = run_job(job, cwd=cwd, port=port)
    except Refused as e:
        results = [{"id": None, "ok": False, "error": f"Refused: {e}"}]
    data = json.dumps(results, sort_keys=True).encode("utf-8")
    tmp = cwd / "fetch_summary.json.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, cwd / "fetch_summary.json")
    print(json.dumps({"requests": len(results), "ok": sum(1 for r in results if r.get("ok"))}), flush=True)
    return 0 if results and all(r.get("ok") for r in results) else 3


if __name__ == "__main__":
    sys.exit(main())
