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

print()
print(f"writes that lose data: {P} passed, {F} failed")
sys.exit(1 if F else 0)
