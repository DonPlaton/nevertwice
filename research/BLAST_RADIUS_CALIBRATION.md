# Blast-radius calibration — what 150 commits of real history said

**Run 2026-08-27.** Thresholds and the deletion decision were fixed first, in
[`BLAST_RADIUS_THRESHOLDS.md`](BLAST_RADIUS_THRESHOLDS.md), and committed before the harness
existed. Artifact: `research/blast_radius_calibration.json`. Regenerate with

```bash
python research/blast_radius_calibration.py --commits 150
python research/blast_radius_calibration.py --print     # summarise the committed artifact
```

Nothing is checked out to measure it. Sources come straight from the object database through one
`git cat-file --batch` process, inside a detached worktree that is removed when the run ends.

---

## Verdict

**T1 and T2 both pass.** The checker survives calibration and the track continues to I2. That is
not a claim that it is useful — I4 still has to show it beats `git grep` — only that it can be
made to fire on the thing it was built for.

| | declared before the run | measured | |
|---|---|---|---|
| **T1** flag rate | at most 20% of commits | **6.0%** (9 of 150) | pass |
| **T2** composition | zero budget or plan complaints | **0** of 9 | pass |
| **T3** budgets | 95th percentile of the class, declaration-only | L0 `(10, 3, 300)`, L1 `(19, 3, 350)` | applied |
| **T4** cost | median at most 2.00 s | **0.0143 s** median, 1.1991 s max | pass |

## What changed, and what it bought

Three arms over the same 150 commits. `shipped` restores the pre-calibration policy from three
module constants, so the baseline stays reproducible rather than quotable from a commit message.

| arm | flagged | rate | commits with a dependency finding | flagged only by budget or plan |
|---|---|---|---|---|
| shipped | 104 | 69.3% | 9 | **95** |
| **undeclared** (the real mode) | 9 | **6.0%** | 9 | **0** |
| declared | 15 | 10.0% | 9 | 6 |

The middle row is the product. The first row is what it replaced: of 104 flags,
**95 said nothing about dependencies at all** — 74
over-reach complaints and 54 demands for a `.nevertwice/plan.md` this repository has never had.
The number of commits carrying a real dependency finding is **the same
9 in every arm**. Nothing was traded away. The checker did
not get quieter about code; it stopped talking about paperwork.

### The bug was structural, not numeric

Retuning the budgets alone would not have fixed this, and finding that out is most of what the
run was for.

`_infer_scope` classifies a diff **L0 because it changed no contract**. The budget then flags
that same diff for touching four files. Two rules read one piece of evidence and reach opposite
conclusions, and the second one wins loudly. No choice of numbers repairs a contradiction; the
inference has to stop being treated as an accusation.

So budgets now apply **only to a scope the caller declared** (`BUDGET_SCOPE = "declared"`). A
declaration is a promise the caller made and may be held to. An inference is this module's own
guess, and a guess is not evidence against anybody. The old behaviour survives as a constant so
this table stays reproducible.

The plan requirement got the same treatment for the same reason: it is **opt-in**, signalled by
a repository having a `.nevertwice/` directory. A convention a codebase does not use must not
manufacture a problem in it. The L2 classification is still reported — the class is a
diagnosis — it just no longer comes with a bill.

### The budgets

T3 fixed the rule before the data existed: the 95th percentile of each class's own observed
distribution, rounded up. What that produced:

| class | commits | files p50 / p95 / max | dirs p50 / p95 / max | lines p50 / p95 / max | budget |
|---|---|---|---|---|---|
| L0 | 87 | 2 / 10 / 33 | 1 / 3 / 3 | 39 / 284 / 389 | `(10, 3, 300)` |
| L1 | 9 | 5 / 19 / 19 | 2 / 3 / 3 | 143 / 333 / 333 | `(19, 3, 350)` |
| L2 | 54 | 6 / 23 / 43 | 4 / 6 / 8 | 763 / 5926 / 69911 | unbounded |

Against those, the declared arm over-reaches on **6 of 87** L0 commits — 6.9% of the class,
against a rule aiming at 5% per dimension across three dimensions. Close enough to say the rule
did what it claimed.

**Two honest weaknesses in that table.** L1 holds 9 commits, so its 95th percentile *is* its
maximum: the L1 budget is "nothing seen so far", which is not a percentile in any useful sense
and will move the first time a tenth L1 commit exists. And L1 and L2 never over-reached at all —
every one of the six firings is L0. The budget mechanism is therefore calibrated on one class
and untested on the other two.

The shipped guesses were `(3, 1, 150)` and `(12, 4, 500)`. The L0 file budget was **three**
against a median of two and a 95th percentile of ten — it was set at roughly the median of real
work, which is why it fired on nearly everything.

### Cost

Median **0.0143 s** per commit, p95 0.5803 s, max
1.1991 s, against a 2.00 s ceiling the integration spec claims for itself. These
are one run on one machine and they move between runs — the artifact declares `seconds` volatile
for exactly that reason, so regenerating it changes the timings and nothing else. The verdict
does not depend on them: the flag counts came back identical across regenerations.

The declared arm is not a second measurement of the tool: the harness runs each commit through
the checker **twice** there, once to learn the inferred class and once to declare it, so its
2.1686 s worst case is the harness doing double work. The tool's own worst
invocation is the undeclared arm's 1.1991 s, on a diff of 69,911 lines. Inside the
ceiling, but not by much, and the ceiling is per invocation.

The 10.3 s measured in the twelve-commit spot check was not the checker being slow. It was the reference scan walking `research/embed_universal/data/` — about 5.3 GB of
vendored third-party clones that a fresh clone does not carry, because `SKIP_DIRS` is a
hand-kept list and does not name `data`.

The scan now asks git what the repository contains (`ls-files --cached --others
--exclude-standard`) and falls back to walking only when git cannot answer. That is a
correctness fix wearing a performance fix's clothes: **a reference inside somebody else's
vendored checkout was never a caller of this project**, and counting it made the answer wrong,
not just slow. The checker's own suite went from 14.2 s to 0.6 s on the same evidence.

### Compatibility facades (I2)

The single largest finding in the first calibration was `write_atomic` with 42 unhandled
references on the E4 seam extraction — a refactor whose entire design was a facade that preserved
every caller. `_infer_scope` was right that a contract moved; the checker was wrong about who it
hurt.

A symbol that leaves module A and is re-exported from A is not a removal. This repository writes
that as `write_atomic = _store_state.write_atomic`, behind an alias bound by a dynamic
`_sibling("store_state")` call, so recognising it means reading three shapes of module alias, not
one. **Recognising the facade is only half of it.** A checker that went quiet on every re-export
would trade a false positive for a false negative — someone re-exports a *renamed* function with a
different signature and nothing says so. So the pointer is followed: when the target module is in
the scanned corpus, the moved signature is compared against the original.

Three outcomes, and the middle one is why the pointer is followed at all:

| what is found | verdict |
|---|---|
| target located, signature unchanged | not a contract change at all — a note saying it moved |
| target located, signature **differs** | still a contract change, now with the real before/after |
| target not in the corpus | a note: the name resolves, and nothing visible here says more |

On this history: **4 commits carry 9 facade notes**, the E4 commit is
clean, and the flag rate falls from 6.7% to 6.0% with three fewer dependency
problems — exactly the three the task named. A resolved facade does not count as a contract change
either, so it cannot push a diff into L1 on its way out: the classification and the problem come
from the same evidence.

## What this run did not settle

- **Whether the 9 findings are right.** The largest
  false positive is gone — see below — but nothing here says the survivors are true. I4 asks that.
- **Whether the checker is worth having.** I4 asks that, against `git grep` for the changed
  symbol, with a hand-labelled sample and a threshold declared before that run.
- **Whether any of this generalises.** One repository, one author, one style. A budget derived
  from this history describes this history.

## Method

The calibration set is the 150 most recent commits reachable from `master` at `7ef8ad2`, each
compared against its own parent. The repository has no merges, so no merge policy was needed.
Every SHA is listed in the artifact, so the set cannot be reshaped after the fact.

For each commit, `before` and `after` are the changed paths' blobs at the parent and at the
commit; the reference corpus is every tracked `.py` file at that commit. A *dependency finding*
is a problem of the form `{qualname}: contract changed, N reference(s) left untouched`, fixed in
the thresholds document before the run so it could not be renegotiated afterwards.

The `declared` arm replays each commit declaring the class the checker itself infers. It exists
because T2 would otherwise be vacuous: once budgets are declaration-only and the history
declares nothing, "zero budget complaints" is true by construction. Declaring the inferred class
is the strongest honest test of a budget — an agent that declares exactly what it is doing must
not then be charged for it. This arm was added after the thresholds were written; T1 and T2 are
still judged on `undeclared`, exactly as declared.
