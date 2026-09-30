#!/usr/bin/env python3
"""PREREG-V3 A8, supermemory-local (the auditor's Q-A8S-SRC = (c), 2026-09-30): the server's facts read from the
pinned binary's embedded Bun bundle - no network, nothing executed, no process started. The vendor's source of the
server is not in the public repository's head tree (a7-discovery d1); the binary bun build --compile made holds its
JavaScript as text, and that text is exactly the code a start would run.

1. The pin, offline: a8-supermemory-bin b2's bin_record.json, by the sha256 freeze_a7's CLEARED_A7 fixes
   (BIN_RECORD_SHA256) - another file is refused; so is a record with problems, of another window, run, tag or tag
   commit, one whose binary was started, or one whose size is not the file's. The binary at the record's path is
   re-read: another sha256 than the record's is refused by name.
2. The bundle: the PE section ".bun" of that file (its raw pointer, its virtual size) - no PE header, no .bun
   section, or one reaching past the file's end, is refused by name. Nothing outside that section is read.
3. The facts (FACTS): each a pattern with capture groups that must match the section exactly once - none is
   "missing", two or more "ambiguous", each refused by name: a fact is never taken from memory. Recorded per fact:
   its file offset, the matched literal, the captured values and the sha256 of WINDOW_BYTES each side of it, clipped
   to the section. A literal holding a key-like token refuses the record (the vendor's embedded telemetry key is
   never copied).
4. The verdicts: R8 (the server's bind) - clear only when the bound host is a loopback name AND the serve call and
   the pre-bind listen both use that bound constant; 0.0.0.0 or '::' is blocked:bind-all-interfaces, anything else
   blocked:bind-not-loopback (Q-A8S-R8 = a: no start before the owner's inbound block rule for the binary). The
   spawn's vendor switches (SPAWN_ENV) must be values the bundle's own comparisons accept, else a problem by name.
   The auth facts (F6: how the server decides "localhost", the unkeyed local role, the key's file and form).

The record: <runs>/_facts/a8-sm-bundle/<run>/bundle_facts.json.

    python research/v3/sm_bundle.py --run f1
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
READER = "a8-sm-bundle"
BIN_WINDOW, BIN_RUN = "a8-supermemory-bin", "b2"
REPO, TAG, VERSION = "supermemoryai/supermemory", "server-v0.0.8", "0.0.8"
COMMIT = "5d2b5855fe492a3682a1cde4a255e2db0c4db595"
#: freeze_a7.CLEARED_A7's fixing of _install/a8-supermemory-bin/b2/bin_record.json (the auditor, 2026-09-30).
BIN_RECORD_SHA256 = "6de665abf22c26c012c8aebc2588c71ea08ad1ab2559c128369277d7a8ad3b7d"
WINDOW_BYTES = 256
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
ALL_INTERFACES = frozenset({"0.0.0.0", "::"})
#: the documented vendor switches the spawn declares (the auditor, 2026-09-30 16:4x): no update check (F3), no
#: telemetry (F4), no browser (F5) - each named in the spawn record and in E5.
SPAWN_ENV = {"SUPERMEMORY_NO_UPDATE_CHECK": "1", "SUPERMEMORY_DISABLE_TELEMETRY": "1", "SUPERMEMORY_NO_OPEN": "1"}
_KEYLIKE = re.compile(rb"(?:sk-|phc_|sm_)[A-Za-z0-9_\-]{8,}")
_ID = rb"([A-Za-z0-9_$]+)"
#: name -> pattern over the bundle's bytes (server-v0.0.8's minified names are part of the literal: another build
#: fails the pin first, and a changed literal is "missing", never guessed).
FACTS: dict[str, bytes] = {
    "version": rb'NQ0="supermemory-server",dr="([0-9][0-9A-Za-z.-]*)"',
    "bind_host": rb'process\.env\.SUPERMEMORY_PORT\|\|Ds5\),' + _ID + rb'="([^"]*)",eh=5000',
    "port": rb'Ds5=(\d+),iX=Number\(process\.env\.PORT\|\|process\.env\.SUPERMEMORY_PORT\|\|Ds5\)',
    "serve": rb'return new Response\("Service is starting",\{status:503\}\)\},port:iX,hostname:' + _ID + rb'\}\)',
    "preflight": rb'v\.once\("error",\(\)=>e\(new QM0\(mb0,\$\)\)\),v\.once\("listening",\(\)=>\{v\.close\(\(u\)=>'
                 rb'\{if\(u\)e\(u\);else z\(\)\}\)\}\),v\.listen\(\$,' + _ID + rb'\)',
    "rivet_hosts": rb'engine\.json"\);await tu8\(T,JSON\.stringify\(\{guard:\{host:"([^"]+)",port:z\},'
                   rb'api_peer:\{host:"([^"]+)",port:z\+1\},metrics:\{host:"([^"]+)",port:z\+2\}',
    "rivet_spawn": rb'r=Bun\.spawn\(\[v,"(start)","--config",T\]',
    "update_switch": rb'if\(process\.env\.SUPERMEMORY_NO_UPDATE_CHECK==="([^"]*)"\)return null;',
    "update_url": rb'zN=`(https://github\.com/)\$\{xd8\}/releases',
    "update_daily": rb'H\(\),Na\("(0 12 \* \* \*)",\(\)=>void H\(\)\)',
    "telemetry_switch": rb'function vH0\(\)\{return process\.env\.SUPERMEMORY_DISABLE_TELEMETRY==="([^"]*)"\|\|'
                        rb'process\.env\.SUPERMEMORY_DISABLE_TELEMETRY==="([^"]*)"\}',
    "telemetry_gate": rb'function l40\(\$,z="PostHog tracking error:"\)\{if\((vH0)\(\)\)return;',
    "telemetry_host": rb'TH0=process\.env\.SUPERMEMORY_POSTHOG_HOST\?\?"(https://[^"]+)"',
    "heartbeat": rb'HO8\(u\),Na\("(0 \*/6 \* \* \*)",\(\)=>HO8\(u\)\)',
    "browser_switch": rb'function Ih5\(\)\{if\(process\.env\.SUPERMEMORY_NO_OPEN==="([^"]*)"\)return!1;',
    "browser_win32": rb'case"win32":return\{command:"(cmd)",args:\["/c","start","",\$\]\}',
    "key_file": rb'Fp4="Local User",Lp4="([^"]+)"',
    "key_length": rb'var hp4=(\d+);function op4',
    "key_mode": rb'await sp4\(\$,(\d+)\)\.catch',
    "key_banner": rb'if\(\$\.apiKey\)z\.push\(\["(api key)",\$\.apiKey\]\)',
    "key_headers": rb'let z=\[\$\.get\("(x-supermemory-api-key)"\),\$\.get\("x-api-key"\),'
                   rb'\$\.get\("Authorization"\)\?\.replace',
    "local_host_check": rb'function hn5\(\$\)\{let z=new URL\(\$\.(url)\)\.hostname;if\(z==="localhost"\|\|'
                        rb'z==="127\.0\.0\.1"\|\|z==="::1"\)return!0;',
    "local_auth": rb'function jn5\(\)\{return async\(\$,z\)=>\{if\(!W3\(\)\.features\.selfHosted\)return z\(\);'
                  rb'if\(\$\.get\("user"\)\)return z\(\);if\(!(hn5)\(\$\.req\.raw\)\)return z\(\);',
    "local_role": rb'\$\.set\("org",e\.org\),\$\.set\("memberRole","([^"]+)"\),\$\.set\("isRestricted",!1\)',
    "data_dir": rb'_\$=VY5\(process\.env\.SUPERMEMORY_DATA_DIR\?\?"([^"]+)"\)',
    "doc_limit": rb'var fQ=(1e4),X\$0=0',
    "embed_compat": rb'T=hl8\(\{name:"openai",(apiKey):u,baseURL:O\}\)\.embeddingModel\(e\)',
    "workflow_default": rb'let z=await id8\(\);if\(z\)\{let e=globalThis;if\(!e\.(__SUPERMEMORY_RIVET_NAPI__)',
}
#: the switch -> the facts whose captured values are the values the bundle compares that variable with
_SWITCH_FACTS = {"SUPERMEMORY_NO_UPDATE_CHECK": "update_switch", "SUPERMEMORY_DISABLE_TELEMETRY": "telemetry_switch",
                 "SUPERMEMORY_NO_OPEN": "browser_switch"}


class FactsRefused(RuntimeError):
    """A pin, a bundle or a fact this module's rules do not allow - named; no record is written."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pinned_binary(runs_root: Path, *, record_sha256: str = BIN_RECORD_SHA256) -> tuple[bytes, dict]:
    """(the binary's bytes, the pin {record, record_sha256, binary, binary_sha256, bytes}) - see step 1."""
    f = runs_root / "_install" / BIN_WINDOW / BIN_RUN / "bin_record.json"
    if not f.is_file():
        raise FactsRefused(f"no {BIN_WINDOW} {BIN_RUN} bin_record.json at {f}")
    raw = f.read_bytes()
    got = _sha256_bytes(raw)
    if got != record_sha256:
        raise FactsRefused(f"bin_record.json sha256 {got[:12]}... is not the fixed record {record_sha256[:12]}...")
    rec = json.loads(raw)
    want = {"window": BIN_WINDOW, "run": BIN_RUN, "repo": REPO, "tag": TAG, "commit": COMMIT, "version": VERSION,
            "binary_started": False, "problems": []}
    for k, v in want.items():
        if rec.get(k) != v:
            raise FactsRefused(f"the bin_record's {k} is {rec.get(k)!r}, not {v!r}")
    binp = Path(rec.get("path") or "")
    if not binp.is_file():
        raise FactsRefused(f"the pinned binary is not at the record's path {binp}")
    data = binp.read_bytes()
    if len(data) != rec.get("bytes"):
        raise FactsRefused(f"the binary is {len(data)} bytes, the bin_record's bytes say {rec.get('bytes')}")
    sha = _sha256_bytes(data)
    if sha != rec.get("sha256"):
        raise FactsRefused(f"the binary's sha256 {sha[:12]}... is not the bin_record's {str(rec.get('sha256'))[:12]}...")
    return data, {"record": f"_install/{BIN_WINDOW}/{BIN_RUN}/bin_record.json", "record_sha256": got,
                  "binary": str(binp), "binary_sha256": sha, "bytes": len(data)}


def bun_section(data: bytes) -> tuple[int, int]:
    """[lo, hi) of the PE section ".bun": its raw pointer and its virtual size - see step 2."""
    if data[:2] != b"MZ" or len(data) < 0x40:
        raise FactsRefused("the binary has no PE header (no MZ)")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise FactsRefused("the binary has no PE header (no PE signature)")
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    table = pe + 24 + struct.unpack_from("<H", data, pe + 20)[0]
    found = []
    for i in range(nsec):
        o = table + 40 * i
        if data[o:o + 8].rstrip(b"\0") == b".bun":
            vsize, _va, _rsize, raw_at = struct.unpack_from("<IIII", data, o + 8)
            found.append((raw_at, raw_at + vsize))
    if len(found) != 1:
        raise FactsRefused(f"the binary has {len(found)} .bun sections, not one")
    lo, hi = found[0]
    if hi > len(data) or lo >= hi:
        raise FactsRefused(f"the .bun section [{lo}, {hi}) reaches past the file's end ({len(data)} bytes)")
    return lo, hi


def read_facts(data: bytes, lo: int, hi: int, facts: dict[str, bytes] | None = None) -> dict:
    """{name: {offset, literal, values, window_sha256}} of every fact, each matched exactly once in data[lo:hi]."""
    sec = data[lo:hi]
    out = {}
    for name, pat in (FACTS if facts is None else facts).items():
        ms = list(re.finditer(pat, sec))
        if not ms:
            raise FactsRefused(f"the fact {name} is missing from the bundle - never taken from memory")
        if len(ms) > 1:
            raise FactsRefused(f"the fact {name} is ambiguous - {len(ms)} matches in the bundle")
        m = ms[0]
        if _KEYLIKE.search(m.group(0)):
            raise FactsRefused(f"the fact {name}'s literal holds a key-like token - not recorded")
        try:
            literal = m.group(0).decode("utf-8")
            values = [g.decode("utf-8") for g in m.groups()]
        except UnicodeDecodeError as e:
            raise FactsRefused(f"the fact {name} is not UTF-8 text ({e})") from None
        a, b = m.start(), m.end()
        out[name] = {"offset": lo + a, "literal": literal, "values": values,
                     "window_sha256": _sha256_bytes(sec[max(0, a - WINDOW_BYTES):min(len(sec), b + WINDOW_BYTES)])}
    return out


def r8_verdict(facts: dict) -> dict:
    """The server's bind (R8) - see step 4."""
    var, host = facts["bind_host"]["values"]
    problems = [f"the {call} uses {facts[fact]['values'][0]}, not the bound constant {var}"
                for fact, call in (("serve", "serve call's hostname"), ("preflight", "pre-bind listen"))
                if facts[fact]["values"][0] != var]
    loopback = host in LOOPBACK and not problems
    if loopback:
        r8 = "clear"
    elif host in ALL_INTERFACES and not problems:
        r8 = (f"blocked:bind-all-interfaces - the server binds {host}; no start before the owner's inbound block rule "
              "for the binary (Q-A8S-R8 = a)")
    else:
        r8 = (f"blocked:bind-not-loopback - the server binds {host!r}; no start before the owner's inbound block rule "
              "for the binary (Q-A8S-R8 = a)")
    return {"host": host, "host_var": var, "port_default": int(facts["port"]["values"][0]),
            "port_env": ["PORT", "SUPERMEMORY_PORT"], "all_interfaces": host in ALL_INTERFACES,
            "loopback_only": loopback, "r8": r8, "problems": problems}


def switches(facts: dict) -> dict:
    """SPAWN_ENV and a problem for each switch whose value the bundle's comparison does not accept."""
    problems = [f"{k}={v} is not a value the bundle compares {k} with ({facts[f]['values']})"
                for k, v in SPAWN_ENV.items() for f in (_SWITCH_FACTS[k],) if v not in facts[f]["values"]]
    return {"env": dict(SPAWN_ENV), "problems": problems}


def auth_facts(facts: dict) -> dict:
    """F6 and R5 as the bundle states them."""
    return {"localhost_by": facts["local_host_check"]["values"][0],
            "unkeyed_local_role": facts["local_role"]["values"][0],
            "key_headers_first": facts["key_headers"]["values"][0], "key_file": facts["key_file"]["values"][0],
            "key_mode": oct(int(facts["key_mode"]["values"][0])), "key_length": int(facts["key_length"]["values"][0]),
            "key_in_banner": facts["key_banner"]["values"] == ["api key"]}


def run_facts(runs_root: Path, *, run: str, record_sha256: str = BIN_RECORD_SHA256) -> dict:
    """The whole read (see the module docstring); a refusal writes nothing."""
    base = runs_root / "_facts" / READER / run
    if base.exists():
        raise FactsRefused(f"the run label {run} was used before - a record is never overwritten")
    data, pin = pinned_binary(runs_root, record_sha256=record_sha256)
    lo, hi = bun_section(data)
    facts = read_facts(data, lo, hi)
    r8, sw = r8_verdict(facts), switches(facts)
    record = {"reader": READER, "run": run, "network": False, "executed": False, "window_bytes": WINDOW_BYTES,
              "pin": pin, "bundle": {"section": ".bun", "offset": lo, "bytes": hi - lo}, "facts": facts, "r8": r8,
              "switches": sw, "auth": auth_facts(facts), "problems": r8["problems"] + sw["problems"],
              "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    base.mkdir(parents=True)
    (base / "bundle_facts.json").write_bytes((json.dumps(record, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return record


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A8: supermemory-local's facts from the pinned binary's bundle, offline")
    ap.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    c = _load("v3_launch", HERE / "launch.py").Contract.default()
    try:
        rec = run_facts(c.runs_root, run=args.run)
    except FactsRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"r8": rec["r8"]["r8"], "switches": rec["switches"], "auth": rec["auth"],
                      "problems": rec["problems"]}, indent=1))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
