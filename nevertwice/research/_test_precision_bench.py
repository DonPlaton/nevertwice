#!/usr/bin/env python3
"""Tests for precision_bench.py (W2) and the shared _rerank.py primitive. Pure functions +
offline-mocked rerank backends + a source-level privacy regression. No Ollama/network/vault."""
import io
import json
import os
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import precision_bench as pb
import _rerank as rr


@contextmanager
def _fake_urlopen(payload: dict):
    """Patch urllib so a backend call returns `payload` as the HTTP body, offline."""
    body = json.dumps(payload).encode("utf-8")

    class _Resp(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): self.close()

    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: _Resp(body)
    try:
        yield
    finally:
        urllib.request.urlopen = orig


# ── _rerank primitive ─────────────────────────────────────────────────────────

def test_parse_scores_valid():
    assert rr.parse_scores({"scores": [1, 5, 9]}, 3) == [1.0, 5.0, 9.0]


def test_parse_scores_clamps_and_pads():
    assert rr.parse_scores({"scores": [99, -4]}, 3) == [10.0, 0.0, 0.0]


def test_parse_scores_rejects_garbage():
    assert rr.parse_scores({"nope": 1}, 3) is None
    assert rr.parse_scores("not a dict", 3) is None
    assert rr.parse_scores({"scores": []}, 3) is None
    assert rr.parse_scores({"scores": ["x", 7]}, 2) == [0.0, 7.0]


def test_prompt_is_bounded():
    huge = "x" * 5000
    p = rr.build_prompt(huge, [huge, huge])
    assert p.count("x") <= rr.CHAR_BUDGET * 3 + 10


def test_deepseek_blocked_without_key():
    os.environ.pop("DEEPSEEK_API_KEY", None)
    st = {"calls": 0, "errors": 0, "prompt_chars": 0}
    assert rr.deepseek_rerank("q", ["c1", "c2"], st) is None
    assert st["calls"] == 0          # blocked before any network attempt


def test_deepseek_offline_mock():
    os.environ["DEEPSEEK_API_KEY"] = "dummy-key-for-test"     # not sk-* so the privacy scan stays clean
    st = {"calls": 0, "errors": 0, "prompt_chars": 0}
    reply = {"choices": [{"message": {"content": json.dumps({"scores": [3, 7]})}}]}
    try:
        with _fake_urlopen(reply):
            scores = rr.deepseek_rerank("query text", ["cand a", "cand b"], st)
        assert scores == [3.0, 7.0] and st["calls"] == 1
    finally:
        os.environ.pop("DEEPSEEK_API_KEY", None)


def test_ollama_offline_mock():
    st = {"calls": 0, "errors": 0, "prompt_chars": 0}
    reply = {"response": json.dumps({"scores": [10, 0]})}
    with _fake_urlopen(reply):
        scores = rr.ollama_rerank("query", ["match", "nope"], "test-model", st)
    assert scores == [10.0, 0.0] and st["calls"] == 1 and st["prompt_chars"] > 0


def test_ollama_bad_json_returns_none():
    st = {"calls": 0, "errors": 0, "prompt_chars": 0}
    with _fake_urlopen({"response": "not json at all"}):
        assert rr.ollama_rerank("q", ["a"], "test-model", st) is None
    assert st["errors"] == 1


def test_ollama_tolerant_missing_commas():
    # small models drop commas: `{"scores":[10 9 8]}` — the regex fallback must still recover them
    st = {"calls": 0, "errors": 0, "prompt_chars": 0}
    with _fake_urlopen({"response": '{"scores":[10 9 8]}'}):
        scores = rr.ollama_rerank("q", ["a", "b", "c"], "test-model", st)
    assert scores == [10.0, 9.0, 8.0] and st["errors"] == 0


# ── precision_bench bench logic ─────────────────────────────────────────────────

def test_clusters_groups_by_cosine():
    a, b, c = [1.0, 0.0], [0.99, 0.01], [0.0, 1.0]
    notes = [("p1", {"vec": a}), ("p2", {"vec": b}), ("p3", {"vec": c})]
    groups = pb._clusters(notes, 0.9)
    assert any(set(g) == {"p1", "p2"} for g in groups)
    assert all("p3" not in g for g in groups)


def test_build_queries_cross_session_gt():
    pb.VEC.clear(); pb.TEXT.clear()
    s1 = "2026-06-01-proj-pattern-aaaa"      # real stem layout: date-project-type-slug
    s2 = "2026-06-09-proj-pattern-bbbb"
    by_proj = {"proj": [(s1, {"vec": [1.0, 0.0]}), (s2, {"vec": [0.98, 0.02]})]}
    qs = pb._build_queries(by_proj)
    assert len(qs) == 2
    qi, cand, gt = qs[0]
    assert gt and gt <= {s1, s2} and qi not in gt


def test_baseline_ranks_by_cosine():
    pb.VEC.clear()
    pb.VEC.update({"q": [1.0, 0.0], "near": [0.9, 0.1], "far": [0.0, 1.0]})
    assert pb._baseline("q", ["far", "near"]) == ["near", "far"]


def test_rocchio_beta0_is_baseline():
    pb.VEC.clear()
    pb.VEC.update({"q": [1.0, 0.0], "a": [0.8, 0.2], "b": [0.2, 0.8]})
    assert pb._rocchio(0.0, 2)("q", ["a", "b"]) == pb._baseline("q", ["a", "b"])


def test_source_has_no_personal_data():
    """Privacy regression: the committed modules embed NO personal data. precision_bench DOES read
    note text at runtime (for the reranker) but ONLY from the local cache — never hard-coded or
    persisted. Real project/name markers are supplied via NEVERTWICE_PRIVACY_MARKERS locally
    (hard-coding the denylist here would itself leak it into the public repo); the built-in default
    catches generic personal signatures (vault path, email, user home)."""
    extra = [x.strip().lower() for x in os.environ.get("NEVERTWICE_PRIVACY_MARKERS", "").split(",")
             if x.strip()]
    generic = ["obsidian", "@gmail", "users\\", "users/", "c:\\users", "d:\\obsidian"]
    for mod in (pb, rr):
        src = Path(mod.__file__).read_text(encoding="utf-8").lower()
        for mk in generic + extra:
            assert mk not in src, f"personal marker {mk!r} leaked into {mod.__name__}"


def test_saved_metrics_are_aggregate_only():
    """The bench may read note text in-process but must persist ONLY aggregate metrics. Guard the
    save schema: no text field name may appear in what precision_bench writes to disk."""
    src = Path(pb.__file__).read_text(encoding="utf-8")
    save_region = src.split("if SAVE", 1)[-1]      # the persistence block
    for textkey in ('"title"', '"desc"', '"prevention"', 'TEXT['):
        assert textkey not in save_region, f"{textkey} must not be written to the aggregate JSON"


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
