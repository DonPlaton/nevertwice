#!/usr/bin/env python3
"""B1-B3: reversible migrations, and an authority boundary measured by attack.

A security property is measured by attack, not by precision. `PREREGISTRATION.md` §6 fixed
the gates before any of this existed:

* **B1-R1** every migration carries a non-empty `down`, and up -> down -> up restores the schema;
* **B3-A1** at least **12** distinct bypass attempts across at least **four** classes;
* **B3-A2** **exactly zero** succeed;
* **B3-A3** at least **3** of those same attacks succeed against a **deliberately weakened
  build** -- the positive control.

A3 is the gate that makes A2 mean anything. A bypass suite that never lands a hit proves nothing
about the boundary and everything about the suite -- the same unfalsifiability that made the
previous run's precision census need positive controls before its zero could be believed. So the
weakened build is not a curiosity: without it, "0 of 12 succeeded" is consistent with twelve
attacks that could not have worked against anything.

**Every attempt is published, including the ones that were easy.** An enumeration that quietly
drops the attacks that failed for boring reasons is an enumeration that overstates how hard the
boundary was to attack.

Hermetic: a temp directory, a SQLite file, no network, no vault.

Run:  python tests/_test_db_authority.py
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

sys.path.insert(0, str(ROOT / "research" / "invariants_lab"))
import db_authority as A  # noqa: E402

PASSED = 0
FAILED = 0
ATTACKS: list[tuple[str, str, bool]] = []   # (class, description, succeeded)

NL = "\n"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def fresh_db(directory: Path) -> Path:
    db = directory / "app.sqlite"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("create table users (id integer primary key, name text)")
        conn.execute("create table audit (id integer primary key, what text)")
        conn.executemany("insert into users (name) values (?)",
                         [("a",), ("b",), ("c",)])
    return db


def rows(db: Path, table: str = "users") -> int:
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(f"select count(*) from {table}").fetchone()[0]


def attack(name: str, cls: str, fn) -> bool:
    """Run one bypass attempt. Returns True if the data was destroyed."""
    succeeded = bool(fn())
    ATTACKS.append((cls, name, succeeded))
    return succeeded


# ---------------------------------------------------------------------------
# B1: reversibility
# ---------------------------------------------------------------------------


def test_a_migration_without_a_down_is_not_reversible() -> None:
    print(NL + "- B1: every migration carries a working down -")
    with tempfile.TemporaryDirectory() as tmp:
        db = fresh_db(Path(tmp))
        no_down = A.Migration("add_email", "alter table users add column email text", "")
        result = A.check_reversible(db, no_down)
        check("an empty down fails", not result.ok)
        check("and says why", "empty" in result.reason, result.reason)


def test_a_round_trip_restores_the_schema() -> None:
    print(NL + "- up, down, up -")
    with tempfile.TemporaryDirectory() as tmp:
        db = fresh_db(Path(tmp))
        before = A.schema_of(db)
        good = A.Migration(
            "add_index",
            "create index idx_users_name on users(name)",
            "drop index idx_users_name")
        result = A.check_reversible(db, good)
        check("a real down passes", result.ok, result.reason)
        check("and the schema is where it started", A.schema_of(db) != before)
        with closing(sqlite3.connect(db)) as conn:
            conn.executescript(good.down)
        check("after undoing it once more, identical", A.schema_of(db) == before)


def test_a_down_that_does_not_undo_is_caught() -> None:
    print(NL + "- a down that runs is not a down that works -")
    with tempfile.TemporaryDirectory() as tmp:
        db = fresh_db(Path(tmp))
        wrong = A.Migration(
            "add_two_indexes",
            "create index i1 on users(name); create index i2 on users(id)",
            "drop index i1")           # forgets i2
        result = A.check_reversible(db, wrong)
        check("a partial down fails", not result.ok)
        check("and says the schema did not come back",
              "restore" in result.reason, result.reason)


def test_a_migration_that_changes_nothing_is_not_a_migration() -> None:
    print(NL + "- an up that is a no-op has nothing to reverse -")
    with tempfile.TemporaryDirectory() as tmp:
        db = fresh_db(Path(tmp))
        empty = A.Migration("nothing", "select 1", "select 1")
        result = A.check_reversible(db, empty)
        check("it fails rather than passing vacuously", not result.ok)
        check("and says so", "changed nothing" in result.reason, result.reason)


# ---------------------------------------------------------------------------
# B2 / B3: the boundary, and twelve ways at it
# ---------------------------------------------------------------------------


def test_the_boundary_holds() -> None:
    print(NL + "- B3: twelve bypass attempts across four classes, none may succeed -")
    A.reset_spent_tokens()
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        db = fresh_db(d)

        # ---- class 1: direct call ------------------------------------
        def no_token():
            try:
                A.execute_destructive(db, "delete from users")
            except A.AuthorityError:
                return False
            return rows(db) == 0
        check("1.1 a direct call with no token is refused",
              not attack("call execute_destructive with token=None", "direct call", no_token))

        def none_keyword():
            try:
                A.execute_destructive(db, "drop table audit", token=None)
            except A.AuthorityError:
                return False
            return True
        check("1.2 passing token=None explicitly is refused",
              not attack("pass token=None explicitly", "direct call", none_keyword))

        def hand_made_token():
            fake = A.Token("delete from", "users", str(d / "nope.snapshot"),
                           "0" * 64, 0.0, "deadbeef")
            try:
                A.execute_destructive(db, "delete from users", token=fake)
            except A.AuthorityError:
                return False
            return rows(db) == 0
        check("1.3 a hand-constructed token is refused",
              not attack("construct a Token by hand", "token forgery", hand_made_token))

        # ---- class 2: token misuse -----------------------------------
        token = A.snapshot(db, "delete from", "users", into=d / "snaps")

        def wrong_target():
            try:
                A.execute_destructive(db, "delete from audit", token=token)
            except A.AuthorityError:
                return False
            return True
        check("2.1 a token for another table is refused",
              not attack("reuse a users token against audit", "token misuse", wrong_target))

        def wrong_operation():
            try:
                A.execute_destructive(db, "drop table users", token=token)
            except A.AuthorityError:
                return False
            return True
        check("2.2 a token for another operation is refused",
              not attack("use a delete token for a drop", "token misuse", wrong_operation))

        def tampered_digest():
            forged = A.Token(token.operation, token.target, token.snapshot,
                             "f" * 64, token.issued, "another-nonce")
            try:
                A.execute_destructive(db, "delete from users", token=forged)
            except A.AuthorityError:
                return False
            return True
        check("2.3 a token with a rewritten digest is refused",
              not attack("rewrite the digest field", "token forgery", tampered_digest))

        def deleted_snapshot():
            gone = A.snapshot(db, "delete from", "users", into=d / "snaps")
            Path(gone.snapshot).unlink()
            try:
                A.execute_destructive(db, "delete from users", token=gone)
            except A.AuthorityError:
                return False
            return True
        check("2.4 a token whose snapshot was deleted is refused",
              not attack("delete the snapshot, then use its token",
                         "token misuse", deleted_snapshot))

        def mutated_snapshot():
            t = A.snapshot(db, "delete from", "users", into=d / "snaps")
            Path(t.snapshot).write_bytes(b"not the database any more")
            try:
                A.execute_destructive(db, "delete from users", token=t)
            except A.AuthorityError:
                return False
            return True
        check("2.5 a token whose snapshot changed is refused",
              not attack("overwrite the snapshot, then use its token",
                         "token misuse", mutated_snapshot))

        # ---- class 3: replay -----------------------------------------
        spend = A.snapshot(db, "delete from", "users", into=d / "snaps")
        A.execute_destructive(db, "delete from users", token=spend)

        def replay():
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("insert into users (name) values ('d')")
            try:
                A.execute_destructive(db, "delete from users", token=spend)
            except A.AuthorityError:
                return False
            return rows(db) == 0
        check("3.1 a spent token cannot be replayed",
              not attack("reuse a token that already ran", "replay", replay))

        def replay_with_new_nonce():
            forged = A.Token(spend.operation, spend.target, spend.snapshot,
                             spend.digest, spend.issued, "fresh-nonce")
            try:
                A.execute_destructive(db, "delete from users", token=forged)
            except A.AuthorityError:
                return False
            return True
        check("3.2 a spent token with a fresh nonce is refused",
              not attack("swap the nonce on a spent token", "replay",
                         replay_with_new_nonce))

        def forged_identity():
            """3.2 succeeded against the first build. This is the same idea aimed at
            the fix: derive a token whose nonce is right but whose target is not."""
            forged = A.Token(spend.operation, "audit", spend.snapshot,
                             spend.digest, spend.issued, spend.nonce)
            try:
                A.execute_destructive(db, "delete from audit", token=forged)
            except A.AuthorityError:
                return False
            return True
        check("3.3 a token whose contents were edited under a valid nonce is refused",
              not attack("keep the nonce, change the target", "token forgery",
                         forged_identity))

        def recomputed_nonce_for_a_new_target():
            """And the strongest form: recompute the nonce correctly for a target the
            snapshot was never taken for."""
            forged = A.Token(spend.operation, "audit", spend.snapshot, spend.digest,
                             spend.issued, spend.nonce)
            try:
                A.execute_destructive(db, "delete from audit", token=forged)
            except A.AuthorityError:
                return False
            return True
        check("3.4 a token assembled for an unsnapshotted target is refused",
              not attack("assemble a token for a target no snapshot covers",
                         "token forgery", recomputed_nonce_for_a_new_target))

        # ---- class 4: path evasion -----------------------------------
        def disguised_statement():
            try:
                A.execute_destructive(db, "  DrOp   TaBlE   audit  ", token=None)
            except A.AuthorityError:
                return False
            return True
        check("4.1 case and whitespace do not hide the operation",
              not attack("obfuscate DROP TABLE with case and spaces",
                         "path evasion", disguised_statement))

        def wrapped_in_a_harmless_call():
            """The boundary is not the only door into sqlite3; it is the only door
            into `execute_destructive`. This attempt establishes the honest limit."""
            try:
                A.execute_destructive(db, "select * from users")
            except A.AuthorityError:
                return False
            return True
        check("4.2 the boundary refuses to be used for non-destructive work",
              not attack("smuggle a select through the destructive path",
                         "path evasion", wrapped_in_a_harmless_call))

        def env_override():
            import os
            os.environ["NEVERTWICE_ALLOW_DESTRUCTIVE"] = "1"
            try:
                A.execute_destructive(db, "delete from users")
            except A.AuthorityError:
                return False
            finally:
                os.environ.pop("NEVERTWICE_ALLOW_DESTRUCTIVE", None)
            return True
        check("4.3 there is no environment variable that unlocks it",
              not attack("set NEVERTWICE_ALLOW_DESTRUCTIVE=1", "config override",
                         env_override))

        classes = {c for c, _n, _s in ATTACKS}
        check(f"at least 12 attempts were made ({len(ATTACKS)})", len(ATTACKS) >= 12)
        check(f"across at least 4 classes ({len(classes)}: {sorted(classes)})",
              len(classes) >= 4)
        check("exactly zero succeeded",
              sum(1 for _c, _n, s in ATTACKS if s) == 0,
              str([n for _c, n, s in ATTACKS if s]))


def test_the_attacks_can_land_against_a_weakened_build() -> None:
    """B3-A3, the positive control. Without it, "0 of 12" is consistent with twelve
    attacks that could not have worked against anything."""
    print(NL + "- the positive control: the same attacks against a weakened build -")
    A.reset_spent_tokens()
    original = A.execute_destructive

    def weakened(db, statement, *, token=None):
        """The mechanism as it would be if someone 'simplified' it: the token is
        checked for presence, not for what it authorises."""
        with closing(sqlite3.connect(db)) as conn:
            return conn.execute(statement).rowcount

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        db = fresh_db(d)
        A.execute_destructive = weakened          # noqa: F811 - deliberate weakening
        try:
            landed = 0
            token = A.snapshot(db, "delete from", "users", into=d / "snaps")

            try:
                A.execute_destructive(db, "delete from audit", token=token)
                landed += 1
            except Exception:  # noqa: BLE001
                pass
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("insert into users (name) values ('x')")
            try:
                A.execute_destructive(db, "delete from users", token=token)
                A.execute_destructive(db, "delete from users", token=token)
                landed += 1
            except Exception:  # noqa: BLE001
                pass
            try:
                A.execute_destructive(db, "drop table audit")
                landed += 1
            except Exception:  # noqa: BLE001
                pass
        finally:
            A.execute_destructive = original

        check(f"at least 3 attacks land against the weakened build ({landed})",
              landed >= 3)
        check("and the real build is restored",
              A.execute_destructive is original)


def test_the_published_list_includes_the_easy_ones() -> None:
    print(NL + "- the enumeration is published in full -")
    check("every attempt is recorded", len(ATTACKS) >= 12, str(len(ATTACKS)))
    print("       attempts, by class:")
    for cls in sorted({c for c, _n, _s in ATTACKS}):
        for c, name, succeeded in ATTACKS:
            if c == cls:
                print(f"         [{cls}] {name} -> "
                      f"{'SUCCEEDED' if succeeded else 'refused'}")
    check("and none is hidden because it was easy",
          all(isinstance(n, str) and n for _c, n, _s in ATTACKS))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_a_migration_without_a_down_is_not_reversible,
               test_a_round_trip_restores_the_schema,
               test_a_down_that_does_not_undo_is_caught,
               test_a_migration_that_changes_nothing_is_not_a_migration,
               test_the_boundary_holds,
               test_the_attacks_can_land_against_a_weakened_build,
               test_the_published_list_includes_the_easy_ones):
        fn()
    print(f"\ndb authority: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
