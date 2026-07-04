#!/usr/bin/env python3
"""LlamaIndex integration for Nevertwice — a retriever over agent memory.

    from nevertwice.integrations.llamaindex_retriever import NevertwiceRetriever
    retriever = NevertwiceRetriever(project="myproj", k=5)
    nodes = retriever.retrieve("how did we fix the OOM crash")
    # plug into a query engine:
    #   from llama_index.core.query_engine import RetrieverQueryEngine
    #   engine = RetrieverQueryEngine.from_args(retriever)

Recall goes through nevertwice.api (stdlib core, unchanged). The mapping helper
`recall_to_nodes` is framework-free and unit-tested without LlamaIndex installed; the
retriever class needs `pip install nevertwice-memory[llamaindex]`.
"""
import sys
from pathlib import Path

try:                                  # installed package — proper relative import (no sys.path/shadowing)
    from .. import api as _api
except ImportError:                   # flat-script use — fall back to a path insert
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import api as _api

INSTALL_HINT = "LlamaIndex not installed — `pip install nevertwice-memory[llamaindex]`"

try:
    from llama_index.core.retrievers import BaseRetriever
    from llama_index.core.schema import NodeWithScore, TextNode
    _HAVE_LI = True
except Exception:  # pragma: no cover - exercised only without llama_index
    _HAVE_LI = False


# ── framework-free mapping (unit-tested without LlamaIndex) ─────────────────────

def recall_to_nodes(results: list[dict]) -> list[dict]:
    """recall() dicts → [{text, score, metadata}] ready to build LlamaIndex nodes."""
    out = []
    for r in results:
        out.append({
            "text": _api.format_note(r),
            "score": r.get("score") or 0.0,
            "metadata": {"ntype": r.get("ntype"), "project": r.get("project"),
                         "stem": r.get("stem"), "title": r.get("title"),
                         "source": "nevertwice"},
        })
    return out


# ── LlamaIndex retriever ────────────────────────────────────────────────────────

if _HAVE_LI:
    class NevertwiceRetriever(BaseRetriever):
        """A LlamaIndex Retriever backed by Nevertwice recall — returns scored TextNodes."""

        def __init__(self, project: str | None = None, k: int = 5,
                     rerank: bool = False, **kwargs):
            # BaseRetriever is a Pydantic model in current LlamaIndex — init it FIRST,
            # then stash our non-field state via object.__setattr__ so Pydantic's field
            # validation (which rejects unknown attrs / would reset pre-init assignments)
            # never sees them. Works on both the Pydantic and the legacy plain base.
            super().__init__(**kwargs)
            object.__setattr__(self, "project", project)
            object.__setattr__(self, "k", k)
            object.__setattr__(self, "rerank", rerank)

        def _retrieve(self, query_bundle, **kwargs):
            q = getattr(query_bundle, "query_str", None) or str(query_bundle)
            results = _api.recall(q, self.project, self.k, rerank=self.rerank)
            return [NodeWithScore(node=TextNode(text=d["text"], metadata=d["metadata"]),
                                  score=d["score"])
                    for d in recall_to_nodes(results)]
else:  # pragma: no cover
    class NevertwiceRetriever:
        def __init__(self, *a, **k):
            raise ImportError(INSTALL_HINT)
