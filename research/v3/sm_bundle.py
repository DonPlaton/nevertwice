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
5. SM3 (the auditor, 2026-09-30 17:4x): every env name the spawn may declare, with the server's own default
   (Q-A8S-ENVN = a) - declared_problems refuses a name the server does not read, one the spawn never declares
   (NOT_DECLARED) and another provider's; resolve() says what a spawn env resolves to and names each vendor default
   it reaches. The workflow engine: on this win32 build with no WORKFLOW_ENGINE the default is direct, in process (no
   Rivet child; its port formula is recorded as outside that default). The product's own local bge-m3 (an E5 line;
   the stand's embedder is ours through the leg). F8: the memory agent's tools (names read from their definitions,
   the definitions' sha256 - the proxy's baseline, Q-SM-TOOLS = a), its limits and its system text's sha256.

The record: <runs>/_facts/a8-sm-bundle/<run>/bundle_facts.json.

    python research/v3/sm_bundle.py --run f2
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
    # SM3 (the auditor, 2026-09-30 17:4x): the Rivet branch - taken only where qd8 is false (workflow_direct_win32)
    "workflow_rivet_branch": rb'let z=await id8\(\);if\(z\)\{let e=globalThis;if\(!e\.(__SUPERMEMORY_RIVET_NAPI__)',
    # Q-A8S-ENVN = (a): every env name the spawn may declare, with the server's own defaults
    "embed_provider": rb'let z=lV\("(SUPERMEMORY_EMBEDDING_PROVIDER)",\$\)\?\.toLowerCase\(\);switch\(z\)\{case void 0:'
                      rb'case"(local)":return"local";case"openai":return"openai";case"(openai-compatible)":'
                      rb'case"openai_compatible":return"openai-compatible";',
    "embed_model": rb'function ef4\(\$,z\)\{if\(\$==="local"\)return lV\("(SUPERMEMORY_EMBEDDING_MODEL)",z\)\?\?'
                   rb'lV\("(SUPERMEMORY_LOCAL_EMBEDDING_MODEL)",z\)\?\?"([^"]+)";return lV\("SUPERMEMORY_EMBEDDING_MODEL",z\)'
                   rb'\?\?\(\$==="google"\?"gemini-embedding-001":"([^"]+)"\)\}',
    "embed_base_url": rb'function vf4\(\$,z\)\{let e=lV\("(SUPERMEMORY_EMBEDDING_BASE_URL)",z\);'
                      rb'if\(\$==="openai-compatible"\)\{let v=e\?\?lV\("(OPENAI_BASE_URL)",z\)',
    "embed_dimensions": rb'function Tf4\(\$,z,e\)\{let v=lV\("(SUPERMEMORY_EMBEDDING_DIMENSIONS)",e\);',
    "embed_api_key": rb'function Sp4\(\$\)\{let z=process\.env\.(SUPERMEMORY_EMBEDDING_API_KEY)\?\.trim\(\);if\(z\)return z;'
                     rb'if\(\$==="google"\)\{let v=process\.env\.GEMINI_API_KEY\?\.trim\(\);if\(v\)return v;throw Error\([^)]*\)\}'
                     rb'let e=process\.env\.(OPENAI_API_KEY)\?\.trim\(\);',
    "embed_local_bge_m3": rb'"(Xenova/bge-m3)":(\d+),"Xenova/all-MiniLM-L6-v2"',
    "embed_local_bge_m3_pooling": rb'Ul8=\{"Xenova/bge-m3":"([a-z]+)"\}',
    "llm_openai_entry": rb'\{id:"(openai)",name:"OpenAI \(or OpenAI-compatible: Ollama, vLLM, LM Studio, etc\.\)",'
                        rb'envVars:\["(OPENAI_API_KEY)"\]',
    "llm_other_providers": rb'\{id:"(anthropic)",name:"Anthropic",envVars:\["(ANTHROPIC_API_KEY)"\],promptable:!0\},'
                           rb'\{id:"(gemini)",name:"Gemini",envVars:\["(GEMINI_API_KEY)"\],promptable:!0\},'
                           rb'\{id:"(groq)",name:"Groq",envVars:\["(GROQ_API_KEY)"\],promptable:!0\},'
                           rb'\{id:"(workers-ai)",name:"Cloudflare Workers AI",envVars:\["(WORKERS_AI_API_KEY)",'
                           rb'"(CLOUDFLARE_ACCOUNT_ID)"\],promptable:!1\},\{id:"(vertex)",name:"Vertex AI",'
                           rb'envVars:\["(GOOGLE_VERTEX_PROJECT_ID)","(GOOGLE_VERTEX_LOCATION)"\]',
    "llm_openai_client": rb'\$o=process\.env\.(OPENAI_API_KEY)\?process\.env\.(OPENAI_BASE_URL)\?hl8\(\{name:"openai",'
                         rb'apiKey:process\.env\.OPENAI_API_KEY,baseURL:process\.env\.OPENAI_BASE_URL\}\)',
    "llm_models": rb'z\$2=process\.env\.(OPENAI_FAST_MODEL)\?\?process\.env\.(OPENAI_MODEL)\?\?"([^"]+)",'
                  rb'e\$2=process\.env\.(OPENAI_TEXT_MODEL)\?\?process\.env\.OPENAI_MODEL\?\?"([^"]+)",',
    # the win32 build's default engine: qd8's default argument is the platform the build baked in
    "workflow_direct_win32": rb'function qd8\(\$="([a-z0-9]+)",z=process\.env\.(WORKFLOW_ENGINE)\)\{if\(z\)return '
                             rb'z\.trim\(\)\.toLowerCase\(\)==="direct";return \$==="win32"\}',
    "workflow_direct_set": rb'if\(qd8\(\)\)return process\.env\.WORKFLOW_ENGINE="(direct)","direct";',
    "rivet_port_formula": rb'async function Qs5\(\)\{let \$=(1e4)\+iX\*(3)%(50000);for\(let z=\$;z<\$\+(\d+);z\+=3\)',
    # F8 (the auditor's Q-SM-TOOLS = a): a document is ingested by a tool-calling agent (AI SDK generateText) - its
    # call, its system text, its tools' definitions and their input schemas
    "memory_agent_call": rb'let c=await ky\(\{model:(wb)\(\),system:x,prompt:n,tools:(Z),stopWhen:hI8\((\d+)\),'
                         rb'maxRetries:(\d+),maxOutputTokens:(\d+),experimental_telemetry:\{isEnabled:!(vH0)\(\),'
                         rb'functionId:"([a-z-]+)"\}\}\)',
    "memory_agent_system": rb'x="(You are a memory extraction agent\.[^"]*)"',
    "memory_agent_tools": rb'Z=\{(searchMemories):TX\(\{description:.*?\}\)\},n=ng4\(',
    "memory_agent_schema_create": rb'(O72)=p\.object\(\{(memory):p\.string\(\)\.min\(1\)\..*?temporalContext:(x78)\}\)',
    "memory_agent_schema_relation": rb'(Cm0)=p\.enum\(\["(updates)","(extends)","(derives)"\]\),(x78)=p\.object\(\{'
                                    rb'documentDate:p\.string\(\)\.nullable\(\)\.optional\(\),eventDate:p\.array\('
                                    rb'p\.string\(\)\)\.nullable\(\)\.optional\(\)\}\)',
}

# Client route literals witnessed in b2, read as data; no vendor binary execution.
FACTS.update({
    'client_compat_def': (
        b'function\\ (hl8)\\(\\$\\)\\{let\\ z=xU\\(\\$\\.baseURL\\),e=\\$\\.name,v=\\{\\.\\.\\.\\$\\.apiKey\\&\\&\\{Authorizati'
        b'on:`Bearer\\ \\$\\{\\$\\.apiKey\\}`\\},\\.\\.\\.\\$\\.headers\\},u=\\(\\)=>VO\\(v,`(ai-sdk/openai-compatible)/\\$'
        b'\\{Ar4\\}`'
    ),
    'client_openai_def': (
        b'function\\ (Gl)\\(\\$=\\{\\}\\)\\{var\\ z,e;let\\ v=\\(z=xU\\(wx\\(\\{settingValue:\\$\\.baseURL,environmentVar'
        b'iableName:"(OPENAI_BASE_URL)"\\}\\)\\)\\)!=null\\?z:"(https://api\\.openai\\.com/v1)",[\\s\\S]{30}T=\\(\\)='
        b'>VO\\(\\{Authorization:`Bearer\\ \\$\\{oa0\\(\\{apiKey:\\$\\.apiKey,environmentVariableName:"OPENAI_API_K'
        b'EY",description:"OpenAI"\\}\\)\\}`,"OpenAI\\-Organization":\\$\\.organization,"OpenAI\\-Project":\\$\\.pr'
        b'oject,\\.\\.\\.\\$\\.headers\\},`(ai-sdk/openai)/\\$\\{Ur4\\}`'
    ),
    'llm_client_branch': (
        b'\\$o=process\\.env\\.OPENAI_API_KEY\\?process\\.env\\.(OPENAI_BASE_URL)\\?(hl8)\\(\\{name:"openai",apiKey'
        b':process\\.env\\.OPENAI_API_KEY,baseURL:process\\.env\\.OPENAI_BASE_URL\\}\\):(Gl)\\(\\{apiKey:process\\.'
        b'env\\.OPENAI_API_KEY\\}\\):null'
    ),
    'embed_base': (
        b'function\\ CB0\\(\\$\\)\\{let\\ z=process\\.env\\.SUPERMEMORY_EMBEDDING_BASE_URL\\?\\.trim\\(\\);if\\(z\\)retu'
        b'rn\\ z;if\\(\\$==="openai\\-compatible"\\)return\\ process\\.env\\.OPENAI_BASE_URL\\?\\.trim\\(\\)\\|\\|void\\ '
        b'0;return\\}'
    ),
    'embed_client_branch': (
        b'function\\ bU\\(\\)\\{if\\(Ld\\)return\\ Ld;let\\ \\$=r40\\(\\),\\{provider:z,modelId:e,dimensions:v\\}=\\$;if'
        b'\\(z==="(local)"\\)return\\ Ld=\\{model:kp4\\(\\{modelId:e,dimensions:v,pooling:Ul8\\[e\\]\\?\\?"mean"\\}\\)'
        b',dimensions:v,provider:z,modelId:e,isLocal:!0\\},Ld;let\\ u=Sp4\\(z\\),T;if\\(z==="(google)"\\)T=VU\\(\\'
        b'{apiKey:u\\}\\)\\.embeddingModel\\(e\\);else\\ if\\(z==="(openai-compatible)"\\)\\{let\\ O=CB0\\("openai\\-c'
        b'ompatible"\\);if\\(!O\\)throw\\ Error\\("\\[embeddings\\]\\ SUPERMEMORY_EMBEDDING_PROVIDER=openai\\-compa'
        b'tible\\ requires\\ SUPERMEMORY_EMBEDDING_BASE_URL\\ \\(or\\ OPENAI_BASE_URL\\),\\ e\\.g\\.\\ http://localh'
        b'ost:11434/v1\\ for\\ Ollama\\."\\);T=(hl8)\\(\\{name:"openai",apiKey:u,baseURL:O\\}\\)\\.embeddingModel\\('
        b'e\\)\\}else\\{let\\ O=CB0\\("openai"\\);T=(Gl)\\(\\{apiKey:u,\\.\\.\\.O\\?\\{baseURL:O\\}:\\{\\}\\}\\)\\.embeddingM'
        b'odel\\(e\\)'
    ),
    'agent_models': (
        b'function\\ W3\\(\\)\\{return\\ u\\$2\\}function\\ \\$\\$\\(\\$\\)\\{let\\ z=W3\\(\\),e=z\\.services\\.backgroundTas'
        b'ks\\(\\$\\.executionCtx\\),v=z\\.services\\.environment\\(\\$\\.env\\),u=z\\.services\\.extraction\\(\\)\\.pipe'
        b'\\(le\\(v\\)\\),T=z\\.services\\.workflowTrigger\\(\\)\\.pipe\\(le\\(\\$O\\(e,v\\)\\)\\),O=z\\.services\\.vectordb'
        b'\\(\\)\\.pipe\\(le\\(v\\)\\);return\\ \\$O\\(e,v,u,T,O\\)\\}var\\ u\\$2,(x_)\\=\\(\\)\\=>W3\\(\\)\\.models\\.(fast)\\(\\'
        b'),(wb)\\=\\(\\)\\=>W3\\(\\)\\.models\\.(text)\\(\\)'
    ),
    'model_chain': (
        b'(S55)=\\(\\)=>r90\\(\\[\\.\\.\\.\\$o\\?\\[(\\$o)\\((z\\$2)\\)\\]:\\[\\],\\.\\.\\.eo\\?\\[(eo)\\("(claude-haiku-4-5)"\\)\\'
        b']:\\[\\],\\.\\.\\.sm\\?\\[(sm)\\("(gemini-3\\.1-flash-lite-preview)"\\)\\]:\\[\\],\\.\\.\\.jm\\?\\[(jm)\\("(gemini-'
        b'3\\.1-flash-lite-preview)"\\)\\]:\\[\\],\\.\\.\\.zo\\?\\[(zo)\\("(openai/gpt-oss-20b)"\\)\\]:\\[\\],\\.\\.\\.vo\\?\\'
        b'[(vo)\\.chat\\("(@cf/openai/gpt-oss-20b)"\\)\\]:\\[\\]\\]\\)'
    ),
})

#: facts recorded for what they say, outside this build's default path
FACT_NOTES = {"rivet_port_formula": "outside the win32 default (workflow_direct_win32: the engine is direct, in process)",
              "workflow_rivet_branch": "taken only where qd8 is false - not on the win32 build with no WORKFLOW_ENGINE"}
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


#: Q-A8S-ENVN: names the spawn never declares - the fast and text models fall back to OPENAI_MODEL, the engine stays
#: the vendor's win32 default, the local model is not ours, the switches are TELEMETRY_OFF's (a declared one would
#: clash, launch.build_env), the embedder's key is OPENAI_API_KEY (the arm's proxy token)
NOT_DECLARED = frozenset({"OPENAI_FAST_MODEL", "OPENAI_TEXT_MODEL", "WORKFLOW_ENGINE", "SUPERMEMORY_LOCAL_EMBEDDING_MODEL",
                          "SUPERMEMORY_EMBEDDING_API_KEY", *SPAWN_ENV})
_ENV_NAME = re.compile(r"[A-Z0-9_]+")


def workflow(facts: dict) -> dict:
    """The build's default workflow engine: qd8 returns "direct" with no WORKFLOW_ENGINE exactly when its baked-in
    platform is win32; the Rivet ports apply only where the default is rivet."""
    platform, env = facts["workflow_direct_win32"]["values"]
    default = "direct" if platform == "win32" and facts["workflow_direct_set"]["values"] == ["direct"] else "rivet"
    base, step, mod, span = facts["rivet_port_formula"]["values"]
    return {"platform": platform, "env": env, "default": default,
            "rivet_ports": {"base": f"{base} + port*{step} % {mod}", "step": 3, "span": int(span),
                            "applies_by_default": default == "rivet"}}


def env_facts(facts: dict) -> dict:
    """{"reads": {name: {fact, default}}, "must_be_absent": [...]} - every env name the server reads that the spawn may
    touch, each with the fact that shows it and the server's own default (a name, when it falls back to another)."""
    ep, em, eb = facts["embed_provider"]["values"], facts["embed_model"]["values"], facts["embed_base_url"]["values"]
    lm, wf = facts["llm_models"]["values"], workflow(facts)
    reads = {ep[0]: ("embed_provider", ep[1]), em[0]: ("embed_model", em[2]), em[1]: ("embed_model", em[2]),
             eb[0]: ("embed_base_url", eb[1]), facts["embed_dimensions"]["values"][0]: ("embed_dimensions",
                                                                                       "the model's native size"),
             facts["embed_api_key"]["values"][0]: ("embed_api_key", facts["embed_api_key"]["values"][1]),
             facts["llm_openai_client"]["values"][0]: ("llm_openai_client", None),
             facts["llm_openai_client"]["values"][1]: ("llm_openai_client", None),
             lm[1]: ("llm_models", lm[2]), lm[0]: ("llm_models", lm[1]), lm[3]: ("llm_models", lm[1]),
             "PORT": ("port", "SUPERMEMORY_PORT"), "SUPERMEMORY_PORT": ("port", facts["port"]["values"][0]),
             "SUPERMEMORY_DATA_DIR": ("data_dir", facts["data_dir"]["values"][0]),
             wf["env"]: ("workflow_direct_win32", f"{wf['default']} ({wf['platform']})"),
             **{k: (f, None) for k, f in _SWITCH_FACTS.items()}}
    return {"reads": {k: {"fact": f, "default": d} for k, (f, d) in reads.items()},
            "must_be_absent": [v for v in facts["llm_other_providers"]["values"] if _ENV_NAME.fullmatch(v)]}


def declared_problems(declared: dict, env_rec: dict) -> list[str]:
    """A problem for each name the spawn declares that it never may, that another provider owns, or that the server
    does not read (a typo would leave the vendor's default on)."""
    problems = []
    for name in sorted(declared):
        if name in NOT_DECLARED:
            problems.append(f"declared {name}: the spawn never declares it (NOT_DECLARED)")
        elif name in env_rec["must_be_absent"]:
            problems.append(f"declared {name}: another provider's variable - it must be absent")
        elif name not in env_rec["reads"]:
            problems.append(f"declared {name}: the server reads no such variable (the bundle's facts)")
    return problems


"""Inserted into sm_bundle.py; static routing census for the already pinned binary."""
CLIENT_PATHS = {"openai-compatible": "/chat/completions", "openai": "/responses"}
# Verified on b2, not inferred from the synthetic fixture. Definitions count as sites.
ROUTE_COUNTS = {"openai-compatible": (3, 0), "openai": (9, 0)}
ROUTE_ARMS = {
    "llm_client_branch": ("OPENAI_BASE_URL", "llm_openai_client", "OPENAI_BASE_URL must be nonempty"),
    "embed_client_branch": ("SUPERMEMORY_EMBEDDING_PROVIDER", "embed_provider",
                            "SUPERMEMORY_EMBEDDING_PROVIDER must be openai-compatible"),
}
_CLIENT_REF = re.compile(rb"(?<![A-Za-z0-9_$.])(?:Gl|hl8)(?![A-Za-z0-9_$])")
# Exact resource spans and SHA256 from static b2 inspection (inclusive quote bytes).
# The binary itself is pinned before any census; arbitrary quote-delimited code is never excluded.
ROUTE_ASSETS = (
    (116658319, 24703706, "332d11a88a290e2c347b5da883374ffd633d2ab27ecc8ce4ac538ade35ae4567"),
    (271686999, 38372, "5288a14fd34647dc27ee77c36abb68535b4f0a0a6875015b224b33d5bcf3a39c"),
)


def _asset_span(sec: bytes, at: int, lo: int) -> tuple[int, int] | None:
    """Only an exact independently witnessed resource span/digest can exclude a reference."""
    for absolute, size, sha in ROUTE_ASSETS:
        a, z = absolute - lo, absolute - lo + size
        if 0 <= a <= at < z <= len(sec) and _sha256_bytes(sec[a:z]) == sha:
            return a, z
    return None


def route(sec: bytes, lo: int, facts: dict) -> dict:
    """Definitions, guarded branches, every bounded call/reference and external-branch findings.

    Scope is exactly .bun. Member names and longer identifiers are distinct; only independently witnessed
    encoded resource spans are excluded by exact offsets, lengths and digests.
    No quote-boundary inference excludes code. Unexplained calls or aliases refuse the census.
    """
    problems, findings, ignored = [], [], {}
    clients = {}
    for client, fact in (("openai-compatible", "client_compat_def"), ("openai", "client_openai_def")):
        f = facts[fact]
        name, ua = f["values"][0], f["values"][-1]
        clients[client] = {"name": name, "user_agent": ua, "path": CLIENT_PATHS[client],
                           "definition": f["offset"] + f["literal"].index(name + "(")}
        if client == "openai":
            clients[client].update(base_url_env=f["values"][1], default_base_url=f["values"][2])
        if name != ("hl8" if client == "openai-compatible" else "Gl") or ua != "ai-sdk/" + client:
            problems.append(f"{fact}: unexpected client definition or user agent")
    branches = {}
    for fact, (env_name, env_fact, rule) in ROUTE_ARMS.items():
        values = facts[fact]["values"]
        names = values[-2:]
        branches[fact] = {"env": env_name, "env_fact": env_fact,
                          "arms": [{"client": "openai-compatible", "name": names[0], "excluded_by": None},
                                   {"client": "openai", "name": names[1], "excluded_by": rule}]}
        expected = ["OPENAI_BASE_URL", "hl8", "Gl"] if fact == "llm_client_branch" else ["local", "google", "openai-compatible", "hl8", "Gl"]
        if values != expected:
            problems.append(f"{fact}: unexpected condition or client arms {values}")
        if env_name not in facts[env_fact]["values"]:
            problems.append(f"{env_fact}: does not read {env_name} attributed to {fact}")
    calls = {c: 0 for c in clients}
    bare = {c: 0 for c in clients}
    bare_offsets = {c: [] for c in clients}
    by_name = {v["name"]: c for c, v in clients.items()}
    for m in _CLIENT_REF.finditer(sec):
        name = m.group().decode("ascii")
        client = by_name.get(name)
        if client is None:
            problems.append(f"unknown client name {name}")
            continue
        after = sec[m.end():m.end()+1]
        if after != b"(":
            asset = _asset_span(sec, m.start(), lo)
            if asset is not None:
                if asset not in ignored:
                    ignored[asset] = {"offset": lo + asset[0], "bytes": asset[1] - asset[0],
                                      "sha256": _sha256_bytes(sec[asset[0]:asset[1]]), "references": []}
                ignored[asset]["references"].append(lo + m.start())
                continue
            bare[client] += 1
            bare_offsets[client].append(lo + m.start())
            continue
        calls[client] += 1
        at = lo + m.start()
        if at == clients[client]["definition"]:
            continue
        if any(facts[f]["offset"] <= at < facts[f]["offset"] + len(facts[f]["literal"].encode("utf-8")) for f in branches):
            continue
        findings.append({"client": client, "name": name, "offset": at,
                         "finding": "call site outside the branch",
                         "window_sha256": _sha256_bytes(sec[max(0, m.start()-WINDOW_BYTES):min(len(sec), m.start()+WINDOW_BYTES)])})
    for client, (want_calls, want_bare) in ROUTE_COUNTS.items():
        if (calls[client], bare[client]) != (want_calls, want_bare):
            problems.append(f"{clients[client]['name']} has {calls[client]} call sites and {bare[client]} bare references; expected {want_calls} and {want_bare}")
    chain = facts["model_chain"]["values"]
    models = facts["agent_models"]["values"]
    agent = facts["memory_agent_call"]["values"][0]
    if models != ["x_", "fast", "wb", "text"] or agent != models[2]:
        problems.append("agent_models: memory agent does not take the witnessed text model")
    return {"clients": clients, "branches": branches, "calls": calls, "bare": bare, "bare_offsets": bare_offsets,
            "scope": ".bun bounded identifiers; independently witnessed encoded resource spans excluded",
            "ignored_assets": sorted(ignored.values(), key=lambda x: x["offset"]),
            "findings": findings, "problems": problems,
            "model_chain": {"name": chain[0], "ours": chain[1], "model": chain[2],
                            "others": dict(zip(chain[3::2], chain[4::2])),
                            "note": "S55 recorded; linkage from models.text to S55 versus Y55 is not statically established"},
            "agent_models": {"fast": models[0], "text": models[2], "memory_agent": "text"}}


def child_env_problems(env: dict, env_rec: dict) -> list[str]:
    """No fallback provider is enabled, regardless of environment-name casing."""
    forbidden = {name.upper() for name in env_rec["must_be_absent"]}
    forbidden.update({"ANTHROPIC_API_KEY", "GROQ_API_KEY", "WORKERS_AI_API_KEY", "CLOUDFLARE_ACCOUNT_ID"})
    return [f"child env {name}: another provider's variable must be absent" for name in sorted(env)
            if name.upper() in forbidden or name.upper().startswith("GOOGLE_")]


def resolve(env: dict, env_rec: dict) -> dict:
    """What a spawn env resolves to by the bundle's own rules, and each vendor default it reaches (never ours)."""
    reads, defaults = env_rec["reads"], []
    provider = (env.get("SUPERMEMORY_EMBEDDING_PROVIDER") or reads["SUPERMEMORY_EMBEDDING_PROVIDER"]["default"]).lower()
    if provider == "local":
        model = (env.get("SUPERMEMORY_EMBEDDING_MODEL") or env.get("SUPERMEMORY_LOCAL_EMBEDDING_MODEL")
                 or reads["SUPERMEMORY_EMBEDDING_MODEL"]["default"])
        defaults.append(f"embedding provider local ({model})")
        embedding = {"provider": "local", "model": model, "base_url": None,
                     "dimensions": env.get("SUPERMEMORY_EMBEDDING_DIMENSIONS") or "native"}
    else:
        model = env.get("SUPERMEMORY_EMBEDDING_MODEL")
        if not model:
            defaults.append("embedding model (the vendor's remote default)")
        dims = env.get("SUPERMEMORY_EMBEDDING_DIMENSIONS")
        if not dims:
            defaults.append("embedding dimensions (the model's native size)")
        embedding = {"provider": provider, "model": model, "base_url": env.get("SUPERMEMORY_EMBEDDING_BASE_URL")
                     or env.get(reads["SUPERMEMORY_EMBEDDING_BASE_URL"]["default"]), "dimensions": dims}
    llm = {}
    for kind, name in (("fast", "OPENAI_FAST_MODEL"), ("text", "OPENAI_TEXT_MODEL")):
        llm[kind] = env.get(name) or env.get(reads[name]["default"]) or reads["OPENAI_MODEL"]["default"]
        if not (env.get(name) or env.get(reads[name]["default"])):
            defaults.append(f"llm {kind} model {llm[kind]}")
    llm["base_url"] = env.get("OPENAI_BASE_URL")
    if not env.get("OPENAI_API_KEY"):
        llm.update(client=None, path=None)
        defaults.append("$o is null: no OPENAI_API_KEY")
    elif env.get("OPENAI_BASE_URL"):
        llm.update(client="openai-compatible", path=CLIENT_PATHS["openai-compatible"])
    else:
        llm.update(client="openai", path=CLIENT_PATHS["openai"])
        defaults.append("Gl at the vendor base URL: /responses (no OPENAI_BASE_URL)")
    if provider not in {"local", "openai-compatible"}:
        defaults.append(f"embedding provider {provider} (vendor route)")
    return {"embedding": embedding, "llm": llm, "vendor_defaults": defaults}


_TOOL_NAME = re.compile(r"(\w+):TX\(\{description:")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tools(facts: dict) -> dict:
    """F8: the memory agent's tools as the bundle defines them - the names read from the definitions, never from a
    list, the definitions' sha256 (the baseline the proxy pins: the auditor's Q-SM-TOOLS = a), each input schema's
    sha256, the call's limits, its telemetry gate and function id, the sha256 of its system text."""
    call = facts["memory_agent_call"]["values"]
    return {"names": _TOOL_NAME.findall(facts["memory_agent_tools"]["literal"]),
            "sha256": _sha(facts["memory_agent_tools"]["literal"]), "max_steps": int(call[2]),
            "max_output_tokens": int(call[4]), "max_retries": int(call[3]), "telemetry_gate": call[5],
            "function_id": call[6], "schemas": {"CreateMemory": _sha(facts["memory_agent_schema_create"]["literal"]),
                                                "relation": _sha(facts["memory_agent_schema_relation"]["literal"])},
            "system_sha256": _sha(facts["memory_agent_system"]["values"][0])}


def run_facts(runs_root: Path, *, run: str, record_sha256: str = BIN_RECORD_SHA256) -> dict:
    """The whole read (see the module docstring); a refusal writes nothing."""
    base = runs_root / "_facts" / READER / run
    if base.exists():
        raise FactsRefused(f"the run label {run} was used before - a record is never overwritten")
    data, pin = pinned_binary(runs_root, record_sha256=record_sha256)
    lo, hi = bun_section(data)
    facts = read_facts(data, lo, hi)
    r8, sw = r8_verdict(facts), switches(facts)
    rt = route(data[lo:hi], lo, facts)
    record = {"reader": READER, "run": run, "network": False, "executed": False, "window_bytes": WINDOW_BYTES,
              "pin": pin, "bundle": {"section": ".bun", "offset": lo, "bytes": hi - lo}, "facts": facts, "r8": r8,
              "switches": sw, "auth": auth_facts(facts), "env": env_facts(facts), "workflow": workflow(facts),
              "embedding_local": {"model": facts["embed_local_bge_m3"]["values"][0],
                                  "dimensions": int(facts["embed_local_bge_m3"]["values"][1]),
                                  "pooling": facts["embed_local_bge_m3_pooling"]["values"][0]},
              "tools": tools(facts), "route": rt, "notes": dict(FACT_NOTES),
              "problems": r8["problems"] + sw["problems"] + rt["problems"],
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
