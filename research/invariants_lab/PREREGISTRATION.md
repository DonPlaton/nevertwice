# Preregistration — six mechanisms, their thresholds, and what deletes each one

**Task C4.** Committed **before any mechanism is measured**. No section here may be edited after
its mechanism has run; a change is recorded in [§9 Deviations](#9-deviations) with its date and
reason, or it did not happen.

Skills used: `hypothesis-generation`, `experimental-design` (C1's design, carried),
`statistical-power` (C3's numbers, carried).

Reads on: [`CORPUS.md`](CORPUS.md) for the design, [`MUTANTS.md`](MUTANTS.md) for the positive
class, [`POWER.md`](POWER.md) for what the corpus can resolve.

---

## 0. Why this document exists in this form

The previous run declared thresholds before measuring — `research/BLAST_RADIUS_DECISION.md` — and
still reached a verdict it was not entitled to, because the thresholds were declared against a
corpus that could not resolve them and an answer key that had no positives in it. Declaring first
is necessary and was never sufficient. This document therefore states, for every gate: **the
number, the n it will be evaluated at, and whether that n can see it.**

Nothing below is a finding. Every row is a *prediction* with a decision rule attached.

## 1. Definitions used by every mechanism

| term | definition, fixed here |
|---|---|
| **unit** | one source commit. Never a mutant: 859 mutants sit in 345 commits and one commit contributes 66 |
| **block** | the repository. Every headline number is reported per block *and* pooled; a pooled number one block dominates is labelled as such |
| **primary sample** | one mutant per source commit, drawn by a seeded stratified sample (`seed=20260827`), **n = 345** |
| **secondary sample** | all 859 mutants, intervals cluster-bootstrapped with the commit as the cluster. Reported, never used for a gate |
| **paired negative** | the same commit, unmutated. The two arms are paired on the commit; the test is exact McNemar |
| **silence pool** | the 6,905 census commits that changed a contract and updated **no** in-repo caller. A detector must stay silent on these |
| **finding** | one (symbol, tree) pair a detector reports as a dependency problem |
| **TRUE finding** | a finding whose named symbol, **in the tree it was reported on**, actually has a call that cannot bind or an import that cannot resolve — decided by `sigscan` plus CPython's binder, the same mechanical bar that built the positive class, applied to the tree under test |
| **hit** | for a mutant, a finding naming the mutated symbol by qualname or short name |

**Precision is decided mechanically, not by hand.** The previous run hand-labelled 26 of 162
sites and found five defects in its own labeller while doing it. Here the labeller is the same
instrument that CPython agreed with 868 times out of 868, and it is applied identically to both
arms.

## 2. What is confirmatory and what is exploratory

**Confirmatory:** every gate in §3–§7 below, on the primary sample, with the stated n and test.

**Exploratory, and labelled as such wherever reported:** per-block breakdowns, the
test-file-versus-package-code stratum, the arity-versus-vanished stratum, the secondary sample,
and any subgroup not named in this document before its mechanism ran.

**Multiplicity.** Within a mechanism the gates are a **conjunction** — all must pass for the
mechanism to survive — so multiple gates make survival *harder*, not easier, and no α correction
is applied or needed. Across mechanisms, each gate decides the fate of a different artifact, not
a shared hypothesis, so no family-wise correction is applied either. Both choices are stated here
rather than left to be inferred after the fact.

**Indeterminate outcomes are declared in advance.** A gate whose n falls below what §3–§7 states
is **not evaluated**, and the mechanism is recorded as *unmeasurable at this corpus size* — not
as passing, and not as failing. That case has bitten this project before: precision on four
findings is not a precision.

---

## 3. Mechanism 2 — `blast_radius` (detector)

**Claim type:** predictive. The mechanism proposes that a static reference analysis identifies
callers left behind by a contract change.

**Hypothesis (candidate).** On a diff that changes a contract, the set of references to that
contract which the diff did *not* touch contains the callers that will break.

**Discriminating prediction.** On a mutant the checker names the mutated symbol; on that mutant's
paired real commit it does not. A checker that names the symbol in **both** trees is responding to
the contract change, not to the stale caller, and the paired design is what separates those.

**Negative controls.**
1. The **paired real commit** — same repository, same diff, same symbols, same size; differs only
   in whether the caller was updated. It shares every bias pathway and cannot operate through the
   mechanism.
2. The **silence pool** — a contract changed and nothing in the repository calls it. The checker
   must stay quiet.

**Named cheap baseline.** `git grep` for each changed symbol, given the symbol list for free,
thresholded on unhandled mentions so its flag rate matches the checker's. Unchanged from
`research/BLAST_RADIUS_DECISION.md`, so the two runs are comparable.

**Precondition, and it is a hard one.** D5 does not run while the defect list is non-empty. D1–D4
must land first: widened signatures, tuple-unpacking rebinds, annotation-only changes,
class-gained-member, and the remaining facade shapes, each with its own regression.

| gate | threshold, declared now | test | n | can the corpus see it |
|---|---|---|---|---|
| **D5-P1** precision | point estimate **≥ 0.50** and the lower bound of the 95% Wilson interval **> 0.25** | Wilson interval on all findings across both arms | ≥ **20** findings required; below that the gate is **not evaluated** | yes — at n=20, p=0.5 gives [0.30, 0.70] |
| **D5-P2** recall | **> 0.50** | exact binomial vs H₀ p=0.50, α=0.05 two-sided | 345 clusters | yes — 20 needed for 0.8, 189 for 0.6; MDE 0.575 |
| **D5-P3** vs baseline | strictly beats `git grep` on recall at matched flag rate; **ties go to `git grep`** | exact McNemar on the same clusters, p < 0.05 | 345 clusters | yes — 226 needed for a 10-point margin at ρ=0.5 |
| **D5-P4** silence | flag rate on the silence pool **≤ 0.05** | exact binomial vs H₀ p=0.05 | 1,000 sampled commits (seeded) | yes — 308 needed to see 0.02 |

**Deletion decision.** Fails **any** of P1–P4 → the mechanism is deleted from
`research/invariants_lab/`, is not promoted, and the negative result is published at the same
length a positive one would be. With an empty defect list and a powered corpus behind it, that is
a real result.

**Reported alongside, never as a gate:** precision and recall split by block, by
test-file-versus-package-code caller, and by `arity` versus `vanished` breakage.

---

## 4. Mechanism 3 — complexity ratchet (detector)

**Claim type:** predictive, with a definitional trap that has to be stated.

**The trap.** A ratchet's positive class is *defined by its own metric*: a diff that raised
cyclomatic complexity above a file's baseline is a positive because the metric says so. Recall
against that definition is 1.0 by construction and measures nothing. Reporting it would be the
same error as reporting precision on an all-negative corpus, in the opposite direction.

**So the informative measurement is agreement with an instrument that is not this one**, plus the
cost of the firing.

**Named cheap baseline.** `radon cc` at a fixed threshold, and `radon`'s own per-file delta across
the same diff.

| gate | threshold | test | n |
|---|---|---|---|
| **R3-C1** silence | flag rate on the silence pool **≤ 0.05** | exact binomial vs H₀ p=0.05 | 1,000 sampled commits |
| **R3-C2** agreement | on diffs where **`radon`'s own delta** says complexity rose, the ratchet fires at rate **> 0.50** | exact binomial vs H₀ p=0.50 | all such diffs in the corpus, minimum 189 |
| **R3-C3** discrimination | the ratchet's firing rate is **strictly higher** on `radon`-rose diffs than on `radon`-flat diffs | exact McNemar / 2×2, p < 0.05 | as above |
| **R3-C4** exemption | one command exempts a file, and the exemption survives a session | contract assertion, binary | — |

R3-C4 is not decoration. A ratchet with an awkward exemption path is switched off the first time
complexity legitimately has to rise, and after that being right does not matter.

**Deletion decision.** Fails any of C1–C4 → deleted, negative result published. **C2 or C3
failing is the interesting negative**: it would mean the ratchet and an established metric
disagree about what "worse" means, and that is worth a paragraph rather than a line.

---

## 5. Mechanism 5 — scale assertions and the 10× canary

**Claim type:** predictive (static) and mechanistic (dynamic).

**Positive class.** Known-quadratic code injected along a declared growth axis. n is chosen by
this project, not by the corpus, so C3's "underpowered at 177" note does not apply: **≥ 250
injections** are generated, which clears the 229 that a 0.95-versus-0.90 comparison needs.

**Negative class.** Linear code along the same axis, matched in length and shape, so the detector
cannot win by responding to size.

| gate | threshold | test | n |
|---|---|---|---|
| **X4-S1** recall on quadratics | **> 0.90** | exact binomial vs H₀ p=0.90 | ≥ 250 injections |
| **X4-S2** false positives on linear code | **≤ 0.05** | exact binomial vs H₀ p=0.05 | ≥ 250 matched controls |
| **X4-S3** the canary discriminates | at ×10 input the quadratic case exceeds its time or memory envelope and the linear case does not, on **every** matched pair | deterministic, binary | ≥ 30 pairs |
| **X4-S4** no declared axis, no finding | with no declared growth axis the mechanism reports nothing at all | contract assertion, binary | — |

The recall floor is **higher** than the detector floors elsewhere on purpose: a missed quadratic
is the entire failure this mechanism exists to prevent, and a scale checker that finds half of
them is a scale checker nobody can rely on.

**Deletion decision.** Fails any of S1–S4 → deleted, negative result published.

---

## 6. Mechanism 6 — DB authority boundary and reversibility (enforcement)

**Claim type:** a security property. **Measured by attack, not by precision**, because an
enforcement mechanism validated by its author's imagination is validated by nothing.

| gate | threshold | test |
|---|---|---|
| **B1-R1** reversibility | every migration in the fixture carries a non-empty `down`, and up → down → up leaves the schema and the suite identical | deterministic, binary |
| **B3-A1** bypass count | **≥ 12** distinct documented bypass attempts, spanning at least four classes: direct call, import-path evasion, environment/config override, and token forgery or replay | enumerated and published in full, including the ones that were easy |
| **B3-A2** bypass success | **exactly 0** succeed | deterministic, binary |
| **B3-A3** the attempts are real | at least **3** of the attempts must have succeeded against a deliberately weakened build, proving the attack harness can land a hit | positive control |

**B3-A3 is the control this project learned to demand.** A bypass suite that never succeeds
against anything proves nothing about the boundary and everything about the suite — the same
unfalsifiability that made the old precision census's labeller need positive controls.

**Deletion decision.** A single successful bypass means the operation is *expressible* without a
snapshot and a token, which is the negation of the mechanism's whole claim. It is deleted or
redesigned, and the successful bypass is published.

---

## 7. Mechanism 1 — the invariant note (infrastructure), and mechanism 4 — the seam journal (process)

Neither is a detector. Applying a precision gate to them would repeat the last mistake in a new
place.

**Mechanism 1 — contract assertions, all binary, all must hold:**

| | assertion |
|---|---|
| I3-K1 | **zero** context tokens are added when no invariant fires |
| I3-K2 | `NEVERTWICE_INVARIANTS=0` disables it completely, verified by output and by token count |
| I3-K3 | the module is **absent from `sys.modules`** after importing the package — the lazy import is proven, not asserted |
| I3-K4 | `rm -rf` of the package leaves the full suite green |
| I2-L1 | an invariant auto-retires after **two** false positives, not five |
| I2-L2 | **at most one** finding per diff, ranked |

I2-L1 and I2-L2 are stricter than intuition wants, and deliberately: the spec's own warning is
that an invariant firing on 30% of diffs is switched off in week one.

**Mechanism 4 — executed, not scored:**

| | assertion |
|---|---|
| S3-E1 | a real migration is performed on a polygon copy, and the **full suite is green at every intermediate seam**, not only at the end |
| S3-E2 | the plan is reloaded across a simulated session boundary and the next step is identified without the earlier context |
| S3-E3 | a seam that would leave the tree red is refused before it is attempted |

**Deletion decision** for both: a failed assertion is a bug to fix, not a verdict, **until the
second attempt fails** — at which point the mechanism is `BLOCKED` under GOAL §6.2 and the reason
is written down.

---

## 8. The combined gates

| gate | threshold | test | n |
|---|---|---|---|
| **T1** union flag rate | the union of all promoted invariants fires on **≤ 0.05** of the silence pool. If it exceeds that, rank and cap, or drop the weakest contributor — do not relax the number | exact binomial vs H₀ p=0.05 | 1,000 sampled commits |
| **T2** interaction | **zero** diffs on which two invariants report the same underlying cause. Two findings from one root cause is one finding and one bug | deterministic, over the flagged set | — |
| **T3** cold start | on a repository with **zero** prior sessions, the pitfall rate with the preconfigured pack on is **lower** than with it off, by a margin whose 95% interval excludes zero | paired by task, exact McNemar over trials | ≥ 12 tasks × 8 trials, the `LIVE_VALIDATION.md` stand |

**T3 runs on a local Ollama model** — the stand already has an `--ollama=` backend and published
`4b` and `7b` results — so it costs nothing and needs no gate. A frontier-model confirmation is
**G8** and stays the owner's to run. T3 is the axis on which a retrieve-and-inject system
scores zero by construction on a repository with no history -- Nevertwice as it ships today
included -- so the *comparison* is against **Nevertwice-with-an-empty-store**, which is the one
arm this project can actually run. Any claim about a named vendor would need that vendor run on
the same stand, and none has been.

---

## 9. Deviations

None yet. Every later entry carries the date, what changed, why, who decided, and the expected
effect on the result. A deviation is a thing that is written down, not a thing that is avoided by
not noticing it.

| date | section | change | reason | decided by | expected effect |
|---|---|---|---|---|---|
| 2026-08-28 | §4 | **The named baseline for R3 changes from `radon cc` to `ruff`'s C901.** Both compute McCabe cyclomatic complexity | `radon` cannot be installed on this machine: PyPI returns `SSL: UNEXPECTED_EOF_WHILE_READING` and `github.com:443` refused the fallback clone. `ruff` 0.15.16 is already present and implements the same metric in Rust, by different authors | the loop, before R3 ran | **none on the property the baseline was chosen for.** Independence is what `radon` was for, and `ruff` shares neither language nor authors with anything here, so the substitution is neutral-to-stronger. `tests/_test_complexity.py` pins agreement between this project's metric and `ruff`'s on every module in `nevertwice/`; before that agreement was reached the comparison found four real errors in this project's implementation |
| 2026-08-28 | §3 | **Added one exploratory, non-gating secondary arm to D5**: the same checker with a per-call-site compatibility filter, reported beside the primary result and never used for a gate | D4's corpus enumeration showed that after every defect is closed, ~67% of the checker's decidable high-confidence findings are calls that still bind. The mechanism is specified as a *dependency* reporter and the gate asks about *breakage*, so the gap is the mechanism's honest precision — but a reader is entitled to know what closing it would buy | the loop, before D5 ran | raises the reported precision of the secondary arm only. **The filter shares its rule with the answer key**, so its precision is agreement with itself, not evidence, and it is labelled that way wherever it appears. The gates are unchanged |
