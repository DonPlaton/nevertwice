#!/usr/bin/env python3
"""The golden store's embedder must be the stub, not the machine's live one.

`tests/_golden_store.py` replaces the model and the embedder with deterministic implementations
rather than switching them off, so that the proof runs through the branches it certifies. Its own
comment names the door it patches: "the one embedder door: `embed_text` still runs". That was
true for every *cloud* provider, which reach the network through `_embed_http` - and false for
the configured one. With `NEVERTWICE_EMBED_PROVIDER=ollama`, the default here, `embed_text` built
its own `urllib.request` call and went straight to `http://127.0.0.1:11434/api/embed`.

Measured 2026-09-19 under the golden harness: `embed_text("a deterministic probe")` returned a
**1024-dimensional bge-m3 vector from the live service**, while the stub returns 48 dimensions.
So the project's headline zero-behaviour-change proof was ranked by a running model, and its
recorded snapshot is only reproducible while that model is up and answers within the timeout. It
is not a hypothetical: a full `tests/test_self_checks.py` run the same day failed on
`Embed failed: TimeoutError` - the service was busy, the note went unembedded, and the golden
snapshot did not match.

The fix is one EMBEDDING door: the ollama branch posts through `_embed_http` like every other
provider, so patching that one function covers every embedder. It is not a claim about the
engine - `ollama_alive` and `_json_api_call` still open sockets of their own, behind their own
stubs. What covers the engine as a whole is the tripwire: `install()` refuses every `urlopen`
for the length of the golden run, so a door nobody has thought of yet fails loudly instead of
quietly borrowing the machine. This suite holds both shut.

    python tests/_test_golden_store_reaches_no_service.py
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

import _golden_store as golden  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

import _engine_source  # noqa: E402  the engine's text, reassembled from its parts in one place

ENGINE_SRC = _engine_source.SRC

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


make_sandbox(m, "golden_reach_")
golden.install(m, {})

PROBE = "a deterministic probe"


def test_the_stub_is_what_answers() -> None:
    print("\n- under the golden harness, the stub is the embedder -")
    want = golden._vec(PROBE)
    m._EMBED_TEXT_MEMO["key"] = None          # the one-slot memo must not mask the door
    got = m.embed_text(PROBE)
    check("embed_text returns a vector", bool(got))
    check(f"and it is the stub's {len(want)} dimensions, not a live model's",
          bool(got) and len(got) == len(want),
          f"got {len(got) if got else None} dimensions - a live embedder answered")
    check("value for value", got == want,
          "the vector is not the deterministic one this harness defines")


def test_no_socket_is_opened() -> None:
    """The decisive form: break every outbound HTTP call and the answer must not change."""
    print("\n- with the network broken, the answer is the same -")
    import urllib.request

    real = urllib.request.urlopen

    def refuse(*a, **k):                       # noqa: ANN001,ANN002 - a tripwire, not a stub
        raise AssertionError("the golden harness opened a socket")

    urllib.request.urlopen = refuse
    try:
        m._EMBED_TEXT_MEMO["key"] = None
        got = m.embed_text(PROBE)
        ok, detail = got == golden._vec(PROBE), "embed_text answered differently with no network"
    except AssertionError as exc:
        ok, detail = False, str(exc)
    finally:
        urllib.request.urlopen = real
    check("embed_text never reaches the network under the golden harness", ok, detail)

    # And the harness installs that tripwire itself, so the whole golden run - not just this
    # call - fails loudly instead of quietly borrowing whatever is listening on the machine.
    import urllib.request as _ur
    try:
        _ur.urlopen("http://127.0.0.1:11434/api/embed")
        fired, msg = False, "urlopen returned instead of refusing"
    except AssertionError as exc:
        fired, msg = True, str(exc)
    except Exception as exc:                        # noqa: BLE001 - any other error is not it
        fired, msg = False, f"{type(exc).__name__}: {exc}"
    check("install() leaves a tripwire on urlopen for the whole run", fired, msg)
    check("and the tripwire names the url it caught", fired and "11434" in msg, msg[:120])


def _function_source(name: str) -> str:
    """The function as it is written in `nevertwice/_engine.py`.

    Not `inspect.getsource(getattr(m, name))`: `memory_hook` is a loader that execs the engine
    into its own globals, and this harness has already replaced `_embed_http` and `_embed_cloud`
    with lambdas - asking the live attribute for its source returns the stub, which is how the
    first draft of this suite passed two checks it had no right to pass.
    """
    tree = ast.parse(ENGINE_SRC)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(ENGINE_SRC, node) or ""
    return ""


def test_there_is_one_http_door() -> None:
    """Structural, so the branch cannot come back without this suite noticing."""
    print("\n- every EMBEDDING provider posts through the same function -")
    src = _function_source("embed_text")
    check("embed_text was found in the engine source", bool(src))
    check("embed_text builds no request of its own", "urlopen" not in src,
          "embed_text calls urlopen directly again; a harness that patches _embed_http "
          "will not cover it")
    check("and it does not construct a urllib Request", "urllib.request.Request" not in src)
    check("the ollama branch goes through the shared door", "_embed_http(" in src,
          "no call to _embed_http; which door does ollama use now?")

    # The scope of the claim, measured rather than asserted: the engine opens sockets in three
    # places, one of them the shared embedding door. The other two are their own doors with their
    # own stubs, so "one door" must never be read as a statement about the engine.
    owners = sorted({fn.name for fn in ast.walk(ast.parse(ENGINE_SRC))
                     if isinstance(fn, ast.FunctionDef)
                     and "urlopen" in (ast.get_source_segment(ENGINE_SRC, fn) or "")})
    check("the engine's socket sites are exactly the three known doors",
          owners == ["_embed_http", "_json_api_call", "ollama_alive"], ", ".join(owners))
    # Behavioural, and it only works because the tripwire is already armed: if `ollama_alive`
    # were still the engine's own, it would reach the tags endpoint and the tripwire would raise.
    try:
        alive, why = m.ollama_alive(1) is True, ""
    except AssertionError as exc:
        alive, why = False, str(exc)
    check("and the health-check door answers without a socket under the harness", alive, why)

    door = _function_source("_embed_http")
    check("the shared door ascii-escapes the error text", "!a}" in door,
          "an OS-localized error (a Russian WinError) will be written in the log reader's "
          "codepage again - the incident the ollama branch carried a comment about")
    check("and it still reports the provider", "EMBED_PROVIDER" in door)


def test_the_harness_still_runs_the_real_path() -> None:
    """The stub must not be a bypass: the prefix, the memo and the cache key still run."""
    print("\n- the door is stubbed, the code around it is not -")
    m._EMBED_TEXT_MEMO["key"] = None
    first = m.embed_text(PROBE, kind="query")
    second = m.embed_text(PROBE, kind="query")
    check("the one-slot memo still answers the second call", first == second)
    check("the nomic task prefix is still applied by the engine",
          "_embed_prefix" in _function_source("embed_text"))
    check("a cloud provider is still gated on its key",
          "_embed_key" in _function_source("_embed_cloud"))


def _cos(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def test_the_stub_embedder_can_be_similar_to_something() -> None:
    """A deterministic embedder that hashes the WHOLE text is orthogonal to everything.

    `_vec` used to be `sha256(text)` expanded to 48 dimensions, so two texts differing by one
    character landed in unrelated directions. Every semantic score in the golden run was then
    noise around zero: the similarity floor rejected every candidate, the semantic tier
    contributed nothing, and a refactor that broke ranking still certified clean. On this
    machine that was invisible, because the live model was answering instead of the stub.

    The stub has to be deterministic AND able to be similar - feature hashing over tokens is
    both.
    """
    print("\n- the stub can be similar to something -")
    note = "the service runs on postgres 16 in production"
    near = "which database does the service use in production"
    far = "calling eval on request data took the worker down"
    check("the stub is a pure function of the text", golden._vec(note) == golden._vec(note))
    check("and unit length", abs(_cos(golden._vec(note), golden._vec(note)) - 1.0) < 1e-9)
    sim_near = _cos(golden._vec(note), golden._vec(near))
    sim_far = _cos(golden._vec(note), golden._vec(far))
    check(f"shared words score above unrelated ones ({sim_near:.3f} > {sim_far:.3f})",
          sim_near > sim_far,
          "the stub hashes the whole text, so every pair is orthogonal noise and the "
          "similarity floor rejects everything")
    check("and the margin is not a rounding artefact", sim_near - sim_far > 0.05,
          f"margin {sim_near - sim_far:.4f}")
    check("an unrelated pair is not similar", sim_far < 0.5, f"{sim_far:.3f}")


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_the_stub_is_what_answers,
               test_no_socket_is_opened,
               test_there_is_one_http_door,
               test_the_harness_still_runs_the_real_path,
               test_the_stub_embedder_can_be_similar_to_something):
        fn()
    print(f"\ngolden store reaches no service: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
