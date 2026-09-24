"""`research/code_sessions_eval.py` (ledger J3): the scoring behind the code-session stand,
without a model. A fact is right when the reader's answer carries a marker; a `current` answer
that carries the old value and not the new one is stale; a situation is caught when a top-three
text carries the prevention sentence; the brackets are 0 and 1 by construction; the corpus gates
read off the summary. Contexts, answers and verdicts are faked into a temporary cache directory.
"""
import contextlib
import io
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import gen_code_sessions as gcs  # noqa: E402
import code_sessions_eval as cse  # noqa: E402
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


def compliant(prompt: str, seed: int) -> dict:
    import re
    must = re.findall(r"^- (?!mistake:)(.+)$", prompt.split("The transcript MUST contain")[1].split("\n\n")[0], re.M)
    prevs = re.findall(r"Prevention \(state this sentence verbatim\): (.+)$", prompt, re.M)
    lines = [f"assistant: Confirmed: {s}." for s in must if s != "(none)"] + [f"assistant: Lesson: {p}" for p in prevs]
    return {"transcript": "\n".join(lines) or "assistant: routine maintenance, nothing to record"}


tmp = Path(tempfile.mkdtemp(prefix="codesess_test_"))
cse.DATA = tmp
cse._ctx_path = lambda arm: tmp / f"ctx_{arm}.json"
cse._cache_path = lambda stage: tmp / f"{stage}.json"

corpus = gcs.build(2, 5, {}, call=compliant, verbose=False)
corpus["sha256"] = "test"
qs = cse.questions(corpus)
pool = cse.pool_of(corpus)

print("\n- markers and hits -")
check("a marker hit folds separators and case", cse.hit(["beta_dashboard"], "the BETA-DASHBOARD flag") and not cse.hit(["8017"], "port 8018"))
check("lesson and situation questions carry the prevention sentence",
      all(q["prevention"] for q in qs if q["type"] in ("lesson", "situation")))
sit = next(q for q in qs if q["type"] == "situation")
check("a situation is caught when a top-three text carries the prevention",
      cse.situation_hit([{"text": "x"}, {"text": "note: " + sit["prevention"]}], sit["prevention"])
      and not cse.situation_hit([{"text": "x"}] * 3 + [{"text": sit["prevention"]}], sit["prevention"]))

print("\n- scoring from faked answers -")
reader, judge, arm = "reader", "judge", "nevertwice_full"
answers, verdicts, ctx = {}, {}, {}
for q in qs:
    if q["type"] == "situation":
        # the right note in the top three for the first project, absent for the second
        ctx[q["id"]] = ([{"text": "note: " + q["prevention"]}] if q["project"] == "cs000" else [{"text": "unrelated"}])
        continue
    context = f"ctx for {q['id']}"
    key = fe._akey(reader, arm, cse.K, q["id"], context)
    if q["type"] == "fact":
        ans = q["answer"] if q["project"] == "cs000" else "I do not know"
    elif q["type"] == "current":
        # first project answers the new value; the second repeats the old one - stale
        ans = q["answer"] if q["project"] == "cs000" else q["stale_markers"][0]
    else:
        ans = "the lesson"
        verdicts[f"{judge}|{key}"] = q["project"] == "cs000"
    answers[key] = {"answer": ans, "prompt_tokens": 100, "context_chars": len(context)}
sc = cse.score_answers(qs, answers, verdicts, ctx, reader, arm, judge)
n_fact = sum(1 for q in qs if q["type"] == "fact")
check("fact accuracy is the marker rate", sc["fact"]["n"] == n_fact and abs(sc["fact"]["accuracy"] - 0.5) < 0.01, str(sc["fact"]))
check("a current answer with the old value and not the new is stale",
      sc["current"]["accuracy"] == 0.5 and sc["current"]["stale_rate"] == 0.5, str(sc["current"]))
check("lesson accuracy comes from the judge", sc["lesson"]["accuracy"] == 0.5, str(sc["lesson"]))
check("situation recall is retrieval only", sc["situation"]["accuracy"] == 0.5 and "mean_prompt_tokens" not in sc["situation"], str(sc["situation"]))
check("core folds fact, current and lesson", sc["core"]["n"] == sc["fact"]["n"] + sc["current"]["n"] + sc["lesson"]["n"])
check("tokens are the reader's count", sc["fact"]["mean_prompt_tokens"] == 100.0)
none_sc = cse.score_answers(qs, {}, {}, {}, reader, "none", judge)
check("the none bracket scores situations at zero by construction", none_sc["situation"]["accuracy"] == 0.0)
orac_sc = cse.score_answers(qs, {}, {}, {}, reader, "oracle", judge)
check("the oracle bracket scores situations at one by construction", orac_sc["situation"]["accuracy"] == 1.0)

print("\n- the summary and the corpus gates -")
fe._save(cse._cache_path("answers"), answers)
fe._save(cse._cache_path("verdicts"), verdicts)
fe._save(cse._ctx_path(arm), {**ctx, "_ingest": {"sessions": 10, "errors": 0}})
res = cse.summarise([arm], qs, corpus, reader, judge)
check("the arm and its ingest are in the summary", arm in res["arms"] and res["ingest"][arm]["sessions"] == 10)
check("gates are None when a bracket is missing", res["corpus_gates"]["none_le_0.10"] is None and res["corpus_gates"]["corpus_separates"] is False)
# brackets answered: none knows nothing, oracle knows everything, naive half
for brk, right in (("none", False), ("oracle", True)):
    for q in qs:
        if q["type"] == "situation":
            continue
        key = fe._akey(reader, brk, cse.K, q["id"], f"{brk} ctx {q['id']}")
        if q["type"] == "lesson":
            verdicts[f"{judge}|{key}"] = right
            answers[key] = {"answer": "x", "prompt_tokens": 50, "context_chars": 1}
        else:
            answers[key] = {"answer": q["answer"] if right else "unknown", "prompt_tokens": 50, "context_chars": 1}
fe._save(cse._cache_path("answers"), answers)
fe._save(cse._cache_path("verdicts"), verdicts)
res = cse.summarise([arm], qs, corpus, reader, judge)
g = res["corpus_gates"]
check("with none at 0 and oracle at 1 the two bracket gates hold", g["none_le_0.10"] is True and g["oracle_ge_0.60"] is True, str(g))
check("the naive gate waits for the naive arm", g["naive_le_oracle_minus_0.20"] is None and g["corpus_separates"] is False)

print("\n- the floor's ranking -")
docs = {"a": "the API server listens on port 8017 and more", "b": "unrelated text about cats"}
check("term overlap ranks the session that shares the question's words first",
      cse._overlap_rank("what port does the API server listen on", docs, 2)[0] == "a")


# ── item 9B: P0(a) transport, P0(f) named refusals, K30 answer/verdict freshness ────────
#
# Mirrors tests/research/_test_frontier.py's own item 9B block - the fold/propagate/stamp
# machinery is SHARED (defined once in frontier_eval.py, reused here via `fe.`), so this file
# exercises code_sessions_eval's OWN call sites (answer_stage/judge_stage/summarise/
# score_answers), not a second copy of the shared mechanism's own mutation tests.

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


MINI_QS = [
    {"id": "cq0", "type": "fact", "project": "p0", "question": "what port does the api use",
     "answer": "8080", "markers": ["8080"], "gold_sessions": ["s0"], "prevention": ""},
    {"id": "cq1", "type": "fact", "project": "p0", "question": "what is the db host",
     "answer": "localhost", "markers": ["localhost"], "gold_sessions": ["s1"], "prevention": ""},
]
MINI_POOL = {"s0": {"id": "s0", "text": "the api uses port 8080"},
            "s1": {"id": "s1", "text": "the db host is localhost"}}
MINI_CORPUS = {"name": "mini_codesess", "sha256": "deadbeef", "generator_model": "test", "counts": {}}
MINI_ARM = "nevertwice_full"
_saved_ctx_path, _saved_cache_path = cse._ctx_path, cse._cache_path


def _use_scratch(tag):
    d = Path(tempfile.mkdtemp(prefix=f"codesess_test_9b_{tag}_"))
    cse._ctx_path = lambda arm: d / f"ctx_{arm}.json"
    cse._cache_path = lambda stage: d / f"{stage}.json"


def _restore_paths():
    cse._ctx_path, cse._cache_path = _saved_ctx_path, _saved_cache_path


def _seed_ctx_cs(arm=MINI_ARM, ids=None):
    ids = ids if ids is not None else [q["id"] for q in MINI_QS]
    fe._save(cse._ctx_path(arm), {i: [{"id": "s0", "text": "some context text"}] for i in ids})


def _one_ctx_text():
    return cse.context_text([{"id": "s0", "text": "some context text"}], cse.K)


print("\n\n- item 9B: a reader failure on ONE question makes the point (and the root) "
      "invalid, and row_refusal refuses a REAL claim pointer -")
_use_scratch("reader")
try:
    with _isolated_pacer():
        _seed_ctx_cs()
        urllib.request.urlopen = _fake_chat_factory(fail_marker="db host")
        cse.answer_stage([MINI_ARM], MINI_QS, MINI_POOL, cse.READER)
        cse.judge_stage([MINI_ARM], MINI_QS, cse.READER, cse.JUDGE)
        res = cse.summarise([MINI_ARM], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    fact_row = res["arms"][MINI_ARM]["fact"]
    check("cq1's reader call failed - n is 1, not 2", fact_row["n"] == 1, str(fact_row))
    check("P0(f)/K23: the fact point is invalid, naming the missing question",
          fact_row.get("valid") is False and "cq1" in (fact_row.get("missing_question_ids") or []),
          str(fact_row))
    check("K25: the root is invalid too", res.get("valid") is False, str(res.get("valid")))
    reason = rm.row_refusal(res, "arms.nevertwice_full.fact.accuracy", 0)
    check("tools/remeasure.row_refusal refuses the REAL claim pointer "
          "(code_sessions.nevertwice_full.fact) on this invalid result",
          reason is not None and "invalid" in reason, str(reason))
finally:
    _restore_paths()


print("\n- item 9B/K30: an unstamped (legacy) cached answer or verdict is not current, and "
      "freshly stamped ones are - checked independently -")
_use_scratch("k30")
try:
    _seed_ctx_cs()
    answers, verdicts = {}, {}
    for q in MINI_QS:
        ctx_text = _one_ctx_text()
        akey = fe._akey(cse.READER, MINI_ARM, cse.K, q["id"], ctx_text)
        answers[akey] = {"answer": q["answer"], "prompt_tokens": 10, "context_chars": len(ctx_text)}
    fe._save(cse._cache_path("answers"), answers)
    fe._save(cse._cache_path("verdicts"), {})
    res = cse.summarise([MINI_ARM], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    fact_row = res["arms"][MINI_ARM]["fact"]
    check("n is complete - both questions have an answer", fact_row["n"] == 2, str(fact_row))
    check("K30: an unstamped cache is NOT current - the point is invalid anyway",
          fact_row.get("valid") is False and fact_row.get("stale_answers_count") == 2,
          str(fact_row))
    reason = rm.row_refusal(res, "arms.nevertwice_full.fact.accuracy", 0)
    check("row_refusal refuses the real pointer on an unstamped-but-complete cache",
          reason is not None and "invalid" in reason, str(reason))

    # now stamp fresh, at HEAD - the point becomes clean
    head = fe.git_head()
    for q in MINI_QS:
        ctx_text = _one_ctx_text()
        akey = fe._akey(cse.READER, MINI_ARM, cse.K, q["id"], ctx_text)
        answers[akey]["commit"] = head
        answers[akey]["utc"] = fe._utc_now_iso()
    fe._save(cse._cache_path("answers"), answers)
    res2 = cse.summarise([MINI_ARM], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    fact_row2 = res2["arms"][MINI_ARM]["fact"]
    check("fresh stamps at HEAD: the fact point carries no invalidity at all",
          "valid" not in fact_row2, str(fact_row2))
finally:
    _restore_paths()


print("\n- item 9B/K37: every lesson verdict missing - the lesson row does not vanish, core is "
      "invalid (not silently redefined over fact+current), and row_refusal refuses the REAL "
      "core pointer -")
_k37_head = fe.git_head()
_k37_closure = fe.engine_closure("python research/code_sessions_eval.py")
_k37_arm = "nevertwice_full"
_k37_qs = ([{"id": f"f{i}", "type": "fact", "markers": ["x"], "stale_markers": []} for i in range(3)]
          + [{"id": f"c{i}", "type": "current", "markers": ["x"], "stale_markers": ["y"]} for i in range(3)]
          + [{"id": f"l{i}", "type": "lesson", "markers": [], "stale_markers": []} for i in range(4)])
_k37_answers, _k37_verdicts = {}, {"_meta": {}}
for q in _k37_qs:
    key = fe._akey("r", _k37_arm, cse.K, q["id"], "ctx")
    _k37_answers[key] = {"answer": "x", "prompt_tokens": 5, "commit": _k37_head, "utc": fe._utc_now_iso()}
    # every lesson verdict is missing entirely - the judge returned None for all of them
out = cse.score_answers(_k37_qs, _k37_answers, _k37_verdicts, {}, "r", _k37_arm, "j",
                        head=_k37_head, closure=_k37_closure)
check("the lesson row does NOT vanish - it is present at n=0, naming every missing question",
      "lesson" in out and out["lesson"]["n"] == 0
      and sorted(out["lesson"].get("missing_question_ids") or []) == ["l0", "l1", "l2", "l3"],
      str(out.get("lesson")))
check("K37: core is invalid - not silently redefined over fact+current alone",
      out["core"].get("valid") is False, str(out.get("core")))
_k37_art = {"arms": {_k37_arm: out}}
fe._propagate_root_invalidity(_k37_art)
check("the root is invalid too", _k37_art.get("valid") is False, str(_k37_art.get("valid")))
reason = rm.row_refusal(_k37_art, "arms.nevertwice_full.core.accuracy", 0)
check("row_refusal refuses the REAL claim pointer (code_sessions.nevertwice_full.core)",
      reason is not None and "invalid" in reason, str(reason))

print("\n- item 9B/K37 mutation: the type-vanishing bug restored (skip when n==0, core "
      "redefined over the survivors) -")


def _legacy_score_by_type(qs, answers, arm):
    """The PRE-FIX shape: `if not d['n']: continue` drops a fully-empty type, and `core` folds
    only whatever survived - written out, not monkeypatched, matching mut_c2a.py's own probe."""
    by = {}
    for q in qs:
        t = q["type"]
        d = by.setdefault(t, {"n": 0, "correct": 0})
        if t == "lesson":
            continue                    # every verdict missing: nothing ever increments n
        akey = fe._find_akey(answers, "r", arm, cse.K, q["id"])
        if akey is None:
            continue
        d["n"] += 1
        d["correct"] += 1               # every fact/current "answer" ("x") hits its marker
    legacy = {}
    for t, d in by.items():
        if not d["n"]:
            continue
        legacy[t] = {"n": d["n"], "accuracy": round(d["correct"] / d["n"], 4)}
    core = [t for t in ("fact", "current", "lesson") if t in legacy]
    if core:
        n = sum(legacy[t]["n"] for t in core)
        c = sum(int(round(legacy[t]["accuracy"] * legacy[t]["n"])) for t in core)
        legacy["core"] = {"n": n, "accuracy": round(c / n, 4)}
    return legacy


_legacy = _legacy_score_by_type(_k37_qs, _k37_answers, _k37_arm)
check("mutation 'type-vanishing bug restored': the lesson row is ABSENT and core is silently "
      "redefined over fact+current alone, looking perfectly valid (would FAIL the checks above)",
      "lesson" not in _legacy and "valid" not in _legacy.get("core", {}), str(_legacy))


print("\n- item 9B/P0(f): a partial cache (contexts for only SOME questions) is a mismatch, "
      "not present -")
_use_scratch("partial")
try:
    _seed_ctx_cs(ids=["cq0"])          # cq1 has NO cached context at all - a partial cache
    with _isolated_pacer():
        urllib.request.urlopen = _fake_chat_factory()
        cse.answer_stage([MINI_ARM], MINI_QS, MINI_POOL, cse.READER)
        res = cse.summarise([MINI_ARM], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    node = res["arms"][MINI_ARM]
    check("a partial cache (missing cq1) is flagged, not treated as present",
          node.get("valid") is False and "cq1" in (node.get("missing_contexts_ids") or []),
          str(node))
    check("K25: the root is invalid too", res.get("valid") is False)
finally:
    _restore_paths()


print("\n- item 9B/P0(f): a requested arm with no cached contexts is a NAMED refusal, never a "
      "vanished arm -")
_use_scratch("noctx")
try:
    with _isolated_pacer():
        urllib.request.urlopen = _fake_chat_factory()
        cse.answer_stage(["mem0_infer"], MINI_QS, MINI_POOL, cse.READER)  # no ctx cache exists
        res = cse.summarise(["mem0_infer"], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    check("the arm appears, naming itself as blocked",
          "mem0_infer" in (res["arms"].get("mem0_infer", {}).get("blocked") or ""),
          str(res["arms"].get("mem0_infer")))
    check("the blocked arm is marked invalid", res["arms"]["mem0_infer"].get("valid") is False)
    check("K25: the root is invalid too", res.get("valid") is False, str(res.get("valid")))

    print("\n- item 9B mutation: the 'no contexts - skipped' bug restored (no _blocked_arms "
          "recorded) - K37's n-vs-asked gate still keeps the arm present and invalid (defense "
          "in depth), but loses the SPECIFIC 'no contexts cached' diagnosis -")
    fe._save(cse._cache_path("answers"), {})     # the OLD bug: nothing records the block at all
    fe._save(cse._cache_path("verdicts"), {})
    res_mut = cse.summarise(["mem0_infer"], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    check("mutation 'skip restored': the arm no longer names itself 'blocked' (only the "
          "generic n-vs-asked gate still catches it)",
          "mem0_infer" in res_mut["arms"] and "blocked" not in res_mut["arms"]["mem0_infer"]
          and res_mut["arms"]["mem0_infer"].get("valid") is False,
          str(res_mut["arms"].get("mem0_infer")))
finally:
    _restore_paths()


print("\n- item 9B/P0(a): a 500 on /api/embed marks a contexts-stage cache's `_transport` "
      "invalid, and install() removed goes blind to it -")
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
    ctx_embed = {"cq0": [{"id": "s0", "text": "x"}]}
    fe._attach_prefixed_transport(ctx_embed, "_transport", snap)
check("a 500 on /api/embed marks the ctx's `_transport` invalid",
      ctx_embed.get("_transport", {}).get("valid") is False, str(ctx_embed.get("_transport")))

saved_install = pacer.install
pacer.install = lambda mode="pace": None
with _isolated_pacer():
    urllib.request.urlopen = _fake_embed_500
    snap = pacer.snapshot()
    try:
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/embed"))
    except urllib.error.HTTPError:
        pass
    ctx_mut = {"cq0": [{"id": "s0", "text": "x"}]}
    fe._attach_prefixed_transport(ctx_mut, "_transport", snap)
check("mutation 'install() removed': no `_transport` key at all - would FAIL the check above",
      "_transport" not in ctx_mut, str(ctx_mut))
pacer.install = saved_install


print("\n- item 9B/K25: a blocked `mem0_infer` arm invalidates the WHOLE artifact at the root, "
      "so row_refusal refuses a claim pointer on the DIFFERENT, otherwise-clean nevertwice_full "
      "arm too - and root propagation removed stops that -")
_use_scratch("rootprop")
saved_propagate = fe._propagate_root_invalidity
try:
    with _isolated_pacer():
        _seed_ctx_cs(arm=MINI_ARM)           # only nevertwice_full gets contexts
        urllib.request.urlopen = _fake_chat_factory()
        cse.answer_stage([MINI_ARM, "mem0_infer"], MINI_QS, MINI_POOL, cse.READER)
        res = cse.summarise([MINI_ARM, "mem0_infer"], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    check("the nevertwice_full arm itself is clean", "valid" not in res["arms"][MINI_ARM]["fact"],
          str(res["arms"][MINI_ARM]["fact"]))
    check("the mem0_infer arm carries its own invalidity (blocked)",
          res["arms"]["mem0_infer"].get("valid") is False, str(res["arms"]["mem0_infer"]))
    check("K25: the ROOT is invalid too, naming mem0_infer",
          res.get("valid") is False and "mem0_infer" in (res.get("invalid_reason") or ""),
          str(res.get("invalid_reason")))
    reason = rm.row_refusal(res, "arms.nevertwice_full.fact.accuracy", 0)
    check("K25: row_refusal refuses the REAL claim pointer on the CLEAN nevertwice_full arm "
          "too, because the root is invalid", reason is not None and "invalid" in reason,
          str(reason))

    fe._propagate_root_invalidity = lambda out: None
    res_mut = cse.summarise([MINI_ARM, "mem0_infer"], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    check("mutation 'root propagation removed': the root stays WRONGLY valid",
          "valid" not in res_mut, str(res_mut.get("valid")))
    reason_mut = rm.row_refusal(res_mut, "arms.nevertwice_full.fact.accuracy", 0)
    check("...and row_refusal no longer refuses the clean-looking arm either",
          reason_mut is None, str(reason_mut))
finally:
    fe._propagate_root_invalidity = saved_propagate
    _restore_paths()
check("fe._propagate_root_invalidity is restored to the real function",
      fe._propagate_root_invalidity is saved_propagate)


# ── item 9B/K36 (the auditor's mut_c2a.py, 2026-09-24): source mutations that survived the
# first pass - Y1/Y3/Y4/Y5 covered above (K37); Y2/Y6/Y7 get their own direct test; Y8 too ──

print("\n- item 9B/K36 Y2: a lesson verdict that is never stamped (or stamped stale) is caught "
      "independently of the answer's own stamp -")
_y2_head = fe.git_head()
_y2_closure = fe.engine_closure("python research/code_sessions_eval.py")
_y2_arm = "nevertwice_full"
_y2_qs = [{"id": "ly0", "type": "lesson", "markers": [], "stale_markers": []}]
_y2_akey = fe._akey("r", _y2_arm, cse.K, "ly0", "ctx")
_y2_answers = {_y2_akey: {"answer": "the lesson", "prompt_tokens": 5,
                          "commit": _y2_head, "utc": fe._utc_now_iso()}}
_y2_vkey = f"j|{_y2_akey}"
_y2_verdicts = {_y2_vkey: True, "_meta": {}}          # the verdict exists but was never stamped
_y2_out = cse.score_answers(_y2_qs, _y2_answers, _y2_verdicts, {}, "r", _y2_arm, "j",
                            head=_y2_head, closure=_y2_closure)
check("Y2: the lesson row is invalid - its verdict is not stamped current, though the answer is",
      _y2_out["lesson"].get("valid") is False and _y2_out["lesson"].get("stale_answers_count", 0) == 0
      and _y2_out["lesson"].get("stale_verdicts_count") == 1, str(_y2_out.get("lesson")))

print("\n- item 9B/K36 Y6: the answer/judge stage-level transport marks the ROOT for "
      "code_sessions too -")
_use_scratch("y6")
try:
    with _isolated_pacer():
        _seed_ctx_cs()
        urllib.request.urlopen = _fake_chat_factory()
        cse.answer_stage([MINI_ARM], MINI_QS, MINI_POOL, cse.READER)
        cse.judge_stage([MINI_ARM], MINI_QS, cse.READER, cse.JUDGE)
        cache_y6 = fe._load(cse._cache_path("answers"))
        cache_y6["_transport"] = {"valid": False, "invalid_reason": "bypass_calls.requests=1"}
        fe._save(cse._cache_path("answers"), cache_y6)
        res_y6 = cse.summarise([MINI_ARM], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    check("Y6: the root is invalid, naming the answer-stage transport",
          res_y6.get("valid") is False and "answer stage" in (res_y6.get("invalid_reason") or ""),
          str(res_y6.get("invalid_reason")))
finally:
    _restore_paths()

print("\n- item 9B/K36 Y7: a failed embed while building nevertwice_full's OWN contexts "
      "reaches the arm (fe._fold_arm_context_provenance is actually called in summarise()) -")
_use_scratch("y7")
try:
    ctx_y7 = {q["id"]: [{"id": "s0", "text": "ctx"}] for q in MINI_QS}
    ctx_y7["_transport"] = {"valid": False, "invalid_reason": "2 embed call(s) failed"}
    fe._save(cse._ctx_path(MINI_ARM), ctx_y7)
    fe._save(cse._cache_path("answers"), {})
    fe._save(cse._cache_path("verdicts"), {})
    res_y7 = cse.summarise([MINI_ARM], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    check("Y7: the arm's own node is marked invalid, naming the failed embed",
          res_y7["arms"][MINI_ARM].get("valid") is False
          and "embed" in (res_y7["arms"][MINI_ARM].get("invalid_reason") or ""),
          str(res_y7["arms"].get(MINI_ARM)))
finally:
    _restore_paths()

print("\n- item 9B/K36 Y8: the RN5 competitor_cache record is written for a non-engine arm -")
_use_scratch("y8")
try:
    _seed_ctx_cs(arm="mem0_infer")
    with _isolated_pacer():
        urllib.request.urlopen = _fake_chat_factory()
        cse.answer_stage(["mem0_infer"], MINI_QS, MINI_POOL, cse.READER)
        cse.judge_stage(["mem0_infer"], MINI_QS, cse.READER, cse.JUDGE)
        res_y8 = cse.summarise(["mem0_infer"], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    check("Y8: a non-engine (competitor) arm's contexts cache file provenance is recorded",
          "mem0_infer" in (res_y8.get("competitor_cache") or {})
          and "sha256" in res_y8["competitor_cache"]["mem0_infer"], str(res_y8.get("competitor_cache")))
finally:
    _restore_paths()


print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
