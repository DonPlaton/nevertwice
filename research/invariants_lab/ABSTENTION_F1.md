# F1 — abstention: the ladder, declared before the run

<!-- withdrawn-banner -->
> **Withdrawn: figures on this page must not be quoted.** They were retracted in 2026-08
> and remain here because deleting a result one was wrong about destroys the record of
> having been wrong. The design, the method and the caveats stand; the numbers do not.
> Each figure's own reason is in
> [`research/evidence_manifest.json`](../evidence_manifest.json), and
> `python tools/check_freshness.py --list-stale` lists every one.

**Task F1.** `BLAST_RADIUS_D5.md` closed with three things a next attempt should carry, and the
third names this experiment: *"emitting only decidable findings is the untried route, and it is a
different filter from the one tried here — abstention, not compatibility. Its cost in recall is
unmeasured, and this corpus can measure it."*

This page is the half that has to exist **before** the measurement: the ladder of policies, the
decision rule, and — first — what the measurement is allowed to conclude, because one of the three
obvious numbers is circular by construction and saying so afterwards is worth nothing.

Skills: `scientific-critical-thinking` (§1), `test-driven-development` (§3),
`experimental-design` (§2, the ladder is a one-dimensional family on purpose).

---

## 1. What this experiment can and cannot claim

The answer key decides a finding is true with an oracle (`measure_blast_radius.is_true_finding`)
that returns `None` — *undecidable* — in six situations: the qualname is not among the contract
changes; the file cannot be read; a **removed method** is reached through an arbitrary receiver; the
change reason is `kind`; the new definition is not a function; or there is no non-star call site at
that line.

An abstention rule that declines to emit in those same situations is **a second implementation of
the oracle's own rule**. It follows immediately, and without any evidence about the world, that:

| number | circular? | why |
|---|---|---|
| **precision** after abstention | **yes** | measures agreement between two implementations of one rule. Reported as a consistency check, never as a gate result. A *disagreement* would be informative — it would mean one of the two is wrong — and is the only thing this column is read for. |
| **recall** after abstention | **no** | the positive class is planted by an independent generator and verified against CPython's `Signature.bind` 868 times of 868. Whether the checker still names the mutated symbol is decided by the mutant record. A rule that abstains too hard loses recall, and the loss is real. |
| **silence-pool flag rate** | **no** | "does the checker say anything at all on a commit with no planted defect", counted per commit. The oracle is not consulted to compute it. |

**So F1 publishes a (recall, flag rate) curve and nothing else as evidence.** That is exactly the
pair `GOAL-SHIP.md` F1 asks for, and the restriction is not a limitation discovered afterwards —
it is why the curve is the deliverable.

The same reasoning retires a temptation: an abstention rule tuned until precision looks good would
be tuned against the oracle, and the resulting number would be a measurement of nothing. **No
policy on the ladder may consult the answer key**, and `tests/_test_abstention.py` asserts it
statically — `abstain.py` may not import `mutate`, `verify_mutants` or any measurement harness.

## 2. The ladder — nested, ordered, and fixed before the run

Six policies, each adding one rule to the previous. Nesting matters for two reasons: the family is
one-dimensional, so naming the best is a choice among six rather than a search over 2⁶ = 64
subsets; and monotonicity is testable — `kept(P_{i+1}) ⊆ kept(P_i)` is asserted, so a policy that
somehow emits *more* than a weaker one is a red test rather than a curious data point.

| # | policy | the rule it adds | why a static checker cannot decide it |
|---|---|---|---|
| **P0** | `emit-all` | — | the mechanism as measured in D5, the baseline |
| **P1** | `no-bare-mentions` | on a **signature** change, drop references whose kind is `name` rather than `call` | a signature growing an argument cannot break a mention that is not a call. This is not undecidability — it is a **known negative**, and it is first on the ladder because it should cost no recall at all |
| **P2** | `no-star-calls` | drop call sites written `f(*args)` or `f(**kw)` | arity is not statically knowable through a splat; the binder cannot be simulated |
| **P3** | `no-unknown-receiver` | on a change to a **method**, drop references reached through an attribute whose receiver the file does not bind to a class | whether `obj.method(...)` reaches *this* class's member is not decidable from a name. Today the checker drops only receivers bound to a *different* class; this drops unknown ones too, and that is the trade |
| **P4** | `no-kind-changes` | drop every finding whose contract change reason is `kind` | function became a class, or a class a function: what breaks depends on how the caller used it, which is not in the name |
| **P5** | `decidable-only` | emit only when the checker can *simulate the failure* — a resolvable non-star call against a known new signature that cannot bind, or a resolvable import of a removed module-level symbol | the full rule. Everything else is silence |

P5 is the policy `D5` described as *"a mechanism that emitted only what it can decide"*. P1–P4 exist
so that the curve has interior points: if P5 is the only measurement, a bad number cannot be
attributed to any particular rule.

## 3. The decision rule, and what happens to each outcome

Declared here, applied without amendment:

1. Compute recall and silence-pool flag rate for all six policies on **`corpus_dev`** — the frozen
   development set, where tuning is legitimate and no number is published as a gate result.
2. **Name the policy with the lowest recall cost among those whose silence-pool flag rate is
   ≤ 0.05.** Ties on flag rate go to the higher recall; ties on both go to the lower-numbered
   (weaker) policy, because a weaker rule generalises further.
3. **If no policy reaches 0.05, that is F1's result and it is stated plainly**, not approximated,
   not re-baselined against a softer ceiling. `GOAL-SHIP.md` §0.2 forbids renegotiating the number
   and Phase E measures the thing it stood for directly.
4. Whatever is named is carried into **F2**, which is an independent lever (scoping to a declared
   surface). F1 and F2 are **not assumed additive** — their combination is measured.

Intervals are cluster bootstrap over source commits, as `CORPUS.md` §2 declared: 859 positives
live in 345 commits and are not independent replicates.

## 4. What would make this experiment worthless, stated so it can be checked

- **Ungraded read as correct.** A policy that emits nothing scores a perfect flag rate and zero
  recall; a harness bug that emits nothing scores the same. The run reports findings *kept* per
  policy alongside every rate, so a collapse to zero is visible rather than flattering. The
  previous run had to learn this three times, in three harnesses.
- **The ladder growing after the numbers are seen.** The six policies above are the six. A seventh
  invented after looking is exploratory, labelled so, and cannot be the named policy.
- **Recall measured at site level.** It is not: `{arm}_hit` is *"the checker names the mutated
  symbol"*, so dropping some references for a symbol costs recall only when it drops the last one.
  That is the definition D5 used and it does not change here.

---

## 5. One deviation, logged before the write-up

**2026-08-28, after the ladder ran, before it was written up.** The ladder as declared answers
*what does abstention cost and buy*. It cannot answer the question that decides whether a quiet
mechanism is worth having at all: **at a 1% flag rate, does the reference analysis still beat a
regex?** D5's headline was that it does at 27.8%; nobody had asked at 1%.

So `measure_abstention.py` gained D5's matched-flag-rate `git grep` arm, run per rung. It is
**exploratory and non-gating** — the named policy is fixed by §3's decision rule and this arm
cannot change it. It is logged here because a comparison added after the numbers are seen is a
comparison a reader is entitled to distrust, and hiding it would earn that distrust.

## 6. The result

345 clusters, silence pool 1,000 commits, the same seed and the same commits D5 measured.
`corpus_dev` — **in sample**, and no number here may carry a public claim.

| policy | recall | cost | silence-pool flag rate | findings kept | undecidable | `git grep` at the same flag rate |
|---|---|---|---|---|---|---|
| `emit-all` | 0.910 [0.88, 0.94] | — | 0.278 [0.25, 0.31] | 4,298 | 91% | 0.826 |
| `no-bare-mentions` | 0.910 [0.88, 0.94] | 0.000 | 0.239 [0.21, 0.27] | 3,519 | 89% | 0.826 |
| `no-star-calls` | 0.910 [0.88, 0.94] | 0.000 | 0.225 [0.20, 0.25] | 3,385 | 89% | 0.826 |
| `no-unknown-receiver` | 0.609 [0.56, 0.66] | **0.301** | 0.143 [0.12, 0.17] | 1,905 | 93% | 0.368 |
| `no-kind-changes` | 0.609 [0.56, 0.66] | 0.301 | 0.141 [0.12, 0.16] | 1,835 | 93% | 0.368 |
| **`decidable-only`** | **0.600** [0.55, 0.65] | **0.310** | **0.010** [0.01, 0.02] | **20** | **0%** | 0.296 |

**Named by the declared rule: `decidable-only`.** It is the only rung at or under the 0.05
ceiling, so the rule names it without a tie-break. Flag rate **0.010**, recall cost **0.310**.

### The trade is not where anyone predicted

Two rungs cost recall and two rungs buy silence, and they are **different rungs**.

| | recall cost | flag rate change |
|---|---|---|
| P1 + P2 — bare mentions and splat calls | **0.000** | 0.278 → 0.225 |
| P3 — unknown receivers | **0.301** | 0.225 → 0.143 |
| P4 — kind changes | 0.000 | 0.143 → 0.141 |
| P5 — emit only what can be simulated | **0.009** | 0.141 → **0.010** |

The entire recall bill is paid at **P3**, and almost all of the silence is bought at **P5**.
Nearly a third of the planted breakages are reachable only through a reference on a receiver the
file does not bind — `backend.deliver_now(1)` where `backend` is a parameter — and there is no
static route to those. That is the real cost of abstention, and it is a fact about Python rather
than about this checker.

P5, which looks like the drastic rung, is nearly free in recall and takes the flag rate down by a
factor of fourteen.

### The comparison that changes the reading

`emit-all` beats `git grep` by 8.4 points of recall at a matched flag rate. `decidable-only` beats
it by **30.4**.

| policy | checker | `git grep`, matched | checker only | grep only | exact McNemar |
|---|---|---|---|---|---|
| `emit-all` | 0.910 | 0.826 @ ≥2 mentions | 55 | 26 | p = 0.0017 |
| `no-unknown-receiver` | 0.609 | 0.368 @ ≥5 | 134 | 51 | p = 8.7 × 10⁻¹⁰ |
| **`decidable-only`** | **0.600** | **0.296** @ ≥6 | **148** | **43** | **p = 1.1 × 10⁻¹⁴** |

**Abstention costs recall in absolute terms and doubles the advantage over the cheap baseline.**
A regex cannot abstain. The only quietness available to it is a higher mention threshold, and a
threshold discards true and false findings in the same proportion; an abstention rule discards
only what it cannot decide. That asymmetry is the case for a reference analysis, and it is
invisible at 27.8% because at 27.8% grep is nearly as good.

### The precision column, read only for a disagreement

As §1 said it would be: **1.000 on 897 decidable findings** under `decidable-only`, against 0.396
under `emit-all`. That number is agreement between two implementations of "undecidable" — the
answer key's oracle and the abstention rule — and is **not evidence about the world**. What it is
good for is a disagreement, and there is none: the two implementations, written for different
purposes, never once disagreed on a finding the strict policy kept.

### Per block, because a pooled number no block resembles is not a measurement

| repository | recall, `emit-all` | recall, `decidable-only` | flag rate, `decidable-only` |
|---|---|---|---|
| `django/django` | 0.944 | 0.514 | 0.008 |
| `pytest-dev/pytest` | 0.934 | 0.697 | 0.020 |
| `sphinx-doc/sphinx` | 0.886 | 0.582 | 0.007 |
| `scrapy/scrapy` | 0.892 | 0.541 | 0.006 |
| `encode/httpx` | 0.800 | 0.680 | 0.014 |
| `pallets/flask` | 0.923 | 0.769 | 0.008 |
| `fastapi/fastapi` | 0.800 | 0.600 | 0.000 |
| `psf/requests` | 1.000 | 1.000 | 0.011 |

**Every block is under the ceiling** — 0.000 to 0.020, against 0.110 to 0.347 for `emit-all`.
Recall is where the blocks separate, from 0.514 to 1.000, and the two smallest blocks carry
intervals that say almost nothing.

### The limitation that matters more than any of the above

The answer key's positive class is built from exactly two failures: **a call that cannot bind**
and **an import that no longer resolves**. `decidable-only` emits exactly those two shapes. So
recall 0.600 is recall *on a positive class made of the two breakages this policy is built to
prove*, and a breakage of a third kind — a changed return type, a narrowed exception contract, a
semantic change behind an unchanged signature — is not in the corpus and would not be found.

That is **not** the precision circularity of §1: membership in the positive class is decided by
the mutation generator and verified against CPython's binder, with no reference to the checker.
It is a construct-validity limit, and it bounds what the number means rather than whether it is
real. The honest statement is: *among breakages a static reader can prove, abstention keeps 60%
of them while emitting one finding per hundred commits.* Nothing here says what fraction of all
breakages a static reader can prove.

### The collapse guard reported nothing, which is the point

A policy that emits nothing scores a perfect flag rate and zero recall; so does a broken harness.
`collapsed_policies` is empty and every row carries its findings count, so the 0.010 is a
mechanism that kept 20 findings rather than a harness that produced none.
