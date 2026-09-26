#!/usr/bin/env python3
"""PREREG-V3 plan step A2.5: the DeepSeek capture fixtures (tests/fixtures/v3_captures) are what their manifest says.

* Every fixture's sha256 is the manifest's (one flipped byte fails by name), and the manifest's run was clean: no
  problem, key scan 0, no key-shaped file, a complete check with 0 native and 0 file-system hits, no catcher host,
  and the proxy reached api.deepseek.com through the declared hop with its issuer recorded.
* The fixtures are the seven fixed calls of research/v3/capture_deepseek.py, request bodies and hashes included.
* The header rule held: in every head, each header outside the seven kept names has a value made only of ``x``,
  and the manifest lists exactly those names; nothing provider-key-shaped is in any fixture or the manifest.
* The proxy's ResponseFramer follows each fixture fed in random-sized pieces and ends exactly at its last byte.
* What the recording mode's TeeParser reads from each, as recorded on 2026-09-26 (run r1): every response names
  deepseek-flash and parses; on /v1 ``"thinking": {"type": "disabled"}`` is honoured (no reasoning), while the
  thinking-on call carries reasoning tokens; both tool-call captures call get_weather and stop on tool_calls; the
  json_object capture is valid JSON; the second tool-call capture shows a prompt-cache hit (the same prompt ran
  just before). On /anthropic, whose requests name no thinking at all, BOTH responses carry thinking blocks: the
  provider's documented default (thinking on) holds on that endpoint - a fact for §2.2.1's thinking-default slot.

    python tests/_test_v3_captures.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CAP = _load("v3_capture_fx", ROOT / "research" / "v3" / "capture_deepseek.py")
P = _load("v3_llm_proxy_fx", ROOT / "research" / "_llm_proxy.py")
FX = HERE / "fixtures" / "v3_captures"

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


print("\n- the manifest and the run it records -")
man_path = FX / "MANIFEST.json"
check("the manifest exists", man_path.is_file())
man = json.loads(man_path.read_bytes()) if man_path.is_file() else {"captures": []}
check("the run was clean: no problem, key scan 0, no key-shaped file, no catcher host",
      man.get("problems") == [] and man.get("scan_key_hits") == 0 and man.get("provider_key_shaped_files") == 0
      and man.get("catcher_hosts") == 0, str({k: man.get(k) for k in ("problems", "scan_key_hits", "catcher_hosts")}))
check("its check was complete with 0 native and 0 file-system hits",
      man.get("check", {}).get("complete") is True and man["check"].get("native_hits") == 0
      and man["check"].get("fs_hits") == 0, str(man.get("check")))
via = man.get("proxy_upstream_via") or {}
check("the proxy went through the declared hop and recorded the upstream's issuer",
      via.get("host") == "127.0.0.1" and isinstance(via.get("port"), int) and bool(via.get("issuer_o"))
      and man.get("test_upstream") is False, str(via))

print("\n- the fixtures are the seven fixed calls, byte for byte -")
entries = {e["name"]: e for e in man["captures"]}
check("one fixture per fixed call, no other", sorted(entries) == sorted(c["name"] for c in CAP.CALLS), str(sorted(entries)))
for call in CAP.CALLS:
    e = entries.get(call["name"], {})
    f = FX / e.get("file", f"{call['name']}.bin")
    data = f.read_bytes() if f.is_file() else b""
    check(f"{call['name']}: sha256 and size are the manifest's", data and hashlib.sha256(data).hexdigest() == e.get("sha256")
          and len(data) == e.get("bytes"), f"{len(data)} B")
    check(f"{call['name']}: the recorded request is the fixed call, and its hash matches",
          e.get("request_body") == call["body"] and e.get("request_sha256") == hashlib.sha256(CAP.body_bytes(call)).hexdigest()
          and e.get("endpoint") == call["path"] and e.get("stream") == call["stream"])

print("\n- the header rule and no key -")
for call in CAP.CALLS:
    data = (FX / f"{call['name']}.bin").read_bytes() if (FX / f"{call['name']}.bin").is_file() else b""
    names, pos, bad = [], 0, []
    while data.startswith(b"HTTP/", pos) and b"\r\n\r\n" in data[pos:]:
        end = data.index(b"\r\n\r\n", pos)
        lines = data[pos:end].split(b"\r\n")
        for line in lines[1:]:
            k, sep, v = line.partition(b":")
            key = k.strip().lower().decode("latin-1")
            if sep and key not in CAP.KEEP_HEADERS:
                if set(v.strip(b" \t")) - {ord("x")}:
                    bad.append(key)
                if key not in names:
                    names.append(key)
        status = lines[0].split(b" ", 2)
        pos = end + 4
        if not (len(status) > 1 and status[1].isdigit() and 100 <= int(status[1]) < 200):
            break
    check(f"{call['name']}: every header outside the kept seven is only x, and the manifest names them",
          not bad and names == entries.get(call["name"], {}).get("redacted_headers"), f"bad={bad} names={names}")
    check(f"{call['name']}: nothing provider-key-shaped", not CAP.PROVIDER_KEY.search(data))
check("nothing provider-key-shaped in the manifest", not CAP.PROVIDER_KEY.search(man_path.read_bytes() if man_path.is_file() else b""))

print("\n- the proxy's framer follows each fixture to its last byte -")
rng = random.Random(20260926)
for call in CAP.CALLS:
    data = (FX / f"{call['name']}.bin").read_bytes() if (FX / f"{call['name']}.bin").is_file() else b""
    fr = P.ResponseFramer("POST")
    used, pos = 0, 0
    while pos < len(data) and not fr.done:
        n = rng.randint(1, 700)
        used += fr.feed(data[pos:pos + n])
        pos += n
    check(f"{call['name']}: the framer ends exactly at the last byte ({len(data)} B)",
          data != b"" and fr.done and used == len(data), f"done={fr.done} used={used}")

print("\n- what the recording mode reads from them (run r1, 2026-09-26) -")


def facts(call: dict) -> dict:
    data = (FX / f"{call['name']}.bin").read_bytes() if (FX / f"{call['name']}.bin").is_file() else b""
    body = bytearray()
    fr = P.ResponseFramer("POST", body_sink=body.extend)
    fr.feed(data)
    tp = P.TeeParser()
    tp.feed(bytes(body))
    rf = (call["body"].get("response_format") or {}).get("type")
    return tp.result(P._hget(fr.headers, "content-type") or "", call["path"], rf)


F = {c["name"]: facts(c) for c in CAP.CALLS}
check("every response names deepseek-flash, parses, and is not empty",
      all(f["response_model"] == "deepseek-flash" and f["parse_ok"] and f["content_empty"] is False for f in F.values()),
      str({k: (f["response_model"], f["parse_ok"], f["content_empty"]) for k, f in F.items()}))
off = ("v1_json_object", "v1_sse_usage", "v1_tool_call", "v1_tool_call_sse")
check("/v1 with thinking disabled: no reasoning in any of the four",
      all(not F[n]["reasoning_seen"] and not F[n]["usage"]["reasoning"] for n in off))
check("/v1 with thinking explicitly on: reasoning seen and counted",
      F["v1_sse_thinking_on"]["reasoning_seen"] and (F["v1_sse_thinking_on"]["usage"]["reasoning"] or 0) > 0,
      str(F["v1_sse_thinking_on"]["usage"]))
check("both tool-call captures call get_weather and stop on tool_calls",
      all(F[n]["tools_called"] == ["get_weather"] and F[n]["finish_reason"] == "tool_calls"
          for n in ("v1_tool_call", "v1_tool_call_sse")))
check("the json_object capture is valid JSON", F["v1_json_object"]["json_ok"] is True)
check("the /v1 SSE captures carry usage (include_usage), with DeepSeek's cache split",
      all(isinstance(F[n]["usage"]["prompt"], int) and F[n]["usage"]["cache_miss"] is not None
          for n in ("v1_sse_usage", "v1_sse_thinking_on", "v1_tool_call_sse")))
check("the second tool-call capture hit the prompt cache (the same prompt ran just before it)",
      (F["v1_tool_call_sse"]["usage"]["cache_hit"] or 0) > 0 and F["v1_tool_call"]["usage"]["cache_hit"] == 0,
      str((F["v1_tool_call"]["usage"], F["v1_tool_call_sse"]["usage"])))
check("/anthropic, no thinking named in the request: both responses carry thinking blocks (the default is on)",
      F["anthropic_messages"]["thinking_block"] and F["anthropic_messages_sse"]["thinking_block"]
      and all("thinking" not in c["body"] for c in CAP.CALLS if c["path"].startswith("/anthropic/")))

print(f"\nv3 captures: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
