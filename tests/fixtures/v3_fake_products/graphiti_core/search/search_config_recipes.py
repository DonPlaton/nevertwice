"""The FAKE search recipes: EDGE_HYBRID_SEARCH_RRF and NODE_HYBRID_SEARCH_RRF with a limit and model_copy(deep=True)."""
from __future__ import annotations

import copy


class _Recipe:
    def __init__(self, kind: str, limit: int) -> None:
        self.kind, self.limit = kind, limit

    def model_copy(self, deep: bool = False) -> "_Recipe":
        return copy.deepcopy(self) if deep else copy.copy(self)


EDGE_HYBRID_SEARCH_RRF = _Recipe("edge", 10)
NODE_HYBRID_SEARCH_RRF = _Recipe("node", 10)
