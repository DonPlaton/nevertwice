# Blast-radius: a negative result

**Run 2026-08-27.** Thresholds and the deletion decision were fixed first, in
[`BLAST_RADIUS_DECISION.md`](BLAST_RADIUS_DECISION.md), and committed before the harness existed.
Artifacts: `research/blast_radius_precision.json`, `research/blast_radius_labels.json`.

```bash
python research/blast_radius_precision.py            # label the census, compare, write the artifact
python research/blast_radius_precision.py --print    # summarise the committed artifact
python tests/_test_blast_radius_labeller.py          # the controls the census depends on
```

---

## Verdict: it does not earn its place. Both gates fail.

| | declared before the run | measured | |
|---|---|---|---|
| **P1** precision | at least 50% of findings TRUE | **0 of 24** — precision **0.000** | fail |
| **P2** beats `git grep` at a matched flag rate | strictly greater, ties to grep | 0.000 cannot exceed any score | fail |

Every one of the 24 dependency findings the checker produced over 150 commits is a false
positive. Not most. All of them.

The declared consequence stands: the mechanism is not merged, and this document is the result.

### Why a null result is not just an absent one

"Precision 0" has two explanations — the findings really are all false, or the labeller says
false to everything — and only one is interesting. So the labeller is held to **controls**:
`tests/_test_blast_radius_labeller.py` constructs cases that are unambiguously breakages and
requires it to call them true, and cases that are unambiguously safe and requires it to call them
false. 32 checks, including five positive controls. It can say true; on this evidence it had no
occasion to.

**136 of 162 sites were decided mechanically** — by rules that read the import graph and compare
call sites to signatures, not by an opinion — and 26 by hand with the reasoning committed in
`blast_radius_labels.json`, each naming the commit it was read at.

## What the false positives actually are

This is the transferable part. "Do not build this" is worth less than "here is what you would
have to solve first."

| why the finding is false | findings | share |
|---|---|---|
| the signature was **widened** — a defaulted or keyword-only parameter appeared | 8 | 33% |
| the symbol **moved and still resolves** — imported back, or reached as a module attribute | 6 | 25% |
| the symbol was **rebound by tuple unpacking**, which the extractor cannot see | 5 | 21% |
| the change was **annotation-only** — types added, runtime identical | 4 | 17% |
| a **class gained a member**, which cannot affect a construction or an annotation | 1 | 4% |

Read down that column: **not one category is about a caller being left behind.** Three of the
five are the checker misreading its own evidence, and two are it reading the evidence correctly
and drawing a conclusion that does not follow.

### The first three are fixable, and fixing them changes nothing

*Widened signatures* (33%) are the largest class and the easiest to remove: a signature that
still accepts every call the old one accepted cannot break a caller, and that is decidable — the
census computes it in twenty lines. *Annotation-only* changes (17%) are the same argument in a
weaker form. *Tuple-unpacking rebinds* (21%) are a plain defect: `visit_Assign` records only
`ast.Name` targets, so `A, B, C = load()` deletes three symbols as far as the extractor knows.

Remove all three and the checker reports **zero findings on 150 commits**. It does not get more
precise; it goes silent. That is the finding underneath the finding: on this repository's history
there was never a stale caller to report, and every firing was a way of being wrong about that.

### The other two are the interesting ones

*Moved and still resolves* (25%) is the compatibility-facade problem in its general form. I2
solved the shape this codebase writes — `write_atomic = _store_state.write_atomic` — and the
remaining six are the shapes it does not: a module attribute (`_st.est_tokens(...)`) where the
module imports the name back, an import that reaches past the module the symbol left. Each one is
individually solvable and there is no reason to believe the list ends.

*A class gaining a member* being reported as a signature change is a category error: the
"signature" of a class here is its member list, and a member list growing is exactly what a
compatible change looks like.

## The baseline

`git grep` for the changed symbol, given the symbol list for free, needs a threshold of **11
unhandled mentions** before it flags no more commits than the checker did. At that threshold it
flags **8 commits, 7 of them shared** with the checker's 9.

P2 is decided without labelling it: precision 0.000 is the minimum possible, and nothing is
strictly greater than the minimum. That is not a technicality — it is what "ties go to `git
grep`" was written for. The baseline was partially labelled anyway (10 findings, 553 sites, one
decided and false, the rest abstained by rules built for AST references rather than raw text),
and it is reported here as context rather than as a result.

**B0** — flag every commit whose contract changed at all, no reference analysis — fires on **22
of 150** commits. It is a worse product than the checker and exactly as informative about stale
callers, which is to say not at all.

## Five defects in the measuring instrument, and why they are reported

The labeller was wrong five times before it was right, and every correction was found by reading
its own output rather than by it failing. Reporting them is not throat-clearing: four of the five
moved sites from TRUE to FALSE, which is the direction the author of the checker would be biased
towards, and the reader is entitled to know that and to check.

1. An `Attribute` call was given an implicit `self`, so `api.guards_check(x)` looked
   over-supplied — 19 correct call sites labelled as breakages.
2. Module aliases were read from top-level statements only, so `import numpy as np` inside a
   `try` was missed and four `np.where(...)` calls looked like calls into this project.
3. A class signature (`class C() :: get, value`) was parsed as an empty parameter list, so every
   construction looked over-supplied.
4. A reference inside the file that **defines** the symbol was called a lexical collision. This
   one moved sites the other way — from FALSE to undecided — and it was found precisely because
   the false labels were audited after the true ones ran out.
5. A removal was judged against the owner's definitions rather than the site's imports, so a
   symbol that moved and was imported back looked deleted.

Each has a regression in `tests/_test_blast_radius_labeller.py`.

## What survives

The track produced things worth keeping even though the checker is not one of them:

- **I1's calibration method.** Replaying a mechanism over a repository's own history before
  shipping it, with the flag-rate ceiling declared first, found in one run that the shipped
  budgets fired on 69.3% of commits and said nothing about dependencies 90% of the time. That is
  reusable for any advisory mechanism this project builds next.
- **The structural finding behind it.** A rule that infers a class from evidence must not then
  charge the diff for the class it inferred. That bug shape is not specific to blast radius.
- **The taxonomy above**, which is the whole value of running the census rather than reasoning
  about it.

## What this does not say

It does not say a blast-radius checker cannot work anywhere. One repository, one author, 150
commits, and a codebase whose refactors are unusually disciplined about compatibility — every
"removal" here turned out to be a move with the name preserved, which is a property of this
project's review culture rather than of software in general. A codebase that breaks callers
would give the checker something to find.

It does say that on **this** repository it found nothing true in 150 commits, and that a
mechanism which fires nine times and is wrong nine times is worse than one that does not fire.
