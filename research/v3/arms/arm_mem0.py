#!/usr/bin/env python3
"""PREREG-V3 TB4.6a (A6): the mem0 adapter - the product arm "mem0" and the retrieval arm "mem0-store" (infer=False), one
child per (arm, run, unit) and stage (Q25), speaking base.py's protocol. It runs in the mem0 venv from the arm's
runs-tree directory, beside byte-identical copies of base.py, _http_count.py and _ollama_pacer.py (ruling Q9 O-b); it
imports nothing of the repository. Named arm_mem0.py so that `import mem0` from that directory finds the venv's package,
never this file (Q-46-1).

    <mem0 venv python> -B arm_mem0.py <spec.json>

rev1 §2.2, as the auditor's Q-46-3 and Q-46-6 read it:
* mem0 writes through the DeepSeek provider at its proxy port (/u/<run>.<unit>/v1), model deepseek-flash; the key is the
  arm's proxy token, read by mem0 itself from DEEPSEEK_API_KEY - it never enters the config; the temperature is NOT set
  (the product's default; the value in effect is read from the proxy capture in the pilot, §5.5); no thinking route is
  known (§2.2.1), so none is sent here;
* one message per add(), role as given ("speaker_a" -> user, "speaker_b" -> assistant is the loader's mapping), content
  "<speaker>: <text>", the §5.3 header "Conversation from <YYYY-MM-DD>:" as its first line on a dated stand; timestamp=
  is never passed (mem0 OSS refuses it); user_id = the unit id;
* read: search(query, filters={"user_id": <unit>}, top_k=<k>, threshold=0.1), both explicit; the context is the memory
  lines, and mem0's add-time is never rendered;
* mem0-store: add(<the item's bytes>, infer=False, metadata={"index": i}); its LLM is never called - the config names an
  Ollama model on a closed loopback port, whose client does not connect at init, and llm_calls is counted (must be 0);
  the item bytes carry the §5.3 header already (the harness puts it there for every retrieval arm), so none is added;
* the embedder is Ollama with the v3 tag, 1024 dims. mem0's Ollama embedder LISTS the models at init and PULLS a missing
  one: before the product is built the adapter asks /api/tags itself and refuses by name when the tag is absent, so no
  pull can ever start (and a pull request, if one were made, refuses the arm);
* the store - qdrant on disk and the history database - lives under <unit>/store; end_write seals it (Q-45-5) and the
  read stage checks the seal before the product opens it.

The spec: arm, stage, stand, run, unit, unit_dir, port (the proxy port; None for mem0-store), embed_tag, ollama_url,
dated (does the stand carry dates), record_path. Counters: the HTTP counter (_http_count, installed inside the pacer),
the pacer's Ollama transport, the writes and their results, and for mem0 its LLM client's logical calls and tokens
(LLMUsage on llm.client.chat.completions.create, Q-AB-1; a mem0 without that client is refused; mem0-store: None), and
spaCy's state as mem0 left it (nlp_state: B-NLP NLP-3b, the auditor's PASSIVE check - read at every counters request,
never loaded: mem0.utils.spacy_models' four names by exactly NLP_NAMES and the model's distribution by its metadata),
and the unit's BM25 (M35, A5 - T34): the Qdrant store's encoder and bm25 slot read passively by BM25_NAMES,
keyword_search's answers counted around the store's own method, A5's three warning lines counted by a filter on the
store's logger that drops nothing, and the returned results whose id had a BM25 score above zero. The verdict is not
the adapter's: artifact.p0k judges each unit's block (the auditor's Q-M35-FAIL = (d)).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import base as B  # noqa: E402 - the protocol module, copied beside this file
import _http_count as HC  # noqa: E402 - the counting-only HTTP wrapper, copied beside this file

ARMS = ("mem0", "mem0-store")
STAGES = ("write", "read")
SPEC_KEYS = ("arm", "stage", "stand", "run", "unit", "unit_dir", "port", "embed_tag", "ollama_url", "dated",
             "record_path")
DEEPSEEK_MODEL = "deepseek-flash"
THRESHOLD = 0.1
EMBED_DIMS = 1024
TOKEN_NAME = "DEEPSEEK_API_KEY"
TOKEN_NAMES = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN")
NO_LLM = {"provider": "ollama", "config": {"model": "nvt3-no-llm", "ollama_base_url": "http://127.0.0.1:0"}}
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Refused(RuntimeError):
    """The arm may not start, or may not take this request."""


def load_spec(path: str | os.PathLike) -> dict:
    spec = json.loads(Path(path).read_bytes().decode("utf-8"))
    missing = [k for k in SPEC_KEYS if k not in spec]
    extra = sorted(set(spec) - set(SPEC_KEYS))
    if missing or extra:
        raise Refused(f"the spec lacks {missing} or carries unknown keys {extra}")
    if spec["arm"] not in ARMS or spec["stage"] not in STAGES:
        raise Refused(f"unknown arm {spec['arm']!r} or stage {spec['stage']!r}")
    if not (_ID.fullmatch(str(spec["run"])) and _ID.fullmatch(str(spec["unit"]))):
        raise Refused("run and unit ids are [A-Za-z0-9_-]{1,64}, no dots (Q3)")
    if not Path(spec["unit_dir"]).is_absolute():
        raise Refused("unit_dir is not an absolute path")
    if (spec["arm"] == "mem0") != isinstance(spec["port"], int):
        raise Refused("mem0 writes through its proxy port; mem0-store has none")
    return spec


def proxy_base(spec: Mapping[str, Any]) -> str:
    return f"http://127.0.0.1:{spec['port']}/u/{spec['run']}.{spec['unit']}/v1"


def content(item: Mapping[str, Any], date: str | None, dated: bool) -> str:
    """The text mem0 is given for one message: "<speaker>: <text>", after the §5.3 header "Conversation from
    <YYYY-MM-DD>:" on a dated stand. run_v3.op_text prices exactly these bytes (F-C6-1)."""
    text = f"{item['speaker']}: {item['text']}"
    if dated:
        if not date:
            raise ValueError("a dated stand's write carries its date (§5.3 header)")
        text = f"Conversation from {date[:10]}:\n{text}"
    return text


def mem0_config(spec: Mapping[str, Any]) -> dict:
    """The product's config: no temperature, no key, no thinking field - only what rev1 §2.2 declares."""
    store = Path(spec["unit_dir"]) / "store"
    llm = ({"provider": "deepseek", "config": {"model": DEEPSEEK_MODEL, "deepseek_base_url": proxy_base(spec)}}
           if spec["arm"] == "mem0" else NO_LLM)
    return {"llm": llm,
            "embedder": {"provider": "ollama", "config": {"model": spec["embed_tag"],
                                                          "ollama_base_url": spec["ollama_url"],
                                                          "embedding_dims": EMBED_DIMS}},
            "vector_store": {"provider": "qdrant", "config": {"collection_name": "nvt3", "path": str(store / "qdrant"),
                                                              "embedding_model_dims": EMBED_DIMS, "on_disk": True}},
            "history_db_path": str(store / "history.db")}


#: B-NLP NLP-3b (the auditor's passive check, Q-NLP-2): the names read from mem0's spacy_models after the adds. probe_a8's
#: M0_SOURCE facts must find exactly these in the pinned source (m0_nlp_active checks the two agree).
NLP_NAMES = {"module": "mem0.utils.spacy_models", "full": "_nlp_full", "lemma": "_nlp_lemma",
             "failed_full": "_load_failed_full", "failed_lemma": "_load_failed_lemma", "model": "en_core_web_sm"}
_MISSING = object()


def _installed(name: str) -> bool | None:
    """The model's distribution, by its metadata only (spacy.util.is_package's own test, without importing spaCy)."""
    import importlib.metadata as md  # noqa: PLC0415 - read at the counters request only
    try:
        md.distribution(name)
        return True
    except md.PackageNotFoundError:
        return False
    except Exception:  # noqa: BLE001 - an unreadable metadata is unknown, never True
        return None


def nlp_state(modules: Mapping | None = None, installed=None) -> dict:
    """spaCy's state as mem0 left it - READ, never loaded (the auditor: a load here would move spaCy's cost out of the
    measured add): whether spacy_models was imported, its four names by exactly NLP_NAMES (a name the module does not
    have is None, never a default; a failed flag that is not a bool is None), and the model installed or not."""
    modules = sys.modules if modules is None else modules
    mod = modules.get(NLP_NAMES["module"])

    def get(key: str) -> Any:
        return getattr(mod, NLP_NAMES[key], _MISSING) if mod is not None else _MISSING
    full, lemma = get("full"), get("lemma")
    out: dict = {"names": {k: v for k, v in NLP_NAMES.items() if k != "module"}, "module": mod is not None,
                 "nlp_full": None if full is _MISSING else full is not None,
                 "nlp_lemma": None if lemma is _MISSING else lemma is not None}
    for key in ("failed_full", "failed_lemma"):
        v = get(key)
        out[key] = v if isinstance(v, bool) else None
    out["is_package"] = (installed or _installed)(NLP_NAMES["model"])
    return out


#: M35 (A5, T34): the names read from mem0 2.2.0's Qdrant store (mem0/vector_stores/qdrant.py, read as data) - the
#: encoder (:86; _get_bm25_encoder :93-109 loads it lazily: None not tried, False failed and sticky, else the encoder),
#: the slot (:90, set by create_col :137-163), keyword_search (:454-484: None without the slot, without the encoder or
#: on an exception) and the store's logger (:26), whose warnings are A5's three lines.
BM25_NAMES = {"store": "vector_store", "encoder": "_bm25_encoder", "slot": "_has_bm25_slot", "search": "keyword_search",
              "logger": "mem0.vector_stores.qdrant"}
BM25_LINES = ("fastembed not installed", "Failed to load BM25 encoder", "predates v3 hybrid search")


class BM25Watch:
    """M35: the unit's BM25, counted in the child - keyword_search's answers around the store's own method (the same
    answer object back, the arguments as given, an exception counted and re-raised as it was) and A5's three lines by
    a filter on the store's logger that drops nothing, so the product's output is unchanged. The log counts only while
    that logger is enabled for WARNING, not disabled and carries the filter (log_watched)."""

    def __init__(self) -> None:
        self.ks = {"calls": 0, "not_none": 0, "hits": 0, "positive_hits": 0, "raised": 0}
        self.lines = {x: 0 for x in BM25_LINES}
        self.results_positive = self.reads_positive = 0
        self._last_positive: set = set()
        self.logger: logging.Logger | None = None
        self._lock = threading.Lock()

    def _count_line(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - a record whose arguments do not format is read by its template
            msg = str(record.msg)
        with self._lock:
            for x in BM25_LINES:
                if x in msg:
                    self.lines[x] += 1
        return True

    def attach_log(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger if logger is not None else logging.getLogger(BM25_NAMES["logger"])
        self.logger.addFilter(self._count_line)

    def log_watched(self) -> bool:
        lg = self.logger
        return bool(lg is not None and not lg.disabled and lg.isEnabledFor(logging.WARNING)
                    and self._count_line in lg.filters)

    @staticmethod
    def _id_score(point: Any) -> tuple:
        if isinstance(point, Mapping):
            return point.get("id"), point.get("score")
        return getattr(point, "id", None), getattr(point, "score", None)

    def wrap(self, store: Any) -> None:
        inner = getattr(store, BM25_NAMES["search"])

        def keyword_search(*args, **kwargs):
            with self._lock:
                self.ks["calls"] += 1
            try:
                res = inner(*args, **kwargs)
            except BaseException:
                with self._lock:
                    self.ks["raised"] += 1
                    self._last_positive = set()
                raise
            pos = set()
            for point in res if res is not None else ():
                pid, score = self._id_score(point)
                if isinstance(score, (int, float)) and not isinstance(score, bool) and score > 0:
                    pos.add(str(pid))
            with self._lock:
                if res is not None:
                    self.ks["not_none"] += 1
                    self.ks["hits"] += len(res)
                    self.ks["positive_hits"] += len(pos)
                self._last_positive = pos
            return res
        setattr(store, BM25_NAMES["search"], keyword_search)

    def note_results(self, ids) -> None:
        """A read's returned results: those whose id the read's own keyword_search scored above zero (published)."""
        with self._lock:
            n = sum(1 for i in ids if str(i) in self._last_positive)
            self.results_positive += n
            self.reads_positive += bool(n)
            self._last_positive = set()

    def snapshot(self) -> dict:
        watched = self.log_watched()
        with self._lock:
            return {"keyword_search": dict(self.ks), "lines": dict(self.lines), "log_watched": watched,
                    "results_bm25_positive": self.results_positive, "reads_bm25_positive": self.reads_positive}


def bm25_state(mem: Any, watch: BM25Watch) -> dict:
    """The unit's BM25 block (M35): the store's encoder and slot READ by BM25_NAMES - an attribute the store does not
    have is unknown (never a default): encoder loaded / not-tried (None) / failed (False) / unknown; the slot the
    store's bool or None - with the watch's counts."""
    store = getattr(mem, BM25_NAMES["store"], _MISSING)
    enc = _MISSING if store is _MISSING else getattr(store, BM25_NAMES["encoder"], _MISSING)
    slot = _MISSING if store is _MISSING else getattr(store, BM25_NAMES["slot"], _MISSING)
    encoder = ("unknown" if enc is _MISSING else "not-tried" if enc is None else "failed" if enc is False else "loaded")
    return {"names": dict(BM25_NAMES), "encoder": encoder, "slot": slot if isinstance(slot, bool) else None,
            **watch.snapshot()}


class LLMUsage:
    """Q-AB-1 (the auditor's O-a): mem0's own LLM client counted in the child - the logical calls and the tokens its
    SDK response reports (response.usage), the same in every leg of the §4.6 A/B, and mem0's K87 check-2 source
    (branch (b)). The counter calls the SDK's create with exactly the arguments it was given, hands back the SDK's own
    response object, and re-raises an SDK exception as it was - a failed call is counted too."""

    def __init__(self) -> None:
        self.calls = self.failed = self.no_usage = self.prompt_tokens = self.completion_tokens = 0
        self._lock = threading.Lock()

    def wrap(self, completions: Any) -> None:
        inner = completions.create

        def create(*args, **kwargs):
            with self._lock:
                self.calls += 1
            try:
                resp = inner(*args, **kwargs)
            except BaseException:
                with self._lock:
                    self.failed += 1
                raise
            u = getattr(resp, "usage", None)
            with self._lock:
                if u is None:
                    self.no_usage += 1
                else:
                    self.prompt_tokens += int(getattr(u, "prompt_tokens", 0) or 0)
                    self.completion_tokens += int(getattr(u, "completion_tokens", 0) or 0)
            return resp
        completions.create = create

    def snapshot(self) -> dict:
        with self._lock:
            return {"calls": self.calls, "failed": self.failed, "no_usage": self.no_usage,
                    "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens}


def _tag_present(tags: Mapping[str, Any], tag: str) -> bool:
    want = {tag, tag if ":" in tag else f"{tag}:latest"}
    names = {m.get(k) for m in tags.get("models") or [] for k in ("name", "model")}
    return bool(want & names)


def declared(spec: Mapping[str, Any]) -> dict:
    """The §2.2 constants of this arm, for arm_decl."""
    product = spec["arm"] == "mem0"
    return {"date_route": ("header" if spec["dated"] else "none") if product else "none (item bytes, §5.3 by the harness)",
            "renderer": {"name": "mem0 memory lines, no add-time" if product else "index and item bytes (#<index> by the harness)"},
            "threshold": THRESHOLD, "top_k": "the point's k, explicit",
            "namespace": "user_id = unit id; qdrant and history.db under <unit>/store",
            "write_granularity": "one message per add()" if product else "one item per add(), infer=False",
            "llm_params": ({"temperature": "product default (value in effect: the proxy capture, §5.5)",
                            "max_tokens": "product default", "thinking_route": "none known (§2.2.1)"}
                           if product else {"llm": None, "llm_calls": "must be 0"}),
            "tools_allowed": [], "store_persistence": "disk"}


def bind(spec: Mapping[str, Any], env: Mapping[str, str]) -> tuple[Any, dict]:
    arm = spec["arm"]
    unit_dir = Path(spec["unit_dir"])
    store = unit_dir / "store"
    rec: dict[str, Any] = {"arm": arm, "stage": spec["stage"], "stand": spec["stand"], "run": spec["run"],
                           "unit": spec["unit"], "unit_store": str(store), "declared": declared(spec)}
    token = env.get(TOKEN_NAME)
    if arm == "mem0":
        if not token or not token.startswith("nvt3-"):
            raise Refused(f"{TOKEN_NAME} does not hold the arm's proxy token (nvt3-...)")
    else:
        held = [n for n in TOKEN_NAMES if n in env]
        if held:
            raise Refused(f"mem0-store has no LLM, yet {held} is set")
    if spec["stage"] == "write" and store.exists():
        raise Refused("the write stage needs a fresh store, and the unit store already exists")
    if spec["stage"] == "read":
        if not store.is_dir():
            raise Refused("the read stage opens the write stage's store, and there is none")
        rec["seal"] = B.check_seal(unit_dir, store)          # Q-45-5, before the product opens the store
    ollama_port = int(spec["ollama_url"].rsplit(":", 1)[1].split("/")[0])
    rec["http_doors"] = HC.install(proxy_port=spec["port"], ollama_ports=(ollama_port,))
    import _ollama_pacer as P  # noqa: PLC0415 - copied beside this file; installed AFTER the counter (Q-46-4)
    P.install("observe")               # R-EMBED-PATH: the proxy leg paces and retries - one layer for every arm
    P.set_route("127.0.0.1", ollama_port)        # the leg is the one route; any other Ollama is direct_calls
    rec["pacer"] = P._MODE  # the installed pacer's own mode, never a copy of the intention (B-PACER-REC)
    rec["ollama_route"] = spec["ollama_url"]
    with urllib.request.urlopen(spec["ollama_url"].rstrip("/") + "/api/tags", timeout=30) as r:
        tags = json.loads(r.read() or b"{}")
    if not _tag_present(tags, spec["embed_tag"]):
        raise Refused(f"the embed tag {spec['embed_tag']!r} is not in Ollama; mem0's embedder would pull it")
    import mem0  # noqa: PLC0415 - the venv's product (Q-46-1: never this file)
    rec["product"] = {"name": "mem0ai", "version": getattr(mem0, "__version__", None),
                      "file": str(Path(mem0.__file__).resolve())}
    if HERE in Path(mem0.__file__).resolve().parents:
        raise Refused("import mem0 found a module in the arm's directory, not the venv's package")
    from mem0 import Memory  # noqa: PLC0415
    store.mkdir(parents=True, exist_ok=True)
    cfg = mem0_config(spec)
    rec["config"] = cfg
    bm25 = BM25Watch()
    bm25.attach_log()                  # M35: before the product is built - create_col warns at init (qdrant.py:147)
    mem = Memory.from_config(cfg)
    vstore = getattr(mem, BM25_NAMES["store"], None)
    if not callable(getattr(vstore, BM25_NAMES["search"], None)):
        raise Refused("mem0 has no vector_store.keyword_search - the unit's BM25 (M35) could not be counted")
    bm25.wrap(vstore)
    rec["bm25_watch"] = f"{BM25_NAMES['store']}.{BM25_NAMES['search']} and the logger {BM25_NAMES['logger']} (M35)"
    pulls = HC.total(HC.snapshot(), "ollama:pull")
    if pulls:
        raise Refused(f"the product sent {pulls} pull request(s) to Ollama while it was built")
    usage = None
    if arm == "mem0":
        completions = getattr(getattr(getattr(getattr(mem, "llm", None), "client", None), "chat", None),
                              "completions", None)
        if not callable(getattr(completions, "create", None)):
            raise Refused("mem0's LLM has no OpenAI client (llm.client.chat.completions.create) - the K87 check-2 "
                          "source (Q-AB-1) would be missing")
        usage = LLMUsage()
        usage.wrap(completions)
    rec["llm_usage"] = "mem0.llm.client.chat.completions.create, response.usage" if usage else None
    rec["env_names"] = sorted(env)
    rec["python"] = sys.version.split()[0]
    return {"mem": mem, "pacer": P, "usage": usage, "bm25": bm25}, rec


class Handler:
    def __init__(self, spec: Mapping[str, Any], ns: Mapping[str, Any], rec: Mapping[str, Any]) -> None:
        self.spec, self.rec = spec, rec
        self.mem, self.pacer, self.usage, self.bm25 = ns["mem"], ns["pacer"], ns.get("usage"), ns["bm25"]
        self.arm, self.stage, self.unit = spec["arm"], spec["stage"], spec["unit"]
        self.store = Path(spec["unit_dir"]) / "store"
        self.item_shas: dict[int, str] = {}
        self.writes = {"adds": 0, "results": 0, "empty": 0}
        self.reads = {"reads": 0, "items_returned": 0}

    def _stage(self, want: str, op: str) -> None:
        if self.stage != want:
            raise RuntimeError(f"{op} belongs to the {want} stage; this process is the {self.stage} stage (Q25)")

    def hello(self) -> dict:
        return {"protocol": B.PROTOCOL, "system": "mem0", "arm": self.arm, "stage": self.stage,
                "version": self.rec["product"]["version"], "python": self.rec["python"],
                "llm_label": f"deepseek:{DEEPSEEK_MODEL}" if self.arm == "mem0" else None,
                "embedder": self.spec["embed_tag"], "env_names": self.rec["env_names"], "start": self.rec}

    def write(self, item: dict, date: str | None = None) -> dict:
        self._stage("write", "write")
        t0 = time.time()
        if self.arm == "mem0":
            if item["role"] not in ("user", "assistant"):
                raise ValueError(f"a mem0 message is from user or assistant, got {item['role']!r}")
            text = content(item, date, self.spec["dated"])
            given = {"text_sha256": B.text_sha256(text)}
            res = self.mem.add([{"role": item["role"], "content": text}], user_id=self.unit, infer=True)
        else:
            idx = item["index"]
            if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx in self.item_shas:
                raise ValueError(f"an item needs a new non-negative int index, got {idx!r}")
            self.item_shas[idx] = B.text_sha256(item["text"])
            given = {"item_sha256": self.item_shas[idx]}
            res = self.mem.add(item["text"], user_id=self.unit, infer=False, metadata={"index": idx})
        results = (res or {}).get("results", []) if isinstance(res, dict) else (res or [])
        self.writes["adds"] += 1
        self.writes["results"] += len(results)
        self.writes["empty"] += not results
        return {"op_id": item["item_id"], **given, "results": len(results), "t0": t0, "t1": time.time()}

    def end_write(self) -> dict:
        self._stage("write", "end_write")
        t0 = time.time()
        got = self.mem.get_all(filters={"user_id": self.unit})
        memories = got.get("results", []) if isinstance(got, dict) else (got or [])
        seal = B.write_seal(self.spec["unit_dir"], self.store, arm=self.arm, run=self.spec["run"], unit=self.unit)
        out = {"footprint": {"retrievable": len(memories), "chars": sum(len(m.get("memory") or "") for m in memories)},
               "seal": seal, "t0": t0, "t1": time.time()}
        if self.arm == "mem0-store":
            out["items_sha256"] = B.items_digest(self.item_shas)
        return out

    def read(self, qid: str, query: str, k: int) -> dict:
        self._stage("read", "read")
        t0 = time.time()
        res = self.mem.search(query, filters={"user_id": self.unit}, top_k=int(k), threshold=THRESHOLD)
        hits = res.get("results", []) if isinstance(res, dict) else (res or [])
        self.bm25.note_results([h.get("id") for h in hits])          # M35: the published BM25-positive count
        items = []
        for rank, h in enumerate(hits, 1):
            if self.arm == "mem0":
                items.append({"text": h.get("memory") or "", "rank": rank})
            else:
                idx = (h.get("metadata") or {}).get("index")
                if not isinstance(idx, int) or isinstance(idx, bool):
                    raise RuntimeError(f"a mem0-store hit carries no item index: {str(h.get('id'))[:40]}")
                items.append({"index": idx, "text": h.get("memory") or "", "rank": rank})
        self.reads["reads"] += 1
        self.reads["items_returned"] += len(items)
        return {"qid": qid, "items": items, "items_returned": len(items), "k": int(k), "t0": t0, "t1": time.time()}

    def counters(self) -> dict:
        snap = HC.snapshot()
        transport: dict = {}
        self.pacer.attach(transport)
        return {"http": snap, "llm_calls": HC.total(snap, "proxy:chat") + HC.total(snap, "ollama:generate"),
                "llm_usage": self.usage.snapshot() if self.usage is not None else None,
                "writes": dict(self.writes), **self.reads, "ollama_transport": transport.get("ollama_transport"),
                "nlp": nlp_state(), "bm25": bm25_state(self.mem, self.bm25)}


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
        _record(spec["record_path"], {"ok": False, "error": reason[:B.ERROR_MAX], "arm": spec["arm"],
                                      "stage": spec["stage"], "unit": spec["unit"]})
        return RefusedHandler(reason)
    _record(spec["record_path"], {"ok": True, **rec})
    return handler


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: arm_mem0.py <spec.json>\n")
        sys.exit(2)
    ENV_AT_START = dict(os.environ)
    sys.exit(B.main_with(lambda: build(sys.argv[1], ENV_AT_START)))
