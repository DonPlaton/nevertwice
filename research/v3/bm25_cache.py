#!/usr/bin/env python3
"""PREREG-V3 T34, M35 (the auditor's Q-BM25-MOCK = a, 2026-09-30): the per-unit offline cache of fastembed's Qdrant/bm25
for the mem0 and mem0-store arms - mem0 2.2.0 builds SparseTextEmbedding(model_name="Qdrant/bm25") lazily
(qdrant.py:92-107), and fastembed 0.8.1 reads FASTEMBED_CACHE_PATH (common/utils.py:58-69), honours HF_HUB_OFFLINE
(common/model_management.py:454-458) and accepts a cached snapshot only if its model_file and every language file exist
(:466-467, :481-484).

The placement, harness-side, before each spawn of a mem0 / mem0-store child (one checked copy per spawn, never reused):
<home>/fastembed_cache/models--Qdrant--bm25/refs/main = exactly the 40 hex of COMMIT, no newline (huggingface_hub
reads it with read(), _snapshot_download.py:317-320); snapshots/<COMMIT>/ = the 33 pinned files of window a7-hf-bm25,
each read from the hub copy that window placed and checked - size, git blob sha1 (BM25_BLOBS, the listing of
a7-discovery-2 d1), the filled sha256 of its pin - written once ("xb") and re-read; plus the MARKERS, 0 bytes each.

The markers are computed, never written by hand (the auditor's Q1 = a): fastembed 0.8.1's language files and its
model_file (bm25.py:25-57: supported_languages, model_file "mock.file"), minus the pinned files - today mock.file and
tamil.txt, which the model repository does not hold. The online path returns the snapshot without that check
(model_management.py:494-501); fastembed never reads their content (model_file only in :466 and :481; Bm25 opens only
<language>.txt, bm25.py:134, :150-155) - the empty markers reproduce the online result. A marker that would land on a
pinned file is refused.

Refusals (CacheRefused, code and file): BC1 a source missing or a link; BC2 its size; BC3 its git blob; BC4 its
sha256 (a filled pin); BC5 the copy differs when re-read; BC6 the cache directory exists (or the home is a link); BC7
refs/main not exactly the commit; BC8 a marker not 0 bytes; BC9 a path of 240 characters or more; BC10 a marker on a
pinned file.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ID = "Qdrant/bm25"
#: = corpus_pin_v3.REV_A7["hf_bm25"] (a row holds them equal)
COMMIT = "22b8d2af71a76161e18dd432d2cee0eefa66e412"
CACHE_DIR = "fastembed_cache"
MARKER_REASON = "Q-BM25-MOCK"
MAX_PATH = 240
#: fastembed 0.8.1, fastembed/sparse/bm25.py:25-57 (read as data in the installed mem0_v3; the real-fastembed row
#: compares them when its interpreter has 0.8.1)
FASTEMBED_LANGUAGES = ["arabic", "danish", "dutch", "english", "finnish", "french", "german", "greek", "hungarian",
                       "italian", "norwegian", "portuguese", "romanian", "russian", "spanish", "swedish", "tamil",
                       "turkish"]
FASTEMBED_MODEL_FILE = "mock.file"
#: path -> (git blob sha1, size): Qdrant/bm25's 33 files at COMMIT, the listing of a7-discovery-2 d1 (the auditor's
#: Q3 = a; a row holds it equal to the pins' expectation). english.txt and stopwords.txt share one blob - keyed by path.
BM25_BLOBS: dict[str, tuple[str, int]] = {
    ".gitattributes": ("a6344aac8c09253b3b630fb776ae94478aa0275b", 1519),
    "README.md": ("88423dc762fcd807f1a4e0e408bc5891e969294f", 1462),
    "arabic.txt": ("ee3a26e793f8ab69a44b30332708cc122b258b51", 6348),
    "azerbaijani.txt": ("27bf2940fe608d8a0698a7b896cd1fd22ad64ac9", 967),
    "basque.txt": ("3b84c32419b346f0606212affb50f08f3d226194", 2202),
    "bengali.txt": ("9dc1bfcb2a87c09952a3e61c4c365612c4adccd2", 5443),
    "catalan.txt": ("cdba332cb94448bf8b93d41e5d58e2b6f447211f", 1558),
    "chinese.txt": ("0873a9033a24948ae253085187c882418b5b3aaa", 5560),
    "config.json": ("9e26dfeeb6e641a33dae4961196235bdb965b21b", 2),
    "danish.txt": ("d3edc6757912e3d83acfa241859c4a1bdb6005ad", 424),
    "dutch.txt": ("cafa0324b53763f7efadda5b0f3d0321ffa7ab38", 453),
    "english.txt": ("1280aa3b08d64b846272b2d23314377200db36e9", 936),
    "finnish.txt": ("47ee200f6781ddfdb31af37e317e1f05d10a04ff", 1579),
    "french.txt": ("00af58791541ed63f33c98207199f6b32727f571", 813),
    "german.txt": ("c2241d0ee7bbc192abe3b56efffb75f29a29e185", 1362),
    "greek.txt": ("9d08b144659c4ae804bb84a624e1248bb2b90e40", 2167),
    "hebrew.txt": ("8ac778585f3d9b71c5a6d1e4fc9afb83eb4e691a", 1836),
    "hinglish.txt": ("de0654e211e01952c3f38c78d370739e7c71d2e2", 5958),
    "hungarian.txt": ("94e9f9a0b07a68aeff30605fc445db71f63be630", 1227),
    "indonesian.txt": ("bf88a458130df5ab852fb1656a8157a00ec355e9", 6446),
    "italian.txt": ("6ee02b51fb1711625c1de28595615cdecba4de7c", 1654),
    "kazakh.txt": ("ebb9fc1bdc99fa06919ad03c1f5ac6a44695e111", 3880),
    "nepali.txt": ("b2e4d34709f5573aa7a19009dc8732db43f9fd6f", 3610),
    "norwegian.txt": ("9ac1abbb6cba1fb3159caa6d88b25543b9080031", 851),
    "portuguese.txt": ("eb53a8fc2544b16d2a849d98e26c6e34bf07bdb0", 1286),
    "romanian.txt": ("45651c90ec7ea5b26c51ea9a11fddafb00652190", 1910),
    "russian.txt": ("ecb83d4a7f393b67b054b44e337d683b2ff2d973", 1235),
    "slovene.txt": ("eb4d1bcf28f31cb48ffa1e1d7b8fddc60f0fbd32", 15980),
    "spanish.txt": ("6a7d50cc633609c8bc2da53c3dac9a015c248b62", 2176),
    "stopwords.txt": ("1280aa3b08d64b846272b2d23314377200db36e9", 936),
    "swedish.txt": ("742bb6263b99fb2ea6e1e3624a65eb2799a1e24c", 559),
    "tajik.txt": ("898614a0210a2f4cd74deed295978c04a19bbf37", 1818),
    "turkish.txt": ("5a48ccce0737bb38cb99cafa1f8d3d0ce34e7488", 260),
}


class CacheRefused(Exception):
    """A placement this module's rules do not allow - ``code`` (BC1..BC10) and ``name`` (the file) named; the child
    is never spawned."""

    def __init__(self, code: str, name: str | None, msg: str) -> None:
        super().__init__(f"{code}: {msg}")
        self.code, self.name = code, name


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def markers(pinned=None) -> list[str]:
    """The marker files: fastembed's language files and its model_file, minus the pinned files (Q1 = a)."""
    pinned = set(BM25_BLOBS if pinned is None else pinned)
    return sorted(({f"{lang}.txt" for lang in FASTEMBED_LANGUAGES} | {FASTEMBED_MODEL_FILE}) - pinned)


def _repo_dir(cache: Path) -> Path:
    return Path(cache) / f"models--{REPO_ID.replace('/', '--')}"


def verify_unit_cache(cache: Path, *, blobs: dict | None = None, marker_names=None) -> list[str]:
    """Problems of a placed cache: refs/main (BC7), each pinned file's size and git blob (BC5), each marker's 0 bytes
    (BC8)."""
    blobs = BM25_BLOBS if blobs is None else blobs
    marks = markers(blobs) if marker_names is None else list(marker_names)
    repo, problems = _repo_dir(cache), []
    refs = repo / "refs" / "main"
    got = refs.read_bytes() if refs.is_file() else None
    if got != COMMIT.encode("ascii"):
        problems.append(f"BC7: refs/main is not exactly the 40 hex of {COMMIT} ({len(got) if got is not None else None} "
                        "bytes) - huggingface_hub would look for another snapshot")
    snap = repo / "snapshots" / COMMIT
    for p, (want_blob, want_size) in sorted(blobs.items()):
        f = snap / p
        d = f.read_bytes() if f.is_file() and not f.is_symlink() else None
        if d is None or len(d) != want_size or _git_blob(d) != want_blob:
            problems.append(f"BC5: {p} in the copy is not its pinned blob and size")
    for m in marks:
        f = snap / m
        if not f.is_file() or f.stat().st_size != 0:
            problems.append(f"BC8: the marker {m} is not a 0-byte file")
    return problems


def place_unit_cache(home: Path, *, hub: Path, shas: dict, blobs: dict | None = None, marker_names=None) -> dict:
    """Place the unit's cache under ``home`` from the hub copy (see the module docstring); ``shas`` is {path: the
    filled sha256 of its pin, or None}. Returns the record the spawn carries; CacheRefused names what it refuses."""
    blobs = BM25_BLOBS if blobs is None else blobs
    marks = markers(blobs) if marker_names is None else list(marker_names)
    over = sorted(set(marks) & set(blobs))
    if over:
        raise CacheRefused("BC10", over[0], f"the marker(s) {over} would land on pinned files - never written over them")
    home = Path(home)
    cache = home / CACHE_DIR
    if home.is_symlink() or cache.exists() or cache.is_symlink():
        raise CacheRefused("BC6", None, f"{cache} exists (or the home is a link) - one checked copy per spawn")
    src = _repo_dir(Path(hub)) / "snapshots" / COMMIT
    datas, n_sha = {}, 0
    for p, (want_blob, want_size) in sorted(blobs.items()):
        f = src / p
        if f.is_symlink() or not f.is_file():
            raise CacheRefused("BC1", p, f"{p}: not a file in the hub copy {src} (a link, or missing - has a7-hf-bm25 run?)")
        d = f.read_bytes()
        if len(d) != want_size:
            raise CacheRefused("BC2", p, f"{p}: {len(d)} bytes, the pin says {want_size}")
        if _git_blob(d) != want_blob:
            raise CacheRefused("BC3", p, f"{p}: its git blob is not the pinned {want_blob}")
        sha = shas.get(p)
        if sha is not None:
            if hashlib.sha256(d).hexdigest() != sha:
                raise CacheRefused("BC4", p, f"{p}: its sha256 is not the filled pin's")
            n_sha += 1
        datas[p] = d
    snap = _repo_dir(cache) / "snapshots" / COMMIT
    longest = max([*blobs, *marks], key=lambda q: len(str(snap / q)))
    if len(str(snap / longest)) >= MAX_PATH:
        raise CacheRefused("BC9", longest, f"{snap / longest} is {len(str(snap / longest))} characters (>= {MAX_PATH})")
    snap.mkdir(parents=True)
    for p, d in datas.items():
        with open(snap / p, "xb") as out:
            out.write(d)
        if (snap / p).read_bytes() != d:
            raise CacheRefused("BC5", p, f"{p}: the copy differs from its source when re-read")
    for m in marks:
        with open(snap / m, "xb"):
            pass
    (_repo_dir(cache) / "refs").mkdir()
    with open(_repo_dir(cache) / "refs" / "main", "xb") as out:
        out.write(COMMIT.encode("ascii"))
    problems = verify_unit_cache(cache, blobs=blobs, marker_names=marks)
    if problems:
        raise CacheRefused(problems[0][:4].rstrip(":"), None, "; ".join(problems))
    return {"path": str(cache), "commit": COMMIT, "files": len(datas),
            "checked": {"size": len(datas), "git_blob_sha1": len(datas), "sha256": n_sha}, "refs_main_bytes": 40,
            "markers": [{"name": m, "bytes": 0, "reason": MARKER_REASON} for m in marks]}
