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
          "recorded) - the requested arm VANISHES instead of appearing blocked -")
    fe._save(cse._cache_path("answers"), {})     # the OLD bug: nothing records the block at all
    fe._save(cse._cache_path("verdicts"), {})
    res_mut = cse.summarise(["mem0_infer"], MINI_QS, MINI_CORPUS, cse.READER, cse.JUDGE)
    check("mutation 'skip restored': the requested arm is silently ABSENT from the artifact "
          "(would FAIL the two checks above)", "mem0_infer" not in res_mut["arms"],
          str(res_mut["arms"]))
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


print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
