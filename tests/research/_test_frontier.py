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
          "recorded) - the requested arm VANISHES instead of appearing blocked -")
    fe._save(fe._cache_path("answers"), {})    # the OLD bug: nothing records the block at all
    fe._save(fe._cache_path("verdicts"), {})
    res_mut = fe.summarise(["mem0"], MINI_DATA, fe.READER, fe.JUDGE, fe.JUDGE2)
    check("mutation 'skip restored': the requested arm is silently ABSENT from the artifact "
          "(would FAIL the two checks above)", "mem0" not in res_mut["arms"], str(res_mut["arms"]))
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


print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
