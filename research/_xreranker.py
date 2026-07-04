#!/usr/bin/env python3
"""RESEARCH shim — the trained cross-encoder reranker now lives in the core opt-in
module `nevertwice.reranker_ce` (it is a shipped, measured precision win, not just an
experiment). This re-exports it so `longmem_eval.py --xrerank` keeps a stable import.
See research/W2_PRECISION.md for the LongMemEval numbers behind shipping it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
from reranker_ce import MODEL, MAX_LEN, available, _load, rerank_scores, reorder  # noqa: F401

__all__ = ["MODEL", "MAX_LEN", "available", "_load", "rerank_scores", "reorder"]
