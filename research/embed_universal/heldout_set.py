#!/usr/bin/env python3
"""M0 - freeze the EXTERNAL held-out evaluation set, and make it checkable from a fresh clone.

The shipped headline for `nevertwice-embed` (recall 0.220 -> 0.475 at 1% FPR) was measured on
the owner's private vault. Nobody else can reproduce it, so nobody else can check it. This
builds the benchmark that replaces it: three axes over material that is entirely external, with
every record carrying its provenance, frozen into one committed artifact with a content hash.

**Nothing here comes from the owner's store.** Every record descends from a held-out public
domain - the Rust book, arXiv quant-ph abstracts, and one Project Gutenberg title - none of
which was trained on. `--verify` asserts that on the frozen file, so the property survives
whoever edits the pipeline next.

Three axes, because one number cannot answer the product's question:

* **twin** - is this the same lesson said twice? The pair task the store's dedup gate runs.
* **retrieval_title** - the title as query. Near ceiling for every model measured so far, and
  kept precisely so the table shows where a benchmark stops discriminating.
* **retrieval_situation** - the *prevention* sentence as query. Forward-looking and lexically
  unlike the note it must find, which is the shape the product actually faces: a situation
  heading towards a failure, not a title someone already knows.

    python research/embed_universal/heldout_set.py --build    # needs data/, rebuilds the frozen set
    python research/embed_universal/heldout_set.py --verify   # needs nothing but the repository

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FROZEN = HERE / "heldout" / "external_heldout_v1.json"
MANIFEST = HERE / "heldout" / "MANIFEST.json"

#: Domains never trained on. gen_corpus.py enforces the same split when it writes the lessons;
#: repeating it here means the frozen file can be checked without trusting that.
HELDOUT_DOMAINS = {"rust-book", "arxiv:quant-ph", "book:132"}

#: Files whose content defines how the lessons were written. Hashed into the manifest so a
#: changed prompt cannot masquerade as the same corpus.
PROVENANCE = ("gen_corpus.py", "gen_pairs.py", "sources.py")

SCHEMA = 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _document(lesson: dict) -> str:
    """A lesson as the store would index it: title, then what happened, then the guard."""
    parts = [lesson.get("title", ""), lesson.get("desc", "")]
    return "\n".join(p for p in parts if p).strip()


def build() -> dict:
    lessons = [r for r in _read_jsonl(DATA / "lessons.jsonl") if r.get("heldout")]
    unknown = {r["domain"] for r in lessons} - HELDOUT_DOMAINS
    if unknown:
        raise SystemExit(f"lessons.jsonl carries held-out domains this file does not know: {unknown}")

    # Deterministic order: the corpus index is part of the answer key, so it may not depend on
    # dict iteration or on the order a generator happened to write its file.
    lessons.sort(key=lambda r: (r["domain"], r.get("chunk_id", 0), r.get("title", "")))

    corpus, by_text = [], {}
    for i, lesson in enumerate(lessons):
        text = _document(lesson)
        corpus.append({"doc_id": i, "text": text, "domain": lesson["domain"],
                       "lang": lesson.get("lang", ""), "type": lesson.get("type", "")})
        by_text.setdefault(text, i)

    # -- retrieval: two query shapes over one corpus ------------------------
    title_q, situation_q = [], []
    for i, lesson in enumerate(lessons):
        title = (lesson.get("title") or "").strip()
        prevention = (lesson.get("prevention") or "").strip()
        if title:
            title_q.append({"qid": len(title_q), "query": title, "gold": i,
                            "domain": lesson["domain"], "lang": lesson.get("lang", "")})
        # A prevention that merely restates the title teaches nothing about a harder shape.
        if prevention and prevention.lower() != title.lower() and len(prevention) > 20:
            situation_q.append({"qid": len(situation_q), "query": prevention, "gold": i,
                                "domain": lesson["domain"], "lang": lesson.get("lang", "")})

    # -- twin: the frozen pair set, re-attributed to its domain --------------
    twin = []
    for row in _read_jsonl(DATA / "test_synth.jsonl"):
        if row.get("domain") not in HELDOUT_DOMAINS:
            raise SystemExit(f"test_synth.jsonl carries a non-held-out domain: {row.get('domain')}")
        twin.append({"a": row["a"], "b": row["b"], "label": int(row["label"]),
                     "domain": row["domain"]})
    twin.sort(key=lambda r: (r["domain"], r["label"], r["a"], r["b"]))
    for i, row in enumerate(twin):
        row["pair_id"] = i

    payload = {
        "schema": SCHEMA,
        "name": "nevertwice external held-out v1",
        "purpose": (
            "An evaluation set for the memory-note embedding that contains NO material from the "
            "owner's private store. It exists because the shipped headline was measured on that "
            "store and is therefore unreproducible by anyone else."
        ),
        "heldout_domains": sorted(HELDOUT_DOMAINS),
        "corpus": corpus,
        "axes": {
            "twin": twin,
            "retrieval_title": title_q,
            "retrieval_situation": situation_q,
        },
    }
    return payload


def summarise(payload: dict) -> dict:
    corpus = payload["corpus"]
    twin = payload["axes"]["twin"]
    counts = {
        "corpus": len(corpus),
        "twin_pairs": len(twin),
        "twin_positive": sum(1 for r in twin if r["label"] == 1),
        "twin_negative": sum(1 for r in twin if r["label"] == 0),
        "retrieval_title": len(payload["axes"]["retrieval_title"]),
        "retrieval_situation": len(payload["axes"]["retrieval_situation"]),
    }
    languages: dict[str, int] = {}
    domains: dict[str, int] = {}
    for doc in corpus:
        languages[doc["lang"] or "?"] = languages.get(doc["lang"] or "?", 0) + 1
        domains[doc["domain"]] = domains.get(doc["domain"], 0) + 1
    return {"counts": counts, "corpus_languages": languages, "corpus_domains": domains}


def freeze(payload: dict) -> dict:
    FROZEN.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    # write_bytes, not write_text: on Windows the text path rewrites every newline as
    # CRLF, so the file on disk would not be the bytes that were hashed - and the hash is
    # the entire point of freezing it.
    FROZEN.write_bytes(body.encode("utf-8"))
    provenance = {}
    for name in PROVENANCE:
        path = HERE / name
        provenance[name] = _sha256(path.read_bytes()) if path.is_file() else None
    manifest = {
        "schema": SCHEMA,
        "file": FROZEN.relative_to(HERE.parent.parent).as_posix(),
        "sha256": _sha256(body.encode("utf-8")),
        "bytes": len(body.encode("utf-8")),
        "heldout_domains": payload["heldout_domains"],
        "generator_sha256": provenance,
        "built_by": "research/embed_universal/heldout_set.py --build",
        "verify_with": "python research/embed_universal/heldout_set.py --verify",
        "regeneration": (
            "The frozen file is deterministic given data/lessons.jsonl and data/test_synth.jsonl. "
            "Those two are NOT reproducible from a fresh clone: they need ~2 GB of fetched public "
            "source material (sources.py) and a local qwen2.5:7b writing the notes (gen_corpus.py), "
            "and an LLM's output is not byte-stable across setups. So the SET is frozen and "
            "committed, its hash is checked in CI, and the pipeline that produced it is committed "
            "beside it. That is the honest reading of 'reproducible from a fresh clone' for an "
            "LLM-written corpus: anyone can CHECK it, and anyone can rebuild an equivalent one."
        ),
        **summarise(payload),
    }
    MANIFEST.write_bytes(
        (json.dumps(manifest, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    return manifest


def verify() -> int:
    if not FROZEN.is_file() or not MANIFEST.is_file():
        print("FAIL: the frozen set or its manifest is missing")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    body = FROZEN.read_bytes()
    problems = []

    if _sha256(body) != manifest["sha256"]:
        problems.append("the frozen file does not match the hash in its manifest")
    payload = json.loads(body.decode("utf-8"))

    domains = set(payload["heldout_domains"])
    if domains != HELDOUT_DOMAINS:
        problems.append(f"held-out domains drifted: {domains} vs {HELDOUT_DOMAINS}")
    for doc in payload["corpus"]:
        if doc["domain"] not in domains:
            problems.append(f"corpus doc {doc['doc_id']} is from {doc['domain']}, not held out")
            break
    for row in payload["axes"]["twin"]:
        if row["domain"] not in domains:
            problems.append(f"twin pair {row['pair_id']} is from {row['domain']}, not held out")
            break

    size = len(payload["corpus"])
    for axis in ("retrieval_title", "retrieval_situation"):
        for q in payload["axes"][axis]:
            if not 0 <= q["gold"] < size:
                problems.append(f"{axis} query {q['qid']} points outside the corpus")
                break
    counts = summarise(payload)["counts"]
    if counts != manifest["counts"]:
        problems.append(f"counts drifted: {counts} vs {manifest['counts']}")

    for name, digest in manifest["generator_sha256"].items():
        path = HERE / name
        if digest is not None and path.is_file() and _sha256(path.read_bytes()) != digest:
            problems.append(f"{name} changed since the set was frozen - rebuild or restamp")

    for problem in problems:
        print(f"  FAIL {problem}")
    if problems:
        return 1
    print(f"  ok   frozen set verified: {counts}")
    print(f"  ok   every record is from a held-out external domain: {sorted(domains)}")
    print(f"  ok   sha256 {manifest['sha256'][:16]}...")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", action="store_true", help="rebuild the frozen set from data/")
    parser.add_argument("--verify", action="store_true", help="check the committed frozen set")
    args = parser.parse_args(argv)
    if args.build:
        manifest = freeze(build())
        print(json.dumps({k: v for k, v in manifest.items()
                          if k in ("sha256", "bytes", "counts", "corpus_languages",
                                   "corpus_domains")}, indent=1, ensure_ascii=False))
        return 0
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
