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

A8 C4 adds one more job kind, {"kind": "oci", "repo", "registry", "auth", "service", "cdn_hosts", "hosts", "platform",
"max_meta_bytes", "max_blob_bytes"} (oci_job, the auditor's Q-C4-1..3): an anonymous pull token for exactly
repository:<repo>:pull, held in memory only and sent only to the registry; every page of the tags list; the newest
release ^v?X.Y.Z$ by the numbers, all its tags resolving to one index; that index's one linux/amd64 manifest - or,
C4A-6, a plain image manifest whose config says linux/amd64 (checked before any layer; index_digest None); the
config and each layer by digest, each checked on the disk against its digest and size, a blob redirect followed once
and only to a declared CDN host over https on 443 without userinfo, which never gets the token, and whose signed query
is never recorded; the digest behind "latest" read right after the tags (C4A-5), information only - a non-200 there is
recorded, never fatal. Files land under <cwd>/oci/blobs/sha256/.

B-NLP NLP-2 adds {"kind": "gh_model", "model", "spacy", "repo", "raw_host", "api_host", "web_host", "compat_path",
"cdn_hosts", "hosts", "max_meta_bytes", "max_asset_bytes"} (gh_model_job, the auditor's rule of 07:19 and Q-NLP-1): the
model's version is the entry explosion/spacy-models' compatibility.json lists FIRST under the LOCKED spaCy's
major.minor (the one spacy.cli.download takes), and it must be the newest X.Y.Z by its numbers - when they differ the job
refuses by name and the auditor rules, neither is picked by hand; the release by its tag <model>-<version> must hold exactly one
asset <model>-<version>-py3-none-any.whl at github.com's own download path, its "digest" the sha256 the stream is
checked against (none: TLS-only trust, recorded as a declared limit); github.com answers the file or redirects it once,
to a declared CDN host over https on 443 with no userinfo and no Authorization; the CDN host is recorded, the signed
query never. The file lands under <cwd>/model/<name>.

T32 (PREREG-V3-AMENDMENTS.md A3) adds {"kind": "gh_release", "repo", "tag", "commit", "api_host", "web_host", "cdn_hosts",
"hosts", "assets": [{"name", "size", "digest"}], "max_meta_bytes", "max_asset_bytes"} (gh_release_job): a vendor's
release binary whose expectation was fixed before the window (bin_install reads it from the a7-docs record) - the tag
must still name the expected commit (commits/<tag>); the release by that tag must be that tag, not a draft, not a
prerelease, and hold exactly one asset of each expected name at github.com's own download path with exactly the expected
size and sha256 digest - the live answer and the record must agree, neither alone is trusted; each file then as for
gh_model (one redirect at most, to a declared CDN host), its stream checked against the digest and the size. No other
asset is requested. The files land under <cwd>/release/<name>.

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
RAW_ENCODINGS = frozenset({"gzip", "x-gzip"})    # a request's raw_encoding: the only encodings it may keep as sent
_EPRINT = re.compile(r"https://arxiv\.org/e-print/[0-9]{4}\.[0-9]{4,5}v[0-9]{1,3}")   # the only URL that may carry it


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


def _issuer_of(conn: http.client.HTTPSConnection) -> tuple:
    """(organisation, common name) of the connected peer certificate's issuer; (None, None) without TLS."""
    if isinstance(conn.sock, ssl.SSLSocket):
        issuer = dict(x[0] for x in conn.sock.getpeercert().get("issuer", ()))
        return issuer.get("organizationName"), issuer.get("commonName")
    return None, None


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
        # plan d8's e-print: a body the server sends gzip-encoded is kept as sent - never decoded, hashed as it came
        raw_enc = req.get("raw_encoding")
        if raw_enc is not None and not (isinstance(raw_enc, list) and raw_enc and set(raw_enc) <= RAW_ENCODINGS):
            raise Refused(f"raw_encoding names only {sorted(RAW_ENCODINGS)}")
        if raw_enc is not None and not (method == "GET" and _EPRINT.fullmatch(str(req.get("url")))):
            raise Refused("raw_encoding is for an arXiv e-print GET only")
        rel = _safe_rel(req["save"]) if req.get("save") else None
        host, path = _check_url(req["url"], hosts)
        hops = 0
        chain: list = []
        while True:
            conn = _open(host, port, ctx, timeout)
            try:
                # B-ISS-CLOSE: the certificate is read on connect, before the request - a response with Connection:
                # close has closed the socket by the time getresponse() returns
                conn.connect()
                o, cn = _issuer_of(conn)
                out["issuer_o"], out["issuer_cn"] = o, cn          # the last host's (a redirect's target wins)
                chain.append({"host": host, "issuer_o": o, "issuer_cn": cn})
                conn.request(method, path, headers={"User-Agent": "nvt3-fetch/1", "Accept-Encoding": "identity"})
                r = conn.getresponse()
                out["status"], out["final_host"] = r.status, host
                if r.status in (301, 302, 303, 307, 308):
                    loc = r.getheader("Location") or ""
                    target = urllib.parse.urljoin(f"https://{host}{path}", loc)
                    out["redirect_host"] = urllib.parse.urlsplit(target).hostname
                    out["redirect_path"] = urllib.parse.urlsplit(target).path   # never the query: signatures live there
                    r.read(65536)                    # a redirect body is short: drain it
                    if method == "HEAD":                 # discovery: report where it points, never follow
                        out["ok"] = True
                        return out
                    if hops >= max_redirects:
                        raise Refused("more redirects than the job allows")
                    host, path = _check_url(target, hosts)
                    out["hops"] = chain                          # every host of a followed redirect, each its issuer
                    hops += 1
                    continue
                if r.status == 429 or (r.status == 403 and host == "api.github.com"):
                    out["rate_limited"] = True
                    raise Refused(f"rate-limited: status {r.status}")
                if r.status != 200:
                    raise Refused(f"status {r.status}")
                enc = (r.getheader("Content-Encoding") or "identity").lower()
                if raw_enc is not None:
                    out["content_encoding"], out["content_type"] = r.getheader("Content-Encoding"), r.getheader("Content-Type")
                if enc != "identity" and enc not in (raw_enc or ()):
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


# ── A8 C4: an OCI registry job (the auditor's Q-C4-1..3) ──────────────────────────────────────────────────────

OCI_INDEX = ("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json")
OCI_MANIFEST = ("application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json")
_RELEASE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_REPO = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+")
_REDIRECTS = (301, 302, 303, 307, 308)
MAX_TAG_PAGES = 100


def release_tags(tags) -> tuple[tuple, list[str]] | None:
    """Q-C4-1: the newest release - the largest (X, Y, Z) of the tags that are ^v?X.Y.Z$ (digits only: no pre-release,
    no "latest"), by the numbers, never by push date - and every tag that names it (X.Y.Z and vX.Y.Z)."""
    by: dict = {}
    for t in tags:
        m = _RELEASE.fullmatch(str(t))
        if m:
            by.setdefault(tuple(int(x) for x in m.groups()), []).append(str(t))
    if not by:
        return None
    key = max(by)
    return key, sorted(by[key])


def _next_link(value: str | None, registry: str, repo: str) -> str | None:
    """The tags list's next page (RFC 5988 Link, rel="next"), kept on the registry and the same list, or None."""
    if not value:
        return None
    for part in value.split(","):
        m = re.match(r'\s*<([^>]*)>\s*;\s*rel="?next"?\s*$', part)
        if m:
            u = urllib.parse.urlsplit(urllib.parse.urljoin(f"https://{registry}/", m.group(1)))
            if u.hostname != registry or u.path != f"/v2/{repo}/tags/list":
                raise Refused(f"the tags list's next page leaves the registry's list: {u.hostname!r}{u.path!r}")
            return u.path + (f"?{u.query}" if u.query else "")
    return None


def tunnel_send(port: int, ctx: ssl.SSLContext | None = None, timeout: float = 300.0):
    """The wire of an OCI job: one request through the catcher's tunnel, TLS verified end to end. A redirect, a HEAD or
    a non-200 answer comes back with no body; a 200 body is streamed to ``save_to`` (via .partial) or kept in memory,
    never past ``max_bytes``, identity encoding only. Returns (status, lower-case headers, body, sha256 hex, bytes).
    R-GHR-ISS (the auditor's Q-ISS-1): after each call ``send.peer`` says the peer it reached - {host, issuer_o,
    issuer_cn} of that host's certificate (R-A3-7: the catcher relays ciphertext, only the child sees it) - and a job's
    entry for the call carries it."""
    ctx = ctx or ssl.create_default_context()

    def send(method: str, host: str, path: str, headers: dict, *, max_bytes: int, save_to: Path | None = None):
        send.peer = None
        conn = _open(host, port, ctx, timeout)
        try:
            conn.request(method, path, headers={"User-Agent": "nvt3-fetch/1", "Accept-Encoding": "identity", **headers})
            r = conn.getresponse()
            if isinstance(conn.sock, ssl.SSLSocket):     # this call's own peer: the host just reached, never an earlier one
                issuer = dict(x[0] for x in conn.sock.getpeercert().get("issuer", ()))
                send.peer = {"host": host, "issuer_o": issuer.get("organizationName"), "issuer_cn": issuer.get("commonName")}
            hdrs = {k.lower(): v for k, v in r.getheaders()}
            if method == "HEAD" or r.status != 200:
                r.read(65536)
                return r.status, hdrs, b"", None, 0
            if (hdrs.get("content-encoding") or "identity").lower() != "identity":
                raise Refused("an encoded body")
            length = hdrs.get("content-length")
            if length is not None and int(length) > max_bytes:
                raise Refused("the body is larger than max_bytes")
            h, total, keep = hashlib.sha256(), 0, []
            part = save_to.with_name(save_to.name + ".partial") if save_to is not None else None
            f = open(part, "wb") if part is not None else None
            try:
                while True:
                    data = r.read(CHUNK)
                    if not data:
                        break
                    total += len(data)
                    if total > max_bytes:
                        raise Refused("the body is larger than max_bytes")
                    h.update(data)
                    if f is not None:
                        f.write(data)
                    else:
                        keep.append(data)
            except BaseException:
                if f is not None:
                    f.close()
                    part.unlink(missing_ok=True)
                raise
            if f is not None:
                f.close()
                os.replace(part, save_to)
            return 200, hdrs, b"".join(keep), h.hexdigest(), total
        finally:
            conn.close()
    send.peer = None
    return send


def _peer_of(send, host: str) -> dict:
    """The issuer fields of the call just made on ``send`` - its peer's when the wire says one for that host, else None
    (a wire that says nothing: never guessed)."""
    peer = getattr(send, "peer", None) or {}
    ok = peer.get("host") == host
    return {"issuer_o": peer.get("issuer_o") if ok else None, "issuer_cn": peer.get("issuer_cn") if ok else None}


def _blob(call, *, registry: str, repo: str, digest: str, size, authz: dict, cdn: frozenset, dest: Path, blob_max: int,
          out: dict) -> None:
    """One blob by digest: the registry answers it or redirects it once, to a declared CDN host only, which gets no
    Authorization (Q-C4-2, Q-C4-3); the file on the disk must be the digest's bytes and the manifest's size."""
    if not _DIGEST.fullmatch(str(digest)):
        raise Refused(f"a blob digest {str(digest)[:80]!r} is not sha256:<64 hex>")
    if not (isinstance(size, int) and not isinstance(size, bool) and 0 <= size <= blob_max):
        raise Refused(f"the blob {digest[:19]} declares a size {size!r} outside 0..max_blob_bytes")
    dest.parent.mkdir(parents=True, exist_ok=True)
    st, h, _, sha, n = call("GET", registry, f"/v2/{repo}/blobs/{digest}", authz, max_bytes=blob_max, save_to=dest)
    if st in _REDIRECTS:
        target = urllib.parse.urljoin(f"https://{registry}/v2/{repo}/blobs/{digest}", h.get("location") or "")
        u = urllib.parse.urlsplit(target)
        if u.scheme != "https" or u.hostname not in cdn or u.port not in (None, 443) or u.username or u.password:
            out["refused_redirect_host"] = u.hostname
            why = ("an undeclared host" if u.hostname not in cdn else
                   "not https" if u.scheme != "https" else f"port {u.port}" if u.port not in (None, 443) else "userinfo")
            raise Refused(f"the blob {digest[:19]} redirects to {u.hostname!r} - {why} (Q-C4-3); not followed")
        st, h, _, sha, n = call("GET", u.hostname, (u.path or "/") + (f"?{u.query}" if u.query else ""), {},
                                max_bytes=blob_max, save_to=dest)      # exactly one redirect, no Authorization
        if st in _REDIRECTS:
            raise Refused(f"the blob {digest[:19]} redirects a second time - one redirect only (Q-C4-3)")
    if st != 200:
        raise Refused(f"the blob {digest[:19]} answered {st}")
    if f"sha256:{sha}" != digest or n != size:
        dest.unlink(missing_ok=True)
        raise Refused(f"the blob {digest[:19]} on the disk is not its digest's bytes or its size ({n} != {size})")


def oci_job(job: dict, *, send, cwd: Path) -> dict:
    """A8 C4: one image by the auditor's rules - see the module docstring's OCI part. Returns its summary; never raises
    for a refusal or a network error. The token never leaves memory; a signed URL's query is never recorded."""
    out: dict = {"id": f"oci:{job.get('repo')}", "kind": "oci", "ok": False, "error": None, "requests": [], "token": None}
    try:
        repo, registry, auth, service = job["repo"], job["registry"], job["auth"], job["service"]
        if not _REPO.fullmatch(str(repo)):
            raise Refused(f"{repo!r} is not a repository name")
        cdn = frozenset(job.get("cdn_hosts") or ())
        if set(job.get("hosts") or ()) != {registry, auth, *cdn}:
            raise Refused("the job's hosts are not exactly its registry, its token service and its declared CDN hosts")
        plat = job.get("platform") or {"os": "linux", "architecture": "amd64"}
        meta_max, blob_max = int(job["max_meta_bytes"]), int(job["max_blob_bytes"])

        def call(method, host, path, headers, *, max_bytes=meta_max, save_to=None, tolerate=False):
            st, h, body, sha, n = send(method, host, path, dict(headers), max_bytes=max_bytes, save_to=save_to)
            out["requests"].append({**_peer_of(send, host), "method": method, "host": host, "status": st,
                                    "path": path.split("?", 1)[0] if host in cdn else path})
            if st == 429 and not tolerate:
                out["rate_limited"] = True
                raise Refused("rate-limited: status 429")
            return st, h, body, sha, n

        scope = f"repository:{repo}:pull"
        st, _, body, _, _ = call("GET", auth, f"/token?service={urllib.parse.quote(service)}&scope={urllib.parse.quote(scope)}", {})
        if st != 200:
            raise Refused(f"the token service answered {st}")
        tok = json.loads(body)
        token = tok.get("token") or tok.get("access_token")
        if not isinstance(token, str) or not token:
            raise Refused("the token service gave no token")
        out["token"] = {"value": "<redacted>", "scope": scope, "expires_in": tok.get("expires_in")}
        authz = {"Authorization": f"Bearer {token}"}
        tags: list = []
        path, pages = f"/v2/{repo}/tags/list?n=1000", 0
        while path:
            pages += 1
            if pages > MAX_TAG_PAGES:
                raise Refused(f"more than {MAX_TAG_PAGES} pages of tags")
            st, h, body, _, _ = call("GET", registry, path, authz)
            if st != 200:
                raise Refused(f"the tags list answered {st}")
            tags += json.loads(body).get("tags") or []
            path = _next_link(h.get("link"), registry, repo)
        out["tags_seen"], out["tag_pages"] = len(tags), pages
        accept = {"Accept": ", ".join(OCI_INDEX + OCI_MANIFEST)}
        # C4A-5 (the auditor): the digest behind "latest" in the same minute as the tags - information only, so a
        # non-200 there is recorded and never ends the job
        st, _h, lbody, _, _ = call("GET", registry, f"/v2/{repo}/manifests/latest", {**authz, **accept}, tolerate=True)
        out["latest_status"] = st
        out["latest_digest"] = ("sha256:" + hashlib.sha256(lbody).hexdigest()) if st == 200 else None
        pick = release_tags(tags)
        if pick is None:
            raise Refused("no tag is a release ^v?X.Y.Z$")
        key, variants = pick
        got: dict = {}
        for t in variants:
            st, h, body, _, _ = call("GET", registry, f"/v2/{repo}/manifests/{t}", {**authz, **accept})
            if st != 200:
                raise Refused(f"the manifest of {t} answered {st}")
            d = "sha256:" + hashlib.sha256(body).hexdigest()
            if h.get("docker-content-digest") and h["docker-content-digest"] != d:
                raise Refused(f"the registry's digest of {t} is not its bytes' digest")
            got[t] = (d, body, h.get("content-type"))
        if len({d for d, _b, _c in got.values()}) != 1:
            raise Refused(f"the tags {variants} of one version resolve to different indexes "
                          f"{ {t: v[0][:19] for t, v in got.items()} } - refused (Q-C4-1)")
        tag_digest, tag_body, ctype = got[variants[0]]
        doc = json.loads(tag_body)
        tag_type = doc.get("mediaType") or ctype
        single = tag_type in OCI_MANIFEST             # C4A-6: a plain image manifest, checked on its config below
        if single:
            index_digest, mdig, mbody, man, mtype = None, tag_digest, tag_body, doc, tag_type
        elif tag_type in OCI_INDEX:
            index_digest = tag_digest
            cands = [m for m in doc.get("manifests") or []
                     if (m.get("platform") or {}).get("os") == plat["os"]
                     and (m.get("platform") or {}).get("architecture") == plat["architecture"]
                     and not (m.get("platform") or {}).get("variant")]
            if len(cands) != 1:
                raise Refused(f"the index of {variants[0]} holds {len(cands)} {plat['os']}/{plat['architecture']} manifests, "
                              f"not one - attempt 2 is Q-A8-4's (the newest installable release), never picked by hand")
            mdig = cands[0].get("digest")
            if not _DIGEST.fullmatch(str(mdig)):
                raise Refused("the platform manifest's digest is not sha256:<64 hex>")
            st, h, mbody, _, _ = call("GET", registry, f"/v2/{repo}/manifests/{mdig}",
                                      {**authz, "Accept": ", ".join(OCI_MANIFEST)})
            if st != 200 or "sha256:" + hashlib.sha256(mbody).hexdigest() != mdig:
                raise Refused("the platform manifest is not its digest's bytes")
            man = json.loads(mbody)
            mtype = man.get("mediaType") or h.get("content-type")
        else:
            raise Refused(f"{variants[0]} names neither an image index nor an image manifest ({tag_type}) - attempt 2 "
                          f"is Q-A8-4's")
        if mtype not in OCI_MANIFEST:
            raise Refused(f"the platform manifest is a {mtype}, not an image manifest")
        oci = cwd / "oci"
        blobs = oci / "blobs" / "sha256"
        blobs.mkdir(parents=True, exist_ok=True)
        (blobs / mdig.split(":", 1)[1]).write_bytes(mbody)
        cfg, layers = man["config"], list(man["layers"])
        _blob(call, registry=registry, repo=repo, digest=cfg.get("digest"), size=cfg.get("size"), authz=authz, cdn=cdn,
              dest=blobs / str(cfg.get("digest")).split(":", 1)[-1], blob_max=blob_max, out=out)
        if single:                                  # C4A-6: its platform is its config's - before any layer
            conf = json.loads((blobs / cfg["digest"].split(":", 1)[1]).read_bytes())
            if (conf.get("os"), conf.get("architecture")) != (plat["os"], plat["architecture"]) or conf.get("variant"):
                raise Refused(f"the image manifest of {variants[0]} is {conf.get('os')}/{conf.get('architecture')}"
                              f"{'/' + conf['variant'] if conf.get('variant') else ''}, not {plat['os']}/"
                              f"{plat['architecture']} - attempt 2 is Q-A8-4's")
        for b in layers:
            _blob(call, registry=registry, repo=repo, digest=b.get("digest"), size=b.get("size"), authz=authz, cdn=cdn,
                  dest=blobs / str(b.get("digest")).split(":", 1)[-1], blob_max=blob_max, out=out)
        out.update(ok=True, repo=repo, version=".".join(str(x) for x in key), tag=variants[0], tag_variants=variants,
                   index_digest=index_digest, single_manifest=single, manifest_digest=mdig, manifest_media_type=mtype,
                   manifest_bytes=len(mbody), config={"digest": cfg["digest"], "size": cfg["size"]},
                   layers=[{"digest": x["digest"], "size": x["size"], "mediaType": x.get("mediaType")} for x in layers])
        return out
    except (Refused, OSError, ssl.SSLError, http.client.HTTPException, ValueError, KeyError, TypeError) as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        return out


_MODEL = re.compile(r"[A-Za-z0-9_]{1,64}")
_XYZ = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_GH_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")   # G2: no "..", no leading dot


def gh_model_job(job: dict, *, send, cwd: Path) -> dict:
    """B-NLP NLP-2: one spaCy model wheel from its GitHub release - see the module docstring's gh_model part. Returns its
    summary; never raises for a refusal or a network error; a signed URL's query is never recorded."""
    out: dict = {"id": f"gh_model:{job.get('model')}", "kind": "gh_model", "ok": False, "error": None, "requests": []}
    try:
        model, spacy_v, repo = job["model"], str(job["spacy"]), job["repo"]
        raw, api, web = job["raw_host"], job["api_host"], job["web_host"]
        if not _MODEL.fullmatch(str(model)):
            raise Refused(f"{model!r} is not a model name")
        if not _GH_REPO.fullmatch(str(repo)):
            raise Refused(f"{repo!r} is not a GitHub repository name")
        m = _XYZ.fullmatch(spacy_v)
        if not m:
            raise Refused(f"the locked spaCy {spacy_v!r} is not an X.Y.Z version")
        cdn = frozenset(job.get("cdn_hosts") or ())
        if set(job.get("hosts") or ()) != {raw, api, web, *cdn}:
            raise Refused("the job's hosts are not exactly its declared hosts (raw, api, github.com and the CDN hosts)")
        meta_max, asset_max = int(job["max_meta_bytes"]), int(job["max_asset_bytes"])

        def call(method, host, path, headers, *, max_bytes=meta_max, save_to=None):
            st, h, body, sha, n = send(method, host, path, dict(headers), max_bytes=max_bytes, save_to=save_to)
            out["requests"].append({**_peer_of(send, host), "method": method, "host": host, "status": st,
                                    "path": path.split("?", 1)[0] if host in cdn else path})
            return st, h, body, sha, n

        # 1. the version: compatibility.json under the locked spaCy's major.minor (spacy.cli.download's own key)
        minor = f"{m.group(1)}.{m.group(2)}"
        st, _, body, _, _ = call("GET", raw, job["compat_path"], {})
        if st != 200:
            raise Refused(f"compatibility.json answered {st}")
        table = (json.loads(body).get("spacy") or {})
        if minor not in table:
            raise Refused(f"compatibility.json lists no spaCy {minor}")
        listed = list((table[minor] or {}).get(model) or [])
        if not listed:
            raise Refused(f"compatibility.json lists no {model} for spaCy {minor}")
        xyz = [v for v in listed if isinstance(v, str) and _XYZ.fullmatch(v)]
        if not xyz:
            raise Refused(f"compatibility.json lists no X.Y.Z version of {model} for spaCy {minor}")
        newest = max(xyz, key=lambda v: tuple(int(x) for x in v.split(".")))
        out["compat"] = {"spacy_minor": minor, "listed": listed, "first": listed[0], "newest": newest}
        if listed[0] != newest:                     # the auditor (07:4x): spacy.cli.download takes the first entry
            raise Refused(f"compatibility.json's first entry {listed[0]} is not its newest {newest} - neither is picked "
                          f"by hand")
        version = newest
        # 2. the release by its tag: exactly one wheel asset, at github.com's own download path
        tag = f"{model}-{version}"
        name = f"{tag}-py3-none-any.whl"
        st, _, body, _, _ = call("GET", api, f"/repos/{repo}/releases/tags/{tag}",
                                 {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        if st != 200:
            raise Refused(f"the release API answered {st} for {tag}")
        rel = json.loads(body)
        assets = [a for a in rel.get("assets") or [] if a.get("name") == name]
        if len(assets) != 1:
            raise Refused(f"the release {tag} holds {len(assets)} assets named {name}, not one")
        a = assets[0]
        want_path = f"/{repo}/releases/download/{tag}/{name}"
        u = urllib.parse.urlsplit(str(a.get("browser_download_url") or ""))
        if (u.scheme, u.hostname, u.port, u.path, u.query, u.username) != ("https", web, None, want_path, "", None):
            raise Refused(f"the asset's URL is not github.com's own download path {want_path}")
        size, digest = a.get("size"), a.get("digest")
        if not (isinstance(size, int) and not isinstance(size, bool) and 0 < size <= asset_max):
            raise Refused(f"the asset declares a size {size!r} outside 1..max_asset_bytes")
        if digest is not None and not _DIGEST.fullmatch(str(digest)):
            raise Refused(f"the asset's digest {str(digest)[:80]!r} is not sha256:<64 hex>")
        out.update(version=version, release={"id": rel.get("id"), "tag": tag}, tls_only=digest is None,
                   asset={"name": name, "size": size, "digest": digest, "path": want_path})
        # 3. the file: github.com answers it or redirects it once, to a declared CDN host, with no Authorization
        dest = cwd / "model" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        st, h, _, sha, n = call("GET", web, want_path, {}, max_bytes=asset_max, save_to=dest)
        out["cdn_host"] = None
        if st in _REDIRECTS:
            t = urllib.parse.urlsplit(urllib.parse.urljoin(f"https://{web}{want_path}", h.get("location") or ""))
            if t.scheme != "https" or t.hostname not in cdn or t.port not in (None, 443) or t.username or t.password:
                out["refused_redirect_host"] = t.hostname
                why = ("an undeclared host" if t.hostname not in cdn else "not https" if t.scheme != "https" else
                       f"port {t.port}" if t.port not in (None, 443) else "userinfo")
                raise Refused(f"the asset redirects to {t.hostname!r} - {why} (Q-NLP-1); not followed")
            out["cdn_host"] = t.hostname
            st, h, _, sha, n = call("GET", t.hostname, (t.path or "/") + (f"?{t.query}" if t.query else ""), {},
                                    max_bytes=asset_max, save_to=dest)
            if st in _REDIRECTS:
                raise Refused("the asset redirects a second time - one redirect only (Q-NLP-1)")
        elif st != 200:
            raise Refused(f"github.com answered {st} for the asset")
        if st != 200:
            raise Refused(f"the CDN answered {st} for the asset")
        if (digest is not None and f"sha256:{sha}" != digest) or n != size:
            dest.unlink(missing_ok=True)
            raise Refused(f"the file is not the digest's bytes or the asset's size ({n} != {size})")
        out.update(ok=True, file={"sha256": sha, "bytes": n, "path": f"model/{name}"})
        return out
    except (Refused, OSError, ssl.SSLError, http.client.HTTPException, ValueError, KeyError, TypeError, AttributeError) as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        return out


_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}")
_ASSET = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,199}")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def gh_release_job(job: dict, *, send, cwd: Path) -> dict:
    """T32: a vendor's release assets, each against the expectation the job carries - see the module docstring's
    gh_release part. Returns its summary; never raises for a refusal or a network error; a signed URL's query is never
    recorded."""
    out: dict = {"id": f"gh_release:{job.get('repo')}@{job.get('tag')}", "kind": "gh_release", "ok": False, "error": None,
                 "requests": [], "assets": []}
    try:
        repo, tag, commit = str(job["repo"]), str(job["tag"]), str(job["commit"])
        api, web = job["api_host"], job["web_host"]
        if not _GH_REPO.fullmatch(repo):
            raise Refused(f"{repo!r} is not a GitHub repository name")
        if not _TAG.fullmatch(tag) or ".." in tag:
            raise Refused(f"{tag!r} is not a tag name")
        if not _COMMIT.fullmatch(commit):
            raise Refused(f"{commit!r} is not a 40-hex commit")
        cdn = frozenset(job.get("cdn_hosts") or ())
        hosts = list(job.get("hosts") or ())
        if set(hosts) != {api, web, *cdn} or len(hosts) != len({api, web, *cdn}):
            raise Refused("the job's hosts are not exactly its declared hosts (api, github.com and the CDN hosts)")
        meta_max, asset_max = int(job["max_meta_bytes"]), int(job["max_asset_bytes"])
        expect = list(job.get("assets") or ())
        if not expect:
            raise Refused("the job expects no asset")
        names = [str(e.get("name")) for e in expect]
        if len(set(names)) != len(names):
            raise Refused("the job expects an asset name twice")
        for e in expect:
            name, size, digest = str(e.get("name")), e.get("size"), e.get("digest")
            if not _ASSET.fullmatch(name) or ".." in name:
                raise Refused(f"{name!r} is not an asset name")
            if not _DIGEST.fullmatch(str(digest)):
                raise Refused(f"the expected digest of {name} {str(digest)[:80]!r} is not sha256:<64 hex>")
            if not (isinstance(size, int) and not isinstance(size, bool) and 0 < size <= asset_max):
                raise Refused(f"the expected size of {name} {size!r} is outside 1..max_asset_bytes")

        def call(method, host, path, headers, *, max_bytes=meta_max, save_to=None):
            st, h, body, sha, n = send(method, host, path, dict(headers), max_bytes=max_bytes, save_to=save_to)
            entry = {**_peer_of(send, host), "method": method, "host": host, "status": st, "path": path}
            if host in cdn:                      # a signed URL: its host and path, and its query only as a sha256
                bare, _, query = path.partition("?")
                entry.update(path=bare, query_sha256=hashlib.sha256(query.encode("utf-8")).hexdigest())
            out["requests"].append(entry)
            return st, h, body, sha, n

        gh = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        # 1. the tag still names the expected commit
        st, _, body, _, _ = call("GET", api, f"/repos/{repo}/commits/{tag}", gh)
        if st != 200:
            raise Refused(f"the commit API answered {st} for {tag}")
        got = json.loads(body).get("sha")
        if got != commit:
            raise Refused(f"the tag {tag} names commit {got}, not the expected {commit}")
        out["commit"] = commit
        # 2. the release by its tag: that tag, published, one asset of each expected name, exactly as expected
        st, _, body, _, _ = call("GET", api, f"/repos/{repo}/releases/tags/{tag}", gh)
        if st != 200:
            raise Refused(f"the release API answered {st} for {tag}")
        rel = json.loads(body)
        if rel.get("tag_name") != tag:
            raise Refused(f"the release names the tag {rel.get('tag_name')!r}, not {tag!r}")
        if rel.get("draft") is not False:
            raise Refused(f"the release {tag} is a draft (draft {rel.get('draft')!r})")
        if rel.get("prerelease") is not False:
            raise Refused(f"the release {tag} is a prerelease (prerelease {rel.get('prerelease')!r})")
        out["release"] = {"id": rel.get("id"), "tag": tag, "draft": False, "prerelease": False,
                          "published_at": rel.get("published_at")}
        plan = []
        for e in expect:
            name = e["name"]
            found = [a for a in rel.get("assets") or [] if a.get("name") == name]
            if len(found) != 1:
                raise Refused(f"the release {tag} holds {len(found)} assets named {name}, not one")
            a = found[0]
            want_path = f"/{repo}/releases/download/{tag}/{name}"
            u = urllib.parse.urlsplit(str(a.get("browser_download_url") or ""))
            if (u.scheme, u.hostname, u.port, u.path, u.query, u.username) != ("https", web, None, want_path, "", None):
                raise Refused(f"the asset's URL is not github.com's own download path {want_path}")
            if a.get("size") != e["size"]:
                raise Refused(f"the release's {name} declares size {a.get('size')!r}, not the expected {e['size']}")
            if a.get("digest") != e["digest"]:
                raise Refused(f"the release's {name} declares digest {a.get('digest')}, not the expected {e['digest']}")
            plan.append((name, e["size"], e["digest"], want_path))
        # 3. each file: github.com answers it or redirects it once, to a declared CDN host, with no Authorization
        for name, size, digest, want_path in plan:
            dest = cwd / "release" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            st, h, _, sha, n = call("GET", web, want_path, {}, max_bytes=asset_max, save_to=dest)
            cdn_host = None
            if st in _REDIRECTS:
                t = urllib.parse.urlsplit(urllib.parse.urljoin(f"https://{web}{want_path}", h.get("location") or ""))
                if t.scheme != "https" or t.hostname not in cdn or t.port not in (None, 443) or t.username or t.password:
                    out["refused_redirect_host"] = t.hostname
                    why = ("an undeclared host" if t.hostname not in cdn else "not https" if t.scheme != "https" else
                           f"port {t.port}" if t.port not in (None, 443) else "userinfo")
                    raise Refused(f"{name} redirects to {t.hostname!r} - {why}; not followed")
                cdn_host = t.hostname
                st, h, _, sha, n = call("GET", t.hostname, (t.path or "/") + (f"?{t.query}" if t.query else ""), {},
                                        max_bytes=asset_max, save_to=dest)
                if st in _REDIRECTS:
                    raise Refused(f"{name} redirects a second time - one redirect only")
            elif st != 200:
                raise Refused(f"github.com answered {st} for {name}")
            if st != 200:
                raise Refused(f"the CDN answered {st} for {name}")
            if f"sha256:{sha}" != digest or n != size:
                dest.unlink(missing_ok=True)
                raise Refused(f"{name} is not the digest's bytes or the expected size ({n} != {size})")
            out["assets"].append({"name": name, "size": size, "digest": digest, "cdn_host": cdn_host,
                                  "file": {"sha256": sha, "bytes": n, "path": f"release/{name}"}})
        out["ok"] = True
        return out
    except (Refused, OSError, ssl.SSLError, http.client.HTTPException, ValueError, KeyError, TypeError, AttributeError) as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        return out


def main() -> int:
    job = json.loads(sys.stdin.readline() or "{}")
    cwd = Path.cwd()
    try:
        port = catcher_port()
        if job.get("kind") == "oci":                     # A8 C4: one image, by the auditor's Q-C4-1..3
            results = [oci_job(job, send=tunnel_send(port), cwd=cwd)]
        elif job.get("kind") == "gh_model":              # B-NLP NLP-2: one spaCy model, by the auditor's rule
            results = [gh_model_job(job, send=tunnel_send(port), cwd=cwd)]
        elif job.get("kind") == "gh_release":            # T32: a vendor's release binary, by the record's expectation
            results = [gh_release_job(job, send=tunnel_send(port), cwd=cwd)]
        else:
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
