"""A FAKE graphiti_core for the v3 adapter suite (tests/research/_test_v3_arm_graphiti.py) - never a benchmark subject.

Graphiti(llm_client, embedder, cross_encoder, graph_driver) logs how it was built; add_episode logs its keyword arguments,
asks the LLM client once (through generate_response, so the adapter's counting subclass sees it), embeds the body and
stores one episode, one entity node per speaker and one fact edge in the driver's graph; search_ ranks the group's edges
or nodes (by the recipe's kind) and honours the recipe's limit. The FalkorDB server is a JSON file per graph under
NVT3_FAKE_FALKOR_DIR, so a new read-stage process sees the write stage's graph.
"""
from __future__ import annotations

import math
import os
import uuid as _uuid
from dataclasses import dataclass, field

import _fake_http as H

from .edges import EntityEdge
from .nodes import EntityNode, EpisodicNode

__version__ = "0.0.0-fake"


@dataclass
class SearchResults:
    edges: list = field(default_factory=list)
    nodes: list = field(default_factory=list)
    episodes: list = field(default_factory=list)


class Graphiti:
    def __init__(self, *, llm_client, embedder, cross_encoder, graph_driver, **kwargs) -> None:
        H.log("Graphiti", extra=sorted(kwargs), database=graph_driver._database)
        self.llm_client, self.embedder, self.cross_encoder, self.driver = llm_client, embedder, cross_encoder, \
            graph_driver

    async def build_indices_and_constraints(self) -> None:
        H.log("build_indices_and_constraints")

    async def add_episode(self, name, episode_body, source_description, reference_time, source=None, group_id=None,
                          **kwargs):
        H.log("add_episode", name=name, episode_body=episode_body, source=getattr(source, "value", source),
              reference_time=reference_time.isoformat(), group_id=group_id, source_description=source_description,
              extra=sorted(kwargs))
        await self.llm_client.generate_response([{"role": "user", "content": episode_body}])
        vec = (await self.embedder.create([episode_body]))
        drv = self.driver.clone(database=group_id)
        db = drv._load()
        speaker = episode_body.split(":", 1)[0]
        node = next((n for n in db["nodes"] if n["name"] == speaker), None)
        if node is None:
            node = {"uuid": _uuid.uuid4().hex, "name": speaker, "summary": f"{speaker} speaks", "labels": ["Entity"],
                    "group_id": group_id, "vec": vec}
            db["nodes"].append(node)
        db["edges"].append({"uuid": _uuid.uuid4().hex, "source_node_uuid": node["uuid"],
                            "target_node_uuid": node["uuid"], "name": "SAID", "fact": episode_body,
                            "valid_at": reference_time.isoformat(), "invalid_at": None, "expired_at": None,
                            "group_id": group_id, "vec": vec})
        db["episodes"].append({"uuid": _uuid.uuid4().hex, "content": episode_body,
                               "valid_at": reference_time.isoformat(), "group_id": group_id})
        drv._save(db)

    async def search_(self, query, config=None, group_ids=None, **kwargs):
        H.log("search_", query=query, kind=config.kind, limit=config.limit, group_ids=group_ids, extra=sorted(kwargs))
        if os.environ.get("NVT3_FAKE_RERANK"):              # a recipe that reranks: the adapter must count it
            await self.cross_encoder.rank(query, ["p"])
        qv = await self.embedder.create([query])
        db = self.driver.clone(database=group_ids[0])._load()

        def cos(a, b):
            na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
            return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0
        rows = db["edges"] if config.kind == "edge" else db["nodes"]
        ranked = sorted(rows, key=lambda r: -cos(qv, r["vec"]))[:config.limit]
        make = EntityEdge.from_row if config.kind == "edge" else EntityNode.from_row
        out = [make(r) for r in ranked]
        return SearchResults(edges=out) if config.kind == "edge" else SearchResults(nodes=out)


__all__ = ["Graphiti", "SearchResults", "EntityEdge", "EntityNode", "EpisodicNode"]
