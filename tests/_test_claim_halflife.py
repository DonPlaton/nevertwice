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
