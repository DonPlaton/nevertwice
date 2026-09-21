#!/usr/bin/env python3
"""Optional trained cross-encoder reranker - the one precision lever that actually works.

Measured on LongMemEval-oracle (940 sessions / 500 questions, external ground truth):
reranking the calibrated-fusion top-10 with the purpose-trained cross-encoder bge-reranker-v2-m3
lifts recall@1 0.550 → 0.614 and MRR 0.657 → 0.712. This is the
opposite of a *promptable* LLM reranker, which DEGRADES recall@1 - see
research/W2_PRECISION.md. So this is the reranker Nevertwice ships.

Opting in: `pip install nevertwice[reranker]`, then one run with NEVERTWICE_XRERANK=1
(that first run downloads the ~2 GB model from HuggingFace). From then on it stays on by
itself - `auto` means "deps installed, model already cached, AND torch built for a GPU", so a
machine that merely has torch for other work never gets a surprise download and a machine
without a GPU never gets a surprise nine seconds a query. NEVERTWICE_XRERANK=1/0 forces it
either way. Heavy deps (torch + transformers) import lazily ONLY when a rerank actually
runs, so the stdlib core stays dependency-free for everyone else.

It does not run *best* on a GPU; outside one it does not run. Measured 2026-09-22 at the
shipped pool of 15 candidates: **36.7 ms on a GPU, 8 911 ms on the CPU** - a factor of 243,
linear in the pool at 2.45-2.56 ms a passage on the GPU. Model load is ~9.1 s on the GPU and
~6.7 s on the CPU, so a process that lives for one query cannot use this at all regardless of
device: the reranker needs a resident process AND a GPU, which is an architecture and not a
setting.
"""
import importlib.util
import os
import sys
from pathlib import Path

MODEL = os.environ.get("NEVERTWICE_XRERANK_MODEL", "BAAI/bge-reranker-v2-m3")
try:                                     # optional module stays standalone; degrade, don't crash
    MAX_LEN = int(os.environ.get("NEVERTWICE_XRERANK_MAXLEN", "") or 512)
except ValueError:
    MAX_LEN = 512
_state = {}


def _model_cached() -> bool:
    """True when the reranker model is already in the local HuggingFace cache. The auto
    switch requires this so it can never trigger a surprise ~2 GB download: torch on the
    machine proves nothing (every ML box has torch for other reasons)."""
    # HF_HUB_CACHE is the CURRENT canonical override (huggingface_hub gives it
    # precedence); HUGGINGFACE_HUB_CACHE is its legacy alias; HF_HOME moves the
    # whole tree. Missing HF_HUB_CACHE made auto-enable never engage for users
    # who relocated the cache with the modern variable.
    hub = Path(os.environ.get("HF_HUB_CACHE")
               or os.environ.get("HUGGINGFACE_HUB_CACHE")
               or Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub")
    leaf = "models--" + MODEL.replace("/", "--")
    try:
        return (hub / leaf).is_dir()
    except OSError:
        return False


def _torch_has_gpu_build() -> bool:
    """True when the INSTALLED torch was built for a GPU, read without importing torch.

    `torch/version.py` is a handful of lines that assign `cuda = '12.8'` (or, annotated,
    `cuda: Optional[str] = '12.8'`) for a CUDA wheel and `cuda = None` for a CPU-only one, with
    `hip` saying the same for ROCm. Reading it costs a file open; importing torch to ask
    `torch.cuda.is_available()` costs a second or more and would put that on the recall path of
    every machine, including the ones this check exists to keep the reranker OFF for.

    Both fields, not only `cuda`: on an AMD build `cuda` is None and `hip` names the ROCm
    version, and a check that read `cuda` alone would answer "no GPU" on a machine with a
    working one - wrong in exactly the sense the docstring claims to be right about.

    It answers "was this build made for a GPU", not "is a GPU plugged in". That is the weaker
    question, and it is the one that separates the case this guards against - `pip install
    torch` hands out CPU wheels on many platforms - from the rest. `_load` covers the remainder
    by saying out loud when it lands on the CPU anyway.
    """
    try:
        spec = importlib.util.find_spec("torch")
        if spec is None or not spec.origin:
            return False
        version_py = Path(spec.origin).with_name("version.py")
        for line in version_py.read_text(encoding="utf-8", errors="replace").splitlines():
            #: `cuda:` catches the annotated form; `cuda ` and `cuda=` the bare one. Anchored so
            #: a future `cuda_version = ...` is not read as the field.
            head = line.strip().split("=", 1)
            if len(head) == 2 and head[0].strip().rstrip(":").split(":")[0].strip() in (
                    "cuda", "hip"):
                if head[1].strip().strip("'\"") not in ("None", ""):
                    return True
        return False
    except Exception:                    # noqa: BLE001 - a broken install must not kill recall
        return False



def enabled() -> bool:
    """Resolve the switch: an explicit NEVERTWICE_XRERANK=1/0 always wins. Unset (or
    'auto') means ON when the deps are installed, the model is already downloaded AND torch was
    built for a GPU - so one `NEVERTWICE_XRERANK=1` run fetches it, and from then on it stays on
    by itself, while a machine that merely has torch for other work is never surprised with a
    2 GB download. Any other value reads as auto. find_spec plus two file checks keep this to a
    few ms.

    The GPU condition is not a preference. Measured 2026-09-22 on this model at the shipped pool
    of 15 candidates: **36.7 ms on a GPU against 8 911 ms on the CPU**, a factor of 243, and the
    cost scales linearly with the pool (2.45-2.56 ms per passage on the GPU). Auto-enable used to
    ask only whether torch and transformers imported, on the premise that having them means the
    reranker pays for itself - and that premise is decided by a GPU the check never looked at.
    `pip install torch` hands out CPU-only wheels on many platforms, so a user who ran once with
    `NEVERTWICE_XRERANK=1` to try it got nine seconds a query, forever, silently. A threshold
    that does not look at the quantity its own premise rests on is the defect class this project
    keeps finding; this is one of them.

    Forcing it on with `NEVERTWICE_XRERANK=1` still works and is the documented way to use it on
    a CPU, or on a GPU box whose torch reports no CUDA."""
    v = os.environ.get("NEVERTWICE_XRERANK", "auto").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    try:
        return bool(importlib.util.find_spec("torch")
                    and importlib.util.find_spec("transformers")
                    and _torch_has_gpu_build()
                    and _model_cached())
    except Exception:                    # a broken package on sys.path must not kill recall
        return False


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
    if not use_cuda:
        # `enabled()` keeps auto off for a CPU-only torch build, but a CUDA build on a box with
        # no visible GPU reaches here, and so does an explicit NEVERTWICE_XRERANK=1. Measured
        # 2026-09-22 at the shipped pool of 15: 8 911 ms against 36.7 ms on a GPU. Nine seconds
        # a query is a decision someone should be making on purpose, so it is said out loud
        # rather than absorbed.
        sys.stderr.write(
            "[nevertwice] cross-encoder reranking is running on the CPU: measured ~8.9 s per "
            "query at the default pool against ~37 ms on a GPU. Set NEVERTWICE_XRERANK=0 to "
            "turn it off.\n")
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
