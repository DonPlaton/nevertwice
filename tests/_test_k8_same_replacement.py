#!/usr/bin/env python3
"""K8 layer 1: a same-slug note from another session is a sibling unless the replacement is proven.

The slug is a title the extractor composes - a topic, not a fact's identity - and two facts on one
topic share it. Until K8 the same-day collision was absorbed in place (the earlier statement no
longer served) and the other-day collision retired the earlier note by the slug alone: 5 of 40
explicit and 14 of 40 implicit still-true facts lost on the supersession stand's controls, 7 of 17
on the two-day dating (ledger K8). Now one function decides for both branches, with no model call,
and only on what the item and the note already carry:

  rule 1  the item's `supersedes` / `contradicts` names this title      -> replace
  rule 4  the item carries no literal and the note does                  -> sibling, never
  rule 2  literals on both sides, every old one in the new block         -> replace
  rule 2' the old statement's text stands inside the new one             -> replace (restated)
  else    a `-2` sibling; the earlier note is stamped `contested`        -> both served

Every rule below is broken by hand once to show the check would catch it (mutation check). No LLM,
no embedder: the extraction backend raises if touched, which is the "no new call in the hook"
contract and the mode without a model in one.

    python tests/_test_k8_same_replacement.py
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m  # noqa: E402
import digest as dg  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

RUN, FAILED = [], []
CALLS: list[str] = []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def boom(prompt, project=None, **kw):
    CALLS.append(prompt)
    raise AssertionError("the write path made a model call")


PROJ = "k8proj"
S1, S2 = "2026-06-01-1000-k8proj-session-aaaaaaaa", "2026-06-01-1100-k8proj-session-bbbbbbbb"
F = m._FACTS_MARK
OLD_FACT = f"The HTTP client timeout is 30 seconds.{F}the HTTP client timeout is 30 seconds"
NEW_FACT = f"The HTTP client timeout is 5 seconds.{F}the HTTP client timeout is 5 seconds"


def fresh():
    CALLS.clear()
    d = make_sandbox(m, "k8l1_", offline=True)
    m.generate_json = boom
    m.call_ollama = boom
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


# ── the rule on its own ───────────────────────────────────────────────────────────────────────
print("\n- the rule, pair by pair -")
d = fresh()
old = write(OLD_FACT, S1)
p_old = note(old)
check("rule 1: an item naming this title as superseded replaces",
      m._same_replacement(p_old, "http client timeout", "now 5 seconds", "http client timeout", "") == (True, "explicit"))
check("rule 1 through contradicts too",
      m._same_replacement(p_old, "http client timeout", "now 5 seconds", "", "HTTP client timeout") == (True, "explicit"))
check("rule 4: a lesson without literals never absorbs a note with them",
      m._same_replacement(p_old, "http client timeout", "Touched three files around the HTTP client timeout is 30 seconds.")
      == (False, "no_literals_in_new"))
check("rule 2: the new block carries every old literal -> the same fact refined",
      m._same_replacement(p_old, "http client timeout",
                          f"Confirmed.{F}the HTTP client timeout is 30 seconds · retries are 3") == (True, "literals"))
check("disagreeing literals are not proven the same fact",
      m._same_replacement(p_old, "http client timeout", NEW_FACT) == (False, "unproven"))
lesson = write("Use redis for the cache.", S1, title="cache backend")
check("rule 2': the old statement's words inside the new -> restated",
      m._same_replacement(note(lesson), "cache backend", "Use redis for the cache. Confirmed again this week.")
      == (True, "restated"))
check("other words, no literals on either side -> unproven",
      m._same_replacement(note(lesson), "cache backend", "The cache is served by redis.") == (False, "unproven"))
check("an unreadable earlier note is kept (fails closed)",
      m._same_replacement(m.VAULT / "nowhere.md", "x", "y") == (False, "unreadable"))

# ── branch d: the same day, another session ──────────────────────────────────────────────────
print("\n- branch d (same day, another session) -")
d = fresh()
old = write(OLD_FACT, S1)
new = write(NEW_FACT, S2)
check("disagreeing literals -> a '-2' sibling, not an absorb", new == f"{old}-2", new)
check("the earlier statement is still served, untouched", served(old) == OLD_FACT and "Previous statement" not in note(old).read_text(encoding="utf-8"))
check("the earlier note is stamped contested with the sibling's stem", contested(old) == [new], str(contested(old)))
check("the sibling is live and serves the new statement", note(new).exists() and served(new) == NEW_FACT)
check("nothing went to Superseded/", not (m.VAULT / "Decisions" / "Superseded").exists()
      or not list((m.VAULT / "Decisions" / "Superseded").glob("*.md")))
third = write(f"The HTTP client timeout is 7 seconds.{F}the HTTP client timeout is 7 seconds", "2026-06-01-1200-k8proj-session-cccccccc")
check("a third statement is a '-3' sibling and the earlier note lists both", third == f"{old}-3" and contested(old) == [new, third])

d = fresh()
old = write(OLD_FACT, S1)
new = write(f"Refined: the HTTP client timeout is 30 seconds on every route.{F}the HTTP client timeout is 30 seconds · every route", S2)
check("rule 2 absorbs in place (same stem), the refined text served", new == old and "every route" in served(old))
check("no contested stamp on an absorb", contested(old) is None)

d = fresh()
old = write("Use redis for the cache.", S1, title="cache backend")
new = write("Use redis for the cache. Confirmed again.", S2, title="cache backend")
check("rule 2' absorbs the restatement in place", new == old)
fm = m._read_frontmatter_file(note(old))
check("the restatement still counts as a recurrence (two sources)", set(fm.get("sources") or []) == {S1, S2})

d = fresh()
old = write(OLD_FACT, S1)
new = write("The timeout was dropped altogether.", S2, supersedes="http client timeout")
check("rule 1 absorbs in place: the item named this title", new == old and "dropped" in served(old))

d = fresh()
old = write(OLD_FACT, S1)
new = write("Touched three files around the HTTP client timeout is 30 seconds and nothing else.", S2)
check("rule 4 keeps a lesson without literals apart from a fact note - even when the words match",
      new == f"{old}-2" and served(old) == OLD_FACT and contested(old) == [new])

d = fresh()
old = write(OLD_FACT, S1)
same = write(NEW_FACT, S1)
check("the same session re-encountering its own note still refreshes in place (not a sibling)", same == old)

# ── branch r: another day ─────────────────────────────────────────────────────────────────────
print("\n- branch r (another day) -")
d = fresh()
old = write(OLD_FACT, S1, date="2026-03-01")
new = write(NEW_FACT, S2, date="2026-05-01")
check("disagreeing literals on another day -> both live, nothing retired",
      note(old).exists() and note(new).exists() and not note(old, where="Superseded").exists())
check("the earlier note is stamped contested", contested(old) == [new])
check("the new note does not claim to supersede anything", not m._read_frontmatter_file(note(new)).get("supersedes"))

d = fresh()
old = write(OLD_FACT, S1, date="2026-03-01")
new = write(f"Now the HTTP client timeout is 30 seconds everywhere.{F}the HTTP client timeout is 30 seconds · everywhere", S2, date="2026-05-01")
check("rule 2 on another day retires the earlier note by the slug, as before",
      note(old, where="Superseded").exists() and "superseded_via: slug" in note(old, where="Superseded").read_text(encoding="utf-8"))

d = fresh()
old = write(OLD_FACT, S1, date="2026-03-01")
new = write("The timeout is gone.", S2, date="2026-05-01", supersedes="http client timeout")
check("rule 1 on another day retires the earlier note, attributed to the explicit path",
      note(old, where="Superseded").exists() and "superseded_via: explicit" in note(old, where="Superseded").read_text(encoding="utf-8"))

# ── the explicit path across slugs: rule 1 as written, and the owner's switch ─────────────────
print("\n- an explicit claim naming ANOTHER title -")
d = fresh()
tempo = write("Traces are exported to Tempo.", S1, title="traces exported to tempo", ntype="pattern")
loki = write("Logs go to Loki.", S2, title="logs exported to loki", ntype="pattern", contradicts="traces exported to Tempo")
check("default (write): the named note is retired on the spot, attributed to the explicit path",
      note(tempo, "pattern", "Superseded").exists()
      and "superseded_via: explicit" in note(tempo, "pattern", "Superseded").read_text(encoding="utf-8"))
d = fresh()
m.EXPLICIT_RETIRE = "judge"
tempo = write("Traces are exported to Tempo.", S1, title="traces exported to tempo", ntype="pattern")
loki = write("Logs go to Loki.", S2, title="logs exported to loki", ntype="pattern", contradicts="traces exported to Tempo")
check("NEVERTWICE_EXPLICIT_RETIRE=judge: the named note stays live, stamped contested for the judge",
      note(tempo, "pattern").exists() and not note(tempo, "pattern", "Superseded").exists()
      and contested(tempo, "pattern") == [loki], str(contested(tempo, "pattern")))
check("the claiming note records nothing it did not do", not m._read_frontmatter_file(note(loki, "pattern")).get("supersedes"))
m.EXPLICIT_RETIRE = "write"

# ── the twin gate does not undo the rule ──────────────────────────────────────────────────────
print("\n- the near-duplicate gate leaves a kept-apart sibling alone -")
d = fresh()
old = write(OLD_FACT, S1)
_nd = m._near_duplicate_paths
m._near_duplicate_paths = lambda *a, **k: [note(old)]          # pretend the cosine gate fires on the earlier note
new = write(NEW_FACT, S2)
check("a sibling the rule kept apart is not retired by the twin gate a moment later",
      new == f"{old}-2" and note(old).exists() and not note(old, where="Superseded").exists())
m._near_duplicate_paths = _nd

# ── mutation checks: break each rule by hand, the check must redden ──────────────────────────
print("\n- mutation checks -")
d = fresh()
_real = m._same_replacement
m._same_replacement = lambda *a, **k: (True, "broken")
old = write(OLD_FACT, S1)
new = write(NEW_FACT, S2)
check("a rule that says 'replace' to everything is caught: the earlier fact would be absorbed",
      new == old and served(old) != OLD_FACT)
m._same_replacement = _real

d = fresh()
_facts = m._facts_in
m._facts_in = lambda desc: set()                                # rule 4 blind: no side has literals
old = write(OLD_FACT, S1)
new = write("Touched three files. " + OLD_FACT.split(F)[0], S2)
check("rule 4 broken by hand is caught: the lesson would absorb the fact note by rule 2'", new == old)
m._facts_in = _facts

d = fresh()
_mark = m._mark_contested
m._mark_contested = lambda old_path, new_stem: True             # the stamp silently dropped
old = write(OLD_FACT, S1)
new = write(NEW_FACT, S2)
check("a dropped contested stamp is caught", new == f"{old}-2" and contested(old) is None)
m._mark_contested = _mark

# ── no call, and the mode without a model ─────────────────────────────────────────────────────
print("\n- zero calls; the pairs are visible without a model -")
d = fresh()
old = write(OLD_FACT, S1)
new = write(NEW_FACT, S2)
lesson_old = write("Use redis for the cache.", S1, title="cache backend", ntype="pattern")
lesson_new = write("The cache is served by redis.", S2, title="cache backend", ntype="pattern")
check("no model call was made on the write path", CALLS == [], str(len(CALLS)))
rows = dg.compute_conflicts(None)
kinds = {(r["kind"], r["old_stem"], r["new_stem"]) for r in rows}
check("conflicts() lists every contested pair, kind 'contested'",
      ("contested", old, new) in kinds and ("contested", lesson_old, lesson_new) in kinds, str(kinds))
check("a contested record is unresolved and carries both dates",
      all(not r["resolved"] and r["old_date"] and r["new_date"] for r in rows if r["kind"] == "contested"))
check("_iter_contested reads them by project too",
      sorted(c["stem"] for c in m._iter_contested(PROJ)) == sorted([old, lesson_old])
      and m._iter_contested("other") == [])
check("note meta carries the stamp", any(n["stem"] == old and n.get("contested") == [new] for n in m._iter_project_notes(PROJ)))

print(f"\nK8 layer 1: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
