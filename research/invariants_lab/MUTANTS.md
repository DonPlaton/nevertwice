# The positive class: 859 breakages nobody had an opinion about

**Task C2.** The previous corpus had **zero** true positives, which is why its precision of 0.000
measured nothing. This one has 859, and each is broken in a way CPython itself confirms.

```bash
python research/invariants_lab/mutate.py             # generate and prove
python research/invariants_lab/verify_mutants.py     # re-decide with CPython's binder
python research/invariants_lab/mutate.py --print
python tests/_test_invariants_lab.py                 # the key's own controls, no corpus needed
```

Artifacts: `mutants.json` (the population), `mutant_controls.json` (the independent verdicts).
Skills used: `experimental-design` (C1's design, carried), `test-driven-development`.

---

## The recipe, in one sentence

Take a commit that changed a callable's parameter list — or moved a symbol out of a module —
**and** updated a caller in a different file in the same commit. Keep the definition half. Revert
the caller half. What is left is a tree containing a call that cannot work.

The answer key was written by the repository's own maintainers, at the time, without knowing this
project exists.

## Why the maintainer's edit is not by itself the key

"They changed the caller, so it must have been broken" is an inference, and a weak one.
Maintainers rename variables, reflow arguments and switch positionals to keywords for readability
constantly. A positive class built on that inference would be contaminated with cosmetic edits,
and every recall number computed from it would be wrong by an unknown amount in an unknown
direction.

So each candidate has to clear a **mechanical** bar:

| class | what must be true | what it would raise |
|---|---|---|
| **arity** | the reverted call cannot bind against the definition as it stands *after* the commit — too many positionals with no `*args`, too few for the required ones, a keyword the callee does not accept with no `**kwargs`, or a required keyword-only parameter left unsupplied | `TypeError` at the call |
| **vanished** | the reverted caller imports the symbol from a module that, after the commit, neither defines **nor re-imports** it | `ImportError` at import |

Candidates that cannot clear it are **discarded, not downgraded**. Of 2,247 candidate
(commit, caller, symbol) triples the census offered, **1,379 were dropped** — 61% — mostly because
the call used `*args`/`**kwargs` and its arity is not statically knowable, or because the
maintainer's edit was genuinely cosmetic. A smaller class that is certainly positive is worth more
than a large one that is probably positive.

## The control that makes a pair a pair

For every mutant the generator also proves the other side: at the **real** commit, every call site
in that same file binds against that same definition. Without it a detector could flag the symbol
in both trees and score perfectly while distinguishing nothing.

## The independent verdict, and the defect it found in the key

`mutate.call_fails` is this project's arity logic. If the same logic both produced the positive
class and confirmed it, the corpus would establish only that the logic is self-consistent — the
exact circularity §0 rule 2 forbids. So every mutant is re-decided by **CPython's own argument
binder**: the callee's parameter list is rebuilt as a real `inspect.Signature` by a separate
walker, and the call is replayed through `Signature.bind`. A `TypeError` from the standard library
is not an opinion.

It earned its place immediately. On the first run it rejected two `vanished` mutants the generator
was sure of. Both were the same shape:

```python
from sphinx.addnodes import math_reference as eqref  # NOQA  # to keep compatibility
```

The definition of `eqref` left `sphinx/ext/mathbase.py`; the **name** stayed, re-imported as an
alias. Anyone importing it is unaffected. **The answer key had the compatibility-facade blind spot
that D4 exists to fix in the checker** — and the independent instrument found it before any
measurement depended on it. `sigscan.import_bindings` now suppresses a removal whose name comes
back as an import, with five regressions in `tests/_test_invariants_lab.py`.

After the fix:

| | |
|---|---|
| mutants proposed by the generator | 868 |
| the binder disagreed that the reverted call fails | **0** |
| the binder found the *un-reverted* caller also fails | 9 |
| **confirmed, and this is the population** | **859** |

The nine are dropped and named in `mutants.json` under `dropped_because`. All nine are **short-name
collisions**: `background_manager`, `parsefactories`, `urlopen`, `setup_databases` and `__init__`
resolve to more than one definition in their repository, so which one a call reaches is not
decidable from the name. An ambiguous case in an answer key is a hole in the answer key, and the
honest move is to remove it rather than resolve it by preference.

## What the population looks like

| repository | mutants | independent source commits | arity | vanished |
|---|---:|---:|---:|---:|
| `django/django` | 311 | 107 | 271 | 40 |
| `pytest-dev/pytest` | 157 | 76 | 136 | 21 |
| `sphinx-doc/sphinx` | 157 | 79 | 123 | 34 |
| `scrapy/scrapy` | 144 | 37 | 131 | 13 |
| `encode/httpx` | 59 | 25 | 30 | 29 |
| `pallets/flask` | 14 | 13 | 10 | 4 |
| `fastapi/fastapi` | 11 | 5 | 9 | 2 |
| `psf/requests` | 6 | 3 | 5 | 1 |
| **total** | **859** | **345** | **715** | **144** |

### 859 mutants are not 859 replicates, and the difference decides the analysis

One commit contributed **66** of them. The median contributes one. Treating 66 correlated mutants
from a single `django` refactor as 66 independent draws is pseudoreplication, and `CORPUS.md` §2
bound it in advance rather than discovering it here:

> at most one mutant per source commit enters the primary analysis; where more than one is
> generated, intervals are cluster-bootstrapped with the source commit as the cluster.

So the number that governs power is **345**, not 859. [`POWER.md`](POWER.md) computes what that
rule was worth: pooling all 859 at ICC 1.0 would leave an effective sample of **69**, five times
smaller than the clean clusters it replaced.

### 43% of the callers are test files

369 of 859 stale call sites live in a test file. That is a real property of how Python projects
are written, not an artifact: a test is a caller, and a test that no longer binds is a genuine
breakage. It is reported because it is a stratum a detector could plausibly be blind to — one that
only reads package code would forfeit two fifths of the positive class — and the D5 measurement is
broken out by it.

## The negative class

Every mutant carries its own negative: the same commit, unmutated. The two arms are paired on the
commit, which is what makes McNemar the right test and what `CORPUS.md` §2 declared before any of
this ran.

A second, larger negative pool exists and is not yet used: the **6,905** census commits that
changed a signature and updated **no** in-repo caller. Those are commits where a contract changed
and nothing in the repository called it — the population a detector must stay silent on. C4
declares how many of them enter the flag-rate measurement.

## What the key is checked against

`tests/_test_invariants_lab.py` — 41 checks, hermetic, no corpus required, so it stays green on a
fresh clone. It holds the key in **both** directions, because the previous labeller was wrong five
times and four of the corrections moved sites the way its author was biased:

- 5 cases it must call **broken** — a real `TypeError` in each;
- 8 it must call **fine**, including the widened signature and the annotation-only change that were
  33% and 17% of the old checker's false positives;
- 3 it must **abstain** on, where arity is not statically knowable;
- 5 pinning **tuple unpacking**, which the checker under test cannot see — D2's defect in the
  instrument must not also be a hole in the key;
- 5 pinning the **compatibility facade** the binder caught above;
- 6 pinning the **power function** against a value computed by hand, because C3's whole argument
  rests on it.
