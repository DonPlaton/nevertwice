#!/usr/bin/env python3
"""PREREG-V3 R5 and the auditor's amended Q1 (2026-09-26): the py-base-314 bootstrap window.

The launch contract spawns only interpreters under the polygon (B1, R5). So before the first spawn that needs one
(A2.5), the harness - running OUTSIDE the contract on C:\\Python314 with psutil - fetches CPython 3.14.4 from the
official NuGet "python" package and unpacks its tools/ directory into D:\\Coding\\_nevertwice_polygon\\py314. A declared
ordering deviation (bootstrap), not a boundary change.

* One declared window, ``py-base-314``, hosts [api.nuget.org], its hop named in the START record. The fetch goes
  through the owner's loopback HTTP proxy explicitly (R4): a CONNECT tunnel to 127.0.0.1:<port>, the port read from
  the one declared config value (<runs>\\_config\\network.json, launch.network_via_port), never from the environment.
  TLS is verified end to end with ssl.create_default_context(); the peer certificate's issuer is recorded.
* Self-witnessed: a native egress witness with this process as its tree root (no job object: the harness is never
  put in a kill-on-close job) and the file-system witness over the fixed watched set, minus py314 itself - the
  window's install target. The hop process (the listener on the via port) is allowed only inside the window and
  revoked at its END, after the tunnel is closed.
* Only api.nuget.org, only https, only 200, only identity encoding - no redirect is followed. The registration leaf
  names the catalog entry and the package; the catalog entry's SHA512 packageHash must match the package's bytes.
  Declared limit: the hash and the package come from the same host, and the NuGet signature is not verified.
* tools/ is unpacked into a fresh directory (no path may leave it), atomically. Recorded: sha256 of the .nupkg, of
  tools/python.exe, and one sha256 over the sorted (relpath, sha256) list.
* Nothing from the package runs except checks, isolated (-I) and without bytecode (-B, PYTHONDONTWRITEBYTECODE=1):
  its version, ``import ensurepip, venv``, and a throwaway ``-m venv --without-pip`` under <runs>\\_tools, removed
  after. The tools directory's listing must be the same before and after the checks.

    python research/v3/fetch_py_base.py [--version 3.14.4]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import io
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.parse
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
NUGET_HOST = "api.nuget.org"
WINDOW = "py-base-314"
DEFAULT_VERSION = "3.14.4"
REGISTRATION = "https://api.nuget.org/v3/registration5-semver1/python/{version}.json"


class FetchRefused(RuntimeError):
    """Something the window's rules do not allow: never retried, never worked around."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class NugetClient:
    """GETs from api.nuget.org through a CONNECT tunnel on the declared hop, TLS verified end to end."""

    def __init__(self, via_port: int, *, ssl_context: ssl.SSLContext | None = None, timeout: float = 120.0):
        self.ctx = ssl_context or ssl.create_default_context()
        self.conn = http.client.HTTPSConnection("127.0.0.1", via_port, context=self.ctx, timeout=timeout)
        self.conn.set_tunnel(NUGET_HOST, 443)
        self.peer: dict | None = None
        self.requests: list[str] = []

    def get(self, url: str) -> bytes:
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" or u.hostname != NUGET_HOST or u.port not in (None, 443) or u.username:
            raise FetchRefused("only https://api.nuget.org/ is allowed in this window")
        self.requests.append(u.path)
        self.conn.request("GET", u.path + (f"?{u.query}" if u.query else ""),
                          headers={"User-Agent": "nvt3-py-base/1", "Accept-Encoding": "identity"})
        r = self.conn.getresponse()
        body = r.read()
        if self.peer is None and isinstance(self.conn.sock, ssl.SSLSocket):
            cert = self.conn.sock.getpeercert()
            issuer = dict(x[0] for x in cert.get("issuer", ()))
            self.peer = {"issuer_dn": [list(rdn) for rdn in cert.get("issuer", ())],
                         "issuer_o": issuer.get("organizationName"), "issuer_cn": issuer.get("commonName"),
                         "subject_cn": dict(x[0] for x in cert.get("subject", ())).get("commonName"),
                         "not_after": cert.get("notAfter"), "tls_version": self.conn.sock.version()}
        if r.status != 200:
            raise FetchRefused(f"{u.path} answered {r.status}; no redirect is followed")
        if (r.getheader("Content-Encoding") or "identity").lower() != "identity":
            raise FetchRefused(f"{u.path} came back encoded")
        return body

    def close(self) -> None:
        self.conn.close()


def fetch_package(client: NugetClient, version: str) -> tuple[bytes, dict]:
    """Registration leaf -> catalog entry (SHA512 packageHash) -> package; the bytes must match the hash."""
    leaf = json.loads(client.get(REGISTRATION.format(version=version)))
    cat_url, pkg_url = leaf.get("catalogEntry"), leaf.get("packageContent")
    if not isinstance(cat_url, str) or not isinstance(pkg_url, str):
        raise FetchRefused("the registration leaf names no catalog entry or package")
    cat = json.loads(client.get(cat_url))
    if str(cat.get("id", "")).lower() != "python" or cat.get("version") != version:
        raise FetchRefused("the catalog entry is not python " + version)
    if cat.get("packageHashAlgorithm") != "SHA512" or not cat.get("packageHash"):
        raise FetchRefused("the catalog entry carries no SHA512 packageHash")
    expected = base64.b64decode(cat["packageHash"])
    nupkg = client.get(pkg_url)
    if hashlib.sha512(nupkg).digest() != expected:
        raise FetchRefused("the package's SHA512 does not match the catalog's packageHash")
    return nupkg, {"registration": REGISTRATION.format(version=version), "catalog_entry": cat_url,
                   "package": pkg_url, "sha512_b64": cat["packageHash"], "sha512_verified": True,
                   "nupkg_sha256": _sha256(nupkg), "nupkg_bytes": len(nupkg)}


def unpack_tools(nupkg: bytes, dest: Path) -> list[tuple[str, str]]:
    """tools/ into a fresh ``dest`` (written to a sibling .partial, then renamed): [(relpath, sha256)], sorted.
    An entry that would leave the directory refuses the whole package."""
    if dest.exists():
        raise FetchRefused("the destination already exists: a base is unpacked once, fresh")
    part = dest.with_name(dest.name + ".partial")
    if part.exists():
        shutil.rmtree(part)
    out: list[tuple[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(nupkg)) as z:
        entries = []
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if not name.startswith("tools/") or name.endswith("/"):
                continue
            rel = PurePosixPath(name[len("tools/"):])
            if rel.is_absolute() or ".." in rel.parts or any(":" in p for p in rel.parts) or not rel.parts:
                raise FetchRefused("a package entry would leave the tools directory")
            entries.append((info, rel))
        try:
            for info, rel in entries:
                target = part.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                data = z.read(info)
                target.write_bytes(data)
                out.append((rel.as_posix(), _sha256(data)))
        except BaseException:
            shutil.rmtree(part, ignore_errors=True)
            raise
    os.replace(part, dest)
    return sorted(out)


def tree_digest(files: list[tuple[str, str]]) -> str:
    """One sha256 over the sorted (relpath, sha256) list."""
    return _sha256("\n".join(f"{rel}\0{h}" for rel, h in sorted(files)).encode("utf-8"))


def _listing(root: Path) -> list[tuple[str, int]]:
    return sorted((p.relative_to(root).as_posix(), p.stat().st_size) for p in root.rglob("*") if p.is_file())


def run_checks(tools: Path, work: Path, *, runner=subprocess.run) -> dict:
    """The only things run from the package: its version, the two imports, a throwaway venv. Isolated, no bytecode;
    the tools directory must list the same before and after."""
    py = tools / "python.exe"
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    env = {"SystemRoot": sysroot, "PATH": str(Path(sysroot) / "System32"), "PYTHONDONTWRITEBYTECODE": "1",
           "TEMP": str(work), "TMP": str(work)}
    before = _listing(tools)
    venv_dir = work / "venvcheck"
    out: dict = {"python": str(py)}

    def run(args: list[str]):
        return runner([str(py), "-I", "-B", *args], env=env, cwd=str(work), capture_output=True, text=True,
                      timeout=300)

    r = run(["-c", "import sys; print(sys.version)"])
    out["version"] = (r.stdout or "").strip() if r.returncode == 0 else None
    r = run(["-c", "import ensurepip, venv; print(ensurepip.version())"])
    out["ensurepip_venv_import"] = r.returncode == 0
    out["bundled_pip"] = (r.stdout or "").strip() if r.returncode == 0 else None
    r = run(["-m", "venv", "--without-pip", str(venv_dir)])
    cfg = venv_dir / "pyvenv.cfg"
    home = None
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip().lower() == "home":
                home = v.strip()
    out["venv_ok"] = (r.returncode == 0 and (venv_dir / "Scripts" / "python.exe").is_file()
                      and home is not None and os.path.normcase(os.path.abspath(home)) == os.path.normcase(str(tools)))
    out["venv_stderr_tail"] = (r.stderr or "")[-300:] if r.returncode != 0 else ""
    shutil.rmtree(venv_dir, ignore_errors=True)
    out["venvcheck_removed"] = not venv_dir.exists()
    out["tools_unchanged_by_checks"] = _listing(tools) == before
    return out


def watch_specs(c, launch) -> list:
    """The fixed watched set minus py314, the window's install target (it joins the set after the window)."""
    return [s for s in launch.watched_set(c) if s.label != "polygon_py314"]


def run_window(c, launch, *, version: str, dest: Path, work: Path, via_port: int, client_factory=NugetClient,
               native=None, fs=None, runner=subprocess.run, hop_sampler=None, settle_s: float = 1.0) -> dict:
    """The whole window, self-witnessed. Returns the record (also written to ``work``/py-base-314.json)."""
    L = launch
    if work.exists():
        raise FetchRefused("the work directory already exists: pick a fresh one")
    work.mkdir(parents=True)
    specs = watch_specs(c, L)
    W = L.Witnesses(c, native=native if native is not None else L.NativeEgressWitness(jobs=None),
                    fs=fs if fs is not None else L.FsWitness(specs))
    if not W.native.register(os.getpid(), label="harness-" + WINDOW):
        raise FetchRefused("the harness's own identity could not be read")
    record: dict = {"window": WINDOW, "version": version, "via": {"host": "127.0.0.1", "port": via_port},
                    "fs_watched_excludes": sorted({s.label for s in L.watched_set(c)} - {s.label for s in specs}),
                    "dest": str(dest)}
    W.begin_check(WINDOW)
    try:
        with L.fetch_window(c, WINDOW, [NUGET_HOST], witnesses=W, via=f"127.0.0.1:{via_port}") as win:
            win.roots.add(os.getpid())
            hop = L.hop_listener_pid(via_port, sampler=hop_sampler)
            if hop is None or not W.native.allow_listener(hop, f"declared hop for window {WINDOW}"):
                raise FetchRefused("the hop's listener could not be identified")
            record["hop_pid"] = hop
            client = client_factory(via_port)
            try:
                nupkg, info = fetch_package(client, version)
                record.update(info)
                record["peer"] = client.peer
                record["requests"] = list(client.requests)
                (work / f"python.{version}.nupkg").write_bytes(nupkg)
                files = unpack_tools(nupkg, dest)
            finally:
                client.close()
                time.sleep(settle_s)                     # the tunnel's socket is gone before the allowance is
                W.native.revoke_listener(hop)
        record["files"] = len(files)
        record["python_exe_sha256"] = dict(files).get("python.exe")
        record["tools_tree_sha256"] = tree_digest(files)
        record["checks"] = run_checks(dest, work, runner=runner)
    finally:
        chk = W.end_check(WINDOW)
        record["check"] = {"complete": chk.get("complete"), "native_hits": (chk.get("native") or {}).get("hits"),
                           "loopback_hits": (chk.get("native") or {}).get("loopback_hits"),
                           "fs_hits": (chk.get("fs") or {}).get("fs_hits")}
        record["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (work / f"{WINDOW}.json").write_bytes((json.dumps(record, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return record


def _load_launch():
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("v3_launch", HERE / "launch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v3_launch"] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the py-base-314 bootstrap window (R5, amended Q1)")
    ap.add_argument("--version", default=DEFAULT_VERSION)
    args = ap.parse_args(argv)
    L = _load_launch()
    c = L.Contract.default()
    port = L.network_via_port(c)
    if port is None:
        print("no declared hop: <runs>\\_config\\network.json is missing", file=sys.stderr)
        return 2
    rec = run_window(c, L, version=args.version, dest=c.polygon_root / "py314",
                     work=c.runs_root / "_tools" / WINDOW, via_port=port)
    keep = ("sha512_verified", "nupkg_sha256", "python_exe_sha256", "tools_tree_sha256", "files", "peer", "checks",
            "check")
    print(json.dumps({k: rec.get(k) for k in keep}, indent=1))
    ok = (rec.get("sha512_verified") and rec["checks"]["venv_ok"] and rec["checks"]["tools_unchanged_by_checks"]
          and rec["check"]["complete"] and rec["check"]["native_hits"] == 0 and rec["check"]["fs_hits"] == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
