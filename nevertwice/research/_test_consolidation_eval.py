#!/usr/bin/env python3
"""Tests for consolidation_eval.py (Phase 2). Pure functions + offline-mocked synthesis + a
source-level privacy regression. No Ollama/network/vault required."""
import io
import json
import math
import os
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import consolidation_eval as ce


@contextmanager
def _fake_urlopen(payload: dict):
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


def test_unit_normalises():
    v = ce._unit([3.0, 4.0])
    assert abs(math.sqrt(v[0] ** 2 + v[1] ** 2) - 1.0) < 1e-9


def test_mean_is_centroid():
    assert ce._mean([[0.0, 0.0], [2.0, 4.0]]) == [1.0, 2.0]


def test_clusters_groups_by_cosine():
    notes = [("a", {"vec": [1.0, 0.0]}), ("b", {"vec": [0.98, 0.02]}), ("c", {"vec": [0.0, 1.0]})]
    groups = ce._clusters(notes, 0.9)
    assert any(set(g) == {"a", "b"} for g in groups) and all("c" not in g for g in groups)


def test_synthesise_offline_mock():
    st = {"calls": 0, "errors": 0, "prompt_chars": 0}
    reply = {"response": json.dumps({"title": "Size batches to VRAM", "principle": "Pick batch by free VRAM."})}
    with _fake_urlopen(reply):
        text = ce.synthesise_principle(["oom at batch 64", "oom at batch 128"], st)
    assert "Size batches to VRAM" in text and "Pick batch" in text
    assert st["calls"] == 1 and st["errors"] == 0


def test_synthesise_failure_returns_empty():
    st = {"calls": 0, "errors": 0, "prompt_chars": 0}
    with _fake_urlopen({"response": "}{ not json"}):
        text = ce.synthesise_principle(["x"], st)
    assert text == "" and st["errors"] == 1


def test_source_has_no_personal_data():
    extra = [x.strip().lower() for x in os.environ.get("NEVERTWICE_PRIVACY_MARKERS", "").split(",")
             if x.strip()]
    generic = ["obsidian", "@gmail", "users\\", "users/", "c:\\users", "d:\\obsidian"]
    src = Path(ce.__file__).read_text(encoding="utf-8").lower()
    for mk in generic + extra:
        assert mk not in src, f"personal marker {mk!r} leaked into consolidation_eval"


def test_saved_metrics_are_aggregate_only():
    """May read note text in-process, but persists ONLY aggregate metrics — no text field name in
    the save block."""
    src = Path(ce.__file__).read_text(encoding="utf-8")
    save_region = src.split("if SAVE", 1)[-1]
    for textkey in ('"title"', '"desc"', '"prevention"', 'TEXT[', 'ptext', 'principle_text'):
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
