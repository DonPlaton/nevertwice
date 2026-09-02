# Out of sample: recall generalises, silence does not, and one mechanism survives

<!-- withdrawn-banner -->
> **Withdrawn: figures on this page must not be quoted.** They were retracted in 2026-08
> and remain here because deleting a result one was wrong about destroys the record of
> having been wrong. The design, the method and the caveats stand; the numbers do not.
> Each figure's own reason is in
> [`research/evidence_manifest.json`](../evidence_manifest.json), and
> `python tools/check_freshness.py --list-stale` lists every one.

**Tasks V1-A, V1-B, V1-C, V2.** Twenty-seven repositories the code never saw, 6,868 mutants over
2,623 source commits, every threshold declared in `PREREGISTRATION-SHIP.md` before the first clone
and every measurement run once against frozen code.

Provenance of each artifact is established in [`PROVENANCE_V.md`](PROVENANCE_V.md) from the data
rather than the labels — three of them call themselves in-sample and are not.

```bash
python research/invariants_lab/analyse_v.py --print
python research/invariants_lab/run_phase_v.py --print
```

Artifact: `v_delta.json`.

---

## The verdict

| gate | declared | measured out of sample | |
|---|---|---|---|
| **V1-A** recall | ≥ 0.45 | **0.597** [0.578, 0.616], n = 2,595 | **pass** |
| **V1-A** silence-pool flag rate | ≤ 0.05 | **0.016** [0.010, 0.026], p = 2.4 × 10⁻⁸ | **pass** |
| **V1-A** beats `git grep`, matched | McNemar p < 0.05 | **0.597 vs 0.344**; 1,053 / 396; p = 5.9 × 10⁻⁶⁹ | **pass** |
| **V1-B** agreement with `ruff` | ≥ 0.50 | **0.755** [0.716, 0.790] | **pass** |
| **V1-B** discrimination | Fisher p < 0.01 | 0.755 vs 0.268 | **pass** |
| **V1-B** silence | ≤ 0.05 | **0.522** | **fail → deleted** |
| **V1-C** blind-declaration flag rate | ≤ 0.05 | **0.184** | **fail** |
| **V1-C** recall on mined fixes | ≥ 0.30, scored if n ≥ 20 | **0.059** [0.031, 0.109], n = **152** | **fail → static half deleted** |
| **V2** union flag rate | ≤ 0.05 | **0.191** [0.168, 0.217] | **fail** |

**`blast_radius` under `decidable-only` is the first mechanism in this project to pass a declared
gate on a corpus it was not built against.** Everything else that was measured against a silence
ceiling failed it, again, and by more.

## The headline: which numbers travel

| | in sample | out of sample | |
|---|---|---|---|
| `blast_radius` recall | 0.9101 | **0.9091** | −0.001 |
| `decidable-only` recall | 0.6000 | **0.5973** | −0.003 |
| `blast_radius` precision | 0.3961 | **0.2742** | **−31%** |
| ratchet flag rate | 0.3640 | **0.5220** | **+43%** |
| `scale` flag rate | 0.0989 | **0.1835** | **+86%** |
| union flag rate | 0.1190 | **0.1910** | **+61%** |

**Recall reproduces to the third decimal on repositories chosen to be as unlike the development
set as the selection criteria allowed. Every flag rate gets worse, most of them by half again.**

That is not a mechanism behaving inconsistently. Recall is a property of the *mechanism* — given a
call that cannot bind, does the reference analysis find it — and it turns out to be close to
invariant across django and numpy alike. Precision and flag rate are properties of the
*corpus*: how much code there is, how much of it the checker cannot decide about. The next section
shows what governs that, and it is not subtle.

## V2's exploratory model: repository size governs precision, and nothing else measured does

`BLAST_RADIUS_D5.md` found precision ranging 0.174 to 0.836 across eight blocks and concluded
"whatever governs precision is a property of the codebase, not of the checker". Eight blocks
could not test that. Twenty-seven can, and precision now spans **0.05 to 0.968 — nineteen-fold**.

| predictor | Spearman ρ | p |
|---|---|---|
| **size on disk** | **−0.620** | **0.00056** |
| Python commits | −0.516 | 0.0059 |
| total commits | −0.515 | 0.0060 |
| contributors | −0.388 | 0.046 |
| Python-commit share | −0.328 | 0.094 |
| mutants in the block | −0.291 | 0.14 |

The first three are **three spellings of size**, not three findings, and `v_delta.json` says so in
the artifact rather than only here. Contributors — the property C1 selected repositories *for* —
is the weakest of the significant ones.

**The domain effect is size wearing a label.** Grouping by domain looks decisive: `async` blocks
have median precision 0.796, `data` blocks 0.220, Kruskal–Wallis H = 12.88, p = 0.025. Residualise
precision on log₁₀(size) first and the same test gives **H = 4.92, p = 0.43**. Domain contributes
nothing once size is accounted for; the `data` and `scientific` blocks are numpy, pandas and scipy,
which are simply the biggest repositories in the corpus.

That check was written into the analysis before its result was seen, because reporting "domain
separates precision" without asking whether domain is size in disguise would be the same error as
scoring a ratchet against its own metric.

**The consequence for anyone quoting a precision number, including this project:** an in-sample
precision figure is a statement about the size distribution of the corpus it was measured on. The
development eight — flask, requests, httpx, fastapi, pytest, scrapy, sphinx, django — are small to
middling. 0.396 was never a property of the checker.

## V1-A · the mechanism that survived, and what it cost

`decidable-only` emits only findings it can prove: a call that cannot bind, an import that
vanished. Everything it cannot decide becomes silence rather than output.

| | in sample | out of sample |
|---|---|---|
| recall | 0.600 [0.547, 0.650] | **0.597** [0.578, 0.616] |
| silence-pool flag rate | 0.010 | **0.016** |
| `git grep` at a matched flag rate | 0.296 | **0.344** |
| **margin over grep** | +0.304 | **+0.253**, p = 5.9 × 10⁻⁶⁹ |
| findings kept on the silence pool | 20 | **67** |
| undecidable findings kept | **0** | **0** |

The flag rate rose by six thousandths and stayed a factor of three under the ceiling. The margin
over a regex narrowed by five points and remains overwhelming: 1,053 commits the checker caught
and grep did not, against 396 the other way.

**Why a regex cannot follow it there.** `git grep`'s only quiet is a higher mention threshold —
seven, out of sample — and a threshold discards true and false findings in the same proportion.
Abstention discards only the undecidable, which is why the gap widens as both get quieter rather
than closing.

**The construct-validity bound stands, unchanged and stated again.** The answer key's positive
class is exactly {a call that cannot bind, an import that vanished}, and `decidable-only` emits
exactly those two shapes. 0.597 is recall *among breakages a static reader can prove*. Nothing here
measures what share of all real breakages that is, and no public sentence about this number may
omit it.

## V1-B · the ratchet got more right and louder at the same time

| | in sample | out of sample |
|---|---|---|
| agreement with `ruff` where complexity rose | 0.637 | **0.755** [0.716, 0.790] |
| fires on `ruff`-flat diffs | 0.200 | 0.268 |
| discrimination ratio | 3.19× | 2.82× |
| **flag rate** | 0.364 | **0.522** |

Its substantive claim **strengthened**: on a corpus where an independent tool says complexity rose,
it now agrees three times in four instead of two in three. Its usability claim collapsed further —
**more than half of all commits** would produce a finding.

The held-out corpus is also harder in a way worth recording: `ruff` says complexity rose on 52.2%
of sampled diffs there against 37.5% on the development set. Bigger, busier repositories churn
more complexity per commit. The ratchet is not wrong about that. It is unusable *because* it is
right about it, everywhere, all the time.

**Deleted, per the declared consequence.** The metric itself is not what failed — it agrees exactly
with `ruff` on 54 of 54 modules — and that agreement is worth keeping for whatever is built next.

## V1-C · `scale`'s static half, finally powered, and it fails

F4 could only mine **7** confirmed quadratic fixes from eight repositories, below the 20 the
preregistration required to score. Twenty-seven repositories yield **152**, from 213
message-matched candidates with 61 rejected on reading.

| | |
|---|---|
| confirmed real quadratic fixes | **152** |
| of a shape the detector has a rule for | **2** |
| recall on the found class | **0.059** [0.031, 0.109] |
| recall on the generated class (`SCALE_X4.md`) | **1.000** |
| **still fires after the fix** | **77 of 152 — 50.7%** |

The last row is the one that settles it. **Half of everything it does find, it finds equally on the
repaired tree** — so it is responding to the shape of the file, not to the fault. A generated
positive class has no paired arm and could never have shown that.

Recall 0.059 against a 0.30 floor, on a class ten times the size the preregistration asked for.
**The static half is deleted.** The canary and the declared-axis premise are untouched: the canary
separates quadratic from linear behaviour 40 out of 40, out of sample as in, and it shares no rule
with the static detector.

What the taxonomy says the corpus actually contains: accumulation in a loop (161), container
became a hash (36), something else (131), nested-walk removed (2), accumulation replaced by `join`
(1). The detector has a rule for the rarest two.

## V2 · the union, and the subset that stopped passing

| union | in sample | out of sample |
|---|---|---|
| all four | 0.119 | **0.191** |
| the three heuristics without `scale` | **0.049 — passed** | **0.055 — fails** |
| `assert` + `bare_except` | 0.047 | **0.045** [0.034, 0.060] |
| `scale` alone | 0.073 | 0.153 |

**T1's largest passing configuration does not survive.** It passed in-sample at 0.049 against a
0.05 ceiling — a margin of one thousandth — and out of sample it is 0.055. The largest subset that
holds is `assert_in_shipped_code + bare_except` at 0.045, and its interval still contains 0.05.

A gate cleared by a thousandth was never cleared. That is worth more than the number: it is a
demonstration that in-sample margins of that size carry no information, and this project produced
one and reported it as a pass.

**What was not measured, and should have been.** The union contains `scale` and three heuristics.
It does **not** contain `decidable-only`, because when T1 ran in-sample `blast_radius` had already
been deleted. Out of sample `decidable-only` is the only mechanism that passed. The union that
matters now — the surviving mechanisms together — has never been run, and its two halves were
measured on different pools, so it cannot be assembled arithmetically from what exists. It is one
command and it is the obvious next measurement.

## What this costs the previous methodology

`GOAL-SHIP.md` §0.3 named the problem: every published number was in sample. That sentence is now
false, and the price of the old way is legible:

- **precision was overstated by a third**, and the mechanism of the overstatement is identified —
  a corpus of small repositories;
- **a gate was reported as passed on a one-thousandth margin** and does not survive;
- **a mechanism scored 1.000 on its own author's examples scores 0.059 on real ones**, and fires
  on the fixed tree half the time it fires at all;
- and **recall was never the fragile number.** It reproduced to three decimals. Everything the
  previous methodology could safely have claimed, it could have claimed; everything it claimed
  about precision and silence, it could not.

## What was deliberately not done

No threshold was moved, no mechanism was retuned, and no frozen module was edited. The three
mislabelled artifacts are corrected in prose with evidence rather than by amending a hashed file —
see `PROVENANCE_V.md`. `power_ship_heldout.json` is left with undecided provenance rather than
inheriting it from its input.
