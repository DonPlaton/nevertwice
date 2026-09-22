#!/usr/bin/env python3
"""How long a claim stays live - and the answer is the project's shape, not a curiosity.

The council's fifth point: `843 withdrawn : 18 live` is honest and tells the reader nothing,
because nothing says whether that is normal. `tools/claim_halflife.py` reads the register's own
git history (160 commits, 28.5 days) and answers it:

    1 day      214 / 869   0.246
    7 days      27 / 757   0.036
    14 days     14 / 272   0.051
    30 days      0 /   0     -     the register is 28.5 days old and cannot be asked

**A registered claim has about one chance in four of surviving its first day.** And the fourteen
that reached two weeks are thirteen embedder claims and one seeded simulation - every one of them
about a FROZEN artefact. No claim about engine behaviour has ever reached that horizon, because
the register withdraws a claim when the code it closes over changes, and the engine changes daily.
That is a structural fact about the design, not an accident of a bad month, and it is the honest
answer to "is 843:18 normal": for this design, yes, and here is why.

Two traps this suite keeps closed:

* **The horizons are not one cohort.** Each has its own denominator - only claims old enough to
  ask - so the share at 14 days (0.051) exceeding the share at 7 (0.036) is a population
  difference, not a resurrection. A reader who takes them for one curve stops trusting the table.
* **A claim too young to ask is not a survivor.** Counting it as one would push every share up
  and would make the number grow simply by registering claims.

    python tests/_test_claim_halflife.py
"""
import _env_guard  # noqa: F401
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import claim_halflife as H  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


records, head = H.lifetimes()

print("\n- the register's history is read, not assumed -")
check("every claim that ever existed has a birth", all(r["born"] for r in records.values()))
check("and there are enough of them to say anything", len(records) >= 800, str(len(records)))

print("\n- the curve is the measured one -")
for days, floor, ceil in ((1, 0.15, 0.35), (7, 0.01, 0.10)):
    s, n = H.survival(records, head, days)
    share = s / n if n else None
    check(f"{days}-day survival is between {floor} and {ceil} ({s}/{n})",
          share is not None and floor <= share <= ceil, str(share))

#: A horizon longer than the register cannot be answered, and the tool must say so rather than
#: divide by zero or print a share over an empty population.
s30, n30 = H.survival(records, head, 30)
check("thirty days has no observations, because the register is younger than that", n30 == 0,
      f"{s30}/{n30}")

print("\n- censoring is real: a claim too young to ask is not counted as a survivor -")
young = {"probe.too.young": {"family": "probe", "born": head - dt.timedelta(hours=2), "died": None}}
s, n = H.survival(young, head, 7)
check("a two-hour-old live claim adds nothing to the seven-day figure", (s, n) == (0, 0),
      f"{s}/{n}")
old_live = {"probe.old": {"family": "probe", "born": head - dt.timedelta(days=9), "died": None}}
check("a nine-day-old live claim counts as a seven-day survivor",
      H.survival(old_live, head, 7) == (1, 1))
died_early = {"probe.short": {"family": "probe", "born": head - dt.timedelta(days=9),
                              "died": head - dt.timedelta(days=8)}}
check("and one that died on day one counts as observed but not survived",
      H.survival(died_early, head, 7) == (0, 1))

print("\n- what survives is a frozen artefact, which is the finding rather than the rate -")
horizon = dt.timedelta(days=14)
long = [cid for cid, r in records.items()
        if head - r["born"] >= horizon and (r["died"] is None or r["died"] - r["born"] >= horizon)]
fams = {cid.split(".")[0] for cid in long}
check("the two-week survivors are the embedder and the simulation", fams <= {"embed", "longitudinal"},
      str(sorted(fams)))
check("no engine family is among them - supersession, asof, abstention, guards, code_sessions",
      not (fams & {"supersession", "supersession_implicit", "asof", "abstention", "guards",
                   "code_sessions", "frontier", "token_ab"}),
      str(sorted(fams)))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
