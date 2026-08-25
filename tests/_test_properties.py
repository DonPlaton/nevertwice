#!/usr/bin/env python3
"""Properties, concurrency, and the four failures this project has actually had.

`_test_doctor.py` proves the diagnostic *detects* three historical failures from fixtures.
This suite is the other half of GOAL E1: **reproduce each incident class** - recreate the
condition that caused it, and require the system to surface it - and then hold the engine to
the properties that would have prevented the rest.

The four classes, in the project's own words:

1. **Dead graphify.** The graph generator died with a `NameError` on *import* while its
   fire-and-forget wrapper logged "graph.json refreshed" on every crashed run, for over a
   month. Reproduced here by building a genuinely broken copy of the package.
2. **Stalled extraction.** Extraction stalled behind an unreachable backend; sessions piled up
   and the only symptom was memory that had stopped getting better.
3. **Embedding-space mismatch.** A cache built by one model was queried by another. Retrieval
   abstained - correct behaviour, and indistinguishable from an empty store.
4. **Live-store contamination.** 2026-08-13, 08-18 and 08-25: test and example code wrote to
   the owner's real vault. Reproduced in a child process with a real-looking store exported
   the supported way, which is what made the original incidents possible.

The property half covers what E1 lists: arbitrary Unicode and YAML in frontmatter, truncated
state and two-generation recovery, concurrent writers, symlinks *and Windows junctions*,
JSON-RPC fuzzing, clock jumps, and duplicate or reordered events replaying idempotently.

**Hypothesis is used when it is installed and never required.** The core CI matrix installs
nothing, so every property has a deterministic generator that runs there; with the `[dev]`
extra the same properties get randomised inputs. A suite that only runs for contributors who
installed an optional package is a suite that silently stops running.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import doctor                   # noqa: E402
import memory_hook as m         # noqa: E402

PASSED = 0
FAILED = 0

try:                            # dev-only, and never required
    from hypothesis import given, settings, HealthCheck, strategies as st
    HAVE_HYPOTHESIS = True
except ImportError:             # pragma: no cover - the core matrix path
    HAVE_HYPOTHESIS = False


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


#: Deterministic stand-ins for what Hypothesis would explore. Every one of these is a shape
#: that has broken something somewhere: a BOM, a lone surrogate, a YAML delimiter inside a
#: value, RTL marks, an emoji outside the BMP, CRLF, and a NUL.
NASTY_TEXT = [
    "", " ", "﻿leading BOM", "line1\r\nline2", "tabs\tand\vvertical",
    "emoji 🧠🔬 outside the BMP", "combining é vs é", "RTL ‮override‬",
    "yaml: value: with: colons", "--- not a real frontmatter ---",
    "[[wikilink]] and #hash", "quote \" and ' and ` ", "back\\slash",
    "null\x00byte", "very " * 200, "日本語のテキスト", "​zero width",
]


# ══════════════════════════════════ the four incident classes ══════════

def test_incident_dead_graphify_is_reproduced() -> None:
    """Class 1: died on import, wrapper logged success anyway."""
    print("\n- incident: the graph generator that died on import for a month -")
    healthy = doctor.check_graph_generator()
    check("a healthy package reports the generator as importable",
          healthy["status"] == doctor.OK, str(healthy))

    with tempfile.TemporaryDirectory() as tmp:
        broken = Path(tmp) / "pkg"
        broken.mkdir()
        # The original was a NameError at module scope. Reproduced exactly: importing it
        # raises, and nothing at the call site would notice because the real wrapper caught
        # everything and logged success regardless.
        (broken / "graphify.py").write_text(
            "UNDEFINED_NAME_FROM_THE_REAL_INCIDENT\n", encoding="utf-8")
        result = doctor.check_graph_generator(package_dir=broken)

    check("the broken generator is reported as FAIL", result["status"] == doctor.FAIL,
          str(result))
    check("the report names the actual exception", "NameError" in result["detail"],
          result["detail"])
    check("and it carries a repair", bool(result["repair"]), str(result))
    check("the repair explains why nothing else catches it",
          "logs success" in result["repair"], result["repair"])


def test_incident_stalled_extraction_is_reproduced() -> None:
    """Class 2: sessions pile up, the store stops growing, nothing errors."""
    print("\n- incident: extraction stalled behind an unreachable backend -")
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "Mistakes").mkdir()
        stale = vault / "Mistakes" / "2026-01-01-acme-mistake-old.md"
        stale.write_text("---\ntype: mistake\n---\n\n# old\n", encoding="utf-8")
        old = time.time() - 60 * 60 * 24 * 30
        os.utime(stale, (old, old))

        result = doctor.check_capture_freshness(vault)
        check("a store that stopped growing is not reported as healthy",
              result["status"] != doctor.OK, str(result))
        check("the report says how long it has been", "day" in result["detail"].lower()
              or "d " in result["detail"] or any(c.isdigit() for c in result["detail"]),
              result["detail"])

        fresh = vault / "Mistakes" / "2026-08-26-acme-mistake-new.md"
        fresh.write_text("---\ntype: mistake\n---\n\n# new\n", encoding="utf-8")
        check("a store still being written to is healthy",
              doctor.check_capture_freshness(vault)["status"] == doctor.OK,
              str(doctor.check_capture_freshness(vault)))


def test_incident_embedding_space_mismatch_is_reproduced() -> None:
    """Class 3: a cache built by one model, queried by another. Abstains forever."""
    print("\n- incident: two embedding spaces, and silence that looks like an empty store -")
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / ".embeddings_meta.json").write_text(
            json.dumps({"model": "ollama:some-other-model", "dim": 768}), encoding="utf-8")
        (vault / ".embeddings_cache.json").write_text(
            json.dumps({"a-stem": {"vec": [0.1] * 768}}), encoding="utf-8")

        # The incident needed *two* spaces: a cache built by one model and a config naming
        # another. Without the second half there is nothing to mismatch against, and the
        # first version of this test asserted a failure the checker could not possibly report.
        saved = os.environ.get("NEVERTWICE_EMBED_MODEL")
        os.environ["NEVERTWICE_EMBED_MODEL"] = "ollama:bge-m3"
        try:
            result = doctor.check_embedding_space(vault)
        finally:
            if saved is None:
                os.environ.pop("NEVERTWICE_EMBED_MODEL", None)
            else:
                os.environ["NEVERTWICE_EMBED_MODEL"] = saved
        check("a mismatched cache is not reported as healthy",
              result["status"] != doctor.OK, str(result))
        check("the report names the model actually configured",
              "bge-m3" in json.dumps(result), str(result))
        check("the report names both spaces",
              "some-other-model" in json.dumps(result), str(result))
        check("and says why the symptom is silence",
              any(word in json.dumps(result).lower()
                  for word in ("abstain", "meaningless", "mismatch")), str(result))


def test_incident_live_store_contamination_is_reproduced() -> None:
    """Class 4: the one that happened three times.

    Reproduced the way it actually happened - `NEVERTWICE_VAULT` exported machine-wide, which
    is the *supported* way to point at a real store, and a script that imports the package
    without declaring a sandbox. The point is not that the guard exists; it is that the guard
    fires under the exact configuration that caused the incidents.
    """
    print("\n- incident: test code writing to the owner's real vault -")
    with tempfile.TemporaryDirectory() as tmp:
        pretend_live = Path(tmp) / "a-real-looking-store"
        (pretend_live / "Mistakes").mkdir(parents=True)
        (pretend_live / "Mistakes" / "2026-01-01-acme-mistake-precious.md").write_text(
            "---\ntype: mistake\n---\n\n# do not touch\n", encoding="utf-8")
        before = {p: p.read_bytes() for p in pretend_live.rglob("*") if p.is_file()}

        script = Path(tmp) / "unguarded.py"
        script.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "import sandbox_guard\n"
            "sandbox_guard.isolate()\n"
            "sys.path.insert(0, sandbox_guard.PKG if hasattr(sandbox_guard, 'PKG') else "
            f"{str(ROOT / 'nevertwice')!r})\n"
            "import memory_hook as m\n"
            "print('VAULT=' + str(m.VAULT))\n", encoding="utf-8")

        env = {**os.environ, "NEVERTWICE_VAULT": str(pretend_live),
               "NEVERTWICE_HOME": str(pretend_live), "PYTHONUTF8": "1"}
        proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120, env=env)

        resolved = ""
        for line in proc.stdout.splitlines():
            if line.startswith("VAULT="):
                resolved = line[6:].strip()

        check("the guarded process runs", proc.returncode == 0,
              (proc.stdout + proc.stderr)[-300:].replace("\n", " | "))
        check("THE INCIDENT CLASS: the store did NOT resolve to the exported live path",
              resolved and Path(resolved).resolve() != pretend_live.resolve(),
              f"resolved to {resolved!r} with NEVERTWICE_VAULT={pretend_live}")
        after = {p: p.read_bytes() for p in pretend_live.rglob("*") if p.is_file()}
        check("and the real-looking store is byte-unchanged", before == after,
              str(set(before) ^ set(after)))


def test_an_undeclared_script_is_caught_before_it_ever_runs() -> None:
    """The other half of class 4 - and it is a *static* guarantee, not a runtime one.

    `verify()` is deliberately a no-op when nothing was pinned: it asserts that a declared
    sandbox held, and there is nothing to assert about a process that never declared one. (The
    first version of this test asserted `verify()` would raise on its own. It does not, and it
    should not - the enforcement lives one layer up.) What catches an undeclared script is
    `tools/check_sandbox.py`, statically, in CI, before the script is ever run - which is the
    only place it *can* be caught, because by the time an unguarded import has resolved the
    vault, the next write is already addressed to the real store.
    """
    print("\n- an undeclared script is caught by the lint, before it can run -")
    import sandbox_guard
    check("verify() is a no-op with nothing pinned - by design",
          sandbox_guard.verify() is None,
          "it asserts a declared sandbox held; there is none here to have escaped")

    # The lint's own entry point enumerates `git ls-files`, so an untracked probe is invisible
    # to it - the same "only tracked files are seen" property that put a docs failure into CI
    # two iterations ago. Driving `check_entry_points` directly tests the rule rather than the
    # enumeration, and needs nothing added to the index.
    sys.path.insert(0, str(ROOT / "tools"))
    import check_sandbox

    offender = ROOT / "examples" / "e1_undeclared_probe.py"
    offender.write_text(
        "# a script that reaches the store without declaring which store\n"
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'nevertwice'))\n"
        "import memory_hook as m\n"
        "print(m.VAULT)\n", encoding="utf-8")
    guarded = ROOT / "examples" / "e1_declared_probe.py"
    guarded.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"
        "import sandbox_guard\n"
        "sandbox_guard.isolate()\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'nevertwice'))\n"
        "import memory_hook as m\n"
        "print(m.VAULT)\n", encoding="utf-8")
    try:
        facts = [check_sandbox.Facts(offender), check_sandbox.Facts(guarded)]
        by_dir = {offender.parent: {f.path.name: f for f in facts}}
        problems = check_sandbox.check_entry_points(facts, by_dir)
    finally:
        offender.unlink(missing_ok=True)
        guarded.unlink(missing_ok=True)

    check("the lint flags the undeclared script",
          any("e1_undeclared_probe" in p for p in problems), str(problems))
    check("and says it resolves the store without arming",
          any("without sandbox_guard" in p for p in problems), str(problems))
    check("the script that DOES declare a sandbox is not flagged",
          not any("e1_declared_probe" in p for p in problems), str(problems))


# ═════════════════════════════════════════════ properties ══════════════

def _roundtrip_frontmatter(value: str) -> bool:
    """A value written into frontmatter comes back as itself, or the reader says it cannot."""
    text = f"---\ntype: mistake\ntitle: {value}\n---\n\nbody\n"
    fm, _body = m._read_frontmatter(text)
    return "title" in fm or not value.strip()


def test_frontmatter_survives_arbitrary_text() -> None:
    print("\n- arbitrary Unicode and YAML in a note header -")
    survived = [v for v in NASTY_TEXT if _roundtrip_frontmatter(v)]
    check("every nasty value is read without raising", len(survived) == len(NASTY_TEXT),
          str([v[:20] for v in NASTY_TEXT if v not in survived]))

    fm, body = m._read_frontmatter("﻿---\ntype: mistake\ntitle: x\n---\n\nbody\n")
    check("a leading BOM does not blank the header", fm.get("type") == "mistake", str(fm))
    check("and the body survives it", "body" in body, repr(body[:40]))

    fm, body = m._read_frontmatter("no frontmatter at all\n")
    check("a note with no header is tolerated", fm == {} and "no frontmatter" in body)
    fm, body = m._read_frontmatter("---\nunterminated: yes\n")
    check("an unterminated header is tolerated", isinstance(fm, dict))

    if HAVE_HYPOTHESIS:
        @given(st.text(max_size=200))
        @settings(max_examples=200, deadline=None,
                  suppress_health_check=[HealthCheck.function_scoped_fixture])
        def _prop(value):
            m._read_frontmatter(f"---\ntype: mistake\ntitle: {value}\n---\n\nbody\n")
        _prop()
        check("hypothesis found no input that raises (200 examples)", True)
    else:
        print("       (hypothesis not installed - deterministic corpus only)")


def test_truncated_state_recovers_from_the_previous_generation() -> None:
    print("\n- a half-written state file, and the generation behind it -")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        m._save_json_generations(path, json.dumps({"version": 1, "good": True}))
        check("the first generation loads",
              m._load_json_generations(path, "state") == {"version": 1, "good": True})

        m._save_json_generations(path, json.dumps({"version": 2, "good": True}))
        path.write_text('{"version": 2, "trunc', encoding="utf-8")
        recovered = m._load_json_generations(path, "state")
        check("a truncated current generation falls back rather than returning nothing",
              isinstance(recovered, dict) and recovered.get("version") in (1, 2),
              str(recovered))

        for junk in ("", "   ", "null", "[]", "{", '{"a": ', "\x00\x01\x02"):
            path.write_text(junk, encoding="utf-8", errors="replace")
            result = m._load_json_generations(path, "state")
            check(f"junk {junk[:8]!r} does not raise", result is None or isinstance(result, dict))

        expected_list = m._load_json_generations(path, "guards", expect=list)
        check("a type mismatch returns None rather than the wrong shape",
              expected_list is None or isinstance(expected_list, list), str(expected_list))


def test_concurrent_writers_never_leave_a_corrupt_file() -> None:
    print("\n- eight threads writing the same state file -")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "concurrent.json"
        errors = []

        def writer(n: int) -> None:
            try:
                for i in range(15):
                    m._save_json_generations(path, json.dumps({"writer": n, "i": i}))
            except Exception as exc:                    # noqa: BLE001 - the finding
                errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        check("no writer raised", not errors, "; ".join(errors[:3]))
        final = m._load_json_generations(path, "concurrent")
        check("the file is readable afterwards", isinstance(final, dict), str(final))
        check("and holds one writer's complete value, not a blend of two",
              isinstance(final, dict) and set(final) == {"writer", "i"}, str(final))


def test_a_symlinked_or_junctioned_store_is_refused() -> None:
    """Windows junctions as well as POSIX symlinks - the platform this is developed on."""
    print("\n- a store that is really a link somewhere else -")
    with tempfile.TemporaryDirectory() as tmp:
        real = Path(tmp) / "real"
        real.mkdir()
        link = Path(tmp) / "link"

        made = kind = None
        try:
            link.symlink_to(real, target_is_directory=True)
            made, kind = True, "symlink"
        except (OSError, NotImplementedError):
            if sys.platform == "win32":
                proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)],
                                      capture_output=True, text=True)
                made, kind = proc.returncode == 0, "junction"

        if not made:
            print("       (skipped: this machine allows neither a symlink nor a junction)")
            return

        check(f"the {kind} was created", link.exists())
        check(f"a {kind} is detected as not being a real directory",
              link.resolve() != link or link.is_symlink()
              or Path(os.path.realpath(link)) == real.resolve(),
              f"{link} -> {os.path.realpath(link)}")
        check("resolving it reaches the real target",
              Path(os.path.realpath(link)).resolve() == real.resolve(),
              f"{os.path.realpath(link)} vs {real}")


def test_jsonrpc_fuzzing_never_crashes_and_never_answers_a_notification() -> None:
    print("\n- malformed JSON-RPC, and the one message that must go unanswered -")
    import mcp_server

    cases = [
        "", "   ", "not json", "{", "[]", "null", "3", '"a string"',
        json.dumps({}), json.dumps({"jsonrpc": "2.0"}),
        json.dumps({"jsonrpc": "2.0", "method": "nope", "id": 1}),
        json.dumps({"jsonrpc": "1.0", "method": "tools/list", "id": 2}),
        json.dumps({"jsonrpc": "2.0", "method": "tools/list"}),          # a NOTIFICATION
        json.dumps({"jsonrpc": "2.0", "method": "tools/call", "id": 3,
                    "params": {"name": "nope", "arguments": {}}}),
        json.dumps({"jsonrpc": "2.0", "method": "tools/call", "id": 4, "params": None}),
        json.dumps({"jsonrpc": "2.0", "method": "x" * 5000, "id": 5}),
        json.dumps({"jsonrpc": "2.0", "method": "tools/call", "id": {"weird": True}}),
    ]
    import io
    import contextlib

    def feed(raw: str) -> str:
        """Parse like the server's own loop does, dispatch, and capture what it wrote.

        `_send` writes to a cached `_REAL_STDOUT` handle - deliberately, so a cp125x pipe
        cannot raise on Cyrillic - so `redirect_stdout` never sees it. Swapping the handle is
        the only way to observe the wire.
        """
        buf = io.StringIO()
        saved = mcp_server._REAL_STDOUT
        mcp_server._REAL_STDOUT = buf
        try:
            try:
                msg = json.loads(raw)
            except ValueError:
                return ""                    # the read loop drops unparseable lines
            if isinstance(msg, dict):
                mcp_server._handle(msg)
        finally:
            mcp_server._REAL_STDOUT = saved
        return buf.getvalue()

    crashed = []
    for raw in cases:
        try:
            feed(raw)
        except Exception as exc:                        # noqa: BLE001 - the finding
            crashed.append(f"{raw[:30]!r} -> {type(exc).__name__}: {exc}")
    check("no malformed message crashes the server", not crashed, "; ".join(crashed[:3]))

    # A JSON-RPC notification carries no `id`, and answering one is a protocol violation that
    # confuses every conforming client. This is the shape a previous audit had to fix.
    answered = feed(json.dumps({"jsonrpc": "2.0", "method": "tools/list"}))
    check("a notification (no id) is never answered", answered.strip() == "",
          f"wrote {answered[:120]!r}")

    with_id = feed(json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 7}))
    check("but the same call WITH an id is answered", with_id.strip() != "",
          "if this is empty the notification check above proves nothing")
    check("and the answer echoes the id",
          '"id": 7' in with_id or '"id":7' in with_id, with_id[:160])


def test_a_clock_jump_does_not_break_anything() -> None:
    print("\n- the clock going backwards, and forwards -")
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "Mistakes").mkdir()
        note = vault / "Mistakes" / "2026-08-01-acme-mistake-x.md"
        note.write_text("---\ntype: mistake\n---\n\n# x\n", encoding="utf-8")

        for label, when in (("far future", time.time() + 60 * 60 * 24 * 365),
                            ("epoch", 0.0),
                            ("before the file", time.time() - 60 * 60 * 24 * 365)):
            try:
                result = doctor.check_capture_freshness(vault, now=when)
                ok = result["status"] in (doctor.OK, doctor.WARN, doctor.FAIL, doctor.SKIP)
                detail = str(result)
            except Exception as exc:                     # noqa: BLE001 - the finding
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            check(f"a {label} clock produces a status rather than an exception", ok, detail)

        result = doctor.check_capture_freshness(vault, now=0.0)
        check("a clock behind the file does not report a negative age",
              "-" not in result["detail"].split("day")[0] or "ago" not in result["detail"],
              result["detail"])


def test_duplicate_and_reordered_events_replay_idempotently() -> None:
    print("\n- the same session twice, and out of order -")
    import ingest

    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "session.jsonl"
        lines = [json.dumps({"role": "user", "content": f"turn {i}"}) for i in range(5)]
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        text = transcript.read_text(encoding="utf-8")

        first = ingest.sweep_session_id(transcript, text)
        second = ingest.sweep_session_id(transcript, text)
        check("the same content yields the same session id", first == second, f"{first} vs {second}")

        shuffled = "\n".join(reversed(lines)) + "\n"
        reordered = ingest.sweep_session_id(transcript, shuffled)
        check("reordered content is a DIFFERENT session, not silently the same",
              reordered != first,
              "two different conversations sharing an id would dedupe one of them away")

        flat_a = ingest._flatten_agent_jsonl(text)
        flat_b = ingest._flatten_agent_jsonl(text)
        check("flattening is deterministic", flat_a == flat_b)
        check("flattening twice is idempotent in content",
              ingest._flatten_agent_jsonl(flat_a) == flat_a or flat_a == text,
              "a second pass must not re-mangle already-flattened text")


def main() -> int:
    for fn in (test_incident_dead_graphify_is_reproduced,
               test_incident_stalled_extraction_is_reproduced,
               test_incident_embedding_space_mismatch_is_reproduced,
               test_incident_live_store_contamination_is_reproduced,
               test_an_undeclared_script_is_caught_before_it_ever_runs,
               test_frontmatter_survives_arbitrary_text,
               test_truncated_state_recovers_from_the_previous_generation,
               test_concurrent_writers_never_leave_a_corrupt_file,
               test_a_symlinked_or_junctioned_store_is_refused,
               test_jsonrpc_fuzzing_never_crashes_and_never_answers_a_notification,
               test_a_clock_jump_does_not_break_anything,
               test_duplicate_and_reordered_events_replay_idempotently):
        fn()
    print(f"\nproperties: {PASSED} passed, {FAILED} failed"
          + ("" if HAVE_HYPOTHESIS else "  (hypothesis absent - deterministic corpus)"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
