#!/usr/bin/env python3
"""Optional trained cross-encoder reranker - the one precision lever that actually works.

Measured on LongMemEval-oracle (940 sessions / 500 questions, external ground truth):
reranking the calibrated-fusion top-10 with the purpose-trained cross-encoder bge-reranker-v2-m3
lifts recall@1 0.550 → 0.614 and MRR 0.657 → 0.712. This is the
opposite of a *promptable* LLM reranker, which DEGRADES recall@1 - see
research/W2_PRECISION.md. So this is the reranker Nevertwice ships, and it is OFF by default.

Enable with NEVERTWICE_XRERANK=1. Heavy deps (torch + transformers) are imported lazily
ONLY when enabled, so the stdlib core stays dependency-free for everyone else. Install:
    pip install nevertwice-memory[reranker]
The model (~2 GB) downloads from HuggingFace on first use and runs best on a GPU.
"""
import os

MODEL = os.environ.get("NEVERTWICE_XRERANK_MODEL", "BAAI/bge-reranker-v2-m3")
try:                                     # optional module stays standalone; degrade, don't crash
    MAX_LEN = int(os.environ.get("NEVERTWICE_XRERANK_MAXLEN", "") or 512)
except ValueError:
    MAX_LEN = 512
ENABLED = os.environ.get("NEVERTWICE_XRERANK", "0") != "0"
_state = {}


def available() -> bool:
    """True iff torch + transformers import - the opt-in deps. Never required by core."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except Exception:
        return False


def _load():
    if "model" in _state:
        return _state
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MODEL)
    use_cuda = torch.cuda.is_available()
    dtype = torch.float16 if use_cuda else torch.float32
    try:                                     # transformers >=5 renamed torch_dtype → dtype
        model = AutoModelForSequenceClassification.from_pretrained(MODEL, dtype=dtype)
    except TypeError:
        model = AutoModelForSequenceClassification.from_pretrained(MODEL, torch_dtype=dtype)
    model = model.to("cuda" if use_cuda else "cpu").eval()
    _state.update(tok=tok, model=model, dev="cuda" if use_cuda else "cpu", torch=torch)
    return _state


def rerank_scores(query: str, passages, batch_size: int = 16):
    """Relevance logit per passage for `query` (higher = more relevant). Empty → []."""
    if not passages:
        return []
    if batch_size <= 0:                  # range(0,n,0) raises; a negative step silently drops all
        raise ValueError("batch_size must be positive")
    st = _load()
    tok, model, dev, torch = st["tok"], st["model"], st["dev"], st["torch"]
    out = []
    with torch.no_grad():
        for i in range(0, len(passages), batch_size):
            chunk = passages[i:i + batch_size]
            inp = tok([[query, p] for p in chunk], padding=True, truncation=True,
                      max_length=MAX_LEN, return_tensors="pt").to(dev)
            out.extend(model(**inp).logits.view(-1).float().tolist())
    return out


def _note_text(r: dict) -> str:
    """Cross-encoder input for a recall result: its title + description + prevention.
    (No TYPE prefix - relevance scoring wants the content, not the label.)"""
    parts = [r.get("title") or "", r.get("description") or "", r.get("prevention") or ""]
    return " ".join(p for p in parts if p).strip()


def reorder(query: str, results: list[dict], k: int) -> list[dict]:
    """Re-rank recall result dicts by the cross-encoder and return the top-k, each
    annotated with `xrerank_score`. Degrades safely: if deps are missing, the model
    fails, or scores don't line up, the input order is preserved (truncated to k)."""
    if not results or len(results) <= 1:
        return results[:k]
    try:
        scores = rerank_scores(query, [_note_text(r) for r in results])
    except Exception:
        return results[:k]
    if not scores or len(scores) != len(results):
        return results[:k]
    # return fresh dicts (don't mutate the caller's results in place - audit 2026-06-18)
    order = sorted(range(len(results)), key=lambda i: -scores[i])[:k]
    return [{**results[i], "xrerank_score": round(float(scores[i]), 3)} for i in order]
