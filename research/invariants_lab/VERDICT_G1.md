# G1 — NO-GO, and every reason for it

**Task G1.** Written to be attacked. Each gate, its threshold as declared *before* the run that
tested it, its measured value, and what the pair obliges. Nothing here is a decision the owner has
delegated: G1 recommends, and `.loop/REPORT.md` lists the commands only the owner runs.

Skills: `peer-review` (claim → evidence, and *not reported* kept distinct from *reported negative*),
`scientific-writing`.

---

## The verdict

> **NO-GO.** Not because a mechanism was measured and failed, but because **one gate could not be
> evaluated at all** and **another is inconclusive rather than positive**. `GOAL-SHIP.md` §1 says
> nothing ships unless all three pass. Two did not.

| gate | declared | measured | verdict |
|---|---|---|---|
| **G-A** every surviving mechanism is finished — its named missing piece built, tested, its cost measured | Phase F | four named pieces, four built, each with its cost | **PASS** |
| **G-B** out-of-sample numbers at declared thresholds | Phase V on ≥ 25 held-out repositories | **no corpus exists** | **UNEVALUABLE** |
| **G-C** the memory measurably works better, harm bounded | net benefit p < 0.05 **and** harm ≤ 0.05 | harm **0.010 PASS**; benefit **p = 1, inconclusive** | **NOT MET** |

## G-A — pass, and what "finished" turned out to mean

Four pieces were named as missing by `invariants/v2`. All four were built, and building each one
produced a number rather than a feature.

| piece | built | its cost, measured |
|---|---|---|
| abstention in `blast_radius` | `abstain.py`, six nested policies declared before the run | flag rate 0.278 → **0.010**; recall 0.910 → **0.600** |
| a declared surface | `surface.py`, five surfaces × two policies | the declared surface **does not exist in found history**: `__all__` names the changed symbol for 18 of 859 |
| independent instruments for the ratchet's three axes | `PLR1702`, `PLR0911`, `PLR0915` | **0 disagreements over 3,685 callables**, after four defects were found and fixed |
| X1's cold-start experiment | `measure_declared_axis.py`, dynamic judge | base rate **0.013**; refused at 750 required trials against a 400 budget |

**G-A passes on the letter and it is worth reading what it cost.** Two of the four "finishings"
established that the mechanism cannot do what was hoped: F2 found the declared surface absent, and
F4 and F5 together found that `scale`'s static half recognises **one of six** ordinary ways to
write the fault it exists to catch. A gate that says *"the work is finished"* is not a gate that
says the work succeeded, and G-A is the only one of the three this run passes.

## G-B — unevaluable, and the distinction matters

**No held-out corpus exists.** `gethostbyname` fails for `github.com`, `pypi.org` and
`example.com` alike on this machine, while raw TCP to a GitHub address connects; the local proxy
resolves the name, opens a tunnel, and receives no TLS handshake back. Fixing that means
reconfiguring the owner's machine, which `GOAL.md` §2 forbids. `RATCHET_R3.md` recorded the same
two failures one day earlier, so it is a property of this machine and not a blip. Full record:
[`HELDOUT_H3_BLOCKED.md`](HELDOUT_H3_BLOCKED.md).

**"Unevaluable" is not "failed", and neither is it "passed".** Every threshold in
[`PREREGISTRATION-SHIP.md`](PREREGISTRATION-SHIP.md) §3 stands untested. The sentence
`GOAL-SHIP.md` §0.3 wrote as the problem this run existed to solve — *every published number is in
sample* — **is still true at the end of the run**. That is the single most important fact in this
document, and no result below softens it.

What was preserved rather than lost: thirty repositories and a falsifiable per-block prediction,
committed **before** the block was discovered; a 29-file code freeze with hashes a test recomputes;
and `--corpus heldout` on every harness, so Phase H is four commands rather than a code change.

## G-C — harm passes, benefit is inconclusive

53 benefit trials and 53 harm trials on `corpus_dev`. **In sample.**

### The harm half — passes, on the cleanest number in the run

| | declared | measured |
|---|---|---|
| harm rate = (`broken` + false-flag repair) / trials | ≤ **0.05** | **0.010** (1 of 99) |
| the mechanism fires on **correct** code | — | **0 of 53** |
| a false flag causing an agent to change correct code | — | **0** |

`blast_radius` under `decidable-only` produced **no findings at all** on 53 files where every call
already binds. F1 measured that silence on a pool of commits; this is the first observation of it
on files an agent was about to edit. **The harm arm is empty because the mechanism never spoke.**

### The benefit half — the direction is right and the evidence is not there

| | |
|---|---|
| fixed | **2** |
| broken | **1** |
| ignored | 17 |
| discordant pairs | **3** |
| exact McNemar | **p = 1** |

**Three discordant pairs cannot reject at α = 0.05 under any arrangement** — an exact sign test
needs six one way. This is an underpowered run, and it is underpowered by a margin that
[`ENDTOEND_E.md`](ENDTOEND_E.md) §2 computed **before the probe**:

> With 53 tasks and an assumed fix rate of 0.60, the stand needs a base rate of at least
> **6 / (53 × 0.60) = 0.189**. Below that, using every task in the pool is still not enough.

**The measured base rate is 0.174** [0.09, 0.31]. The prediction written before the data was that
the stand would fall short if the base rate landed below 0.189, and it landed at 0.174.

## What would change the verdict, per gate

Concrete enough to be checked, and each is a thing somebody could do rather than a hope.

| gate | what would change it |
|---|---|
| **G-B** | one working DNS resolution. `clone_heldout.py --plan` is verified, the manifest and predictions are committed, and the four commands are in `HELDOUT_H3_BLOCKED.md` §3. Nothing else is needed |
| **G-C benefit** | a task pool large enough for the measured base rate: **58 usable trials** at 0.174, against 46. Raising the 400-line file cap widens the pool and trades context truncation for sample size; the held-out corpus would widen it far more |
| **G-C benefit, alternative** | harder tasks. A base rate of 0.30 needs 33 trials and the current pool would carry it. The tasks are declared in `ENDTOEND_E.md` §2 and were chosen before any of this was known |
| **G-A for `scale`** | the catalogue of shapes. Six are pinned in `tests/_test_scale.py`; the detector sees one |

## What this run establishes anyway

A NO-GO is not an absence of result. Five things are now measured that were not:

1. **Abstention works, and it is the only lever that did.** 0.278 → 0.010 on the flag rate, and
   the margin over `git grep` **doubles** as the policy tightens (+0.084 → +0.304,
   p = 1.1 × 10⁻¹⁴). A regex cannot abstain.
2. **It is not general.** The ratchet's four axes now all agree exactly with instruments nobody
   here wrote, and its flag rate moved 0.358 → 0.310. *Abstention is available to a checker that
   cannot decide and not to one that can.*
3. **The declared surface that makes `scale` quiet does not exist in found history** — 18 of 859.
4. **`scale`'s static half recognises one of six shapes**, established twice by independent
   routes: 0 of 7 real maintainer fixes, and 0 of 2 model-written quadratics.
5. **The mechanism is silent on correct code**, 0 of 53, end to end.

## The limits of everything above, stated once

- **In sample.** Every number in this document was measured on the eight repositories the
  mechanisms were tuned on. No claim here transfers to a ninth without Phase V.
- **One model, one task shape, one language.** G-C used `qwen3-coder:30b` on one kind of task —
  repair a stale caller after an arity change — in Python.
- **The memory store is empty in both arms.** E measures the mechanism's *marginal* contribution,
  which is the contrast the owner's rule names but is not the same as Nevertwice-with-history
  against Nevertwice-without.
- **One judge defect, found and not fixed mid-run.** It cost 1 task of 53, excluded from both arms.
  `ENDTOEND_E.md` §11.
- **Seven instruments have now been checked against something they did not write, and every check
  found something.** Assume an eighth.
