#!/usr/bin/env python3
"""(б) b-a, K45: the pacer sees httpx2 traffic, says "zero" when there was none, and a model arm with zero is refused.

The openai SDK 3.x sends through `httpx2`, a separate package whose `Client`/`AsyncClient` are not
httpx's. Graphiti reaches Ollama through that SDK, so the zep arm's calls never passed the pacer's
httpx hooks: its rows carry no transport record and the server log was the only witness. And
`attach()` wrote nothing on zero calls, so an absent record could not tell "no traffic" from
"traffic this module never saw".

Where the real `httpx2` is not installed (this interpreter), a second, independent copy of `httpx`
is loaded under the name `httpx2` - distinct classes, the same API, which is what the fork is to
the pacer. `research/_pacer_selftest.py` runs the same probe against the real package inside a
competitor's own environment.

    python tests/research/_test_pacer_k45.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "research"))

import _env_guard  # noqa: F401,E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def load_httpx2():
    """The real httpx2 if installed, else httpx loaded a second time under that name."""
    try:
        import httpx2  # noqa: PLC0415
        return httpx2, "real"
    except ImportError:
        pass
    import httpx  # noqa: PLC0415
    pkg = Path(httpx.__file__).parent
    spec = importlib.util.spec_from_file_location("httpx2", pkg / "__init__.py",
                                                  submodule_search_locations=[str(pkg)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["httpx2"] = mod
    spec.loader.exec_module(mod)
    return mod, "a second copy of httpx"


httpx2, kind = load_httpx2()
import httpx  # noqa: E402
import _ollama_pacer as pacer  # noqa: E402

print(f"\n- httpx2 ({kind}) is a separate library to the pacer -")
check("its Client is not httpx's (so the httpx hook alone cannot see it)",
      httpx2.Client is not httpx.Client and not issubclass(httpx2.Client, httpx.Client))

URL = "http://127.0.0.1:11434/api/embed"     # never dialled: MockTransport answers in-process


def handler(request):
    return httpx2.Response(200, json={"embeddings": [[0.1, 0.2]]})


def sync_call() -> int:
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as c:
        return c.post(URL, json={"model": "m", "input": "x"}).status_code


async def async_call() -> int:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as c:
        r = await c.post(URL, json={"model": "m", "input": "x"})
        return r.status_code


print("\n- installed, the pacer paces and counts httpx2 calls to Ollama -")
pacer.uninstall()
pacer._reset_for_tests()
pacer._now, pacer._sleep = (lambda: 0.0), (lambda s: None)
real_async_sleep = pacer._async_sleep


async def _no_sleep(s):
    return None


pacer._async_sleep = _no_sleep
try:
    pacer.install()
    check("httpx2.Client.send is the pacer's", httpx2.Client.send is pacer._paced_httpx2_send)
    check("httpx2.AsyncClient.send is the pacer's", httpx2.AsyncClient.send is pacer._paced_httpx2_async_send)
    before = pacer.snapshot()
    ok_sync = sync_call()
    ok_async = asyncio.run(async_call())
    out: dict = {}
    pacer.attach(out, since=before)
    tr = out.get("ollama_transport") or {}
    check("one sync and one async httpx2 call answered through the hook", ok_sync == 200 and ok_async == 200)
    check("both are counted as paced Ollama calls (calls == 2)", tr.get("calls") == 2, str(tr))
    quiet_before = pacer.snapshot()
    quiet: dict = {}
    pacer.attach(quiet, since=quiet_before)
    check("an installed pacer with no traffic in the span records calls: 0, not nothing",
          (quiet.get("ollama_transport") or {}).get("calls") == 0, str(quiet))
finally:
    pacer.uninstall()
    pacer._async_sleep = real_async_sleep
check("uninstall puts httpx2's own send back",
      httpx2.Client.send is not pacer._paced_httpx2_send
      and httpx2.AsyncClient.send is not pacer._paced_httpx2_async_send)

print("\n- not installed: nothing is counted, and attach() writes nothing (the untracked case) -")
pacer._reset_for_tests()
before = pacer.snapshot()
sync_call()
out = {}
pacer.attach(out, since=before)
check("an httpx2 call with the pacer uninstalled leaves no record at all", "ollama_transport" not in out, str(out))

print("\n- a model arm that recorded zero paced calls is refused; a naive arm is not asked -")
arm = {"stale_rate": 0.1, "ollama_transport": {"calls": 0}}
pacer.require_traffic(arm, "zep")
check("an arm with calls: 0 is marked invalid, naming the arm and K45",
      arm.get("valid") is False and "zep" in arm.get("invalid_reason", "") and "K45" in arm["invalid_reason"], str(arm))
arm = {"stale_rate": 0.1, "valid": False, "invalid_reason": "2 embed call(s) failed", "ollama_transport": {"calls": 0}}
pacer.require_traffic(arm, "nevertwice")
check("an existing invalid reason is kept and extended, not replaced",
      arm["invalid_reason"].startswith("2 embed call(s) failed; ") and "0 paced" in arm["invalid_reason"], str(arm))
arm = {"stale_rate": 0.1, "ollama_transport": {"calls": 12}}
pacer.require_traffic(arm, "mem0")
check("an arm with paced calls is left alone", "valid" not in arm, str(arm))
arm = {"blocked": "import failed"}
pacer.require_traffic(arm, "zep")
check("a blocked arm has no numbers and is left alone", "valid" not in arm, str(arm))

print("\n- and the stands apply it to exactly their model arms -")
import supersession_bench as sb  # noqa: E402
import asof_bench as ab  # noqa: E402
check("supersession: nevertwice, mem0 and zep must show traffic; naive is not asked",
      sb.OLLAMA_ARMS == {"nevertwice", "mem0", "zep"} and "naive" in sb.ARMS, str(sb.OLLAMA_ARMS))
check("as-of: nevertwice and zep; naive is not asked",
      ab.OLLAMA_ARMS == {"nevertwice", "zep"} and "naive" in ab.ARMS, str(ab.OLLAMA_ARMS))
_saved_arms = dict(sb.ARMS)
_ROW = {"stale_rate": 0.0, "stale_ci": [0, 0], "stale_at_rank_1": 0.0, "current_rate": 1.0,
        "current_ci": [1, 1], "control_miss_rate": 0.0, "mean_chars_returned": 10, "seconds": 0.1}
sb.ARMS["zep"] = lambda cases, k: dict(_ROW)          # "measured", but no call the pacer saw
sb.ARMS["naive"] = lambda cases, k: dict(_ROW)
try:
    import types  # noqa: PLC0415
    _args = types.SimpleNamespace(k=5, sleep=False, third_session=False)
    _data = {"name": "t", "sha256": "0", "path": "x"}
    import contextlib, io  # noqa: E401,PLC0415
    with contextlib.redirect_stdout(io.StringIO()):
        _out = sb._one_run(_data, [], _args, ["zep", "naive"])
    _arms = (_out or {}).get("arms", {})
finally:
    sb.ARMS.clear()
    sb.ARMS.update(_saved_arms)
    pacer.uninstall()
check("a zep arm whose calls the pacer never saw is refused by the stand itself",
      (_arms.get("zep") or {}).get("valid") is False, str(_arms.get("zep")))
check("while the naive arm, which calls nothing, stays valid", "valid" not in (_arms.get("naive") or {"valid": "?"}),
      str(_arms.get("naive")))

print("\n- head_to_head: a competitor arm with no paced call is refused; our cached arm is not asked -")
import head_to_head as hh  # noqa: E402
check("every competitor adapter is a model arm, our re-scoring arm is not",
      hh.MODEL_ARMS == set(hh.ADAPTERS) - {"nevertwice"} and "mem0_infer" in hh.MODEL_ARMS, str(sorted(hh.MODEL_ARMS)))
_saved_mem0 = hh.ADAPTERS["mem0"]
hh.ADAPTERS["mem0"] = lambda data, pool: {"recall@5": 0.5, "n": 1, "mode": "fake, no call"}
try:
    pacer.install()
    _row = hh.run_and_score_arm("mem0", [], {})
finally:
    hh.ADAPTERS["mem0"] = _saved_mem0
    pacer.uninstall()
check("a mem0 row whose calls the pacer never saw is invalid in the stand itself",
      _row.get("valid") is False and "0 paced Ollama calls" in (_row.get("invalid_reason") or ""), str(_row)[:300])

print("\n- the pacer reads every endpoint name a stand or a competitor can be pointed with -")
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402
check("every name sandbox_guard closes is a name the pacer takes an Ollama host from "
      "(OLLAMA_OPENAI_BASE for graphiti, OLLAMA_API_BASE for litellm included)",
      set(sandbox_guard.CLOSED_OLLAMA) <= set(pacer._HOST_ENV_VARS),
      str(sorted(set(sandbox_guard.CLOSED_OLLAMA) - set(pacer._HOST_ENV_VARS))))

print(f"\npacer k45: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
