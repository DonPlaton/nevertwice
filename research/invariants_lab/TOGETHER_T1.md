# All of them at once: the ceiling and the only measured mechanism are incompatible

**Tasks S1–S3, T1, T2.** The seam journal is executed and works. The union of everything that
passed its own gate fires on **11.9%** of commits against a declared ceiling of **5%** — and the
declared remedy, applied honestly, removes the one mechanism with non-circular evidence behind it.

```bash
python research/invariants_lab/measure_together.py --print
python tests/_test_seam_journal.py     # 37 checks; a real migration, verified at every seam
```

Artifact: `together_t1.json`. Thresholds: [`PREREGISTRATION.md`](PREREGISTRATION.md) §8.

---

## S1–S3 · The seam journal, executed rather than scored

Not a detector, so no precision. The failure it addresses is precise: *an agent attempts the
migration in one piece, the intermediate states are red, it panics and props the result up with
workarounds.*

**S3 is a real migration**, run on a temp copy: a module's storage moves from a JSON list to a
JSON object keyed by id, in three seams — write both shapes, read the new one, drop the old — with
the **full contract check run at every intermediate seam**, not only at the end. All three are
green, the journal is reloaded from disk before each one, and the migrated module still satisfies
the contract.

**And the big bang is refused.** The same migration attempted in one step leaves the reader
requiring a key the writer no longer produces; the seam goes red, `next_seam()` returns nothing,
`blocked()` names it, and the second seam is never reached. That refusal is the entire mechanism,
and it is one assertion.

Three format decisions carry their own contracts:

- **the journal is a file**, because a plan held in context dies at the session boundary and a
  migration outlives the session by construction. S2 reloads it with no other state and identifies
  the next step;
- **the brief costs one step, not the plan.** A forty-seam migration must not cost forty seams of
  context every session start, and a test asserts the brief mentions `seam-0` and not `seam-1`;
- **a corrupt journal is `None`, not half a plan.**

## T1 · The union, and the number that decides whether anyone keeps it on

Every mechanism that passed its own gate, switched on at once, over 1,000 corpus commits with the
growth axis chosen adversarially — the most-iterated identifier in each commit.

| | |
|---|---|
| union flag rate | **0.119** [0.100, 0.141], 119/1000 |
| declared ceiling | 0.05 |
| verdict | **fail** |

`blast_radius` and the complexity ratchet are absent because they failed their own gates. That is
what "deleted" means.

### The cap is doing real work, and the numbers show how much

| | |
|---|---|
| findings the mechanisms produced | **241** |
| findings delivered to a person | **119** |
| suppressed by the one-per-diff cap | **122** |
| commits where more than one mechanism fired | 7 |

Half of everything produced never reaches anyone. Reporting only the delivered number would make
the cap look free; reporting only the raw number would describe an experience nobody has.

### T2 · Interaction

**2 commits of 1,000** had two mechanisms naming the same subject. Under the one-per-diff cap
neither produces a doubled finding, so the ranked delivery already resolves what deduplication
would. Two is small enough that no dedup rule is justified by it, and that is the finding rather
than an absence of one.

## The declared remedy, applied — and what it costs

§8 is explicit: *"If it exceeds that, rank and cap, or drop the weakest contributor — do not relax
the number."* The cap is already at one per diff and cannot go lower without going to zero. So:
drop a contributor. Every subset:

| union | flag rate | |
|---|---|---|
| all four | 0.119 | |
| `scale` + `assert` + `mutable_default` | 0.115 | |
| `scale` + `mutable_default` + `bare_except` | 0.083 | |
| **`scale` alone** | **0.073** | **still over the ceiling** |
| `assert` + `mutable_default` + `bare_except` | **0.049** | **under** |
| `assert` alone | 0.042 | under |
| `mutable_default` + `bare_except` | 0.010 | under |

**Every subset containing `scale` is over the ceiling, including `scale` by itself.** The largest
union that satisfies T1 is the three preconfigured heuristics without it.

So the rule, followed exactly, deletes the **only mechanism in this lab that passed a measurement
gate with non-circular evidence behind it**, and keeps three heuristics that have never been
measured against anything but their own definitions. `scale` is not the weakest contributor. It is
the loudest, and those are different words.

**That is reported as the result rather than resolved by preference.** The rule was declared
before the measurement precisely so it could not be renegotiated afterwards, and renegotiating it
now — "well, `scale` is *good* loud" — is the move the whole preregistration exists to prevent.
What the rule cannot do is tell anyone which of the two numbers is worth more, and this run does
not pretend to know.

### Why `scale` reads 0.073 here and 0.099 in X4

Same 73 commits fire; the denominator differs. X4 excluded the 262 commits with no loop to derive
an axis from; T1 keeps them, because a union's flag rate is over *all* commits a person makes.
Both are stated, neither is the "real" one, and the difference is arithmetic rather than
disagreement.

## What Phase T establishes about the idea, not the mechanisms

Three of the four mechanisms with a flag rate measured on real commits are **above 5%**:

| | flag rate |
|---|---|
| complexity ratchet | 0.364 |
| `blast_radius` | 0.278 |
| union of everything that passed | 0.119 |
| `scale`, alone, adversarial axis | 0.073 |
| the three preconfigured heuristics | 0.049 |

The trend is the finding. Every mechanism that reads *the whole diff* is loud; the one scoped to a
**declared** axis is four to five times quieter; and the only configuration under the ceiling is
the one that asks the smallest questions. **Scope, not accuracy, is what buys silence** — which is
consistent with D5 and R3 failing on flag rate while passing everything about correctness, and it
is the single most reusable thing this run produced.
