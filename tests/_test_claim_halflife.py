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


#: This suite reads the repository's HISTORY, not its files, so it cannot run in a clone that
#: does not have one. A `git clone --depth 1` - which is what `actions/checkout` does by default -
#: leaves exactly one commit, and every re-derivation below then fails for want of data rather
#: than for want of correctness. Measured on a real depth-1 clone of this branch before the branch
#: was ever pushed: the battery came back 2 failed, 204 passed, and both failures were this shape.
#: CI is fixed properly (`fetch-depth: 0` in `.github/workflows/ci.yml`); this says so out loud
#: for anyone else who clones shallow, rather than reporting a defect that is not there.
SHALLOW = H._git("rev-parse", "--is-shallow-repository").strip() == "true"
if SHALLOW:
    print("\n- SKIPPED: this clone is shallow, and these checks read the register's history -")
    print("  clone with full depth (or `git fetch --unshallow`) to run them;")
    print("  CI does this with `fetch-depth: 0`, added after a depth-1 clone reddened them.")
    print("\nALL OK (skipped: shallow clone)")
    sys.exit(0)

records, head = H.lifetimes()

print("\n- the register's history is read, not assumed -")
check("every claim that ever existed has a birth", all(r["born"] for r in records.values()))
check("and there are enough of them to say anything", len(records) >= 800, str(len(records)))

print("\n- the curve is the measured one -")
#: Printed, not asserted. These bands were read at TODAY's HEAD, so restore #2 and the v2
#: registrations would move them whatever the code does - the calendar source K4 and K17 removed
#: elsewhere in this file (the auditing session, 2026-09-24). The page's numbers are asserted
#: EXACTLY below, re-derived at the artifact's own pinned commit; a band over the live register
#: added nothing but a date to fail on.
for days in (1, 7):
    s, n = H.survival(records, head, days)
    print(f"       (at today's HEAD, for information: {days}-day survival {s}/{n})")

#: A horizon longer than the register cannot be answered, and the tool must say so rather than
#: divide by zero or print a share over an empty population.
#:
#: K4 (2026-09-23, the auditor's finding): the ORIGINAL assertion here hardcoded "n30 == 0"
#: because the register was 28.5 days old when it was written. `head` moves forward with every
#: commit that touches the register, and the first claim's birth (2026-08-24 22:45:38) is fixed
#: history - the span between them only grows. At d009e47 it crossed 30.019 days, so this
#: assertion went red on EVERY future commit to the register, on q5 and on invariants/v3 alike -
#: a calendar time bomb, not a real check of `survival`'s behaviour.
#:
#: Replaced with a TIME-INDEPENDENT invariant: n30 must equal the count of records whose birth
#: is at or before head-30d, counted HERE independently of `H.survival`'s own censoring logic
#: (so a bug in censoring cannot cancel an equal bug in the reference count - two different
#: implementations of the same rule, not one checked against itself). This holds exactly the
#: same whether the register's span is under 30 days (both sides read 0, the ORIGINAL
#: assumption) or over it (both sides read whatever the register now actually has) - never
#: red on a calendar date alone.
horizon30 = dt.timedelta(days=30)
expected_n30 = sum(1 for r in records.values() if head - r["born"] >= horizon30)
s30, n30 = H.survival(records, head, 30)
check("the 30-day OBSERVED count matches an independently-counted reference (born <= "
     "head-30d), whatever the register's current age happens to be",
     n30 == expected_n30, f"n30={n30} expected={expected_n30}")
if expected_n30 == 0:
    print("       (register is still younger than 30 days: 0 observations, the original case)")
else:
    print(f"       (register has crossed 30 days: {n30} observation(s), {s30} survivor(s) - "
         "research/CLAIM_HALFLIFE.md's '30 days | 0 | 0' row is a snapshot as of `737444c` "
         "and correct AT that commit; a refresh at HEAD would show "
         f"{n30} observed, {s30} survivor at 30 days)")


def _survival_without_censoring(records: dict, days: int) -> tuple[int, int]:
    """The OLD, broken shape `H.survival` replaced: every record counts as 'observed'
    regardless of age - the exact defect censoring exists to prevent (a claim born yesterday
    cannot honestly answer "did it survive 30 days"). Reconstructed inline rather than by
    monkeypatching `H.survival` - there is no separate 'censoring' toggle inside it to flip,
    the censoring IS the function - for the mutation proof below."""
    horizon = dt.timedelta(days=days)
    survivors = observed = 0
    for rec in records.values():
        observed += 1
        if rec["died"] is None or rec["died"] - rec["born"] >= horizon:
            survivors += 1
    return survivors, observed


check("setup: at least one record is younger than 30 days (otherwise the mutation below "
     "cannot bite - censored and uncensored counts would coincide by accident)",
     expected_n30 < len(records), f"expected_n30={expected_n30} of {len(records)} records")
_, n30_uncensored = _survival_without_censoring(records, 30)
check("mutation: WITHOUT censoring, the 30-day observed count no longer matches the "
     "independent reference (would FAIL the invariant check above, by name)",
     n30_uncensored != expected_n30, f"n30_uncensored={n30_uncensored} expected={expected_n30}")

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

print("\n- the committed artifact is re-derived at the state it names, not trusted -")
#: The first version of this suite recomputed everything and checked the recomputation, and never
#: opened `research/results/claim_halflife.json` at all. The auditing session edited a survivor
#: count in that file by hand and the suite stayed ALL OK (2026-09-22): the page's evidence was
#: unguarded exactly the way a claim's evidence is not. The artifact now names the HEAD it was
#: generated at, so it can be re-derived - and a number changed by hand no longer agrees with it.
import json as _json  # noqa: E402

ART = ROOT / "research" / "results" / "claim_halflife.json"
check("the artifact the page cites is committed", ART.exists())
if ART.exists():
    art = _json.loads(ART.read_text(encoding="utf-8"))
    check("and it names the state it is a snapshot of", bool(art.get("head")), str(art.get("head")))
    at_head, head_at = H.lifetimes(art["head"])
    for days in (1, 7, 14):
        s, n = H.survival(at_head, head_at, days)
        was = art["overall"][str(days)]
        check(f"{days}-day row re-derives at {art['head'][:7]}: {s}/{n}",
              (s, n) == (was["survived"], was["observed"]),
              f"artifact says {was['survived']}/{was['observed']}")
    #: The pin must be READ, or it is decoration. Measured by the auditing session: changing
    #: `head` to a commit six back left the battery ALL OK (the curve had not moved over that
    #: stretch), and replacing `manifest_sha256` with sixty-four zeros left it ALL OK too - the
    #: field appeared in the tool that writes it and nowhere else. One line closes both: the
    #: register AT the recorded commit must hash to the recorded digest, so a wrong `head` brings
    #: a wrong manifest and a wrong digest with it. Third instance of this shape tonight, after
    #: `code_sha` in a commit message and locomo's `embed_cache.sha256`.
    import hashlib as _hashlib  # noqa: E402

    blob = H._git("show", f"{art['head']}:{H.MANIFEST_PATH}")
    digest_at_head = _hashlib.sha256(blob.encode("utf-8")).hexdigest() if blob else ""
    check("the register at the recorded commit hashes to the recorded digest",
          digest_at_head == art.get("manifest_sha256"),
          f"at {art['head'][:7]}: {digest_at_head[:16]}..., recorded {str(art.get('manifest_sha256'))[:16]}...")

    horizon = dt.timedelta(days=art["survivors"]["horizon_days"])
    again = [cid for cid, r in at_head.items()
             if head_at - r["born"] >= horizon
             and (r["died"] is None or r["died"] - r["born"] >= horizon)]
    check("and the survivor count re-derives too", len(again) == art["survivors"]["count"],
          f"{len(again)} against {art['survivors']['count']}")

    #: The split the page acts on - engine behaviour against frozen artefact - is re-derived by
    #: the same source-read rule, not trusted from the file. Every claim must land in exactly one
    #: group: a first draft classified by family name and left 387 of 869 outside both, which
    #: would have made the headline a statement about whichever families someone remembered.
    sys.path.insert(0, str(ROOT / "tools"))
    import campaign_triage as T  # noqa: E402

    kinds = {"frozen_artefact": {}, "engine_behaviour": {}}
    for cid, r in at_head.items():
        kinds["frozen_artefact" if T.triage(r["meta"])[0] == "A" else "engine_behaviour"][cid] = r
    check("every claim lands in exactly one kind",
          sum(len(v) for v in kinds.values()) == art["claims"],
          f"{sum(len(v) for v in kinds.values())} against {art['claims']}")
    for label, sub in kinds.items():
        s7, n7 = H.survival(sub, head_at, 7)
        was = art["by_kind"][label]["horizons"]["7"]
        check(f"{label}: 7-day row re-derives, {s7}/{n7}",
              (s7, n7) == (was["survived"], was["observed"]),
              f"artifact says {was['survived']}/{was['observed']}")

print("\n- what survives is a frozen artefact, which is the finding rather than the rate -")
#: Asked of the SNAPSHOT the page is addressed to (`at_head`/`head_at`, the artifact's own pinned
#: commit), not of today's HEAD. At the live HEAD the two-week set changes with the calendar: a
#: frontier claim born 2026-09-10 06:26 crossed fourteen days at 2026-09-24 06:26 and turned this
#: check red for every commit made after that minute, whatever it contained (found on stands/ports
#: 316b518, 2026-09-24) - the same shape as the thirty-day check K4 fixed. The page's finding is a
#: statement about its snapshot; the live curve is the next campaign's page, not this assertion.
horizon = dt.timedelta(days=14)
_recs, _at = (at_head, head_at) if ART.exists() else (records, head)
long = [cid for cid, r in _recs.items()
        if _at - r["born"] >= horizon and (r["died"] is None or r["died"] - r["born"] >= horizon)]
_live = {cid.split(".")[0] for cid, r in records.items()
         if head - r["born"] >= horizon and (r["died"] is None or r["died"] - r["born"] >= horizon)}
print(f"       (at today's HEAD, for information: two-week families {sorted(_live)})")
fams = {cid.split(".")[0] for cid in long}
check("the two-week survivors are the embedder and the simulation", fams <= {"embed", "longitudinal"},
      str(sorted(fams)))
check("no engine family is among them - supersession, asof, abstention, guards, code_sessions",
      not (fams & {"supersession", "supersession_implicit", "asof", "abstention", "guards",
                   "code_sessions", "frontier", "token_ab"}),
      str(sorted(fams)))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
