# The invariant as a note: same machinery, different provenance, stricter lifecycle

**Tasks I1, I2, I3.** Mechanism 1 is infrastructure, not a detector, so it is judged by
**contract assertions** — [`PREREGISTRATION.md`](PREREGISTRATION.md) §7 — and not by precision.
Applying a precision gate here would repeat the last run's mistake in a new place.

```bash
python tests/_test_invariant_notes.py    # 93 contracts, hermetic, no vault and no corpus
```

Modules: `invariant_notes.py` (I1, I2), `preconfigured.py` (I3). Both in the lab; nothing enters
`nevertwice/` before T4.

---

## The difference, stated as a table rather than a mood

A **scar** is knowledge induced from something that already broke. An **invariant** is a claim
about the code that can be checked before anything does. The spec's framing is that the difference
is *provenance and lifecycle, not machinery*, and this takes that literally.

| | scar | invariant |
|---|---|---|
| where it comes from | an incident | a property of the code, asserted |
| what it matches | a regex over the proposed action text | a deterministic checker over the diff |
| retires after | five false positives | **two** |
| how many fire at once | as many as match | **one**, the highest ranked |
| cost when quiet | nothing | nothing |

Same ledger shape, so one file holds both and the scar path is untouched. A `kind` field
separates them, and `invariants(ledger)` never returns a scar.

## I1 — the note, and the hot path that decides whether anyone keeps it on

**Zero context tokens until it fires** is the token-economy core, and it is asserted four ways:
an empty ledger, a silent checker, an unregistered checker, and a retired note all produce the
empty string — not a heading, not a blank line.

Three properties are less obvious and were written as contracts because they are the ones that
bite in production:

- **Scope is honoured before any checker runs.** An out-of-scope invariant costs a dictionary
  lookup, and a test asserts the checker function *was not called* rather than that it returned
  nothing.
- **A broken checker is not a broken commit.** A checker that raises is skipped and the others
  still report. A mechanism that can take down the tool it protects has a worse failure mode than
  the one it prevents.
- **A corrupt ledger reads as empty, not as an exception.** `guards.py` recovers across two
  generations because a silently swallowed ledger once meant every guard stopped firing with no
  trace; the lab has no generations yet, so it fails to the visible answer.

The checker is **code the registry resolves, not a regex the hot path interprets**. "This diff
degrades a file against its own baseline" is not expressible as a regex, and pretending otherwise
is how a structural claim quietly becomes a text match.

## I2 — the lifecycle, and why two

The spec's own warning: *an invariant firing on 30% of diffs is switched off in week one, and
after that being right does not matter.* [`BLAST_RADIUS_D5.md`](BLAST_RADIUS_D5.md) measured
**27.8%** for the first candidate checker. So the budget for being wrong is two, and the delivery
layer caps output at one **before any checker is trusted to behave**.

Three decisions in the lifecycle are deliberate and each has its contract:

1. **Being helpful does not buy forgiveness.** Ten `helped` signals do not undo one false
   positive. The attention a wrong finding spent is not refunded by a right one, and a mechanism
   that averages its mistakes away is one whose owner discovers the average only after switching
   it off.
2. **Retirement is recorded, not just applied** — the date and the reason. A retirement nobody can
   see is a bug nobody can find.
3. **Revival is a decision, not a side effect.** A `helped` signal cannot un-retire anything;
   `revive()` can, it resets the budget, and the note remembers it happened. A claim that keeps
   being wrong and keeps being restored is then visible as such instead of looking new each time.

An outcome nobody defined **raises** rather than being dropped. A feedback channel that ignores
what it does not recognise reports success forever.

## I3 — the pack that ships, and what it is allowed to contain

This is the delivery vehicle for the cold-start claim, and the claim is narrow:

> A scar requires you to fall first. On a new repository the store is empty, so a
> retrieve-and-inject system returns nothing **by construction** — Nevertwice as it ships today
> included. An invariant checks a property of the code itself, so it can ship preconfigured and
> work from minute zero.

Whether that is *worth* anything is T3's measurement against Nevertwice-with-an-empty-store. I3 is
only what makes the question askable.

Four admission rules, and the third is the one that stops this becoming a scar collection with a
different name:

1. **deterministic** — no model, no embedding, no network;
2. **structural** — it reads the diff, not the prose; "you probably meant" is not an invariant;
3. **portable** — true of Python as a language, never of this project's habits;
4. **quiet by default** — it fires on a property being *violated*, not present.

| checker | what it fires on | why it is portable |
|---|---|---|
| `mutable_default` | a parameter defaulting to a list, dict or set literal, **newly introduced by this diff** | the default is evaluated once at definition time; the most-taught Python bug that still ships |
| `bare_except` | `except:` with no type, newly introduced | it swallows `KeyboardInterrupt` and `SystemExit`, so Ctrl-C becomes a no-op |
| `assert_in_shipped_code` | a bare `assert` outside a test file, newly introduced | `python -O` deletes it, so the contract depends on a flag nobody checks |

**Only newly introduced violations fire.** A file that already had one is not this diff's problem,
and reporting it would make every unrelated edit to that file noisy — which is precisely how an
invariant gets switched off in week one.

**`except Exception:` does not fire.** It is a judgement call, and an invariant that fires on
judgement calls spends its two-false-positive budget on the first reviewer who disagrees.

**The pack is deliberately three.** `BLAST_RADIUS_D5.md` measured what happens when a checker
that is right 91% of the time also fires on 28% of the commits that broke nothing; the lesson
taken here is that the number of shipped invariants is a **budget, not a feature list**.

### The killswitch is checked first

`NEVERTWICE_INVARIANTS=0` — and `false`, `no`, `off`, or empty — turns the mechanism off before
the ledger is read, before scope is matched, before a checker is resolved. A contract asserts that
no checker function was *called*, not merely that none reported. A killswitch that still pays for
the machinery it disables is a killswitch nobody believes.

## What is asserted, and what is not

**Asserted (93 contracts, hermetic):** well-formedness, id stability, ledger coexistence with
scars, atomic storage with no temp file left behind, corrupt-ledger tolerance, zero cost when
quiet, the one-finding cap, scope short-circuiting, crash isolation, the two-strike lifecycle,
recorded retirement, deliberate revival, the pack's provenance and silence on ordinary work, and
the killswitch in six spellings.

**Not asserted, and not claimed:** that the pack's three checkers are *useful*. Firing correctly
on a synthetic diff is not evidence that anyone wants the finding. That is T3's measurement, and
the threshold for it was declared in `PREREGISTRATION.md` §8 before any of this was written.

The `rm -rf`, lazy-import and killswitch contracts for the **shipped** package
(`nevertwice/invariants/`) already exist in `tests/_test_blast_radius.py` from the previous run's
I3 and still pass. Note that the package's only current member, `blast_radius`, **failed its gate
in D5**; moving it out of `nevertwice/` and into the lab is T4's job, not this one's, and it is
recorded here so the sequencing is not mistaken for an oversight.
