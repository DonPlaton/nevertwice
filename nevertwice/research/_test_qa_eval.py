#!/usr/bin/env python3
"""Self-check for qa_eval.py — mocked LLM, no network, no GPU. Verifies the
accuracy math, per-type aggregation, the oracle vs retrieved context selection,
the resumable cache (no second LLM call), the down-backend guard, a distinct
judge model, and the deepseek-reasoner response parsing (mocked urllib)."""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import memory_hook as m          # noqa: E402
import longmem_eval as le        # noqa: E402
import qa_eval as qa             # noqa: E402

GOLD = {"q1": "blue", "q2": "red", "q3": "tea"}
# fake answerer: right for q1/q2, wrong for q3 → expected 2/3 overall.
ANS = {"q1": "blue", "q2": "red", "q3": "coffee"}


def make_llm(counter):
    def llm(prompt):
        counter[0] += 1
        if "REFERENCE ANSWER:" in prompt:                     # judge call
            ref = re.search(r"REFERENCE ANSWER: (.*)", prompt).group(1).strip()
            pred = re.search(r"MODEL ANSWER: (.*)", prompt).group(1).strip()
            return {"correct": ref == pred}                   # key-fact match
        q = re.search(r"QUESTION: (.*)", prompt).group(1).strip()
        return {"answer": ANS.get(q, "?")}
    return llm


DATA = [
    {"question_id": "q1", "question_type": "temporal-reasoning", "question": "q1",
     "answer": "blue", "answer_session_ids": ["s1"]},
    {"question_id": "q2", "question_type": "temporal-reasoning", "question": "q2",
     "answer": "red", "answer_session_ids": ["s2"]},
    {"question_id": "q3", "question_type": "single-session-preference", "question": "q3",
     "answer": "tea", "answer_session_ids": ["s3"]},
]
POOL = {"s1": "the sky was blue today", "s2": "the car is red and fast",
        "s3": "i drink tea each morning"}


def test_oracle_accuracy_and_types():
    cnt = [0]
    results, cache = qa.evaluate(DATA, POOL, ["oracle"], k=5, budget=1000,
                                 llm=make_llm(cnt), model="t")
    r = results["oracle"]
    assert abs(r["accuracy"] - 2 / 3) < 1e-9, r["accuracy"]
    assert r["n"] == 3
    assert r["by_type"]["temporal-reasoning"] == {"acc": 1.0, "n": 2}
    assert r["by_type"]["single-session-preference"] == {"acc": 0.0, "n": 1}
    assert cnt[0] == 6                                          # 3 answers + 3 judge calls
    print("ok test_oracle_accuracy_and_types")


def test_cache_is_resumable():
    cnt = [0]
    llm = make_llm(cnt)
    _, cache = qa.evaluate(DATA, POOL, ["oracle"], k=5, budget=1000, llm=llm, model="t")
    first = cnt[0]
    # re-run with the SAME cache → zero new LLM calls, identical result
    results2, _ = qa.evaluate(DATA, POOL, ["oracle"], k=5, budget=1000, llm=llm,
                              model="t", cache=cache)
    assert cnt[0] == first, f"cache miss: {cnt[0]} != {first}"
    assert abs(results2["oracle"]["accuracy"] - 2 / 3) < 1e-9
    print("ok test_cache_is_resumable")


def test_retrieved_setting_ranks_and_answers():
    # one 3-dim vector per session/question; q1 closest to s1, etc. (identity-ish)
    svec = {"s1": [1.0, 0.0, 0.0], "s2": [0.0, 1.0, 0.0], "s3": [0.0, 0.0, 1.0]}
    qvec = {"q1": [1.0, 0.0, 0.0], "q2": [0.0, 1.0, 0.0], "q3": [0.0, 0.0, 1.0]}
    pool_ids = list(POOL)
    bm = le.build_bm25(pool_ids, {s: m._token_list(POOL[s]) for s in pool_ids})
    cnt = [0]
    results, _ = qa.evaluate(DATA, POOL, ["retrieved"], k=1, budget=1000,
                             llm=make_llm(cnt), model="t",
                             svec=svec, qvec=qvec, pool_ids=pool_ids, bm=bm)
    r = results["retrieved"]
    assert r["n"] == 3, r["n"]
    assert abs(r["accuracy"] - 2 / 3) < 1e-9, r["accuracy"]     # top-1 retrieves the right session
    print("ok test_retrieved_setting_ranks_and_answers")


def test_down_backend_raises():
    try:
        qa.evaluate(DATA, POOL, ["oracle"], k=5, budget=1000,
                    llm=lambda p: {}, model="t")              # backend returns nothing
    except RuntimeError as e:
        assert "returned nothing" in str(e)
        print("ok test_down_backend_raises")
        return
    raise AssertionError("expected RuntimeError on a down backend")


def test_context_budget_and_relevance():
    ctx = qa._context(["s1", "s2"], POOL, budget=40)
    assert len(ctx) <= 40
    print("ok test_context_budget_and_relevance")


def test_distinct_judge_llm():
    # a wrong-but-confident reader + a judge that always says correct → accuracy 1.0,
    # proving the judge path uses judge_llm, not the answerer.
    reader = lambda p: {"answer": "totally wrong"}
    judge = lambda p: {"correct": True}
    results, _ = qa.evaluate(DATA, POOL, ["oracle"], k=5, budget=1000,
                             llm=reader, judge_llm=judge, model="t")
    assert results["oracle"]["accuracy"] == 1.0
    print("ok test_distinct_judge_llm")


class _FakeResp:
    def __init__(self, payload): self._b = json.dumps(payload).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _patch_urlopen(content):
    payload = {"choices": [{"message": {"content": content}}]}
    return lambda req, timeout=0: _FakeResp(payload)


def test_reasoner_parses_json_fenced_and_prose():
    os.environ["DEEPSEEK_API_KEY"] = "test-key"          # presence only; urlopen is mocked
    orig = qa.urllib.request.urlopen
    call = qa.make_deepseek_reasoner("deepseek-reasoner", timeout=1, retries=1)
    try:
        qa.urllib.request.urlopen = _patch_urlopen('{"answer": "blue"}')
        assert call("q")["answer"] == "blue"             # plain JSON
        qa.urllib.request.urlopen = _patch_urlopen('```json\n{"answer": "red"}\n```')
        assert call("q")["answer"] == "red"              # fenced JSON
        qa.urllib.request.urlopen = _patch_urlopen('The answer is teal.')
        assert "teal" in call("q")["answer"]             # bare prose → kept, not dropped
        qa.urllib.request.urlopen = _patch_urlopen('{"correct": true}')
        assert call("q")["correct"] is True              # judge JSON
    finally:
        qa.urllib.request.urlopen = orig
    print("ok test_reasoner_parses_json_fenced_and_prose")


def test_rerank_fn_is_applied():
    # a rerank_fn that picks s3 (gold for q3) for every question → q3 now answerable from
    # its own session; proves evaluate threads rerank_fn into the retrieved path.
    svec = {"s1": [1.0, 0.0, 0.0], "s2": [0.0, 1.0, 0.0], "s3": [0.0, 0.0, 1.0]}
    qvec = {"q1": [1.0, 0.0, 0.0], "q2": [0.0, 1.0, 0.0], "q3": [0.0, 0.0, 1.0]}
    pool_ids = list(POOL)
    bm = le.build_bm25(pool_ids, {s: m._token_list(POOL[s]) for s in pool_ids})
    seen = []

    def rr(question, ranked, k):
        seen.append(question)
        return ranked[:k]                                # identity, but records it was called

    cnt = [0]
    results, _ = qa.evaluate(DATA, POOL, ["retrieved"], k=1, budget=1000, llm=make_llm(cnt),
                             model="t", svec=svec, qvec=qvec, pool_ids=pool_ids, bm=bm,
                             rerank_fn=rr)
    assert len(seen) == 3, seen                          # rerank_fn ran once per question
    assert results["retrieved"]["n"] == 3
    print("ok test_rerank_fn_is_applied")


def test_reasoner_empty_is_failure():
    os.environ["DEEPSEEK_API_KEY"] = "test-key"
    orig = qa.urllib.request.urlopen
    call = qa.make_deepseek_reasoner("deepseek-reasoner", timeout=1, retries=1)
    try:
        qa.urllib.request.urlopen = _patch_urlopen("")   # empty content → {} (down-backend guard fires)
        assert call("q") == {}
    finally:
        qa.urllib.request.urlopen = orig
    print("ok test_reasoner_empty_is_failure")


if __name__ == "__main__":
    test_oracle_accuracy_and_types()
    test_cache_is_resumable()
    test_retrieved_setting_ranks_and_answers()
    test_down_backend_raises()
    test_context_budget_and_relevance()
    test_distinct_judge_llm()
    test_reasoner_parses_json_fenced_and_prose()
    test_rerank_fn_is_applied()
    test_reasoner_empty_is_failure()
    print("\nall qa_eval self-checks passed")
