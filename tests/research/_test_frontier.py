"""`research/frontier_eval.py`: the accuracy-per-token stand's arithmetic, without a model.

The reader and the judge are local models and are not run here; what is pinned is everything
around them - the stratified sample, the context assembly under a character budget, the
per-arm summary over the caches (accuracy, Wilson interval, tokens from the reader's own
count), the brackets, and the judge-agreement figure.
"""
import contextlib
import io
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

import json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import frontier_eval as fe  # noqa: E402
import _ollama_pacer as pacer  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
import remeasure as rm  # noqa: E402 - K16(2)/K25/K30: row_refusal on the RESULT artifact

#: m2_check addition: `summarise()` now calls `corpus_pin.record("longmemeval_oracle")`, which
#: hashes the real (third-party, uncommitted, ~15MB) corpus file - absent in a hermetic test
#: environment. Faked here to return exactly what the real function would on a MATCHING file
#: (the pinned sha256 straight from `corpus_pin.CORPORA`), so every `fe.summarise()` call in
#: this suite stays hermetic while still exercising the real wiring: does `summarise()` copy
#: `corpus_pin.record()`'s own result into `out["provenance"]` faithfully.
def _fake_corpus_record(name):
    spec = fe.corpus_pin.CORPORA[name]
    return {"corpus": name, "sha256": spec["sha256"], "bytes": spec["bytes"],
            "questions": spec["questions"], "pool_sessions": spec["pool_sessions"],
            "url": spec["url"], "licence": spec["licence"], "citation": spec["citation"]}


fe.corpus_pin.record = _fake_corpus_record

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


print("\n- the sample -")
data = [{"question_id": f"q{i}", "question_type": t, "question": "?", "answer": "a", "answer_session_ids": ["s"]}
        for i, t in enumerate(["multi-session", "temporal-reasoning", "multi-session", "knowledge-update",
                               "multi-session", "temporal-reasoning"])]
s = fe.stratified(data, 2)
check("per_type caps each type in corpus order", [e["question_id"] for e in s] == ["q0", "q1", "q2", "q3", "q5"])
check("zero means everything", fe.stratified(data, 0) == data)

print("\n- context assembly under a budget -")
items = [{"id": "a", "text": "x" * 50}, {"id": "b", "text": "y" * 50}, {"id": "c", "text": ""}, {"id": "d", "text": "z" * 50}]
ctx = fe._context_text(items, 2, 1000)
check("top-k items joined, empty ones skipped", ctx == "x" * 50 + "\n\n---\n\n" + "y" * 50)
ctx = fe._context_text(items, 4, 120)
check("the budget cuts the tail, counting separators: the last item is trimmed to the room left",
      len(ctx) <= 132 and ctx.startswith("x" * 50) and ctx.count("z") == 8, str(len(ctx)))
check("k=1 is one item", fe._context_text(items, 1, 1000) == "x" * 50)

print("\n- the summary over the caches -")
tmp = Path(fe.DATA)
saved = (fe._cache_path("answers"), fe._cache_path("verdicts"))
backup = {p: (p.read_text(encoding="utf-8") if p.exists() else None) for p in saved}
try:
    qids = [e["question_id"] for e in data]
    answers, verdicts = {}, {}

    def put(arm, k, qid, tokens, verdict, ctx="some context", judge="j"):
        akey = fe._akey("r", arm, k, qid, ctx)
        answers[akey] = {"answer": "a", "prompt_tokens": tokens, "context_chars": len(ctx)}
        verdicts[f"{judge}|{akey}"] = verdict
        return akey

    for arm in ("nevertwice_whole", "mem0"):
        for k in fe.KS:
            for i, qid in enumerate(qids):
                put(arm, k, qid, 100 * k + i, (i % 2 == 0) if arm == "mem0" else (i != 5))
    for qid in qids:
        put("none", 0, qid, 20, False)
        put("oracle", 99, qid, 900, True)
    for i, qid in enumerate(qids[:4]):
        verdicts[f"j2|{fe._akey('r', 'nevertwice_whole', 5, qid, 'some context')}"] = (i != 0)  # one disagreement
    fe._save(fe._cache_path("answers"), answers)
    fe._save(fe._cache_path("verdicts"), verdicts)
    res = fe.summarise(["nevertwice_whole", "mem0"], data, "r", "j", "j2")
    nt5 = res["arms"]["nevertwice_whole"]["5"]
    check("accuracy per arm and k from the verdicts", nt5["accuracy"] == round(5 / 6, 4) and nt5["n"] == 6)
    check("a Wilson interval brackets it", nt5["ci"][0] < nt5["accuracy"] < nt5["ci"][1])
    check("tokens come from the reader's own count, averaged", nt5["mean_prompt_tokens"] == round((500 * 6 + 15) / 6, 1))
    check("the other arm at another k", res["arms"]["mem0"]["1"]["accuracy"] == 0.5)
    check("brackets: none and oracle", res["brackets"]["none"]["accuracy"] == 0.0 and res["brackets"]["oracle"]["accuracy"] == 1.0)
    check("m2_check: the artifact carries provenance.sha256 equal to corpus_pin's pinned hash",
          res.get("provenance", {}).get("sha256")
          == fe.corpus_pin.CORPORA["longmemeval_oracle"]["sha256"], str(res.get("provenance")))
    ja = res["judge_agreement"]
    check("judge agreement over the doubly judged answers", ja["n"] == 4 and ja["rate"] == 0.75, str(ja))
    check("the artifact names reader, judge and second judge",
          res["judge"] == "j" and res["second_judge"] == "j2" and "reader" in res)

    print("\n- an answer belongs to the context it was read from -")
    # The ranker moving is exactly what must invalidate an answer, and the only thing the cache
    # can see of the ranker is the bytes it produced. Before the digest was part of the key, a
    # re-run after a weight change served every answer from this file.
    k1 = fe._akey("r", "nevertwice_whole", 5, qids[0], "context A")
    k2 = fe._akey("r", "nevertwice_whole", 5, qids[0], "context B")
    check("a different context is a different key", k1 != k2)
    check("...and the same context is the same key",
          k1 == fe._akey("r", "nevertwice_whole", 5, qids[0], "context A"))
    check("the key still names reader, arm, k and question",
          k1.split("|")[:4] == ["r", "nevertwice_whole", "5", qids[0]])
    found = fe._find_akey({k1: {}}, "r", "nevertwice_whole", 5, qids[0])
    check("the summary finds an answer whatever context produced it", found == k1)
    check("a key from before the digest is not found (it is re-read, not reused)",
          fe._find_akey({f"r|nevertwice_whole|5|{qids[0]}": {}}, "r", "nevertwice_whole", 5, qids[0]) is None)
    check("another question is not found", fe._find_akey({k1: {}}, "r", "nevertwice_whole", 5, "other") is None)
finally:
    for p, text in backup.items():
        if text is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(text, encoding="utf-8")

print("\n- wilson -")
lo, hi = fe.wilson(5, 6)
check("wilson interval for 5 of 6", 0.4 < lo < 0.84 < hi <= 1.0)
check("wilson of nothing is empty", fe.wilson(0, 0) == (0.0, 0.0))


# ── item 9B: P0(a) transport, P0(f) named refusals, K30 answer/verdict freshness ────────
#
# The reader/judge are faked at `urllib.request.urlopen` (gotcha 1: never at `ollama_chat`
# itself), so `answer_stage`/`judge_stage`/`summarise` run for REAL over a two-question mini
# corpus - the same "run the real pipeline small" pattern `_test_asof_bench.py` uses for its
# own item 9A pacer wiring.

class _ChatResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_chat_factory(fail_marker=None):
    """The embed endpoint always succeeds (the reader/judge stand never embeds); the chat
    endpoint answers as the reader (`{"answer": ...}`) or the judge (`{"correct": true}`, told
    apart by the JUDGE_PROMPT's own "REFERENCE ANSWER" line) - UNLESS the prompt carries
    `fail_marker`, in which case it raises a genuine (non-port-exhaustion) 500, exactly what a
    real Ollama outage on ONE question looks like."""
    def _fake(*a, **kw):
        req = a[0] if a else kw.get("url")
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if pacer._is_embed_path(url):
            return _ChatResp({"embeddings": [[0.1, 0.2, 0.3]]})
        body = json.loads((req.data or b"{}").decode("utf-8"))
        prompt = (body.get("messages") or [{}])[0].get("content", "")
        if fail_marker and fail_marker in prompt:
            raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, io.BytesIO(b"busy"))
        if "REFERENCE ANSWER" in prompt:
            return _ChatResp({"message": {"content": json.dumps({"correct": True})},
                              "prompt_eval_count": 12, "eval_count": 3})
        return _ChatResp({"message": {"content": json.dumps({"answer": "8080"})},
                          "prompt_eval_count": 12, "eval_count": 3})
    return _fake


@contextlib.contextmanager
def _isolated_pacer():
    check("pacer starts uninstalled entering this block", not pacer.installed())
    if pacer.installed():
        pacer.uninstall()
    saved_urlopen = urllib.request.urlopen
    pacer._reset_for_tests()
    try:
        yield
    finally:
        if pacer.installed():
            pacer.uninstall()
        urllib.request.urlopen = saved_urlopen
        pacer._reset_for_tests()


MINI_DATA = [
    {"question_id": "mq0", "question": "what port does the api use", "answer": "8080",
     "question_type": "single-session-user", "answer_session_ids": ["s0"]},
    {"question_id": "mq1", "question": "what is the request timeout", "answer": "30 seconds",
     "question_type": "single-session-user", "answer_session_ids": ["s1"]},
]
MINI_POOL = {"s0": "the api uses port 8080", "s1": "the request timeout is 30 seconds"}
MINI_ARM = "nevertwice_whole"
saved_DATA = fe.DATA


def _seed_ctx(arm=MINI_ARM):
    fe._save(fe._ctx_path(arm), {q["question_id"]: [{"id": "s0", "text": "some context text"}]
                                 for q in MINI_DATA})


def _one_context(k):
    return fe._context_text([{"id": "s0", "text": "some context text"}], k, fe.CHAR_BUDGET)


print("\n- item 9B: a reader failure on ONE question makes the point (and the root) invalid, "
      "and row_refusal refuses a REAL claim pointer -")
fe.DATA = Path(tempfile.mkdtemp(prefix="frontier_test_9b_"))
try:
    with _isolated_pacer():
        _seed_ctx()
        urllib.request.urlopen = _fake_chat_factory(fail_marker="request timeout")
        fe.answer_stage([MINI_ARM], MINI_DATA, MINI_POOL, fe.READER, fe.CHAR_BUDGET)
        fe.judge_stage([MINI_ARM], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2, 100)
        res = fe.summarise([MINI_ARM], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    pt1 = res["arms"][MINI_ARM]["1"]
    check("mq1's reader call failed everywhere - n is 1, not 2", pt1["n"] == 1, str(pt1))
    check("P0(f)/K23: the point is invalid, naming the missing question",
          pt1.get("valid") is False and "mq1" in (pt1.get("missing_question_ids") or []), str(pt1))
    check("K25: the root is invalid too", res.get("valid") is False, str(res.get("valid")))
    reason = rm.row_refusal(res, "arms.nevertwice_whole.1.accuracy", 0)
    check("tools/remeasure.row_refusal refuses the REAL claim pointer "
          "(frontier.nevertwice_whole.k1.accuracy) on this invalid result",
          reason is not None and "invalid" in reason, str(reason))

    print("\n- item 9B mutation: the n-vs-asked gate removed - the old shape silently "
          "under-counts instead of flagging it (would FAIL the two checks above) -")

    def _legacy_point(answers, verdicts, arm, k):
        """The PRE-FIX shape of the closure inside `summarise()`: no missing-question
        tracking, no `n_expected` comparison - written out, not monkeypatched, since the real
        `point` is a closure over `summarise()`'s own locals and cannot be swapped from here."""
        vs = []
        for q in MINI_DATA:
            akey = fe._find_akey(answers, fe.READER, arm, k, q["question_id"])
            vkey = f"{fe.JUDGE}|{akey}" if akey is not None else None
            if akey is not None and vkey in verdicts:
                vs.append(1 if verdicts[vkey] else 0)
        if not vs:
            return None
        return {"n": len(vs), "accuracy": round(sum(vs) / len(vs), 4)}

    legacy_answers = fe._load(fe._cache_path("answers"))
    legacy_verdicts = fe._load(fe._cache_path("verdicts"))
    legacy_pt = _legacy_point(legacy_answers, legacy_verdicts, MINI_ARM, 1)
    check("mutation 'n-vs-asked gate removed': the legacy shape reports n=1 with NO 'valid' "
          "flag at all - the exact silent undercount K23 exists to catch",
          legacy_pt is not None and legacy_pt["n"] == 1 and "valid" not in legacy_pt,
          str(legacy_pt))
finally:
    fe.DATA = saved_DATA


print("\n- item 9B/K30: an unstamped (legacy) cached answer or verdict is not current - the "
      "point is invalid even though every question WAS answered -")
fe.DATA = Path(tempfile.mkdtemp(prefix="frontier_test_9b_k30a_"))
try:
    _seed_ctx()
    answers, verdicts = {}, {}
    for q in MINI_DATA:
        for k in fe.KS:
            ctx_text = _one_context(k)
            akey = fe._akey(fe.READER, MINI_ARM, k, q["question_id"], ctx_text)
            # a legacy entry: complete, but carrying NO commit/utc at all (exactly how a cache
            # from before K30, or a stale 09-09 competitor cache, would look)
            answers[akey] = {"answer": q["answer"], "prompt_tokens": 10, "context_chars": len(ctx_text)}
            verdicts[f"{fe.JUDGE}|{akey}"] = True
    fe._save(fe._cache_path("answers"), answers)
    fe._save(fe._cache_path("verdicts"), verdicts)
    res = fe.summarise([MINI_ARM], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    pt1 = res["arms"][MINI_ARM]["1"]
    check("n is complete - both questions have an answer and a verdict", pt1["n"] == 2, str(pt1))
    check("K30: an unstamped cache is NOT current - the point is invalid anyway",
          pt1.get("valid") is False and pt1.get("stale_answers_count") == 2
          and pt1.get("stale_verdicts_count") == 2, str(pt1))
    reason = rm.row_refusal(res, "arms.nevertwice_whole.1.accuracy", 0)
    check("row_refusal refuses the real pointer on an unstamped-but-complete cache",
          reason is not None and "invalid" in reason, str(reason))

    print("\n- item 9B/K30 mutation: the verdict-stamp check removed (answers only) -")

    def _stale_answers_only(a):
        head, closure = fe.git_head(), fe.engine_closure("python research/frontier_eval.py")
        stale = 0
        for q in MINI_DATA:
            akey = fe._find_akey(a, fe.READER, MINI_ARM, 1, q["question_id"])
            if akey is not None and not fe._stamp_current(a[akey], head, closure):
                stale += 1
        return stale

    check("mutation 'verdict-stamp check removed': answers-only staleness (2) is a real "
          "signal by itself, but a mutation that drops the verdict check would still miss a "
          "stale-verdict-only case (proven by the next block)", _stale_answers_only(answers) == 2)
finally:
    fe.DATA = saved_DATA


print("\n- item 9B/K30: fresh stamps at HEAD are current, and a fresh answer with a STALE "
      "verdict under the SAME key is still invalid (both are checked independently) -")
fe.DATA = Path(tempfile.mkdtemp(prefix="frontier_test_9b_k30b_"))
try:
    _seed_ctx()
    head = fe.git_head()
    answers, verdicts = {}, {}
    for q in MINI_DATA:
        for k in fe.KS:
            ctx_text = _one_context(k)
            akey = fe._akey(fe.READER, MINI_ARM, k, q["question_id"], ctx_text)
            answers[akey] = {"answer": q["answer"], "prompt_tokens": 10, "context_chars": len(ctx_text),
                             "commit": head, "utc": fe._utc_now_iso()}
            vkey = f"{fe.JUDGE}|{akey}"
            verdicts[vkey] = True
            verdicts.setdefault("_meta", {})[vkey] = {"commit": head, "utc": fe._utc_now_iso()}
    # K34: point() no longer returns None for an unscored point - the "none"/"oracle" brackets
    # are ALWAYS scored too (summarise()'s own separate bracket loop), so isolating "is a fresh
    # nevertwice_whole point clean" from "are the brackets clean" needs both seeded here.
    for arm, k in (("none", 0), ("oracle", 99)):
        for q in MINI_DATA:
            ctx_text = "(no memory available)" if arm == "none" else "oracle ctx"
            akey = fe._akey(fe.READER, arm, k, q["question_id"], ctx_text)
            answers[akey] = {"answer": q["answer"], "prompt_tokens": 10, "context_chars": len(ctx_text),
                             "commit": head, "utc": fe._utc_now_iso()}
            vkey = f"{fe.JUDGE}|{akey}"
            verdicts[vkey] = True
            verdicts.setdefault("_meta", {})[vkey] = {"commit": head, "utc": fe._utc_now_iso()}
    fe._save(fe._cache_path("answers"), answers)
    fe._save(fe._cache_path("verdicts"), verdicts)
    res = fe.summarise([MINI_ARM], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    pt1 = res["arms"][MINI_ARM]["1"]
    check("fresh stamps at HEAD: the point carries no invalidity at all",
          "valid" not in pt1, str(pt1))
    check("the root stays clean too", res.get("valid") is not False, str(res.get("valid")))

    # now regenerate the answers (fresh stamp, same akeys) but strip the verdicts' _meta -
    # an answer current with HEAD, judged by a verdict that was never (re)stamped
    verdicts.pop("_meta", None)
    fe._save(fe._cache_path("verdicts"), verdicts)
    res2 = fe.summarise([MINI_ARM], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    pt1b = res2["arms"][MINI_ARM]["1"]
    check("a fresh answer with an unstamped verdict under the SAME key is still invalid "
          "(K30 checks the verdict independently of the answer)",
          pt1b.get("valid") is False and pt1b.get("stale_answers_count", 0) == 0
          and pt1b.get("stale_verdicts_count") == 2, str(pt1b))
finally:
    fe.DATA = saved_DATA


print("\n- item 9B/P0(f): a requested arm with no cached contexts is a NAMED refusal, never a "
      "vanished arm -")
fe.DATA = Path(tempfile.mkdtemp(prefix="frontier_test_9b_noctx_"))
try:
    with _isolated_pacer():
        urllib.request.urlopen = _fake_chat_factory()
        fe.answer_stage(["mem0"], MINI_DATA, MINI_POOL, fe.READER, fe.CHAR_BUDGET)  # no ctx cache for "mem0"
        res = fe.summarise(["mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    check("the arm appears, naming itself as blocked",
          "mem0" in (res["arms"].get("mem0", {}).get("blocked") or ""), str(res["arms"].get("mem0")))
    check("the blocked arm is marked invalid", res["arms"]["mem0"].get("valid") is False)
    check("K25: the root is invalid too", res.get("valid") is False, str(res.get("valid")))

    print("\n- item 9B mutation: the 'no contexts - skipped' bug restored (no _blocked_arms "
          "recorded) - K34 still keeps the arm present and invalid (defense in depth), but "
          "loses the SPECIFIC 'no contexts cached' diagnosis -")
    fe._save(fe._cache_path("answers"), {})    # the OLD bug: nothing records the block at all
    fe._save(fe._cache_path("verdicts"), {})
    res_mut = fe.summarise(["mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    check("mutation 'skip restored': the arm no longer names itself 'blocked' (the specific "
          "diagnosis is lost - only K34's generic n-vs-asked gate still catches it)",
          "mem0" in res_mut["arms"] and "blocked" not in res_mut["arms"]["mem0"]
          and res_mut["arms"]["mem0"].get("valid") is False, str(res_mut["arms"].get("mem0")))
finally:
    fe.DATA = saved_DATA


print("\n- item 9B/(в): an arm blocked on one answer_stage run is UNBLOCKED once a later run "
      "finds its contexts - _blocked_arms does not only grow -")
fe.DATA = Path(tempfile.mkdtemp(prefix="frontier_test_9b_unblock_"))
try:
    with _isolated_pacer():
        urllib.request.urlopen = _fake_chat_factory()
        fe.answer_stage(["mem0"], MINI_DATA, MINI_POOL, fe.READER, fe.CHAR_BUDGET)  # no ctx yet
        cache1 = fe._load(fe._cache_path("answers"))
        check("run 1: mem0 is recorded as blocked", "mem0" in (cache1.get("_blocked_arms") or []),
              str(cache1.get("_blocked_arms")))
        _seed_ctx("mem0")                                         # contexts now exist for mem0
        fe.answer_stage(["mem0"], MINI_DATA, MINI_POOL, fe.READER, fe.CHAR_BUDGET)
        cache2 = fe._load(fe._cache_path("answers"))
    check("(в): run 2 found contexts - mem0 is REMOVED from _blocked_arms, not left there forever",
          "mem0" not in (cache2.get("_blocked_arms") or []), str(cache2.get("_blocked_arms")))
    res2 = fe.summarise(["mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    check("mem0 is no longer reported blocked in the artifact",
          "blocked" not in res2["arms"].get("mem0", {}), str(res2["arms"].get("mem0")))
finally:
    fe.DATA = saved_DATA


print("\n- item 9B/P0(a): a 500 on /api/embed marks a contexts-stage cache's `_transport` "
      "invalid (never the stand's own ollama_chat wrapper - the fake sits at urlopen) -")
with _isolated_pacer():
    def _fake_embed_500(*a, **kw):
        req = a[0] if a else kw.get("url")
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if pacer._is_embed_path(url):
            raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, io.BytesIO(b"busy"))
        return _ChatResp({"models": []})
    urllib.request.urlopen = _fake_embed_500
    pacer.install()
    snap = pacer.snapshot()
    try:
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/embed"))
    except urllib.error.HTTPError:
        pass
    ctx_embed = {"q0": [{"id": "s0", "text": "x"}]}
    fe._attach_prefixed_transport(ctx_embed, "_transport", snap)
check("a 500 on /api/embed marks the ctx's `_transport` invalid",
      ctx_embed.get("_transport", {}).get("valid") is False, str(ctx_embed.get("_transport")))
check("the reason names the embed failure",
      "embed" in (ctx_embed["_transport"].get("invalid_reason") or ""))


print("\n- item 9B/K35: a later, CLEAN stage run must not launder an earlier invalid transport - "
      "_attach_prefixed_transport MERGES (sticky valid:false, counters summed, every run's own "
      "reason survives) -")
with _isolated_pacer():
    # run 1: a bypass (simulated the way mut_bfd.py's own auditor probe does - a pre-seeded
    # invalid entry, exactly what a first stage run would have written).
    v = {"_transport": {"valid": False, "invalid_reason": "bypass_calls.aiohttp=1 (run 1)",
                        "ollama_transport": {"calls": 5}}}
    # run 2: a genuinely clean window - one real, unbypassed call.
    urllib.request.urlopen = _fake_chat_factory()
    pacer.install()
    snap2 = pacer.snapshot()
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/chat", data=b"{}")).read()
    fe._attach_prefixed_transport(v, "_transport", snap2)
check("K35: valid:false is STICKY across a later clean run",
      v["_transport"].get("valid") is False, str(v["_transport"]))
check("K35: run 1's own reason text survives the merge",
      "run 1" in json.dumps(v), str(v["_transport"].get("invalid_reason")))
check("K35: the counters are SUMMED, not overwritten (5 + the new run's own calls)",
      v["_transport"]["ollama_transport"]["calls"] > 5, str(v["_transport"]["ollama_transport"]))

print("\n- item 9B/K35 mutation: the merge dropped (overwrite restored) - a clean second run "
      "LAUNDERS the first run's own bypass -")
def _attach_overwrite(cache, key, since):
    scratch = {}
    pacer.attach(scratch, since=since)
    if scratch:
        cache[key] = scratch          # the pre-K35 shape: overwrite, not merge


with _isolated_pacer():
    v_mut = {"_transport": {"valid": False, "invalid_reason": "bypass_calls.aiohttp=1 (run 1)",
                            "ollama_transport": {"calls": 5}}}
    urllib.request.urlopen = _fake_chat_factory()
    pacer.install()
    snap3 = pacer.snapshot()
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/chat", data=b"{}")).read()
    _attach_overwrite(v_mut, "_transport", snap3)
check("mutation 'merge dropped': the clean run WRONGLY erases run 1's own bypass "
      "(would FAIL the two checks above)",
      v_mut["_transport"].get("valid") is not False and "run 1" not in json.dumps(v_mut),
      str(v_mut["_transport"]))


print("\n- item 9B mutation: install() removed - no `_transport` is ever written (blind to "
      "P0(a) entirely) -")
saved_install = pacer.install
pacer.install = lambda mode="pace": None
with _isolated_pacer():
    urllib.request.urlopen = _fake_embed_500
    snap = pacer.snapshot()
    try:
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/embed"))
    except urllib.error.HTTPError:
        pass
    ctx_mut = {"q0": [{"id": "s0", "text": "x"}]}
    fe._attach_prefixed_transport(ctx_mut, "_transport", snap)
check("mutation 'install() removed': no `_transport` key at all (nothing was ever paced or "
      "counted) - would FAIL the checks above", "_transport" not in ctx_mut, str(ctx_mut))
pacer.install = saved_install

print("\n- item 9B mutation: ctx transport not copied into the result - an invalid ctx never "
      "reaches the arm's own node -")
mut_out = {"arms": {"nevertwice_whole": {"1": {"n": 2, "accuracy": 1.0}}}}
fe._fold_arm_context_provenance("nevertwice_whole", mut_out["arms"]["nevertwice_whole"],
                                ctx_embed, mut_out)
check("the real fold DOES propagate a `_transport`-flagged ctx into the arm",
      mut_out["arms"]["nevertwice_whole"].get("valid") is False, str(mut_out))
saved_fold = fe._fold_arm_context_provenance
fe._fold_arm_context_provenance = lambda *a, **k: None
mut_out2 = {"arms": {"nevertwice_whole": {"1": {"n": 2, "accuracy": 1.0}}}}
fe._fold_arm_context_provenance("nevertwice_whole", mut_out2["arms"]["nevertwice_whole"],
                                ctx_embed, mut_out2)
check("mutation 'ctx transport not copied': the arm stays WRONGLY valid despite an invalid "
      "ctx (would FAIL the check above)",
      "valid" not in mut_out2["arms"]["nevertwice_whole"], str(mut_out2))
fe._fold_arm_context_provenance = saved_fold


print("\n- item 9B/K25: a blocked `mem0` arm invalidates the WHOLE artifact at the root, so "
      "row_refusal refuses a claim pointer on a DIFFERENT, otherwise-clean arm too -")
fe.DATA = Path(tempfile.mkdtemp(prefix="frontier_test_9b_rootprop_"))
saved_propagate = fe._propagate_root_invalidity
try:
    with _isolated_pacer():
        _seed_ctx()                          # only nevertwice_whole gets contexts - mem0 does not
        urllib.request.urlopen = _fake_chat_factory()     # every question answered cleanly
        fe.answer_stage([MINI_ARM, "mem0"], MINI_DATA, MINI_POOL, fe.READER, fe.CHAR_BUDGET)
        fe.judge_stage([MINI_ARM, "mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2, 100)
        res = fe.summarise([MINI_ARM, "mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    check("the nevertwice_whole arm itself is clean", "valid" not in res["arms"][MINI_ARM]["1"],
          str(res["arms"][MINI_ARM]["1"]))
    check("the mem0 arm carries its own invalidity (blocked)",
          res["arms"]["mem0"].get("valid") is False, str(res["arms"]["mem0"]))
    check("K25: the ROOT is invalid too, naming mem0",
          res.get("valid") is False and "mem0" in (res.get("invalid_reason") or ""),
          str(res.get("invalid_reason")))
    reason = rm.row_refusal(res, "arms.nevertwice_whole.1.accuracy", 0)
    check("K25: row_refusal refuses the REAL claim pointer on the CLEAN nevertwice_whole arm "
          "too, because the root is invalid", reason is not None and "invalid" in reason,
          str(reason))

    print("\n- item 9B/K25 mutation: root propagation removed - row_refusal stops refusing "
          "the clean-looking nevertwice_whole arm (would FAIL the check above) -")
    fe._propagate_root_invalidity = lambda out: None
    res_mut = fe.summarise([MINI_ARM, "mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    check("mutation 'root propagation removed': the root stays WRONGLY valid despite the "
          "blocked mem0 arm", "valid" not in res_mut, str(res_mut.get("valid")))
    reason_mut = rm.row_refusal(res_mut, "arms.nevertwice_whole.1.accuracy", 0)
    check("...and row_refusal no longer refuses the clean-looking arm either",
          reason_mut is None, str(reason_mut))
finally:
    fe._propagate_root_invalidity = saved_propagate
    fe.DATA = saved_DATA
check("fe._propagate_root_invalidity is restored to the real function",
      fe._propagate_root_invalidity is saved_propagate)


# ── item 9B/K36 (the auditor's mut_bfd.py, 2026-09-24): source mutations that survived the
# first pass - X4 already covered incidentally above; X5-X8 get their own direct test here ──

print("\n- item 9B/K36 X5: nevertwice_whole/_snippet's contexts stage copies longmem's vector-"
      "cache flags into ctx['_cache_provenance'] -")
import head_to_head as hh  # noqa: E402
saved_run_nevertwice = hh.run_nevertwice
saved_emb_path = fe.le._emb_path


def _fake_run_nevertwice(data, pool):
    ranked = {q["question_id"]: list(pool)[:5] for q in data}
    return hh.score(ranked, data, list(pool))


hh.run_nevertwice = _fake_run_nevertwice
fake_emb = Path(tempfile.mkdtemp(prefix="frontier_test_x5_")) / "emb.json"
fake_emb.write_text(json.dumps({
    "sessions": {}, "questions": {}, "valid": False,
    "invalid_reason": "3 embed call(s) failed", "ollama_transport": {"calls": 9},
    "dropped_sessions": ["s9"], "dropped_questions": ["q9"],
}), encoding="utf-8")
fe.le._emb_path = lambda *a, **k: fake_emb
try:
    ctx_x5 = fe.contexts_nevertwice(MINI_DATA, MINI_POOL, snippet=False)
finally:
    hh.run_nevertwice = saved_run_nevertwice
    fe.le._emb_path = saved_emb_path
check("X5: ctx['_cache_provenance'] carries the vector cache's own valid/invalid_reason/"
      "ollama_transport/dropped_*", ctx_x5.get("_cache_provenance", {}).get("valid") is False
      and ctx_x5["_cache_provenance"].get("dropped_sessions") == ["s9"], str(ctx_x5.get("_cache_provenance")))

print("\n- item 9B/K36 X6: an invalid cache_provenance folds into the arm (the P0(a)/(b) path "
      "for the MAIN arms nevertwice_whole/_snippet) -")
node_x6 = {"1": {"n": 2, "accuracy": 1.0}}
ctx_x6 = {"_cache_provenance": {"valid": False, "invalid_reason": "3 embed call(s) failed"}}
fe._fold_arm_context_provenance("nevertwice_whole", node_x6, ctx_x6, {})
check("X6: the arm's own node is marked invalid, naming the vector-cache reason",
      node_x6.get("valid") is False and "embed" in (node_x6.get("invalid_reason") or ""),
      str(node_x6))

print("\n- item 9B/K36 X7: the answer/judge stage-level transport (answer_transport/"
      "judge_transport valid:false) marks the ROOT, not just itself -")
fe.DATA = Path(tempfile.mkdtemp(prefix="frontier_test_x7_"))
try:
    with _isolated_pacer():
        _seed_ctx()
        urllib.request.urlopen = _fake_chat_factory()
        fe.answer_stage([MINI_ARM], MINI_DATA, MINI_POOL, fe.READER, fe.CHAR_BUDGET)
        fe.judge_stage([MINI_ARM], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2, 100)
        cache_x7 = fe._load(fe._cache_path("answers"))
        cache_x7["_transport"] = {"valid": False, "invalid_reason": "bypass_calls.requests=1"}
        fe._save(fe._cache_path("answers"), cache_x7)
        res_x7 = fe.summarise([MINI_ARM], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    check("X7: the root is invalid, naming the answer-stage transport",
          res_x7.get("valid") is False and "answer stage" in (res_x7.get("invalid_reason") or ""),
          str(res_x7.get("invalid_reason")))
finally:
    fe.DATA = saved_DATA

print("\n- item 9B/K36 X8: the RN5 competitor_cache record is written for a non-engine arm -")
fe.DATA = Path(tempfile.mkdtemp(prefix="frontier_test_x8_"))
try:
    _seed_ctx("mem0")
    with _isolated_pacer():
        urllib.request.urlopen = _fake_chat_factory()
        fe.answer_stage(["mem0"], MINI_DATA, MINI_POOL, fe.READER, fe.CHAR_BUDGET)
        fe.judge_stage(["mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2, 100)
        res_x8 = fe.summarise(["mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    check("X8: a non-engine (competitor) arm's contexts cache file provenance is recorded",
          "mem0" in (res_x8.get("competitor_cache") or {})
          and "sha256" in res_x8["competitor_cache"]["mem0"], str(res_x8.get("competitor_cache")))
finally:
    fe.DATA = saved_DATA

print("\n- item 9B/K36 X4 (direct, the auditor's request): a PARTIAL competitor cache - contexts for only "
      "some of the asked questions - is a mismatch, not 'present' (PREREG P0(f)), and row_refusal refuses "
      "the real pointer arms.mem0.1.accuracy -")
fe.DATA = Path(tempfile.mkdtemp(prefix="frontier_test_x4_"))
try:
    fe._save(fe._ctx_path("mem0"), {MINI_DATA[0]["question_id"]: [{"id": "s0", "text": "some context text"}]})
    with _isolated_pacer():
        urllib.request.urlopen = _fake_chat_factory()
        fe.answer_stage(["mem0"], MINI_DATA, MINI_POOL, fe.READER, fe.CHAR_BUDGET)
        fe.judge_stage(["mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2, 100)
        res_x4 = fe.summarise(["mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    node_x4 = res_x4.get("arms", {}).get("mem0") or {}
    check("X4: the arm names the questions its cache lacks",
          node_x4.get("missing_contexts_count") == len(MINI_DATA) - 1
          and node_x4.get("missing_contexts_ids") == [q["question_id"] for q in MINI_DATA[1:]], str(node_x4)[:300])
    check("X4: the arm and the root are invalid",
          node_x4.get("valid") is False and res_x4.get("valid") is False, str(res_x4.get("invalid_reason")))
    why_x4 = rm.row_refusal(res_x4, "arms.mem0.1.accuracy", 0)
    check("X4: row_refusal refuses the real pointer arms.mem0.1.accuracy", why_x4 is not None, str(why_x4))
    saved_gap = fe.ctx_coverage_gap
    fe.ctx_coverage_gap = lambda ctx, ids: []                    # mutation X4: the gap ignored
    try:
        with _isolated_pacer():
            urllib.request.urlopen = _fake_chat_factory()
            res_x4m = fe.summarise(["mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    finally:
        fe.ctx_coverage_gap = saved_gap
    check("mutation X4 'gap ignored' is caught: the arm then carries no missing_contexts record",
          "missing_contexts_count" not in ((res_x4m.get("arms") or {}).get("mem0") or {}),
          str((res_x4m.get("arms") or {}).get("mem0"))[:200])
    check("fe.ctx_coverage_gap is restored", fe.ctx_coverage_gap is saved_gap)
finally:
    fe.DATA = saved_DATA

print("\nK42: a competitor store that returns NOTHING for every question refuses (the smoke at 64011bb "
      "read an empty, freshly CREATED store and exited 0), and a populated one records its provenance -")
import types as _types  # noqa: E402
import os as _os  # noqa: E402


class _FakeMem:
    def __init__(self, results):
        self._results = results

    def search(self, query, filters=None, top_k=10):
        return {"results": list(self._results)}


class _FakeMemory:
    results: list = []

    @classmethod
    def from_config(cls, cfg):
        return _FakeMem(cls.results)


class _FakeAMem:
    hits: list = []

    def __init__(self, **kw):
        pass

    def search_agentic(self, q, k=10):
        return list(self.hits)


_saved_mods = {k: sys.modules.get(k) for k in ("mem0", "agentic_memory", "agentic_memory.retrievers",
                                              "agentic_memory.memory_system")}
_saved_h2h = _os.environ.get("H2H_DATA")
sys.modules["mem0"] = _types.SimpleNamespace(Memory=_FakeMemory)
_am = _types.ModuleType("agentic_memory")
_am_r = _types.ModuleType("agentic_memory.retrievers")
_am_r.SentenceTransformerEmbeddingFunction = None
_am_ms = _types.ModuleType("agentic_memory.memory_system")
_am_ms.AgenticMemorySystem = _FakeAMem
_am.retrievers, _am.memory_system = _am_r, _am_ms
sys.modules["agentic_memory"] = _am
sys.modules["agentic_memory.retrievers"] = _am_r
sys.modules["agentic_memory.memory_system"] = _am_ms
try:
    with tempfile.TemporaryDirectory() as td_k42:
        _os.environ["H2H_DATA"] = td_k42
        def _mark(sub, **over):
            marker = {"argv": ["research/head_to_head.py", "--only=" + sub.split("_", 1)[1]],
                      "commit": fe.git_head(), "utc": "2026-09-24T20:00:00Z", "limit": None,
                      "sessions": None, "n_items": 40}
            marker.update(over)
            (Path(td_k42) / sub / ".populated_by.json").write_text(json.dumps(marker), encoding="utf-8")

        for sub in ("qdrant_mem0_infer", "chroma_amem_full"):
            (Path(td_k42) / sub).mkdir()
            (Path(td_k42) / sub / "data.bin").write_bytes(b"x")
            _mark(sub)
        _FakeMemory.results = []
        try:
            fe.contexts_mem0_infer(MINI_DATA, MINI_POOL)
            check("K42: an EMPTY mem0_infer store refuses (RuntimeError naming the store)", False)
        except RuntimeError as e:
            check("K42: an EMPTY mem0_infer store refuses (RuntimeError naming the store)",
                  "never populated" in str(e) and "qdrant_mem0_infer" in str(e), str(e))
        _FakeAMem.hits = []
        try:
            fe.contexts_amem_full(MINI_DATA, MINI_POOL)
            check("K42: an EMPTY amem_full store refuses", False)
        except RuntimeError as e:
            check("K42: an EMPTY amem_full store refuses", "never populated" in str(e), str(e))
        _FakeMemory.results = [{"memory": "the api uses port 8080", "metadata": {"session_id": "s0"}}]
        ctx_ok = fe.contexts_mem0_infer(MINI_DATA, MINI_POOL)
        check("K42: a populated store reads normally and records its provenance",
              all(len(ctx_ok[q["question_id"]]) == 1 for q in MINI_DATA)
              and ctx_ok.get("_store_provenance", {}).get("n_items") == len(MINI_DATA)
              and "qdrant_mem0_infer" in ctx_ok["_store_provenance"]["path"], str(ctx_ok.get("_store_provenance")))
        _FakeAMem.hits = [{"id": "n1", "content": "note"}]
        ctx_am = fe.contexts_amem_full(MINI_DATA, MINI_POOL)
        check("K42: a populated A-MEM store reads normally and records its provenance",
              ctx_am.get("_store_provenance", {}).get("n_items") == len(MINI_DATA))
        check("K42: the provenance names the full run that built the store, at this commit",
              "--limit" not in ctx_ok["_store_provenance"]["populated_by"]
              and ctx_ok["_store_provenance"]["built_at_commit"] == fe.git_head(), str(ctx_ok["_store_provenance"]))
        _FakeMemory.results = [{"memory": "x", "metadata": {"session_id": "s0"}}]
        for label, over, needle in (("a PARTIAL (--limit) store refuses", {"limit": 2,
                                     "argv": ["research/head_to_head.py", "--limit", "2"]}, "PARTIAL"),
                                    ("a store built at another commit refuses", {"commit": "0" * 40}, "rebuild it"),):
            _mark("qdrant_mem0_infer", **over)
            try:
                fe.contexts_mem0_infer(MINI_DATA, MINI_POOL)
                check("K42: " + label, False)
            except RuntimeError as e:
                check("K42: " + label, needle in str(e), str(e))
        (Path(td_k42) / "qdrant_mem0_infer" / ".populated_by.json").unlink()
        try:
            fe.contexts_mem0_infer(MINI_DATA, MINI_POOL)
            check("K42: a store with NO marker refuses", False)
        except RuntimeError as e:
            check("K42: a store with NO marker refuses", ".populated_by.json" in str(e), str(e))
        _mark("qdrant_mem0_infer")
        _saved_refuse = fe._refuse_empty_store
        fe._refuse_empty_store = lambda arm, store, out, populate: out          # mutation: no refusal
        _FakeMemory.results = []
        try:
            ctx_mut = fe.contexts_mem0_infer(MINI_DATA, MINI_POOL)
            mut_ok = sum(len(v) for k, v in ctx_mut.items() if not k.startswith("_")) == 0
        except RuntimeError:
            mut_ok = False
        finally:
            fe._refuse_empty_store = _saved_refuse
        check("mutation K42 'no refusal' is caught: the empty store then reads WRONGLY as 0 items",
              mut_ok)
        check("fe._refuse_empty_store is restored", fe._refuse_empty_store is _saved_refuse)
finally:
    for k, v in _saved_mods.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
    if _saved_h2h is None:
        _os.environ.pop("H2H_DATA", None)
    else:
        _os.environ["H2H_DATA"] = _saved_h2h

print("\nK42: the store provenance reaches the ARTIFACT (competitor_cache[arm].store), where m2_check "
      "reads it -")
fe.DATA = Path(tempfile.mkdtemp(prefix="frontier_test_k42fold_"))
try:
    _ctx_fold = {q["question_id"]: [{"id": "s0", "text": "some context text"}] for q in MINI_DATA}
    _ctx_fold["_store_provenance"] = {"path": "P", "newest_file_mtime": 1.0, "n_items": 2,
                                      "populated_by": "research/head_to_head.py --only=mem0_infer",
                                      "built_at_commit": fe.git_head()}
    fe._save(fe._ctx_path("mem0_infer"), _ctx_fold)
    with _isolated_pacer():
        urllib.request.urlopen = _fake_chat_factory()
        fe.answer_stage(["mem0_infer"], MINI_DATA, MINI_POOL, fe.READER, fe.CHAR_BUDGET)
        fe.judge_stage(["mem0_infer"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2, 100)
        res_fold = fe.summarise(["mem0_infer"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    check("K42: frontier.json carries competitor_cache['mem0_infer'].store with the building command",
          ((res_fold.get("competitor_cache") or {}).get("mem0_infer") or {}).get("store", {}).get("populated_by")
          == "research/head_to_head.py --only=mem0_infer", str(res_fold.get("competitor_cache"))[:300])
finally:
    fe.DATA = saved_DATA

print("\nK43: a competitor arm the runner DECLARES blocked (its own pipeline blocked in b9) is printed "
      "'blocked (reason)' and leaves the run valid (P3); the same arm REQUESTED without contexts "
      "invalidates it (P0(f)) -")
fe.DATA = Path(tempfile.mkdtemp(prefix="frontier_test_k43_"))
try:
    with _isolated_pacer():
        _seed_ctx()
        urllib.request.urlopen = _fake_chat_factory()
        fe.answer_stage([MINI_ARM], MINI_DATA, MINI_POOL, fe.READER, fe.CHAR_BUDGET)
        fe.judge_stage([MINI_ARM], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2, 100)
        _why = "Mem0 pipeline: 120 of 940 sessions yielded no memory - not a measurement of Mem0"
        res_decl = fe.summarise([MINI_ARM], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2,
                                declared_blocked={"mem0_infer": _why})
    check("K43: the declared arm is PRESENT as blocked, naming the reason (P3: never absent)",
          (res_decl.get("arms") or {}).get("mem0_infer", {}).get("blocked") == _why, str(res_decl.get("arms", {}).get("mem0_infer")))
    check("K43: the run stays valid - a declared block is not a requested arm with no contexts",
          res_decl.get("valid") is not False, str(res_decl.get("invalid_reason")))
    check("K43: our own arm's real pointer still restores",
          rm.row_refusal(res_decl, "arms.nevertwice_whole.1.accuracy", 0) is None)
    check("K43: the blocked arm's pointer does not restore (its pair claims stay pending)",
          rm.row_refusal(res_decl, "arms.mem0_infer.1.accuracy", 0) is not None)
    _saved_argv = sys.argv
    for bad, label in ((["--arms", "mem0", "--blocked", "nevertwice_full:x"], "one of our OWN arms (not requested)"),
                       (["--arms", MINI_ARM + ",mem0_infer", "--blocked", "mem0_infer:x"], "an arm that is also requested")):
        try:
            sys.argv = ["frontier_eval.py", "summary", "--stratify", "0"] + bad
            with contextlib.redirect_stdout(io.StringIO()):
                rc_bad = fe.main()
        except SystemExit as e:
            rc_bad = e.code
        except Exception as e:                   # noqa: BLE001 - a named red, not a crash
            rc_bad = f"crash: {type(e).__name__}"
        finally:
            sys.argv = _saved_argv
        check(f"K43: main() refuses --blocked for {label} (exit 2)", rc_bad == 2, str(rc_bad))
finally:
    fe.DATA = saved_DATA

print("\n(б) b-k: the nevertwice_full ingest cache resumes only under the code, model and store that "
      "built it, and anything else is set aside, never overwritten -")
with tempfile.TemporaryDirectory(prefix="nevertwice_ingest_") as _td:
    _cp = Path(_td) / "frontier_full_ingest_cache.json"
    _key = {"_store": "S1", "_engine_commit": "c" * 40, "_extractor": "qwen2.5-7b-64k:latest", "_num_predict": 4096}
    _fresh = fe.resume_ingest_cache(_cp, _key)
    check("no cache: a fresh one carrying the key", _fresh == _key and not list(Path(_td).glob("*.stale-*")))
    fe._save(_cp, {**_key, "sess-1": 2, "sess-2": 0})
    check("the same store, commit, model and cap: resumed with its sessions",
          fe.resume_ingest_cache(_cp, _key).get("sess-1") == 2)
    for field, other in (("_engine_commit", "d" * 40), ("_extractor", "qwen2.5:3b"), ("_num_predict", None),
                         ("_store", "S2")):
        fe._save(_cp, {**_key, "sess-1": 2})
        for p in Path(_td).glob("*.stale-*"):
            p.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            _got = fe.resume_ingest_cache(_cp, {**_key, field: other})
        _aside = list(Path(_td).glob("frontier_full_ingest_cache.stale-*.json"))
        check(f"a cache built under another {field} is not resumed", "sess-1" not in _got, str(_got))
        check(f"... and is set aside, not overwritten ({field})",
              len(_aside) == 1 and json.loads(_aside[0].read_text(encoding="utf-8")).get("sess-1") == 2
              and not _cp.exists(), str(_aside))
    #: The stopped b8 run's leftover is exactly this shape: a store path and session counts, no code.
    fe._save(_cp, {"_store": "S1", "sess-1": 2})
    with contextlib.redirect_stdout(io.StringIO()):
        _got = fe.resume_ingest_cache(_cp, _key)
    check("the campaign-v2 leftover (keyed to its store only) is never resumed", "sess-1" not in _got, str(_got))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
