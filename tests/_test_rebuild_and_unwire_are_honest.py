#!/usr/bin/env python3
"""Two operations that reported success while destroying what they were meant to preserve.

* `store_version.rebuild(--include-embeddings)` deletes `.embeddings_cache.json` and
  `.embeddings_meta.json` and then rebuilds `Index.md` and `.index.sqlite` - neither of which
  contains a vector. Nothing re-embeds. The caller is handed `ok: True` and a list of rebuilt
  artifacts while every vector in the store is gone, on a machine that may have no embedder to
  make them again. The module's own comment says deleting them without one "would destroy work
  that cannot be recreated"; the flag did exactly that and called it a rebuild.
* `hosts.py`'s unwire parses the settings file, then takes its backup from a SECOND read. Between
  the two, another agent writing that file makes the backup a copy of the version that is about to
  be overwritten by a `data` computed from the version before it - so the write clobbers a change
  and the backup does not contain it. One read is both correct and simpler.

    python tests/_test_rebuild_and_unwire_are_honest.py
"""
from __future__ import annotations

import json
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


make_sandbox(m, offline=True)
import store_version as sv  # noqa: E402

print("# --include-embeddings does not delete vectors it cannot put back")
for name in sv.EXPENSIVE:
    (m.VAULT / name).write_text('{"a": [0.1, 0.2]}', encoding="utf-8")
real_avail = getattr(m, "embedder_available", None)
m.embedder_available = lambda: False               # the machine this would ruin
out = sv.rebuild(m.VAULT, include_embeddings=True, dry_run=False)
survived = [n for n in sv.EXPENSIVE if (m.VAULT / n).exists()]
check("the caches are still there when nothing could rebuild them",
      sorted(survived) == sorted(sv.EXPENSIVE), f"lost {set(sv.EXPENSIVE) - set(survived)}")
check("and the call says so instead of reporting a successful rebuild",
      out.get("ok") is False, f"reported {out.get('ok')!r}: {out.get('detail', '')[:120]}")

print("# ... and with an embedder up, the vectors come back")
calls = []
m.embedder_available = lambda: True
import embed_index as _emb  # noqa: E402

real_run = _emb._run_embed
_emb._run_embed = lambda rebuild: calls.append(rebuild)
try:
    out = sv.rebuild(m.VAULT, include_embeddings=True, dry_run=False)
finally:
    _emb._run_embed = real_run
    if real_avail is not None:
        m.embedder_available = real_avail
check("the embeddings are actually rebuilt, not just deleted", calls == [True],
      f"_run_embed calls: {calls}")
check("and the result names them among the rebuilt artifacts",
      any("embed" in str(r).lower() for r in out.get("rebuilt", [])), repr(out.get("rebuilt")))

print("# unwire backs up exactly the bytes it is about to replace")
import hosts  # noqa: E402

src = Path(hosts.__file__).read_text(encoding="utf-8")
seg = src[src.index("        if removed and not dry_run:"):]
seg = seg[:seg.index("return", seg.index("write_atomic"))]
check("the backup is not taken from a second read of the file",
      seg.count("read_text") == 0,
      "the backup re-reads settings.json, so a concurrent write lands in the backup while the "
      "value it was supposed to preserve is overwritten")

print()
print(f"rebuild and unwire are honest: {P} passed, {F} failed")
sys.exit(1 if F else 0)
