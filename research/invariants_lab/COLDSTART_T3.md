# Cold start: the pack fires from minute zero, and there is nothing there to catch

<!-- withdrawn-banner -->
> **Withdrawn: figures on this page must not be quoted.** They were retracted in 2026-08
> and remain here because deleting a result one was wrong about destroys the record of
> having been wrong. The design, the method and the caveats stand; the numbers do not.
> Each figure's own reason is in
> [`research/evidence_manifest.json`](../evidence_manifest.json), and
> `python tools/check_freshness.py --list-stale` lists every one.

**Task T3.** The headline axis, and it fails on both models — for a reason that is the most
interesting thing this run found.

```bash
python research/invariants_lab/measure_coldstart.py --model qwen3.5:4b --trials 8
python research/invariants_lab/measure_coldstart.py --print
```

Artifacts: `coldstart_t3_qwen35_4b.json`, `coldstart_t3_qwen25_3b.json`. Threshold:
[`PREREGISTRATION.md`](PREREGISTRATION.md) §8.

---

## The verdict

| model | base pitfall rate (pack off) | with the pack on | McNemar | |
|---|---|---|---|---|
| `qwen3.5:4b` | **0.000** [0.000, 0.038], 0/96 | 0.000 | undefined — no discordant pairs | **fail** |
| `qwen2.5:3b` | **0.052** [0.022, 0.116], 5/96 | 0.031 [0.011, 0.088], 3/96 | 2 fixed, 0 broken, p = 0.5 | **fail** |

96 usable generations per model, zero unparseable, zero errors. Every trial paired by task.

## Why it failed, which is not "the invariant does not work"

**There was almost nothing to prevent.** The stronger model made **none** of the three mistakes in
96 attempts, on tasks written to invite them. The weaker one made five, all `mutable_default`, and
the invariant fixed **two of five and broke none** — directionally exactly right, and nowhere near
enough to resolve.

The stand was **underpowered by construction, and the arithmetic says by how much**. An exact
McNemar needs **6 discordant pairs all in one direction** to reach p < 0.05. At the observed fix
rate of 2 in 5, that is **15 errors**, which at a base rate of 0.052 needs **288 trials** — three
times what was run. That number is stated because C3's whole point was that "how many does it
take" is answerable in advance, and it was not asked for this gate before the run.

But enlarging the stand would only move the second row. The first row cannot be fixed by more
trials: **0 of 96 with an interval of [0.000, 0.038]** is not a small effect awaiting resolution,
it is the absence of anything to detect.

## The finding underneath the failure

> **A preconfigured invariant can only help where the model is wrong. What ships in the box has to
> be portable, and portable knowledge is the knowledge models already have.**

That is a tension in the cold-start thesis, not a defect in the implementation, and it is the same
wall `research/LIVE_VALIDATION.md` hit from the other side:

> *On generic pitfalls the model was trained on, base error rate is ~0. A strong model needs no
> memory for textbook mistakes.* … *Memory's value is project-specific knowledge, and that is
> exactly where it fires* — on invented API constraints no model could know from training, where
> it fixed three of four failures.

Those two results are the same result. A scar is useful **because** it is unportable: nothing in
training contains "`authenticate()` must precede `connect()`" in *this* codebase. An invariant that
ships in the box must be true of Python everywhere, and a rule true of Python everywhere is in
every model's weights already.

**So cold start is real and the cold-start advantage is not free.** The pack does fire from minute
zero on a repository with no history — `I3` proves that, and it costs nothing when quiet. What T3
shows is that firing from minute zero is only worth something if the thing it fires about is
something the model would otherwise get wrong, and the three most portable Python pitfalls are not
that.

## What a next attempt must change, and it is not the pack

1. **Find portable-but-unlearned shapes, or accept that there are few.** The pack's three were
   chosen for portability, and portability was exactly the wrong axis to optimise. Candidates that
   might survive: properties of *this project's* declared configuration (the scale axis of X4 is
   one — `records: 1_000 -> 50_000_000` is a fact no model can know), rather than properties of
   the language.
2. **Measure against a base rate before building.** T3's gate was declared without asking what
   base rate it needed, which is the one C3 discipline this run failed to apply to itself. The
   number is 288 trials at a 5% base rate, and it was computable before a line was written.
3. **The strongest form of the claim now runs through X1.** A declared growth axis is
   project-specific, checkable from minute zero, and not in any model's weights. That is the
   cold-start experiment worth running next, and this run did not run it.

## Two defects in this harness, found and fixed before any number was believed

**Every response was empty.** `qwen3.5:4b` is a thinking model; it spent the whole 400-token
budget in `thinking`, `done_reason` came back `length`, and `response` was the empty string.
Thinking is now off and the budget is 700.

**And an empty answer scored as a clean pass.** `ast.parse("")` succeeds and finds no pitfall, so
every truncated completion counted as the model getting it right — a base rate of zero produced by
a model that had not written a line. The judge now returns *ungraded* for any answer containing no
function definition.

The first run of this file reported base rate 0.000 for both reasons at once, and the two errors
would have cancelled into a confident, wrong headline. **Ungraded is not the same as correct** —
the third time this run has had to write that sentence, after the canary's abstentions in X4 and
the undecidable findings in D5.

## What the judge is and is not

`judge()` re-derives each pitfall with an AST predicate written in this file, not imported from
`preconfigured`. The two look for the same *shape* — they must, or the experiment would be about
nothing — but through different code, so a bug in the pack cannot score itself correct.

The honest claim is therefore about **delivery**: does handing a model this constraint change what
it writes? On the five occasions there was anything to change, twice. That is not independent
evidence that the constraint is worth having, and this page does not offer it as such.

## Courtesy, recorded

Run against the owner's already-running Ollama, read-only: no model pulled, nothing started or
stopped, and the requests are the same shape their own hook makes. 28 GB of VRAM free at the
start; the two models used are 3.4 GB and 1.9 GB and were already resident.
