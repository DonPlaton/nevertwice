#!/usr/bin/env python3
"""PREREG-V3 TB4.3a (A6): the stand's tokenizers - the §5.1 embed cut and the §5.2 cl100k counter.

§5.1: every text the stand embeds, or hands a product to embed as one item, is cut by the stand to <= 2,048 bge-m3
tokens, with the pinned BAAI/bge-m3 tokenizer, explicitly, counted per item, identically for every arm. The auditor's
A6 Q29 ruling: the 2,048 INCLUDE the two special tokens Ollama adds (<s>, </s>) - num_batch 2048 applies to the whole
sequence and an overflow is a 400, a failed outcome (P0a, no shrink-retry) - so the content is cut to 2,046 tokens, at
a character offset, so that the kept text is an exact prefix of the original. One Truncator serves every arm, before
any arm sees the text; each cut records the original and kept token counts and whether it truncated.

The real tokenizers load lazily from their pinned files (`tokenizers`, `tiktoken`; the v3_data venv), so this module
imports nothing heavy and the core suite drives it with a fake tokenizer.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

CAP = 2048                 # §5.1, the whole sequence the embedder sees (A6 Q29 O-a)
SPECIALS = 2               # <s> and </s>, added to every bge-m3 input (A6 Q29 O-a)
CL100K_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"

Spans = Callable[[str], Sequence[tuple[int, int]]]


@dataclass(frozen=True)
class Cut:
    text: str
    original_tokens: int     # the whole sequence, specials included
    kept_tokens: int
    truncated: bool


class Truncator:
    """The §5.1 cut. ``spans(text)`` returns the character span of each CONTENT token (special tokens excluded)."""

    def __init__(self, spans: Spans, *, cap: int = CAP, specials: int = SPECIALS) -> None:
        if cap <= specials:
            raise ValueError("the cap must leave room for content")
        self._spans, self.cap, self.specials = spans, cap, specials
        self.items = self.truncated = 0

    def cut(self, text: str) -> Cut:
        spans = list(self._spans(text))
        n = len(spans) + self.specials
        self.items += 1
        if n <= self.cap:
            return Cut(text, n, n, False)
        keep = self.cap - self.specials
        end = spans[keep - 1][1]
        kept = text[:end]
        # A prefix can tokenize differently at its edge; shrink by whole tokens until it fits, never grow.
        while len(self._spans(kept)) > keep:
            keep -= 1
            if keep == 0:
                raise ValueError("no prefix of the text fits the cap")
            kept = text[:spans[keep - 1][1]]
        self.truncated += 1
        return Cut(kept, n, len(self._spans(kept)) + self.specials, True)

    def stats(self) -> dict:
        return {"items": self.items, "truncated": self.truncated, "cap": self.cap, "specials": self.specials}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def bge_m3_spans(tokenizer_json: Path, *, expected_sha256: str) -> Spans:
    """The pinned bge-m3 tokenizer.json as a span function (content tokens only). Refuses a file off its pin."""
    if sha256_file(tokenizer_json) != expected_sha256:
        raise ValueError(f"{tokenizer_json} is not the pinned bge-m3 tokenizer.json")
    from tokenizers import Tokenizer  # noqa: PLC0415 - the v3_data venv only

    tok = Tokenizer.from_file(str(tokenizer_json))

    def spans(text: str) -> list[tuple[int, int]]:
        enc = tok.encode(text, add_special_tokens=True)
        specials = sum(enc.special_tokens_mask)
        if specials != SPECIALS:
            raise ValueError(f"bge-m3 added {specials} special tokens, not the {SPECIALS} A6 Q29 counts")
        return [tuple(o) for o, sp in zip(enc.offsets, enc.special_tokens_mask) if not sp]

    return spans


def cl100k_counter(bpe_file: Path, *, expected_sha256: str) -> Callable[[str], int]:
    """tiktoken's own cl100k_base, built from the pinned .tiktoken file with no network: the file is offered to
    tiktoken as its cache entry (the key is sha1 of the URL) and tiktoken checks the hash itself."""
    if sha256_file(bpe_file) != expected_sha256:
        raise ValueError(f"{bpe_file} is not the pinned cl100k_base.tiktoken")
    import tiktoken  # noqa: PLC0415 - the v3_data venv only

    cache = Path(tempfile.mkdtemp(prefix="nvt3_tiktoken_"))
    old = os.environ.get("TIKTOKEN_CACHE_DIR")
    try:
        shutil.copyfile(bpe_file, cache / hashlib.sha1(CL100K_URL.encode()).hexdigest())
        os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
        enc = tiktoken.get_encoding("cl100k_base")
    finally:
        if old is None:
            os.environ.pop("TIKTOKEN_CACHE_DIR", None)
        else:
            os.environ["TIKTOKEN_CACHE_DIR"] = old
        shutil.rmtree(cache, ignore_errors=True)
    return lambda text: len(enc.encode(text, disallowed_special=()))
