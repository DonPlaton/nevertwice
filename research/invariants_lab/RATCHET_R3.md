# The complexity ratchet: it discriminates, and it will not stop talking either

**Tasks R1, R2, R3.** One gate of four fails, so the declared consequence stands: **deleted, and
this is the result.** It is the second mechanism in a row to fail on the same axis, and that
pattern is worth more than either measurement alone.

```bash
python research/invariants_lab/measure_ratchet.py --print
python tests/_test_complexity.py     # 59 contracts, including agreement with ruff
```

Artifact: `ratchet_r3.json`. Thresholds: [`PREREGISTRATION.md`](PREREGISTRATION.md) §4.
Skills used: `networkx` (R1's import graph), `test-driven-development`.

---

## The verdict

| gate | declared before the run | measured | |
|---|---|---|---|
| **R3-C1** silence | flag rate ≤ 0.05 | **0.364** [0.335, 0.394], p = 3.0 × 10⁻²⁰⁵ | **fail** |
| **R3-C2** agreement with an independent instrument | fires on > 0.50 of diffs where `ruff`'s own delta says complexity rose | **0.637** [0.588, 0.684], 239/375, p = 1.2 × 10⁻⁷ | **pass** |
| **R3-C3** discrimination | fires strictly more on `ruff`-rose than on `ruff`-flat diffs | **0.637 vs 0.200**, Fisher p = 4.1 × 10⁻⁴⁴ | **pass** |
| **R3-C4** exemption in one command | contract | one call, a reason required, refused without one | **pass** |

## The trap this measurement was built to avoid

`PREREGISTRATION.md` §4 said it before the run:

> A ratchet's positive class is *defined by its own metric*. A diff that raised cyclomatic
> complexity above a file's baseline is a positive because the metric says so. Recall against
> that definition is 1.0 by construction and measures nothing.

So no recall number appears here. What appears instead is **agreement with `ruff`** — an
implementation of McCabe in Rust, by other people, for another purpose — and **discrimination**
between the diffs `ruff` says got worse and the ones it says did not.

### The metric had to earn the right to be compared

A ratchet whose complexity number disagrees with the tools everyone else runs measures its author's taste.
So `complexity.cyclomatic()` is pinned against `ruff`'s C901 on **every module in
`nevertwice/`, every callable, exact equality** — 54 of 54 files.

Reaching that agreement took four corrections, each found by a case written to isolate a
disagreement rather than by reading the code:

| the implementation counted | the standard says |
|---|---|
| `and` / `or`, ternaries, `assert`, comprehensions | none of them is a decision |
| a `match` as one per case | one per case **beyond the first** |
| a nested `def` as belonging to itself | it counts into its parent too — a function that hides a branchy closure has not hidden it |
| — | and the *scanner* could not see a `def` inside a module-level `try:`, so four classes behind the optional-dependency idiom were never scanned at all |

That last one is the same blind spot D6 found in the checker and in the answer key. **Three
instruments, one habit: forgetting that `tree.body` is not the module.**

## What it found

**It discriminates, and strongly.** The ratchet fires on 64% of diffs where an independent tool
says complexity rose, and on 20% where it says it did not — a factor of **3.2**, Fisher
p = 4 × 10⁻⁴⁴. It is responding to complexity, not to diff size or to chance.

**And it fires on 36% of everything.** Against a declared ceiling of 5%. Over a thousand commits
drawn from all eight repositories, more than one commit in three would have produced a finding.

### The 20% on `ruff`-flat diffs is not 20% wrong

`ruff`'s C901 measures cyclomatic complexity only. The ratchet also holds **nesting, return
count and length**, and a diff can leave McCabe untouched while deepening a nest or adding a
fourth exit. So "`ruff` says flat" is not "nothing got worse", and C3's contrast is a
discrimination test on the cyclomatic axis rather than a false-positive rate. It is reported as
what it is.

That cuts the other way too, and the write-up says so: the three axes `ruff` cannot see are
exactly the ones with no independent instrument behind them, so their contribution to the 36%
is **unaudited**.

## The same failure, twice

| | recall / agreement | flag rate | ceiling | verdict |
|---|---|---|---|---|
| `blast_radius` (D5) | recall **0.910** | **0.278** | 0.05 | deleted |
| complexity ratchet (R3) | agreement **0.637** | **0.364** | 0.05 | deleted |

Two mechanisms, built independently, measured against different instruments on different
definitions of a positive — and both **find what they are looking for and cannot stay quiet**.

Neither failed for the reason the previous run's write-up predicted. `BLAST_RADIUS_PRECISION.md`
expected the problem to be that the checker's findings were *wrong*; on a corpus with positives
in it, they are substantially right and there are simply far too many of them. The binding
constraint on this idea is not accuracy. It is **silence**, and nothing measured so far addresses
it.

## Design decisions that survive the deletion

Three, and each is reusable by whatever comes next:

**Differential, not absolute.** "Complexity 47, bad" is true of code nobody is touching. The
enforceable claim is *this diff must not degrade this file against its own baseline*.

**The baseline is stored and only moves down.** Comparing *after* to *before* permits a slow
climb: +1 every commit is never a degradation against the previous commit and is a disaster
against last quarter. Storing the baseline and lowering it automatically on improvement is what
makes it a ratchet rather than a threshold.

**Slack belongs on `length` and nowhere else.** A branch is a branch, but a docstring, an
annotation or a reformat adds lines without adding anything a reader holds in their head.
Without ten lines of slack the ratchet fires on `black`, and a contract asserts it does not.

## What a next attempt must change

1. **Solve silence first, for both mechanisms at once.** Two independent detectors hit the same
   wall; a third will too. Ranking and capping delivery — the one-finding-per-diff rule
   `INVARIANT_NOTES.md` already implements — bounds what *reaches* a person, but T1 has to show
   whether a union of capped mechanisms is quiet enough to leave on.
2. **Give the other three axes an independent instrument, or drop them.** Nesting, returns and
   length carry an unknown share of the 36% and nothing outside this project can check them.
3. The metric itself is **not** what failed. It agrees exactly with an independent implementation
   on 54 of 54 modules, and that agreement is worth keeping whatever is built next.

## Note on the baseline

`radon` was named in the preregistration. PyPI returns `SSL: UNEXPECTED_EOF_WHILE_READING` from
this machine and `github.com:443` refused the fallback clone, so it could not be installed.
`ruff` 0.15.16 was already present, computes the same McCabe metric, and shares neither language
nor authors with anything here — which is the property `radon` was chosen for. The substitution
is logged in `PREREGISTRATION.md` §9 with its date, reason and expected effect, rather than made
quietly.
