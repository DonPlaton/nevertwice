"""`research/head_to_head.py`: the two guards that decide whether a run may become a row.

Neither needs a model. `accept()` is the rule that a run which retrieved nothing is a harness
failure rather than a product's score - the A-MEM pipeline arm published a row of zeros on
2026-09-06 because nothing checked. `_OllamaChromaEF` is the shim that makes every arm embed
with the same endpoint; chroma asks it for `embed_query` at search time, and the missing method
was what made that arm retrieve nothing in the first place.
"""
import ast
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import head_to_head as h2h  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def row(**over):
    base = {"recall@1": 0.4, "recall@3": 0.6, "recall@5": 0.7, "recall@10": 0.8, "mrr@10": 0.5,
            "n": 500, "version": "1.2.3", "label": "Some product", "_wall_s": 12.0,
            "measured_at": {"commit": "abc", "utc": "2026-09-06T00:00:00Z"}}
    base.update(over)
    return base


print("\n- a scored run is passed through untouched -")
good = row()
check("nothing is added or removed", h2h.accept("mem0", good) == good)

print("\n- a run that retrieved nothing is refused -")
empty = row(**{f"recall@{k}": 0.0 for k in h2h.KS}, mrr=0.0)
out = h2h.accept("amem_full", empty)
check("the row becomes a blocker", "blocked" in out and "recall@10" not in out, str(sorted(out)))
check("the blocker names the arm and the size of the run",
      "amem_full" in out["blocked"] and "500" in out["blocked"], out.get("blocked", ""))
check("the numbers are kept under `refused`, not thrown away",
      out["refused"]["recall@10"] == 0.0 and out["refused"]["n"] == 500)
check("the provenance of the run survives",
      out["version"] == "1.2.3" and out["measured_at"]["commit"] == "abc" and out["label"] == "Some product")

# R-v2-ports: a refused row still carries what the pacer/coverage checks recorded - a run
# that ALSO bypassed the pacer, or under-observed a competitor's own traffic, is worth
# knowing about even (especially) on a row this stand is about to refuse to score.
print("\n- a refused row still carries the pacer's own findings about that run -")
flagged = row(**{f"recall@{k}": 0.0 for k in h2h.KS}, mrr=0.0,
             ollama_transport={"calls": 3, "bypass_calls": {"requests": 1, "aiohttp": 0}},
             valid=False, invalid_reason="bypassed the pacer via requests: 1 request(s)",
             coverage="unobserved")
out2 = h2h.accept("mem0", flagged)
check("ollama_transport survives onto the refused row",
      out2.get("ollama_transport") == flagged["ollama_transport"], str(out2))
check("valid/invalid_reason survive onto the refused row",
      out2.get("valid") is False and out2.get("invalid_reason") == flagged["invalid_reason"],
      str(out2))
check("coverage survives onto the refused row", out2.get("coverage") == "unobserved", str(out2))
check("the raw recall numbers still moved under `refused`, not kept at the top level",
      "recall@10" not in out2 and out2["refused"]["recall@10"] == 0.0, str(out2))

print("\n- coverage_verdict(): a competitor arm under-observed by the pacer says so -")
under = {"ingested_items": 10, "ollama_transport": {"calls": 4}}
h2h.coverage_verdict("mem0", under)
check("fewer paced calls than ingested items -> coverage='unobserved'",
      under.get("coverage") == "unobserved", str(under))
full = {"ingested_items": 10, "ollama_transport": {"calls": 10}}
h2h.coverage_verdict("mem0", full)
check("calls == ingested items -> no coverage key at all",
      "coverage" not in full, str(full))
over = {"ingested_items": 10, "ollama_transport": {"calls": 12}}
h2h.coverage_verdict("mem0", over)
check("MORE calls than ingested items (batching, retries) -> no coverage key",
      "coverage" not in over, str(over))
check("our OWN arm is never flagged, even with the same shortfall",
      "coverage" not in h2h.coverage_verdict(
          "nevertwice", {"ingested_items": 10, "ollama_transport": {"calls": 4}}), "")
check("a blocked arm is never flagged",
      "coverage" not in h2h.coverage_verdict(
          "mem0", {"blocked": "x", "ingested_items": 10,
                  "ollama_transport": {"calls": 4}}), "")
check("no ingested_items recorded at all -> nothing to report, no coverage key",
      "coverage" not in h2h.coverage_verdict("mem0", {"ollama_transport": {"calls": 0}}), "")

print("\n- _pace_excluded(): a phase's own elapsed time, minus the pacer's sleep in it -")
before_snap = {"pace_sleep_s": 1.0, "retry_sleep_s": 0.0}
after_snap = {"pace_sleep_s": 3.5, "retry_sleep_s": 15.0}
check("elapsed minus (pace_sleep delta + retry_sleep delta)",
      h2h._pace_excluded(20.0, before_snap, after_snap) == 20.0 - 2.5 - 15.0,
      str(h2h._pace_excluded(20.0, before_snap, after_snap)))
check("zero pacing in the window -> elapsed is untouched",
      h2h._pace_excluded(5.0, before_snap, before_snap) == 5.0)

print("\n- the rule is about retrieving nothing at all, not about scoring badly -")
weak = row(**{"recall@1": 0.0, "recall@3": 0.0, "recall@5": 0.0, "recall@10": 0.002}, mrr=0.0004)
check("a run that found one answer in five hundred is still a row", h2h.accept("langmem", weak) == weak)
check("an arm that blocked earlier is left as it is",
      h2h.accept("zep", {"blocked": "needs Neo4j"}) == {"blocked": "needs Neo4j"})
empty_pool = row(n=0, **{f"recall@{k}": 0.0 for k in h2h.KS})
check("a run over no questions is left to the caller (n=0 has no meaning here)",
      h2h.accept("mem0", empty_pool) == empty_pool)

print("\n- the embedding shim answers chroma's query-side method -")
seen = []


class _Recording(h2h._OllamaChromaEF):
    """The shim with the network call replaced - `embed_query` must reach `__call__`."""

    def __call__(self, input):                                   # noqa: A002 - chroma's name
        seen.append(list(input))
        return [[0.1, 0.2]] * len(input)


ef = _Recording("bge-m3")
check("embed_query exists", hasattr(ef, "embed_query"))
check("it delegates to __call__ with the same input",
      ef.embed_query(["a question"]) == [[0.1, 0.2]] and seen == [["a question"]], str(seen))
check("chroma's protocol pieces are all present",
      all(hasattr(ef, a) for a in ("name", "get_config", "build_from_config", "default_space",
                                   "supported_spaces", "validate_config", "validate_config_update")))

# the metric is named for the depth it is measured at
#: `score()` truncated recall at k and walked the WHOLE list for MRR, so the number depended on
#: how many candidates an arm happened to return - and they differed: mem0 2.0.19's `search` has
#: no `limit` (it is `top_k`, default 20), so the stand's `limit=max(KS)` fell into `**kwargs`
#: and Mem0 answered with twenty where our arm and LangMem answered with ten. A hit at rank
#: 11-20 earned Mem0 up to 1/11 that no other arm could earn. Truncating fixes the comparison
#: and changes what the number IS, so the field is `mrr@10` and not `mrr`.
print()
print("- MRR is taken at one depth for every arm, and says so in its name -")

_DATA = [{"question_id": "q1", "answer_session_ids": ["s15"]}]
_POOL = [f"s{i}" for i in range(1, 21)]
_KEY = f"mrr@{max(h2h.KS)}"
_deep = h2h.score({"q1": [f"s{i}" for i in range(1, 21)]}, _DATA, _POOL)
_shallow = h2h.score({"q1": [f"s{i}" for i in range(1, 11)]}, _DATA, _POOL)

check("the key carries the depth, not the bare name 'mrr'",
      _KEY in _deep and "mrr" not in _deep, str(sorted(_deep)))
check("a hit past the depth earns nothing", _deep[_KEY] == 0.0, str(_deep[_KEY]))
check("so an arm that returned twenty scores what an arm that returned ten scores",
      _deep[_KEY] == _shallow[_KEY], f"{_deep[_KEY]} vs {_shallow[_KEY]}")
_inside = h2h.score({"q1": ["sX", "s15"]}, _DATA, _POOL)
check("and a hit INSIDE the depth still scores", _inside[_KEY] == 0.5, str(_inside[_KEY]))


# two source rules, because both defects were visible without running anything
_STANDS = ("head_to_head.py", "frontier_eval.py", "code_sessions_eval.py")


def _tree(name):
    return ast.parse((ROOT / "research" / name).read_text(encoding="utf-8")), name


print()
print("- what the source can be asked before anything runs -")

#: `limit=10` was passed to mem0 for as long as this stand existed and was swallowed by
#: `**kwargs` in silence. `named_or_raise` catches it at the call sites that remember to ask;
#: this catches the call site written next year that does not.
_bad = []
for _t, _name in (_tree(n) for n in _STANDS):
    for _n in ast.walk(_t):
        if not (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
                and _n.func.attr == "search"):
            continue
        kw = {k.arg for k in _n.keywords if k.arg}
        if "filters" not in kw:          # mem0's signature is the one carrying `filters`
            continue
        if "limit" in kw or "top_k" not in kw:
            _bad.append(f"{_name}:{_n.lineno} {sorted(kw)}")
check("no mem0 search asks for `limit`, and every one asks for `top_k`", not _bad, "; ".join(_bad))

#: `accept` and `_OllamaChromaEF.embed_query` each stood twice in head_to_head.py, byte for
#: byte. The second silently replaced the first - harmless while they are identical, a live
#: defect the moment one is edited. Ruff's F811 had been reporting both for as long as they
#: existed; nothing ever ran ruff against research/, so the rule existed and the answer did not.
_dupes = []
for _t, _name in (_tree(n) for n in _STANDS):
    _scopes = [("<module>", _t.body)] + [(c.name, c.body) for c in ast.walk(_t)
                                         if isinstance(c, ast.ClassDef)]
    for _scope, _body in _scopes:
        _seen = collections.Counter(
            d.name for d in _body
            if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        _dupes += [f"{_name} {_scope}.{k} x{v}" for k, v in _seen.items() if v > 1]
check("no name in a stand is defined twice", not _dupes, "; ".join(_dupes))


print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
