"""The FAKE graphiti_core.nodes: EpisodeType, EntityNode and EpisodicNode with get_by_group_ids(driver, group_ids,
limit=None, uuid_cursor=None) as in 0.30.2. NVT3_FAKE_GBG_LIMIT caps what get_by_group_ids returns - a read cut short, as
pagination would cut it - so the adapter's completeness check has something to catch."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EpisodeType(Enum):
    message = "message"
    json = "json"
    text = "text"


def _dt(v):
    return datetime.fromisoformat(v) if isinstance(v, str) else v


def _cap(rows: list) -> list:
    cap = os.environ.get("NVT3_FAKE_GBG_LIMIT")
    return rows[:int(cap)] if cap else rows


@dataclass
class EntityNode:
    uuid: str
    name: str
    summary: str
    labels: list
    group_id: str

    @classmethod
    def from_row(cls, r: dict) -> "EntityNode":
        return cls(r["uuid"], r["name"], r["summary"], list(r["labels"]), r["group_id"])

    @classmethod
    async def get_by_group_ids(cls, driver, group_ids, limit=None, uuid_cursor=None):
        return _cap([cls.from_row(r) for r in driver._load()["nodes"] if r["group_id"] in group_ids])


@dataclass
class EpisodicNode:
    uuid: str
    content: str
    valid_at: datetime
    group_id: str

    @classmethod
    async def get_by_group_ids(cls, driver, group_ids, limit=None, uuid_cursor=None):
        return _cap([cls(r["uuid"], r["content"], _dt(r["valid_at"]), r["group_id"])
                     for r in driver._load()["episodes"] if r["group_id"] in group_ids])
