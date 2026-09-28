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
    never past ``max_bytes``, identity encoding only. Returns (status, lower-case headers, body, sha256 hex, bytes)."""
    ctx = ctx or ssl.create_default_context()

    def send(method: str, host: str, path: str, headers: dict, *, max_bytes: int, save_to: Path | None = None):
        conn = _open(host, port, ctx, timeout)
        try:
            conn.request(method, path, headers={"User-Agent": "nvt3-fetch/1", "Accept-Encoding": "identity", **headers})
            r = conn.getresponse()
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
    return send


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
            out["requests"].append({"method": method, "host": host, "status": st,
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


def main() -> int:
    job = json.loads(sys.stdin.readline() or "{}")
    cwd = Path.cwd()
    try:
        port = catcher_port()
        if job.get("kind") == "oci":                     # A8 C4: one image, by the auditor's Q-C4-1..3
            results = [oci_job(job, send=tunnel_send(port), cwd=cwd)]
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
