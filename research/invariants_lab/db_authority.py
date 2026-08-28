"""B1-B2: reversible migrations, and a destructive operation that cannot be expressed.

Schema is the one place a mistake does **not** come back with `git revert`, because data has
state. So this mechanism has two halves and they are different kinds of thing.

**B1, reversibility, is a detector.** Every migration carries a non-empty `down`, and the suite
runs up -> down -> up and checks the schema came back. Deterministic, cheap, and either true or
false.

**B2, the authority boundary, is enforcement.** The claim is not "the agent is asked nicely not
to drop the table". It is that a destructive operation is **inexpressible** without a snapshot
and an explicit token -- the agent leaves the trust chain entirely rather than being trusted
inside it.

## What "inexpressible" has to mean to be worth anything

A boundary that can be stepped around is a suggestion. So the design is deliberately small,
because every feature is a way through:

* one function performs destructive work, and it takes a `Token`;
* a `Token` can only be obtained by taking a snapshot, and it records what it was taken for;
* the token is **bound** to the exact operation, the target and the snapshot -- reusing one for
  a different table, or a second time, or after the snapshot was deleted, fails;
* there is **no** flag, environment variable or keyword that skips the snapshot. An escape hatch
  that exists is an escape hatch that gets used at 3 a.m.

B3 attacks all of that and publishes the attempts, including the ones that were easy. An
enforcement mechanism validated by its author's imagination is validated by nothing, so B3 also
requires that at least three attacks **succeed against a deliberately weakened build** -- a
bypass suite that never lands a hit proves nothing about the boundary and everything about the
suite.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import closing
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Statements that can destroy data that no `git revert` brings back.
DESTRUCTIVE = re.compile(
    r"^\s*(drop\s+(table|index|view|column)|truncate|delete\s+from|"
    r"alter\s+table\s+\S+\s+drop)\b",
    re.IGNORECASE,
)


class AuthorityError(RuntimeError):
    """The operation was not expressible. Not a warning; the call did not happen."""


# --------------------------------------------------------------------------
# B1: reversibility
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    name: str
    up: str
    down: str

    def reversible(self) -> bool:
        return bool(self.down.strip())


def schema_of(db: Path) -> str:
    """A canonical dump of the schema, for comparing before and after."""
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(
            "select type, name, sql from sqlite_master "
            "where name not like 'sqlite_%' order by type, name"
        ).fetchall()
    return "\n".join(f"{t}|{n}|{(s or '').strip()}" for t, n, s in rows)


@dataclass
class ReversibilityResult:
    name: str
    ok: bool
    reason: str = ""


def check_reversible(db: Path, migration: Migration) -> ReversibilityResult:
    """up -> down -> up, and the schema must come back to where it started.

    Running up **twice** is not incidental: a `down` that silently fails leaves the
    schema migrated, and the second `up` would then be a no-op or an error rather than
    a repeat. The round trip is what distinguishes a `down` that works from a `down`
    that exists.
    """
    if not migration.reversible():
        return ReversibilityResult(migration.name, False, "the down step is empty")
    before = schema_of(db)
    try:
        with closing(sqlite3.connect(db)) as conn:
            conn.executescript(migration.up)
        migrated = schema_of(db)
        if migrated == before:
            return ReversibilityResult(migration.name, False, "the up step changed nothing")
        with closing(sqlite3.connect(db)) as conn:
            conn.executescript(migration.down)
        if schema_of(db) != before:
            return ReversibilityResult(migration.name, False,
                                       "down did not restore the schema")
        with closing(sqlite3.connect(db)) as conn:
            conn.executescript(migration.up)
        if schema_of(db) != migrated:
            return ReversibilityResult(migration.name, False,
                                       "up was not repeatable after down")
    except sqlite3.Error as exc:
        return ReversibilityResult(migration.name, False, f"sqlite refused it: {exc}")
    return ReversibilityResult(migration.name, True)


# --------------------------------------------------------------------------
# B2: the authority boundary
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    """Proof that a snapshot exists, bound to one operation on one target.

    Frozen, and its fields are part of its identity: a token for `drop table a` is
    arithmetically not a token for `drop table b`, so there is nothing to tamper with
    that does not simply produce a token that fails to verify.
    """

    operation: str
    target: str
    snapshot: str          # path to the snapshot file
    digest: str            # sha256 of the snapshot at the moment it was taken
    issued: float
    nonce: str

    def matches(self, operation: str, target: str) -> bool:
        return self.operation == operation and self.target == target

    def fingerprint(self) -> str:
        """Everything this token claims, in one string. Not its authority.

        Two attacks, both landed against earlier builds of this file, and both are
        published in `tests/_test_db_authority.py`:

        * **3.2** -- the nonce was an independent field and the replay ledger keyed on
          it, so copying a spent token with a fresh string walked through. *A replay
          guard keyed on a value the attacker chooses is not a replay guard.*
        * **3.4** -- the nonce was then **derived** from the token's own contents, and
          the derivation is public code. Anyone can recompute it for a target the
          snapshot was never taken for. *A self-verifying capability is a
          self-forgeable one.*

        The fix is the standard answer and the reason it is standard: a capability is
        **issued**, not derived. `snapshot()` records what it handed out; verification
        is membership in that record. Nothing computable from the token alone grants
        anything.
        """
        return hashlib.sha256(
            f"{self.operation}|{self.target}|{self.snapshot}|{self.digest}".encode()
        ).hexdigest()[:32]


@dataclass
class _Ledger:
    """What was issued, and what has been spent. The issuer's record, not the caller's.

    In process, because that is where the boundary lives: a caller that can edit this
    is a caller already running arbitrary code in the same interpreter, and no token
    scheme survives that. The threat this defends against is the realistic one -- an
    agent constructing objects and calling functions -- not an attacker with the
    process debugger attached, and the write-up says so rather than implying more.
    """

    issued: set[str] = field(default_factory=set)
    used: set[str] = field(default_factory=set)


_SPENT = _Ledger()


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(db: Path, operation: str, target: str, *, into: Path | None = None) -> Token:
    """Take a snapshot and return the only thing that authorises the operation.

    There is no other way to construct a usable token, which is the point: the
    snapshot is not a step the caller is *asked* to perform, it is the step that
    produces the authority.
    """
    directory = into or db.parent / ".snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    # The filename carries randomness so two snapshots of the same database never
    # collide; the token's identity does NOT come from that randomness, it is derived
    # from the snapshot that exists on disk. See Token.expected_nonce.
    rid = hashlib.sha256(f"{time.time_ns()}|{db}|{operation}".encode()).hexdigest()[:16]
    dest = directory / f"{db.stem}.{rid}.snapshot"
    shutil.copy2(db, dest)
    digest = _digest(dest)
    nonce = hashlib.sha256(
        f"{rid}|{digest}|{time.time_ns()}".encode()).hexdigest()[:32]
    token = Token(operation, target, str(dest), digest, time.time(), nonce)
    _SPENT.issued.add(token.fingerprint() + "|" + nonce)
    return token


def execute_destructive(db: Path, statement: str, *, token: Token | None = None) -> int:
    """Run a destructive statement, or refuse in a way the caller cannot ignore.

    Every refusal raises. Returning a status code would let a caller carry on with a
    value it did not check, and a boundary whose violation is a return value is a
    boundary that is crossed by an unchecked call.
    """
    match = DESTRUCTIVE.match(statement)
    if not match:
        raise AuthorityError(
            "execute_destructive is for destructive statements; use an ordinary "
            "connection for everything else"
        )
    operation = " ".join(match.group(0).lower().split())
    target = _target_of(statement)

    if token is None:
        raise AuthorityError(
            f"{operation} on {target!r} requires a token, and a token requires a snapshot"
        )
    if not token.matches(operation, target):
        raise AuthorityError(
            f"this token authorises {token.operation!r} on {token.target!r}, "
            f"not {operation!r} on {target!r}"
        )
    receipt = token.fingerprint() + "|" + token.nonce
    if receipt not in _SPENT.issued:
        raise AuthorityError(
            "no snapshot issued this token; a capability is granted, never computed"
        )
    if receipt in _SPENT.used:
        raise AuthorityError("this token has already been spent")
    snap = Path(token.snapshot)
    if not snap.exists():
        raise AuthorityError("the snapshot this token refers to no longer exists")
    if _digest(snap) != token.digest:
        raise AuthorityError("the snapshot has changed since the token was issued")

    _SPENT.used.add(receipt)
    with closing(sqlite3.connect(db)) as conn:
        cursor = conn.execute(statement)
        return cursor.rowcount


def _target_of(statement: str) -> str:
    """The table or index a destructive statement names."""
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", statement)
    lowered = [w.lower() for w in words]
    for keyword in ("from", "table", "index", "view"):
        if keyword in lowered:
            i = lowered.index(keyword)
            if i + 1 < len(words):
                if words[i + 1].lower() in ("if", "exists"):
                    return words[i + 3] if i + 3 < len(words) else ""
                return words[i + 1]
    return words[-1] if words else ""


def reset_spent_tokens() -> None:
    """For tests only. Named so that using it in production reads as the mistake it is."""
    _SPENT.used.clear()
    _SPENT.issued.clear()


# --------------------------------------------------------------------------
# the migration ledger
# --------------------------------------------------------------------------


def load_migrations(path: Path) -> list[Migration]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for entry in data if isinstance(data, list) else []:
        try:
            out.append(Migration(str(entry["name"]), str(entry["up"]),
                                 str(entry.get("down", ""))))
        except (KeyError, TypeError):
            continue
    return out
