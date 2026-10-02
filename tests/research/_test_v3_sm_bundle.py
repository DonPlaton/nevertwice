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
  values the bundle's own comparisons accept, else a problem by name; the auth facts (F6) are recorded;
* SBF (Q-C6-ROUTE = a): the two clients by their user agents, the branches choosing them linked by name to their env
  facts, the census of every hl8( and Gl( call site (a site outside a branch is a finding, not a problem), the client
  resolve() says the LLM takes, and F-SBF-2 - another provider's variable in the child env is a problem by name.

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
from unittest import mock
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
    b'function bU(){if(Ld)return Ld;let $=r40(),{provider:z,modelId:e,dimensions:v}=$;if(z==="local")r'
    b'eturn Ld={model:kp4({modelId:e,dimensions:v,pooling:Ul8[e]??"mean"}),dimensions:v,provider:z,mod'
    b'elId:e,isLocal:!0},Ld;let u=Sp4(z),T;if(z==="google")T=VU({apiKey:u}).embeddingModel(e);else if('
    b'z==="openai-compatible"){let O=CB0("openai-compatible");if(!O)throw Error("[embeddings] SUPERMEM'
    b'ORY_EMBEDDING_PROVIDER=openai-compatible requires SUPERMEMORY_EMBEDDING_BASE_URL (or OPENAI_BASE'
    b'_URL), e.g. http://localhost:11434/v1 for Ollama.");T=hl8({name:"openai",apiKey:u,baseURL:O}).em'
    b'beddingModel(e)}else{let O=CB0("openai");T=Gl({apiKey:u,...O?{baseURL:O}:{}}).embeddingModel(e)',
    b'let z=await id8();if(z){let e=globalThis;if(!e.__SUPERMEMORY_RIVET_NAPI__',
]
#: SM3 (the auditor, 2026-09-30 17:4x: Q-A8S-ENVN = a, the workflow engine on win32, Qs5): the env names the server
#: reads with their defaults, the win32 default engine, the Rivet port formula - appended, the indices above kept.
ENV_LITERALS = [
    b'let z=lV("SUPERMEMORY_EMBEDDING_PROVIDER",$)?.toLowerCase();switch(z){case void 0:case"local":return"local";'
    b'case"openai":return"openai";case"openai-compatible":case"openai_compatible":return"openai-compatible";',
    b'function ef4($,z){if($==="local")return lV("SUPERMEMORY_EMBEDDING_MODEL",z)??'
    b'lV("SUPERMEMORY_LOCAL_EMBEDDING_MODEL",z)??"Xenova/bge-base-en-v1.5";return lV("SUPERMEMORY_EMBEDDING_MODEL",z)??'
    b'($==="google"?"gemini-embedding-001":"text-embedding-3-small")}',
    b'function vf4($,z){let e=lV("SUPERMEMORY_EMBEDDING_BASE_URL",z);if($==="openai-compatible"){let v=e??'
    b'lV("OPENAI_BASE_URL",z)',
    b'function Tf4($,z,e){let v=lV("SUPERMEMORY_EMBEDDING_DIMENSIONS",e);',
    b'function Sp4($){let z=process.env.SUPERMEMORY_EMBEDDING_API_KEY?.trim();if(z)return z;if($==="google"){let '
    b'v=process.env.GEMINI_API_KEY?.trim();if(v)return v;throw Error("[embeddings] SUPERMEMORY_EMBEDDING_PROVIDER=google '
    b'requires GEMINI_API_KEY or SUPERMEMORY_EMBEDDING_API_KEY.")}let e=process.env.OPENAI_API_KEY?.trim();',
    b'"Xenova/bge-m3":1024,"Xenova/all-MiniLM-L6-v2"',
    b'Ul8={"Xenova/bge-m3":"cls"}',
    b'{id:"openai",name:"OpenAI (or OpenAI-compatible: Ollama, vLLM, LM Studio, etc.)",envVars:["OPENAI_API_KEY"]',
    b'{id:"anthropic",name:"Anthropic",envVars:["ANTHROPIC_API_KEY"],promptable:!0},{id:"gemini",name:"Gemini",'
    b'envVars:["GEMINI_API_KEY"],promptable:!0},{id:"groq",name:"Groq",envVars:["GROQ_API_KEY"],promptable:!0},'
    b'{id:"workers-ai",name:"Cloudflare Workers AI",envVars:["WORKERS_AI_API_KEY","CLOUDFLARE_ACCOUNT_ID"],promptable:!1},'
    b'{id:"vertex",name:"Vertex AI",envVars:["GOOGLE_VERTEX_PROJECT_ID","GOOGLE_VERTEX_LOCATION"]',
    b'$o=process.env.OPENAI_API_KEY?process.env.OPENAI_BASE_URL?hl8({name:"openai",apiKey:process.env.'
    b'OPENAI_API_KEY,baseURL:process.env.OPENAI_BASE_URL}):Gl({apiKey:process.env.OPENAI_API_KEY}):nul'
    b'l',
    b'z$2=process.env.OPENAI_FAST_MODEL??process.env.OPENAI_MODEL??"gpt-5.1",e$2=process.env.OPENAI_TEXT_MODEL??'
    b'process.env.OPENAI_MODEL??"gpt-5.1",',
    b'function qd8($="win32",z=process.env.WORKFLOW_ENGINE){if(z)return z.trim().toLowerCase()==="direct";'
    b'return $==="win32"}',
    b'if(qd8())return process.env.WORKFLOW_ENGINE="direct","direct";',
    b'async function Qs5(){let $=1e4+iX*3%50000;for(let z=$;z<$+60;z+=3)',
]
#: F8 (the auditor's Q-SM-TOOLS = a): the memory agent - its call, its system text, its seven tools, their schemas
TOOLS_LIT = (b'Z={searchMemories:TX({description:"Search related memories.",inputSchema:p.object({query:p.string()})}),'
             b'readChunks:TX({description:`Read more chunks. There are ${O} total chunks available.`,'
             b'inputSchema:p.object({start:p.number()})}),searchDocumentChunks:TX({description:"Search chunks.",'
             b'inputSchema:p.object({query:p.string()})}),getMemoryDetails:TX({description:"Get a memory.",'
             b'inputSchema:p.object({memoryId:p.string()})}),linkMemories:TX({description:"Link two.",'
             b'inputSchema:p.object({relation:Cm0})}),forgetMemory:TX({description:"Forget one.",'
             b'inputSchema:p.object({memoryId:p.string()})}),CreateMemory:TX({description:"Create a new memory.",'
             b'inputSchema:O72,execute:(c)=>d("CreateMemory",c,async()=>{return{skipped:!0}})})},n=ng4(')
AGENT_LITERALS = [
    b'let c=await ky({model:wb(),system:x,prompt:n,tools:Z,stopWhen:hI8(40),maxRetries:2,maxOutputTokens:12000,'
    b'experimental_telemetry:{isEnabled:!vH0(),functionId:"self-hosted-memory-agent"}})',
    b'x="You are a memory extraction agent. Use tools to inspect context."',
    TOOLS_LIT,
    b'O72=p.object({memory:p.string().min(1).describe("The durable fact to remember."),temporalContext:x78})',
    b'Cm0=p.enum(["updates","extends","derives"]),x78=p.object({documentDate:p.string().nullable().optional(),'
    b'eventDate:p.array(p.string()).nullable().optional()})',
]
LITERALS += ENV_LITERALS + AGENT_LITERALS
#: SBF (Q-C6-ROUTE = a; the auditor's literals, sbf_literals_b2.json, 2026-10-01): the clients' definitions -
#: hl8 (createOpenAICompatible by its user agent) and Gl (createOpenAI: its head, the 30 bytes he did not quote,
#: its user agent) - the default instance Gr4, CB0, the two Gl sites outside the $o branch (qX0, jX0) and their
#: uses, the three Workers clients (dX0, cy, vo: STAND-INS, their literals were not quoted), the model chain S55,
#: the agent's models, and a harmless noise literal; b2 has zero bare code references
GAP = b"/*" + b"-" * 26 + b"*/"
ROUTE_LITERALS = [
    b'function hl8($){let z=xU($.baseURL),e=$.name,v={...$.apiKey&&{Authorization:`Bearer ${$.apiKey}`'
    b'},...$.headers},u=()=>VO(v,`ai-sdk/openai-compatible/${Ar4}`',
    b'function Gl($={}){var z,e;let v=(z=xU(wx({settingValue:$.baseURL,environmentVariableName:"OPENAI'
    b'_BASE_URL"})))!=null?z:"https://api.openai.com/v1",'
    + GAP
    + b'T=()=>VO({Authorization:`Bearer ${oa0({apiKey:$.apiKey,environmentVariableName:"OPENAI_API_KEY",'
    + b'description:"OpenAI"})}`,"OpenAI-Organization":$.organization,"OpenAI-Project":$.project,...$.he'
    + b'aders},`ai-sdk/openai/${Ur4}`'
    ,
    b'Gr4=Gl()',
    b'function CB0($){let z=process.env.SUPERMEMORY_EMBEDDING_BASE_URL?.trim();if(z)return z;if($==="o'
    b'penai-compatible")return process.env.OPENAI_BASE_URL?.trim()||void 0;return}',
    b'qX0=X4.OPENAI_API_KEY?Gl({apiKey:X4.OPENAI_API_KEY}):null',
    b'jX0=m4.OPENAI_API_KEY?Gl({apiKey:m4.OPENAI_API_KEY}):null',
    b'...qX0?[qX0("gpt-5-nano")]:[]',
    b'...jX0?[jX0("gpt-5-nano")]:[]',
    b'dX0=X4.WORKERS_AI_API_KEY&&X4.CLOUDFLARE_ACCOUNT_ID?Gl({apiKey:X4.WORKERS_AI_API_KEY,'
    b'baseURL:"https://workers.stand-in/ai/v1"}):null',
    b'cy=m4.WORKERS_AI_API_KEY&&m4.CLOUDFLARE_ACCOUNT_ID?Gl({apiKey:m4.WORKERS_AI_API_KEY,'
    b'baseURL:"https://workers.stand-in/ai/v1"}):null',
    b'vo=process.env.WORKERS_AI_API_KEY&&process.env.CLOUDFLARE_ACCOUNT_ID?Gl({apiKey:process.env.'
    b'WORKERS_AI_API_KEY,baseURL:"https://workers.stand-in/ai/v1"}):null',
    b'S55=()=>r90([...$o?[$o(z$2)]:[],...eo?[eo("claude-haiku-4-5")]:[],...sm?[sm("gemini-3.1-flash-li'
    b'te-preview")]:[],...jm?[jm("gemini-3.1-flash-lite-preview")]:[],...zo?[zo("openai/gpt-oss-20b")]'
    b':[],...vo?[vo.chat("@cf/openai/gpt-oss-20b")]:[]])',
    b'function W3(){return u$2}function $$($){let z=W3(),e=z.services.backgroundTasks($.executionCtx),'
    b'v=z.services.environment($.env),u=z.services.extraction().pipe(le(v)),T=z.services.workflowTrigg'
    b'er().pipe(le($O(e,v))),O=z.services.vectordb().pipe(le(v));return $O(e,v,u,T,O)}var u$2,x_=()=>W'
    b'3().models.fast(),wb=()=>W3().models.text()',
    b'var routeNoise="stand-in"',
]
LITERALS += ROUTE_LITERALS
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

    print("\n- SM3: the env names the server reads, the win32 engine, the local bge-m3 (the auditor, 17:4x) -")
    NEW3 = {"embed_provider", "embed_model", "embed_base_url", "embed_dimensions", "embed_api_key", "embed_local_bge_m3",
            "embed_local_bge_m3_pooling", "llm_openai_entry", "llm_other_providers", "llm_openai_client", "llm_models",
            "workflow_direct_win32", "workflow_direct_set", "rivet_port_formula", "workflow_rivet_branch"}
    check("SMB-20: the SM3 facts are recorded once each, and the Rivet branch is named for what it is - "
          "workflow_rivet_branch, no workflow_default",
          err is None and NEW3 <= set(rec["facts"]) and "workflow_default" not in rec["facts"]
          and rec["facts"]["llm_models"]["values"] == ["OPENAI_FAST_MODEL", "OPENAI_MODEL", "gpt-5.1", "OPENAI_TEXT_MODEL",
                                                       "gpt-5.1"], str(err or sorted(NEW3 - set((rec or {}).get("facts", {})))))
    WANT_READS = {
        "SUPERMEMORY_EMBEDDING_PROVIDER": "local", "SUPERMEMORY_EMBEDDING_MODEL": "Xenova/bge-base-en-v1.5",
        "SUPERMEMORY_LOCAL_EMBEDDING_MODEL": "Xenova/bge-base-en-v1.5", "SUPERMEMORY_EMBEDDING_BASE_URL": "OPENAI_BASE_URL",
        "SUPERMEMORY_EMBEDDING_DIMENSIONS": "the model's native size", "SUPERMEMORY_EMBEDDING_API_KEY": "OPENAI_API_KEY",
        "OPENAI_API_KEY": None, "OPENAI_BASE_URL": None, "OPENAI_MODEL": "gpt-5.1", "OPENAI_FAST_MODEL": "OPENAI_MODEL",
        "OPENAI_TEXT_MODEL": "OPENAI_MODEL", "PORT": "SUPERMEMORY_PORT", "SUPERMEMORY_PORT": "6767",
        "SUPERMEMORY_DATA_DIR": "./.supermemory", "WORKFLOW_ENGINE": "direct (win32)",
        "SUPERMEMORY_NO_UPDATE_CHECK": None, "SUPERMEMORY_DISABLE_TELEMETRY": None, "SUPERMEMORY_NO_OPEN": None}
    reads = (rec or {}).get("env", {}).get("reads", {})
    check("SMB-21 (Q-A8S-ENVN): every env name the server reads is a fact with its default - the embedder's provider "
          "(local), model, base URL (OPENAI_BASE_URL), dimensions, key (OPENAI_API_KEY); the LLM's key, base URL, model "
          "(gpt-5.1) and the fast and text models (OPENAI_MODEL); the port, the data dir, the engine, the three switches; "
          "the other providers' names must be absent",
          err is None and {k: v["default"] for k, v in reads.items()} == WANT_READS
          and all(v["fact"] in rec["facts"] for v in reads.values())
          and rec["env"]["must_be_absent"] == ["ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "WORKERS_AI_API_KEY",
                                               "CLOUDFLARE_ACCOUNT_ID", "GOOGLE_VERTEX_PROJECT_ID",
                                               "GOOGLE_VERTEX_LOCATION"],
          str(err or {k: v.get("default") for k, v in reads.items()}))
    SPAWN = {"SUPERMEMORY_EMBEDDING_PROVIDER": "openai-compatible",
             "SUPERMEMORY_EMBEDDING_BASE_URL": "http://127.0.0.1:47001/u/r1.u1/v1", "SUPERMEMORY_EMBEDDING_MODEL": "bge-m3@d1",
             "SUPERMEMORY_EMBEDDING_DIMENSIONS": "1024", "OPENAI_API_KEY": "nvt3-token",
             "OPENAI_BASE_URL": "http://127.0.0.1:47002/u/r1.u1/v1", "OPENAI_MODEL": "deepseek-flash", "PORT": "18123",
             "SUPERMEMORY_DATA_DIR": "C:/u/sm_store"}
    dp, dperr = _try(lambda: (SB.declared_problems(SPAWN, rec["env"]),
                              SB.declared_problems({**SPAWN, "SUPERMEMORY_EMBEDDINGS_MODEL": "x"}, rec["env"])))
    check("SMB-22 (Q-A8S-ENVN): the spawn's declared names are all names the server reads - a name it does not read "
          "(SUPERMEMORY_EMBEDDINGS_MODEL, a typo that would leave the local embedder on) is a problem by name",
          dperr is None and dp[0] == [] and any("SUPERMEMORY_EMBEDDINGS_MODEL" in p for p in dp[1]), str(dperr or dp))
    fb, fberr = _try(lambda: [SB.declared_problems({**SPAWN, n: "x"}, rec["env"]) for n in SB.NOT_DECLARED])
    check("SMB-22b: a name the spawn must never declare - OPENAI_FAST_MODEL, OPENAI_TEXT_MODEL, WORKFLOW_ENGINE, "
          "SUPERMEMORY_LOCAL_EMBEDDING_MODEL, the three switches (theirs is TELEMETRY_OFF), "
          "SUPERMEMORY_EMBEDDING_API_KEY (the embedder's key is OPENAI_API_KEY, the arm's proxy token) - is a problem "
          "by name, each", fberr is None and len(fb) == 8 and all(len(p) == 1 for p in fb)
          and sorted(SB.NOT_DECLARED) == sorted(["OPENAI_FAST_MODEL", "OPENAI_TEXT_MODEL", "WORKFLOW_ENGINE",
                                                  "SUPERMEMORY_LOCAL_EMBEDDING_MODEL", "SUPERMEMORY_NO_UPDATE_CHECK",
                                                  "SUPERMEMORY_DISABLE_TELEMETRY", "SUPERMEMORY_NO_OPEN",
                                                  "SUPERMEMORY_EMBEDDING_API_KEY"]), str(fberr or fb))
    rs, rserr = _try(lambda: (SB.resolve(SPAWN, rec["env"]), SB.resolve({}, rec["env"])))
    check("SMB-23: what the declared env resolves to by the bundle's own rules - the embedder openai-compatible at the "
          "leg with our tag and 1024 dimensions, the fast and text models both OPENAI_MODEL, the client hl8; no vendor "
          "default reached. With nothing declared, every vendor default is named (local bge-base, gpt-5.1 twice, $o null "
          "with no OPENAI_API_KEY)",
          rserr is None and rs[0] == {"embedding": {"provider": "openai-compatible", "model": "bge-m3@d1",
                                                     "base_url": "http://127.0.0.1:47001/u/r1.u1/v1", "dimensions": "1024"},
                                       "llm": {"fast": "deepseek-flash", "text": "deepseek-flash",
                                               "base_url": "http://127.0.0.1:47002/u/r1.u1/v1",
                                               "client": "openai-compatible", "path": "/chat/completions"},
                                       "vendor_defaults": []}
          and rs[1]["vendor_defaults"][:3] == ["embedding provider local (Xenova/bge-base-en-v1.5)",
                                               "llm fast model gpt-5.1", "llm text model gpt-5.1"]
          and len(rs[1]["vendor_defaults"]) == 4 and "$o" in rs[1]["vendor_defaults"][3]
          and "OPENAI_API_KEY" in rs[1]["vendor_defaults"][3] and rs[1]["llm"].get("client", "unset") is None, str(rserr or rs))
    wf_win = (rec or {}).get("workflow")
    rl, rlerr = _try(lambda: (lambda w: SB.run_facts(w[0], run="f1", record_sha256=w[1]))(
        world(TMP / "linux", pe_file(BUNDLE.replace(b'function qd8($="win32"', b'function qd8($="linux"'))))["workflow"])
    check("SMB-24: the workflow engine - on this build (qd8's platform win32) with no WORKFLOW_ENGINE the default is "
          "direct, in process (no Rivet child); the Rivet port formula recorded as outside that default. A build whose "
          "platform is not win32 defaults to rivet",
          err is None and wf_win == {"platform": "win32", "env": "WORKFLOW_ENGINE", "default": "direct",
                                     "rivet_ports": {"base": "1e4 + port*3 % 50000", "step": 3, "span": 60,
                                                     "applies_by_default": False}}
          and rlerr is None and rl["default"] == "rivet" and rl["rivet_ports"]["applies_by_default"] is True,
          str(err or rlerr or (wf_win, rl)))
    check("SMB-25: the product's own local bge-m3 (ONNX in process, 1024 dimensions, cls pooling) is recorded - an E5 "
          "line; the stand's embedder is ours through the leg (the auditor: another runtime, other vectors, §3.4)",
          err is None and (rec or {}).get("embedding_local") == {"model": "Xenova/bge-m3", "dimensions": 1024,
                                                                  "pooling": "cls"},
          str(err or (rec or {}).get("embedding_local")))

    SEVEN = ["searchMemories", "readChunks", "searchDocumentChunks", "getMemoryDetails", "linkMemories", "forgetMemory",
             "CreateMemory"]
    sha = lambda x: hashlib.sha256(x).hexdigest()  # noqa: E731
    check("SMB-26 (F8, Q-SM-TOOLS): the memory agent - its seven tools in order and the sha256 of their definitions "
          "(the baseline the proxy will pin), each input schema's sha256 (CreateMemory's O72, the relation's Cm0), the "
          "call's limits (40 steps, 12000 output tokens a step, 2 retries), its telemetry gate vH0 and its function id, "
          "the sha256 of its system text",
          err is None and (rec or {}).get("tools") == {
              "names": SEVEN, "sha256": sha(TOOLS_LIT), "max_steps": 40, "max_output_tokens": 12000, "max_retries": 2,
              "telemetry_gate": "vH0", "function_id": "self-hosted-memory-agent",
              "schemas": {"CreateMemory": sha(AGENT_LITERALS[3]), "relation": sha(AGENT_LITERALS[4])},
              "system_sha256": sha(b"You are a memory extraction agent. Use tools to inspect context.")},
          str(err or (rec or {}).get("tools")))
    eighth = TOOLS_LIT.replace(b'CreateMemory:TX(', b'runShell:TX({description:"Run.",inputSchema:p.object({})}),'
                                                    b'CreateMemory:TX(')
    t8, t8err = _try(lambda: (lambda w: SB.run_facts(w[0], run="f1", record_sha256=w[1]))(
        world(TMP / "eighth", pe_file(BUNDLE.replace(TOOLS_LIT, eighth))))["tools"])
    check("SMB-27 (F8): the tool names are read from the definitions, never from a list - an eighth tool in the bundle "
          "is an eighth name and another sha256",
          t8err is None and t8["names"] == SEVEN[:6] + ["runShell", "CreateMemory"] and t8["sha256"] == sha(eighth),
          str(t8err or t8))

    print("\n- SBF: which client a model call takes (Q-C6-ROUTE = a, the auditor's literals of 2026-10-01) -")
    rt = (rec or {}).get("route") or {}

    def at(literal: bytes, name: bytes = b"Gl(") -> int:
        return IMAGE.index(literal) + literal.index(name)
    check("SMB-28 (Q-C6-ROUTE): the two clients by their definitions - hl8 is createOpenAICompatible by its user agent "
          "(ai-sdk/openai-compatible: chat completions), Gl is createOpenAI (ai-sdk/openai: the Responses API) with its "
          "base URL from OPENAI_BASE_URL, else the vendor's",
          err is None and rt.get("clients") == {
              "openai-compatible": {"name": "hl8", "user_agent": "ai-sdk/openai-compatible", "path": "/chat/completions",
                                    "definition": at(ROUTE_LITERALS[0], b"hl8(")},
              "openai": {"name": "Gl", "user_agent": "ai-sdk/openai", "path": "/responses",
                         "definition": at(ROUTE_LITERALS[1]), "base_url_env": "OPENAI_BASE_URL",
                         "default_base_url": "https://api.openai.com/v1"}}, str(err or rt.get("clients")))
    br = rt.get("branches") or {}
    arms = {k: [(a.get("client"), a.get("name"), a.get("excluded_by") is not None) for a in v.get("arms", [])]
            for k, v in br.items()}
    check("SMB-29 (Q-C6-ROUTE): each branch choosing a client, its arms in the order the literal calls them - $o: hl8 "
          "with OPENAI_BASE_URL set, else Gl; bU: hl8 for provider openai-compatible, else Gl - linked by name to the env "
          "fact that reads the variable deciding it; each Gl arm names the rule that keeps our spawn off it",
          err is None and arms == {"llm_client_branch": [("openai-compatible", "hl8", False), ("openai", "Gl", True)],
                                   "embed_client_branch": [("openai-compatible", "hl8", False), ("openai", "Gl", True)]}
          and {k: (v.get("env"), v.get("env_fact")) for k, v in br.items()}
          == {"llm_client_branch": ("OPENAI_BASE_URL", "llm_openai_client"),
              "embed_client_branch": ("SUPERMEMORY_EMBEDDING_PROVIDER", "embed_provider")}, str(err or br))
    wb = SB.WINDOW_BYTES
    want_f = sorted(({"client": "openai", "name": "Gl", "offset": at(x), "finding": "call site outside the branch",
                      "window_sha256": hashlib.sha256(IMAGE[max(LO, at(x) - wb):min(HI, at(x) + wb)]).hexdigest()}
                     for x in (ROUTE_LITERALS[i] for i in (2, 4, 5, 8, 9, 10))), key=lambda f: f["offset"])
    check("SMB-30 (the auditor's census L6): hl8 has 3 call sites and no bare reference, Gl 9 and 0 - each site a "
          "definition, a branch's arm, or a finding 'call site outside the branch' with its offset and window (Gr4, qX0, "
          "jX0, the Workers clients); a finding is recorded, never a problem - the clean read has none",
          err is None and rt.get("calls") == {"openai-compatible": 3, "openai": 9}
          and rt.get("bare") == {"openai-compatible": 0, "openai": 0}
          and sorted(rt.get("findings") or [], key=lambda f: f["offset"]) == want_f
          and rt.get("problems") == [] and rec["problems"] == [],
          str(err or {k: rt.get(k) for k in ("calls", "bare", "problems")}))

    def route_of(bundle: bytes, label: str) -> dict:
        w = world(TMP / label, pe_file(bundle))
        return SB.run_facts(w[0], run="f1", record_sha256=w[1])["route"]
    noise = ROUTE_LITERALS[13]
    r_more, e_more = _try(lambda: route_of(BUNDLE.replace(noise, noise + b';Kq=Gl({apiKey:"x"})'), "route_more"))
    r_alias, e_alias = _try(lambda: route_of(BUNDLE.replace(noise, noise + b";let f=Gl;"), "route_alias"))
    r_other, e_other = _try(lambda: route_of(BUNDLE.replace(noise, noise + b";x.Gl(1),WGl(2),$Gl(3),Glx(4)"),
                                             "route_other"))
    check("SMB-31 (the census): one more Gl call site, or one more bare Gl (an alias a call could hide behind), is a "
          "problem naming the counts - never a silent pass; a member x.Gl(, WGl(, $Gl( or Glx( is another name",
          e_more is None and any("Gl has 10 call sites and 0 bare" in x for x in r_more["problems"])
          and e_alias is None and any("Gl has 9 call sites and 1 bare" in x for x in r_alias["problems"])
          and e_other is None and r_other["problems"] == [], str(e_more or e_alias or e_other or (r_more, r_alias)))
    def asset_pinned_route(bundle, label, literal, expected_sha=None):
        lo = SB.bun_section(pe_file(bundle))[0]
        pins = ((lo + bundle.index(literal), len(literal), expected_sha or hashlib.sha256(literal).hexdigest()),)
        with mock.patch.object(SB, "ROUTE_ASSETS", pins, create=True):
            return route_of(bundle, label)

    asset_noise = b';let asset="' + b"A" * 140 + b"+Gl+" + b"A" * 140 + b'";'
    algebra_noise = b';let algebra=' + b"A+" * 140 + b"Gl+" + b"A+" * 140 + b'1;'
    r_asset, e_asset = _try(lambda: asset_pinned_route(BUNDLE.replace(noise, noise + asset_noise), "route_asset", asset_noise[len(b';let asset='):-1]))
    r_algebra, e_algebra = _try(lambda: route_of(BUNDLE.replace(noise, noise + algebra_noise), "route_algebra"))
    check("SMB-36: pure quoted base64 asset text is excluded with a span hash; an unquoted expression containing Gl remains a bare reference",
          e_asset is None and r_asset["problems"] == [] and len(r_asset.get("ignored_assets", [])) == 1
          and len(r_asset["ignored_assets"][0]["sha256"]) == 64
          and e_algebra is None and any("Gl has 9 call sites and 1 bare" in problem for problem in r_algebra["problems"]),
          str(e_asset or e_algebra or (r_asset.get("problems"), r_algebra.get("problems"))))

    uri_noise = b';let image="data:image/png;base64,' + b"A" * 140 + b"+Gl+" + b"A" * 140 + b'";'
    r_uri, e_uri = _try(lambda: asset_pinned_route(BUNDLE.replace(noise, noise + uri_noise), "route_data_uri", uri_noise[len(b';let image='):-1]))
    check("SMB-37: a fully quoted base64 data URI is witnessed as an asset, not a bare client reference",
          e_uri is None and r_uri["problems"] == [] and len(r_uri.get("ignored_assets", [])) == 1,
          str(e_uri or r_uri.get("problems")))

    braid = b';let a="x"+' + b"A+" * 70 + b"Gl+" + b"A+" * 70 + b'"y";'
    r_braid, e_braid = _try(lambda: route_of(BUNDLE.replace(noise, noise + braid), "route_braid"))
    check("SMB-38: code between closing and opening string quotes cannot masquerade as an ignored asset",
          e_braid is None and any("Gl has 9 call sites and 1 bare" in x for x in r_braid["problems"]), str(e_braid or r_braid["problems"]))
    quoted = asset_noise[len(b';let asset='):-1]
    changed = quoted.replace(b"AAAA", b"BAAA", 1)
    bad_asset = b';let asset=' + changed + b';'
    r_changed, e_changed = _try(lambda: asset_pinned_route(BUNDLE.replace(noise, noise + bad_asset), "route_asset_changed",
                                                         changed, hashlib.sha256(quoted).hexdigest()))
    check("SMB-39: an encoded resource with a changed digest cannot hide a client identifier",
          e_changed is None and any("Gl has 9 call sites and 1 bare" in x for x in r_changed["problems"]), str(e_changed or r_changed["problems"]))

    sec = IMAGE[LO:HI]

    def route_with(fact: str, values: list) -> dict:
        facts = json.loads(json.dumps(rec["facts"]))
        facts[fact]["values"] = values
        return SB.route(sec, LO, facts)
    sw2, sw2e = _try(lambda: (route_with("llm_client_branch", ["OPENAI_BASE_URL", "Gl", "hl8"]),
                              route_with("embed_client_branch", ["local", "google", "openai-compatible", "hl8", "hl8"])))
    check("SMB-32 (Q-C6-ROUTE): a branch whose arms call the clients otherwise - Gl with OPENAI_BASE_URL set, or hl8 on "
          "both arms - is a problem naming the branch",
          sw2e is None and any("llm_client_branch" in x for x in sw2[0]["problems"])
          and any("embed_client_branch" in x for x in sw2[1]["problems"]), str(sw2e or sw2))
    el, ele = _try(lambda: (route_with("llm_openai_client", ["OPENAI_API_KEY", "OPENAI_API_BASE"]),
                            route_with("embed_provider", ["SUPERMEMORY_EMBEDDER", "local", "openai-compatible"])))
    check("SMB-33 (the branch attributed to its env fact by name): an env fact that does not read the variable its "
          "branch decides on - OPENAI_BASE_URL, SUPERMEMORY_EMBEDDING_PROVIDER - is a problem naming both",
          ele is None and any("llm_openai_client" in x and "OPENAI_BASE_URL" in x for x in el[0]["problems"])
          and any("embed_provider" in x and "SUPERMEMORY_EMBEDDING_PROVIDER" in x for x in el[1]["problems"]),
          str(ele or el))
    no_base = {k: v for k, v in SPAWN.items() if k != "OPENAI_BASE_URL"}
    rv, rve = _try(lambda: [SB.resolve(e, rec["env"]) for e in (
        SPAWN, no_base, {**SPAWN, "OPENAI_BASE_URL": ""}, {**SPAWN, "SUPERMEMORY_EMBEDDING_PROVIDER": "openai"},
        {**SPAWN, "SUPERMEMORY_EMBEDDING_PROVIDER": "google"}, {**SPAWN, "OPENAI_API_KEY": ""})])
    check("SMB-34 (Q-C6-ROUTE): resolve() says which client the LLM takes - with OPENAI_BASE_URL hl8, chat "
          "completions; with none, or an empty one (the bundle's test is JavaScript truth), Gl and the Responses API "
          "at the vendor's base URL, a vendor default by name; an empty OPENAI_API_KEY leaves $o null (no client, "
          "a vendor default by name); an embedder provider other than openai-compatible or local (openai, google) is "
          "a vendor default by name",
          rve is None and rv[0]["llm"].get("client", "unset") == "openai-compatible" and rv[0]["llm"].get("path", "unset") == "/chat/completions"
          and rv[0]["vendor_defaults"] == []
          and all(r["llm"].get("client", "unset") == "openai" and r["llm"].get("path", "unset") == "/responses"
                  and any("Gl" in d and "/responses" in d for d in r["vendor_defaults"]) for r in rv[1:3])
          and all(any(f"embedding provider {v}" in d for d in r["vendor_defaults"])
                  for v, r in (("openai", rv[3]), ("google", rv[4])))
          and rv[5]["llm"].get("client", "unset") is None and rv[5]["llm"].get("path", "unset") is None
          and any("$o" in d and "OPENAI_API_KEY" in d for d in rv[5]["vendor_defaults"]), str(rve or rv))
    names = ("ANTHROPIC_API_KEY", "google_generative_ai_api_key", "Groq_Api_Key", "GOOGLE_CLOUD_PROJECT")
    ce, cee = _try(lambda: (SB.child_env_problems(SPAWN, rec["env"]),
                            SB.child_env_problems({**SPAWN, **{n: "x" for n in names}}, rec["env"])))
    mc = rt.get("model_chain") or {}
    check("SMB-35 (F-SBF-2): the model chain S55 holds $o (ours, the fast model z$2) and a model of every other "
          "provider whose variable the child env holds - child_env_problems names each such variable, a must-be-absent "
          "name or any GOOGLE_*, whatever its case; the spawn's env has none. The agent's models: x_ fast, wb text, the "
          "memory agent's model wb",
          cee is None and ce[0] == [] and len(ce[1]) == 4 and all(any(n in x for x in ce[1]) for n in names)
          and mc.get("name") == "S55" and mc.get("ours") == "$o" and mc.get("model") == "z$2"
          and rt.get("agent_models") == {"fast": "x_", "text": "wb", "memory_agent": "text"}
          and mc.get("others") == {"eo": "claude-haiku-4-5", "sm": "gemini-3.1-flash-lite-preview",
                                   "jm": "gemini-3.1-flash-lite-preview", "zo": "openai/gpt-oss-20b",
                                   "vo": "@cf/openai/gpt-oss-20b"}, str(cee or ce or mc))

    print("\n- the run label -")
    check("SMB-19: a used run label is refused by name - a record is never overwritten",
          refused(lambda: SB.run_facts(runs, run="f1", record_sha256=rsha), "f1"))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 sm bundle: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
