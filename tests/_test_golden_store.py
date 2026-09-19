#!/usr/bin/env python3
"""The same sessions in, the same bytes out - and the proof is checked against a canary.

Level 1 of the three-level proof that a refactor changed nothing. It drives the engine over a
fixed corpus with a deterministic extractor and a deterministic embedder (see `_golden_store.py`
for why they are *replaced* rather than switched off), then snapshots every note, its frontmatter,
the index and the order of a fixed set of queries.

The corpus is chosen to enter the code the project's headline numbers come from, because the
obvious corpus does not: a plain stubbed ingest enters 54% of the engine's statements and none of
`as_of`, `_same_fact_verdict`, `_replacement_guard`, `supersede_note`, `_same_replacement`,
`_mark_contested` or `_earlier_text`. So the corpus carries a supersession pair, a same-slug
contested pair sent through the sleep-time judge, an as-of question spanning both, a fired guard,
and recall over both live and retired notes.

Two invariants, and the second is what makes the first worth anything:

* **stable** - running the corpus twice gives the identical snapshot;
* **sensitive** - the canary: a `raise` injected into any of four load-bearing functions must make
  this proof FAIL. A proof those four can survive is empty, and would certify a refactor that broke
  them. `--canary` runs that check; the suite runs it as its last section.

    python tests/_test_golden_store.py
    python tests/_test_golden_store.py --coverage    # print what the corpus enters
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

import _golden_store as G  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

P = F = 0
PROJ_DIR = str(Path(tempfile.gettempdir()) / "golden_proj")


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}{chr(10) + detail if detail else ''}")


# ── the corpus ──────────────────────────────────────────────────────────────────────
#: Five sessions. Two of them state the same fact differently on different days, which is the
#: whole displacement machinery; one resolves a mistake; one is off-topic; one carries a literal
#: the guards can fire on.
TABLE = {
    "GS-DB-OLD": {
        "project_relevant": True,
        "decisions": [{"title": "production database version",
                       "description": "The service runs on Postgres 15 in production.",
                       "facts": ["Postgres 15"]}],
        "patterns": [], "mistakes": [],
        "session_summary": "picked the database", "context_update": "database chosen",
    },
    "GS-DB-NEW": {
        "project_relevant": True,
        "decisions": [{"title": "production database version",
                       "description": "The service runs on Postgres 16 in production.",
                       "facts": ["Postgres 16"]}],
        "patterns": [], "mistakes": [],
        "session_summary": "upgraded the database", "context_update": "database upgraded",
    },
    "GS-PORT": {
        "project_relevant": True,
        "decisions": [{"title": "the api listens on 8080",
                       "description": "The public API listens on port 8080.",
                       "facts": ["8080"]}],
        "patterns": [], "mistakes": [],
        "session_summary": "fixed the port", "context_update": "port settled",
    },
    "GS-BUG": {
        "project_relevant": True,
        "mistakes": [{"title": "eval on user input crashes the handler",
                      "description": "Calling eval on request data raised and took the worker down.",
                      "prevention": "Parse with json.loads instead of eval."}],
        "patterns": [], "decisions": [],
        "session_summary": "debugged the handler", "context_update": "handler fixed",
    },
    "GS-OFFTOPIC": {
        "project_relevant": False,
        "patterns": [{"title": "unrelated tinkering", "description": "Not about this project."}],
        "mistakes": [], "decisions": [],
        "session_summary": "off topic", "context_update": "",
    },
}


def judge(prompt: str) -> str:
    """The verdict the model would give, in the judge's own schema.

    It answers the PROMPT, so the engine's `_same_fact_verdict` builds that prompt and parses this
    answer for real, and `consolidate_memory` then applies its three replacement guards to the
    result. Replacing `_same_fact_verdict` itself would be easier and would leave all of that cold
    - which is exactly what the first draft of this suite did, silently.
    """
    return "replaces" if "postgres" in prompt.lower() else "separate"


def run_corpus(store: Path) -> dict:
    """Ingest the corpus, adjudicate, then ask the questions. Returns the snapshot."""
    G.install(m, TABLE, judge=judge)
    Path(PROJ_DIR).mkdir(parents=True, exist_ok=True)
    m.PROJECT_ROOTS = [str(Path(PROJ_DIR).parent)]
    m._ROOTS_NORM = [m._norm_path(str(Path(PROJ_DIR).parent))]
    #: The corpus has to live somewhere, and every writable somewhere in a test is under the
    #: OS temp tree, which `_EXCLUDE_FRAGMENTS` rejects as transient. That rule is real and has
    #: its own suite (`_test_memory_v3`); it is not what this proof is about, so the fragment
    #: is lifted here and only here.
    m._EXCLUDE_FRAGMENTS = [f for f in m._EXCLUDE_FRAGMENTS
                            if 'temp' not in f.lower() and 'tmp' not in f.lower()]

    tdir = store / "_transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    order = [("GS-DB-OLD", "2026-03-01"), ("GS-PORT", "2026-03-02"), ("GS-BUG", "2026-03-03"),
             ("GS-OFFTOPIC", "2026-03-04"), ("GS-DB-NEW", "2026-03-05")]
    for marker, day in order:
        tp = G.session(tdir / f"{marker}.jsonl", cwd=PROJ_DIR, marker=marker, day=day)
        m.process_session(f"gs-{marker.lower()}", PROJ_DIR, tp, "SessionEnd", {})

    project = m.derive_project_from_cwd(PROJ_DIR)

    # the sleep-time half: whatever the write path left contested goes past the guards to a verdict
    contested_before = len(m._iter_contested(project))
    try:
        cm = m._sibling("consolidate_memory")
        cm.adjudicate_contested(apply=True, has_llm=True)
    except Exception as exc:                               # noqa: BLE001 - recorded, not hidden
        adjudication = f"error: {type(exc).__name__}: {exc}"
    else:
        adjudication = "ran"
    contested_after = len(m._iter_contested(project))

    #: the guard path: a registered pattern, a tool action that matches it, and the hook's own
    #: PreToolUse entry - the branch a customer pays for on every tool call
    guard_out = []
    try:
        gmod = m._sibling("guards")
        gs = gmod.load_guards()
        gmod.register(gs, gmod.make_guard(r"eval\(", "eval on request data took the worker down",
                                          project=project))
        gmod.save_guards(gs)
        m.emit_pretooluse_guard({"tool_name": "Edit",
                                 "tool_input": {"file_path": "handler.py",
                                                "new_string": "value = eval(body)"}}, PROJ_DIR)
    except Exception as exc:                               # noqa: BLE001 - recorded, not hidden
        guard_out.append(f"error: {type(exc).__name__}: {exc}")

    #: the served payload itself, not just which notes came back. Without this the proof is blind
    #: to every change in what the reader is handed - which is exactly what track N moves.
    def payload(q):
        hits = m.pair_siblings(m.retrieve_relevant(project, q, 3))
        return [m._fact_line(h) for h in hits]

    queries = ["which database does the service use",
               "what port does the api listen on",
               "eval on user input"]
    recall = {q: [h.get("stem") or h.get("title") for h in
                  m.retrieve_relevant(project, q, 3)] for q in queries}
    asof = {day: [h.get("stem") or h.get("title") for h in m.as_of(project, day)]
            for day in ("2026-03-03", "2026-03-06")}

    served = {q: payload(q) for q in queries}
    return G.snapshot(store, {
        "recall": recall, "served": served,
        "served_chars": {q: sum(len(x) for x in v) for q, v in served.items()},
        "as_of": asof,
        "contested": [contested_before, contested_after],
        "adjudication": adjudication,
        "guard": guard_out,
    })


def once() -> dict:
    store = make_sandbox(m, "golden_")
    return run_corpus(store)


# ── stability ───────────────────────────────────────────────────────────────────────
print("# the corpus is deterministic")
first = once()
second = once()
same = first == second
check("two runs of the corpus give the identical snapshot", same,
      "\n".join(G.diff(first, second)[:12]))
check("the corpus wrote notes", len(first["files"]) > 4,
      f"{len(first['files'])} files")
check("the displacement machinery ran",
      first["contested"] != [0, 0] or any("Superseded" in f for f in first["files"]),
      f"contested {first['contested']}, adjudication {first['adjudication']}")
check("as_of answers both days", all(first["as_of"].values()),
      json.dumps(first["as_of"]))
check("recall answers every query", all(first["recall"].values()),
      json.dumps(first["recall"]))

fixture = HERE / "_golden_store_fixture.json"
if "--record" in sys.argv:
    fixture.write_text(json.dumps(first, indent=1, sort_keys=True) + "\n",
                       encoding="utf-8", newline="\n")
    print(f"  recorded {fixture}")
elif fixture.exists():
    want = json.loads(fixture.read_text(encoding="utf-8"))
    check("the snapshot matches the recorded fixture", want == first,
          "\n".join(G.diff(want, first)[:20]))
else:
    print("  [--] no fixture recorded yet (run with --record)")

# ── the canary: the proof must be able to fail ──────────────────────────────────────
#: Four functions the headline numbers are made of. `_canary_run.py` copies the package, injects
#: a `raise` into one of them and re-runs this corpus against the copy; if the snapshot still
#: matches, the proof never entered that function and certifies nothing about it.
CANARIES = ("_same_fact_verdict", "_replacement_guard", "as_of", "_same_replacement")

if "--no-canary" in sys.argv:
    print()
    print(f"golden store: {P} passed, {F} failed")
    sys.exit(1 if F else 0)

#: A raise proves the proof ENTERED a function. It does not prove the proof can see a change in
#: what that function RETURNS, and a ranking regression is exactly that: nothing crashes, one
#: number moves, an order changes. Measured 2026-09-19 by bisection at `_calibrated_fusion`: a
#: shift of 0.75 goes unnoticed and 0.9 is caught, so the fixture's ranking resolution sits
#: between them. The gate is written at 1.0 - above the resolution, never below it.
NUDGE_TARGET = "_calibrated_fusion"
NUDGE = 1.0

print("# the proof itself is sensitive")
for target in CANARIES:
    r = subprocess.run([sys.executable, str(HERE / "_canary_run.py"), target],
                       capture_output=True, text=True, timeout=900)
    broke = r.returncode != 0
    check(f"a raise in {target} breaks the proof", broke,
          (r.stdout or r.stderr)[-400:] if not broke else "")

r = subprocess.run([sys.executable, str(HERE / "_canary_run.py"), NUDGE_TARGET,
                    f"--nudge={NUDGE}"], capture_output=True, text=True, timeout=900)
broke = r.returncode != 0
check(f"moving ONE score by {NUDGE} in {NUDGE_TARGET} breaks the proof - a ranking change, "
      "not a crash", broke, (r.stdout or r.stderr)[-400:] if not broke else "")

print()
print(f"golden store: {P} passed, {F} failed")
sys.exit(1 if F else 0)
