"""External corpora, pinned by content hash, so a result can never again be withdrawn for
not knowing what it ran on.

Sixteen retrieval figures were retracted in 2026-08 for one reason: the dataset behind them was
third-party, uncommitted, and **unhashed**. Not one of those three on its own was fatal. Being
third-party is normal and is the point of an external benchmark. Being uncommitted is a size
decision. Being unhashed is what made the run unpinnable: nobody, including us, could say which
revision of the file produced the number, so the number described nothing.

This module fixes the third. The corpus stays out of git; its **hash comes in**, committed here
in source, together with where the bytes come from and what licence lets you fetch them. A
harness calls `verify()` before it reads a byte and stamps `record()` into its result file, so
every future figure carries the fingerprint of the exact file it was computed on.

    python research/corpus_pin.py --list
    python research/corpus_pin.py --verify longmemeval_oracle
    python research/corpus_pin.py --fetch longmemeval_s       # download, then verify

A pin is a fact about a file, not a preference: `verify()` raises rather than warns, because a
warning about a corpus mismatch is a warning nobody reads until the numbers are already public.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

#: The hash is recorded from the file this project actually measured against. Changing a pin
#: means the numbers measured under the old one describe a different corpus, so a pin change and
#: a re-measurement travel together or neither happens.
CORPORA = {
    "longmemeval_oracle": {
        "path": "research/data/longmemeval_oracle.json",
        "sha256": "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c",
        "bytes": 15388478,
        "url": "https://huggingface.co/datasets/xiaowu0162/longmemeval/resolve/main/"
               "longmemeval_oracle",
        "licence": "MIT",
        "citation": "Wu et al., LongMemEval: Benchmarking Chat Assistants on Long-Term "
                    "Interactive Memory, ICLR 2025",
        "questions": 500,
        "pool_sessions": 940,
        "note": "The oracle variant ships only each question's evidence sessions plus a few "
                "distractors. Pooled globally - every question retrieving from all 940 unique "
                "sessions - it is a real retrieval task, but a small-haystack one.",
    },
    "longmemeval_s": {
        "path": "research/data/longmemeval_s.json",
        "sha256": "08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894",
        "bytes": 278025796,
        "url": "https://huggingface.co/datasets/xiaowu0162/longmemeval/resolve/main/"
               "longmemeval_s",
        "licence": "MIT",
        "citation": "Wu et al., LongMemEval: Benchmarking Chat Assistants on Long-Term "
                    "Interactive Memory, ICLR 2025",
        "questions": 500,
        "pool_sessions": 19829,
        "note": "The standard non-oracle variant: about 54 haystack sessions per question, "
                "19,829 unique across the set. Twenty-one times the oracle pool, and the "
                "setting the roadmap named when it said 'outside the oracle setting'.",
    },
}


class CorpusMismatch(RuntimeError):
    """The file on disk is not the file the pinned numbers were measured on."""


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def path_of(name: str) -> Path:
    return HERE.parent / CORPORA[name]["path"]


def verify(name: str, *, required: bool = True) -> dict:
    """Check the local file against its pin and return the record.

    `required=False` returns a record with `present: False` instead of raising, for a harness
    that wants to report a missing corpus as a blocker rather than crash.
    """
    if name not in CORPORA:
        raise KeyError(f"unknown corpus {name!r}; have {', '.join(sorted(CORPORA))}")
    spec = dict(CORPORA[name], name=name)
    p = path_of(name)
    if not p.exists():
        if required:
            raise CorpusMismatch(
                f"{spec['path']} is absent. It is third-party and not committed; fetch it with "
                f"`python research/corpus_pin.py --fetch {name}` "
                f"({spec['bytes']:,} bytes, {spec['licence']}, {spec['url']})")
        return {**spec, "present": False, "actual_sha256": None}
    actual = _sha256(p)
    if actual != spec["sha256"]:
        raise CorpusMismatch(
            f"{spec['path']} does not match its pin.\n"
            f"  pinned  {spec['sha256']}\n  on disk {actual}\n"
            f"Every published figure from this corpus was measured on the pinned bytes. Either "
            f"restore that file, or change the pin AND re-measure everything that cites it - "
            f"one without the other publishes a number about a corpus nobody has.")
    return {**spec, "present": True, "actual_sha256": actual}


def record(name: str) -> dict:
    """The provenance block a result file should carry: what, where from, and which bytes."""
    spec = verify(name)
    return {"corpus": name, "sha256": spec["sha256"], "bytes": spec["bytes"],
            "questions": spec["questions"], "pool_sessions": spec["pool_sessions"],
            "url": spec["url"], "licence": spec["licence"], "citation": spec["citation"]}


def fetch(name: str) -> Path:
    """Download the corpus if absent, then verify. Never overwrites a file that already verifies."""
    import urllib.request                                       # noqa: PLC0415 - only this path

    spec = CORPORA[name]
    p = path_of(name)
    if p.exists():
        try:
            verify(name)
            print(f"{spec['path']} already present and matches its pin")
            return p
        except CorpusMismatch as e:
            print(e, file=sys.stderr)
            raise
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".part")
    print(f"fetching {name} ({spec['bytes']:,} bytes) from {spec['url']}")
    with urllib.request.urlopen(spec["url"], timeout=300) as r, tmp.open("wb") as out:
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            out.write(block)
    tmp.replace(p)
    verify(name)
    print(f"saved and verified: {spec['path']}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", metavar="NAME")
    ap.add_argument("--fetch", metavar="NAME")
    args = ap.parse_args()

    if args.fetch:
        fetch(args.fetch)
        return 0
    if args.verify:
        rec = verify(args.verify)
        print(json.dumps(rec, indent=1))
        return 0
    for name, spec in sorted(CORPORA.items()):
        p = path_of(name)
        state = "absent"
        if p.exists():
            state = "OK" if _sha256(p) == spec["sha256"] else "MISMATCH"
        print(f"{name:24s} {state:9s} {spec['bytes']:>12,} B  {spec['pool_sessions']:>6} sessions"
              f"  {spec['questions']} questions  {spec['licence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
