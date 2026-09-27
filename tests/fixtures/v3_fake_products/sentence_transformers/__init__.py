"""A FAKE sentence_transformers for the v3 adapter suite: building a SentenceTransformer logs it and raises - a v3 arm
embeds with the v3 tag only, so an attempt is a finding, never a silent download."""
import _fake_http as H

__version__ = "0.0.0-fake"


class SentenceTransformer:
    def __init__(self, *a, **k) -> None:
        H.log("SentenceTransformer", args=[str(x) for x in a], kwargs=sorted(k))
        raise RuntimeError("the fake refuses SentenceTransformer: v3 arms embed with the v3 tag only")
