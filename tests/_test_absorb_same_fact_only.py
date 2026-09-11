#!/usr/bin/env python3
"""K7: a same-title note from another session is absorbed only when it is the same fact.

The same-day, same-stem absorb rewrote an earlier note in place whenever a later session produced
the same title - decided by the title alone. On the supersession stand's controls that was most of
what we lost: session two's *different* fact on the same topic ("logs to Loki" after "traces to
Tempo") replaced session one's served text, the earlier statement survived only under
`## Previous statement`, and a still-true fact stopped being handed back on 5 of 40 explicit and
14 of 40 implicit control case-runs (ledger K1b/K7).

The gate: literals that agree (both sides carry some, and the new `[facts]` block carries every old
literal) absorb as before with no call; anything else - literals that disagree, or a side with none
(most control notes carry none) - goes to one adjudication call, and a "separate" verdict keeps both
notes, the new one as a sibling. The judge failing open is part of the contract. Every section below
breaks the mechanism by hand once to show the check would catch it.
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


PROJ, DAY = "k7proj", "2026-06-01"
S1, S2 = f"{DAY}-1000-{PROJ}-session-aaaaaaaa", f"{DAY}-1100-{PROJ}-session-bbbbbbbb"
CALLS: list[str] = []


def judge(relation):
    def fake(prompt, project=None, **kw):
        CALLS.append(prompt)
        return {"relation": relation} if relation else {}
    return fake


def write(desc, session, title="observability stack"):
    return m.write_typed_note("Decisions", {"title": title, "description": desc}, PROJ, DAY,
                              ["t"], "decision", session_stem_=session)


def served(stem):
    body = (m.VAULT / "Decisions" / f"{stem}.md").read_text(encoding="utf-8")
    _, desc, _ = m._parse_note_body(body.split("\n"))
    return desc, body


def fresh(mode="llm"):
    CALLS.clear()
    d = make_sandbox(m, "k7_", offline=True)
    m.ABSORB_JUDGE = 1
    m.ABSORB_JUDGE_MODE = mode
    return d


print("\n- the facts block is read back as a set of literals -")
check("literals parse", m._facts_in("traces go to Tempo  [facts] Tempo · port 3200") == {"tempo", "port 3200"})
check("no block, no literals", m._facts_in("plain description") == set())

print("\n- a different fact under the same title is a sibling, not an absorb -")
d = fresh()
m.generate_json = judge("separate")
a = write("traces are exported to Tempo  [facts] Tempo", S1)
b = write("logs are shipped to Loki  [facts] Loki", S2)
check("the earlier note kept its stem", bool(a) and a == f"{DAY}-{PROJ}-decision-observability-stack")
check("the later note is a sibling (-2), not the same stem", bool(b) and b != a and b.startswith(a), b)
desc_a, body_a = served(a)
check("the earlier statement is still what the earlier note serves", "Tempo" in desc_a, desc_a)
check("nothing was demoted to a previous statement", "## Previous statement" not in body_a)
check("exactly one adjudication call was made", len(CALLS) == 1, str(len(CALLS)))
check("the judge saw both statements", "Tempo" in CALLS[0] and "Loki" in CALLS[0])
check("two live notes on disk", len(list((d / "Decisions").glob(f"*{PROJ}-decision-observability-stack*.md"))) == 2)

print("\n- the same fact restated or replaced still absorbs (the judge says so) -")
d = fresh()
m.generate_json = judge("replaces")
a = write("the HTTP client timeout is 30 seconds  [facts] 30 seconds", S1)
b = write("the HTTP client timeout is 5 seconds  [facts] 5 seconds", S2)
check("absorbed into the same stem", a == b, f"{a} vs {b}")
desc_a, body_a = served(a)
check("the note now serves the replacement", "5 seconds" in desc_a and "30 seconds" not in desc_a, desc_a)
check("the earlier statement survives under Previous statement", "## Previous statement" in body_a and "30 seconds" in body_a)
check("one call", len(CALLS) == 1, str(len(CALLS)))

print("\n- agreeing literals absorb without asking -")
d = fresh()
m.generate_json = judge("separate")          # a judge that would refuse - it must not be consulted
a = write("the embedder is bge-m3  [facts] bge-m3", S1)
b = write("the embedder is bge-m3, confirmed after the suite  [facts] bge-m3 · pytest -q", S2)
check("a superset of the old literals absorbs", a == b, f"{a} vs {b}")
check("no adjudication call", len(CALLS) == 0, str(len(CALLS)))

print("\n- a side with no literals goes to the judge (most control notes carry none) -")
d = fresh()
m.generate_json = judge("separate")
a = write("traces are exported to Tempo  [facts] Tempo", S1)
b = write("logs are shipped to Loki", S2)
check("no literals on the new side, judge says separate: a sibling", a != b and b.startswith(a), f"{a} vs {b}")
check("one adjudication call", len(CALLS) == 1, str(len(CALLS)))
d = fresh()
m.generate_json = judge("replaces")
a = write("traces are exported to Tempo", S1)
b = write("traces are exported to Jaeger now  [facts] Jaeger", S2)
check("no literals on the old side, judge says replaces: absorbed", a == b, f"{a} vs {b}")
check("one adjudication call", len(CALLS) == 1, str(len(CALLS)))
d = fresh()
m.generate_json = judge("separate")
a = write("we settled the observability stack", S1)
b = write("we settled the observability stack, confirmed", S2)
check("no literals on either side: the judge is asked, not assumed", len(CALLS) == 1 and a != b, f"{len(CALLS)} {a} {b}")

print("\n- the judge failing open keeps the prior behaviour -")
d = fresh()
m.generate_json = judge(None)                  # {} - the backend produced nothing
a = write("traces are exported to Tempo  [facts] Tempo", S1)
b = write("logs are shipped to Loki  [facts] Loki", S2)
check("an unanswered judge absorbs (fail open)", a == b)


def boom(prompt, project=None, **kw):
    raise RuntimeError("backend down")


d = fresh()
m.generate_json = boom
a = write("traces are exported to Tempo  [facts] Tempo", S1)
b = write("logs are shipped to Loki  [facts] Loki", S2)
check("a raising judge absorbs and never breaks the write", a == b and bool(b))

print("\n- the same session refreshing its own note never asks -")
d = fresh()
m.generate_json = judge("separate")
a = write("traces are exported to Tempo  [facts] Tempo", S1)
b = write("logs are shipped to Loki  [facts] Loki", S1)
check("a same-session re-encounter refreshes in place", a == b)
check("no adjudication call", len(CALLS) == 0)

print("\n- shadow mode (a stand control): the call is made, the verdict is only logged -")
d = fresh("shadow")
m.generate_json = judge("separate")
a = write("traces are exported to Tempo  [facts] Tempo", S1)
b = write("logs are shipped to Loki  [facts] Loki", S2)
check("shadow: absorbed despite a separate verdict", a == b)
check("shadow: the call was still made", len(CALLS) == 1, str(len(CALLS)))

print("\n- the kill switch restores the title-only absorb (mutation check) -")
d = fresh()
m.ABSORB_JUDGE = 0
m.generate_json = judge("separate")
a = write("traces are exported to Tempo  [facts] Tempo", S1)
b = write("logs are shipped to Loki  [facts] Loki", S2)
check("with the gate off, the different fact is absorbed - the old defect", a == b)
check("and the judge is not consulted", len(CALLS) == 0)
m.ABSORB_JUDGE = 1

print(f"\nabsorb same-fact gate: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
