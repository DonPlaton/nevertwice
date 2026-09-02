#!/usr/bin/env python3
"""A state file keeps the generation BEFORE the current one, so a bad write is undoable.

`.bak` is a crash-safety twin by design: both copies are written from the same known-good
in-memory text so a good generation survives a truncated write (audit D1). That makes it
byte-identical to the primary in the steady state — which the 2026-09 review found while
looking for a way to undo a destructive re-mine, and there was none. CLAUDE.md presents
`.bak` as the safety net; it cannot be one.

`.prev` adds the missing generation without weakening D1.
"""
import _env_guard  # noqa: F401
import sys, tempfile, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import store_state as st  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "state.json"
    prev, bak = f.with_name("state.json.prev"), f.with_name("state.json.bak")

    print("\n- the first write has nothing to roll back to -")
    st._save_json_generations(f, json.dumps({"gen": 1}))
    check("primary written", json.loads(f.read_text(encoding="utf-8")) == {"gen": 1})
    check("bak matches the primary (D1 unchanged)", bak.read_text(encoding="utf-8") == f.read_text(encoding="utf-8"))
    check("no prev yet", not prev.exists())

    print("\n- the second write leaves the FIRST generation recoverable -")
    st._save_json_generations(f, json.dumps({"gen": 2}))
    check("primary advanced", json.loads(f.read_text(encoding="utf-8")) == {"gen": 2})
    check("prev holds the generation before it",
          json.loads(prev.read_text(encoding="utf-8")) == {"gen": 1},
          prev.read_text(encoding="utf-8"))
    check("bak still tracks the primary, not the rollback",
          bak.read_text(encoding="utf-8") == f.read_text(encoding="utf-8"))

    print("\n- a destructive write is undoable, which is the whole point -")
    st._save_json_generations(f, json.dumps({}))          # the 'everything vanished' write
    check("the primary is now empty", json.loads(f.read_text(encoding="utf-8")) == {})
    check("and the content before it survives in prev",
          json.loads(prev.read_text(encoding="utf-8")) == {"gen": 2})

    print("\n- an unchanged write does not consume the rollback -")
    st._save_json_generations(f, json.dumps({}))
    check("prev still holds the last DIFFERENT content",
          json.loads(prev.read_text(encoding="utf-8")) == {"gen": 2})

print(f"\nrollback generation: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
