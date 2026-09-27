"""The FAKE FalkorDriver: one JSON file per graph (database) under NVT3_FAKE_FALKOR_DIR stands in for the server;
clone(database) and execute_query(cypher, **params) -> (records, header, summary) as in 0.30.2, answering the three
count queries by group_id that the adapter's completeness check sends (and nothing else)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import _fake_http as H


class FalkorDriver:
    def __init__(self, host: str = "localhost", port: int = 6379, username=None, password=None, falkor_db=None,
                 database: str = "default_db") -> None:
        H.log("FalkorDriver", host=host, port=port, database=database)
        self.host, self.port, self._database = host, port, database

    def clone(self, database: str) -> "FalkorDriver":
        d = FalkorDriver.__new__(FalkorDriver)
        d.host, d.port, d._database = self.host, self.port, database
        return d

    def _file(self) -> Path:
        return Path(os.environ["NVT3_FAKE_FALKOR_DIR"]) / f"{self.host}_{self.port}_{self._database}.json"

    def _load(self) -> dict:
        f = self._file()
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"nodes": [], "edges": [], "episodes": []}

    def _save(self, db: dict) -> None:
        self._file().parent.mkdir(parents=True, exist_ok=True)
        self._file().write_text(json.dumps(db), encoding="utf-8")

    async def execute_query(self, cypher_query_, **kwargs):
        H.log("execute_query", cypher=cypher_query_, params=kwargs, database=self._database)
        db = self._load()
        gid = kwargs.get("gid")
        for key, marker in (("nodes", ":Entity "), ("edges", ":RELATES_TO "), ("episodes", ":Episodic ")):
            if marker in cypher_query_ and "count(" in cypher_query_:
                return [{"c": sum(1 for r in db[key] if r["group_id"] == gid)}], ["c"], None
        return [], [], None
