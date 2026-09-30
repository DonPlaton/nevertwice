#!/usr/bin/env python3
"""PREREG-V3 A8, supermemory-local (the auditor's Q-A8S-SRC = (c), 2026-09-30): research/v3/sm_bundle.py - the
server's facts read from the pinned binary's embedded Bun bundle, with no network and nothing executed.

* the pin: a8-supermemory-bin b2's bin_record.json by its fixed sha256; the binary at its path re-read against the
  record's sha256 and size; a record with problems, another window, run, tag or commit, or a started binary, refuses;
* the bundle: the PE section ".bun" only - no PE header, no .bun, a section past the file's end refuse by name; a
  literal outside the section is never read;
* each fact matches the bundle exactly once - none is "missing", two "ambiguous", each refused by name; the record
  holds its file offset, literal, captured values and the sha256 of the bytes around it (clipped to the section);
  a key-like literal refuses the record;
* R8: clear only when the bound host is a loopback name and the serve call and the pre-bind listen use the bound
  constant; 0.0.0.0 is blocked:bind-all-interfaces, anything else blocked:bind-not-loopback;
* the spawn's vendor switches (SUPERMEMORY_NO_UPDATE_CHECK, SUPERMEMORY_DISABLE_TELEMETRY, SUPERMEMORY_NO_OPEN) are
  values the bundle's own comparisons accept, else a problem by name; the auth facts (F6) are recorded.

No child process: every row runs in this process on files under a temporary directory.

    python tests/research/_test_v3_sm_bundle.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import struct
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def _try(fn):
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


try:
    _spec = importlib.util.spec_from_file_location("v3_sm_bundle", ROOT / "research" / "v3" / "sm_bundle.py")
    SB = importlib.util.module_from_spec(_spec)
    sys.modules["v3_sm_bundle"] = SB
    _spec.loader.exec_module(SB)
    load_err = None
except Exception as e:  # noqa: BLE001
    SB, load_err = None, f"{type(e).__name__}: {e}"
check("the reader module loads", SB is not None, str(load_err))


def refused(fn, *words: str) -> bool:
    """fn raises the reader's own refusal, naming every word."""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        return SB is not None and isinstance(e, SB.FactsRefused) and all(w in str(e) for w in words)
    return False


# ── fixtures: a fake PE file whose .bun section holds the vendor's literals (public code of server-v0.0.8) ──────────

LITERALS = [
    b'NQ0="supermemory-server",dr="0.0.8"',
    b'process.env.SUPERMEMORY_PORT||Ds5),mb0="0.0.0.0",eh=5000',
    b'Ds5=6767,iX=Number(process.env.PORT||process.env.SUPERMEMORY_PORT||Ds5)',
    b'return new Response("Service is starting",{status:503})},port:iX,hostname:mb0})',
    b'v.once("error",()=>e(new QM0(mb0,$))),v.once("listening",()=>{v.close((u)=>{if(u)e(u);else z()})}),'
    b'v.listen($,mb0)',
    b'engine.json");await tu8(T,JSON.stringify({guard:{host:"127.0.0.1",port:z},api_peer:{host:"127.0.0.1",port:z+1},'
    b'metrics:{host:"127.0.0.1",port:z+2}',
    b'r=Bun.spawn([v,"start","--config",T]',
    b'if(process.env.SUPERMEMORY_NO_UPDATE_CHECK==="1")return null;',
    b'zN=`https://github.com/${xd8}/releases',
    b'H(),Na("0 12 * * *",()=>void H())',
    b'function vH0(){return process.env.SUPERMEMORY_DISABLE_TELEMETRY==="true"||'
    b'process.env.SUPERMEMORY_DISABLE_TELEMETRY==="1"}',
    b'function l40($,z="PostHog tracking error:"){if(vH0())return;',
    b'TH0=process.env.SUPERMEMORY_POSTHOG_HOST??"https://us.i.posthog.com"',
    b'HO8(u),Na("0 */6 * * *",()=>HO8(u))',
    b'function Ih5(){if(process.env.SUPERMEMORY_NO_OPEN==="1")return!1;',
    b'case"win32":return{command:"cmd",args:["/c","start","",$]}',
    b'Fp4="Local User",Lp4="api-key"',
    b'var hp4=64;function op4',
    b'await sp4($,384).catch',
    b'if($.apiKey)z.push(["api key",$.apiKey])',
    b'let z=[$.get("x-supermemory-api-key"),$.get("x-api-key"),$.get("Authorization")?.replace',
    b'function hn5($){let z=new URL($.url).hostname;if(z==="localhost"||z==="127.0.0.1"||z==="::1")return!0;',
    b'function jn5(){return async($,z)=>{if(!W3().features.selfHosted)return z();if($.get("user"))return z();'
    b'if(!hn5($.req.raw))return z();',
    b'$.set("org",e.org),$.set("memberRole","owner"),$.set("isRestricted",!1)',
    b'_$=VY5(process.env.SUPERMEMORY_DATA_DIR??"./.supermemory")',
    b'var fQ=1e4,X$0=0',
    b'T=hl8({name:"openai",apiKey:u,baseURL:O}).embeddingModel(e)',
    b'let z=await id8();if(z){let e=globalThis;if(!e.__SUPERMEMORY_RIVET_NAPI__',
]
FILLER = b"\n;/* ---- */\n" + b"x" * 300 + b"\n"
BUNDLE = FILLER.join(LITERALS) + FILLER + b"\n---- Bun! ----\n"


def pe_file(bun: bytes | None, *, text: bytes = b"\0" * 64, bun_past_end: bool = False) -> bytes:
    """A minimal PE image: MZ, the PE signature at 0x80, a COFF header, a 16-byte optional header, a .text section
    holding ``text`` and (unless None) a .bun section holding ``bun`` - its raw size rounded up, its virtual size
    exact, as bun --compile writes it."""
    secs = [(b".text", text)] + ([(b".bun", bun)] if bun is not None else [])
    head = bytearray(b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x80) + b"\0" * (0x80 - 0x40))
    opt = b"\0" * 16
    head += b"PE\0\0" + struct.pack("<HHIIIHH", 0x8664, len(secs), 0, 0, 0, len(opt), 0x22) + opt
    table_end = len(head) + 40 * len(secs)
    raw_at = (table_end + 511) // 512 * 512
    table, body = b"", b""
    for name, data in secs:
        raw = (len(data) + 511) // 512 * 512
        vsize = len(data) + (1 << 20 if bun_past_end and name == b".bun" else 0)
        table += name.ljust(8, b"\0") + struct.pack("<IIII", vsize, 0x1000, raw, raw_at + len(body)) + b"\0" * 16
        body += data.ljust(raw, b"\0")
    return bytes(head) + table + b"\0" * (raw_at - table_end) + body


def world(base: Path, image: bytes, *, record: dict | None = None) -> tuple[Path, str]:
    """<base>/runs/_install/a8-supermemory-bin/b2/bin_record.json naming <base>/bin/<binary> = ``image``; returns
    (runs root, the record's sha256)."""
    runs = base / "runs"
    binp = base / "bin" / "supermemory-server-windows-x64.exe"
    binp.parent.mkdir(parents=True, exist_ok=True)
    binp.write_bytes(image)
    rec = {"window": "a8-supermemory-bin", "run": "b2", "repo": "supermemoryai/supermemory", "tag": "server-v0.0.8",
           "commit": "5d2b5855fe492a3682a1cde4a255e2db0c4db595", "version": "0.0.8", "binary_started": False,
           "path": str(binp), "sha256": hashlib.sha256(image).hexdigest(), "bytes": len(image), "problems": []}
    rec.update(record or {})
    f = runs / "_install" / "a8-supermemory-bin" / "b2" / "bin_record.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode("utf-8")
    f.write_bytes(raw)
    return runs, hashlib.sha256(raw).hexdigest()


TMP = Path(tempfile.mkdtemp(prefix="v3_sm_bundle_"))
try:
    IMAGE = pe_file(BUNDLE)
    LO = IMAGE.index(BUNDLE)
    HI = LO + len(BUNDLE)

    print("\n- the pin and the bundle -")
    runs, rsha = world(TMP / "ok", IMAGE)
    rec, err = _try(lambda: SB.run_facts(runs, run="f1", record_sha256=rsha))
    out = runs / "_facts" / "a8-sm-bundle" / "f1" / "bundle_facts.json"
    on_disk, derr = _try(lambda: json.loads(out.read_text(encoding="utf-8")))
    check("SMB-1: a clean read - the pin (record sha256, binary sha256 and size), the .bun section by the PE header, "
          "every fact once; the record written at _facts/a8-sm-bundle/<run>/bundle_facts.json says no network, "
          "nothing executed, no problem",
          err is None and derr is None and on_disk == rec and rec["problems"] == [] and rec["network"] is False
          and rec["executed"] is False and rec["pin"]["binary_sha256"] == hashlib.sha256(IMAGE).hexdigest()
          and rec["pin"]["bytes"] == len(IMAGE) and rec["pin"]["record_sha256"] == rsha
          and rec["bundle"] == {"section": ".bun", "offset": LO, "bytes": len(BUNDLE)}
          and sorted(rec["facts"]) == sorted(SB.FACTS), str(err or derr))

    def fact_ok(name: str, literal: bytes, values: list[str]) -> bool:
        f = rec["facts"][name]
        off = IMAGE.index(literal)
        w = SB.WINDOW_BYTES
        return (f["offset"] == off and f["literal"] == literal.decode("utf-8") and f["values"] == values
                and f["window_sha256"] == hashlib.sha256(IMAGE[max(LO, off - w):min(HI, off + len(literal) + w)])
                .hexdigest())
    fk, fkerr = _try(lambda: fact_ok("bind_host", LITERALS[1], ["mb0", "0.0.0.0"])
                     and fact_ok("serve", LITERALS[3], ["mb0"]) and fact_ok("version", LITERALS[0], ["0.0.8"])
                     and fact_ok("local_host_check", LITERALS[21], ["url"])
                     and fact_ok("local_role", LITERALS[23], ["owner"]))
    check("SMB-2: each fact is its file offset, its literal, its captured values and the sha256 of WINDOW_BYTES "
          "each side clipped to the section - bind_host, serve, version, local_host_check (F6 hn5), local_role (F6 jn5)",
          fk is True, str(fkerr))

    print("\n- R8: the server's bind -")
    check("SMB-3 (R8): the bound constant 0.0.0.0, used by the serve call and the pre-bind listen, is "
          "blocked:bind-all-interfaces - the probe waits for the owner's inbound block rule; port 6767 from "
          "PORT or SUPERMEMORY_PORT",
          err is None and rec["r8"]["host"] == "0.0.0.0" and rec["r8"]["all_interfaces"] is True
          and rec["r8"]["loopback_only"] is False and rec["r8"]["r8"].startswith("blocked:bind-all-interfaces")
          and rec["r8"]["port_default"] == 6767 and rec["r8"]["port_env"] == ["PORT", "SUPERMEMORY_PORT"]
          and rec["r8"]["problems"] == [], str(err or (rec or {}).get("r8")))

    def r8_of(bundle: bytes, label: str):
        img = pe_file(bundle)
        rr, rs = world(TMP / label, img)
        return SB.run_facts(rr, run="f1", record_sha256=rs)["r8"]
    lb, lberr = _try(lambda: r8_of(BUNDLE.replace(b'mb0="0.0.0.0"', b'mb0="127.0.0.1"'), "lb"))
    check("SMB-4 (R8): a bound constant 127.0.0.1 used by both calls is clear - loopback only",
          lberr is None and lb["r8"] == "clear" and lb["loopback_only"] is True and lb["all_interfaces"] is False,
          str(lberr or lb))
    v6, v6err = _try(lambda: r8_of(BUNDLE.replace(b'mb0="0.0.0.0"', b'mb0="::"'), "v6"))
    ot, oterr = _try(lambda: r8_of(BUNDLE.replace(b'mb0="0.0.0.0"', b'mb0="192.168.1.7"'), "ot"))
    check("SMB-5 (R8): '::' is blocked:bind-all-interfaces and any other host blocked:bind-not-loopback - loopback is "
          "an allow-list, never a deny-list",
          v6err is None and oterr is None and v6["r8"].startswith("blocked:bind-all-interfaces")
          and ot["r8"].startswith("blocked:bind-not-loopback") and not ot["loopback_only"], str(v6err or oterr))
    mm, mmerr = _try(lambda: r8_of(BUNDLE.replace(b'mb0="0.0.0.0"', b'mb0="127.0.0.1"')
                                   .replace(b"port:iX,hostname:mb0})", b"port:iX,hostname:Zz9})"), "mm"))
    mp, mperr = _try(lambda: r8_of(BUNDLE.replace(b'mb0="0.0.0.0"', b'mb0="127.0.0.1"')
                                   .replace(b"v.listen($,mb0)", b"v.listen($,Zz9)"), "mp"))
    check("SMB-6 (R8): a loopback constant the serve call (or the pre-bind listen) does not use is not clear - "
          "blocked:bind-not-loopback with the variable named as a problem",
          mmerr is None and mperr is None and mm["r8"].startswith("blocked:bind-not-loopback")
          and any("Zz9" in p for p in mm["problems"]) and mp["r8"].startswith("blocked:bind-not-loopback")
          and any("Zz9" in p for p in mp["problems"]), str(mmerr or mperr or (mm, mp)))

    print("\n- refusals: the pin -")
    rp, rps = world(TMP / "moved", IMAGE)
    bad = bytearray(IMAGE)
    bad[HI - 5] ^= 1
    Path(json.loads((rp / "_install/a8-supermemory-bin/b2/bin_record.json").read_text(encoding="utf-8"))["path"]) \
        .write_bytes(bytes(bad))
    check("SMB-7 (pin): a binary one byte off its record's sha256 is refused by name, nothing written",
          refused(lambda: SB.run_facts(rp, run="f1", record_sha256=rps), "sha256")
          and not (rp / "_facts").exists())
    rr2, _ = world(TMP / "recmoved", IMAGE)
    check("SMB-8 (pin): a bin_record.json that is not the fixed one (another sha256) is refused by name",
          refused(lambda: SB.run_facts(rr2, run="f1", record_sha256="0" * 64), "bin_record.json"))
    cases = {"problems": {"problems": ["x"]}, "binary_started": {"binary_started": True}, "run": {"run": "b1"},
             "window": {"window": "a7-docs"}, "tag": {"tag": "server-v0.0.7"}, "commit": {"commit": "1" * 40},
             "bytes": {"bytes": 1}}
    bad_rec = {}
    for k, patch in cases.items():
        rk, rks = world(TMP / f"rec_{k}", IMAGE, record=patch)
        bad_rec[k] = refused(lambda: SB.run_facts(rk, run="f1", record_sha256=rks), k)
    check("SMB-9 (pin): a record with problems, a started binary, another run, window, tag or commit, or a size that "
          "is not the file's, is refused naming the field", all(bad_rec.values()), str(bad_rec))
    check("SMB-10 (pin): the fixed record is freeze_a7's - BIN_RECORD_SHA256 is the sha256 CLEARED_A7 names for "
          "_install/a8-supermemory-bin/b2/bin_record.json (one source)",
          SB is not None and re.search(r'"_install/a8-supermemory-bin/b2/bin_record\.json": "([0-9a-f]{64})"',
                                       (ROOT / "research/v3/freeze_a7.py").read_text(encoding="utf-8")).group(1)
          == SB.BIN_RECORD_SHA256, str(getattr(SB, "BIN_RECORD_SHA256", None)))

    print("\n- refusals: the bundle and the facts -")
    rn, rns = world(TMP / "nobun", pe_file(None, text=BUNDLE))
    check("SMB-11 (bundle): an image with no .bun section is refused by name - the literals in .text are never read",
          refused(lambda: SB.run_facts(rn, run="f1", record_sha256=rns), ".bun"))
    rpe, rpes = world(TMP / "past", pe_file(BUNDLE, bun_past_end=True))
    rmz, rmzs = world(TMP / "nomz", b"XX" + IMAGE[2:])
    check("SMB-12 (bundle): a .bun section that reaches past the file's end, and a file with no PE header, are "
          "refused by name",
          refused(lambda: SB.run_facts(rpe, run="f1", record_sha256=rpes), ".bun", "past")
          and refused(lambda: SB.run_facts(rmz, run="f1", record_sha256=rmzs), "PE"))
    miss = BUNDLE.replace(LITERALS[3], b"return new Response(1)")
    rmi, rmis = world(TMP / "miss", pe_file(miss, text=LITERALS[3]))
    check("SMB-13 (facts): a fact the bundle does not hold is refused as missing, by its name - even when the "
          "literal sits in another section",
          refused(lambda: SB.run_facts(rmi, run="f1", record_sha256=rmis), "serve", "missing"))
    two = BUNDLE + FILLER + LITERALS[1]
    rtw, rtws = world(TMP / "two", pe_file(two))
    check("SMB-14 (facts): a fact the bundle holds twice is refused as ambiguous, by its name - never the first one",
          refused(lambda: SB.run_facts(rtw, run="f1", record_sha256=rtws), "bind_host", "ambiguous"))
    keyb = BUNDLE + FILLER + b'uH0=process.env.K??"phc_ABCDEFGHijklmnop123"'
    kd, kderr = _try(lambda: SB.read_facts(keyb, 0, len(keyb),
                                           {**SB.FACTS, "tkey": rb'uH0=process\.env\.K\?\?"([^"]+)"'}))
    check("SMB-15 (facts): a fact whose literal holds a key-like token (phc_, sk-, sm_ + 8 key characters) refuses "
          "by name - the vendor's embedded telemetry key is never copied into a record",
          refused(lambda: SB.read_facts(keyb, 0, len(keyb), {**SB.FACTS, "tkey": rb'uH0=process\.env\.K\?\?"([^"]+)"'}),
                  "tkey", "key"), str(kderr or kd))

    print("\n- the spawn's switches and the auth facts -")
    check("SMB-16: the spawn's declared vendor switches are exactly SUPERMEMORY_NO_UPDATE_CHECK=1, "
          "SUPERMEMORY_DISABLE_TELEMETRY=1, SUPERMEMORY_NO_OPEN=1 - each a value the bundle's own comparison accepts",
          err is None and SB.SPAWN_ENV == {"SUPERMEMORY_NO_UPDATE_CHECK": "1", "SUPERMEMORY_DISABLE_TELEMETRY": "1",
                                           "SUPERMEMORY_NO_OPEN": "1"}
          and rec["switches"] == {"env": SB.SPAWN_ENV, "problems": []}, str(err or (rec or {}).get("switches")))
    rsw, rsws = world(TMP / "sw", pe_file(BUNDLE.replace(b'SUPERMEMORY_NO_UPDATE_CHECK==="1"',
                                                          b'SUPERMEMORY_NO_UPDATE_CHECK==="yes"')))
    sw, swerr = _try(lambda: SB.run_facts(rsw, run="f1", record_sha256=rsws))
    check("SMB-17: a switch value the bundle does not accept (it compares with 'yes', the spawn sets '1') is a "
          "problem naming the variable - never a silent pass",
          swerr is None and any("SUPERMEMORY_NO_UPDATE_CHECK" in p for p in sw["switches"]["problems"])
          and any("SUPERMEMORY_NO_UPDATE_CHECK" in p for p in sw["problems"]), str(swerr or sw and sw["switches"]))
    check("SMB-18 (F6): the auth facts - 'localhost' is the request URL's hostname (hn5: localhost, 127.0.0.1, ::1), "
          "the unkeyed local request's role is owner (jn5), the key's headers, its file api-key with mode 0600, "
          "64 characters, printed in the banner",
          err is None and rec["auth"] == {"localhost_by": "url", "unkeyed_local_role": "owner",
                                          "key_headers_first": "x-supermemory-api-key", "key_file": "api-key",
                                          "key_mode": "0o600", "key_length": 64, "key_in_banner": True},
          str(err or (rec or {}).get("auth")))

    print("\n- the run label -")
    check("SMB-19: a used run label is refused by name - a record is never overwritten",
          refused(lambda: SB.run_facts(runs, run="f1", record_sha256=rsha), "f1"))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 sm bundle: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
