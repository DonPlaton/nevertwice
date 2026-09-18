# The gate that decides shipping, measured out of sample: not met

**Task G-C, re-run on the held-out corpus.** The in-sample stand was capped at 53 tasks and
produced 3 discordant pairs — an absence of measurement rather than a result. The held-out corpus
supplies **2,623 source commits**, the task pool is **514**, and the stand ran at **150 benefit and
150 harm trials** against thresholds fixed in `PREREGISTRATION-SHIP.md` before the corpus existed.

```bash
python research/invariants_lab/measure_endtoend.py --corpus heldout --print
```

Artifact: `endtoend_e_heldout.json`. Model `qwen3-coder:30b`, policy `decidable-only`.

---

## The verdict

| | declared | measured | |
|---|---|---|---|
| **net benefit** | exact McNemar p < 0.05 | **p = 0.453**; 5 fixed, 2 broken, 7 discordant | **not met** |
| **harm** | ≤ 0.05 | **0.013** (3 of 234) | **pass** |

**G-C is not met**, so `GOAL-SHIP.md` §1 stands: nothing ships. The owner's rule — ship only if the
polygon result is positive — returns **do not push**.

## This is a negative result, not a missing one, and the difference is the point

The in-sample stand could not have detected anything: 3 discordant pairs, and an exact sign test
needs 6 in one direction. This one **was adequately powered by its own declared arithmetic** —
`trials_needed` was 37 usable and the run produced **87**.

| | in sample | out of sample |
|---|---|---|
| task pool | 53 | **514** |
| usable benefit trials | 46 | **87** |
| base rate (off arm fails) | 0.174 | **0.276** [0.193, 0.378] |
| discordant pairs | 3 | 7 |
| McNemar p | 1 | 0.453 |

**What it actually did:** the off arm failed 24 times of 87; the on arm failed 21. Three fewer
failures out of eighty-seven. The mechanism fired on 57 of 150 trials, so it was not silent — it
spoke often and the speaking did not reliably help.

**Five fixed against two broken is the shape that matters.** The effect is not zero and it is not
one-directional: delivering a true finding sometimes causes a repair that removes a definition the
file needed. A mechanism that fixes five and breaks two at a flag rate of 38% is not one to put in
front of a person on this evidence.

## Harm passes, and it stopped being zero

| | in sample | out of sample |
|---|---|---|
| fired on correct code | **0 of 53** | **2 of 150** |
| false-flag repair | 0 | 1 |
| of which broke it | 0 | 0 |
| harm rate | 0.010 | **0.013** (ceiling 0.05) |

The in-sample claim was that the mechanism is *perfectly* silent on correct code. Out of sample it
is not — it fired twice on trees where every call already binds, and once that firing caused an
agent to change code that was correct. Nothing broke, and the rate clears the ceiling comfortably.

**This is the same pattern as every other number in Phase V:** recall travels, silence degrades.
`HELDOUT_V.md` measured it on corpora; this measures it on an agent's behaviour, and it points the
same way.

## The limitation that must be read with the verdict

**63 of 150 benefit attempts were unusable, and 92 of the 300 attempts across both arms failed with
`harness: TimeoutError`.** The cause is infrastructure, not the mechanism: the 30B model was
cycling between loaded and unloaded on a GPU sitting at 98% of its memory, so a trial that should
take about a minute intermittently took fifteen or timed out.

That does not make the run underpowered — 87 usable exceeds the 37 the preregistration required.
It does mean **the 87 are not a random subsample**: a timeout correlates with the larger, slower
inputs, so the surviving trials are biased toward smaller files. Whether the lost third would have
moved 5-versus-2 is unknown.

**What that licenses and does not license.** It licenses the verdict *on this evidence*, which is
what a gate is for, and it forbids the stronger claim that the mechanism has been shown not to
work. A rerun on an unloaded GPU is cheap, is the obvious next step if anyone wants to overturn
this, and is named here rather than left for a reviewer to notice.

Other unusable trials are the model's own doing and are counted honestly rather than excluded
quietly: 11 outputs did not parse, and 10 lost definitions the file needed — deleting the call site
is not a fix, and the judge refuses it.

## What survives

`blast_radius` under `decidable-only` remains the only mechanism in this project to pass an
out-of-sample gate: recall 0.597, flag rate 0.016 against a 0.05 ceiling, and a 25-point margin
over `git grep` at a matched flag rate (p = 5.9 × 10⁻⁶⁹). **Those retrieval numbers are unaffected
by this page.** What this page settles is the different and harder question of whether delivering
those findings to an agent makes its work measurably better, and on 87 paired trials it does not.
