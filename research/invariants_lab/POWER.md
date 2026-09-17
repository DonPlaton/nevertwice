# What this corpus can and cannot see

<!-- withdrawn-banner -->
> **Withdrawn: figures on this page must not be quoted.** They were retracted and remain
> here because deleting a result one was wrong about destroys the record of having been
> wrong. The design, the method and the caveats stand; the numbers do not. Each figure's
> own reason and date are in
> [`research/evidence_manifest.json`](../evidence_manifest.json), and
> `python tools/check_freshness.py --list-stale` lists every one.

**Task C3.** The question the previous run never asked, and the reason it reached a verdict it
was not entitled to.

```bash
python research/invariants_lab/power.py
python research/invariants_lab/power.py --print
python tests/_test_invariants_lab.py     # the power function against a hand-computed value
```

Artifacts: `power.json` (current), `power_before_enlargement.json` (the corpus that failed).
Skill used: `statistical-power`.

---

## The question, and the answer to the one that names it

*How many positives does it take to tell a precision of 0.5 from a precision of 0.8?*

**Twenty.** At n=20 the two-sided exact binomial region against H₀ p=0.5 is `k ≤ 5 or k ≥ 15`,
size 0.0414, and under p=0.8 it carries **0.8042** of the mass. At n=19 it carries 0.6733. The
boundary is real, not rounded, and `tests/_test_invariants_lab.py` pins it against a value
computed by hand so a later refactor cannot quietly move it.

The comparison is *exact*, not normal-approximate. The approximation says 19. At the sample
sizes that matter here it is optimistic by roughly one observation in every direction, which is
exactly the margin by which an underpowered corpus gets waved through.

## What that means about the run this one is replacing

`research/BLAST_RADIUS_PRECISION.md` reported precision **0.000 on 24 findings**. So:

| what 24 findings could distinguish | verdict |
|---|---|
| precision 0.8 from a floor of 0.5 | **yes** — 20 needed |
| precision 0.7 from a floor of 0.5 | no — 43 needed |
| anything between 0.50 and 0.76 | **invisible** — 0.76 is the MDE at n=24 |

The 95% Wilson interval on a precision of 0.5 at n=24 runs **[0.31, 0.69]**, a width of 0.37.
So the old result is not wrong about what it measured — 0 of 24 is 0 of 24 — but the *corpus*
could only ever have separated a very good detector from a coin, and only if the detector fired
at least 24 times. That is the second half of why the verdict did not follow: not just an
all-negative answer key, but a resolution too coarse for the threshold it was checking.

## Nothing here is observed power

Every number below is a **minimum detectable effect at a fixed n**, or the n a stated effect
would require. Computing power from an effect already measured is a deterministic function of
its p-value and adds nothing. No mechanism has been measured yet, which is the entire reason
this task runs before Phase D.

## The positives are not the mutants

859 confirmed mutants sit in **345 source commits**. One `django` refactor contributes 66; the
median commit contributes one. Pooling them would be pseudoreplication, and the design effect
says how expensive:

| assumed within-commit correlation | design effect | effective n if all 859 were pooled |
|---|---:|---:|
| ICC 0.2 | 3.28 | 262 |
| ICC 0.5 | 6.71 | 128 |
| ICC 0.8 | 10.14 | 85 |
| ICC 1.0 (a detector that catches one catches all) | 12.42 | **69** |

Pooling 859 correlated mutants would leave an effective sample **five times smaller than the 345 clean
clusters**. `CORPUS.md` §2 declared one-mutant-per-commit before any of this was computed; this
table is what that rule was worth. **n = 345** governs every primary number.

## The verdicts

α = 0.05 two-sided, target power 0.80, exact binomial for one proportion, simulated exact
McNemar for the paired comparison (4,000 replicates).

| question | n available | n required | |
|---|---:|---:|---|
| recall 0.8 against a floor of 0.5 | 345 | 20 | **powered** |
| recall 0.7 against a floor of 0.5 | 345 | 43 | **powered** |
| recall 0.6 against a floor of 0.5 | 345 | 189 | **powered** |
| recall 0.9 against a floor of 0.8 | 345 | 100 | **powered** |
| recall 0.95 against a floor of 0.9 — X4's quadratic case | 345 | 229 | **powered** |
| flag rate 0.02 against a ceiling of 0.05 | 6,905 | 308 | **powered** |
| flag rate 0.03 against a ceiling of 0.05 | 6,905 | 778 | **powered** |

### The paired comparison against the baseline is the binding constraint

McNemar sees only the **discordant** pairs, so the correlation between the two arms matters more
than either marginal. `git grep` over the same symbol list is strongly correlated with the
detector by construction; assuming independence would flatter the design badly, so both
correlations are reported.

| detector | baseline | correlation | n required | power at n=345 |
|---:|---:|---:|---:|---:|
| 0.80 | 0.50 | 0.5 | 34 | 1.000 |
| 0.80 | 0.60 | 0.5 | 66 | 1.000 |
| **0.80** | **0.70** | **0.5** | **226** | **0.945** |
| 0.80 | 0.50 | 0.8 | 27 | 1.000 |
| 0.80 | 0.60 | 0.8 | 48 | 1.000 |
| 0.80 | 0.70 | 0.8 | 148 | 0.993 |

A detector only **ten points** better than `git grep` is the hardest thing this corpus is asked
to see, and 226 clusters is the price. That row is why the corpus was enlarged.

### Minimum detectable effects, at the n that exists

| quantity | floor | smallest truth detected at 80% power |
|---|---|---|
| recall | 0.50 | 0.575 |
| recall | 0.80 | 0.860 |
| recall | 0.90 | 0.945 |
| precision on 24 findings *(the old run)* | 0.50 | 0.760 |
| precision on 50 findings | 0.50 | 0.685 |
| precision on 100 findings | 0.50 | 0.640 |
| flag rate | 0.05 | 0.035 |

Precision's n is the **number of findings**, which no one knows until the detector runs. C4
therefore declares the precision threshold together with a **minimum findings count**: a
detector that fires fewer than 20 times has not been measured, and the write-up says so instead
of quoting a ratio with a denominator of four.

## The corpus was enlarged, and that is not threshold-shopping

The first census used a 4,000-commit window and produced **177 clusters**. Three cells failed:

| | required | had | |
|---|---:|---:|---|
| recall 0.6 against a floor of 0.5 | 189 | 177 | **underpowered** |
| recall 0.95 against a floor of 0.9 | 229 | 177 | **underpowered** |
| detector 0.80 vs baseline 0.70, ρ=0.5 | 226 | 177 | **underpowered** |

C3's rule is *"if the corpus is underpowered, enlarge the corpus — do not weaken the threshold
and do not proceed."* So the window was replaced by **the entire history of every repository**,
which is the maximal choice available and therefore not a number chosen to reach 226. Nothing
else moved: the same selection, the same eligibility rule, the same mechanical bar, the same
independent binder. `power_before_enlargement.json` is committed so the enlargement can be
audited rather than taken on trust.

| | window 4,000 | full history |
|---|---:|---:|
| candidate commits | 10,771 | 27,368 |
| commits that changed a signature | 3,338 | 8,023 |
| eligible commits | 435 | 1,118 |
| confirmed mutants | 495 | **859** |
| **independent clusters** | **177** | **345** |
| underpowered cells | 3 | **0** |

## What the enlargement could not reach

Reaching the whole history means reaching Python 2. `ast.parse` under Python 3.14 refuses those
files, and a census that silently returned "no definitions" for them would make an unreadable
history look like a clean one. So the failures are counted and reported:

| repository | parse failures | sources read | rate |
|---|---:|---:|---:|
| `pytest-dev/pytest` | 2,654 | 32,616 | 8.14% |
| `django/django` | 5,534 | 90,942 | 6.09% |
| `sphinx-doc/sphinx` | 1,510 | 35,788 | 4.22% |
| `pallets/flask` | 207 | 4,994 | 4.14% |
| `scrapy/scrapy` | 982 | 23,878 | 4.11% |
| `psf/requests` | 67 | 4,030 | 1.66% |
| `encode/httpx` | 1 | 5,496 | 0.02% |
| `fastapi/fastapi` | 0 | 4,004 | 0.00% |
| **total** | **10,955** | **201,748** | **5.43%** |

**5.43% of every source read was unreadable** — overwhelmingly `SyntaxError` on `print`
statements and Python-2 `except X, e` syntax, with a handful of `TabError`. The consequence is
stated plainly rather than buried: **the corpus is the Python-3-parsable part of these
histories.** The oldest era of `django`, `flask` and `requests`
contributes fewer commits than its commit count suggests, and any claim about "the whole
history" is a claim about the part a modern parser can read.

## The answer key was wrong twice, and the independent control said so

The first enlarged run produced two `vanished` mutants CPython's binder refused to confirm. Both
were a definition that left a module and came back as an aliased import — `from sphinx.addnodes
import math_reference as eqref  # to keep compatibility`. That is the **compatibility-facade shape
D4 exists to teach the checker**, and the answer key had it too. Fixed in `sigscan.import_bindings`
with five regressions, and after the fix the binder disagrees with the generator **zero times out
of 868**. The lesson is not that the key was wrong; it is that an instrument checked only against
itself would never have found out.

## What C4 inherits

- **n = 345** clusters for every recall and paired comparison; 859 mutants only as a secondary,
  cluster-bootstrapped view.
- **6,905** commits that changed a contract and updated no in-repo caller — the silence pool for
  flag rate.
- A precision threshold must come with a **minimum findings count of 20**, or it is not a
  measurable threshold.
- A "beats the baseline" threshold must state **how much better**, because 30 points and 10
  points are 34 clusters and 226 clusters apart.
