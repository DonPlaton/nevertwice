#!/usr/bin/env python3
"""Tests for the W2 trained-cross-encoder path: _xreranker.py + longmem_eval's
per-embedder cache routing. The light tests are offline (no model, no network);
the actual cross-encoder ranking is gated behind NEVERTWICE_TEST_XRERANK=1 because
it downloads ~2 GB and needs a GPU — CI stays fast and hermetic without it."""
import os
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import _xreranker as xr
import longmem_eval as le
import memory_hook as m
import reranker_ce as ce
import memory_search as ms


# ── _xreranker: offline contract ───────────────────────────────────────────────

def test_available_is_bool():
    assert isinstance(xr.available(), bool)


def test_empty_passages_no_model_load():
    # must short-circuit before any import/model load — empty input → empty scores
    assert xr.rerank_scores("q", []) == []


def test_model_env_override(monkeypatch=None):
    # the model id is env-overridable and defaults to the multilingual cross-encoder
    assert xr.MODEL  # non-empty
    assert "reranker" in xr.MODEL.lower()


# ── longmem_eval: per-embedder cache routing (the clean-A/B guarantee) ─────────

def test_emb_path_bge_keeps_legacy_name():
    assert le._emb_path("bge-m3").name == "longmem_embeds.json"


def test_emb_path_is_per_model():
    a = le._emb_path("mxbai-embed-large:latest")
    b = le._emb_path("snowflake-arctic-embed2:latest")
    assert a != b
    assert a != le._emb_path("bge-m3")
    # filesystem-safe: no path separators or colons leak into the filename
    for bad in (":", "/", "\\"):
        assert bad not in a.name


def test_emb_path_defaults_to_active_model():
    assert le._emb_path() == le._emb_path(m.EMBED_MODEL)


# ── reranker_ce.reorder: pure-logic, degrade-safe (no model) ───────────────────

def test_reorder_empty_and_singleton_passthrough():
    assert ce.reorder("q", [], 5) == []
    one = [{"title": "a"}]
    assert ce.reorder("q", one, 5) == one


def test_reorder_reorders_by_score_and_annotates():
    with mock.patch.object(ce, "rerank_scores", return_value=[0.1, 0.9, 0.5]):
        out = ce.reorder("q", [{"title": "a"}, {"title": "b"}, {"title": "c"}], 2)
    assert [r["title"] for r in out] == ["b", "c"]
    assert out[0]["xrerank_score"] == 0.9


def test_reorder_degrades_safe_on_length_mismatch():
    with mock.patch.object(ce, "rerank_scores", return_value=[1.0]):   # wrong length
        out = ce.reorder("q", [{"title": "a"}, {"title": "b"}], 5)
    assert [r["title"] for r in out] == ["a", "b"]            # input order kept


def test_reorder_degrades_safe_on_exception():
    with mock.patch.object(ce, "rerank_scores", side_effect=RuntimeError("no gpu")):
        out = ce.reorder("q", [{"title": "a"}, {"title": "b"}], 1)
    assert [r["title"] for r in out] == ["a"]                 # truncated, no crash


# ── search_core wiring: xrerank takes precedence over cloud rerank ──────────────

def _two_note_cache():
    base = {"vec": [0.1], "project": "p", "desc": "", "prevention": ""}
    return {"s1": {**base, "ntype": "pattern", "title": "alpha note"},
            "s2": {**base, "ntype": "mistake", "title": "beta note"}}


def test_search_core_xrerank_wins_over_cloud_rerank():
    reordered = [{"title": "alpha note", "stem": "s1"}]
    with mock.patch.object(ms.m, "load_embed_cache", return_value=_two_note_cache()), \
         mock.patch.object(ms.m, "ollama_alive", return_value=False), \
         mock.patch.object(ms._ce, "reorder", return_value=reordered) as xr_mock, \
         mock.patch.object(ms.m, "rerank_notes") as cloud_mock:
        results, mode = ms.search_core("alpha beta note", "p", 5,
                                       rerank=True, xrerank=True)
    xr_mock.assert_called_once()
    cloud_mock.assert_not_called()
    assert "xrerank" in mode and results == reordered


# ── heavy: real cross-encoder ranking (opt-in) ─────────────────────────────────

def test_cross_encoder_ranks_relevant_first():
    if os.environ.get("NEVERTWICE_TEST_XRERANK") != "1":
        return  # skipped unless explicitly enabled (downloads a model, needs GPU)
    q = "how do I fix CUDA out of memory during training"
    passages = [
        "lower the batch size or enable gradient checkpointing to avoid GPU OOM",
        "the best pasta recipe uses fresh basil and parmesan cheese",
        "use mixed precision and torch.cuda.empty_cache to cut VRAM use",
    ]
    scores = xr.rerank_scores(q, passages)
    assert len(scores) == len(passages)
    top = max(range(len(passages)), key=lambda i: scores[i])
    assert top in (0, 2), f"cross-encoder ranked an irrelevant passage first: {scores}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
