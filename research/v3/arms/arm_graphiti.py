#!/usr/bin/env python3
"""PREREG-V3 TB4.6b (A6): the zep-graphiti adapter - the product arm "zep-graphiti", one child per (run, unit) and stage
(Q25), speaking base.py's protocol. It runs in the graphiti venv from the arm's runs-tree directory beside byte-identical
copies of base.py, _http_count.py and _ollama_pacer.py (Q9 O-b); it imports nothing of the repository, and is named
arm_graphiti.py so that `import graphiti_core` from that directory finds the venv's package (Q-46-1).

    <graphiti venv python> -B arm_graphiti.py <spec.json>

rev1 §2.2, as the auditor's Q-46b-1..4 read it:
* the writer is graphiti's OpenAIGenericClient with structured_output_mode="json_object" at the arm's proxy port
  (/u/<run>.<unit>/v1), model and small model deepseek-flash, the api key the arm's proxy token; no temperature and no
  max_tokens of ours (the product's defaults; the value in effect is the proxy capture, §5.5); no thinking route is
  known (§2.2.1); a counting subclass gives the logical calls for K87 (the v2 pattern, _graphiti_arm.py:76-82);
* the reranker is OpenAIRerankerClient on the same config - the constructor needs one - and it is counted: the RRF
  recipes of Points K and B never call it (Q-46b-3), so reranker_calls must stay 0;
* the embedder is graphiti's OpenAIEmbedder on Ollama's /v1 with the v3 tag at 1024 dims;
* the store is a FalkorDB graph per unit in the (arm, run, block) server: group_id = the unit id; a write stage needs
  it empty;
* one EpisodeType.message episode per message, "<speaker>: <text>", awaited one after another; reference_time = the
  session's date on a dated stand; on an undated stand the wall clock of the write, asserted strictly increasing over
  the unit's episodes (Q-46b-2), and the reader renders no valid_at / invalid_at there;
* the graph between the stages (Q-46b-1): end_write digests the unit's nodes, edges and episodes as the product's own
  get_by_group_ids returns them, after checking each count against a direct count by group_id in FalkorDB (a read cut
  short by pagination refuses by name), and writes a state seal beside the unit; the read stage recomputes it before
  the first search;
* read (Q-46b-4): EDGE_HYBRID_SEARCH_RRF then NODE_HYBRID_SEARCH_RRF, each with limit = k; at Point K the first k of
  "edges, then nodes", at Point B all of them (the harness's budget cuts); edges render as the fact, with its validity
  on a dated stand, nodes as "name: summary".
* Point V (the auditor's Q-V-1 = O-a; rev1 :334, :1337): 20 edges + 20 nodes in Zep's paper's context string template -
  only with that template in the spec (v_template: its text and sha256, built by the harness from the pin
  zep_paper_src; the text is never committed) and its sha256 the pinned one (V_TEMPLATE_SHA256, FREEZE's
  arm_templates); the facts one per line, "FACT (Date range: from - to)" with the K/B dates on a dated stand and the
  fact alone on an undated one (Q-ZT-7), the entities "name: summary", each block inserted verbatim; the answer is one
  item of kind "rendered" with the template's sha256 (Row V's Q1 = (a)). Without the template, Point V is refused by name as before.

The spec: arm ("zep-graphiti"), stage, stand, run, unit, unit_dir, port, embed_tag, ollama_url, falkor_host,
falkor_port, dated, record_path; optionally v_template (SPEC_OPTIONAL).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import base as B  # noqa: E402 - the protocol module, copied beside this file
import _http_count as HC  # noqa: E402 - the counting-only HTTP wrapper, copied beside this file

ARM = "zep-graphiti"
STAGES = ("write", "read")
SPEC_KEYS = ("arm", "stage", "stand", "run", "unit", "unit_dir", "port", "embed_tag", "ollama_url", "falkor_host",
             "falkor_port", "dated", "record_path")
DEEPSEEK_MODEL = "deepseek-flash"
EMBED_DIMS = 1024
TOKEN_NAME = "DEEPSEEK_API_KEY"
POINTS = ("K", "B")
#: Q-V-1 = O-a: Point V's reads (rev1 :334) and Zep's template, by the sha256 FREEZE records for "zep-graphiti:V" (the
#: text built from the pin zep_paper_src - Q-ZT-3a; the auditor chose it from the survey of a7-arxiv-src s3)
V_EDGES = V_NODES = 20
V_TEMPLATE_SHA256 = "1f38010000254c1fcdc7bf2a64b88326023c1094eea6ac6fe2446d263193a9b7"
V_SLOTS = ("{facts}", "{entities}")
SPEC_OPTIONAL = ("v_template",)
COUNT_CYPHER = {"nodes": "MATCH (n:Entity {group_id: $gid}) RETURN count(n) AS c",
                "edges": "MATCH ()-[e:RELATES_TO {group_id: $gid}]->() RETURN count(e) AS c",
                "episodes": "MATCH (n:Episodic {group_id: $gid}) RETURN count(n) AS c"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Refused(RuntimeError):
    """The arm may not start, or may not take this request."""


def load_spec(path: str | os.PathLike) -> dict:
    spec = json.loads(Path(path).read_bytes().decode("utf-8"))
    missing = [k for k in SPEC_KEYS if k not in spec]
    extra = sorted(set(spec) - set(SPEC_KEYS) - set(SPEC_OPTIONAL))
    if missing or extra:
        raise Refused(f"the spec lacks {missing} or carries unknown keys {extra}")
    if spec["arm"] != ARM or spec["stage"] not in STAGES:
        raise Refused(f"unknown arm {spec['arm']!r} or stage {spec['stage']!r}")
    if not (_ID.fullmatch(str(spec["run"])) and _ID.fullmatch(str(spec["unit"]))):
        raise Refused("run and unit ids are [A-Za-z0-9_-]{1,64}, no dots (Q3)")
    if not isinstance(spec["port"], int):
        raise Refused("zep-graphiti writes through its proxy port")
    return spec


def v_template_text(t, *, pinned: str = V_TEMPLATE_SHA256) -> str:
    """Point V's template from the spec (Q-V-1 = O-a): refused by name when absent, when its sha256 is not the pinned
    one, when its text is not its sha256, or when a slot is not there exactly once."""
    if not isinstance(t, Mapping) or not isinstance(t.get("text"), str):
        raise ValueError("point is K or B; Point V has no zep row until the Zep LME template is pinned (Q-46b-4)")
    if t.get("sha256") != pinned:
        raise Refused(f"Point V: the template's sha256 {str(t.get('sha256'))[:12]} is not the pinned template's "
                      f"{pinned[:12]} (Q-V-1)")
    if B.text_sha256(t["text"]) != t["sha256"]:
        raise Refused("Point V: the template's text is not its sha256")
    for slot in V_SLOTS:
        n = t["text"].count(slot)
        if n != 1:
            raise Refused(f"Point V: the template holds {slot} {n} times, not exactly once")
    return t["text"]


def render_v(template: str, facts: str, entities: str) -> str:
    """The template with its two slots filled, each value inserted verbatim - a fact holding "{entities}" is text."""
    at = sorted((template.index(slot), slot, value) for slot, value in zip(V_SLOTS, (facts, entities)))
    out, pos = [], 0
    for i, slot, value in at:
        out += [template[pos:i], value]
        pos = i + len(slot)
    return "".join(out + [template[pos:]])


def proxy_base(spec: Mapping[str, Any]) -> str:
    return f"http://127.0.0.1:{spec['port']}/u/{spec['run']}.{spec['unit']}/v1"


def store_uri(spec: Mapping[str, Any]) -> str:
    return f"falkordb://{spec['falkor_host']}:{spec['falkor_port']}/{spec['unit']}"


def declared(spec: Mapping[str, Any]) -> dict:
    return {"date_route": "field:reference_time" if spec["dated"] else "none (wall clock, strictly increasing; no "
                                                                      "validity rendered)",
            "renderer": {"name": "edges then nodes: fact [validity on dated stands]; name: summary"},
            "threshold": "n/a", "limit": "k per recipe; Point K the first k of edges-then-nodes, Point B all; Point V "
                                        "20 edges + 20 nodes in Zep's context string template (Q-V-1)",
            "namespace": "group_id = unit id; one FalkorDB graph per unit in the (arm, run, block) server",
            "write_granularity": "one EpisodeType.message episode per message, sequential",
            "llm_params": {"temperature": "product default (value in effect: the proxy capture, §5.5)",
                           "max_tokens": "product default", "thinking_route": "none known (§2.2.1)",
                           "structured_output_mode": "json_object"},
            "reranker": "OpenAIRerankerClient on the same config, never called by the RRF recipes (counted)",
            "tools_allowed": [], "store_persistence": "server (state seal)"}


def _tag_present(tags: Mapping[str, Any], tag: str) -> bool:
    want = {tag, tag if ":" in tag else f"{tag}:latest"}
    return bool(want & {m.get(k) for m in tags.get("models") or [] for k in ("name", "model")})


def _iso(v) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else (None if v is None else str(v))


def graph_state(g, loop, unit: str) -> tuple[str, dict]:
    """The unit's graph as the product reads it back - checked complete against a direct count - and its digest."""
    from graphiti_core.edges import EntityEdge  # noqa: PLC0415
    from graphiti_core.nodes import EntityNode, EpisodicNode  # noqa: PLC0415
    drv = g.driver.clone(database=unit)
    nodes = loop.run_until_complete(EntityNode.get_by_group_ids(drv, [unit]))
    edges = loop.run_until_complete(EntityEdge.get_by_group_ids(drv, [unit]))
    episodes = loop.run_until_complete(EpisodicNode.get_by_group_ids(drv, [unit]))
    got = {"nodes": len(nodes), "edges": len(edges), "episodes": len(episodes)}
    direct = {}
    for kind, cypher in COUNT_CYPHER.items():
        records, _header, _summary = loop.run_until_complete(drv.execute_query(cypher, gid=unit))
        direct[kind] = int(records[0]["c"]) if records else 0
    if got != direct:
        raise Refused(f"the product's reads of the graph are cut short: they return {got}, the graph holds {direct}")
    canon = {"nodes": sorted([n.uuid, n.name, n.summary, sorted(n.labels or [])] for n in nodes),
             "edges": sorted([e.uuid, e.source_node_uuid, e.target_node_uuid, e.name, e.fact, _iso(e.valid_at),
                              _iso(e.invalid_at), _iso(e.expired_at)] for e in edges),
             "episodes": sorted([p.uuid, p.content, _iso(p.valid_at)] for p in episodes)}
    digest = hashlib.sha256(json.dumps(canon, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    return digest.hexdigest(), got


def bind(spec: Mapping[str, Any], env: Mapping[str, str]) -> tuple[dict, dict]:
    rec: dict[str, Any] = {"arm": ARM, "stage": spec["stage"], "stand": spec["stand"], "run": spec["run"],
                           "unit": spec["unit"], "store": store_uri(spec), "declared": declared(spec)}
    token = env.get(TOKEN_NAME)
    if not token or not token.startswith("nvt3-"):
        raise Refused(f"{TOKEN_NAME} does not hold the arm's proxy token (nvt3-...)")
    ollama_port = int(spec["ollama_url"].rsplit(":", 1)[1].split("/")[0])
    rec["http_doors"] = HC.install(proxy_port=spec["port"], ollama_ports=(ollama_port,))
    import _ollama_pacer as P  # noqa: PLC0415 - copied beside this file; installed AFTER the counter (Q-46-4)
    P.install("observe")               # R-EMBED-PATH: the proxy leg paces and retries - one layer for every arm
    P.set_route("127.0.0.1", ollama_port)        # the leg is the one route; any other Ollama is direct_calls
    rec["pacer"] = P._MODE  # the installed pacer's own mode, never a copy of the intention (B-PACER-REC)
    rec["ollama_route"] = spec["ollama_url"]
    with urllib.request.urlopen(spec["ollama_url"].rstrip("/") + "/api/tags", timeout=30) as r:
        if not _tag_present(json.loads(r.read() or b"{}"), spec["embed_tag"]):
            raise Refused(f"the embed tag {spec['embed_tag']!r} is not in Ollama")
    import graphiti_core  # noqa: PLC0415 - the venv's product (Q-46-1: never this file)
    rec["product"] = {"name": "graphiti-core", "version": getattr(graphiti_core, "__version__", None),
                      "file": str(Path(graphiti_core.__file__).resolve())}
    if HERE in Path(graphiti_core.__file__).resolve().parents:
        raise Refused("import graphiti_core found a module in the arm's directory, not the venv's package")
    from graphiti_core import Graphiti  # noqa: PLC0415
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient  # noqa: PLC0415
    from graphiti_core.driver.falkordb_driver import FalkorDriver  # noqa: PLC0415
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig  # noqa: PLC0415
    from graphiti_core.llm_client.config import LLMConfig  # noqa: PLC0415
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient  # noqa: PLC0415
    tally = {"llm_calls": 0, "llm_failures": 0, "reranker_calls": 0}

    class CountingLLM(OpenAIGenericClient):
        async def generate_response(self, *a, **k):
            tally["llm_calls"] += 1
            try:
                return await super().generate_response(*a, **k)
            except Exception:
                tally["llm_failures"] += 1
                raise

    class CountingReranker(OpenAIRerankerClient):
        async def rank(self, *a, **k):
            tally["reranker_calls"] += 1
            return await super().rank(*a, **k)

    cfg = LLMConfig(**{"api_key": token, "model": DEEPSEEK_MODEL, "small_model": DEEPSEEK_MODEL,
                       "base_url": proxy_base(spec)})
    g = Graphiti(llm_client=CountingLLM(config=cfg, structured_output_mode="json_object"),
                 embedder=OpenAIEmbedder(OpenAIEmbedderConfig(**{
                     "embedding_model": spec["embed_tag"], "embedding_dim": EMBED_DIMS, "api_key": "ollama",
                     "base_url": spec["ollama_url"].rstrip("/") + "/v1"})),
                 cross_encoder=CountingReranker(config=cfg),
                 graph_driver=FalkorDriver(host=spec["falkor_host"], port=spec["falkor_port"], database=spec["unit"]))
    loop = asyncio.new_event_loop()
    unit_dir = Path(spec["unit_dir"])
    if spec["stage"] == "write":
        loop.run_until_complete(g.build_indices_and_constraints())
        _digest, counts = graph_state(g, loop, spec["unit"])
        if any(counts.values()):
            raise Refused(f"the write stage needs a fresh graph, and the unit's holds {counts}")
    else:
        digest, counts = graph_state(g, loop, spec["unit"])
        rec["seal"] = B.check_state_seal(unit_dir, store_uri(spec), digest)     # Q-46b-1, before the first search
    rec["semaphore_limit"] = env.get("SEMAPHORE_LIMIT") or "20 (the product's default)"
    rec["telemetry"] = env.get("GRAPHITI_TELEMETRY_ENABLED")
    rec["env_names"] = sorted(env)
    rec["python"] = sys.version.split()[0]
    return {"g": g, "loop": loop, "tally": tally, "pacer": P}, rec


class Handler:
    def __init__(self, spec: Mapping[str, Any], ns: Mapping[str, Any], rec: Mapping[str, Any]) -> None:
        self.spec, self.rec = spec, rec
        self.g, self.loop, self.tally, self.pacer = ns["g"], ns["loop"], ns["tally"], ns["pacer"]
        self.stage, self.unit = spec["stage"], spec["unit"]
        self.last_ref: datetime | None = None
        self.episodes = {"episodes": 0, "episode_errors": 0}
        self.reads = {"reads": 0, "items_returned": 0}

    def _stage(self, want: str, op: str) -> None:
        if self.stage != want:
            raise RuntimeError(f"{op} belongs to the {want} stage; this process is the {self.stage} stage (Q25)")

    def hello(self) -> dict:
        return {"protocol": B.PROTOCOL, "system": "graphiti", "arm": ARM, "stage": self.stage,
                "version": self.rec["product"]["version"], "python": self.rec["python"],
                "llm_label": f"deepseek:{DEEPSEEK_MODEL}", "embedder": self.spec["embed_tag"],
                "env_names": self.rec["env_names"], "start": self.rec}

    def _reference_time(self, date: str | None) -> datetime:
        if self.spec["dated"]:
            if not date:
                raise ValueError("a dated stand's write carries its date (reference_time)")
            ref = datetime.fromisoformat(date if "T" in date else f"{date}T00:00:00")
            return ref if ref.tzinfo else ref.replace(tzinfo=timezone.utc)
        ref = datetime.now(timezone.utc)
        if self.last_ref is not None and ref <= self.last_ref:
            raise Refused("reference_time on an undated stand is not strictly increasing (Q-46b-2)")
        self.last_ref = ref
        return ref

    def write(self, item: dict, date: str | None = None) -> dict:
        self._stage("write", "write")
        from graphiti_core.nodes import EpisodeType  # noqa: PLC0415
        t0 = time.time()
        body = f"{item['speaker']}: {item['text']}"
        ref = self._reference_time(date)
        self.episodes["episodes"] += 1
        try:
            self.loop.run_until_complete(self.g.add_episode(
                name=f"{self.unit}-{item['item_id']}", episode_body=body, source_description="conversation message",
                reference_time=ref, source=EpisodeType.message, group_id=self.unit))
        except Exception:
            self.episodes["episode_errors"] += 1
            raise
        return {"op_id": item["item_id"], "text_sha256": B.text_sha256(body), "reference_time": ref.isoformat(),
                "t0": t0, "t1": time.time()}

    def end_write(self) -> dict:
        self._stage("write", "end_write")
        t0 = time.time()
        digest, counts = graph_state(self.g, self.loop, self.unit)
        seal = B.write_state_seal(self.spec["unit_dir"], store_uri(self.spec), digest, arm=ARM, run=self.spec["run"],
                                  unit=self.unit, counts=counts)
        return {"footprint": {"retrievable": counts["edges"] + counts["nodes"], **counts}, "seal": seal, "t0": t0,
                "t1": time.time()}

    def _range(self, e) -> str:
        va = _iso(e.valid_at)[:10] if e.valid_at else "?"
        ia = _iso(e.invalid_at)[:10] if e.invalid_at else "present"
        return f"{va} - {ia}"

    def _render_edge(self, e) -> str:
        return e.fact if not self.spec["dated"] else f"{e.fact} ({self._range(e)})"

    def _fact_v(self, e) -> str:
        """Q-ZT-4/7: the paper's 'FACT (Date range: from - to)' with the K/B dates on a dated stand; the fact alone else."""
        return e.fact if not self.spec["dated"] else f"{e.fact} (Date range: {self._range(e)})"

    def _read_v(self, qid: str, query: str) -> dict:
        template = v_template_text(self.spec.get("v_template"))
        from graphiti_core.search.search_config_recipes import EDGE_HYBRID_SEARCH_RRF, NODE_HYBRID_SEARCH_RRF  # noqa
        t0 = time.time()
        ecfg, ncfg = EDGE_HYBRID_SEARCH_RRF.model_copy(deep=True), NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
        ecfg.limit, ncfg.limit = V_EDGES, V_NODES
        er = self.loop.run_until_complete(self.g.search_(query, config=ecfg, group_ids=[self.unit]))
        nr = self.loop.run_until_complete(self.g.search_(query, config=ncfg, group_ids=[self.unit]))
        text = render_v(template, "\n".join(self._fact_v(e) for e in er.edges),
                        "\n".join(f"{n.name}: {n.summary}" for n in nr.nodes))
        self.reads["reads"] += 1
        self.reads["items_returned"] += 1
        item = {"kind": "rendered", "text": text, "rank": 1, "template_sha256": self.spec["v_template"]["sha256"]}
        return {"qid": qid, "items": [item], "items_returned": 1,
                "k": {"edges": V_EDGES, "nodes": V_NODES}, "edges": len(er.edges), "nodes": len(nr.nodes), "point": "V",
                "template_sha256": self.spec["v_template"]["sha256"], "t0": t0, "t1": time.time()}

    def read(self, qid: str, query: str, k: int, point: str | None = None) -> dict:
        self._stage("read", "read")
        if point == "V":
            return self._read_v(qid, query)
        if point not in POINTS:
            raise ValueError("point is K or B; Point V has no zep row until the Zep LME template is pinned (Q-46b-4)")
        from graphiti_core.search.search_config_recipes import EDGE_HYBRID_SEARCH_RRF, NODE_HYBRID_SEARCH_RRF  # noqa
        t0 = time.time()
        ecfg, ncfg = EDGE_HYBRID_SEARCH_RRF.model_copy(deep=True), NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
        ecfg.limit = ncfg.limit = int(k)
        er = self.loop.run_until_complete(self.g.search_(query, config=ecfg, group_ids=[self.unit]))
        nr = self.loop.run_until_complete(self.g.search_(query, config=ncfg, group_ids=[self.unit]))
        texts = [("edge", self._render_edge(e)) for e in er.edges] + [("node", f"{n.name}: {n.summary}")
                                                                      for n in nr.nodes]
        if point == "K":
            texts = texts[:int(k)]
        items = [{"kind": kind, "text": text, "rank": r} for r, (kind, text) in enumerate(texts, 1)]
        self.reads["reads"] += 1
        self.reads["items_returned"] += len(items)
        return {"qid": qid, "items": items, "items_returned": len(items), "k": int(k), "point": point, "t0": t0,
                "t1": time.time()}

    def counters(self) -> dict:
        snap = HC.snapshot()
        transport: dict = {}
        self.pacer.attach(transport)
        return {"http": snap, **self.tally, **self.episodes, **self.reads,
                "ollama_transport": transport.get("ollama_transport")}


class RefusedHandler:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __getattr__(self, op: str):
        if op not in B.OPS:
            raise AttributeError(op)

        def refuse(**_kw):
            raise Refused(f"the arm refused to start: {self.reason}")
        return refuse


def _record(path: str, rec: Mapping[str, Any]) -> None:
    Path(path).write_bytes((json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=1, default=str) + "\n")
                           .encode("utf-8"))


def build(spec_path: str, env: Mapping[str, str]) -> Any:
    try:
        spec = load_spec(spec_path)
    except (Refused, OSError, ValueError, KeyError) as e:
        return RefusedHandler(f"{type(e).__name__}: {e}")
    try:
        ns, rec = bind(spec, env)
        handler = Handler(spec, ns, rec)
    except Exception as e:  # noqa: BLE001 - every failure to bind is a named refusal, recorded
        reason = f"{type(e).__name__}: {e}"
        _record(spec["record_path"], {"ok": False, "error": reason[:B.ERROR_MAX], "arm": ARM,
                                      "stage": spec["stage"], "unit": spec["unit"]})
        return RefusedHandler(reason)
    _record(spec["record_path"], {"ok": True, **rec})
    return handler


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: arm_graphiti.py <spec.json>\n")
        sys.exit(2)
    ENV_AT_START = dict(os.environ)
    sys.exit(B.main_with(lambda: build(sys.argv[1], ENV_AT_START)))
