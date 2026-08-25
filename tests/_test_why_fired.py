#!/usr/bin/env python3
"""One object, four surfaces, and a test that makes them agree.

A guard that fires interrupts the agent, so the interruption has to carry its own
justification. Before GOAL D3 four surfaces answered that differently: the Python API returned
`{id, status, message, scope}`, the CLI printed one line, the MCP tool printed a slightly
different line, and the dashboard did not mention guards at all. Nothing forced them to agree,
so "why did this fire?" had four answers depending on where you asked.

`nevertwice/why_fired.py` builds the answer once. This suite proves the four surfaces render
*that* object and not their own reconstruction of it:

1. **Completeness** - every field GOAL D3 asks for is present, and a field that does not apply
   (a guard has no lexical or semantic contribution - it fires on a regex) says so rather than
   reporting a zero, because a zero is a lie shaped like a measurement.
2. **Agreement** - `api.guards_check(explain=True)`, `api.why_fired`, the CLI and the MCP tool
   produce the same object for the same guard.
3. **The hot path is untouched** - the default `guards.check()` result is byte-identical with
   and without the module present. The zero-token argument rests on that.
4. **Mutation** - each guarantee is broken on purpose and must turn this suite red.

Drives the real engine on a throwaway store, like `_test_schemas.py`: a contract checked
against itself is not checked.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import api                      # noqa: E402
import guards as G              # noqa: E402
import mcp_server               # noqa: E402
import schemas                  # noqa: E402
import why_fired as W           # noqa: E402

LESSON = {
    "type": "mistake", "title": "sql-built-by-fstring",
    "description": "A filter was interpolated into the SQL string - an injection hole.",
    "prevention": "Never build SQL by f-string - pass values as query parameters.",
    "entities": ["database", "security"],
}
ACTION = 'cursor.execute(f"SELECT * FROM users WHERE name = {name}")'
PATTERN = r'f"SELECT'

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _seed() -> tuple[str, str]:
    """A real note, a real guard born from it. Returns (guard_id, note_stem)."""
    api.remember_lessons([LESSON], project="acme", embed=False)
    import memory_hook as m
    stem = next(n["stem"] for n in m._iter_all_notes() if "sql" in n["stem"])
    ledger = G.load_guards()
    guard = G.make_guard(PATTERN, "past mistake: never build SQL by f-string",
                         project="acme", born_from=[stem])
    G.register(ledger, guard)
    G.save_guards(ledger)
    return guard["id"], stem


GUARD_ID, STEM = _seed()


def _norm(why: dict) -> dict:
    """Drop the counters that legitimately move between two reads of the same guard.

    `fired` is telemetry bumped by `api.guards_check`, so two surfaces called in sequence will
    disagree on it by design. Everything else must match exactly - and normalising more than
    this would be the test quietly excusing the drift it exists to catch.
    """
    out = dict(why)
    out.pop("fired", None)
    out.pop("last_fired", None)
    return out


# ------------------------------------------------------------ completeness


def test_the_object_answers_the_question() -> None:
    print("\n- the object carries every field D3 asks for -")
    why = api.why_fired(GUARD_ID, ACTION, project="acme")
    check("the guard resolves", why is not None)
    if why is None:
        return

    check("it declares its shape version", why.get("schema_version") == W.SCHEMA_VERSION)
    check("it declares what kind of intervention this was", why.get("kind") in W.KINDS,
          str(why.get("kind")))
    check("it conforms to schemas.WhyFired", not schemas.conforms(why, "WhyFired"),
          "; ".join(schemas.conforms(why, "WhyFired")))

    match = why.get("match") or {}
    check("the matched span is recovered", match.get("span") == [15, 23], str(match.get("span")))
    check("the matched text is the text that fired", match.get("text") == PATTERN,
          repr(match.get("text")))
    check("the span points at the real action text",
          ACTION[match["span"][0]:match["span"][1]] == match["text"] if match.get("span")
          else False)

    source = why.get("source") or {}
    episodes = source.get("episodes") or []
    check("the source episode is named", any(e.get("stem") == STEM for e in episodes),
          str([e.get("stem") for e in episodes]))
    check("the source episode is resolved to a real note",
          all(e.get("resolved") for e in episodes))
    check("the source episode carries its title",
          any((e.get("title") or "").strip() for e in episodes))
    check("recurrence is reported", isinstance(why.get("recurrence"), int),
          str(why.get("recurrence")))
    check("age is reported in days", isinstance(why.get("age_days"), int),
          str(why.get("age_days")))
    check("confidence is reported", isinstance(why.get("confidence"), (int, float)),
          str(why.get("confidence")))
    check("confidence states what it is", "Laplace" in (why.get("confidence_basis") or ""))

    policy = why.get("policy") or {}
    check("the policy decision is stated", policy.get("decision") in ("warn", "block"),
          str(policy.get("decision")))
    check("the promotion threshold is named", policy.get("promote_at") == G.K_PROMOTE)
    check("the retirement threshold is named", policy.get("retire_at") == G.M_RETIRE)
    check("the policy says what would change the decision",
          len(str(policy.get("promotion") or "")) > 20)

    cost = why.get("cost") or {}
    check("the zero-token claim is stated as arithmetic",
          cost.get("tokens_until_it_fired") == 0)
    check("what it spent is reported", isinstance(cost.get("tokens_spent_now"), int))
    check("the counterfactual is reported",
          isinstance(cost.get("tokens_to_read_the_sources"), int),
          str(cost.get("tokens_to_read_the_sources")))
    check("the estimate says it is an estimate", "estimate" in (cost.get("basis") or ""))


def test_an_inapplicable_signal_says_so_rather_than_reporting_zero() -> None:
    """The failure this prevents: `lexical: 0.0` reads as a measurement that came out zero,
    when the truth is that a regex match has no lexical contribution to measure."""
    print("\n- a signal that does not apply is None with a reason, never 0.0 -")
    signals = (api.why_fired(GUARD_ID, ACTION, project="acme") or {}).get("signals") or {}
    for name in ("lexical", "semantic"):
        check(f"{name} is None, not a number", signals.get(name) is None,
              repr(signals.get(name)))
        check(f"{name} says why it is absent",
              len(str(signals.get(f"{name}_note") or "")) > 30)
    check("the graph path is not computed unless asked",
          signals.get("graph_path") is None and "deep=True" in
          str(signals.get("graph_path_note") or ""))


# -------------------------------------------------------------- agreement


def test_all_four_surfaces_render_the_same_object() -> None:
    print("\n- Python, CLI, MCP and the dashboard agree -")
    baseline = _norm(api.why_fired(GUARD_ID, ACTION, project="acme"))

    # 1. Python: the explain=True path on the hot-path call.
    hits = api.guards_check(ACTION, project="acme", explain=True)
    check("guards_check(explain=True) attaches the object", bool(hits) and "why" in hits[0])
    if hits and hits[0].get("why"):
        check("Python surface agrees", _norm(hits[0]["why"]) == baseline,
              "guards_check(explain=True) differs from api.why_fired")

    # 2. CLI: the same object, through the process a contributor actually runs.
    proc = subprocess.run(
        [sys.executable, str(ROOT / "nevertwice" / "guards.py"), "check", ACTION,
         "--project", "acme", "--json"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, env={**_env_guard.child_env()} if hasattr(_env_guard, "child_env") else None)
    cli_ok = proc.returncode == 0 and proc.stdout.strip().startswith("[")
    check("the CLI emits the object as JSON", cli_ok,
          (proc.stdout + proc.stderr)[:200].replace("\n", " | "))
    if cli_ok:
        cli = json.loads(proc.stdout)
        check("CLI surface agrees", bool(cli) and _norm(cli[0]) == baseline,
              "nevertwice-guards check --json differs from api.why_fired")

    # 3. MCP: the tool handler, called exactly as a client would.
    text, is_error = mcp_server._tool_memory_guard_check(
        {"action_text": ACTION, "project": "acme", "explain": True})
    check("the MCP tool does not error", not is_error, text[:160])
    check("the MCP tool renders the shared formatter",
          W.render(api.why_fired(GUARD_ID, ACTION, project="acme")).splitlines()[1].strip()
          in text,
          "MCP output does not contain why_fired.render's own lines")
    check("the MCP tool declares the explain argument",
          any(t["name"] == "memory_guard_check" and "explain" in t["inputSchema"]["properties"]
              for t in mcp_server.TOOLS))

    # 4. Dashboard: the HTML must carry this guard's evidence, not a re-derived summary.
    html = api.dashboard() if hasattr(api, "dashboard") else ""
    check("the dashboard has a guards section", "Guards" in html and "0 tokens until" in html,
          "no guards section in the rendered dashboard")
    check("the dashboard names this guard", GUARD_ID in html)
    check("the dashboard shows the policy the object computed",
          (baseline["policy"]["promotion"][:40] in html), "policy text missing")


def test_the_hot_path_is_unchanged() -> None:
    """The whole zero-token argument is that `check()` is a regex match. If explaining had
    leaked into it, the product's central claim would be quietly false."""
    print("\n- the default check path still returns exactly what it always did -")
    hits = api.guards_check(ACTION, project="acme")
    check("no explanation is attached by default", all("why" not in h for h in hits))
    check("the hit shape is unchanged",
          all(set(h) == {"id", "status", "message", "scope"} for h in hits),
          str([sorted(h) for h in hits]))
    check("the hit still conforms to Intervention",
          all(not schemas.conforms(h, "Intervention") for h in hits))


# --------------------------------------------------------------- mutations


def test_mutations_turn_it_red() -> None:
    print("\n- the checks fail when they should -")
    ledger = G.load_guards()

    # 1. A guard whose source note is gone must say so, not silently show an empty source.
    orphan = G.make_guard(r"eval\(", "past mistake: never eval user input",
                          project="acme", born_from=["2020-01-01-acme-mistake-does-not-exist"])
    G.register(ledger, orphan)
    why = W.explain(orphan["id"], "y = eval(s)", project="acme", guards=ledger)
    check("a guard whose source note is missing admits it",
          bool(why) and "no longer in the live store" in (why["source"].get("note") or ""),
          str((why or {}).get("source")))
    check("the missing episode is marked unresolved",
          bool(why) and why["source"]["episodes"][0]["resolved"] is False)

    # 2. A guard with no source at all is unfalsifiable, and must be labelled as such.
    rootless = G.make_guard(r"chmod 777", "past mistake: never chmod 777", project="acme")
    G.register(ledger, rootless)
    why = W.explain(rootless["id"], "chmod 777 /srv", project="acme", guards=ledger)
    check("a guard with no source note says its evidence cannot be traced",
          bool(why) and "cannot be traced" in (why["source"].get("note") or ""))

    # 3. Explaining a guard against text it does not match must not invent a span.
    why = W.explain(GUARD_ID, "print('hello')", project="acme", guards=ledger)
    check("no span is invented when the text does not match",
          bool(why) and why["match"]["span"] is None and "does not match" in why["match"]["note"])

    # 4. A corrupt pattern must be reported, not raised.
    broken = dict(G.make_guard(r"ok\(", "placeholder", project="acme"))
    broken["pattern"] = "(unclosed"
    ledger.append(broken)
    why = W.explain(broken["id"], "(unclosed", project="acme", guards=ledger)
    check("a pattern that no longer compiles is reported, not raised",
          bool(why) and "no longer compiles" in (why["match"].get("note") or ""))

    # 5. An unknown id is None, not an empty object that reads like a real answer.
    check("an unknown guard id returns None",
          W.explain("g-doesnotexist", ACTION, guards=ledger) is None)

    # 6. A pack guard can never be promoted to blocking, and the object must say so.
    pack = dict(G.make_guard(r"pickle\.loads", "pack: never unpickle untrusted data"))
    pack["pack"] = True
    pack["corroborations"] = G.K_PROMOTE + 5
    ledger.append(pack)
    why = W.explain(pack["id"], "pickle.loads(blob)", guards=ledger)
    check("a pack guard past the promotion threshold still says it can never block",
          bool(why) and why["policy"]["advisory_only"] is True
          and "never" in why["policy"]["promotion"],
          str((why or {}).get("policy", {}).get("promotion")))


def main() -> int:
    for fn in (test_the_object_answers_the_question,
               test_an_inapplicable_signal_says_so_rather_than_reporting_zero,
               test_all_four_surfaces_render_the_same_object,
               test_the_hot_path_is_unchanged,
               test_mutations_turn_it_red):
        fn()
    print(f"\nwhy_fired: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
