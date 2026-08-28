# Phase E — does the memory's work get better with the mechanism in it?

**Tasks E1–E5, and E4 is G-C.** Every number in Phases D, F, T and X is *mechanism-level*:
precision, recall, a flag rate against a corpus. None of them answers the owner's question, which
is whether an agent doing real work does it better with the mechanism than without.

**Every number here is in sample**, and the reason is `HELDOUT_H3_BLOCKED.md`: DNS resolves
nothing on this machine, so the held-out corpus does not exist. The tasks come from `corpus_dev`
— the eight repositories the mechanisms were tuned on. That weakens the *benefit* half and it does
not weaken the **harm** half, which is a property of the mechanism's output and the agent's
response rather than a generalisation about a codebase.

Skills: `experimental-design` (§2, the arms and what is held constant), `statistical-power`
(§4, the trial count computed from a measured base rate before any gate is scored),
`scientific-critical-thinking` (§3, on judging an agent's output with an instrument the mechanism
does not share).

---

## 1. What "with the mechanism" and "without" actually differ by

`GOAL-SHIP.md` E1 names the arms as *Nevertwice memory alone* and *memory plus the surviving
mechanisms*. On this corpus there is no memory store for eight public repositories, so the memory
context is **empty in both arms and therefore cancels**. What remains is exactly the contrast the
owner's rule is about: **the mechanism's marginal contribution to an agent's work.**

| arm | the agent is given |
|---|---|
| **off** | the contract change (old signature → new), the caller file as it stands, and the instruction to fix every call that would now fail |
| **on** | the same, **plus** what `blast_radius` under `decidable-only` says: file, line, and why |

Both arms see the same task, the same file and the same model at the same temperature, and the
pair is by task. The only difference is whether the mechanism's findings were delivered.

## 2. The task, and why this one

**Reproducing a real change, where the correct outcome is knowable independently.** One task is
one mutant from the answer key: a real commit that changed a callable's parameter list *and*
updated a caller, with the caller half reverted. The tree therefore contains a call that cannot
bind, and CPython's own binding rule says so — no opinion involved.

**Selection, declared before the run:**

| | |
|---|---|
| breakage kind | `arity` only — a `vanished` import is a one-line fix and would floor the base rate |
| caller file | ≤ **400 lines**, so the whole file fits a local model's context and the agent is not being tested on truncation |
| call sites | ≥ **2** calls to the changed symbol in that file, so "find the one call" is not the whole task |
| sampling | one task per source commit, seeded 20260828 |

The pool that satisfies all four is **53 tasks** — 123 mutants, deduplicated to one per source
commit — censused before any task ran.

**The pool size caps what the stand can resolve, and that is declared here rather than discovered
later.** With 53 tasks, `trials <= 53`, so at the assumed fix rate of 0.60 the arithmetic in §4
needs a base rate of at least

    6 / (53 x 0.60) = 0.189

Below that, **using every task in the pool is still not enough**, and the honest outcome is a
refusal with the arithmetic rather than a run that cannot reject. Widening the pool means raising
the 400-line cap, which trades context truncation for sample size — and a model failing because a
file was truncated is not a model failing at the task.

## 3. The judge, which the mechanism does not share

The agent returns a file. The judge:

1. **parses it** — a file that does not parse is *unusable*, never a failure;
2. **checks it kept its definitions** — a file that lost callables is *unusable*, so "delete the
   problem" cannot score as a fix;
3. **simulates every call** to the changed symbol against the new definition, using
   `binding.call_fails` — CPython's argument-binding rule, which `verify_mutants` agreed with 868
   times of 868.

**The overlap is named rather than hidden.** `decidable-only` emits "a call that cannot bind", and
the judge asks "does a call still fail to bind". Those are the same predicate applied to different
objects: the mechanism applies it to the *input* to find candidate sites; the judge applies it to
the agent's *output*. Both arms are judged identically, so the comparison is an intervention
contrast and not a scoring of the mechanism against itself. What would be circular — and is not
done — is judging the ON arm by whether it addressed the mechanism's findings.

## 4. E2 — the base rate, and the trial count that follows from it

The C3 discipline, and the reason F5 stopped cheaply instead of expensively.

**Measured first, before any gate is scored:** how often the **off** arm leaves a call that cannot
bind. Exact McNemar needs 6 discordant pairs one way to reject at α = 0.05, so with `b` the base
rate and `f` the share of those the mechanism's findings fix:

> **trials ≈ 6 / (b × f)**

| | declared |
|---|---|
| base rate floor | **0.10.** Below it the stand is rebuilt with harder tasks *before* anything is scored — a stand that cannot fail cannot be improved |
| assumed fix rate | **0.60**, the same conservative figure F5 used |
| trial ceiling | **600.** Above it the run is refused and the arithmetic published, as F5's was |

## 5. E3 — harm, as its own number

Not the complement of benefit, not inferable from a flag rate, and the half nobody has measured.

| count | what it is |
|---|---|
| **fixed** | off fails, on passes |
| **broken** | off passes, on fails |
| **ignored** | the mechanism fired and the outcome did not change |
| **false-flag repair** | on a tree that is **already correct**, the mechanism fired and the agent changed the code |

The fourth needs its own arm, because it cannot happen on a task that starts broken. **The harm
arm is the same commits, unmutated** — the real tree, where every call binds. The mechanism runs;
where it fires, the finding is delivered; and the judge asks two questions: *did the agent change
the file at all*, and *does it still bind afterwards*.

A false flag that costs only attention is a nuisance. One that makes an agent edit correct code
and break it is the thing that makes a mechanism worse than nothing, and it is the reason harm is
a separate gate rather than a footnote.

## 6. E4 — the gate, and it is two gates

| | threshold |
|---|---|
| **net benefit** | `fixed` > `broken`, exact McNemar on paired outcomes, **p < 0.05** |
| **harm bound** | (`broken` + `false-flag repair`) / trials ≤ **0.05**, one-sided exact binomial |

**Both must hold.** A mechanism that fixes more than it breaks while breaking 10% of everything
is not one anybody should install, and net benefit alone would hide that.

## 7. E5 — the audit, published either way

Five times in five harnesses this project has read "could not answer" as "answered fine": three in
T3, a `# noqa` silencing `ruff` in F3, and a canary abstaining on half of F5's probe. Before any
number here is believed:

- empty and truncated completions — counted, excluded, reported;
- files that do not parse — the same;
- files that lost their definitions — the same, because deleting the call site is not a fix;
- the base rate reported next to the count of **usable** trials, so a base rate of zero cannot be
  a harness that produced nothing;
- both arms' failure counts printed, so an arm that collapsed is visible rather than flattering.

## 8. What this cannot conclude

- **Nothing out of sample.** `HELDOUT_H3_BLOCKED.md`. A positive result here is evidence about
  eight repositories the mechanism was tuned on.
- **Nothing about a real memory store.** The memory context is empty in both arms; this measures
  the mechanism's marginal contribution, which is the contrast the owner's rule names but is not
  the same as measuring Nevertwice-with-history against Nevertwice-without.
- **Nothing about the other mechanisms.** The ratchet failed its silence gate twice on this corpus
  and `scale`'s static half cannot see the shapes real code uses; neither is in a state where an
  end-to-end arm would measure anything but that. **E measures `blast_radius` under
  `decidable-only`**, the one mechanism Phase F finished and that reached the ceiling.

---

## 9. E2 — the base rate, measured before anything was scored

12 tasks, `qwen3-coder:30b`, temperature 0.2, both arms on the same first file.

| | |
|---|---|
| trials | 12 |
| **usable** | **11** — one answer lost a definition and was excluded, not counted as a failure |
| **base rate**, the off arm leaves a call that cannot bind | **0.273** [0.10, 0.57] — 3 of 11 |
| floor declared in §4 | 0.10 — **cleared** |
| **trials needed** at the assumed fix rate 0.60 | **37** |
| pool | 53, so 37 fits — **the stand can resolve the declared effect** |

**The mechanism fired on 8 of 12 broken trees and on 0 of 6 correct ones.** `decidable-only` is
silent when there is nothing to say, which is what F1 measured on the silence pool and is the
first time it has been observed on a stand rather than on a corpus.

**Declared before E4 ran:** the arithmetic asks for 37 and the pool holds 53, so **E4 runs the
whole pool** — 53 benefit trials and 53 harm trials, one pass, no interim looks and no early
stop. Running more than the requirement adds power; it does not change the threshold, which stays
where §6 put it.
