"""The FAKE graphiti_core.edges: EntityEdge with the fields the adapter reads and get_by_group_ids as in 0.30.2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .nodes import _cap, _dt


@dataclass
class EntityEdge:
    uuid: str
    source_node_uuid: str
    target_node_uuid: str
    name: str
    fact: str
    valid_at: datetime | None
    invalid_at: datetime | None
    expired_at: datetime | None
    group_id: str

    @classmethod
    def from_row(cls, r: dict) -> "EntityEdge":
        return cls(r["uuid"], r["source_node_uuid"], r["target_node_uuid"], r["name"], r["fact"], _dt(r["valid_at"]),
                   _dt(r["invalid_at"]), _dt(r["expired_at"]), r["group_id"])

    @classmethod
    async def get_by_group_ids(cls, driver, group_ids, limit=None, uuid_cursor=None, with_embeddings=False):
        return _cap([cls.from_row(r) for r in driver._load()["edges"] if r["group_id"] in group_ids])
