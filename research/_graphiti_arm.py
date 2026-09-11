"""RESEARCH helper - the Zep/Graphiti arm for the temporal stands (ledger J4).

Graphiti is the one competitor with bitemporal edges (`valid_at` / `invalid_at`) and an LLM
invalidation step, so it is the direct rival on the supersession and as-of stands. This helper
drives `graphiti-core` the way its documentation says a user would: one episode per session
with the session's date as `reference_time`, one `group_id` per case, hybrid edge search
(BM25 + cosine, reciprocal-rank fusion - no cross-encoder, the same shape as the other arms).

The driver check of 2026-09-10 fixed the configuration recorded here: the embedded drivers do
not run on this machine (Kuzu is deprecated and broken in 0.30.2, FalkorDB Lite has no Windows
build), so the arm talks to a local FalkorDB container; the local model returns valid JSON only
under `structured_output_mode="json_schema"` (2 of 5 episodes under `json_object`); the
extractor and embedder are the stands' (`qwen3-coder:30b`, `bge-m3`) through Ollama's
OpenAI-compatible endpoint.

As-of is answered from Graphiti's own bitemporal fields, filtered here rather than through
`SearchFilters`: in the probe the server-side date filter admitted edges whose `valid_at` lay
after the day asked for, and an edge the extractor left without `valid_at` fell out of every
filtered search. An edge counts as believed on a day when it was stated by an episode on or
before that day (its own `valid_at`, else the episode's reference time) and neither
`invalid_at` nor `expired_at` lies on or before it. That is the most generous reading of the
product's data, and it is written down.

Underscore-prefixed: a helper, imported by the stands inside their `zep` arm, never run alone.
It imports no project module and touches no store of ours.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

OLLAMA_OPENAI = os.environ.get("OLLAMA_OPENAI_BASE", "http://localhost:11434/v1")
FALKOR_HOST = os.environ.get("FALKORDB_HOST", "localhost")
FALKOR_PORT = int(os.environ.get("FALKORDB_PORT", "6380"))
JSON_MODE = os.environ.get("GRAPHITI_JSON_MODE", "json_schema")


def available() -> str | None:
    """None when the arm can run here; else the reason it is blocked."""
    try:
        import graphiti_core  # noqa: F401
        from graphiti_core.driver.falkordb_driver import FalkorDriver  # noqa: F401
    except ImportError as e:
        return f"graphiti-core with the FalkorDB client is not installed in this environment ({e})"
    try:
        import socket
        with socket.create_connection((FALKOR_HOST, FALKOR_PORT), timeout=2):
            pass
    except OSError as e:
        return (f"no FalkorDB at {FALKOR_HOST}:{FALKOR_PORT} ({type(e).__name__}); start one with "
                f"`docker run -d -p 127.0.0.1:{FALKOR_PORT}:6379 falkordb/falkordb:latest`")
    return None


def _day(d: str) -> datetime:
    return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)


class GraphitiArm:
    """One Graphiti instance for a whole stand run; cases are separated by `group_id`."""

    def __init__(self, llm: str, embedder: str, database: str | None = None):
        from graphiti_core import Graphiti
        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        self.llm_name, self.embedder_name = llm, embedder
        cfg = LLMConfig(api_key="ollama", model=llm, small_model=llm, base_url=OLLAMA_OPENAI, temperature=0)
        outer = self

        class Counting(OpenAIGenericClient):
            async def generate_response(self, *a, **k):
                outer.llm_calls += 1
                try:
                    return await super().generate_response(*a, **k)
                except Exception:
                    outer.llm_failures += 1
                    raise

        self.llm_calls = 0
        self.llm_failures = 0
        self.episodes = 0
        self.episode_errors = 0
        self.episode_time = {}                        # episode uuid -> reference_time
        self.database = database or f"nw_{int(time.time())}"
        self.g = Graphiti(
            llm_client=Counting(config=cfg, structured_output_mode=JSON_MODE),
            embedder=OpenAIEmbedder(OpenAIEmbedderConfig(embedding_model=embedder, embedding_dim=1024,
                                                         api_key="ollama", base_url=OLLAMA_OPENAI)),
            cross_encoder=OpenAIRerankerClient(config=cfg),        # unused by the RRF recipe
            graph_driver=FalkorDriver(host=FALKOR_HOST, port=FALKOR_PORT, database=self.database),
        )
        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(self.g.build_indices_and_constraints())

    # ── write ─────────────────────────────────────────────────────────────────

    def ingest(self, group: str, sessions: list[tuple[str, str | None]]) -> dict:
        """Sessions as episodes, in order. `sessions` = [(text, 'YYYY-MM-DD' or None)]; an undated
        session is stamped now, as the other arms date it. Returns per-group counts."""
        from graphiti_core.nodes import EpisodeType
        ok = err = 0
        for j, (text, day) in enumerate(sessions):
            when = _day(day) if day else datetime.now(timezone.utc)
            try:
                res = self.loop.run_until_complete(self.g.add_episode(
                    name=f"{group}-s{j}", episode_body=text, source_description="agent session",
                    reference_time=when, source=EpisodeType.text, group_id=group))
                ep = getattr(res, "episode", None)
                if ep is not None:
                    self.episode_time[ep.uuid] = when
                ok += 1
            except Exception as e:                       # noqa: BLE001 - counted, the stand reports
                err += 1
                self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
            self.episodes += 1
        self.episode_errors += err
        return {"ok": ok, "errors": err}

    # ── read ──────────────────────────────────────────────────────────────────

    def _edges(self, group: str, query: str, k: int):
        from graphiti_core.search.search_config_recipes import EDGE_HYBRID_SEARCH_RRF
        cfg = EDGE_HYBRID_SEARCH_RRF.model_copy(deep=True)
        cfg.limit = max(k, 10)
        res = self.loop.run_until_complete(self.g.search_(query, config=cfg, group_ids=[group]))
        return list(res.edges)

    def search_now(self, group: str, query: str, k: int) -> list[str]:
        """What Graphiti hands back for the question today: the facts of the top edges. Edges
        the product itself expired or invalidated are what it would hide; they are dropped, as
        the product's own `search()` does."""
        out = []
        for e in self._edges(group, query, k):
            if e.expired_at is not None or e.invalid_at is not None:
                continue
            out.append(e.fact)
            if len(out) >= k:
                break
        return out

    def edges_all(self, group: str) -> list[tuple[str, bool]] | None:
        """Every edge the graph holds for `group`, as (fact, ended): `ended` when Graphiti itself
        invalidated or expired the edge. What the store holds, not what search ranks - the input to
        the cause split of a control miss (retired / never written / unranked). None when the
        graph could not be read, so the caller leaves the cause unread rather than zero."""
        try:
            from graphiti_core.edges import EntityEdge
            edges = self.loop.run_until_complete(EntityEdge.get_by_group_ids(self.g.driver, [group]))
            return [(e.fact, e.invalid_at is not None or e.expired_at is not None) for e in edges]
        except Exception as e:                           # noqa: BLE001 - reported once, not a number
            if not getattr(self, "_edges_all_failed", False):
                self._edges_all_failed = True
                self.last_error = f"edges_all: {type(e).__name__}: {str(e)[:160]}"
                print(f"  (graph read for the cause split unavailable: {self.last_error})", flush=True)
            return None

    def _stated_at(self, e) -> datetime | None:
        if e.valid_at is not None:
            return e.valid_at if e.valid_at.tzinfo else e.valid_at.replace(tzinfo=timezone.utc)
        times = [self.episode_time[u] for u in (e.episodes or []) if u in self.episode_time]
        return min(times) if times else None

    def search_asof(self, group: str, query: str, day: str, k: int) -> list[str]:
        """The facts believed on `day`: stated on or before it, not invalidated or expired by it."""
        d = _day(day)
        out = []
        for e in self._edges(group, query, k * 3):
            stated = self._stated_at(e)
            if stated is None or stated > d:
                continue
            ends = [t for t in (e.invalid_at, e.expired_at) if t is not None]
            ends = [t if t.tzinfo else t.replace(tzinfo=timezone.utc) for t in ends]
            if any(t <= d for t in ends):
                continue
            out.append(e.fact)
            if len(out) >= k:
                break
        return out

    def stats(self) -> dict:
        return {"llm": self.llm_name, "embedder": self.embedder_name, "json_mode": JSON_MODE,
                "driver": f"falkordb {FALKOR_HOST}:{FALKOR_PORT}", "database": self.database,
                "episodes": self.episodes, "episode_errors": self.episode_errors,
                "llm_calls": self.llm_calls, "llm_failures": self.llm_failures,
                "llm_calls_per_episode": round(self.llm_calls / self.episodes, 2) if self.episodes else None}

    def close(self) -> None:
        try:
            self.loop.run_until_complete(self.g.close())
        except Exception:                                # noqa: BLE001
            pass
        self.loop.close()
