#!/usr/bin/env python3
"""The two search surfaces that do not say what they mean (T1, memory_search.py).

* **The DRY comment was false.** `CONFIDENT_SIM` and `_low_confidence` were captured at
  IMPORT from `memory_hook`, under a comment promising that "the CLI and the
  SessionStart/per-prompt hook gate identically (DRY)". A snapshot is the opposite of DRY:
  the moment anything rebinds the engine's gate - an env re-read, a sandbox rebase, a test
  tightening the floor - the CLI keeps gating by the value it happened to import with, and
  nothing says so. Resolved on access now, so the promise is a mechanism.

* **A negative `--k` sliced from the end.** `scored[:k]` with `k=-3` drops the THREE BEST
  matches and returns the rest; `--k=abc` fell back to 10 without a word. `mcp_server.py`
  already clamps at its door (`max(1, min(k, 25))`) - and `search_core` is the funnel every
  surface goes through, so the clamp belongs there, where api.recall and any custom caller
  reach it too.

No vault, no embedder, no network: the embed cache and the scale index are stubbed.

    python tests/_test_search_surfaces.py
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

from _sandbox import make_sandbox  # noqa: E402

make_sandbox(m, offline=True)

import memory_search as ms  # noqa: E402

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    print(("  ok   " if ok else "  FAIL ") + label + (f"  [{detail}]" if detail and not ok else ""))
    P += int(ok)
    F += int(not ok)


# ── the gate the CLI claims to share with the hook ─────────────────────

print("- the engine's gate, not a copy of it as of import time -")

_floor = m.RETRIEVAL_SIM_FLOOR
try:
    m.RETRIEVAL_SIM_FLOOR = 0.99
    check("CONFIDENT_SIM follows the engine's floor",
          ms.CONFIDENT_SIM == 0.99, repr(ms.CONFIDENT_SIM))
finally:
    m.RETRIEVAL_SIM_FLOOR = _floor
check("and comes back with it", ms.CONFIDENT_SIM == _floor, repr(ms.CONFIDENT_SIM))
# `ICON` was the third name captured the same way, one line above the comment.
check("ICON is the engine's icon map, not a copy", ms.ICON is m.TYPE_ICON)
check("and an unknown name is still an AttributeError",
      not hasattr(ms, "no_such_name_here"))

_gate = m._low_confidence
_asked = []
try:
    m._low_confidence = lambda sims: (_asked.append(tuple(sims)), True)[-1]
    check("_low_confidence is the engine's current gate, not the imported one",
          ms._low_confidence is m._low_confidence)
    # and the module BODY asks the same question - an alias nobody calls proves nothing
    check("and search_core asks that gate", ms._low_confidence([0.9, 0.1]) is True)
    check("the gate was really reached", _asked == [(0.9, 0.1)], repr(_asked))
finally:
    m._low_confidence = _gate


# ── k, on the surface every caller funnels through ─────────────────────

print()
print("- a non-positive k asks for nothing, and must not mean 'all but the last n' -")

NOTES = {f"m-note{i}": {"ntype": "mistake", "project": "p", "title": f"cpu device {i}",
                        "desc": "training silently fell back to the cpu device",
                        "prevention": "assert cuda", "recurrence": 1}
         for i in range(5)}

_cache, _index = m.load_embed_cache, m._scale_index
try:
    m.load_embed_cache = lambda *a, **kw: dict(NOTES)
    m._scale_index = lambda *a, **kw: None          # force the token-overlap path
    full, _ = ms.search_core("cpu device", "p", k=5)
    check("the fixture really produces five hits", len(full) == 5, str(len(full)))
    neg, _ = ms.search_core("cpu device", "p", k=-3)
    check("k=-3 is not read as 'every hit but the last three'",
          [r["stem"] for r in neg] != [r["stem"] for r in full[:-3]],
          str([r["stem"] for r in neg]))
    check("it asks for one, the nearest positive request",
          len(neg) == 1, str(len(neg)))
    check("k=0 is the same story", len(ms.search_core("cpu device", "p", k=0)[0]) == 1)
finally:
    m.load_embed_cache, m._scale_index = _cache, _index


# ── and the CLI says what it refused ───────────────────────────────────

print()
print("- --k=abc used to become 10 in silence -")


def _cli(*args) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    argv = sys.argv
    sys.argv = ["nevertwice-search", *args]
    try:
        with redirect_stdout(out), redirect_stderr(err):
            ms.main()
        rc = 0
    except SystemExit as e:
        rc = int(e.code or 0)
    finally:
        sys.argv = argv
    return rc, out.getvalue(), err.getvalue()


for _bad in ("--k=abc", "--k=-3", "--k=0", "--k="):
    _rc, _out, _err = _cli("anything", _bad)
    check(f"{_bad} is refused, not read as 10", _rc == 2, f"rc={_rc} err={_err.strip()!r}")
    check(f"and {_bad} says which flag", "--k" in _err, repr(_err.strip()))

print()
print(f"search surfaces: {P} passed, {F} failed")
sys.exit(1 if F else 0)
