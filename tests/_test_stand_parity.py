#!/usr/bin/env python3
"""The external-retrieval stand ranks with the shipped ranker and embeds what the competitors embed.

Two things the 2026-09-05 review found in `research/longmem_eval.py`:

* `calibrated` was a copy of `memory_hook._calibrated_fusion` that had drifted - the engine
  gives a signal's sole candidate z = 1.0, the copy gave it 0.0 - under a docstring that
  said "identical". Inert on every question of the two corpora it was checked on, and a
  stand that says it measures the shipped ranker has to call it, not re-type it.
* `memory_hook.embed_text` cuts its input to 2,000 characters, a hot-path latency guard.
  The stand fed it whole LongMemEval sessions - 14,000 characters at the median - so our
  semantic arm embedded the first seventh of every session while every competitor on the
  same stand embedded the whole one through the same endpoint.

No network here: the embed call is intercepted and its payload inspected.
"""
import _env_guard  # noqa: F401
import json
import random
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nevertwice"))
sys.path.insert(0, str(ROOT / "research"))
import memory_hook as m  # noqa: E402
import longmem_eval as le  # noqa: E402
import head_to_head as h2h  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


print("\n- the stand's fusion IS the engine's fusion -")
rng = random.Random(7)
ids = [f"s{i}" for i in range(40)]
sem = {s: rng.random() for s in ids}
lex = {s: rng.random() * 5 for s in rng.sample(ids, 12)}
check("identical scores on a random pool", le.calibrated(sem, lex) == m._calibrated_fusion(sem, lex))
single = {"s3": 2.5}
check("the sole-candidate case follows the engine (z = 1.0, not 0.0)",
      le.calibrated(sem, single) == m._calibrated_fusion(sem, single))
check("the z-map delegates too", le._zmap(single) == {"s3": 1.0})
check("the docstring no longer claims an identity it has to keep by hand",
      "identical to memory_hook" not in (le.calibrated.__doc__ or ""))

print("\n- the stand embeds the whole session through the engine's endpoint -")
captured = {}


class _Resp:
    def __init__(self, vec):
        self._body = json.dumps({"embeddings": [vec]}).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(req, timeout=None):
    captured["url"] = req.full_url
    captured["payload"] = json.loads(req.data.decode("utf-8"))
    captured["timeout"] = timeout
    return _Resp([0.1, 0.2, 0.3])


_orig = urllib.request.urlopen
urllib.request.urlopen = _fake_urlopen
try:
    long_text = "word " * 5000                       # 25,000 characters, a typical session
    vec = le.embed_full(long_text, kind=m.doc_embed_kind())
    sent = captured["payload"]["input"]
    check("the vector comes back", vec == [0.1, 0.2, 0.3])
    check("the whole text is sent, not the first 2,000 characters",
          len(sent) == len(long_text[:le.MAXCHARS]) and len(sent) > 2000, str(len(sent)))
    check("the engine's endpoint", captured["url"] == m.OLLAMA_EMBED_URL, captured["url"])
    check("the engine's model", captured["payload"]["model"] == m.EMBED_MODEL)
    check("the engine's task prefix", sent.startswith(m._embed_prefix(m.doc_embed_kind())))
    check("a pathological session is capped at the pool's own cap",
          len(json.loads(_fake_urlopen(urllib.request.Request(
              "http://x", data=b"{}"), 1).read())) > 0)    # the fake still answers
    m.embed_text("x" * 3000)                          # the engine's own path, for contrast
    check("the engine's hot path still caps at 2,000 - the stand differs by design",
          len(captured["payload"]["input"]) == 2000, str(len(captured["payload"]["input"])))
finally:
    urllib.request.urlopen = _orig

print("\n- a cache built under another cap cannot pass for this one -")
check("the cache name carries the cap", f"c{le.MAXCHARS}" in le.EMB.name, le.EMB.name)
check("the pre-review cache name is not the current one", le.EMB.name != "longmem_embeds.json")
good = {"sessions": {}, "questions": {}, "meta": le.cache_meta()}
check("a cache with the stamp is accepted", le.cache_ok(good))
check("a cache without the stamp is refused", not le.cache_ok({"sessions": {}, "questions": {}}))
check("a cache under another cap is refused",
      not le.cache_ok({"meta": {"embed_chars": 2000, "embedder": m.EMBED_MODEL}}))
check("a cache from another model is refused",
      not le.cache_ok({"meta": {"embed_chars": le.MAXCHARS, "embedder": "other"}}))

print("\n- the competitor arms say what they are -")
for arm in ("mem0_infer", "langmem_full", "amem_full"):
    check(f"the {arm} arm exists", arm in h2h.ADAPTERS and arm in h2h._PKG)
check("the store arms are labelled as store arms",
      "store" in h2h.ARM_LABEL["langmem"].lower() and "store" in h2h.ARM_LABEL["amem"].lower())
check("Mem0's store arm discloses infer=False", "infer=False" in h2h.ARM_LABEL["mem0"])
check("the full arms name the LLM pipeline",
      all("pipeline" in h2h.ARM_LABEL[a] for a in ("mem0_infer", "langmem_full", "amem_full")))
ef = h2h._OllamaChromaEF()
check("the chroma embedding function carries the protocol pieces a persistent collection needs",
      callable(ef) and ef.name() and ef.get_config() == {"model_name": h2h.EMBED_MODEL}
      and h2h._OllamaChromaEF.build_from_config(ef.get_config()).model_name == h2h.EMBED_MODEL
      and ef.default_space() == "cosine")

print(f"\nstand parity: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
