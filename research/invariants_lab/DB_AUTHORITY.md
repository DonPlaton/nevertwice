# The authority boundary: fifteen attacks, two of which landed

**Tasks B1, B2, B3.** All gates pass — **on the third build**. The first two were broken by the
suite that was written to break them, and that is the result worth reporting.

```bash
python tests/_test_db_authority.py    # 31 checks, hermetic; every attack printed with its verdict
```

Thresholds: [`PREREGISTRATION.md`](PREREGISTRATION.md) §6. Skill used:
`scientific-critical-thinking`.

---

## The verdict

| gate | declared before the run | measured | |
|---|---|---|---|
| **B1-R1** every migration carries a working `down`; up → down → up restores the schema | deterministic | 4 cases: empty `down`, partial `down`, no-op `up`, and a real round trip | **pass** |
| **B3-A1** ≥ 12 bypass attempts across ≥ 4 classes | count | **15** attempts across **6** classes | **pass** |
| **B3-A2** exactly zero succeed | count | **0 of 15** against the final build | **pass** |
| **B3-A3** ≥ 3 succeed against a deliberately weakened build | positive control | **3** | **pass** |

## Two attacks landed, and they are the point of the page

A2 passing means nothing on its own — a suite that never lands a hit proves nothing about the
boundary and everything about the suite. Here is what it caught.

### 3.2 — a replay guard keyed on a value the attacker chooses

The first build gave each `Token` a random `nonce` field and kept a set of spent nonces. The
attack is two lines:

```python
forged = Token(spent.operation, spent.target, spent.snapshot,
               spent.digest, spent.issued, "fresh-nonce")
execute_destructive(db, "delete from users", token=forged)   # ran
```

The token was already spent; a different string in a field the attacker controls made it new
again. **A replay guard keyed on a value the attacker chooses is not a replay guard.**

### 3.4 — a self-verifying capability is a self-forgeable one

The fix for 3.2 *derived* the nonce from the token's own contents — operation, target, snapshot
path, digest — so editing any field invalidated it. That held against 3.2 and 3.3, and fell to
the obvious next attack: **the derivation is public code**. Anyone can compute a correct nonce
for a target no snapshot was ever taken for.

```python
forged = Token(op, "audit", snap, digest, t, "")
forged = Token(op, "audit", snap, digest, t, forged.expected_nonce())   # ran
```

Both builds looked right and both were wrong in the same way: **authority was something the
token could prove about itself.**

### The design that survived

A capability is **issued, never computed**. `snapshot()` records what it handed out; verification
is membership in the issuer's record. Nothing derivable from a token grants anything, because
nothing about the token *is* the authority — the record is.

That is a standard answer, and this run is a small demonstration of why it is standard: two
independent, plausible, carefully-reasoned designs, both broken by a fifteen-line attack, before
the textbook one was reached by necessity rather than by taste.

## The fifteen attempts, published in full

Every one, including the ones that were easy — an enumeration that quietly drops the boring
failures overstates how hard the boundary was to attack.

| class | attempt | final build |
|---|---|---|
| direct call | call with no token | refused |
| direct call | pass `token=None` explicitly | refused |
| token forgery | construct a `Token` by hand | refused |
| token forgery | rewrite the digest field | refused |
| token forgery | keep the nonce, change the target | refused |
| token forgery | assemble a token for a target no snapshot covers | refused |
| token misuse | reuse a `users` token against `audit` | refused |
| token misuse | use a `delete` token for a `drop` | refused |
| token misuse | delete the snapshot, then use its token | refused |
| token misuse | overwrite the snapshot, then use its token | refused |
| replay | reuse a token that already ran | refused |
| replay | **swap the nonce on a spent token** | refused *(landed on build 1)* |
| path evasion | obfuscate `DROP TABLE` with case and spacing | refused |
| path evasion | smuggle a `select` through the destructive path | refused |
| config override | set `NEVERTWICE_ALLOW_DESTRUCTIVE=1` | refused |

The last one deserves a sentence: there is no such variable, and the attack exists to assert that
there is none. **An escape hatch that exists is an escape hatch that gets used at 3 a.m.**

## The threat model, stated rather than implied

The issuer's record lives in process. A caller who can edit it is a caller already running
arbitrary code in the same interpreter, and no capability scheme survives that.

**What this defends against:** an agent that constructs objects, calls functions and reads the
source — which is the realistic threat, and the one the mechanism exists for.

**What it does not:** a debugger, a monkeypatch, or direct `sqlite3` use. Attempt 4.2 makes that
limit explicit rather than leaving it to be discovered: the boundary is the only door into
`execute_destructive`, not the only door into the database. A project adopting this has to make
the destructive path the *only* path, and that is a project decision this mechanism cannot make
for it.

## B1, which is much less exciting and matters as much

Schema is the one place a mistake does not come back with `git revert`, because data has state.
Four contracts:

- an empty `down` fails, and says so;
- a `down` that only undoes *part* of the `up` fails — running `down` is not the same as `down`
  working;
- an `up` that changes nothing fails rather than passing vacuously;
- and the check is **up → down → up**, not up → down. The second `up` is what distinguishes a
  `down` that worked from one that silently did nothing and left the schema migrated.

## What this establishes

**Establishes:** the boundary holds against fifteen attempts spanning six classes, the attack
harness demonstrably lands hits (three against the weakened build, and two against real earlier
builds), and reversibility is decidable and cheap.

**Does not establish:** that the boundary is unbreakable. Fifteen attacks by the person who wrote
the mechanism is fifteen attacks by one imagination — better than zero, and the positive control
proves the harness works, but the honest claim is *"no attempt in this published list succeeds"*,
not *"none exists"*. The list is published precisely so someone else can extend it.
