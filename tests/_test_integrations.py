#!/usr/bin/env python3
"""Tests for the optional framework adapters. The field-mapping helpers are
framework-free and always exercised; the adapter classes are only asserted to raise a
helpful ImportError when their framework is absent (so CI passes without LangChain /
LlamaIndex installed). If a framework IS present, its mapping is still validated."""
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import api
from integrations import langchain_memory as lc
from integrations import llamaindex_retriever as li

_RES = [{"ntype": "pattern", "title": "Crash-safe writes", "description": "tmp then replace",
         "prevention": "never write partial files", "project": "pr", "stem": "s1", "score": 0.83},
        {"ntype": "mistake", "title": "OOM", "description": "", "prevention": "",
         "project": "pr", "stem": "s2", "score": 0.4}]


# ── LangChain mapping (framework-free) ──────────────────────────────────────────

def test_lc_recall_to_documents_shape():
    docs = lc.recall_to_documents(_RES)
    assert len(docs) == 2
    assert "PATTERN — Crash-safe writes" in docs[0]["page_content"]
    assert "Prevention: never write partial files" in docs[0]["page_content"]
    md = docs[0]["metadata"]
    assert md["stem"] == "s1" and md["project"] == "pr" and md["source"] == "nevertwice"
    assert md["score"] == 0.83


def test_lc_format_memory_empty_and_nonempty():
    assert lc.format_memory([]) == ""
    s = lc.format_memory(_RES)
    assert "Relevant memory" in s
    assert "- PATTERN — Crash-safe writes" in s and "- MISTAKE — OOM" in s


# ── LlamaIndex mapping (framework-free) ─────────────────────────────────────────

def test_li_recall_to_nodes_shape():
    nodes = li.recall_to_nodes(_RES)
    assert nodes[0]["text"].startswith("PATTERN — Crash-safe writes")
    assert nodes[0]["score"] == 0.83
    assert nodes[0]["metadata"]["stem"] == "s1" and nodes[0]["metadata"]["source"] == "nevertwice"


def test_li_recall_to_nodes_score_defaults_zero():
    nodes = li.recall_to_nodes([{"title": "X"}])
    assert nodes[0]["score"] == 0.0


# ── lazy-import contract ────────────────────────────────────────────────────────

def test_lc_retriever_raises_without_langchain():
    if lc._HAVE_LC:
        return  # langchain present → real class; nothing to assert here
    try:
        lc.NevertwiceRetriever(project="p")
        assert False, "expected ImportError"
    except ImportError as e:
        assert "langchain" in str(e).lower()


def test_lc_memory_load_save_and_flush_resets():
    # NevertwiceMemory is a plain class (no BaseMemory) — works without langchain.
    mem = lc.NevertwiceMemory(project="pr", memory_key="hist", k=3)
    assert mem.memory_variables == ["hist"]
    with mock.patch.object(api, "recall", return_value=_RES):
        mv = mem.load_memory_variables({"input": "crash"})
    assert "hist" in mv and "Crash-safe writes" in mv["hist"]
    with mock.patch.object(api, "capture_session",
                           return_value={"stored": True, "patterns": 1}) as cs:
        mem.save_context({"input": "q"}, {"output": "a"})
        res = mem.flush()
    cs.assert_called_once()
    assert res["stored"] is True
    # flush() reset the buffer → a second flush must NOT re-extract (no duplicate notes)
    with mock.patch.object(api, "capture_session") as cs2:
        res2 = mem.flush()
    cs2.assert_not_called()
    assert res2["stored"] is False


def test_li_retriever_raises_without_llamaindex():
    if li._HAVE_LI:
        return
    try:
        li.NevertwiceRetriever(project="p")
        assert False, "expected ImportError"
    except ImportError as e:
        assert "llamaindex" in str(e).lower()


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
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
