#!/usr/bin/env python3
"""PREREG-V3 T34, M35 (the auditor's Q-BM25-MOCK = a, 2026-09-30): research/v3/bm25_cache.py - the per-unit offline
cache of fastembed's Qdrant/bm25 for the mem0 and mem0-store arms.

* the markers are computed, never written by hand: fastembed 0.8.1's language files and its model_file, minus the
  pinned files (today mock.file and tamil.txt), each 0 bytes; a marker that would land on a pinned file is refused;
* the placement: every pinned file read from the hub copy, checked (size, git blob sha1, the filled sha256), written
  once ("xb") and re-read; refs/main exactly the 40 hex of the commit, no newline; a source that is missing, foreign or
  a link, a cache directory that exists, a refs file with anything else - each refused by name, before any spawn;
* the real fastembed (when this interpreter has 0.8.1) loads Qdrant/bm25 offline from the copy - else skipped by name.

No child process: every row runs in this process on files under a temporary directory.

    python tests/research/_test_v3_bm25_cache.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def _try(fn):
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


try:
    _spec = importlib.util.spec_from_file_location("v3_bm25_cache", ROOT / "research" / "v3" / "bm25_cache.py")
    BC = importlib.util.module_from_spec(_spec)
    sys.modules["v3_bm25_cache"] = BC
    _spec.loader.exec_module(BC)
    load_err = None
except Exception as e:  # noqa: BLE001
    BC, load_err = None, f"{type(e).__name__}: {e}"
check("the cache module loads", BC is not None, str(load_err))


def refused(fn, code: str, *words: str) -> bool:
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        return BC is not None and isinstance(e, BC.CacheRefused) and e.code == code and all(w in str(e) for w in words)
    return False


COMMIT = "22b8d2af71a76161e18dd432d2cee0eefa66e412"
LANGS = ["arabic", "danish", "dutch", "english", "finnish", "french", "german", "greek", "hungarian", "italian",
         "norwegian", "portuguese", "romanian", "russian", "spanish", "swedish", "tamil", "turkish"]


def blob(d: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(d) + d).hexdigest()


def fake_hub(base: Path, files: dict[str, bytes]) -> Path:
    """<base>/hub/models--Qdrant--bm25/snapshots/<commit>/<files> - the placement's shape."""
    hub = base / "hub"
    snap = hub / "models--Qdrant--bm25" / "snapshots" / COMMIT
    for p, d in files.items():
        (snap / p).parent.mkdir(parents=True, exist_ok=True)
        (snap / p).write_bytes(d)
    return hub


TMP = Path(tempfile.mkdtemp(prefix="v3_bm25_cache_"))
try:
    #: synthetic files for the real names: english.txt and stopwords.txt the same bytes (one blob, as upstream)
    names = list(getattr(BC, "BM25_BLOBS", {}) or {})
    FILES = {p: (f"{p} stopword\nand\nthe\n" if p != "stopwords.txt" else "english.txt stopword\nand\nthe\n").encode()
             for p in names}
    if "stopwords.txt" in FILES:
        FILES["stopwords.txt"] = FILES["english.txt"]
    BLOBS = {p: (blob(d), len(d)) for p, d in FILES.items()}
    SHAS = {p: hashlib.sha256(d).hexdigest() for p, d in FILES.items()}

    print("- the markers (computed) -")
    check("BC-MARKERS-1 (Q1 = a): the markers are fastembed 0.8.1's language files and its model_file minus the 33 "
          "pinned files - exactly mock.file and tamil.txt, each 0 bytes, reason Q-BM25-MOCK; the census: as many as "
          "the difference",
          BC is not None and BC.FASTEMBED_LANGUAGES == LANGS and BC.FASTEMBED_MODEL_FILE == "mock.file"
          and BC.markers() == ["mock.file", "tamil.txt"]
          and BC.markers() == sorted(({f"{x}.txt" for x in LANGS} | {"mock.file"}) - set(BC.BM25_BLOBS))
          and len(BC.BM25_BLOBS) == 33, str(getattr(BC, "markers", lambda: None)()))
    check("BC-MARKERS-2 (M6): a marker that would land on a pinned file is refused by name (BC10) - never written over it",
          refused(lambda: BC.place_unit_cache(TMP / "m6.home", hub=fake_hub(TMP / "m6", FILES), blobs=BLOBS, shas=SHAS,
                                              marker_names=["mock.file", "english.txt"]), "BC10", "english.txt"))

    print("\n- a clean placement -")
    hub = fake_hub(TMP / "ok", FILES)
    home = TMP / "unit.home"
    home.mkdir()
    rec, err = _try(lambda: BC.place_unit_cache(home, hub=hub, blobs=BLOBS, shas=SHAS))
    cache = home / "fastembed_cache"
    snap = cache / "models--Qdrant--bm25" / "snapshots" / COMMIT
    check("BC-PLACE-1: the 33 pinned files are copied into <home>/fastembed_cache/models--Qdrant--bm25/snapshots/<commit> "
          "byte for byte, each checked (size, git blob, sha256); the record says so and names the markers",
          err is None and rec["path"] == str(cache) and rec["commit"] == COMMIT and rec["files"] == 33
          and rec["checked"] == {"size": 33, "git_blob_sha1": 33, "sha256": 33}
          and all((snap / p).read_bytes() == d for p, d in FILES.items())
          and rec["markers"] == [{"name": "mock.file", "bytes": 0, "reason": "Q-BM25-MOCK"},
                                    {"name": "tamil.txt", "bytes": 0, "reason": "Q-BM25-MOCK"}], str(err or rec))
    check("BC-MARKERS-3 (M1): the placed snapshot holds each marker, 0 bytes - what fastembed 0.8.1's offline check "
          "needs (model_management.py:481) and the repository does not hold",
          err is None and all((snap / m).is_file() and (snap / m).stat().st_size == 0 for m in ("mock.file", "tamil.txt")),
          str(sorted(p.name for p in snap.iterdir()) if snap.is_dir() else None))
    refs = cache / "models--Qdrant--bm25" / "refs" / "main"
    check("BC-REFS-1: refs/main is exactly the 40 hex of the commit - no newline (huggingface_hub reads it with read())",
          err is None and refs.read_bytes() == COMMIT.encode("ascii"), repr(refs.read_bytes() if refs.is_file() else None))
    check("BC-REFS-2 (M3): a refs/main with a newline after the commit is a problem by name (BC7) - never stripped",
          err is None and (refs.write_bytes(COMMIT.encode() + b"\n") or True)
          and any(p_.startswith("BC7") for p_ in BC.verify_unit_cache(cache)), str(BC.verify_unit_cache(cache) if BC else None))

    print("\n- refusals before any spawn -")
    bad = dict(FILES)
    bad["english.txt"] = b"X" * len(FILES.get("english.txt", b"x"))   # same size, foreign bytes
    h_bad = fake_hub(TMP / "foreign", bad)
    (TMP / "foreign.home").mkdir()
    check("BC-FOREIGN-1 (M4): a source file with foreign bytes of the right size is refused by its git blob (BC3), "
          "naming the file",
          refused(lambda: BC.place_unit_cache(TMP / "foreign.home", hub=h_bad, blobs=BLOBS, shas=SHAS), "BC3", "english.txt"))
    shas_bad = dict(SHAS, **{"arabic.txt": "0" * 64})
    (TMP / "sha.home").mkdir()
    check("BC-FOREIGN-2: a file whose sha256 is not the filled pin's is refused by name (BC4)",
          refused(lambda: BC.place_unit_cache(TMP / "sha.home", hub=fake_hub(TMP / "sha", FILES), blobs=BLOBS,
                                              shas=shas_bad), "BC4", "arabic.txt"))
    miss = {p: d for p, d in FILES.items() if p != "README.md"}
    (TMP / "miss.home").mkdir()
    check("BC-SOURCE-1: a pinned file missing from the hub copy is refused by name (BC1) - the window must have run",
          refused(lambda: BC.place_unit_cache(TMP / "miss.home", hub=fake_hub(TMP / "miss", miss), blobs=BLOBS,
                                              shas=SHAS), "BC1", "README.md"))
    check("BC-EXISTS-1: a unit whose fastembed_cache already exists is refused (BC6) - one checked copy per spawn",
          refused(lambda: BC.place_unit_cache(home, hub=hub, blobs=BLOBS, shas=SHAS), "BC6"))

    print("\n- the pins (Q3 = a) -")
    try:
        _cs = importlib.util.spec_from_file_location("v3_corpus_pin_bc", ROOT / "research" / "v3" / "corpus_pin_v3.py")
        CP = importlib.util.module_from_spec(_cs)
        _cs.loader.exec_module(CP)
        bm_pins = {p["path"]: p for p in CP.PINS_A7.values() if p.get("window") == "a7-hf-bm25"}
        cp_err = None
    except Exception as e:  # noqa: BLE001
        CP, bm_pins, cp_err = None, {}, f"{type(e).__name__}: {e}"
    check("BC-PINS-1 (Q3 = a): the cache's commit is the pins' revision (REV_A7 hf_bm25), its files are exactly the 33 "
          "pins of a7-hf-bm25, each blob's size the filled pin's bytes, and every pin filled (a 64-hex sha256)",
          cp_err is None and BC is not None and BC.COMMIT == CP.REV_A7["hf_bm25"] and set(bm_pins) == set(BC.BM25_BLOBS)
          and all(BC.BM25_BLOBS[p][1] == pin["bytes"] and isinstance(pin["sha256"], str) and len(pin["sha256"]) == 64
                  for p, pin in bm_pins.items()), str(cp_err or sorted(set(bm_pins) ^ set(getattr(BC, "BM25_BLOBS", {})))))

    print("\n- the real fastembed, offline, from the copy (skipped by name without fastembed 0.8.1) -")
    try:
        import fastembed  # noqa: F401,PLC0415
        from fastembed import SparseTextEmbedding  # noqa: PLC0415
        from fastembed.sparse import bm25 as FB  # noqa: PLC0415
        have = getattr(fastembed, "__version__", "") == "0.8.1"
    except Exception:  # noqa: BLE001
        have = False
    if not have:
        print("  skip BC-LOAD-1: this interpreter has no fastembed 0.8.1 (the binding check is probe A8 mem0's)")
    else:
        h2 = TMP / "load.home"
        h2.mkdir()
        rec2, err2 = _try(lambda: BC.place_unit_cache(h2, hub=hub, blobs=BLOBS, shas=SHAS))
        saved = {k: os.environ.get(k) for k in ("HF_HUB_OFFLINE", "FASTEMBED_CACHE_PATH")}
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["FASTEMBED_CACHE_PATH"] = str(h2 / "fastembed_cache")
        try:
            enc, e_err = _try(lambda: SparseTextEmbedding(model_name="Qdrant/bm25"))
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        md = getattr(getattr(enc, "model", None), "_model_dir", None)
        check("BC-LOAD-1: fastembed 0.8.1 loads Qdrant/bm25 offline from the unit's copy (model dir = the copy's "
              "snapshot) - and its language list and model_file are the ones the markers come from",
              err2 is None and e_err is None and md is not None
              and Path(md).resolve() == (h2 / "fastembed_cache" / "models--Qdrant--bm25" / "snapshots" / COMMIT).resolve()
              and list(FB.supported_languages) == LANGS and FB.supported_bm25_models[0].model_file == "mock.file",
              str(err2 or e_err or md))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 bm25 cache: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
