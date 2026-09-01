# G1 — NO-GO, and every reason for it

**Task G1, revised 2026-08-29 after Phase V completed.** Written to be attacked. Each gate, its
threshold as declared *before* the run that tested it, its measured value, and what the pair
obliges. Nothing here is a decision the owner has delegated: G1 recommends, and `.loop/REPORT.md`
lists the commands only the owner runs.

> **What this revision changes.** The first version of this page recorded **G-B as UNEVALUABLE**
> because DNS resolved nothing on this machine and no held-out corpus existed. The network came
> back, `H3` ran, and the eleven-step chain finished unattended at 04:53. **G-B is now evaluated.**
> Its answer moves one mechanism from *deleted* to *passing* and deletes three more. The verdict is
> still NO-GO, and the reason is now a different one — which is the point of measuring.

Skills: `peer-review` (claim → evidence, and *not reported* kept distinct from *reported negative*),
`scientific-writing`, `statistical-power`.

---

## The verdict

> **NO-GO, and now on evidence rather than on a missing measurement.** Two of three gates pass.
> The one that does not is **G-C — the gate that encodes the owner's rule**. It has now been
> re-run on the held-out corpus at 150 benefit and 150 harm trials, with 87 usable pairs against
> the 37 the preregistration required, and it still does not resolve: 5 fixed, 2 broken,
> p = 0.453. `GOAL-SHIP.md` §1 says nothing ships unless all three pass. **Do not push.**

| gate | declared | measured | verdict |
|---|---|---|---|
| **G-A** every surviving mechanism is finished | Phase F | four named pieces, four built, each with its cost | **PASS** |
| **G-B** out-of-sample numbers at declared thresholds | Phase V on ≥ 25 held-out repositories | 27 repositories, 6,868 mutants, 2,623 source commits; **one mechanism passes all three of its gates, three fail theirs** | **PASS**, for what survives |
| **G-C** the memory measurably works better, harm bounded | net benefit p < 0.05 **and** harm ≤ 0.05 | **out of sample**: harm **0.013 PASS**; benefit **p = 0.453**, 5 fixed against 2 broken on 87 usable paired trials | **NOT MET** |

## G-B — evaluated, and what it decided

Full numbers in [`HELDOUT_V.md`](HELDOUT_V.md); provenance of each artifact in
[`PROVENANCE_V.md`](PROVENANCE_V.md), derived from the data because three of them are mislabelled.

| mechanism | gate | out of sample | |
|---|---|---|---|
| `blast_radius` under `decidable-only` | recall ≥ 0.45 | **0.597** [0.578, 0.616] | pass |
| | flag rate ≤ 0.05 | **0.016** | pass |
| | beats `git grep`, matched | 0.597 vs 0.344, p = 5.9 × 10⁻⁶⁹ | pass |
| complexity ratchet | silence ≤ 0.05 | **0.522** | **fail → deleted** |
| `scale`, static half | recall ≥ 0.30 on ≥ 20 mined fixes | **0.059** on **152** | **fail → deleted** |
| the union | flag rate ≤ 0.05 | **0.191** | **fail** |

**One mechanism survived a gate it was not built against.** That has not happened before in this
project. Everything measured against a silence ceiling on a corpus it *was* built against failed
that ceiling again, and by more.

### Why the distinction this page kept is the reason the revision is possible

The first version recorded G-B as **UNEVALUABLE** rather than failed, and refused to let the
absence of a corpus read as a negative result. **Unevaluable is not the same as failed**, and the
proof is what happened next: the network returned, the same thresholds were applied unchanged, and
the gate came back *passing* for the one mechanism and *failing* for three. Had the first version
written "G-B: FAIL" for tidiness, `decidable-only` would now be deleted on the strength of a DNS
outage.

The same distinction is still live in two places. `power_ship_heldout.json` has **undecided**
provenance rather than an assumed one — undecided is not the same as wrong, and it was not quietly
inherited from its input. G-C below has since crossed that line in the other direction: it was *not measured* in sample and
is now **measured and not met** out of sample, which is a stronger and more useful thing to be.

### The finding G-B produced that no gate asked for

**Recall generalises; precision and silence do not.** `blast_radius` recall moved 0.9101 → 0.9091
and `decidable-only` recall 0.600 → 0.597 — three decimal places, on repositories chosen to be as
unlike the development set as the criteria allowed. Over the same move, precision fell 31% and
every flag rate rose 43–86%.

The between-block model on 27 blocks says why, and the answer is single: **precision tracks
repository size** (Spearman ρ = −0.620, p = 0.00056 against size on disk). The apparent domain
effect — `async` blocks at median precision 0.796 against `data` blocks at 0.220, Kruskal–Wallis
p = 0.025 — **does not survive residualising on log₁₀(size)**: H falls to 4.92, p = 0.43. Domain
was size wearing a label, and the check that found this was written before its result was seen.

**So an in-sample precision figure is a statement about the size distribution of its corpus.** The
development eight are small to middling; 0.396 was never a property of the checker. Any project
quoting a precision number measured on repositories it chose should read that sentence twice.

## G-A — pass, and what "finished" turned out to mean

Four pieces were named as missing by `invariants/v2`. All four were built, and building each one
produced a number rather than a feature.

| piece | built | its cost, measured |
|---|---|---|
| abstention in `blast_radius` | `abstain.py`, six nested policies declared before the run | flag rate 0.278 → **0.010** in sample, **0.016** out; recall 0.910 → **0.600** / **0.597** |
| a declared surface | `surface.py`, five surfaces × two policies | the declared surface **does not exist in found history**: `__all__` names the changed symbol for 18 of 859 |
| independent instruments for the ratchet's three axes | `PLR1702`, `PLR0911`, `PLR0915` | **0 disagreements over 3,685 callables**, after four defects were found and fixed |
| X1's cold-start experiment | `measure_declared_axis.py`, dynamic judge | base rate **0.013**; refused at 750 required trials against a 400 budget |

**G-A passes on the letter and it is worth reading what it cost.** Two of the four "finishings"
established that the mechanism cannot do what was hoped, and Phase V confirmed both out of sample.
A gate that says *"the work is finished"* is not a gate that says the work succeeded.

## G-C — measured out of sample, and still not met

Re-run on the held-out corpus at **150 benefit and 150 harm trials** over a 514-task pool. Full
page: [`ENDTOEND_HELDOUT.md`](ENDTOEND_HELDOUT.md). The in-sample stand is kept below it because
the pair is the finding.

| | declared | out of sample | in sample |
|---|---|---|---|
| net benefit | McNemar p < 0.05 | **p = 0.453**; 5 fixed, 2 broken, 7 discordant | p = 1; 2 fixed, 1 broken, 3 discordant |
| harm | ≤ 0.05 | **0.013** (3 of 234) | 0.010 (1 of 99) |
| usable paired trials | ≥ 37 required | **87** | 46 |
| base rate | measured, not assumed | 0.276 [0.193, 0.378] | 0.174 [0.09, 0.31] |

**The off arm failed 24 times of 87; the on arm 21.** Three fewer failures out of eighty-seven,
with the mechanism firing on 57 of 150 trials. Five fixed against two broken is not a
one-directional effect: delivering a true finding sometimes makes an agent remove a definition the
file needed.

**Harm passes and stopped being zero.** In sample the mechanism fired on **0 of 53** correct trees;
out of sample on **2 of 150**, and one of those firings caused a repair to correct code. Nothing
broke. The claim that it is *perfectly* silent on correct code does not survive the move, which is
the same direction every other Phase V number moved.

**The limitation, stated with the verdict rather than after it.** 92 of the 300 attempts across
both arms failed with `harness: TimeoutError` — the 30B model was cycling in and out of a GPU at
98% of its memory. The run is **not underpowered**: 87 usable exceeds the 37 required. But a
timeout correlates with larger inputs, so the surviving trials are biased toward smaller files, and
a rerun on an unloaded GPU is the cheap way to overturn this if anyone wants to.

### The in-sample stand, kept for the contrast

53 benefit trials and 53 harm trials on `corpus_dev`.

### The harm half — passes, on the cleanest number in the run

| | declared | measured |
|---|---|---|
| harm rate | ≤ **0.05** | **0.010** (1 of 99) |
| the mechanism fires on **correct** code | — | **0 of 53** |
| a false flag causing an agent to change correct code | — | **0** |

`blast_radius` under `decidable-only` produced **no findings at all** on 53 files where every call
already binds. **The harm arm is empty because the mechanism never spoke.** Phase V raises the
confidence in that number rather than lowering it: the same policy kept 67 findings out of a
1,000-commit silence pool on twenty-seven unfamiliar repositories, and zero of them undecidable.

### The benefit half — the direction is right and the evidence is not there

| | |
|---|---|
| fixed | **2** |
| broken | **1** |
| ignored | 17 |
| discordant pairs | **3** |
| exact McNemar | **p = 1** |

**Three discordant pairs cannot reject at α = 0.05 under any arrangement** — an exact sign test
needs six one way. Underpowered, by a margin [`ENDTOEND_E.md`](ENDTOEND_E.md) §2 computed *before
the probe*: with 53 tasks and an assumed fix rate of 0.60 the stand needs a base rate of at least
**6 / (53 × 0.60) = 0.189**, and the measured base rate is **0.174** [0.09, 0.31].

**This is not evidence of no effect. It is the absence of a measurement**, and the distinction is
the whole reason this page separates *not reported* from *reported negative*.

## What would change the verdict, per gate

| gate | what would change it |
|---|---|
| **G-C benefit** | **done, and it did not change the answer.** The stand was re-run out of sample on a 514-task pool: 87 usable pairs against the 37 required, base rate 0.276, and the off arm failed 24 times against the on arm's 21. See [`ENDTOEND_HELDOUT.md`](ENDTOEND_HELDOUT.md). What would change it now is a rerun on an unloaded GPU: 92 of 300 attempts timed out while the model cycled in and out of a full card, and a timeout correlates with the larger inputs, so the surviving trials are biased toward smaller files |
| **G-C benefit, alternative** | harder tasks. A base rate of 0.30 needs 33 trials and even the old pool would carry it |
| **G-A for `scale`** | the catalogue of shapes. Phase V settled that this is not a detail: 152 real quadratic fixes, 2 of a shape it recognises, and **50.7% of its hits fire on the repaired tree as well** |
| **the union** | it has never been measured with `decidable-only` in it. When T1 ran, `blast_radius` was deleted; out of sample it is the only survivor. The union that matters has not been run |

**The recommended next measurement is one thing, not four:** re-run the end-to-end stand on the
held-out corpus with `decidable-only`. It is the only mechanism that passed G-B, the pool that
starved G-C now exists, and a single run would produce the first out-of-sample end-to-end number
this project has ever had.

## What this run establishes anyway

A NO-GO is not an absence of result. Seven things are now measured that were not:

1. **Abstention works, and it survives out of sample.** 0.278 → 0.010 in sample, 0.016 out, with
   the margin over `git grep` at +0.304 and +0.253 respectively — 1,053 commits the checker caught
   and grep did not, against 396 the other way. A regex cannot abstain; its only quiet is a higher
   threshold, and a threshold discards true and false alike.
2. **It is not general.** Abstention is available to a checker that *cannot decide* and not to one
   that can. The ratchet has nothing to abstain from and moved 0.358 → 0.310 → **0.522**.
3. **Recall is the number that travels. Precision and silence are not.** Three decimals against
   31% and 43–86%.
4. **Repository size governs precision**, and the domain effect that looked real dissolves under a
   size control.
5. **`scale`'s static half recognises one of six shapes**, established three times by independent
   routes and finally at n = 152 rather than n = 7.
6. **A gate cleared by one thousandth was never cleared.** The three-heuristic union passed in
   sample at 0.049 against a 0.05 ceiling and reads 0.055 out of sample.
7. **The mechanism is silent on correct code**, 0 of 53 end to end, and 0 undecidable findings kept
   on 1,000 unfamiliar commits.

## The limits of everything above, stated once

- **G-C is still in sample.** Phase V closed G-B; it did not touch the end-to-end stand. Every
  benefit and harm number here was measured on the eight repositories the mechanisms were tuned on.
- **One model, one task shape, one language.** G-C used `qwen3-coder:30b` on one kind of task —
  repair a stale caller after an arity change — in Python.
- **The memory store is empty in both arms.** E measures the mechanism's *marginal* contribution,
  which is the contrast the owner's rule names but is not Nevertwice-with-history against
  Nevertwice-without.
- **Recall 0.597 is recall among breakages a static reader can prove.** The answer key's positive
  class is exactly the two shapes `decidable-only` emits. What share of all real breakages that is,
  nothing here measures.
- **Three Phase V artifacts label themselves in sample and are not.** The data is out of sample and
  the labels are hardcoded literals; see `PROVENANCE_V.md`. The frozen modules were not edited to
  fix them.
- **Eight instruments have now been checked against something they did not write, and every check
  found something.** Assume a ninth.
