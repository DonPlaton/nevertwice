#!/usr/bin/env python3
"""Regression tests for opt-in 1-bit embedding quantization (launch round, A2).

NEVERTWICE_EMBED_QUANT=binary packs sign-bit codes in the SQLite scale-index: 16x
smaller than the float16 default, and — because the ranker cosines the float query
against the unpacked {-1,+1} doc — scoring as asymmetric binary cosine (measured
~lossless on LongMemEval; research/QUANTIZATION.md). These guard the pack/unpack
round-trip, the size win, the self-retrieval ranking property, and a full
build+search end-to-end in binary mode.

    NEVERTWICE_EMBED_QUANT=binary python _test_quant.py    (env optional; set below)
"""
import math
import os
import sys
import tempfile
from pathlib import Path

os.environ["NEVERTWICE_EMBED_QUANT"] = "binary"      # before importing the index module

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import memory_hook as m          # noqa: E402
import index_sqlite as ix        # noqa: E402

P = F = 0


def check(name, cond):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}")


print("binary-quant pack/unpack")
check("binary mode active", ix._BINARY and ix.VEC_FORMAT == "b1")

vec = [0.7, -0.2, 0.0, -1.5, 3.0, -0.01, 0.5, -0.5, 1.0, -2.0]
blob = ix._pack(vec)
back = ix._unpack(blob)
check("signs preserved", all((x >= 0) == (y > 0) for x, y in zip(vec, back)))
check("unpacked length matches", len(back) == len(vec))
check("codes are ±1", set(back) <= {1.0, -1.0})

big = [0.1 * (1 if i % 3 else -1) for i in range(1024)]
bb = ix._pack(big)
check("1024-dim packs to 130 bytes (2 hdr + 128)", len(bb) == 130)
check("1024-dim round-trips length", len(ix._unpack(bb)) == 1024)
check("16x smaller than float16 (2 B/dim)", len(bb) < 1024 * 2 / 8)
check("empty vec round-trips to []", ix._unpack(ix._pack([])) == [])

# self-retrieval property: a float query is closest to its own sign code.
# cosine(q, sign(q)) = L1(q)/(L2(q)·√d) > cosine(q, sign(other)) on average.
print("asymmetric binary cosine ranking")
q = [0.31, -0.62, 0.11, 0.44, -0.28, 0.53, -0.17, 0.39, -0.71, 0.22,
     0.08, -0.49, 0.66, -0.13, 0.27, -0.55]
self_code = ix._unpack(ix._pack(q))
anti_code = ix._unpack(ix._pack([-x for x in q]))
other = [0.5, 0.5, -0.5, -0.5, 0.5, 0.5, -0.5, -0.5, 0.5, 0.5,
         -0.5, -0.5, 0.5, 0.5, -0.5, -0.5]
other_code = ix._unpack(ix._pack(other))
s_self = m.cosine(q, self_code)
s_anti = m.cosine(q, anti_code)
s_other = m.cosine(q, other_code)
check("query closest to its own sign code", s_self > s_other > s_anti)
check("anti-aligned code is negative", s_anti < 0)

# end-to-end: build a binary index from a synthetic cache, search returns the
# semantically-closest note first.
print("end-to-end build + search (binary index)")
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    # three orthogonal-ish unit vectors so cosine ordering is unambiguous
    def unit(seed):
        import random
        rnd = random.Random(seed)
        v = [rnd.gauss(0, 1) for _ in range(64)]
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]
    va, vb, vc = unit(1), unit(2), unit(3)
    cache = {
        "note-a": {"vec": va, "ntype": "pattern", "project": "p", "title": "alpha",
                   "desc": "", "prevention": "", "recurrence": 1},
        "note-b": {"vec": vb, "ntype": "pattern", "project": "p", "title": "beta",
                   "desc": "", "prevention": "", "recurrence": 1},
        "note-c": {"vec": vc, "ntype": "pattern", "project": "p", "title": "gamma",
                   "desc": "", "prevention": "", "recurrence": 1},
    }
    m.load_embed_cache = lambda: cache            # build() reads the cache from here
    m.load_embed_meta = lambda: {"model": "test", "prefixed": False}
    m.embed_signature = lambda: "test"
    n = ix.build()
    check("index built all 3 notes", n == 3)
    meta = ix.index_meta()
    check("index stamped binary vec_format", meta.get("vec_format") == "b1")
    # candidates come back with ±1 vecs; the ranker cosines the float query
    cands = ix.iter_candidates("p")
    check("3 candidates unpacked", len(cands) == 3)
    rec = dict(cands)
    # query near va → note-a must score highest under asymmetric cosine
    scored = sorted(((m.cosine(va, r["vec"]), s) for s, r in cands), reverse=True)
    check("query≈va ranks note-a first", scored[0][1] == "note-a")
    # every candidate vec is a binary code
    check("candidate vecs are ±1 codes",
          all(set(r["vec"]) <= {1.0, -1.0} for _, r in cands))

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
