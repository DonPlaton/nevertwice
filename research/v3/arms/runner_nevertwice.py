#!/usr/bin/env python3
"""PREREG-V3 TB4.5b (A6): the child runner of our four arms - nevertwice, nevertwice-rawtext, nevertwice-ablation and
nevertwice-ranker - one process per (arm, run, unit) and stage (ruling Q25: the write stage's process exits after
end_write; the read stage opens the same on-disk store in a new process), speaking base.py's protocol.

    python -B research/v3/arms/runner_nevertwice.py <spec.json>

The spec (written by the harness, never inside the unit directory) holds arm, stage, stand, run, unit, unit_dir,
port, embed_tag, extract_temp, max_transcript, s7 and record_path. The declared variables are computed from it by
``declared_env`` - the same function the harness uses to build the child's environment - and compared with that
environment before anything else happens. The proxy token travels in the environment only (rev1 §2.6.2, C11).

The binding order is the auditor's Q-45-1 O-c with his 09:27 correction, asserted in code (``bind``):
1. the environment the harness built - snapshotted in __main__ before isolate() touches it - carries exactly the
   declared values; an LLM arm carries a proxy token (nvt3-, never sk-), the ranker none;
2. ``sandbox_guard.isolate()`` (in __main__, the module-level call tools/check_sandbox.py requires; bind refuses unless
   it ran): the scrub, NEVERTWICE_DOTENV=explicit, git housekeeping off, the real stores recorded;
3. NEVERTWICE_VAULT and NEVERTWICE_HOME := <unit>/store, NEVERTWICE_PROJECTS_ROOT := <unit>/transcripts, and the
   declared variables set again - isolate() scrubs NEVERTWICE_EMBED_MODEL and sets NEVERTWICE_CLOUD=none, which rev1
   §2.2 replaces with deepseek; its NEVERTWICE_XRERANK=0 equals rev1's "off" (§2.4), is kept and recorded, and every
   read passes xrerank=False;
4. only config is loaded from the project (asserted), then ``importlib.reload(config)``: the product resolves its
   store itself, from the environment, as a user's install does (never _rebase_vault, "FOR TESTS/TOOLING ONLY");
5. the engine is imported; POSITIVE checks (``positive_checks``) - config.VAULT, the engine's VAULT (memory_hook runs
   _engine_config.py in its own namespace) and the VAULT of every loaded project module equal the unit store,
   PROJECTS_ROOT is the unit's transcripts directory, no project Path constant points into isolate()'s temporary
   store, ``verify_no_live_paths()`` holds, and the backend is the declared one; any miss refuses the arm by name
   (verify() is not called after the move: it would refuse by construction);
6. the ablation installs its variant (ablation_c1.enable) before any write; the pacer is installed;
The store between the stages (Q-45-5): end_write seals it (base.write_seal: its path and tree digest, beside it); the
read stage checks the seal before the engine is imported, and another path or another byte refuses the arm.

7. the start record (written to record_path, and returned by hello): isolate()'s temporary store, the unit store, the
   project modules at the reload, every NEVERTWICE_* value and the declared ones - a name holding KEY, TOKEN, SECRET or
   PASSWORD, in any case, as "<set>" only (the 09:27 correction) - and each check passed.

A refused bind answers every request ok:false with its reason; the record says ok: false and why.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import re
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

LLM_ARMS = ("nevertwice", "nevertwice-rawtext", "nevertwice-ablation")
RANKER = "nevertwice-ranker"
ARMS = (*LLM_ARMS, RANKER)
STAGES = ("write", "read")
DEEPSEEK_MODEL = "deepseek-flash"
TOKEN_NAME = "DEEPSEEK_API_KEY"
TOKEN_PREFIX = "nvt3-"
EXTRACT_NUM_PREDICT = 4096                   # B1, rev1 §2.2 "Output cap"
SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")     # Q3: a run or unit id has no dot - /u/<run>.<unit> splits on it
SPEC_KEYS = ("arm", "stage", "stand", "run", "unit", "unit_dir", "port", "ollama_port", "embed_tag", "extract_temp",
             "max_transcript", "s7", "record_path")
#: R-EMBED-PATH: the engine's three Ollama URLs (_engine_config.py:317-319), each on the arm's proxy leg.
OLLAMA_ROUTES = (("OLLAMA_URL", "OLLAMA_URL", "/api/generate"), ("OLLAMA_EMBED_URL", "OLLAMA_EMBED_URL", "/api/embed"),
                 ("OLLAMA_TAGS_URL", "OLLAMA_TAGS_URL", "/api/tags"))


class Refused(RuntimeError):
    """The arm may not start: a declared value, the store binding or the backend is not what rev1 says."""


# ── the declared variables (the harness builds the child's environment with the same function) ──────────────────

def proxy_url(port: int, run: str, unit: str) -> str:
    """The arm's DeepSeek URL: its proxy port, the unit's path (ruling Q3), the chat-completions route."""
    for what, v in (("run", run), ("unit", unit)):
        if not _ID.fullmatch(str(v)):
            raise ValueError(f"the {what} id must match [A-Za-z0-9_-]{{1,64}} (no dots): {v!r}")
    if not (isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536):
        raise ValueError(f"the proxy port must be an int in 1..65535, got {port!r}")
    return f"http://127.0.0.1:{port}/u/{run}.{unit}/v1/chat/completions"


def leg_url(ollama_port: int, run: str, unit: str, path: str) -> str:
    """R-EMBED-PATH: an Ollama endpoint on the arm's proxy leg, tagged with the unit (/u/<run>.<unit>, Q3)."""
    base = proxy_url(ollama_port, run, unit).rsplit("/v1/chat/completions", 1)[0]
    return base + path


def declared_env(arm: str, *, run: str, unit: str, port: int | None, embed_tag: str, ollama_port: int | None = None,
                 extract_temp: float | None = None, max_transcript: int | None = None) -> dict[str, str]:
    """The NEVERTWICE_* and DeepSeek variables rev1 §2.2 declares for ``arm`` - everything but the proxy token - and
    the engine's three Ollama URLs on the arm's proxy leg (R-EMBED-PATH: every arm embeds through its leg)."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    if not (isinstance(embed_tag, str) and embed_tag.strip()):
        raise ValueError("the embed tag is required (rev1 §5.1)")
    if ollama_port is None:
        raise ValueError("the arm embeds through Ollama: its proxy leg's port is required - never the direct 11434 "
                         "(R-EMBED-PATH)")
    env = {"NEVERTWICE_EMBED_MODEL": embed_tag, "NEVERTWICE_XRERANK": "0"}
    env.update({name: leg_url(ollama_port, run, unit, path) for name, _attr, path in OLLAMA_ROUTES})
    if arm in LLM_ARMS:
        env.update({"NEVERTWICE_CLOUD": "deepseek", "NEVERTWICE_DEEPSEEK_MODEL": DEEPSEEK_MODEL,
                    "DEEPSEEK_URL": proxy_url(port, run, unit), "NEVERTWICE_CLOUD_FALLBACK": "0"})
        if extract_temp is not None:
            env["NEVERTWICE_EXTRACT_TEMP"] = repr(float(extract_temp))
    else:
        if port is not None or extract_temp is not None:
            raise ValueError("the ranker has no LLM: no proxy port, no extraction temperature")
        env["NEVERTWICE_CLOUD"] = "none"
    if arm == "nevertwice-ablation":
        if not (isinstance(max_transcript, int) and not isinstance(max_transcript, bool) and max_transcript > 0):
            raise ValueError("the ablation's window is the unit's length in characters (Q14)")
        env["NEVERTWICE_MAX_TRANSCRIPT"] = str(max_transcript)
    elif max_transcript is not None:
        raise ValueError("only the ablation sets the transcript window")
    return env


def masked(name: str, value: str) -> str:
    """The 09:27 correction: a variable whose name holds KEY, TOKEN, SECRET or PASSWORD is recorded as "<set>"."""
    return "<set>" if any(w in name.upper() for w in SECRET_WORDS) else value


def load_spec(path: str | os.PathLike) -> dict:
    spec = json.loads(Path(path).read_bytes().decode("utf-8"))
    missing = [k for k in SPEC_KEYS if k not in spec]
    extra = sorted(set(spec) - set(SPEC_KEYS))
    if missing or extra:
        raise Refused(f"the spec lacks {missing} or carries unknown keys {extra}")
    if spec["arm"] not in ARMS or spec["stage"] not in STAGES:
        raise Refused(f"unknown arm {spec['arm']!r} or stage {spec['stage']!r}")
    if not Path(spec["unit_dir"]).is_absolute():
        raise Refused("unit_dir is not an absolute path")
    if spec["s7"] and spec["arm"] != "nevertwice":
        raise Refused("only the nevertwice arm reads S7 through the hook's reader (rawtext gets render_text)")
    return spec


# ── the binding (Q-45-1 O-c) ───────────────────────────────────────────────────────────────────────────────────

def _same(a, b) -> bool:
    """B-SEAL83: canonical paths (realpath) - the engine may resolve what the harness wrote by an 8.3 name."""
    return os.path.normcase(os.path.realpath(os.fspath(a))) == os.path.normcase(os.path.realpath(os.fspath(b)))


def _inside(path, root) -> bool:
    a = os.path.normcase(os.path.realpath(os.fspath(path)))
    r = os.path.normcase(os.path.realpath(os.fspath(root)))
    return a == r or a.startswith(r.rstrip("\\/") + os.sep)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def check_token(arm: str, token: str | None) -> None:
    """An LLM arm carries its proxy token (nvt3-..., so never an sk- key); the ranker carries none."""
    if arm in LLM_ARMS:
        if not token or not token.startswith(TOKEN_PREFIX):
            raise Refused(f"{TOKEN_NAME} does not hold the arm's proxy token (nvt3-...)")
    elif token is not None:
        raise Refused(f"the ranker has no LLM, yet {TOKEN_NAME} is set")


def only_config(loaded: list[str]) -> None:
    """Step 4's assertion: nothing of the project but config was imported before the reload."""
    if loaded != ["config"]:
        raise Refused(f"project modules other than config were loaded before the reload: {loaded}")


def positive_checks(spec: Mapping[str, Any], declared: Mapping[str, str], token: str | None, *, cfg, m, api, SG,
                    store: Path, transcripts: Path, environ: Mapping[str, str]) -> list[str]:
    """Step 5: every way the store, the paths or the backend can differ from rev1, each named."""
    arm = spec["arm"]
    problems = []
    if api.m is not m:
        problems.append("api drives another engine namespace than memory_hook")
    for name, val in (("config.VAULT", cfg.VAULT), ("the engine's VAULT (_engine_config.py)", m.VAULT)):
        if not _same(val, store):
            problems.append(f"{name} is not the unit store")
    for name, mod in SG._loaded_project_modules().items():
        vault = getattr(mod, "VAULT", None)
        if vault is not None and not _same(vault, store):
            problems.append(f"{name}.VAULT is not the unit store")
        for attr, value in list(vars(mod).items()):
            if not attr.startswith("_") and isinstance(value, Path) and _inside(value, SG.store()):
                problems.append(f"{name}.{attr} points into isolate()'s temporary store")
    if not _same(cfg.PROJECTS_ROOT, transcripts):
        problems.append("config.PROJECTS_ROOT is not the unit's transcripts directory")
    try:
        SG.verify_no_live_paths()
    except SG.SandboxEscape as e:
        problems.append(f"verify_no_live_paths: {str(e).splitlines()[0]}")
    if m.EMBED_MODEL != spec["embed_tag"]:
        problems.append("EMBED_MODEL is not the declared tag")
    for name, attr, _path in OLLAMA_ROUTES:                           # R-EMBED-PATH: all three, not only embed
        if getattr(m, attr, None) != declared.get(name):
            problems.append(f"the engine's {attr} is not the declared leg URL")
    if environ.get("NEVERTWICE_XRERANK") != "0":
        problems.append("NEVERTWICE_XRERANK is not 0 (rev1: off)")
    if arm in LLM_ARMS:
        want_temp = 0.2 if spec["extract_temp"] is None else float(spec["extract_temp"])
        for what, ok in (("ACTIVE_CLOUD is not deepseek", m.ACTIVE_CLOUD == "deepseek"),
                         ("DEEPSEEK_MODEL is not deepseek-flash", m.DEEPSEEK_MODEL == DEEPSEEK_MODEL),
                         ("DEEPSEEK_URL is not the declared proxy URL", m.DEEPSEEK_URL == declared["DEEPSEEK_URL"]),
                         ("the DeepSeek key read by the engine is not the proxy token",
                          m.provider_key("deepseek") == token),
                         ("the local fallback is on", m.cloud_fallback_enabled() is False),
                         ("extract_temperature() is not the declared value", m.extract_temperature() == want_temp),
                         ("EXTRACT_NUM_PREDICT is not 4096", m.EXTRACT_NUM_PREDICT == EXTRACT_NUM_PREDICT)):
            if not ok:
                problems.append(what)
    elif m.ACTIVE_CLOUD != "none":
        problems.append("the ranker's ACTIVE_CLOUD is not none")
    if arm == "nevertwice-ablation" and m.MAX_TRANSCRIPT_CHARS != spec["max_transcript"]:
        problems.append("MAX_TRANSCRIPT_CHARS is not the unit's length")
    return problems


def bind(spec: Mapping[str, Any], env: Mapping[str, str]) -> tuple[dict, dict]:
    """Steps 1-7 on the environment the harness built (``env``, the snapshot taken before isolate()). Returns (the
    engine namespaces, the start record); raises Refused by name."""
    arm, stage = spec["arm"], spec["stage"]
    unit_dir = Path(spec["unit_dir"])
    store, transcripts = unit_dir / "store", unit_dir / "transcripts"
    rec: dict[str, Any] = {"arm": arm, "stage": stage, "stand": spec["stand"], "run": spec["run"],
                           "unit": spec["unit"], "unit_store": str(store), "projects_root": str(transcripts),
                           "checks": []}
    # 1. the environment carries the declared values
    declared = declared_env(arm, run=spec["run"], unit=spec["unit"], port=spec["port"], embed_tag=spec["embed_tag"],
                            ollama_port=spec["ollama_port"], extract_temp=spec["extract_temp"],
                            max_transcript=spec["max_transcript"])
    rec["declared"] = {k: masked(k, v) for k, v in sorted(declared.items())}
    off = sorted(k for k, v in declared.items() if env.get(k) != v)
    if off:
        raise Refused(f"the environment's {off} differ from the arm's declared values")
    token = env.get(TOKEN_NAME)
    check_token(arm, token)
    rec["checks"].append("declared values and token")
    if stage == "write" and store.exists():
        raise Refused("the write stage needs a fresh store, and the unit store already exists")
    if stage == "read" and not store.is_dir():
        raise Refused("the read stage opens the write stage's store, and there is none")
    if stage == "read":
        rec["seal"] = B.check_seal(unit_dir, store)      # Q-45-5: the sealed path and bytes, before the product opens it
    # 2. isolate() ran in __main__, before any project import
    SG = sandbox_guard
    if SG.mode() != "sandbox" or SG.store() is None:
        raise Refused("sandbox_guard.isolate() did not run before the bind")
    rec["temp_store"] = str(SG.store())
    iso = {k: os.environ.get(k) for k in declared}
    # 3. the unit's store, the transcripts root, the declared values again
    store.mkdir(parents=True, exist_ok=True)
    transcripts.mkdir(parents=True, exist_ok=True)
    os.environ["NEVERTWICE_VAULT"] = os.environ["NEVERTWICE_HOME"] = str(store)
    os.environ["NEVERTWICE_PROJECTS_ROOT"] = str(transcripts)
    os.environ.update(declared)
    rec["isolate_values"] = {k: (None if v is None else masked(k, v)) for k, v in sorted(iso.items())
                             if v != declared[k]}
    rec["kept_from_isolate"] = {k: masked(k, v) for k, v in sorted(iso.items()) if v == declared[k]}
    # 4. only config is loaded; the product resolves its store itself
    loaded = sorted(SG._loaded_project_modules())
    rec["modules_at_reload"] = loaded
    only_config(loaded)
    cfg = importlib.reload(sys.modules["config"])
    rec["checks"].append("reload with only config loaded")
    # 5. the engine, then the positive checks
    import memory_hook as m  # noqa: PLC0415 - the engine, _engine_config.py included, runs in this ONE namespace
    import api  # noqa: PLC0415
    problems = positive_checks(spec, declared, token, cfg=cfg, m=m, api=api, SG=SG, store=store,
                               transcripts=transcripts, environ=os.environ)
    if problems:
        raise Refused("; ".join(problems))
    rec["checks"].append("stores, paths and backend")
    # 6. the ablation, the pacer
    rec["ablation"] = None
    if arm == "nevertwice-ablation":
        rec["ablation"] = _load("v3_ablation_c1", REPO / "research" / "v3" / "ablation_c1.py").enable(m)
    pacer = _load("v3_ollama_pacer", REPO / "research" / "_ollama_pacer.py")
    pacer.install("observe")                      # R-EMBED-PATH: the leg paces and retries - one layer for every arm
    pacer.set_route("127.0.0.1", spec["ollama_port"])
    rec["pacer"] = "observe"
    rec["ollama_route"] = {name: declared[name] for name, _a, _p in OLLAMA_ROUTES}
    # 7. the environment as recorded - names always, values masked
    rec["nevertwice_env"] = {k: masked(k, v) for k, v in sorted(os.environ.items()) if k.startswith("NEVERTWICE_")}
    rec["env_names"] = sorted(os.environ)
    rec["engine"] = {"llm": (f"deepseek:{m.DEEPSEEK_MODEL}" if arm in LLM_ARMS else "none"),
                     "embedder": m.EMBED_MODEL, "python": sys.version.split()[0]}
    return {"m": m, "api": api, "pacer": pacer, "sg": SG}, rec


# ── the S7 hook path: capture_session's own sequence around process_session (api.py:672-699) ────────────────────

def s7_capture(m, *, session_id: str, transcript_path: str, cwd: str, project: str) -> dict:
    """The hook's reader on a rendered Claude Code JSONL, wrapped exactly as api.capture_session wraps the ingest path:
    the same checks, lock, store, processed-db, post-steps and summary (F-N3 compares the call sequences by AST)."""
    if not m.llm_available():
        raise RuntimeError("no LLM backend (cloud key unset + Ollama down)")
    if not m.acquire_lock(timeout_s=120):
        raise RuntimeError("could not acquire vault lock - another process is busy")
    try:
        m.VAULT.mkdir(parents=True, exist_ok=True)
        db = m.load_processed()
        run_log: list[dict] = []
        ok = m.process_session(session_id, cwd, transcript_path, "SessionEnd", db, run_log=run_log,
                               project_override=project)
        if ok:
            m.rebuild_index()
            m.archive_old_sessions()
            m.archive_old_typed()
            m.prune_processed_db(db)
            m.git_autocommit()
        r = run_log[-1] if run_log else {}
        return {"stored": bool(ok), "project": r.get("project", project),
                "patterns": r.get("patterns", 0), "mistakes": r.get("mistakes", 0),
                "decisions": r.get("decisions", 0), "proposed": r.get("proposed", {}),
                "refused": r.get("refused", {}), "quarantined": r.get("quarantined", {}),
                "skipped": r.get("skipped", {}), "off_topic": r.get("off_topic", {}), "relevant": r.get("relevant"),
                "session_id": session_id}
    finally:
        m.release_lock()


# ── the handler ────────────────────────────────────────────────────────────────────────────────────────────────

class Handler:
    def __init__(self, spec: Mapping[str, Any], ns: Mapping[str, Any], rec: Mapping[str, Any]) -> None:
        self.spec, self.rec = spec, rec
        self.m, self.api, self.pacer = ns["m"], ns["api"], ns["pacer"]
        self.arm, self.stage, self.project = spec["arm"], spec["stage"], spec["stand"]
        self.store = Path(spec["unit_dir"]) / "store"
        self.outcomes: list[dict] = []
        self.ranker_items: dict[int, str] = {}
        self.item_shas: dict[int, str] = {}
        self.not_written: list[int] = []

    def _stage(self, want: str, op: str) -> None:
        if self.stage != want:
            raise RuntimeError(f"{op} belongs to the {want} stage; this process is the {self.stage} stage (Q25)")

    def hello(self) -> dict:
        return {"protocol": B.PROTOCOL, "system": "nevertwice", "arm": self.arm, "stage": self.stage,
                "llm_label": self.rec["engine"]["llm"], "embedder": self.rec["engine"]["embedder"],
                "python": self.rec["engine"]["python"], "env_names": self.rec["env_names"], "start": self.rec}

    def write(self, item: dict, date: str | None = None) -> dict:
        self._stage("write", "write")
        t0 = time.time()
        if self.arm == RANKER:
            idx = item["index"]
            if not isinstance(idx, int) or isinstance(idx, bool) or idx in self.ranker_items:
                raise ValueError(f"a ranker item needs a new int index, got {idx!r}")
            self.ranker_items[idx] = item["text"]
            self.item_shas[idx] = B.text_sha256(item["text"])
            return {"op_id": item["item_id"], "item_sha256": self.item_shas[idx], "t0": t0, "t1": time.time(),
                    "buffered": True}
        sid = f"{self.spec['unit']}-{item['session_id']}"
        if self.spec["s7"]:
            given = {"transcript_sha256": hashlib.sha256(Path(item["transcript_path"]).read_bytes()).hexdigest()}
            out = s7_capture(self.m, session_id=sid, transcript_path=item["transcript_path"],
                             cwd=self.spec["unit_dir"], project=self.project)
        else:
            given = {"text_sha256": B.text_sha256(item["text"])}
            out = self.api.capture_session(item["text"], project=self.project, session_id=sid, date=date)
        self.outcomes.append(out)
        return {"op_id": item["item_id"], **given, "t0": t0, "t1": time.time(), "outcome": out}

    def end_write(self) -> dict:
        self._stage("write", "end_write")
        t0 = time.time()
        if self.arm == RANKER and self.ranker_items:
            order = sorted(self.ranker_items)
            lessons = [{"title": f"item {i}", "description": self.ranker_items[i], "type": "pattern"} for i in order]
            stems = self.api.remember_lessons_aligned(lessons, project=self.project)
            self.not_written = [i for i, s in zip(order, stems) if not s]
        footprint = self._footprint()
        seal = B.write_seal(self.spec["unit_dir"], self.store, arm=self.arm, run=self.spec["run"], unit=self.spec["unit"])
        digest = {"items_sha256": B.items_digest(self.item_shas)} if self.arm == RANKER else {}
        return {"footprint": footprint, "not_written": self.not_written, "seal": seal, **digest, "t0": t0,
                "t1": time.time()}

    def _footprint(self) -> dict:
        """Retrievable items: typed notes of this unit's project only (a Session note is not retrievable)."""
        proj = self.m.slug_project(self.project)
        cache = self.m.load_embed_cache()
        typed = [r for r in cache.values() if isinstance(r, dict) and r.get("project") == proj
                 and r.get("ntype") in self.m.TYPED_TYPES]
        return {"retrievable": len(typed), "embedded": sum(isinstance(r.get("vec"), list) for r in typed),
                "chars": sum(len(self.api.format_note({"ntype": r.get("ntype"), "title": r.get("title"),
                                                       "description": r.get("desc"),
                                                       "prevention": r.get("prevention")})) for r in typed)}

    def read(self, qid: str, query: str, k: int) -> dict:
        self._stage("read", "read")
        t0 = time.time()
        hits = self.api.recall(query, project=self.project, k=k, xrerank=False)
        if self.arm == RANKER:
            items = []
            for rank, h in enumerate(hits, 1):
                mt = re.fullmatch(r"item (\d+)", (h.get("title") or "").strip())
                if not mt:
                    raise RuntimeError(f"a ranker hit is not one of the unit's items: title {h.get('title')!r}")
                items.append({"index": int(mt.group(1)), "text": h.get("description") or "", "rank": rank})
        else:
            items = [{"text": self.api.format_note(h), "rank": rank} for rank, h in enumerate(hits, 1)]
        return {"qid": qid, "items": items, "t0": t0, "t1": time.time()}

    def counters(self) -> dict:
        m = self.m
        keys = ("stored", "patterns", "mistakes", "decisions")
        transport: dict = {}
        self.pacer.attach(transport)            # the Ollama transport, the artifact's own shape (JSON-safe)
        return {"llm_stats": dict(m._LLM_STATS), "recall": self.api.recall_stats(),
                "outcomes": {"writes": len(self.outcomes),
                             **{k: sum(int(o.get(k) or 0) for o in self.outcomes) for k in keys}},
                "not_written": len(self.not_written), "ollama_transport": transport.get("ollama_transport"),
                "ablation": (dict(sys.modules["v3_ablation_c1"].STATS) if self.arm == "nevertwice-ablation" else None)}


class RefusedHandler:
    """A refused bind: every request is answered ok:false with the reason, by name."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __getattr__(self, op: str):
        if op not in B.OPS:
            raise AttributeError(op)

        def refuse(**_kw):
            raise Refused(f"the arm refused to start: {self.reason}")
        return refuse


def _write_record(path: str, rec: Mapping[str, Any]) -> None:
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes((json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8"))
    os.replace(tmp, p)


def build(spec_path: str, env_at_start: Mapping[str, str]) -> Any:
    """The handler factory main_with calls AFTER claiming the stdio: the spec, the bind, the start record."""
    try:
        spec = load_spec(spec_path)
    except (Refused, OSError, ValueError, KeyError) as e:
        return RefusedHandler(f"{type(e).__name__}: {e}")
    try:
        ns, rec = bind(spec, env_at_start)
    except Exception as e:  # noqa: BLE001 - every failure to bind is a named refusal, recorded
        reason = f"{type(e).__name__}: {e}"
        _write_record(spec["record_path"], {"ok": False, "error": reason[:B.ERROR_MAX], "arm": spec["arm"],
                                            "stage": spec["stage"], "unit": spec["unit"]})
        return RefusedHandler(reason)
    rec = {"ok": True, **rec}
    _write_record(spec["record_path"], rec)
    return Handler(spec, ns, rec)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: runner_nevertwice.py <spec.json>\n")
        sys.exit(2)
    ENV_AT_START = dict(os.environ)          # step 1 compares the declared values with the harness's environment
    sandbox_guard.isolate()                   # step 2, before any project module is imported
    sys.exit(B.main_with(lambda: build(sys.argv[1], ENV_AT_START)))
