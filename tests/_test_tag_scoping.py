#!/usr/bin/env python3
"""An extraction is grounded on ITS OWN project's tag vocabulary.

collect_existing_tags scanned the whole vault, so a batch run handed one project the
signature tags of whichever project held the most notes. Verified delta in the 2026-09
review: nine gears_experiments notes — an ML architecture project with nothing quantum in
it — went from ["architecture", "configuration", "qa"] to ["architecture",
"configuration", "quantum_computing"]. They then surfaced for the other project's
queries while no longer matching their own.
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# a loud neighbour and a quiet project with a vocabulary of its own
m._TAG_COUNTS = {"quantum_computing": 400, "circuits": 300, "qa": 9, "testing": 8,
                 "architecture": 7, "configuration": 6, "flops": 5}
m._TAG_COUNTS_BY_PROJECT = {
    "quantum_prism": {"quantum_computing": 400, "circuits": 300},
    "gears": {"qa": 9, "testing": 8, "architecture": 7, "configuration": 6, "flops": 5},
    "newbie": {"onlytag": 3},
}

print("\n- the global vocabulary is dominated by the loudest project -")
glob = m.collect_existing_tags()
check("the neighbour's signature tag leads globally", glob[0] == "quantum_computing", str(glob[:3]))

print("\n- a scoped call returns the project's OWN tags -")
gears = m.collect_existing_tags(project="gears")
check("the neighbour's tag is absent", "quantum_computing" not in gears, str(gears))
check("its own tags are present", {"qa", "testing", "architecture"} <= set(gears), str(gears))

print("\n- a thin project is padded, own tags first, never left empty -")
new = m.collect_existing_tags(project="newbie")
check("the thin project is not left with nothing", len(new) > 1, str(new))
check("its own tag comes first", new[0] == "onlytag", str(new[:3]))
check("padding comes from the global vocabulary", "quantum_computing" in new, str(new))

print("\n- an unknown project degrades to the global vocabulary -")
unk = m.collect_existing_tags(project="does-not-exist")
check("an unknown project still gets grounding", len(unk) > 0)
check("and it is the global one", unk[0] == "quantum_computing", str(unk[:2]))

print("\n- the cap still holds -")
check("scoped results respect top_k",
      len(m.collect_existing_tags(project="newbie", top_k=3)) <= 3)

m._TAG_COUNTS, m._TAG_COUNTS_BY_PROJECT = None, {}
print(f"\ntag scoping: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
