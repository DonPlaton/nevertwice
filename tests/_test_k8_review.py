#!/usr/bin/env python3
"""Regression tests for the xhigh code review on k8/zero-loss (HEAD 2de439d).

One file per the repo's test-discipline rule ("or a new tests/_test_k8_review.py"), sectioned by
finding id. Each section is a direct regression check plus, where a single named helper carries the
fix, a mutation check that breaks that helper by hand and confirms the section above would redden.

    python tests/_test_k8_review.py
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


F = m._FACTS_MARK
PROJ = "k8proj"
OLD_FACT = f"The HTTP client timeout is 30 seconds.{F}the HTTP client timeout is 30 seconds"
NEW_FACT = f"The HTTP client timeout is 5 seconds.{F}the HTTP client timeout is 5 seconds"


def fresh():
    d = make_sandbox(m, "k8rv_", offline=True)
    m.generate_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call on the write path"))
    m.call_ollama = m.generate_json
    return d


def write(desc, session, date="2026-06-01", title="http client timeout", ntype="decision", **extra):
    item = {"title": title, "description": desc, **extra}
    return m.write_typed_note(m.TYPE_FOLDER[ntype], item, PROJ, date, ["t"], ntype, session_stem_=session)


def note(stem, ntype="decision", where=""):
    return m.VAULT / m.TYPE_FOLDER[ntype] / where / f"{stem}.md"


def served(stem, ntype="decision"):
    _, desc, _ = m._parse_note_body(note(stem, ntype).read_text(encoding="utf-8").split("\n"))
    return desc or ""


def contested(stem, ntype="decision", where=""):
    return m._read_frontmatter_file(note(stem, ntype, where)).get("contested")


def fm_of(stem, ntype="decision", where=""):
    return m._read_frontmatter_file(note(stem, ntype, where))


S1 = "2026-06-01-1000-k8proj-session-aaaaaaaa"
S2 = "2026-06-01-1100-k8proj-session-bbbbbbbb"

# ── F1: a same-session refresh must not drop or invert the contested stamp ────────────────────
print("\n- F1: a same-session refresh carries the contested stamp forward, in the right direction -")
d = fresh()
old = write(OLD_FACT, S1)
sib = write(NEW_FACT, S2)
check("setup: the earlier note is contested against the sibling", contested(old) == [sib])
refreshed = write(OLD_FACT, S1)                          # same session: a crash-retry / grown-transcript refresh
check("the refresh absorbs in place (same stem), not a new sibling", refreshed == old)
check("the contested stamp survives the absorb rewrite instead of being dropped", contested(old) == [sib])
check("the sibling gains no reverse stamp - direction stays earlier -> later", contested(sib) is None)

print("\n- mutation check: a direction guard blind to the note's own carried stamp re-inverts it -")
d = fresh()
old = write(OLD_FACT, S1)
sib = write(NEW_FACT, S2)
_real_contested_of = m._contested_of
m._contested_of = lambda fm: []                          # pretend the guard cannot see what this note already carries
_ = write(OLD_FACT, S1)
check("blinded, the sibling wrongly gains a reverse stamp pointing at the earlier note",
      contested(sib) == [old])
m._contested_of = _real_contested_of

# ── F3: rule 2 needs a VALUE-shaped literal, not merely a shared context ───────────────────────
print("\n- F3: a shared context path alone does not prove replacement -")
d = fresh()
cfg_old = write(f"The service reads its config from docker-compose.yml.{F}docker-compose.yml",
                S1, title="service config source")
cfg_new = write(f"The service now also binds port 9090, per docker-compose.yml.{F}docker-compose.yml · port 9090",
                S2, title="service config source")
check("a different fact about the same file is a sibling, not an absorb",
      cfg_new == f"{cfg_old}-2" and contested(cfg_old) == [cfg_new], str(contested(cfg_old)))
check("_has_value_literal rejects a bare context literal", not m._has_value_literal({"docker-compose.yml"}))
check("...but accepts a number+unit, a version, or a hex identifier (the commit sha shape)",
      m._has_value_literal({"the timeout is 30 seconds"}) and m._has_value_literal({"v2.0"})
      and m._has_value_literal({"87c8b17"}))

print("\n- mutation check: without the value-shaped gate, the shared context wrongly proves it -")
d = fresh()
_real_hvl = m._has_value_literal
m._has_value_literal = lambda facts: True
old2 = write(f"The service reads its config from docker-compose.yml.{F}docker-compose.yml",
             S1, title="service config source mut")
new2 = write(f"The service now also binds port 9090, per docker-compose.yml.{F}docker-compose.yml · port 9090",
             S2, title="service config source mut")
check("without the gate, a different fact about the same file is wrongly absorbed", new2 == old2)
m._has_value_literal = _real_hvl

print("\n- F3: rule 2' does not fire on disagreeing facts blocks even when the prose is a substring -")
d = fresh()
prose_old = write(f"Confirmed the timeout setting.{F}the http client timeout is 30 seconds",
                  S1, title="timeout confirmation")
prose_new = write(f"Confirmed the timeout setting. Now it is 5 seconds.{F}the http client timeout is 5 seconds",
                  S2, title="timeout confirmation")
check("disagreeing facts block the restated-text rule, even though the old prose is a literal substring "
      "of the new prose",
      prose_new == f"{prose_old}-2" and contested(prose_old) == [prose_new], str(contested(prose_old)))
check("_same_replacement reports it unproven, not restated",
      m._same_replacement(note(prose_old), "timeout confirmation",
                          f"Confirmed the timeout setting. Now it is 5 seconds.{F}the http client timeout is 5 seconds")
      == (False, "unproven"))

print("\n- F3: rule 2/2' run the write-time guard - a hallucinated additional value is not absorbed -")
d = fresh()
pool_old = write(f"The connection pool size is 10.{F}the connection pool size is 10", S1, title="pool size")
pool_new = write(f"The connection pool size is 10, raised temporarily to 99 under load.{F}the connection pool size is 10",
                 S2, title="pool size")
check("a value the new facts block does not back is not proven replacement, even though the old value is unchanged",
      pool_new == f"{pool_old}-2" and served(pool_old) == f"The connection pool size is 10.{F}the connection pool size is 10"
      and contested(pool_old) == [pool_new])
check("_same_replacement names the veto reason",
      m._same_replacement(note(pool_old), "pool size",
                          f"The connection pool size is 10, raised temporarily to 99 under load.{F}the connection pool size is 10")
      == (False, "unverified_value"))

print("\n- mutation check: without the write-time guard, the hallucinated value is wrongly absorbed -")
d = fresh()
_real_uv_guard = m._unverified_values
m._unverified_values = lambda desc: []
old3 = write(f"The connection pool size is 10.{F}the connection pool size is 10", S1, title="pool size mut")
new3 = write(f"The connection pool size is 10, raised temporarily to 99 under load.{F}the connection pool size is 10",
             S2, title="pool size mut")
check("without the guard, the hallucinated '99' is silently absorbed as the same fact", new3 == old3)
m._unverified_values = _real_uv_guard

# ── F4: _unverified_values is whole-token membership, not substring ────────────────────────────
print("\n- F4: whole-token membership, not substring, for unverified-value tokens -")
check("'5 MB' is not verified merely because the block says '25 MB'",
      m._unverified_values(f"The limit is 5 MB now.{F}the limit is 25 mb") == ["5 mb"])
check("'v2' is not verified merely because the block says 'v2.0'",
      m._unverified_values(f"Now on v2 of the API.{F}the api is now on v2.0") == ["v2"])
check("'80' is not verified merely because the block says '8080'",
      m._unverified_values(f"Now listening on port 80.{F}the service listens on port 8080") == ["80"])
check("a value that IS a whole token in the block is verified",
      m._unverified_values(f"The limit is 25 MB now.{F}the limit is 25 mb") == [])


def _pre_f4_unverified_values(desc):
    """The pre-fix implementation: `tok not in facts` over the block's raw TEXT (a substring scan)."""
    statement = m._norm_statement(desc)
    facts = " ".join(m._facts_in(desc))
    out = []
    for mo in m._VALUE_RE.finditer(statement):
        tok = m._norm_ws(mo.group(0).strip("~+- "))
        if tok and not tok.isdigit() and tok not in facts and tok not in out:
            out.append(tok)
        elif tok and tok.isdigit() and len(tok) >= 2 and tok not in facts and tok not in out:
            out.append(tok)
    return out


print("\n- mutation check: substring semantics restored by hand let all three through unflagged -")
_real_uv = m._unverified_values
m._unverified_values = _pre_f4_unverified_values
check("'5 MB' passes against '25 MB' unflagged (the bug)",
      m._unverified_values(f"The limit is 5 MB now.{F}the limit is 25 mb") == [])
check("'v2' passes against 'v2.0' unflagged (the bug)",
      m._unverified_values(f"Now on v2 of the API.{F}the api is now on v2.0") == [])
check("'80' passes against '8080' unflagged (the bug)",
      m._unverified_values(f"Now listening on port 80.{F}the service listens on port 8080") == [])
m._unverified_values = _real_uv

# ── F9: the near-dup gate and the retirement filters must key on the REAL absorb target ────────
print("\n- F9: a same-session refresh into a `-2` sibling excludes its OWN stem from the near-dup gate -")
d = fresh()
qbase = write("Use Kafka for the queue.", S2, title="queue backend f9")
qsib = write("Use RabbitMQ for the queue.", S1, title="queue backend f9")   # disagrees -> S1's note is the '-2'
check("setup: S1's note is the '-2' sibling, S2's note kept the base stem", qsib == f"{qbase}-2", qsib)
seen_exclude = []


def _fake_ndp(folder_path, project, ntype, title, desc, prevention, exclude, entities=None):
    seen_exclude.append(set(exclude))
    return []


_real_ndp = m._near_duplicate_paths
m._near_duplicate_paths = _fake_ndp
_ = write("Use RabbitMQ for the queue.", S1, title="queue backend f9")      # S1 refreshes its own sibling
check("the gate was called, excluding the absorb target's own stem (write_stem), not just base_stem",
      bool(seen_exclude) and qsib in seen_exclude[-1] and qbase in seen_exclude[-1], str(seen_exclude))
m._near_duplicate_paths = _real_ndp

print("\n- F9: the same refresh never supersedes its own stem, even when the gate returns it anyway -")
d = fresh()
qbase = write("Use Kafka for the queue.", S2, title="queue backend f9b")
qsib = write("Use RabbitMQ for the queue.", S1, title="queue backend f9b")
_real_ndp2 = m._near_duplicate_paths
m._near_duplicate_paths = lambda *a, **k: [note(qsib)]     # the pre-fix gate: ignores exclude, returns the target itself
refreshed = write("Use RabbitMQ for the queue.", S1, title="queue backend f9b")
check("the refresh still absorbs into the sibling, not a spurious self-supersede",
      refreshed == qsib and served(qsib) == "Use RabbitMQ for the queue.")
check("no self-reference lands in its own supersedes list",
      qsib not in (fm_of(qsib).get("supersedes") or []), str(fm_of(qsib).get("supersedes")))
m._near_duplicate_paths = _real_ndp2

print(f"\nxhigh review (write path): {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
