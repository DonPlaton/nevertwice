#!/usr/bin/env python3
"""Four writes that lost something, each one the error arm of a fix that works on the happy path.

* `bootstrap_contexts --force` splices a fresh head in front of the accumulated session history.
  Its read of that history caught `OSError` and fell through to a head-only write, so any failure
  to read the tail destroyed it - the data loss the comment above it says critic R3 fixed, reached
  through the error path instead of the happy one.
* `mark_resolved`'s `write_atomic` was the one unguarded call among the deferred post-write stages.
  It runs after the new note is durably on disk and after the retirement loop, so on `api.remember`
  it escaped with the note written, its predecessors retired, and the index, the embeddings and the
  git snapshot all skipped - while the caller was told the write failed.
* `finalize()` ran inside a `try:` whose only companion was `finally: release_lock()`. One `OSError`
  out of `rebuild_index` - Obsidian or an AV scanner holding `Index.md` past the replace retry -
  escaped `main()` and took `git_autocommit` with it, so the run that wrote the notes never
  snapshotted them.
* `_ensure_vault_gitignore()` was called only inside the auto-init branch, so its reconcile half
  could never run on a store that already had `.git` - every store `install.py` touched. Measured on
  a real store: an 84.9 MB derived cache tracked across 13 commits, because `*.prev` reached the
  ignore list in this file and never reached the store.

    python tests/_test_writes_that_lose_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

from _sandbox import make_sandbox  # noqa: E402

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


# `offline=True` stubs git so no section can reach a real repo; the ignore-list check below needs
# the real one, so it is captured before the stub goes in.
REAL_AUTOCOMMIT = m.git_autocommit

make_sandbox(m, offline=True)

print("# a re-seed that cannot read the history refuses rather than overwriting it")
import bootstrap_contexts as bc  # noqa: E402

ctx = m.VAULT / "Context"
ctx.mkdir(parents=True, exist_ok=True)
page = ctx / "demo.md"
HISTORY = ("---\ntype: context\n---\n\n# demo\n\nseed text\n\n"
           "## 2026-03-01\n\nthe first session said something.\n\n"
           "## 2026-03-02\n\nthe second session said something else.\n")
page.write_text(HISTORY, encoding="utf-8")
broken, m._split_context = m._split_context, lambda *a, **k: (_ for _ in ()).throw(ValueError("torn"))
try:
    bc.write_context("demo", {"description": "d", "stack": [], "purpose": "p",
                              "current_state": "s", "structure_overview": "o",
                              "key_files": [], "tags": [], "next_steps": []},
                     Path("D:/Coding/demo"))
except Exception as exc:                                       # noqa: BLE001
    print(f"    (write_context raised {type(exc).__name__}, which is also not a loss)")
finally:
    m._split_context = broken
check("the accumulated history is still on disk", "the first session said something" in
      page.read_text(encoding="utf-8"), "the file was rewritten head-only")

print("# mark_resolved returns False when its write fails, like its sibling supersede_note")
m.write_typed_note("Mistakes", {"title": "flaky migration", "description": "It ran twice."},
                   "demo", "2026-02-01", [], "mistake")
fp = m.VAULT / "Mistakes" / "2026-02-01-demo-mistake-flaky-migration.md"
real_write = m.write_atomic
m.write_atomic = lambda *a, **k: (_ for _ in ()).throw(PermissionError("held open"))
try:
    got = m.mark_resolved(fp, "2026-02-02-demo-decision-fix-it")
    ok, detail = got is False, f"returned {got!r}"
except Exception as exc:                                       # noqa: BLE001 - that is the defect
    ok, detail = False, f"raised {type(exc).__name__}: {exc}"
finally:
    m.write_atomic = real_write
check("a failed stamp is a False, not an exception out of the write path", ok, detail)

print("# the ignore list is reconciled on a store that already has git")
make_sandbox(m)
m.git_autocommit = REAL_AUTOCOMMIT
m.write_typed_note("Mistakes", {"title": "t", "description": "d."}, "demo", "2026-01-01", [],
                   "mistake")
m.git_autocommit()                                   # first call inits and writes the list
gi = m.VAULT / ".gitignore"
gi.write_text("# someone trimmed this\n.logs/\n", encoding="utf-8")
m.git_autocommit()                                   # the store now HAS .git - the old blind spot
text = gi.read_text(encoding="utf-8") if gi.exists() else ""
check("a trimmed ignore list is reconciled on the next run", "*.prev" in text,
      f"the list reads {text!r}")
check("and the caller's own lines are kept", ".logs/" in text)

print("# finalize survives a stage that fails, and still reaches the git snapshot")
src = (ROOT / "nevertwice" / "_engine.py").read_text(encoding="utf-8")
seg = src[src.index("def finalize(swept_count: int):"):]
seg = seg[:seg.index("if event == \"SessionStart\"")]
check("every finalize stage runs under its own guard", seg.count("except Exception") >= 3,
      f"{seg.count('except Exception')} guard(s) in finalize")
check("and the git snapshot is one of the guarded stages", "git snapshot failed" in seg)

print("# a transcript rewritten in place at the same byte count is mined again")
import time  # noqa: E402

import ingest  # noqa: E402

make_sandbox(m, offline=True)
mined = []


def _fake_process(sid, cwd, path, trigger, db, **kw):
    mined.append(sid)
    db[sid] = {"ok": True}
    return True


m.process_session = _fake_process
docs = m.VAULT / "docs"
docs.mkdir(parents=True, exist_ok=True)
doc = docs / "rollout.txt"
db: dict = {}
doc.write_text("A" * 400, encoding="utf-8")
ingest.ingest_files([doc], "demo", "agent", db, settle_s=0)
first = len(mined)
ingest.ingest_files([doc], "demo", "agent", db, settle_s=0)
check("an unchanged file is still skipped without a read", len(mined) == first,
      f"{len(mined) - first} extra mine(s)")
time.sleep(0.02)
doc.write_text("B" * 400, encoding="utf-8")       # same size, different bytes
ingest.ingest_files([doc], "demo", "agent", db, settle_s=0)
check("a same-size rewrite reaches the prefix-hash proof and is mined", len(mined) == first + 1,
      "the size-only fast-skip continued before the proof could run, so the new content was "
      "never mined again")

print("# a stamp over a block-style list takes the list with it")
#: `_stamp_frontmatter` replaced the line whose key matched and moved on. A block-style list -
#: what Obsidian writes when a human edits a note's properties by hand, and what an import can
#: carry in - lives on the lines UNDER its key, so replacing `entities:` with an inline value
#: left `  - alpha` and `  - beta` sitting under a key that no longer describes them. The result
#: is YAML no parser accepts: a strict reader rejects the whole block, which costs the note every
#: other field in it, not just the list. The engine's own reader skips any line without a colon,
#: which is exactly why nothing here ever noticed.
NL = chr(10)
BLOCK = ("---" + NL + "title: a note" + NL + "entities:" + NL + "  - alpha" + NL +
         "  - beta" + NL + "recurrence: 1" + NL + "---" + NL + NL + "body text" + NL)


def _head(text: str) -> list[str]:
    return text.split(NL + "---", 1)[0].split(NL)


stamped = m._stamp_frontmatter(BLOCK, {"entities": ["gamma"]})
orphans = [ln for ln in _head(stamped) if ln.lstrip().startswith("- ")]
check("replacing a block list leaves none of its items behind", not orphans, repr(orphans))
fm, body = m._read_frontmatter(stamped)
check("the replaced key holds the new value", fm.get("entities") == ["gamma"], repr(fm))
check("and every other key survives", fm.get("title") == "a note" and fm.get("recurrence") == "1",
      repr(fm))
check("the body is untouched", body.strip() == "body text", repr(body))
keys = [ln.split(":", 1)[0] for ln in _head(stamped) if ":" in ln and ln[:1].strip()]
check("no key is written twice", len(keys) == len(set(keys)), repr(keys))

#: The collateral half: stamping a scalar must not disturb a block list it never named.
kept = m._stamp_frontmatter(BLOCK, {"recurrence": 2})
check("a scalar stamp leaves an untouched block list alone",
      [ln for ln in _head(kept) if ln.lstrip().startswith("- ")] == ["  - alpha", "  - beta"],
      repr(_head(kept)))
check("while still doing its own job", m._read_frontmatter(kept)[0].get("recurrence") == "2")

#: And a nested mapping is not a top-level key, whatever it is called. `_stamp_frontmatter`
#: documents itself as replacing TOP-LEVEL keys; matching on any indented line would let a
#: stamp rewrite a value inside somebody else's block.
NESTED = ("---" + NL + "title: a note" + NL + "entity_types:" + NL + "  recurrence: method" + NL +
          "recurrence: 1" + NL + "---" + NL + NL + "body text" + NL)
deep = m._stamp_frontmatter(NESTED, {"recurrence": 7})
check("a stamp does not reach inside a nested block",
      "  recurrence: method" in _head(deep), repr(_head(deep)))
check("it changes the top-level key instead",
      m._read_frontmatter(deep)[0].get("recurrence") == "7", repr(_head(deep)))


# ── the fifth: a write that changes the bytes it was handed ────────────
#
# `Path.write_text` translates every "\n" to `os.linesep`, so on Windows every file the engine
# published grew a "\r" per line, and text that ALREADY held CRLF - a transcript mined from a
# Windows host, a note from a CRLF editor - landed as "\r\r\n". Read back through universal
# newlines that is a BLANK LINE, so a body gained one per line on every consolidation rewrite.
# `write_atomic` is how almost every note, ledger and index in the project is published, and a
# read-back round-trip hides the whole class: only the bytes show it.
#
# A rule rather than five separate fixes, because the shape recurs in files that never call each
# other - `merge.py` keeps its own copy of the atomic write so git can invoke it import-free, and
# `hosts.py` writes a BACKUP of the user's settings. Two sites are exempt, each for a reason a
# reader can check: they generate a file rather than publish a caller's text.
import ast  # noqa: E402

_EXEMPT = {
    "dashboard.py": "generated HTML, never read back as the text it was handed",
    "doctor.py": "a four-byte liveness probe, deleted immediately",
}
_unguarded = []
for _f in sorted(Path(m.__file__).resolve().parent.glob("*.py")):
    for _n in ast.walk(ast.parse(_f.read_text(encoding="utf-8"))):
        if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
                and _n.func.attr == "write_text"
                and not any(k.arg == "newline" for k in _n.keywords)
                and _f.name not in _EXEMPT):
            _unguarded.append(_f.name + ":" + str(_n.lineno))
check("every published file holds the bytes it was handed: " + ", ".join(_unguarded),
      not _unguarded)
check("and the two exempt sites still exist to be exempt",
      all((Path(m.__file__).resolve().parent / _e).is_file() for _e in _EXEMPT))

# ... and the rule bites, on a source written to be caught. A rule that has never refused
# anything is indistinguishable from one that cannot.
def _audit(src: str) -> list:
    return [str(n.lineno) for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "write_text"
            and not any(k.arg == "newline" for k in n.keywords)]


check("the rule catches an unguarded write_text",
      _audit('p.write_text(t, encoding="utf-8")') == ["1"])
check("and passes the guarded one",
      _audit('p.write_text(t, encoding="utf-8", newline="")') == [])

# The property itself, end to end, through the facade every caller uses.
_eol = make_sandbox(m) / "eol.md"
m.write_atomic(_eol, "one\ntwo\n")
check("LF is published as LF", _eol.read_bytes() == b"one\ntwo\n", repr(_eol.read_bytes()))
m.write_atomic(_eol, "one\r\ntwo\r\n")
check("and CRLF is published once, not doubled",
      _eol.read_bytes() == b"one\r\ntwo\r\n", repr(_eol.read_bytes()))

print()
print(f"writes that lose data: {P} passed, {F} failed")
sys.exit(1 if F else 0)
