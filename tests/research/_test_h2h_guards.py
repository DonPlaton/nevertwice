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
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import head_to_head as h2h  # noqa: E402
import requests  # noqa: E402 - declared in the `research` extra alongside the tripwire

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
# K14: exact is driven by max_concurrent_paced (the WHOLE paced operation), never by the
# narrower max_inflight (call-only) - carried here too, to prove the synthetic dicts
# below are not accidentally passing because a STALE key happens to still be read.
before_snap = {"pace_sleep_s": 1.0, "retry_sleep_s": 0.0, "max_inflight": 1,
              "max_concurrent_paced": 1}
after_snap = {"pace_sleep_s": 3.5, "retry_sleep_s": 15.0, "max_inflight": 1,
             "max_concurrent_paced": 1}
val, exact = h2h._pace_excluded(20.0, before_snap, after_snap)
check("elapsed minus (pace_sleep delta + retry_sleep delta)", val == 20.0 - 2.5 - 15.0, str(val))
check("exact is True when max_concurrent_paced never exceeded 1", exact is True, str(exact))
val0, exact0 = h2h._pace_excluded(5.0, before_snap, before_snap)
check("zero pacing in the window -> elapsed is untouched", val0 == 5.0, str(val0))

print("\n- R2/K14: _pace_excluded() is clamped at 0 and reports inexact under concurrency -")
concurrent_after = {"pace_sleep_s": 3.5, "retry_sleep_s": 15.0, "max_inflight": 1,
                    "max_concurrent_paced": 3}
_, exact_c = h2h._pace_excluded(20.0, before_snap, concurrent_after)
check("max_concurrent_paced > 1 anywhere in the span -> exact is False (max_inflight "
      "alone staying at 1 must NOT be enough to read exact - the K14 regression)",
      exact_c is False, str(exact_c))
over_after = {"pace_sleep_s": 3.5, "retry_sleep_s": 100.0, "max_inflight": 1,
             "max_concurrent_paced": 1}
val_neg, _ = h2h._pace_excluded(20.0, before_snap, over_after)
check("pacing sleep exceeding elapsed time is CLAMPED at 0, never negative",
      val_neg == 0.0, str(val_neg))

# mutation: read max_inflight instead of max_concurrent_paced (K14's own regression) -
# the SAME concurrent_after (max_inflight=1, max_concurrent_paced=3) now WRONGLY exact
_saved_pace_excluded = h2h._pace_excluded
def _pace_excluded_old_key(elapsed_s, before, after):
    paced = ((after["pace_sleep_s"] - before["pace_sleep_s"]) +
            (after["retry_sleep_s"] - before["retry_sleep_s"]))
    return max(0.0, round(elapsed_s - paced, 3)), after.get("max_inflight", 0) <= 1
h2h._pace_excluded = _pace_excluded_old_key
try:
    _, exact_mut = h2h._pace_excluded(20.0, before_snap, concurrent_after)
    check("mutation 'read max_inflight instead of max_concurrent_paced': the SAME "
          "concurrent span now WRONGLY reads exact=True (would FAIL the exact-is-False "
          "check above)", exact_mut is True, str(exact_mut))
finally:
    h2h._pace_excluded = _saved_pace_excluded

print("\n- K12(в)/MG2: the coverage boundary - exactly one call short of full is "
     "'unobserved', not just 'far short' -")
boundary = {"ingested_items": 10, "ollama_transport": {"calls": 9}}
h2h.coverage_verdict("mem0", boundary)
check("observed == ingested - 1 (one short) -> coverage='unobserved'",
      boundary.get("coverage") == "unobserved", str(boundary))

print("\n- K12/MG4: run_and_score_arm() attaches EACH arm's OWN pacer delta, never the "
     "process-wide cumulative counters -")


class _FakeUrlResp:
    def read(self):
        return b'{"ok": true}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeReqResp:
    status_code = 200


def _fake_urlopen(url_or_req, *a, **k):
    return _FakeUrlResp()


def _fake_requests_send(self, request, **k):
    return _FakeReqResp()


def _k12_arm1(data, pool):
    for _ in range(3):
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags")
    session = requests.Session()
    req = requests.Request("GET", "http://127.0.0.1:11434/api/tags").prepare()
    session.send(req)                              # a tripwire bypass, deliberately
    return {"recall@1": 0.5, "n": 10, "recall@10": 0.5}


def _k12_arm2(data, pool):
    return {"recall@1": 0.5, "n": 10, "recall@10": 0.5}   # makes NO calls at all


_saved_adapters = dict(h2h.ADAPTERS)
_saved_urlopen = urllib.request.urlopen
_saved_requests_send = requests.Session.send
urllib.request.urlopen = _fake_urlopen
requests.Session.send = _fake_requests_send
h2h.ADAPTERS["k12_arm1"] = _k12_arm1
h2h.ADAPTERS["k12_arm2"] = _k12_arm2
try:
    h2h.pacer.install()
    try:
        r1 = h2h.run_and_score_arm("k12_arm1", [], {})
        r2 = h2h.run_and_score_arm("k12_arm2", [], {})
    finally:
        h2h.pacer.uninstall()
    check("arm 1: calls == 3 (its OWN delta)",
          r1.get("ollama_transport", {}).get("calls") == 3, str(r1.get("ollama_transport")))
    check("arm 1: valid is False (the requests bypass)", r1.get("valid") is False, str(r1))
    check("arm 2: NO ollama_transport at all - it made zero calls of its own",
          "ollama_transport" not in r2, str(r2))
    check("arm 2: NO valid key - arm 1's bypass must not leak onto a later arm",
          "valid" not in r2, str(r2))

    # MG4 (the auditor's own finding): attach() called WITHOUT since= reads the
    # process-wide CUMULATIVE counters, so a later, innocent arm inherits an earlier
    # arm's traffic and its bypass.
    def _broken_run_and_score_arm(name, data, pool):
        fn = h2h.ADAPTERS[name]
        r = fn(data, pool)
        h2h.pacer.attach(r)                         # BUG: no since= - MG4
        h2h.coverage_verdict(name, r)
        return r
    h2h.pacer.install()
    try:
        h2h.pacer._reset_for_tests()
        mr1 = _broken_run_and_score_arm("k12_arm1", [], {})
        mr2 = _broken_run_and_score_arm("k12_arm2", [], {})
    finally:
        h2h.pacer.uninstall()
    check("mutation MG4 (attach() without since=): arm 2 WRONGLY inherits arm 1's "
          "bypass - ollama_transport/valid leak across arms (would FAIL the arm-2 "
          "checks above)",
          "ollama_transport" in mr2 and mr2.get("valid") is False, str(mr2))
finally:
    h2h.ADAPTERS.clear()
    h2h.ADAPTERS.update(_saved_adapters)
    urllib.request.urlopen = _saved_urlopen
    requests.Session.send = _saved_requests_send
    h2h.pacer._reset_for_tests()

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


print("\n- K42: mark_store writes the marker a reader (frontier_eval) checks - argv, commit, --limit/--sessions, item count -")
import json as _json  # noqa: E402
import tempfile as _tempfile  # noqa: E402
_saved_argv, _saved_args = sys.argv, h2h.ARGS
try:
    sys.argv = ["research/head_to_head.py", "--only=mem0_infer", "--limit", "2"]
    h2h.ARGS = type(h2h.ARGS)(**{**vars(h2h.ARGS), "limit": 2, "sessions": None})
    with _tempfile.TemporaryDirectory() as _td:
        _store = Path(_td) / "qdrant_mem0_infer"
        h2h.mark_store(_store, 7)
        _mk = _json.loads((_store / ".populated_by.json").read_text(encoding="utf-8"))
        check("K42: the marker records argv, the commit, the --limit and the item count",
              _mk["argv"] == sys.argv and _mk["commit"] == h2h._git_head() and _mk["limit"] == 2
              and _mk["n_items"] == 7, str(_mk))
finally:
    sys.argv, h2h.ARGS = _saved_argv, _saved_args

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
