#!/usr/bin/env python3
"""Two agents, one store: measured rather than assumed, because tonight was an instance of it.

The council's second point was that the multi-agent model is undefined - the system is built for
one agent per store while orchestrating several agents over one repository is now ordinary. The
point deserved measuring rather than agreeing with, because the store is not in fact unguarded:
`_engine_store.acquire_lock` is a cross-process lock with stale reclamation by pid, writes publish
through `store_state.write_atomic` (temp file + `os.replace`), and the index runs WAL with a busy
timeout. What nobody had done is put several processes on one vault and look.

So this does. Four processes, one vault, each writing a typed note through the production writer
under the production lock, started as close to simultaneously as the OS allows:

    every note survives            no writer's file is lost to another's publish
    the lock is exclusive          no two processes are inside the critical section at once
    nothing is left behind         no `.tmp`, no `.lock`, no `.stale.*` after the last exit

The third is not decoration. A leftover `.lock` wedges every future writer for `LOCK_STALE_S`, and
a leftover `.tmp` is the file the next `os.replace` races against - the exact failure the retry
loop in `write_atomic` was written for.

All three hold on this box today, so the suite would be worth little if it could not go red.
Measured, 2026-09-22:

    lock replaced by a plain write (no O_EXCL, so every process "holds" it at once)
        -> "and still its own by the time it released  ([{pid 8832, after 44112}])"
        -> "no .lock survives the last exit  (['.lock'])"
    lock removed entirely
        -> three named failures, starting with the writers that could not report a holder

The first is the one that matters: it says another process was inside the critical section while
this one was, which is exactly the property the lock exists for and the only one a green line here
should be read as asserting.

What this does NOT measure, said plainly: two agents writing CONTRADICTORY facts in the same
window. Supersession decides per session, so the second writer's note does not see the first's
inside the same window; whether that is right is a design question for the owner and not a defect
this can fail on. It is named here so the gap is a decision rather than an oversight.

    python tests/_test_two_agents_one_vault.py
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

import _env_guard  # noqa: F401,E402

FAILS = 0
WRITERS = 4


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


#: One writer, as a separate PROCESS - the only way to test a cross-process lock. It reports what
#: it saw of the lock while holding it, so exclusivity is measured from inside the critical
#: section rather than inferred from the absence of damage.
WRITER = r'''
import json, os, sys, time
sys.path.insert(0, r"{tests}")
sys.path.insert(0, r"{pkg}")
import _env_guard  # noqa: F401
import memory_hook as m

vault = r"{vault}"
n = int(sys.argv[1])
m.PROJECTS_ROOT = os.path.join(vault, "transcripts")
m._rebase_vault(vault)

got = m.acquire_lock(60)
result = {{"writer": n, "pid": os.getpid(), "locked": bool(got)}}
if got:
    try:
        # Seen from INSIDE the critical section: the lock file must name this process.
        try:
            result["holder_seen"] = (m.VAULT / ".lock").read_text().strip()
        except OSError as e:
            result["holder_seen"] = f"unreadable: {{type(e).__name__}}"
        time.sleep(0.05)      # widen the window so a broken lock actually overlaps
        item = {{"title": f"concurrent writer {{n}}",
                "description": f"written by process {{os.getpid()}}",
                "prevention": "measured, not assumed"}}
        result["path"] = m.write_typed_note("Mistakes", item, "concurrency", "2026-09-22",
                                            ["test"], "mistake")
        result["holder_after"] = (m.VAULT / ".lock").read_text().strip()
    finally:
        m.release_lock()
print("RESULT " + json.dumps(result))
'''

with tempfile.TemporaryDirectory(prefix="nw_two_agents_") as td:
    vault = Path(td) / "vault"
    (vault / "transcripts").mkdir(parents=True, exist_ok=True)
    script = Path(td) / "writer.py"
    script.write_text(WRITER.format(tests=HERE, pkg=ROOT / "nevertwice", vault=vault),
                      encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    procs = [subprocess.Popen([sys.executable, str(script), str(i)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace", env=env)
             for i in range(WRITERS)]
    results, errors = [], []
    for p in procs:
        out, err = p.communicate(timeout=300)
        line = next((ln for ln in out.splitlines() if ln.startswith("RESULT ")), None)
        if line:
            results.append(json.loads(line[len("RESULT "):]))
        else:
            errors.append((err or out)[-300:].replace("\n", " | "))

    print(f"\n- {WRITERS} processes, one vault, the production lock and the production writer -")
    check("every writer ran to completion", not errors, " || ".join(errors[:2]))
    check(f"all {WRITERS} acquired the lock within the timeout",
          len(results) == WRITERS and all(r["locked"] for r in results),
          json.dumps([r.get("locked") for r in results]))

    print("\n- the lock was exclusive, seen from inside the critical section -")
    mismatched = [r for r in results if str(r.get("holder_seen")) != str(r.get("pid"))]
    check("each holder found its own pid in the lock file", not mismatched,
          json.dumps([{"pid": r["pid"], "saw": r.get("holder_seen")} for r in mismatched][:2]))
    stolen = [r for r in results if r.get("holder_after") not in (None, str(r.get("pid")))]
    check("and still its own by the time it released", not stolen,
          json.dumps([{"pid": r["pid"], "after": r.get("holder_after")} for r in stolen][:2]))

    print("\n- no writer's note was lost to another writer's publish -")
    notes = sorted((vault / "Mistakes").glob("*.md")) if (vault / "Mistakes").exists() else []
    titles = {p.read_text(encoding="utf-8", errors="replace").count("concurrent writer")
              for p in notes}
    check(f"{WRITERS} notes on disk", len(notes) == WRITERS,
          f"{len(notes)}: {[p.name for p in notes]}")
    check("each note carries a writer's title", titles <= {1} and notes, str(titles))
    #: `write_typed_note` returns the note's STEM, not a path - so the check resolves it rather
    #: than calling `Path.exists()` on a name and reporting a loss that is really a misreading.
    stems = {r.get("path") for r in results if r.get("path")}
    missing = sorted(s for s in stems if s and not (vault / "Mistakes" / f"{s}.md").exists())
    check("and every writer's own stem resolves to a file", not missing, str(missing)[:200])
    check(f"the {WRITERS} stems are distinct, so nobody overwrote a neighbour",
          len(stems) == WRITERS, str(len(stems)))

    print("\n- nothing is left behind to wedge the next writer -")
    leftovers = [str(p.relative_to(vault)) for p in vault.rglob("*")
                 if p.name == ".lock" or p.suffix == ".tmp" or ".stale." in p.name]
    check("no .lock, .tmp or .stale.* survives the last exit", not leftovers, str(leftovers[:4]))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
