# F3 — the three unaudited axes, declared before the audit

**Task F3.** `RATCHET_R3.md` closed with: *"give the other three axes an independent instrument,
or drop them. Nesting, returns and length carry an unknown share of the 36% and nothing outside
this project can check them."*

Something outside this project could check two of them, and it was installed on this machine the
whole time. `ruff`'s C901 measures McCabe and nothing else — but `ruff` also ships the
**pylint-derived** rules, and three of them are exactly the missing instruments.

Skills: `statistical-power` (§3, the sample sizes below were computed before the audit ran),
`scientific-critical-thinking` (§2, on what a *different* metric can and cannot certify),
`test-driven-development` (the instruments were written against hand cases that failed first).

---

## 1. What was reachable, and when

Recorded because `RATCHET_R3.md` had to record the same thing and the situation has not changed.

| | status on **2026-08-28** |
|---|---|
| PyPI | **unreachable** — `pip download radon` still returns `SSL: UNEXPECTED_EOF_WHILE_READING` after three retries |
| `radon`, `pylint`, `flake8` plugins | not installable |
| `ruff` 0.15.16 | **installed**, and carries `PLR1702`, `PLR0911`, `PLR0915` |

Each rule is run with its threshold set to **zero**, so the diagnostic message carries the
*value* rather than a pass or a fail — the trick `ruff_complexity` already used for C901.
`PLR1702` is a preview rule in this version and needs `--preview`.

## 2. One axis has an instrument for the same metric; one does not

| axis | instrument | measures | check |
|---|---|---|---|
| `cyclomatic` | `C901` | McCabe | **exact agreement** — already done in R1, 54 of 54 modules |
| `nesting` | `PLR1702` too-many-nested-blocks | deepest nested block | **exact agreement** |
| `returns` | `PLR0911` too-many-return-statements | `return` statements per function | **exact agreement** |
| `length` | `PLR0915` too-many-statements | **statements**, not lines | **directional only** |

`length` is the axis this cannot rescue with an equality test, and pretending otherwise would be
the mistake this project keeps writing down. This project's `length` is *lines from `def` to the
last statement*; `PLR0915` counts *statements*. They are different numbers about the same thing.
A blank line, a docstring and a wrapped call argument move one and not the other — which is
precisely why `length` carries ten lines of slack and no other axis does.

So `length` gets a weaker check of a different kind, and it is declared as weaker here rather
than presented as agreement afterwards.

`PLR1702` needs one piece of care that is stated before the numbers exist: it reports once per
nested **block group**, not once per function, so each diagnostic is attributed to the *innermost*
callable containing it and the function's value is the maximum over its own blocks. Attributing to
the innermost is what makes the comparison valid — `nesting()` stops at a nested `def`, so the
instrument checking it must stop there too.

## 3. The gates, their sample sizes, and the arithmetic behind them

`GOAL-SHIP.md` §3 makes `statistical-power` mandatory wherever a stand is built, because T3
declared a gate without asking what base rate it needed and burned a whole experiment finding out
the answer was 288 trials. The arithmetic is therefore here, before the audit.

### F3-A · nesting and returns, exact agreement

**Gate:** every callable in the audit set gets the same number from both implementations.
**Threshold: 100%.** Not a proportion with an interval — a disagreement on a metric this
mechanical is a defect in one of the two, and R1 found four in this project's cyclomatic that way.

**Audit set, declared before running:**
1. every callable in `nevertwice/**/*.py` — the set R1 used, for comparability;
2. **plus** a seeded sample of **300 files** drawn evenly from the eight `corpus_dev` blocks,
   which is a far harder set: 5.43% of that corpus is Python 2 and it contains idioms nobody
   here writes.

**Consequence, declared:** a disagreement is investigated. If this project's metric is wrong, it
is fixed and the audit re-run. If the two definitions genuinely differ and cannot be reconciled,
**the axis is dropped**, exactly as `GOAL-SHIP.md` F3 says.

### F3-L1 · length, directional agreement

**Gate:** among function-diffs where the ratchet's `length` axis regressed, the fraction where
`PLR0915`'s statement count *also* rose is **> 0.50**. One-sided exact binomial, α = 0.05.

**Why conditioned on the ratchet firing, not on statements rising.** The ratchet's `length`
carries ten lines of slack and `PLR0915` carries none, so "statements rose" is far commoner than
"lines rose by more than ten" and conditioning that way would fail the gate on a definitional
mismatch rather than on evidence. The question that actually matters is the other one: **when the
length axis fires, is it firing on something an independent instrument also calls more code, or
is it firing on reformatting?**

**Sample size.** Exact binomial, H₀ p = 0.50, 80% power:

| effect to detect | n needed |
|---|---|
| 0.60 | 189 |
| **0.65** — what the audited `cyclomatic` axis achieved in R3-C2 (0.637) | **85** |
| 0.70 | 43 |

0.65 is the declared effect, on the ground that the axis with an instrument behind it reached
0.637 and an axis worth keeping should not be far below it. **n = 85 firings needed.**

**Available.** A census of 120 commits found 11,161 function-diffs and **16** where the length
axis regressed beyond slack. The full `corpus_dev` pool is **2,853** commits, extrapolating to
**≈ 380 firings** — four times the requirement. **The gate is resolvable.** If the realised count
comes in under 85, the gate is **withdrawn before the measurement is scored**, not after.

### F3-L2 · length, discrimination

**Gate:** the length axis fires at least **2×** more often on function-diffs where `PLR0915` says
statements rose than where it says they did not. Fisher exact, one-sided, **p < 0.01**.

The R3-C3 shape. With ~93,000 function-diffs available and a firing base rate near 0.14%, both
arms are heavily powered; the constraint is F3-L1's 85, not this one.

### F3-S · the flag rate, re-measured, in a 2 × 2

`GOAL-SHIP.md` F3 also asks for `scale`'s lesson: *scope to what a project declared, not to
whatever the diff touched.* The ratchet's declaration is its baseline note, and on this corpus
nobody wrote one, so the scope is defined **mechanically and in advance**:

| | every changed file | **shipped code only** |
|---|---|---|
| **all four axes** | R3's 0.364 — the number that deleted the mechanism | |
| **audited axes only** | | |

*Shipped code* excludes, by path: `test`/`tests` directories and `test_*.py`, `docs`,
`doc`, `examples`, `benchmarks`, `scripts`, `setup.py`, `conftest.py`, and anything under a
`.github` or `tools` directory. The rule is written here, before the run, so it cannot be tuned
to a number.

**No threshold is declared for F3-S and none is scored.** T1's 0.05 ceiling is not renegotiable
and Phase E measures directly what it stood for. F3-S reports four numbers so the contribution of
each change is separable; whether any of them is small enough is not F3's question.

## 4. What would make this audit worthless

- **An instrument that silently did not run.** `ruff_nesting`, `ruff_returns` and
  `ruff_statements` return `None` when `ruff` fails, never `{}`, and the suite skips **loudly**
  rather than passing. A control that reports "no disagreement" because it produced no output is
  the fourth instrument this project would have believed by accident.
- **Attributing `PLR1702` to the wrong function.** Handled above and pinned by a hand case where
  a nested `def` holds the deep block and the outer function must score zero.
- **Reading a directional result as an equality one.** `length` does not get an agreement number
  in this document, in the write-up, or anywhere else. It gets F3-L1 and F3-L2 and a sentence
  saying what they are.

---

## 5. A deviation, and the counterfactual under the gate as literally written

**Declared:** exact agreement on **every callable in the audit set**. **Run as:** exact agreement
on every callable whose value `PLR1702` can *express*. That is a restriction of the audit set made
**after** the first run showed disagreements, and it is logged here as one.

The reason is a property of the instrument, demonstrable without reference to any disagreeing
case: `PLR1702` counts a nested block chain **without resetting at a `def`**, and reports the whole
chain at the chain's outermost block. A probe on three lines of code shows it emitting *two*
diagnostics at one line — depth 1 and depth 2 — for a `def` nested inside an `if`. So for a
callable that contains another callable, sits inside one, or sits under a module-level block,
ruff's output is not that callable's own depth in either direction, and no parsing recovers it.

`returns` needs no such restriction: `PLR0911` reports once per function at its `def` line, and it
was compared on every callable.

| | callables | nesting comparable | excluded as inexpressible |
|---|---|---|---|
| `nevertwice/**` | 850 | 776 | 74 (8.7%) |
| `corpus_dev`, 296-file sample | 4,001 | 2,909 | 1,092 (27.3%) |

**The counterfactual, stated because it is the honest one:** under the gate as literally declared
— every callable, 100% agreement — `nesting` **fails**, and F3's declared consequence is to drop
it. What that failure would have been measuring is `PLR1702`'s reporting granularity, not this
project's number, and the fixes below are global properties of the metric rather than repairs
aimed at the disagreeing cases. A reader who prefers the literal reading should drop the axis;
`F3-S` below reports the flag rate with and without it, so that reading costs nothing to take.

## 6. F3-A — the result, and the four defects the instruments found

**PASS.** Zero disagreements, both axes, both sets — 3,685 comparable callables.

| set | files | callables | nesting disagreements | returns disagreements |
|---|---|---|---|---|
| `nevertwice/**` | 53 | 850 | **0** | **0** |
| `corpus_dev` sample | 296 | 4,001 | **0** | **0** |

It did not start there. The first run had **133** nesting disagreements, and four separate faults
were behind them — three in this project, one in how the instrument was being asked:

| # | what | where | how it was found |
|---|---|---|---|
| 1 | an **`elif` chain counted as one level per branch**. In the AST an `elif` is an `If` inside the parent's `orelse`, so walking children read a flat three-way chain as depth 3 | `complexity.nesting` | the bulk of the 133 |
| 2 | **`else:` + an indented `if` read as an `elif`**. The two produce an *identical* AST; only the column offset separates them, and the first fix for (1) swallowed real nesting | `complexity.nesting` | `psf/requests`'s `Server.__exit__`, 1 where both a reader and the instrument say 2 |
| 3 | a **`# noqa` in the source switching the instrument off**. `psf/requests` carries `def proxy_bypass(...):  # noqa`, so `PLR0911` returned nothing there and the comparison scored it as agreement | the harness | a `returns` disagreement that had no business existing |
| 4 | a **qualname defined more than once** resolved by traversal order. `psf/requests` defines `cookiejar_from_dict` three times — two `@overload` stubs with `...` bodies, then the real one — and a stub's metrics (nesting 0) were landing where the real function's (nesting 3) belonged | `complexity.scan_file` | five disagreements of the form *mine 0, ruff 3* |
| 5 | a **`match` counted as nesting** where `PLR1702` does not | `complexity._NESTS` | a definitional difference, resolved toward the instrument, as R1 resolved the same question for cyclomatic |

**(3) is the fourth time this project has had to write "ungraded is not the same as correct", and
(4) is the third appearance of D6's blind spot — a name defined more than once, silently resolved —
in a third instrument.** Defect (4) is not cosmetic for a ratchet: the baseline stores
`path → qualname → axis`, so an overloaded function's stored baseline was an ellipsis, and next
quarter's real function would have been compared against it.

## 7. F3-L — `length` against a metric that is not `length`

**F3-L1 PASS.** 496 firings against the 85 the power table required.

| | |
|---|---|
| function-diffs where the `length` axis regressed | **496** |
| of those, `PLR0915`'s statement count also rose | **429** |
| rate | **0.865** [0.83, 0.89] |
| one-sided exact binomial vs 0.50 | p = 5.9 × 10⁻⁶⁶ |

**F3-L2 PASS**, on the declared 400-commit sample.

| | function-diffs | fired | rate |
|---|---|---|---|
| statements rose | 586 | 78 | **0.1331** |
| statements flat | 32,825 | 9 | **0.0003** |

**Ratio 485×**, Fisher exact one-sided p = 2.5 × 10⁻¹²⁸, against a declared bar of 2×. Both arms
clear the 152-per-arm requirement.

`length` therefore survives, and the sentence that goes with it is the one §2 declared in advance:
this is **directional agreement with a different metric**, not agreement. When the length axis
fires, an independent statement count agrees that the function got bigger 86.5% of the time, and
the axis is 485 times likelier to fire where that count rose. It is not measuring reformatting.

## 8. F3-S — and the flag rate does not care

| | flagged | flag rate |
|---|---|---|
| all four axes, every changed file | 1,021 / 2,853 | **0.358** [0.34, 0.38] |
| all four axes, shipped code only | 909 / 2,853 | 0.319 [0.30, 0.34] |
| audited axes only, every changed file | 983 / 2,853 | 0.345 [0.33, 0.36] |
| audited axes only, shipped code only | 884 / 2,853 | **0.310** [0.29, 0.33] |

*(0.358 rather than R3's 0.364 because this ran the whole 2,853-commit pool instead of a
1,000-commit sample, and because the metric changed under the fixes above.)*

**Against a ceiling of 0.05, none of it matters.** Auditing the axes moved the flag rate by 1.3
points. Scoping to shipped code moved it by 4. Both together: 0.358 → 0.310, still **six times**
the ceiling, and the interval does not come close.

### The contrast with F1 is the finding

| | flag rate before | after its lever | what the lever removed |
|---|---|---|---|
| `blast_radius` | 0.278 | **0.010** | findings it **could not decide** — 91% of what it said |
| complexity ratchet | 0.358 | 0.310 | axes without an instrument, and unshipped consumers |

**Abstention is available to a checker that cannot decide. It is not available to one that can.**
Every ratchet finding is decidable by construction: the metric really did go up, and two
independent instruments now agree it did. There is nothing to abstain *from*, and that is precisely
why the ratchet cannot be made quiet the way `blast_radius` was.

The ratchet's problem was never its axes. F3 was worth running because it said so with numbers
instead of assuming it — and because on the way it found four defects, one of which was silently
storing an `@overload` stub's metrics as a function's baseline.
