"""A FAKE rank_bm25 for the v3 adapter suite: only the name A-mem's memory_system imports."""


class BM25Okapi:
    def __init__(self, corpus) -> None:
        self.corpus = corpus
