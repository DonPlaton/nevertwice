#!/usr/bin/env python3
"""PREREG-V3 (the auditor's Q-47-8g GO): research/v3/fetch_npm_a7.py - window a7-npm, the supermemory-server@0.0.8
tarball, download only - offline, with real processes under the contract: the catcher-only proxy, two fetch-child
jobs, a fake registry.npmjs.org behind a fake hop on a local TLS server (a throwaway certificate whose issuer
organisation is a public one; the test hands the children its CA file - the one thing a real window never passes).

* clean: the registry document, then the tarball at the canonical URL; its sha512 equals the document's integrity and
  its sha1 the shasum; tarball and document placed under <runs>/_pins/npm/ with npm_pin.json (every digest, the
  install scripts as data, the window record's sha256); only the window's host was tunnelled;
* the document is data, not directions: a tarball URL other than the canonical one, another version, a malformed
  integrity or shasum - no tarball is requested, the reason is named, nothing is placed;
* a tarball whose bytes are not the registry's integrity is not placed; an issuer outside the public set is a P3;
  a second placement over an existing pin refuses;
* read_document and digests, table-tested in process.

    python tests/_test_v3_fetch_npm_a7.py
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite
import _tls_fake as TF  # noqa: E402

_TEST_EXC = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    _TEST_EXC[sys._base_executable] = "the test interpreter's base"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


L = _load("v3_launch_npm", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_npm", ROOT / "research" / "v3" / "fetch_a3.py")
P = _load("v3_fetch_pins_npm", ROOT / "research" / "v3" / "fetch_pins_a3.py")
N = _load("v3_fetch_npm_a7", ROOT / "research" / "v3" / "fetch_npm_a7.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_npm_a7_"))
HOST = "registry.npmjs.org"
made = TF.make_test_cert(TMP / "cert", HOST, extra_hosts=("evil.example",), org="Google Trust Services")
if made is None:
    print("  SKIP the window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 fetch npm a7: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)

TARBALL = b"\x1f\x8b fake tarball bytes of supermemory-server 0.0.8 " * 50


def sri(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")


def document(**over) -> dict:
    d = {"name": "supermemory-server", "version": "0.0.8",
         "scripts": {"postinstall": "node scripts/setup.js", "test": "vitest"},
         "dependencies": {"hono": "^4.0.0"}, "engines": {"node": ">=20"},
         "dist": {"tarball": "https://registry.npmjs.org/supermemory-server/-/supermemory-server-0.0.8.tgz",
                  "integrity": sri(TARBALL), "shasum": hashlib.sha1(TARBALL).hexdigest(), "fileCount": 12,
                  "unpackedSize": 4096}}
    d.update(over)
    return d


DOC_PATH = "/supermemory-server/0.0.8"
TGZ_PATH = "/supermemory-server/-/supermemory-server-0.0.8.tgz"
routes: dict = {}


def serve(doc: dict, tarball: bytes = TARBALL) -> None:
    routes.clear()
    routes[DOC_PATH] = (200, [("Content-Type", "application/json")], json.dumps(doc).encode("utf-8"))
    routes[TGZ_PATH] = (200, [("Content-Type", "application/octet-stream")], tarball)


srv = TF.TlsHttpServer(made[0], made[1], routes)
hop = TF.TunnelHop(srv.port)


class AnySampler:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


def contract(tag):
    base = TMP / tag
    (base / "watched").mkdir(parents=True, exist_ok=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    return L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                      owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                      conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                      system_dirs=(Path(sys.executable).parent,)), base


def window(tag: str, run: str = "g1", **kw):
    c, base = contract(tag)
    n0 = len(srv.heads)
    res = N.run_npm_window(c, L, F, P, run=run, python=Path(sys.executable), via_port=hop.port, parent_env=os.environ,
                           native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
                           fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]),
                           child_env_extra={"SSL_CERT_FILE": str(made[0])}, volume=TMP, **kw)
    heads = [h.split(b" ")[1].decode() for h in srv.heads[n0:]]
    return res, c, heads


def named(problems: list, code: str) -> bool:
    return any(p.startswith(code) or f" {code} " in p or p.startswith(f"P1 window_not_clean: {code}") for p in problems)


try:
    print("\n- the clean window: document, tarball, verified, placed -")
    serve(document())
    res, c, heads = window("clean")
    pin = res.get("pin") or {}
    npm_dir = c.runs_root / "_pins" / "npm"
    check("the window is clean and the pin is written", res.get("problems") == [] and bool(pin), str(res)[:400])
    check("exactly two requests: the version document, then the canonical tarball URL", heads == [DOC_PATH, TGZ_PATH],
          str(heads))
    check("the pin carries the registry's sha512 integrity and sha1 shasum, and the sha256 and size measured",
          pin.get("integrity") == sri(TARBALL) and pin.get("shasum") == hashlib.sha1(TARBALL).hexdigest()
          and pin.get("sha256") == hashlib.sha256(TARBALL).hexdigest() and pin.get("size") == len(TARBALL), str(pin)[:300])
    tgz = npm_dir / "supermemory-server-0.0.8.tgz"
    check("the tarball and the registry document are placed under <runs>/_pins/npm/",
          tgz.is_file() and tgz.read_bytes() == TARBALL and (npm_dir / "supermemory-server-0.0.8.json").is_file())
    on_disk = json.loads((npm_dir / "npm_pin.json").read_text(encoding="utf-8")) if (npm_dir / "npm_pin.json").is_file() else {}
    check("npm_pin.json is the pin", on_disk == pin)
    rec_path = c.runs_root / "_fetch" / "a7-npm" / "g1" / "record.json"
    check("the pin names the window record by its sha256",
          rec_path.is_file() and pin.get("window", {}).get("record_sha256") == hashlib.sha256(rec_path.read_bytes()).hexdigest())
    check("the install scripts are recorded as data (only the install hooks), with dependencies and engines",
          pin.get("install_scripts") == {"postinstall": "node scripts/setup.js"} and pin.get("dependencies") == {"hono": "^4.0.0"}
          and pin.get("engines") == {"node": ">=20"}, str(pin.get("install_scripts")))
    rec = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.is_file() else {}
    check("only the window's host was tunnelled, and the check is complete with no hit",
          {x.get("host") for x in rec.get("catcher") or []} == {HOST} and rec.get("check", {}).get("complete")
          and rec.get("check", {}).get("native_hits") == 0, str(rec.get("check")))
    check("the issuer the child saw is recorded", pin.get("issuer", [None])[0] == "Google Trust Services", str(pin.get("issuer")))
    res2, _c2, heads2 = window("clean", run="g2")
    check("a second placement over the pin refuses (N9), nothing overwritten",
          named(res2.get("problems") or [], "N9") and tgz.read_bytes() == TARBALL, str(res2.get("problems")))

    print("\n- the document is data: no tarball unless it names exactly this version at the canonical URL -")
    for tag, doc, code in (
            ("other-url", document(dist=dict(document()["dist"], tarball="https://evil.example/x.tgz")), "N3"),
            ("other-version", document(version="0.0.9"), "N2"),
            ("bad-integrity", document(dist=dict(document()["dist"], integrity="sha256-abc")), "N4"),
            ("bad-shasum", document(dist=dict(document()["dist"], shasum="xyz")), "N5")):
        serve(doc)
        r, cc, hh = window(tag)
        check(f"{code}: {tag} - no tarball requested, the reason named, nothing placed",
              hh == [DOC_PATH] and named(r.get("problems") or [], code) and r.get("pin") is None
              and not (cc.runs_root / "_pins" / "npm" / "supermemory-server-0.0.8.tgz").exists(),
              f"{hh} {r.get('problems')}")
    serve(document(), tarball=TARBALL + b"tampered")
    r, cc, hh = window("tampered")
    check("N7 a tarball whose bytes are not the registry's integrity is not placed",
          hh == [DOC_PATH, TGZ_PATH] and named(r.get("problems") or [], "N7") and r.get("pin") is None
          and not (cc.runs_root / "_pins" / "npm").exists(), str(r.get("problems")))
    BAD = TARBALL + b"tampered"
    serve(document(dist=dict(document()["dist"], shasum=hashlib.sha1(BAD).hexdigest())), tarball=BAD)
    r, cc, hh = window("integrity-only")
    check("N7 the sha512 integrity is checked on its own (a shasum that matches does not save a wrong sha512)",
          any(p.startswith("N7 integrity_mismatch") for p in r.get("problems") or [])
          and not any(p.startswith("N7 shasum_mismatch") for p in r.get("problems") or []) and r.get("pin") is None,
          str(r.get("problems")))
    serve(document(dist=dict(document()["dist"], integrity=sri(BAD))), tarball=BAD)
    r, cc, hh = window("shasum-only")
    check("N7 the sha1 shasum is checked on its own", any(p.startswith("N7 shasum_mismatch") for p in r.get("problems") or [])
          and not any(p.startswith("N7 integrity_mismatch") for p in r.get("problems") or []) and r.get("pin") is None,
          str(r.get("problems")))
    routes.clear()
    r, cc, hh = window("no-document")
    check("N0 a document the registry does not serve: no tarball requested, named",
          hh == [DOC_PATH] and named(r.get("problems") or [], "N0") and r.get("pin") is None, str(r.get("problems")))
    serve(document())
    r, cc, hh = window("issuer", issuer_orgs=frozenset({"Amazon"}))
    check("P3 an issuer outside the public set stops placement, naming host and organisation",
          any(p.startswith("P3 issuer_not_public: registry.npmjs.org") and "Google Trust Services" in p
              for p in r.get("problems") or []) and r.get("pin") is None, str(r.get("problems")))

    print("\n- in process: read_document and digests -")
    ok = N.read_document(json.dumps(document()).encode())
    check("a good document yields its integrity, shasum and the canonical URL",
          ok["integrity"] == sri(TARBALL) and ok["tarball"] == N.TARBALL_URL)
    for label, raw, code in (("not JSON", b"<html>", "N1"), ("a list", b"[]", "N2"),
                             ("another package", json.dumps(document(name="supermemory")).encode(), "N2"),
                             ("no dist", json.dumps({k: v for k, v in document().items() if k != "dist"}).encode(), "N3"),
                             ("a sha512 SRI of the wrong length",
                              json.dumps(document(dist=dict(document()["dist"], integrity="sha512-" + "A" * 84 + "=="))).encode(), "N4"),
                             ("an uppercase shasum",
                              json.dumps(document(dist=dict(document()["dist"], shasum="A" * 40))).encode(), "N5")):
        try:
            N.read_document(raw)
            got = "no stop"
        except N.Stop as e:
            got = str(e)
        check(f"read_document stops on {label} ({code})", got.startswith(code), got)
    j1 = N.doc_job()
    reasons: list = []
    fake_unit = TMP / "fake_unit"
    (fake_unit / "npm").mkdir(parents=True)
    (fake_unit / N.DOC_SAVE).write_bytes(json.dumps(document()).encode())
    j2 = N.tarball_job([{"unit": str(fake_unit), "summary": [{"id": "npm:document", "ok": True}]}], reasons)
    check("both jobs: the one host, no redirect, no child-side expectation, the canonical URLs, bounded sizes",
          all(j["hosts"] == [HOST] and j["max_redirects"] == 0 and len(j["requests"]) == 1
              and j["requests"][0]["expect"] is None and j["requests"][0]["method"] == "GET" for j in (j1, j2))
          and j1["requests"][0]["url"] == f"https://{HOST}{DOC_PATH}" and j2["requests"][0]["url"] == f"https://{HOST}{TGZ_PATH}"
          and j2["requests"][0]["max_bytes"] == 64 << 20 and reasons == [], json.dumps([j1, j2])[:300])
    (fake_unit / N.TARBALL_SAVE).write_bytes(TARBALL)
    good = {"id": "npm:tarball", "ok": True, "sha256": hashlib.sha256(TARBALL).hexdigest(), "bytes": len(TARBALL)}
    fake_rec = {"run": "x", "jobs": [{"unit": str(fake_unit), "summary": [{"id": "npm:document", "ok": True}]},
                                     {"unit": str(fake_unit), "summary": [dict(good, sha256="0" * 64)]}]}
    rp = TMP / "fake_record.json"
    rp.write_bytes(b"{}")
    pin8, probs8 = N.place(fake_rec, rp, TMP / "pins8", precheck_problems=[])
    check("N8 a file on disk that is not the one the child hashed is not placed",
          pin8 is None and any(p.startswith("N8") for p in probs8) and not (TMP / "pins8").exists(), str(probs8))
    fake_rec["jobs"][1]["summary"] = [good]
    real_copy = N.shutil.copyfile
    N.shutil.copyfile = lambda s, d: Path(d).write_bytes(Path(s).read_bytes() + b"x")
    try:
        pin10, probs10 = N.place(fake_rec, rp, TMP / "pins10", precheck_problems=[])
    finally:
        N.shutil.copyfile = real_copy
    check("N10 a placed copy that differs from the verified file is removed, not pinned",
          pin10 is None and probs10 == ["N10 placed_copy_differs"]
          and not (TMP / "pins10" / "npm" / "supermemory-server-0.0.8.tgz").exists()
          and not (TMP / "pins10" / "npm" / "npm_pin.json").exists(), str(probs10))
    pin_ok, probs_ok = N.place(fake_rec, rp, TMP / "pins_ok", precheck_problems=["P1 window_not_clean: x"])
    check("a precheck problem alone keeps the pin from being written", pin_ok is None and probs_ok[0].startswith("P1")
          and not (TMP / "pins_ok").exists(), str(probs_ok))
    f = TMP / "d.bin"
    f.write_bytes(TARBALL)
    d = N.digests(f)
    check("digests: the SRI sha512, sha1, sha256 and size of a file",
          d == {"integrity": sri(TARBALL), "shasum": hashlib.sha1(TARBALL).hexdigest(),
                "sha256": hashlib.sha256(TARBALL).hexdigest(), "size": len(TARBALL)})
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 fetch npm a7: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
