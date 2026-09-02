# Finishing four half-built mechanisms, and what finishing them showed

<!-- withdrawn-banner -->
> **Withdrawn: figures on this page must not be quoted.** They were retracted in 2026-08
> and remain here because deleting a result one was wrong about destroys the record of
> having been wrong. The design, the method and the caveats stand; the numbers do not.
> Each figure's own reason is in
> [`research/evidence_manifest.json`](../evidence_manifest.json), and
> `python tools/check_freshness.py --list-stale` lists every one.

**The `invariants/v3` run.** `invariants/v2` measured six mechanisms on a corpus built for the
purpose, deleted two, kept two, and closed with a list of four things that were **named and not
built**. This run built them. The owner's rule decided what happens next: *if the mechanisms are
fully implemented and they improve the memory's work, we ship; if they are not finished, finish
them; if they then do not help, we do not ship.*

```bash
python research/invariants_lab/measure_abstention.py --print    # F1
python research/invariants_lab/measure_surface.py    --print    # F2
python research/invariants_lab/audit_axes.py         --print    # F3
python research/invariants_lab/mine_quadratics.py    --print    # F4
python research/invariants_lab/measure_declared_axis.py --print # F5
python research/invariants_lab/measure_endtoend.py   --print    # E
```

---

## The one-line summary of each

| task | the named missing piece | what building it showed |
|---|---|---|
| **F1** | `blast_radius` emitted 70% of findings it could not decide; abstention was never built | flag rate **0.278 → 0.010**, recall 0.910 → 0.600, and the margin over `git grep` **doubles** |
| **F2** | scope it to a *declared* surface, the way `scale` is scoped | the declared surface **does not exist in found history**; conventions cost recall in proportion to what they remove |
| **F3** | three ratchet axes had no independent instrument | all three now have one, all agree exactly — and **the flag rate does not move** |
| **F4** | `scale`'s positives were written by the detector's author | **0 of 7** real quadratic fixes are a shape it recognises |
| **F5** | X1's cold-start experiment was never run | base rate **0.013**; the mechanism sees **1 of 6** ways to write the fault |

## Finding 1 — abstention is the lever, and only one mechanism could use it

`BLAST_RADIUS_D5.md` deleted the checker for talking too much: it fired on 27.8% of commits that
broke nothing, against a 5% ceiling, while finding 91% of genuine stale callers. The diagnosis it
left was precise — *91% of what it says on the silence pool is undecidable, and it reports every
one* — and the remedy was untried.

Built as a ladder of six nested policies, declared before the run:

| policy | recall | flag rate | `git grep` at the same flag rate |
|---|---|---|---|
| `emit-all` | 0.910 | 0.278 | 0.826 |
| `no-bare-mentions` | 0.910 | 0.239 | 0.826 |
| `no-star-calls` | 0.910 | 0.225 | 0.826 |
| `no-unknown-receiver` | 0.609 | 0.143 | 0.368 |
| `no-kind-changes` | 0.609 | 0.141 | 0.368 |
| **`decidable-only`** | **0.600** | **0.010** | **0.296** |

Two things in that table are worth more than the headline.

**The recall bill and the silence are bought at different rungs.** P3 costs 0.301 of recall and
takes the flag rate from 0.225 to 0.143. P5 costs 0.009 and divides it by fourteen. Nearly a third
of real breakages are reachable only through a receiver the file does not bind —
`backend.deliver_now(1)` where `backend` is a parameter — and no static route reaches them. That
is a fact about Python, not about this checker.

**Abstention widens the gap over the cheap baseline instead of narrowing it.** At 27.8% the
checker beats `git grep` by 8.4 points of recall; at 1.0% it beats it by **30.4** (McNemar
p = 1.1 × 10⁻¹⁴). A regex cannot abstain. Its only route to quiet is a higher mention threshold,
and a threshold discards true and false findings in the same proportion, while an abstention rule
discards only what it cannot decide.

**And the lever is not general.** F3 audited the complexity ratchet's four axes, found every one
of them agrees exactly with an implementation nobody here wrote, and the flag rate moved from
0.358 to 0.310 — still six times the ceiling:

> **Abstention is available to a checker that cannot decide. It is not available to one that can.**
> Every ratchet finding is decidable by construction: the metric really did go up, and two
> independent instruments now agree it did. There is nothing to abstain *from*.

## Finding 2 — the declared surface that makes `scale` quiet does not exist in found history

`scale` is the one mechanism that passed its gates on non-circular evidence, and it is quiet for
one reason: a project writes `records: 1_000 -> 50_000_000` **for the mechanism**, and the detector
speaks about that axis and nothing else. F2 asked for the same shape for contracts.

The census, run before anything was declared: of **859** confirmed breakages, `__all__` names the
changed symbol's module-level head for **18**. A checker scoped that way carries recall near 0.02
and reaches the ceiling by saying almost nothing.

The two scopings that *are* available are **conventions** — a leading underscore, a test-file
consumer — and they behave like filters uncorrelated with correctness:

| lever | recall cost | flag rate, from 0.278 |
|---|---|---|
| abstention alone | 0.310 | **0.010** |
| both conventions | 0.316 | 0.196 |

Almost the same bill, a factor of twenty apart. The two levers turn out to be **almost exactly
independent** — the combined flag rate is 0.196 against 0.196 predicted under independence — which
was measured rather than assumed.

> The version of F2 worth running is on a project that *writes* the declaration. Found history
> does not contain one, so nothing in either corpus can answer it.

## Finding 3 — a detector checked only against its author's own cases

`SCALE_X4.md` reported static recall **1.000** on a positive class the detector's author generated
in the same afternoon, and said so in its first paragraph. F4 mined eight whole repository
histories for quadratic fixes real maintainers made and named, confirmed them mechanically, and
ran the detector under a declaration generated **blind** from the parent commit.

**Zero of seven** confirmed fixes are of a shape the detector has a rule for. They are string
accumulation (4), a container that became a hash behind an unchanged membership test (3), and
accumulation replaced by `join` (1). Recall on the found class is 1 of 7 — and the one hit reports
5 findings on the parent and **6 on the child**, so it did not detect the fault; it reported the
same membership test in both trees.

F5 reached the same place from the other direction. Given a 30B coding model and six tasks over a
declared axis, the two generations a dynamic canary condemned as quadratic — time growing 16.9×
and 16.3× faster than the input — produced **zero** static findings, because both were written as

```python
for i in range(len(records)):
    for j in range(i + 1, len(records)):
```

Asked directly, the detector recognises **one of six** ordinary ways to write a pairwise scan or an
accidental quadratic. The other five are now pinned as known gaps in `tests/_test_scale.py`.

> The mechanism did not fail because a declared axis is the wrong idea. It failed because **its
> catalogue of shapes does not contain the shape the code has**.

## Finding 4 — the cold-start claim, from the other side

`COLDSTART_T3.md` closed with *"portable knowledge is the knowledge models already have"*. A
declared growth axis escapes that argument entirely: it is project-specific, in no model's weights,
and checkable on day one. F5 ran it, base rate first, and stopped on the arithmetic:

| | `qwen2.5:7b` | `qwen3-coder:30b` |
|---|---|---|
| base rate | **0.000** [0.00, 0.11] | **0.013** [0.00, 0.05] over 150 generations |
| fault *and* the checker fired | 0 | **0** |
| discordant pairs | — | **0 fixed, 0 broken** |
| trials needed | infinite | **750**, against a declared budget of 400 |

**Refused, with the arithmetic** — which is what T3 did not do before spending 96 generations per
model.

## The instrument lesson, now seven times

Every time an instrument was checked only against itself it was wrong; every time it was checked
against something written by someone else, the check found something.

| instrument | independent check | what it found |
|---|---|---|
| the mutation generator | CPython's `Signature.bind` | the answer key had the generator's own blind spot |
| this project's cyclomatic | `ruff`'s C901 | 4 errors, including a `def` inside a `try:` |
| the scale canary | its own abstentions | 12 of 40 "misses" were declining to answer |
| the cold-start judge | an empty response | truncation scored as a clean pass |
| the authority boundary | 15 attacks | 2 landed, on 2 different builds |
| **this project's `nesting` and `returns`** | **`ruff`'s `PLR1702` / `PLR0911`** | **an `elif` chain counted per branch; an `@overload` stub's metrics stored as a function's baseline; a `# noqa` silencing the instrument itself** |
| **`scale`'s axis resolution** | **a blind declaration over real code** | **`enumerate(rows)` and `enumerate(cols)` were the same axis** |

Two of those deserve naming twice. A **`# noqa` in `psf/requests` switched the instrument off**, so
the comparison scored a function as agreement because the control had produced nothing — the fourth
time this project has had to write *ungraded is not the same as correct*. And **F5's first probe
abstained on half of every generation**, entirely on three of six tasks whose answers are too fast
to time, which would have silently halved the stand and biased it toward the slow half. That is
five instances, in five harnesses.

## What could not be measured, and why that is not a result

`GOAL-SHIP.md` §0.3 named the problem this run existed to fix: every published number is **in
sample**, because the eight repositories were used both to find the defect classes and to score the
gates. Phase H was the answer — thirty repositories across six domains, cloned only after the code
was frozen.

**It did not run.** DNS resolves nothing on this machine: `gethostbyname` fails for `github.com`,
`pypi.org` and `example.com` alike, while raw TCP to a GitHub address connects. The local proxy
resolves the name, opens the tunnel, and its upstream returns no TLS handshake. Fixing that means
reconfiguring the owner's machine, which is out of bounds. `RATCHET_R3.md` recorded the same two
failures one day earlier.

So **G-B is unevaluable, which is not the same as failed**. Every threshold in
`PREREGISTRATION-SHIP.md` stands untested, the thirty repositories and their per-block predictions
were committed *before* the block was found, and every harness takes `--corpus heldout` — H3 is
four commands, not a code change.

## Finding 5 — the end-to-end gate splits, and one half of it is clean

The owner's rule is end-to-end: *does the memory's work get better with the mechanism in it?* Every
number before this one is mechanism-level, and none of them answers that.

The stand: one task is one real commit that changed a callable's parameter list and updated a
caller, with the caller half reverted. The agent gets the contract change and the file and must
return the corrected file. The **off** arm gets nothing more; the **on** arm gets what
`blast_radius` under `decidable-only` says. Both are judged by replaying every call against the new
definition with CPython's binding rule — on the agent's *output*, identically in both arms.

53 benefit trials, 53 harm trials, `qwen3-coder:30b`. **In sample.**

| | |
|---|---|
| **harm rate**, ceiling 0.05 | **0.010** — **passes** |
| the mechanism fired on **correct** code | **0 of 53** |
| a false flag causing an agent to change correct code | **0** |
| fixed / broken / ignored | 2 / 1 / 17 |
| discordant pairs | **3**, exact McNemar **p = 1** |

**The harm half is the cleanest result in the run.** A mechanism that never speaks about correct
code cannot cause a repair to correct code, and the arm built to catch that is empty. F1 measured
this silence on a pool of commits; this is the first observation of it on files an agent was about
to edit.

**The benefit half is inconclusive, and the reason was written down first.** Three discordant pairs
cannot reject at α = 0.05 under any arrangement — an exact sign test needs six one way. The design
document computed, before the probe, that a 53-task pool needs a base rate of at least **0.189**;
the measured base rate is **0.174**.

> 2 fixed against 1 broken is the direction the mechanism claims and nowhere near enough to assert
> it. **Harm passes; benefit is inconclusive, not negative.**

## The verdict

**NO-GO** — [`VERDICT_G1.md`](VERDICT_G1.md). Not because a mechanism was measured and failed:
because **G-B could not be evaluated at all** and **G-C's benefit half is inconclusive**. Two of
three gates did not pass, and `GOAL-SHIP.md` §1 requires all three.

Nothing was promoted into the package. `nevertwice/invariants/` stays empty, as `T4` left it.

## What a next attempt should carry

1. **Abstention is the finding.** It is the only lever that moved the flag rate by an order of
   magnitude, and it works *because* the checker has an "I cannot decide" state to exploit. Any new
   detector should be designed with one from the start, and a detector whose findings are all
   decidable — like the ratchet — has no route to silence and should not be attempted.
2. **A catalogue of shapes beats a clever rule.** `scale`'s static half sees one of six ways to
   write a pairwise scan; F4 and F5 established that independently. Accumulation in a loop and
   membership against a linear container are both statically visible and neither is implemented.
3. **Measure the base rate before building the stand.** F5 stopped after 30 generations instead of
   96 per model; Phase E predicted its own underpowering before it ran. Both cost an afternoon and
   saved a week.
4. **Nothing is out of sample yet.** Thirty repositories, six domains and a per-block prediction are
   committed and waiting on one working DNS resolution. Until Phase V runs, every number in this
   project is a number about eight repositories.
