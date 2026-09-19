# F5 — the cold-start experiment X1 named and nobody ran

<!-- withdrawn-banner -->
> **Withdrawn: figures on this page must not be quoted.** They were retracted and remain
> here because deleting a result one was wrong about destroys the record of having been
> wrong. The design, the method and the caveats stand; the numbers do not. Each figure's
> own reason and date are in
> [`research/evidence_manifest.json`](../evidence_manifest.json), and
> `python tools/check_freshness.py --list-stale` lists every one.

**Task F5.** `COLDSTART_T3.md` failed, and the reason it failed generalises:

> A preconfigured invariant can only help where the model is wrong. What ships in the box has to
> be **portable**, and portable knowledge is the knowledge models already have.

`SCALE_X4.md`'s declared axis is the one shape that escapes that argument. `records: 1_000 ->
50_000_000` is **project-specific** — no model's weights contain it — and it is **checkable on day
one**, before any history exists. T3 measured a portable pack and found nothing to prevent. F5
measures the unportable one.

Skills: `statistical-power` (§3, the base rate comes *first* and the arithmetic decides whether the
stand runs at all), `experimental-design` (§2, the paired arms and an independent judge),
`scientific-critical-thinking` (§4, on judging a static mechanism with a static instrument).

---

## 1. The one discipline T3 failed to apply to itself

T3 declared a gate, ran 96 generations per model, and discovered afterwards that the base rate was
**0.000** on the stronger model — nothing to prevent — and that resolving the effect it did see
would have needed **288 trials**, three times what was run. The arithmetic was computable before a
line was written and was not asked for.

So F5 runs in two stages and the first one can end it:

1. **A base-rate probe.** A small, cheap number of generations, measuring only *how often the
   model writes a scale fault at all*.
2. **The powered stand**, run only if the probe says the effect is resolvable at a trial count
   this machine can afford. **If the base rate is at or near zero, F5 stops there and says so** —
   that is the exit condition `GOAL-SHIP.md` wrote for it, and it is a result rather than a
   failure.

## 2. The design

**Tasks.** Six small functions over a parameter literally named `records`, each of a shape that
*invites* a superlinear implementation without requiring one: find duplicate pairs, count matches
per record, deduplicate, join against a second collection, take the top by a key, and summarise.
Every one has a linear or near-linear answer, so a fault is a choice rather than a trap.

**Two arms, paired by generation.** Both arms share the model's **first** completion, so the pair
differs in exactly one thing: whether the mechanism's finding was delivered.

| arm | what happens |
|---|---|
| **off** | the model writes the function; that is the answer |
| **on** | the checker runs on that same completion under the declared axis; **if it fires**, the finding is handed back and the model writes the function again |

When the checker does not fire, the two arms are the same code and the pair is concordant by
construction. That is the honest structure — the mechanism cannot help where it is silent — and it
is why the analysis is exact McNemar on discordant pairs rather than a difference of proportions.

**The judge is dynamic, and that is the whole point.** Scoring a static checker with a static rule
would be the `ABSTENTION_F1.md` §1 circularity again: the ON arm is optimised against the judge and
wins by construction. So the outcome is decided by **`scale.canary`** — run the generated function
at *n* and at *10n*, and ask whether time or memory grew faster than the input. It observes the
program running; the mechanism reads it; they cannot agree by construction.

`find_problems`'s own verdict is recorded next to it and is **not** the gate.

**Containment.** Generated code is executed, so: no execution unless the AST is free of `import`
statements other than a small allowlist, and free of `eval`, `exec`, `__import__`, `open`, and any
attribute of `os`, `sys`, `subprocess`, `shutil`, `socket` or `pathlib`. Every run happens in a
subprocess with a hard timeout. A completion that fails the gate is **unusable**, not clean.

## 3. What counts, and what is refused

**Ungraded is not correct — for the fifth time.** T3 had to learn this three times in three
harnesses; a fourth turned up in F3 (a `# noqa` silencing the instrument). Here, every one of these
is **unusable and excluded with a reported count**, never a clean pass:

- an empty or truncated completion;
- code that does not parse;
- code with no function in it;
- code the containment gate refuses;
- a function that raises or times out on the synthetic input;
- **a canary that abstains** — `scale.canary` reports `abstained=True` when the base run is too
  fast to time, and `SCALE_X4` already found 12 of 40 "misses" were abstentions scored as failures.

**The outcome, per usable generation:** the canary says time or memory grew more than 3× faster
than the input at a 10× step. That budget is `scale.canary`'s default, sitting between linear
(1×) and quadratic (10×), and it is not tuned here.

## 4. Sample size — computed before the stand, not after it

The analysis is **exact McNemar** on paired outcomes. What it needs is *discordant* pairs, and the
number of trials that produces them depends on two rates nobody knows yet:

- **b**, the base rate: how often the off arm writes a fault;
- **f**, the fix rate: of those, how often the finding causes the model to write a linear version.

An exact binomial sign test on discordant pairs needs **6 discordant one way, 0 the other** to
reject at α = 0.05 (two-sided p = 2 × 0.5⁶ = 0.031). So:

> **trials ≈ 6 / (b × f)**

| base rate `b` | fix rate `f` | trials needed |
|---|---|---|
| 0.50 | 0.80 | 15 |
| 0.30 | 0.80 | 25 |
| 0.20 | 0.60 | 50 |
| 0.10 | 0.60 | 100 |
| 0.05 | 0.60 | 200 |
| 0.05 | 0.30 | 400 |
| **0.00** | anything | **∞ — the stand cannot resolve anything** |

**Declared here, before the probe:** the powered stand runs only if the probe's base rate implies
**≤ 400 trials per model**. Above that, F5 reports the refusal and the arithmetic, exactly as
`GOAL-SHIP.md` asks. Below it, the stand runs at the implied count, computed from the probe's
observed base rate and a conservative fix rate of **0.60**.

The probe itself is sized to distinguish a base rate of 0.30 from 0.05 — an exact binomial needs
about 30 generations for that — so **the probe is 6 tasks × 5 trials = 30 per model**.

## 5. Models

Local Ollama only; nothing billable. Free VRAM is checked before each load and the run refuses
rather than colliding with the owner's own work.

| model | why |
|---|---|
| `qwen2.5:7b` | not a thinking model, so T3's thinking-token budget leak cannot recur |
| `qwen3-coder:30b` | a coding model, and the strongest available: **if the base rate is zero on the model people would actually use, that is the finding**, exactly as T3's `qwen3.5:4b` result was |

T3's headline came from the *stronger* model having a base rate of 0.000. Running a strong model
here is therefore not optimism — it is the arm most likely to end the experiment early, and it is
run first for that reason.

---

## 6. The probe, and the harness defect it exposed first

**The first probe abstained on half of every generation** — 15 of 30, on both models — and not at
random: `dedupe`, `summarise` and `top_by_score` have answers so fast they never reached the
canary's 5 ms measurement floor within its size ceiling. Three of six tasks contributed nothing,
and the stand would have been silently halved *and* biased toward the slow half.

The ceiling was raised to `scale.canary`'s own default and the second probe had **zero** unusable
generations. **Fifth time in this project that an instrument which could not answer had to be
stopped from being read as an instrument that answered "fine"** — three in T3, a `# noqa` silencing
`ruff` in F3, and now this.

| probe | usable | unusable | why |
|---|---|---|---|
| first | 30 / 60 | 30 | canary abstained, entirely on three of the six tasks |
| second | 60 / 60 | 0 | — |

## 7. The base rate, and the arithmetic that ends the experiment

Probe, 30 generations per model:

| model | base rate | trials needed at fix rate 0.60 |
|---|---|---|
| `qwen2.5:7b` | **0.000** [0.00, 0.11] — 0 of 30 | **infinite** |
| `qwen3-coder:30b` | 0.067 [0.02, 0.21] — 2 of 30 | 150 — within the 400 budget |

The declared rule said the second arm was resolvable, so **the stand ran at 150 generations**.

| | `qwen3-coder:30b`, 150 generations |
|---|---|
| usable | **150** — none unusable |
| **base rate** | **0.013** [0.00, 0.05] — 2 of 150 are superlinear |
| the checker fired | 25 of 150 |
| **fault *and* checker fired** | **0** |
| **discordant pairs** | **0** — 0 fixed, 0 broken |
| trials needed at the realised base rate | **750** |
| budget declared in §4 | 400 |

> **REFUSED.** The stand cannot resolve the declared effect at any trial count this run will pay
> for, and F5 stops here with the arithmetic — which is exactly the exit `GOAL-SHIP.md` wrote for
> it, and exactly what T3 did not do before spending 96 generations per model.

## 8. Why it fails, and it is not T3's reason

T3's answer was portability: *what ships in the box has to be general, and general is what training
already covers.* A declared axis escapes that argument completely — `records: 1_000 -> 50_000_000`
is project-specific and in no model's weights. It still does not help, and the reason is different
and more fixable.

**Both faults are the same code, and the detector cannot see it:**

```python
def duplicate_pairs(records):
    pairs = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            if records[i]['email'] == records[j]['email']:
                pairs.append((i, j))
    return pairs
```

Time grew **16.9×** and **16.3×** faster than the input. Static findings: **zero.** The inner loop
walks `range(i + 1, len(records))`, whose first argument is an expression, so it resolves to no
axis at all and never matches the outer one. `find_problems` recognises `for a in records: for b in
records:` and nothing else that means the same thing.

Asked directly, the detector sees **one of six** ordinary ways to write a pairwise scan or an
accidental quadratic — the other five are pinned as known gaps in `tests/_test_scale.py`:

| shape | seen? |
|---|---|
| `for a in records: for b in records:` | **yes** |
| `for i in range(len(records)): for j in range(i+1, len(records)):` | no |
| `for i, a in enumerate(records): for j in range(i+1, len(records)):` | no |
| `for i, a in enumerate(records): for j, b in enumerate(records[i+1:], i+1):` | no |
| `if r['id'] not in seen:` against a list that grows | no |
| `out += str(r)` in a loop | no |

**The mechanism did not fail because a declared axis is the wrong idea. It failed because its
catalogue of shapes does not contain the shape the model writes** — and this is the same finding
F4 reached from the other direction, where 0 of 7 quadratic fixes made by real maintainers were of
a shape it recognises. Two independent routes, one conclusion.

## 9. The 25 firings, which are not demonstrated false positives

All 25 are `top_by_score` writing `sorted(records, key=…)[:k]`, and the canary clears every one:
time factors 1.2 to 1.7, well under the 3.0 budget.

That does **not** make them wrong. `sorted()` is O(n log n) in time and **O(n) in memory**, and the
declared axis goes to 50,000,000 records. A growth test that steps from n to 10n around n ≈ 10⁵
cannot condemn a linear-memory materialiser; it is not the question that instrument asks.

So the honest statement is: **the dynamic judge and the static checker disagree here because they
answer different questions** — "did cost grow faster than the input?" against "will this fit in
memory at the declared target?" Calling the 25 false positives would be reading the judge past its
competence, which is the mistake this project has spent the run trying not to make. They are
recorded as *unadjudicated*.

## 10. What F5 leaves

**The cold-start claim is not rescued.** Two experiments, two mechanisms, two different reasons:

| | mechanism | why it did not help |
|---|---|---|
| T3 | a portable pitfall pack | there was almost nothing to prevent — portable knowledge is what models already have |
| **F5** | a project-specific declared axis | there was almost nothing to prevent **on these tasks and this model** (base rate 0.013), and on the two occasions there was, **the mechanism could not see it** |

**What is named and unbuilt**, and it is the same item F4 named: **the catalogue of shapes**. An
index pair scan, a growing-list membership test, and accumulation in a loop are all statically
visible and none is implemented. Until they are, no experiment on this axis can produce anything
but this result, and running one would be spending trials to rediscover a gap that a six-case unit
test finds in a second.

**What would make a next attempt worth running**, stated concretely so it can be checked:

1. the catalogue covers at least the six shapes in §8's table;
2. a base-rate probe on the intended model shows ≥ 0.10, measured before any stand is built;
3. the judge is dynamic *and* memory-aware, so a materialiser at the declared target can be
   adjudicated rather than recorded as unadjudicated.
