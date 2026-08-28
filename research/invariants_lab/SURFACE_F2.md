# F2 — scope to what a project declared, and the census that says it cannot

**Task F2.** `SCALE_X4.md` is the only mechanism that passed its gates on non-circular evidence,
and it is quiet for one reason: it answers **only what a project asked**. A note says
`records: 1_000 -> 50_000_000` and the detector speaks about that axis and nothing else. Flag rate
0.073 against the ratchet's 0.364, on the same commits.

`GOAL-SHIP.md` F2 asks for the same shape here: *"a declared surface — the contracts a project
says are public — instead of every symbol in every changed file."*

The census that has to come first says the declared surface **is not there**, and that is F2's
first result rather than an obstacle to it.

Skills: `experimental-design` (§2, the grid and the additivity question),
`statistical-power` (§1, the base-rate census that decides which cells can be scored at all),
`scientific-critical-thinking` (§4, on the difference between a declaration and a convention).

---

## 1. The census, run before anything was declared

The C3 discipline T3 failed to apply to itself: **measure the base rate before declaring the
gate.** For a `__all__`-based surface the base rate is the share of the answer key's 859 positives
whose changed symbol the defining module actually declares.

| | of 859 confirmed positives |
|---|---|
| the defining module's `__all__` names the symbol's module-level head | **18** (2.1%) |
| private by convention — a single-underscore component in the qualname | 93 (10.8%) |
| everything else — no `__all__`, or one that does not name it | 841 (97.9%) |

**Eighteen.** A checker scoped to `__all__` would carry a recall near 0.02, and the gate could not
be resolved at any sample size this corpus can reach.

*(A first pass of this census asked whether `__all__` named the changed symbol's **leaf** —
`deliver_now` for `Mailer.deliver_now` — and found 3 of 859. `__all__` lists module-level exports,
so the rule the surface actually applies asks about the **head**, `Mailer`, which is how a caller
reaches the method. The table above is the recomputed number under the rule the code uses; the
first is recorded here rather than dropped, because a census that changes when you fix its rule is
a census whose rule belongs in the write-up.)*

`AXES_F3.md`'s rule applies: a gate the
corpus cannot resolve is **withdrawn before the measurement**, not after. `__all__` is still
measured below, because "we tried it and it collapses" is worth more than "we decided not to try
it", but it is reported as a withdrawn gate rather than scored as a failure.

### Why this is the interesting result and not a setback

`scale` has a declared axis because a project wrote one **for the mechanism**. Nothing equivalent
exists for *"which of my contracts are public"* in found history:

- `__all__` is a re-export control for `from x import *`, not a contract declaration, and it
  names the head of the changed symbol for 2.1% of real breakages;
- a leading underscore is a **convention**, and it marks 10.8% of the symbols that really did
  break a caller;
- a `docs/api.rst` is prose.

So the honest statement is: **the declared surface F2 wants cannot be measured on found history,
because found history does not contain one.** It can only be measured on a project that declares
it — which is a stand where somebody writes the note, and that is Phase E, not Phase V.

What *is* available on found history are **conventions**. They are measured here, and they are
labelled conventions in every table, because the distinction is the whole point of the task.

## 2. The grid, declared before the run

Two independent scoping levers plus the withdrawn one, crossed with the two abstention policies
F1 established. Ten cells, fixed here.

| surface | rule | declaration or convention |
|---|---|---|
| **W0** `everything` | every symbol in every changed file | the mechanism as measured in D5 |
| **W1** `public-by-convention` | drop contract changes whose symbol, or any component of whose qualname, starts with a single `_` (dunders excepted) | **convention** |
| **W2** `shipped-consumers` | drop findings whose *referencing* file is not shipped code, by `AXES_F3.md` §3's path rule | **convention**, and it scopes by consumer rather than by producer |
| **W3** `both` | W1 and W2 together | — |
| **W4** `declared-public` | the defining module has an `__all__` naming the symbol | **declaration**, and the census says it is empty |

| policy | from F1 |
|---|---|
| `emit-all` | the baseline |
| `decidable-only` | the policy F1's declared rule named |

**W1 and W2 are independent levers and their combination is measured, not assumed additive.** The
run reports the observed W3 flag rate next to the product of W1's and W2's marginal reductions, so
"not additive" is a number rather than a caution.

## 3. The decision rule

1. Compute recall and silence-pool flag rate for all ten cells on `corpus_dev` — in sample, and no
   number here may carry a public claim.
2. **Among cells whose silence-pool flag rate is ≤ 0.05, name the one with the least recall cost
   against (W0, `emit-all`).** Ties on cost go to the higher recall; ties on both go to the weaker
   surface, then the weaker policy, because a weaker rule generalises further.
3. W4 is reported but **cannot be named**: its gate was withdrawn in §1 before the run, on a base
   rate of 18 of 859.
4. If no cell reaches 0.05 the ceiling is not renegotiated — F1 already produced a cell that does,
   so this outcome would mean scoping adds nothing, and that is a result.

Sample sizes are D5's, unchanged: 345 paired clusters and a 1,000-commit silence pool at the same
seed, so every row compares to every published row.

## 4. What would make this measurement worthless

- **Calling a convention a declaration.** W1 and W2 are conventions. The tables say so on every
  row. The one row that is a declaration is W4, and it is empty.
- **Reading a silent cell as a quiet mechanism.** A surface that excludes everything scores a
  perfect flag rate. Findings kept is reported for every cell, as in F1.
- **Assuming the levers multiply.** They are measured together, and the deviation from the product
  of the marginals is reported.

---

## 5. The result

345 clusters, 1,000-commit silence pool, the same seed as D5 and F1, so every row compares.
`corpus_dev` — **in sample**.

| policy | surface | kind | recall | cost | flag rate | findings kept |
|---|---|---|---|---|---|---|
| `emit-all` | `everything` | baseline | 0.910 [0.88, 0.94] | — | 0.278 [0.25, 0.31] | 4,298 |
| `emit-all` | `public-by-convention` | convention | 0.794 [0.75, 0.83] | 0.116 | 0.263 [0.24, 0.29] | 4,062 |
| `emit-all` | `shipped-consumers` | convention | 0.670 [0.62, 0.72] | 0.241 | 0.207 [0.18, 0.23] | 2,081 |
| `emit-all` | `both` | convention | 0.594 [0.54, 0.64] | 0.316 | 0.196 [0.17, 0.22] | 1,960 |
| `emit-all` | `declared-public` | **declaration** | 0.043 [0.03, 0.07] | 0.867 | 0.013 [0.01, 0.02] | 192 *(withdrawn)* |
| **`decidable-only`** | **`everything`** | baseline | **0.600** [0.55, 0.65] | **0.310** | **0.010** [0.01, 0.02] | **20** |
| `decidable-only` | `public-by-convention` | convention | 0.530 [0.48, 0.58] | 0.380 | 0.008 [0.00, 0.02] | 17 |
| `decidable-only` | `shipped-consumers` | convention | 0.423 [0.37, 0.48] | 0.487 | 0.006 [0.00, 0.01] | 11 |
| `decidable-only` | `both` | convention | 0.383 [0.33, 0.43] | 0.528 | 0.004 [0.00, 0.01] | 8 |
| `decidable-only` | `declared-public` | **declaration** | 0.009 [0.00, 0.03] | 0.901 | 0.000 | 0 *(withdrawn)* |

**The declared rule names `decidable-only` + `everything`** — the cell F1 already found. Every
scoped cell that reaches the ceiling reaches it by giving up more recall than the unscoped one
gives up, so scoping is never the cheaper way to the same silence.

### Scoping is not free, and abstention is not scoping

| lever | recall cost | flag rate, from 0.278 |
|---|---|---|
| abstention alone (`decidable-only`) | 0.310 | **0.010** |
| conventions alone (`both`) | 0.316 | 0.196 |

**Almost the same recall bill; a factor of twenty apart on silence.** The two mechanisms are not
substitutes and the reason is structural: abstention removes findings the checker *cannot decide*,
which is 91% of what it says on the silence pool; a surface removes findings about *symbols or
callers of a certain kind*, and those are distributed across decidable and undecidable alike.

### The levers multiply, and that is measured rather than assumed

| policy | `both`, observed | if the two levers were independent | difference |
|---|---|---|---|
| `emit-all` | 0.196 | 0.196 | **+0.000** |
| `decidable-only` | 0.004 | 0.005 | −0.001 |

`SURFACE_F2.md` §2 refused to assume additivity, and the answer is that they are almost exactly
independent — which is itself informative: the private-symbol rule and the test-file rule select
on properties that have nothing to do with each other, so stacking them buys the product and no
more.

### The declaration, which is the row the task was actually about

`declared-public` is the only row that is a **declaration** rather than a convention, and it does
what §1's census said it would: **recall 0.043**, and 0.009 once abstention is applied on top. It
reaches the ceiling by being silent about almost everything, which is not the same as being right.

That is F2's finding, and it is not about `__all__`:

> **`scale` is quiet because a project declared something for the mechanism to check. Found
> history contains no such declaration about contracts, so this lever cannot be measured on found
> history at all — only on a project that writes one down.**

`__all__` is the closest thing that exists, and it is a re-export control rather than a contract
declaration: it names the symbol's module-level head for 18 of 859 real breakages. The conventions
that *are* available — a leading underscore, a test-file consumer — are conventions, and they cost
recall roughly in proportion to what they remove, which is what a filter uncorrelated with
correctness does.

**The version of F2 worth running is on a stand where somebody declares the surface**, and that is
Phase E rather than Phase V. Nothing in `corpus_dev` or `corpus_heldout` can answer it, and saying
so is more useful than reporting 0.043 as though it measured the idea.
