#!/usr/bin/env python3
"""PREREG-V3 A5 (T34) M35: research/v3/arms/arm_mem0.py's BM25 counter - spawns NO child and imports no product: the
adapter module is loaded in this process (it imports mem0 only when a unit starts) and is handed fake stores.

A mem0 unit counts its BM25 (A5, the amendment's paragraph after the arm table): the encoder loaded, the collection has
its `bm25` sparse slot, keyword_search returned a result (not None) at least once, and none of the lines "fastembed not
installed", "Failed to load BM25 encoder" or "predates v3 hybrid search" in its log - a unit without all four is a
failed unit, never a scored one; its results with a BM25 score above zero are counted and published, never a reason to
fail. mem0 2.2.0 (read as data): Qdrant._get_bm25_encoder (qdrant.py:93-109) loads lazily - None not tried, False failed
(sticky), else the encoder; create_col sets _has_bm25_slot (qdrant.py:137-163); keyword_search (qdrant.py:454-484)
answers None without the slot, without the encoder or on an exception; the three lines are logger.warning of the
logger mem0.vector_stores.qdrant.

* bm25_state reads the store passively by exactly BM25_NAMES - an attribute the store does not have is unknown, never a
  default;
* BM25Watch counts keyword_search's answers (calls, not None, hits, hits with a score above zero) around the store's own
  method - the same answer object back, the arguments as given, an exception re-raised - and the three lines by a filter
  on the store's logger that never drops a record (the output is unchanged); the log is watched only while that logger
  is enabled for WARNING, not disabled and carries the filter;
* the verdict is not the adapter's: artifact.p0k judges each unit's block (the auditor's Q-M35-FAIL = (d); its rows are
  _test_v3_artifact's P0K);
* the adapter wires it: the log watch before Memory.from_config (create_col logs at init), keyword_search wrapped after,
  each read's results noted, counters() answering bm25_state under "bm25".

    python tests/research/_test_v3_arm_mem0_bm25.py
"""
from __future__ import annotations

import ast
import importlib.util
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


A = _load("v3_arm_mem0_bm25_t", ROOT / "research" / "v3" / "arms" / "arm_mem0.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def holds(fn):
    """(value, None) or (None, the exception named) - a call that raises FAILs its own row, never the suite."""
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


LINES = ("fastembed not installed", "Failed to load BM25 encoder", "predates v3 hybrid search")
#: the amendments' text with its wrapped lines joined (A5 wraps a quoted line across two)
AMD = " ".join((ROOT / "research" / "v3" / "PREREG-V3-AMENDMENTS.md").read_text(encoding="utf-8").split())
print("- the names and the lines are A5's -")
check("BM-1: BM25_LINES are exactly A5's three lines, each quoted in the amendment's paragraph; BM25_NAMES read the "
      "store as mem0 2.2.0 names it (vector_store, _bm25_encoder, _has_bm25_slot, keyword_search, the logger "
      "mem0.vector_stores.qdrant)",
      tuple(getattr(A, "BM25_LINES", ())) == LINES and all(f'"{x}"' in AMD for x in LINES)
      and getattr(A, "BM25_NAMES", None) == {"store": "vector_store", "encoder": "_bm25_encoder", "slot": "_has_bm25_slot",
                                             "search": "keyword_search", "logger": "mem0.vector_stores.qdrant"},
      str((getattr(A, "BM25_LINES", None), getattr(A, "BM25_NAMES", None))))


class Hit:
    def __init__(self, id_, score):
        self.id, self.score = id_, score


class Store:
    """A fake Qdrant store: keyword_search answers what it is told, recording its arguments."""

    def __init__(self, answers, *, encoder=object(), slot=True):
        self._bm25_encoder, self._has_bm25_slot = encoder, slot
        self.answers, self.seen = list(answers), []

    def keyword_search(self, query, top_k=5, filters=None):
        self.seen.append((query, top_k, filters))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def fresh_logger(tag: str) -> logging.Logger:
    lg = logging.getLogger(f"nvt3.test.bm25.{tag}")
    lg.setLevel(logging.NOTSET)
    lg.propagate = False
    return lg


def watch(tag: str = "w"):
    """(a BM25Watch attached to a fresh logger, the logger) - the watch None when the adapter has none (its rows FAIL by
    name through holds(), never the suite)."""
    lg = fresh_logger(tag)
    w, _e = holds(lambda: A.BM25Watch())
    if w is not None:
        holds(lambda: w.attach_log(lg))
    return w, lg


print("\n- BM25Watch: keyword_search counted around the store's own method -")
r1, r2 = [Hit("a", 0.8), Hit("b", 0.0), Hit("c", 1.5)], []
boom = RuntimeError("query_points broke")
st = Store([r1, None, r2, boom])
w, _lg = watch("ks")
_x, werr = holds(lambda: w.wrap(st))
got1, e1 = holds(lambda: st.keyword_search(query="q one", top_k=60, filters={"user_id": "u1"}))
got2, e2 = holds(lambda: st.keyword_search(query="q two", top_k=60, filters={"user_id": "u1"}))
got3, e3 = holds(lambda: st.keyword_search(query="q three", top_k=60, filters={"user_id": "u1"}))
got4, e4 = holds(lambda: st.keyword_search(query="q four", top_k=60, filters={"user_id": "u1"}))
ks = (getattr(w, "snapshot", lambda: {})() or {}).get("keyword_search") if w is not None else None
check("BM-2: the wrapped keyword_search hands back the store's own answer object, the arguments as given, and counts "
      "calls, answers that are not None, hits and hits with a score above zero - None counted as a call only, an "
      "exception counted and re-raised as it was",
      werr is None and got1 is r1 and got2 is None and got3 is r2 and e4 == "RuntimeError: query_points broke"
      and st.seen == [(f"q {n}", 60, {"user_id": "u1"}) for n in ("one", "two", "three", "four")]
      and ks == {"calls": 4, "not_none": 2, "hits": 3, "positive_hits": 2, "raised": 1},
      str(werr or (ks, st.seen))[:400])
st5 = Store([[Hit("a", 0.9), Hit("b", 0.0)], [Hit("z", 0.0)]])
w5, _ = watch("res")
holds(lambda: w5.wrap(st5))
holds(lambda: st5.keyword_search(query="x", top_k=60, filters=None))
holds(lambda: w5.note_results(["a", "b", "q"]))
holds(lambda: st5.keyword_search(query="y", top_k=60, filters=None))
holds(lambda: w5.note_results(["a", "z"]))
snap5 = (holds(lambda: w5.snapshot())[0] or {})
check("BM-3: the results a read returned are counted as BM25-positive only when the read's own keyword_search gave "
      "their id a score above zero (a published count, A5)", snap5.get("results_bm25_positive") == 1
      and snap5.get("reads_bm25_positive") == 1, str(snap5))

print("\n- BM25Watch: the three lines, counted by a filter that drops nothing -")
w6, lg6 = watch("log")
seen6: list = []


class Keep(logging.Handler):
    def emit(self, record):
        seen6.append(record.getMessage())


lg6.addHandler(Keep())
lg6.warning("fastembed not installed - BM25 keyword search disabled. Install it with: pip install \"mem0ai[extras]\"")
lg6.warning("Failed to load BM25 encoder: %s", "no files")
lg6.warning("Collection 'nvt3' predates v3 hybrid search (no 'bm25' sparse slot). BM25 keyword scoring will be disabled")
lg6.warning("Failed to load BM25 encoder: %s", "again")
lg6.warning("an unrelated warning")
lines6 = (holds(lambda: w6.snapshot())[0] or {}).get("lines")
check("BM-4: each of the three lines is counted in the store's log (a formatted message too), an unrelated line is "
      "not, and every record still reaches the logger's handlers - the output is unchanged",
      lines6 == {"fastembed not installed": 1, "Failed to load BM25 encoder": 2, "predates v3 hybrid search": 1}
      and len(seen6) == 5, str((lines6, seen6)))
w7, lg7 = watch("level")
ok7 = (holds(lambda: w7.snapshot())[0] or {}).get("log_watched")
lg7.setLevel(logging.ERROR)
lv7 = (holds(lambda: w7.snapshot())[0] or {}).get("log_watched")
lg7.setLevel(logging.NOTSET)
lg7.disabled = True
ds7 = (holds(lambda: w7.snapshot())[0] or {}).get("log_watched")
lg7.disabled = False
for f_ in list(lg7.filters):
    lg7.removeFilter(f_)
rm7 = (holds(lambda: w7.snapshot())[0] or {}).get("log_watched")
check("BM-5: the log is watched only while the store's logger is enabled for WARNING, not disabled and carries the "
      "filter - a raised level, a disabled logger or a removed filter reads False",
      ok7 is True and lv7 is False and ds7 is False and rm7 is False, str((ok7, lv7, ds7, rm7)))

print("\n- bm25_state: the store read passively by BM25_NAMES -")
wz, _ = watch("state")


def state(store):
    return holds(lambda: A.bm25_state(SimpleNamespace(vector_store=store) if store is not None else SimpleNamespace(), wz))


s_ok, e_ok = state(SimpleNamespace(_bm25_encoder=object(), _has_bm25_slot=True))
s_nt, _ = state(SimpleNamespace(_bm25_encoder=None, _has_bm25_slot=True))
s_f, _ = state(SimpleNamespace(_bm25_encoder=False, _has_bm25_slot=False))
s_un, _ = state(SimpleNamespace(_has_bm25_slot="yes"))
s_ns, _ = state(None)
check("BM-6: the encoder reads loaded / not-tried (None) / failed (False) / unknown (no such attribute, or no store); "
      "the slot is the store's bool, None when missing or not a bool; the watch's counts and the names ride along",
      e_ok is None and (s_ok["encoder"], s_ok["slot"]) == ("loaded", True) and s_nt["encoder"] == "not-tried"
      and (s_f["encoder"], s_f["slot"]) == ("failed", False) and (s_un["encoder"], s_un["slot"]) == ("unknown", None)
      and (s_ns["encoder"], s_ns["slot"]) == ("unknown", None) and s_ok["names"] == A.BM25_NAMES
      and set(s_ok) >= {"keyword_search", "lines", "log_watched", "results_bm25_positive"},
      str(e_ok or (s_ok, s_nt, s_f, s_un, s_ns))[:500])

print("\n- the adapter wires it -")
src = (ROOT / "research" / "v3" / "arms" / "arm_mem0.py").read_text(encoding="utf-8")
tree = ast.parse(src)
fn = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def calls_in(node) -> list[tuple[int, str]]:
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            out.append((n.lineno, name))
    return sorted(out)


bind_calls = calls_in(fn["bind"]) if "bind" in fn else []
at = {name: ln for ln, name in reversed(bind_calls)}
check("BM-7: bind attaches the log watch BEFORE Memory.from_config (create_col warns at init) and wraps keyword_search "
      "after it", all(k in at for k in ("attach_log", "from_config", "wrap"))
      and at["attach_log"] < at["from_config"] < at["wrap"], str({k: at.get(k) for k in ("attach_log", "from_config", "wrap")}))
cls = fn.get("Handler")
meth = {n.name: n for n in (cls.body if cls else []) if isinstance(n, ast.FunctionDef)}
rets = [n.value for n in ast.walk(meth["counters"]) if isinstance(n, ast.Return)] if "counters" in meth else []
keys = [(k.value if isinstance(k, ast.Constant) else None, v) for r_ in rets if isinstance(r_, ast.Dict)
        for k, v in zip(r_.keys, r_.values)]
check("BM-8: counters() answers bm25_state under \"bm25\", and read() notes each read's results for the positive count",
      any(k == "bm25" and isinstance(v, ast.Call) and getattr(v.func, "id", None) == "bm25_state" for k, v in keys)
      and "read" in meth and any(name == "note_results" for _ln, name in calls_in(meth["read"])),
      str([k for k, _ in keys]))

print(f"\nv3 arm_mem0 bm25: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
