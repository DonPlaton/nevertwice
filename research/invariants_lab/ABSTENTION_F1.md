# F1 — abstention: the ladder, declared before the run

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
