# The positive class: 495 breakages nobody had an opinion about

**Task C2.** The previous corpus had **zero** true positives, which is why its precision of
0.000 measured nothing. This one has 495, and each is broken in a way CPython itself will
confirm.

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
**and** updated a caller in a different file in the same commit. Keep the definition half.
Revert the caller half. What is left is a tree containing a call that cannot work.

The answer key is written by the repository's own maintainers, at the time, without knowing
this project exists.

## Why the maintainer's edit is not by itself the key

"They changed the caller, so it must have been broken" is an inference, and a weak one.
Maintainers rename variables, reflow arguments and switch positionals to keywords for
readability constantly. A positive class built on that inference would be contaminated with
cosmetic edits, and every recall number computed from it would be wrong by an unknown amount
in an unknown direction.

So each candidate has to clear a **mechanical** bar:

| class | what must be true | what it would raise |
|---|---|---|
| **arity** | the reverted call cannot bind against the definition as it stands *after* the commit — too many positionals with no `*args`, too few for the required ones, a keyword the callee does not accept with no `**kwargs`, or a required keyword-only parameter left unsupplied | `TypeError` at the call |
| **vanished** | the reverted caller imports the symbol from a module that, after the commit, no longer defines it | `ImportError` at import |

Candidates that cannot clear it are **discarded, not downgraded**. Of 946 candidate
(commit, caller, symbol) triples the census offered, **443 were dropped** — 47% — mostly
because the call used `*args`/`**kwargs` and its arity is not statically knowable, or because
the maintainer's edit was genuinely cosmetic. A smaller class that is certainly positive is
worth more than a large one that is probably positive.

## The control that makes a pair a pair

For every mutant the generator also proves the other side: at the **real** commit, every call
site in that same file binds against that same definition. Without it a detector could flag
the symbol in both trees and score perfectly while distinguishing nothing.

## The independent verdict

`mutate.call_fails` is this project's arity logic. If the same logic both produced the positive
class and confirmed it, the corpus would establish only that the logic is self-consistent — the
exact circularity §0 rule 2 forbids. So every mutant is re-decided by **CPython's own argument
binder**: the callee's parameter list is rebuilt as a real `inspect.Signature` by a separate
walker, and the call is replayed through `Signature.bind`. A `TypeError` from the standard
library is not an opinion.

| | |
|---|---|
| mutants proposed by the generator | 503 |
| the binder disagreed that the reverted call fails | **0** |
| the binder found the *un-reverted* caller also fails | 8 |
| **confirmed, and this is the population** | **495** |

The eight are dropped and named in `mutants.json` under `dropped_because`. All eight are
**short-name collisions**: `background_manager`, `parsefactories`, `urlopen`, `setup_databases`
and `__init__` resolve to more than one definition in their repository, so which one a call
reaches is not decidable from the name. An ambiguous case in an answer key is a hole in the
answer key, and the honest move is to remove it rather than resolve it by preference.

That the binder never once disagreed on the *broken* side is the result worth stating: the
generator's arity rule and CPython's binder agree on 503 of 503 calls.

## What the population looks like

| repository | mutants | independent source commits | arity | vanished |
|---|---:|---:|---:|---:|
| `django/django` | 118 | 20 | 113 | 5 |
| `encode/httpx` | 59 | 25 | 30 | 29 |
| `fastapi/fastapi` | 10 | 5 | 9 | 1 |
| `pallets/flask` | 15 | 14 | 10 | 5 |
| `psf/requests` | 6 | 3 | 5 | 1 |
| `pytest-dev/pytest` | 116 | 49 | 99 | 17 |
| `scrapy/scrapy` | 96 | 22 | 87 | 9 |
| `sphinx-doc/sphinx` | 75 | 39 | 52 | 23 |
| **total** | **495** | **177** | **405** | **90** |

### 495 mutants are not 495 replicates, and the difference decides the analysis

One commit contributed **66** of them. The median contributes one. Treating 66 correlated
mutants from a single `django` refactor as 66 independent draws is pseudoreplication, and
`CORPUS.md` §2 bound it in advance rather than discovering it here:

> at most one mutant per source commit enters the primary analysis; where more than one is
> generated, intervals are cluster-bootstrapped with the source commit as the cluster.

So the number that governs power is **177**, not 495. C2's exit asked for 200 synthetic
positives and 495 clears it; whether **177 independent clusters** is enough is a different
question, it is C3's, and it is not answered by looking at the number and feeling reassured.
The census window was capped at 4,000 commits per repository and four of the eight blocks hit
that cap, so the corpus can be enlarged if C3 says it must be.

### Half the callers are test files

248 of 495 (**50%**) of the stale call sites live in a test file. That is a real property of
how Python projects are written, not an artifact: a test is a caller, and a test that no longer
binds is a genuine breakage. It is reported because it is a stratum a detector could plausibly
be blind to — one that only reads package code would forfeit half the positive class — and the
D5 measurement is broken out by it.

## The negative class

Every mutant carries its own negative: the same commit, unmutated. The two arms are paired on
the commit, which is what makes McNemar the right test and what `CORPUS.md` §2 declared before
any of this ran.

A second, larger negative pool exists and is not yet used: the 3,338 census commits that changed
a signature and updated **no** in-repo caller, of which only 435 were eligible. Those are commits
where a contract changed and nothing in the repository called it — the population a detector must
stay silent on. C4 declares how many of them enter the flag-rate measurement.

## What the key is checked against

`tests/_test_invariants_lab.py` — 30 checks, hermetic, no corpus required, so it stays green on a
fresh clone. It holds the key in **both** directions, because the previous labeller was wrong five
times and four of the corrections moved sites the way its author was biased:

- it must call a real `TypeError` broken (5 cases),
- it must call a compatible change fine (8 cases, including the widened signature and the
  annotation-only change that were 33% and 17% of the old checker's false positives),
- it must abstain where arity is not statically knowable (3 cases),
- and it must see tuple unpacking (5 cases), which the checker under test does not — that is D2,
  and a defect in the instrument must not also be a hole in the key.
