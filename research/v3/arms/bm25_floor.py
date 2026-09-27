#!/usr/bin/env python3
"""PREREG-V3 TB4.5b (A6): the bm25-floor arm - the retrieval tier's lexical floor, one child per (run, unit) and stage
(Q25), speaking base.py's protocol.

    python -B research/v3/arms/bm25_floor.py <spec.json>

The auditor's Q-45-2 O-a: the engine's own lexical scorer - ``_bm25_scores`` with its tokenizer (``_tokens`` for the
query; ``_token_list``, stop words and stems inside the scorer) and its k1 / b defaults, recorded from the signature -
over the unit's items after the §5.1 cut; IDF over the unit's items only, never a store; top-k as every retrieval arm.
The function object is the ANCHOR's, taken from the engine namespace (memory_hook runs _engine_recall.py in it), and
hello reports that it is that object and where its code lives - no copy, no rewritten formula. The one adapter is
structural: the scorer takes store notes, so each item is a note {title: "", desc: <text>, prevention: ""} under a key
that tokenizes to nothing (the scorer reads the key into the document text: "#" and the index's digits written as
punctuation), which write refuses if it ever did tokenize.

No LLM, no embedder, no store of the product: the write stage keeps the items and end_write persists them to
<unit>/store/items.json and seals the store (Q-45-5); the read stage (a new process) checks the seal, then loads it. A read returns only the items that share a query term with it (the scorer keeps a score > 0), so it can return fewer
than k where a vector arm returns k: declared in the start record ("returns") and counted per read (items_returned,
reads_short_of_k) - a property of the lexical path, not a defect (the auditor's F7). sandbox_guard.isolate() runs in
__main__
before any project import (bind refuses unless it ran), so the engine's store constants land on a throwaway store that
nothing writes. Ties rank by index.
The spec: arm ("bm25-floor"), stage, stand, run, unit, unit_dir, record_path.

B-A2: the floor has no LLM. __main__ takes the environment the harness gave it BEFORE isolate() (as the runner does),
and bind refuses by name a proxy token's name, any variable whose name holds KEY, TOKEN, SECRET or PASSWORD, and any
value in a provider key's (sk-) or a proxy token's (nvt3-) form - isolate() can no longer hide one from the check.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(REPO))
import base as B  # noqa: E402 - the protocol module next to this file
import sandbox_guard  # noqa: E402 - imports no project module; isolate() runs in __main__, before any

ARM = "bm25-floor"
STAGES = ("write", "read")
SPEC_KEYS = ("arm", "stage", "stand", "run", "unit", "unit_dir", "record_path")
TOKEN_NAMES = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN")
SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")        # the runner's (the 09:27 correction)
SECRET_VALUE_PREFIXES = ("sk-", "nvt3-")
_DIGITS = str.maketrans("0123456789", "!$%&()*+,;")


class Refused(RuntimeError):
    """The floor may not start or may not take this item."""


def item_key(index: int) -> str:
    """The item's key in the scorer's candidate list: unique, and no token of the engine's tokenizer."""
    return "#" + str(index).translate(_DIGITS)


def load_spec(path: str | os.PathLike) -> dict:
    spec = json.loads(Path(path).read_bytes().decode("utf-8"))
    missing = [k for k in SPEC_KEYS if k not in spec]
    extra = sorted(set(spec) - set(SPEC_KEYS))
    if missing or extra:
        raise Refused(f"the spec lacks {missing} or carries unknown keys {extra}")
    if spec["arm"] != ARM or spec["stage"] not in STAGES:
        raise Refused(f"unknown arm {spec['arm']!r} or stage {spec['stage']!r}")
    if not Path(spec["unit_dir"]).is_absolute():
        raise Refused("unit_dir is not an absolute path")
    return spec


def check_anchor(m) -> list[str]:
    """The scorer and the tokenizer are the anchor's: code in nevertwice/_engine_recall.py, and the engine namespace's
    own objects (their globals ARE that namespace) - not a copy made elsewhere; the morphology step is on (shipped)."""
    problems = []
    for what, fn in (("scorer", m._bm25_scores), ("tokenizer", m._tokens)):
        code = Path(fn.__code__.co_filename)
        if not (code.name == "_engine_recall.py" and code.resolve().parent == REPO / "nevertwice"):
            problems.append(f"the {what}'s code is not the anchor's nevertwice/_engine_recall.py: {code}")
        if fn.__globals__ is not vars(m):
            problems.append(f"the {what} is not the engine namespace's own object (a copy?)")
    if m.LEXICAL_MORPHOLOGY is not True:
        problems.append("the engine's morphology step is off; the shipped default is on")
    return problems


def check_env(env: Mapping[str, str]) -> None:
    """B-A2: the environment the child was given holds nothing an LLM arm would - see the module docstring."""
    held = sorted(n for n in env if n.upper() in TOKEN_NAMES or any(w in n.upper() for w in SECRET_WORDS))
    shaped = sorted(n for n, v in env.items() if isinstance(v, str) and v.startswith(SECRET_VALUE_PREFIXES))
    if held or shaped:
        raise Refused(f"the floor has no LLM, yet its environment holds {held + [n for n in shaped if n not in held]}")


def bind(spec: Mapping[str, Any], env: Mapping[str, str]) -> tuple[dict, dict]:
    """The environment it was given (``env``, taken before isolate()), then the engine namespace; returns (the engine's
    scorer and tokenizer, the start record)."""
    check_env(env)
    store = Path(spec["unit_dir"]) / "store"
    if spec["stage"] == "write" and store.exists():
        raise Refused("the write stage needs a fresh store, and the unit store already exists")
    if spec["stage"] == "read" and not (store / "items.json").is_file():
        raise Refused("the read stage opens the write stage's items, and there are none")
    seal = B.check_seal(spec["unit_dir"], store) if spec["stage"] == "read" else None   # Q-45-5
    SG = sandbox_guard
    if SG.mode() != "sandbox" or SG.store() is None:
        raise Refused("sandbox_guard.isolate() did not run before the bind")
    import memory_hook as m  # noqa: PLC0415 - the engine namespace, _engine_recall.py included
    problems = check_anchor(m)
    if problems:
        raise Refused("; ".join(problems))
    scorer, tokens = m._bm25_scores, m._tokens
    params = inspect.signature(scorer).parameters
    rec = {"arm": ARM, "stage": spec["stage"], "stand": spec["stand"], "run": spec["run"], "unit": spec["unit"],
           "unit_store": str(store), "temp_store": str(SG.store()),
           "scorer": {"name": scorer.__name__,
                      "code": str(Path(scorer.__code__.co_filename).resolve().relative_to(REPO)).replace(os.sep, "/"),
                      "tokenizer": tokens.__name__, "is_engine_object": scorer.__globals__ is vars(m),
                      "k1": params["k1"].default, "b": params["b"].default},
           "returns": {"fewer_than_k": True,
                       "rule": "only items sharing a query term (a BM25 score > 0, _engine_recall._bm25_scores): fewer "
                               "than k when fewer items match; a vector arm returns k"},
           "lexical_morphology": m.LEXICAL_MORPHOLOGY, "seal": seal, "env_names": sorted(os.environ),
           "python": sys.version.split()[0]}
    return {"m": m, "scorer": scorer, "tokens": tokens}, rec


class Handler:
    def __init__(self, spec: Mapping[str, Any], ns: Mapping[str, Any], rec: Mapping[str, Any]) -> None:
        self.spec, self.rec = spec, rec
        self.m, self.scorer, self.tokens = ns["m"], ns["scorer"], ns["tokens"]
        self.stage = spec["stage"]
        self.store = Path(spec["unit_dir"]) / "store"
        self.items: dict[int, str] = {}
        self.reads = {"reads": 0, "items_returned": 0, "reads_short_of_k": 0}
        if self.stage == "read":
            raw = json.loads((self.store / "items.json").read_bytes().decode("utf-8"))
            self.items = {int(k): v for k, v in raw.items()}

    def _stage(self, want: str, op: str) -> None:
        if self.stage != want:
            raise RuntimeError(f"{op} belongs to the {want} stage; this process is the {self.stage} stage (Q25)")

    def hello(self) -> dict:
        return {"protocol": B.PROTOCOL, "system": ARM, "arm": ARM, "stage": self.stage, "llm_label": "none",
                "embedder": None, "python": self.rec["python"], "env_names": self.rec["env_names"], "start": self.rec}

    def write(self, item: dict, date: str | None = None) -> dict:
        self._stage("write", "write")
        t0 = time.time()
        idx = item["index"]
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx in self.items:
            raise ValueError(f"an item needs a new non-negative int index, got {idx!r}")
        if self.m._token_list(item_key(idx)):
            raise Refused(f"the key of item {idx} would add tokens to its document")
        self.items[idx] = item["text"]
        return {"op_id": item["item_id"], "item_sha256": B.text_sha256(item["text"]), "t0": t0, "t1": time.time()}

    def end_write(self) -> dict:
        self._stage("write", "end_write")
        t0 = time.time()
        self.store.mkdir(parents=True)
        data = json.dumps({str(k): v for k, v in sorted(self.items.items())}, ensure_ascii=False, sort_keys=True)
        (self.store / "items.json").write_bytes(data.encode("utf-8"))
        seal = B.write_seal(self.spec["unit_dir"], self.store, arm=ARM, run=self.spec["run"], unit=self.spec["unit"])
        return {"footprint": {"retrievable": len(self.items), "embedded": 0,
                              "chars": sum(len(v) for v in self.items.values())}, "seal": seal,
                "items_sha256": B.items_digest({i: B.text_sha256(t) for i, t in self.items.items()}),
                "t0": t0, "t1": time.time()}

    def read(self, qid: str, query: str, k: int) -> dict:
        self._stage("read", "read")
        t0 = time.time()
        keys = {item_key(i): i for i in self.items}
        cands = [(key, {"title": "", "desc": self.items[i], "prevention": ""}) for key, i in keys.items()]
        scores = self.scorer(self.tokens(query), cands)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], keys[kv[0]]))[:max(1, int(k))]
        items = [{"index": keys[key], "text": self.items[keys[key]], "rank": r, "score": s}
                 for r, (key, s) in enumerate(ranked, 1)]
        self.reads["reads"] += 1
        self.reads["items_returned"] += len(items)
        self.reads["reads_short_of_k"] += len(items) < int(k)
        return {"qid": qid, "items": items, "items_returned": len(items), "k": int(k), "t0": t0, "t1": time.time()}

    def counters(self) -> dict:
        return {"items": len(self.items), **self.reads}


class RefusedHandler:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __getattr__(self, op: str):
        if op not in B.OPS:
            raise AttributeError(op)

        def refuse(**_kw):
            raise Refused(f"the arm refused to start: {self.reason}")
        return refuse


def build(spec_path: str, env_at_start: Mapping[str, str]) -> Any:
    try:
        spec = load_spec(spec_path)
    except (Refused, OSError, ValueError, KeyError) as e:
        return RefusedHandler(f"{type(e).__name__}: {e}")
    try:
        ns, rec = bind(spec, env_at_start)
        handler = Handler(spec, ns, rec)
    except Exception as e:  # noqa: BLE001 - every failure to bind is a named refusal, recorded
        reason = f"{type(e).__name__}: {e}"
        Path(spec["record_path"]).write_bytes(json.dumps({"ok": False, "error": reason[:B.ERROR_MAX], "arm": ARM,
                                                          "stage": spec["stage"], "unit": spec["unit"]}).encode("utf-8"))
        return RefusedHandler(reason)
    Path(spec["record_path"]).write_bytes((json.dumps({"ok": True, **rec}, ensure_ascii=False, sort_keys=True,
                                                      indent=1) + "\n").encode("utf-8"))
    return handler


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: bm25_floor.py <spec.json>\n")
        sys.exit(2)
    ENV_AT_START = dict(os.environ)           # B-A2: what the harness gave the child, before isolate() changes it
    sandbox_guard.isolate()                   # before any project module is imported
    sys.exit(B.main_with(lambda: build(sys.argv[1], ENV_AT_START)))
