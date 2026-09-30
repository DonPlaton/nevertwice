#!/usr/bin/env python3
"""PREREG-V3 A7 phase 2 (the auditor's R3 on Zep's LME template): research/v3/fetch_a3.py plan d6 - the a7-arxiv window,
metadata only - offline, with real processes under the contract (the catcher-only proxy, one fetch-child job, a fake
export.arxiv.org behind a fake hop on a local TLS server; the test hands the children its CA file).

* the window is the manifest's "a7-arxiv" entry, the single source: one host (export.arxiv.org), no redirect, the
  search query (the product's name in the title - never a paper id from memory) and the page size - anything else in
  the entry is refused before any spawn;
* exactly one GET: the arXiv API's query with the declared search, sorted by submission date - no link in the answer
  is followed and no paper is fetched;
* the report: the total the API names, whether the page was full, and per entry its arXiv id and version, title,
  published and updated dates, authors and its abstract-page link - as text, for the auditor to choose from; an answer
  that declares a DOCTYPE (entities) or does not parse is a problem by name and yields no entry.

Plan d8 (window a7-arxiv-src, the auditor's Q-D8-1..3 = O-a, 2026-09-30): the paper the auditor chose (arXiv 2501.13956
v1) - its OAI-PMH arXivRaw record (licence, versions) from export.arxiv.org, then, after a pause of at least 3 s, its
e-print of v1 from arxiv.org, kept as sent (never decoded or unpacked; sha256 and size), no redirect followed:

* the manifest's "a7-arxiv-src" entry is the single source - both hosts exactly, the new-style id, the version, the
  byte bound - anything else is refused before any spawn;
* the report names the licence (none is a problem), the versions (the declared one alone, else a problem), the
  source's kind from its first bytes (tar.gz, tar, gzip of one file with its name and first line; a PDF only or
  anything else is a problem), and a DOCTYPE, an answer that is not OAI-PMH, an OAI error, another paper, a redirect
  (its host named, not followed), a 429 (nothing more sent) and a saved file that is not its summary's, by name.

    python tests/_test_v3_fetch_a7_arxiv.py
"""
from __future__ import annotations

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


L = _load("v3_launch_d6", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_d6", ROOT / "research" / "v3" / "fetch_a3.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def holds(fn):
    """(value, None) or (None, the exception named) - a call that raises FAILs its own row, never the suite."""
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


MANI = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))
REAL = MANI["windows"].get("a7-arxiv") or {}
print("- the manifest's a7-arxiv entry (the auditor's R3) -")
decl, derr = holds(lambda: F.d6_decl(MANI))
check("the manifest declares a7-arxiv as plan d6 reads it: export.arxiv.org only, no redirect, the search 'ti:zep' (the "
      "product's name in the title) and a page of 50", derr is None and decl["hosts"] == ["export.arxiv.org"]
      and decl["max_redirects"] == 0 and decl["search_query"] == "ti:zep" and decl["max_results"] == 50, str(derr or decl))
bad_cases = {
    "an extra key": {**REAL, "id_list": "2501.00001"},
    "another host": {**REAL, "hosts": ["export.arxiv.org", "arxiv.org"]},
    "a redirect": {**REAL, "max_redirects": 1},
    "a query with an ampersand": {**REAL, "search_query": "ti:zep&max_results=1000"},
    "an empty query": {**REAL, "search_query": ""},
    "a page of 0": {**REAL, "max_results": 0},
    "a page over 100": {**REAL, "max_results": 500},
}
refused = {}
for label, entry in bad_cases.items():
    _v, e = holds(lambda entry=entry: F.d6_decl({"windows": {"a7-arxiv": entry}}))
    refused[label] = bool(e) and e.startswith("D6ManifestError")
check("DF: an entry with anything but the declared shape is refused by name (D6ManifestError) - "
      + ", ".join(bad_cases), all(refused.values()), str([k for k, v in refused.items() if not v]))

TMP = Path(tempfile.mkdtemp(prefix="nvt3_d6_"))
AX = "export.arxiv.org"
made = TF.make_test_cert(TMP / "cert", AX, org="Let's Encrypt")
if made is None:
    print("  SKIP the window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 fetch a7 arxiv: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)

DECL = {"hosts": ["export.arxiv.org"], "purpose": "test", "search_query": "ti:zep", "max_results": 2, "max_redirects": 0}
Q = "/api/query?search_query=ti:zep&start=0&max_results=2&sortBy=submittedDate&sortOrder=ascending"
ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>3</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2501.00001v2</id>
    <updated>2025-01-20T10:00:00Z</updated>
    <published>2025-01-10T09:00:00Z</published>
    <title>Zep: A Temporal
      Memory Layer</title>
    <author><name>A. Author</name></author><author><name>B. Author</name></author>
    <link href="http://arxiv.org/abs/2501.00001v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2501.00001v2" rel="related" type="application/pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/hep-th/9901001v1</id>
    <updated>1999-01-01T00:00:00Z</updated>
    <published>1999-01-01T00:00:00Z</published>
    <title>Zeppelins in string theory</title>
    <author><name>C. Author</name></author>
    <link href="http://arxiv.org/abs/hep-th/9901001v1" rel="alternate" type="text/html"/>
  </entry>
</feed>
"""


class AnySampler:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


def window(run: str, body: bytes):
    srv = TF.TlsHttpServer(made[0], made[1], {Q: (200, [("Content-Type", "application/atom+xml")], body)})
    hop = TF.TunnelHop(srv.port)
    base = TMP / run
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    jobs, jerr = holds(lambda: F.d6_jobs(DECL))
    rec, rerr = holds(lambda: F.run_child_window(
        c, L, window="a7-arxiv", hosts=F.D6_HOSTS, jobs=jobs, python=Path(sys.executable), via_port=hop.port, run=run,
        parent_env=os.environ, native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
        fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), child_env_extra={"SSL_CERT_FILE": str(made[0])},
        volume=TMP)) if jerr is None else (None, jerr)
    asked = [h.split(b" ")[1].decode() for h in srv.heads]
    rep, perr = holds(lambda: F.d6_report(rec, DECL)) if rec is not None else (None, rerr)
    for x in (hop, srv):
        try:
            x.close()
        except Exception:  # noqa: BLE001
            pass
    return asked, rec, rep, perr


try:
    print("\n- the window, offline -")
    asked, rec, rep, perr = window("t1", ATOM)
    check("D6-1: exactly one GET - the API query with the declared search, one page, oldest first; no link followed",
          perr is None and asked == [Q], f"{perr} {asked}")
    rep = rep or {}
    ents = rep.get("entries") or []
    check("D6-2: the report names the API's total and that the page was full (2 of 2 asked, 3 in all)",
          rep.get("total") == 3 and rep.get("page_full") is True and len(ents) == 2,
          json.dumps({k: rep.get(k) for k in ("total", "page_full")}))
    check("D6-3: each entry - its arXiv id and version apart (a new-style and an old-style id), the title with its white "
          "space collapsed, the dates, the authors and the abstract-page link, as text",
          ents[:1] == [{"id": "2501.00001", "version": 2, "title": "Zep: A Temporal Memory Layer",
                        "published": "2025-01-10T09:00:00Z", "updated": "2025-01-20T10:00:00Z",
                        "authors": ["A. Author", "B. Author"], "abs": "http://arxiv.org/abs/2501.00001v2"}]
          and ents[1:2] and ents[1]["id"] == "hep-th/9901001" and ents[1]["version"] == 1, json.dumps(ents)[:500])
    check("D6-4: the window's check is complete, no native hit, no problem", rec is not None and rec["check"]["complete"]
          and rec["check"]["native_hits"] == 0 and rec["problems"] == [] and rep.get("problems") == [],
          str((rec or {}).get("problems")) + str(rep.get("problems")))
    print("\n- answers that must not be read -")
    evil = b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaaaaaa">]><feed xmlns="http://www.w3.org/2005/Atom">&a;</feed>'
    _a2, _r2, rep2, perr2 = window("t2", evil)
    check("D6-5: an answer that declares a DOCTYPE (entities) is refused by name - no entry is read from it",
          perr2 is None and (rep2 or {}).get("entries") == [] and any("DOCTYPE" in p for p in (rep2 or {}).get("problems") or []),
          f"{perr2} {rep2}")
    _a3, _r3, rep3, perr3 = window("t3", b"<feed><entry>")
    check("D6-6: an answer that does not parse is a problem by name, no entry", perr3 is None
          and (rep3 or {}).get("entries") == [] and any("does not parse" in p for p in (rep3 or {}).get("problems") or []),
          f"{perr3} {rep3}")

    def offline(tag: str, body: bytes):
        """d6_report on a saved answer, no window: the report's reading alone."""
        u = TMP / "offline" / tag
        u.mkdir(parents=True)
        (u / "arxiv_query.xml").write_bytes(body)
        return holds(lambda: F.d6_report({"jobs": [{"index": 0, "unit": str(u)}]}, DECL))

    # the auditor (2026-09-30 00:43): a parsed answer that is not the API's Atom feed was read as an empty result -
    # entries [], problems [], total None - and Zep's row would have gone to "not measured" on a silent report
    html = [offline("html", b"<html><body>Rate exceeded.</body></html>"), offline("html_empty", b"<html/>"),
            offline("other_ns", b'<feed xmlns="http://example.org/not-atom"><entry/></feed>')]
    check("D6-6b: an answer that parses but is not the API's Atom feed (an HTML page, an empty element, a feed in another "
          "namespace) is a problem by name - no entry, no total",
          all(e is None and (r or {}).get("entries") == [] and (r or {}).get("total") is None
              and any("not an Atom feed" in p for p in (r or {}).get("problems") or []) for r, e in html), str(html)[:600])
    no_total = ATOM.replace(b"  <opensearch:totalResults>3</opensearch:totalResults>\n", b"")
    bad_total = ATOM.replace(b">3</opensearch:totalResults>", b">many</opensearch:totalResults>")
    nt = [offline("no_total", no_total), offline("bad_total", bad_total)]
    good = offline("good", ATOM)
    check("D6-6c: an Atom feed with no integer totalResults (none, or not a number) is a problem by name - its entries are "
          "still listed as text; the full feed read the same way has no problem",
          no_total != ATOM and bad_total != ATOM
          and all(e is None and len((r or {}).get("entries") or []) == 2 and (r or {}).get("total") is None
                  and any("no integer totalResults" in p for p in (r or {}).get("problems") or []) for r, e in nt)
          and good[1] is None and (good[0] or {}).get("problems") == [] and (good[0] or {}).get("total") == 3,
          str(nt)[:600] + str(good)[:200])
    # the auditor (2026-09-30 00:5x, not blocking): the API's error feed answers one entry whose id is
    # http://arxiv.org/api/errors#..., which was listed with id None and no problem
    err_feed = (b'<?xml version="1.0" encoding="UTF-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom" '
                b'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">\n'
                b'  <opensearch:totalResults>1</opensearch:totalResults>\n'
                b'  <entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_zep</id><title>Error</title>'
                b'<summary>incorrect id format for zep</summary></entry>\n</feed>\n')
    ef = offline("err_feed", err_feed)
    check("D6-6d: an entry with no arXiv id (the API's error feed, id .../api/errors#...) is a problem by name - "
          "'entry 1 has no arXiv id' - and is still listed as text",
          ef[1] is None and len((ef[0] or {}).get("entries") or []) == 1 and (ef[0] or {}).get("entries")[0]["id"] is None
          and any("entry 1 has no arXiv id" in p for p in (ef[0] or {}).get("problems") or []), str(ef)[:400])

    print("\n- main(): plan d6 only in window a7-arxiv, on its declared entry - refused before any spawn -")
    import contextlib  # noqa: E402,PLC0415
    import io  # noqa: E402,PLC0415
    from types import SimpleNamespace  # noqa: E402,PLC0415

    class _WouldSpawn(Exception):
        pass

    def run_main(argv, manifest):
        mp = TMP / "manifest_main.json"
        mp.write_text(json.dumps(manifest), encoding="utf-8")
        saved = (F._load, F.MANIFEST, F.run_child_window)
        stub_l = SimpleNamespace(Contract=SimpleNamespace(default=lambda: SimpleNamespace(runs_root=TMP)),
                                 network_via_port=lambda c_: 47999)
        F._load = lambda name, path: stub_l if name == "v3_launch" else SimpleNamespace(PINS={})
        F.MANIFEST = mp

        def no_spawn(*a_, **k_):
            raise _WouldSpawn("main reached the window")
        F.run_child_window = no_spawn
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = F.main(argv)
        except _WouldSpawn:
            rc = "spawned"
        except SystemExit as e:
            rc = f"exit {e.code}"
        finally:
            F._load, F.MANIFEST, F.run_child_window = saved
        return rc, err.getvalue()

    tail = ["--run", "t", "--python", sys.executable]
    m1 = run_main(["--window", "a7-arxiv", "--plan", "d5", *tail], MANI)
    m2 = run_main(["--window", "a7-docs", "--plan", "d6", *tail], MANI)
    check("D6-7: plan d6 outside window a7-arxiv, and that window with another plan, are refused (rc 2, named)",
          all(rc == 2 and "plan d6 runs in window a7-arxiv" in msg for rc, msg in (m1, m2)), f"{m1} {m2}")
    other = json.loads(json.dumps(MANI))
    other["windows"].setdefault("a7-arxiv", {})["hosts"] = ["export.arxiv.org", "arxiv.org"]
    m3 = run_main(["--window", "a7-arxiv", "--plan", "d6", *tail], other)
    m4 = run_main(["--window", "a7-arxiv", "--plan", "d6", *tail], MANI)
    check("D6-8: a manifest whose a7-arxiv entry is not the declared shape is refused (rc 2, named); the real one gets to "
          "the window", m3[0] == 2 and "a7-arxiv" in m3[1] and m4[0] == "spawned", f"{m3} {m4}")
    print("\n- plan d8: window a7-arxiv-src (the auditor's Q-D8-1..3 = O-a) - the paper's licence, versions, source -")
    import gzip  # noqa: E402,PLC0415
    import hashlib  # noqa: E402,PLC0415
    import tarfile  # noqa: E402,PLC0415

    REAL8 = MANI["windows"].get("a7-arxiv-src") or {}
    decl8, derr8 = holds(lambda: F.d8_decl(MANI))
    check("D8-M1: the manifest declares a7-arxiv-src as plan d8 reads it: export.arxiv.org, oaipmh.arxiv.org and "
          "arxiv.org, one redirect (the OAI request's, Q-D8-5 = O-b), arXiv 2501.13956 (the auditor's choice from a7-arxiv "
          "d2's report) at version 1, at most 64 MB",
          derr8 is None and decl8["hosts"] == ["export.arxiv.org", "oaipmh.arxiv.org", "arxiv.org"]
          and decl8["max_redirects"] == 1
          and decl8["arxiv_id"] == "2501.13956" and decl8["version"] == 1 and decl8["max_bytes"] == 64 * 1024 * 1024,
          str(derr8 or decl8))
    bad8 = {
        "an extra key": {**REAL8, "search_query": "ti:zep"},
        "a missing key": {k: v for k, v in REAL8.items() if k != "version"},
        "a fourth host": {**REAL8, "hosts": ["export.arxiv.org", "oaipmh.arxiv.org", "arxiv.org", "evil.example"]},
        "the old two hosts": {**REAL8, "hosts": ["export.arxiv.org", "arxiv.org"]},
        "one host only": {**REAL8, "hosts": ["export.arxiv.org"]},
        "no redirect": {**REAL8, "max_redirects": 0},
        "two redirects": {**REAL8, "max_redirects": 2},
        "an id with its version": {**REAL8, "arxiv_id": "2501.13956v1"},
        "an old-style id": {**REAL8, "arxiv_id": "hep-th/9901001"},
        "an id that climbs": {**REAL8, "arxiv_id": "../2501.13956"},
        "version 0": {**REAL8, "version": 0},
        "version True": {**REAL8, "version": True},
        "version '1'": {**REAL8, "version": "1"},
        "max_bytes 0": {**REAL8, "max_bytes": 0},
        "max_bytes over 64 MB": {**REAL8, "max_bytes": 64 * 1024 * 1024 + 1},
        "max_bytes True": {**REAL8, "max_bytes": True},
    }
    ref8 = {}
    for label, entry in bad8.items():
        _v, e = holds(lambda entry=entry: F.d8_decl({"windows": {"a7-arxiv-src": entry}}))
        ref8[label] = bool(e) and e.startswith("D8ManifestError")
    check("D8-M2: an entry with anything but the declared shape is refused by name (D8ManifestError) - " + ", ".join(bad8),
          all(ref8.values()), str([k for k, v in ref8.items() if not v]))

    ID8 = "2501.00001"
    DECL8 = {"hosts": ["export.arxiv.org", "oaipmh.arxiv.org", "arxiv.org"], "purpose": "test", "arxiv_id": ID8, "version": 1,
             "max_bytes": 1 << 20, "max_redirects": 1}
    OAI_Q = f"/oai2?verb=GetRecord&identifier=oai:arXiv.org:{ID8}&metadataPrefix=arXivRaw"
    OAI_Q2 = f"/oai?verb=GetRecord&identifier=oai:arXiv.org:{ID8}&metadataPrefix=arXivRaw"   # the moved endpoint's path
    MOVED = (301, [("Location", "https://oaipmh.arxiv.org" + OAI_Q2), ("Connection", "close")], b"")
    EP_Q = f"/src/{ID8}v1"                      # Q-D8-6 = O-a: the path a7-arxiv-src s2's record gave
    LIC = "http://creativecommons.org/licenses/by/4.0/"

    def oai(*, versions=("v1",), licence=LIC, rid=ID8) -> bytes:
        """An OAI-PMH GetRecord answer in the arXivRaw format, as export.arxiv.org gives it."""
        vs = "".join(f'<version version="{v}"><date>Mon, 20 Jan 2025 17:{i:02d}:00 GMT</date><size>1024kb</size>'
                     f"<source_type>D</source_type></version>" for i, v in enumerate(versions))
        lic = f"<license>{licence}</license>" if licence is not None else ""
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
                "<responseDate>2026-09-30T00:00:00Z</responseDate><GetRecord><record><header>"
                f"<identifier>oai:arXiv.org:{rid}</identifier></header><metadata>"
                f'<arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>{rid}</id>{vs}<title>Zep: A Temporal\n  Knowledge '
                f"Graph</title>{lic}</arXivRaw></metadata></record></GetRecord></OAI-PMH>\n").encode("utf-8")

    def tar_bytes(compress: bool) -> bytes:
        import io as _io  # noqa: PLC0415
        buf = _io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz" if compress else "w") as t:
            data = b"\\documentclass{article}\n"
            ti = tarfile.TarInfo("main.tex")
            ti.size, ti.mtime = len(data), 0
            t.addfile(ti, _io.BytesIO(data))
        return buf.getvalue()

    def gz_one(name: str = "zep.tex") -> bytes:
        import io as _io  # noqa: PLC0415
        buf = _io.BytesIO()
        with gzip.GzipFile(filename=name, mode="wb", fileobj=buf, mtime=0) as g:
            g.write(b"\\documentclass{article}\n\\title{Zep}\n")
        return buf.getvalue()

    def gz_named(name: bytes, body: bytes = b"\\documentclass{article}\n") -> bytes:
        """A gzip member whose header names ``name`` verbatim (GzipFile would keep only its base name): FNAME set."""
        import struct  # noqa: PLC0415
        import zlib  # noqa: PLC0415
        c = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
        return (b"\x1f\x8b\x08\x08" + b"\0\0\0\0" + b"\x00\xff" + name + b"\0" + c.compress(body) + c.flush()
                + struct.pack("<II", zlib.crc32(body), len(body)))

    TGZ = tar_bytes(True)
    made8 = TF.make_test_cert(TMP / "cert8", "export.arxiv.org", extra_hosts=("arxiv.org", "oaipmh.arxiv.org"),
                              org="Let's Encrypt")

    def head_of(h: bytes) -> tuple:
        lines = h.split(b"\r\n")
        host = next((ln.split(b":", 1)[1].strip().decode() for ln in lines[1:] if ln.lower().startswith(b"host:")), None)
        return lines[0].split(b" ")[1].decode(), host

    def window8(run: str, routes: dict, pauses: list):
        srv8 = TF.TlsHttpServer(made8[0], made8[1], routes)
        hop8 = TF.TunnelHop(srv8.port)
        base = TMP / run
        (base / "watched").mkdir(parents=True)
        (base / "watched" / "idle.txt").write_bytes(b"idle")
        c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                       owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                       conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                       system_dirs=(Path(sys.executable).parent,))
        jobs, jerr = holds(lambda: F.d8_jobs(DECL8, sleep=pauses.append))
        rec, rerr = holds(lambda: F.run_child_window(
            c, L, window="a7-arxiv-src", hosts=F.D8_HOSTS, jobs=jobs, python=Path(sys.executable), via_port=hop8.port,
            run=run, parent_env=os.environ, native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
            fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), child_env_extra={"SSL_CERT_FILE": str(made8[0])},
            volume=TMP)) if jerr is None else (None, jerr)
        heads = [head_of(h) for h in srv8.heads]
        rep, perr = holds(lambda: F.d8_report(rec, DECL8)) if rec is not None else (None, rerr)
        for x in (hop8, srv8):
            try:
                x.close()
            except Exception:  # noqa: BLE001
                pass
        return heads, rec, rep, perr

    routes_ok = {OAI_Q: MOVED, OAI_Q2: (200, [("Content-Type", "text/xml")], oai()),
                 EP_Q: (200, [("Content-Type", "application/x-eprint-tar"), ("Content-Encoding", "x-gzip")], TGZ)}
    pauses: list = []
    heads8, rec8, rep8, perr8 = window8("d8a", routes_ok, pauses)
    check("D8-1: exactly three GETs, in order - the OAI-PMH GetRecord (arXivRaw) on export.arxiv.org, its one redirect to "
          "oaipmh.arxiv.org at the path the server gave, then the e-print of v1 on arxiv.org; no link followed", perr8 is None
          and heads8 == [(OAI_Q, "export.arxiv.org"), (OAI_Q2, "oaipmh.arxiv.org"), (EP_Q, "arxiv.org")], f"{perr8} {heads8}")
    check("D8-2: the harness pauses at least 3 s between them - asked once, before the e-print",
          pauses == [getattr(F, "D8_PAUSE_S", None)] and getattr(F, "D8_PAUSE_S", 0) >= 3, str(pauses))
    rep8 = rep8 or {}
    oa, ep = rep8.get("oai") or {}, rep8.get("eprint") or {}
    check("D8-3: the report - the licence the record names, its versions (v1 alone, with its date, size and source type), "
          "its title with white space collapsed", oa.get("licence") == LIC and oa.get("versions") == [
              {"version": "v1", "date": "Mon, 20 Jan 2025 17:00:00 GMT", "size": "1024kb", "source_type": "D"}]
          and oa.get("title") == "Zep: A Temporal Knowledge Graph", json.dumps(oa)[:400])
    check("D8-4: the e-print is kept as sent (x-gzip, never decoded): its sha256 and size are the served bytes', its kind "
          "tar.gz, read from its first bytes; the report has no problem",
          ep.get("sha256") == hashlib.sha256(TGZ).hexdigest() and ep.get("bytes") == len(TGZ) and ep.get("kind") == "tar.gz"
          and ep.get("content_encoding") == "x-gzip" and rep8.get("problems") == [], json.dumps(rep8)[:600])
    unit1 = F._unit_of(rec8["jobs"], 1) if rec8 else None
    check("D8-5: the e-print's unit holds the file as sent; nothing is unpacked anywhere in the run",
          unit1 is not None and (unit1 / "eprint.bin").is_file() and (unit1 / "eprint.bin").read_bytes() == TGZ
          and not any(p.name == "main.tex" for p in (TMP / "d8a").rglob("*")), str(unit1))
    check("D8-6: the window's check is complete, no native hit, no problem; its catcher tunnelled the three hosts and the "
          "record names an issuer for each - export.arxiv.org's too, though its 301 closed the connection (B-ISS-CLOSE)",
          rec8 is not None and rec8["check"]["complete"] and rec8["check"]["native_hits"] == 0 and rec8["problems"] == []
          and {x["host"] for x in rec8["catcher"] if x.get("tunnelled")} == {"export.arxiv.org", "oaipmh.arxiv.org", "arxiv.org"}
          and {i[0] for i in rec8["issuers"]} == {"export.arxiv.org", "oaipmh.arxiv.org", "arxiv.org"},
          str((rec8 or {}).get("problems")) + str((rec8 or {}).get("issuers")))
    check("D8-R1 (Q-D8-5 = O-b): the report names the OAI answer's final host and the redirect's path, never its query",
          oa.get("final_host") == "oaipmh.arxiv.org" and oa.get("redirect_path") == "/oai" and "verb=" not in json.dumps(oa),
          json.dumps(oa)[:300])
    pe: list = []
    heads_e, _rec_e, rep_e, perr_e = window8("d8e", {OAI_Q: (301, [("Location", "https://evil.example" + OAI_Q2)], b""),
                                                   EP_Q: routes_ok[EP_Q]}, pe)
    probs_e = (rep_e or {}).get("problems") or []
    check("D8-R2: a redirect to any other host is refused by name - not followed, the e-print never asked for",
          perr_e is None and heads_e == [(OAI_Q, "export.arxiv.org")] and pe == []
          and any("redirected to evil.example" in p and "not followed" in p for p in probs_e)
          and any("the e-print was not asked for" in p for p in probs_e), f"{perr_e} {heads_e} {probs_e}")
    p2: list = []
    heads_2, _rec_2, rep_2, perr_2 = window8("d8r", {OAI_Q: MOVED, OAI_Q2: (301, [("Location", "https://oaipmh.arxiv.org/oai3")], b""),
                                                   EP_Q: routes_ok[EP_Q]}, p2)
    probs_2 = (rep_2 or {}).get("problems") or []
    check("D8-R3: a second redirect is refused by name - the declared one is the only one followed, no e-print",
          perr_2 is None and heads_2 == [(OAI_Q, "export.arxiv.org"), (OAI_Q2, "oaipmh.arxiv.org")] and p2 == []
          and any("not followed" in p and "more redirects" in p for p in probs_2)
          and any("the e-print was not asked for" in p for p in probs_2), f"{perr_2} {heads_2} {probs_2}")
    p429: list = []
    heads9, _rec9, rep9, perr9 = window8("d8b", {OAI_Q: (429, [("Retry-After", "60")], b""), EP_Q: routes_ok[EP_Q]}, p429)
    probs9 = (rep9 or {}).get("problems") or []
    check("D8-7: a 429 to the OAI request is a problem by name; the e-print is never asked for and no pause is taken",
          perr9 is None and heads9 == [(OAI_Q, "export.arxiv.org")] and p429 == [] and any("429" in p for p in probs9)
          and any("the e-print was not asked for" in p for p in probs9), f"{perr9} {heads9} {probs9}")

    def offline8(tag: str, oai_body, ep_body, *, oai_summ=None, ep_summ=None, ep_job=True):
        """d8_report on saved answers, no window: the report's reading alone."""
        u0, u1 = TMP / "off8" / tag / "j0", TMP / "off8" / tag / "j1"
        u0.mkdir(parents=True)
        u1.mkdir(parents=True)
        if oai_body is not None:
            (u0 / "oai_record.xml").write_bytes(oai_body)
        if ep_body is not None:
            (u1 / "eprint.bin").write_bytes(ep_body)
        s0 = oai_summ or {"id": "oai", "ok": True, "status": 200}
        s1 = ep_summ or {"id": "eprint", "ok": True, "status": 200, "bytes": len(ep_body or b""),
                         "sha256": hashlib.sha256(ep_body or b"").hexdigest(), "content_encoding": None,
                         "content_type": "application/octet-stream"}
        jobs = [{"index": 0, "unit": str(u0), "summary": [s0]}]
        if ep_job:
            jobs.append({"index": 1, "unit": str(u1), "summary": [s1]})
        return holds(lambda: F.d8_report({"jobs": jobs}, DECL8))

    def probs(r) -> list:
        return (r[0] or {}).get("problems") or [] if r[1] is None else [f"raised {r[1]}"]

    def epk(r) -> dict:
        return (r[0] or {}).get("eprint") or {}

    pdf = offline8("pdf", oai(), b"%PDF-1.5\n%\xe2\xe3\n1 0 obj\n")
    gz1 = offline8("gz1", oai(), gz_one())
    tar = offline8("tar", oai(), tar_bytes(False))
    html = offline8("html", oai(), b"<!DOCTYPE html><html><body>Rate exceeded</body></html>")
    check("D8-8: the source's kind is named from its first bytes - a PDF only is a problem by name (no LaTeX source), a "
          "gzip of one file names its file (zep.tex) and first line, an uncompressed tar is a tar, anything else (an HTML "
          "page) is a problem by name",
          epk(pdf).get("kind") == "pdf" and any("only a PDF" in p for p in probs(pdf))
          and epk(gz1).get("kind") == "gzip" and epk(gz1).get("gzip_name") == "zep.tex"
          and epk(gz1).get("first_line") == "\\documentclass{article}" and probs(gz1) == []
          and epk(tar).get("kind") == "tar" and probs(tar) == []
          and epk(html).get("kind") == "unknown" and any("neither a gzip, a tar nor a PDF" in p for p in probs(html)),
          str([(epk(r).get("kind"), probs(r)) for r in (pdf, gz1, tar, html)]))
    import time as _time  # noqa: PLC0415
    bomb = gzip.compress(b"\0" * (64 << 20), mtime=0)   # 64 MB of zeros in about 64 KB
    t0 = _time.monotonic()
    bm = offline8("bomb", oai(), bomb)
    took = _time.monotonic() - t0
    check("D8-8b: a gzip whose output is huge (64 MB of zeros) is read no further than 512 bytes of output, in memory - "
          "its peek is exactly 512 bytes and the reading is immediate", epk(bm).get("kind") == "gzip"
          and epk(bm).get("peek_bytes") == 512 and took < 2.0 and probs(bm) == [], f"{epk(bm)} took {took:.2f}s")
    evil_gz = gz_named(b"../x/evil.tex")
    evil = offline8("evil_name", oai(), evil_gz)
    check("D8-8c: the gzip header's file name is text in the report, never a path - '../x/evil.tex' is named as it is and "
          "no such file is written anywhere", gzip.decompress(evil_gz) == b"\\documentclass{article}\n"
          and epk(evil).get("gzip_name") == "../x/evil.tex"
          and not any(p.name == "evil.tex" for p in TMP.rglob("*")), str(epk(evil)))
    dt = oai().replace(b"<OAI-PMH", b'<!DOCTYPE OAI-PMH [<!ENTITY a "aaaa">]><OAI-PMH', 1)
    err = (b'<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="idDoesNotExist">'
           b"No matching identifier</error></OAI-PMH>")
    cases8 = {
        "a DOCTYPE": (offline8("dt", dt, TGZ), "declares a DOCTYPE"),
        "not OAI-PMH": (offline8("html_oai", b"<html/>", TGZ), "not an OAI-PMH answer"),
        "an OAI error": (offline8("err", err, TGZ), "OAI error idDoesNotExist"),
        "no licence": (offline8("nolic", oai(licence=None), TGZ), "names no licence"),
        "a second version": (offline8("v2", oai(versions=("v1", "v2")), TGZ), "v1 is not the only version"),
        "another paper": (offline8("rid", oai(rid="2501.00002"), TGZ), "the record names '2501.00002', not 2501.00001"),
        "no answer saved": (offline8("nosave", None, TGZ), "no OAI answer saved"),
    }
    check("D8-9: the OAI record's problems are named - " + ", ".join(cases8),
          all(any(w in p for p in probs(r)) for r, w in cases8.values()),
          str({k: probs(r) for k, (r, _w) in cases8.items()})[:900])
    ok8 = offline8("ok8", oai(), TGZ)
    check("D8-9b: the same reading of a sound record and source has no problem", probs(ok8) == [], str(probs(ok8)))
    moved = offline8("moved", None, None, ep_job=False, oai_summ={
        "id": "oai", "ok": False, "status": 301, "redirect_host": "oaipmh.arxiv.org",
        "error": "Refused: more redirects than the job allows"})
    check("D8-10: an OAI request redirected elsewhere is a problem naming the host, not followed, and the e-print was not "
          "asked for", any("redirected to oaipmh.arxiv.org" in p and "not followed" in p for p in probs(moved))
          and any("the e-print was not asked for" in p for p in probs(moved)), str(probs(moved)))
    bad_sum = offline8("badsum", oai(), TGZ, ep_summ={"id": "eprint", "ok": True, "status": 200, "bytes": len(TGZ),
                                                     "sha256": "0" * 64})
    ep_fail = offline8("epfail", oai(), None, ep_summ={"id": "eprint", "ok": False, "status": 404,
                                                      "error": "Refused: status 404"})
    check("D8-10b: an e-print whose saved bytes are not the ones its summary names, or a failed e-print request, is a "
          "problem by name", any("sha256" in p for p in probs(bad_sum))
          and any("the e-print failed" in p and "404" in p for p in probs(ep_fail)), str((probs(bad_sum), probs(ep_fail))))

    m81 = run_main(["--window", "a7-arxiv-src", "--plan", "d6", *tail], MANI)
    m82 = run_main(["--window", "a7-arxiv", "--plan", "d8", *tail], MANI)
    check("D8-11: plan d8 outside window a7-arxiv-src, and that window with another plan, are refused (rc 2, named)",
          all(rc == 2 and "plan d8 runs in window a7-arxiv-src" in msg for rc, msg in (m81, m82)), f"{m81} {m82}")
    other8 = json.loads(json.dumps(MANI))
    other8["windows"].setdefault("a7-arxiv-src", {})["hosts"] = ["export.arxiv.org"]
    m83 = run_main(["--window", "a7-arxiv-src", "--plan", "d8", *tail], other8)
    m84 = run_main(["--window", "a7-arxiv-src", "--plan", "d8", *tail], MANI)
    check("D8-12: a manifest whose a7-arxiv-src entry is not the declared shape is refused (rc 2, named); the real one gets "
          "to the window", m83[0] == 2 and "a7-arxiv-src" in m83[1] and m84[0] == "spawned", f"{m83} {m84}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 fetch a7 arxiv: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
