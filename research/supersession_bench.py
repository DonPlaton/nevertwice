"""Does a memory return a fact that has since been retracted?

Every public memory benchmark - LoCoMo, LongMemEval, BEAM - measures whether a system
*recalls* a fact. None measures whether it hands back a fact that is no longer true. For a
chat companion that is a nuance. For a coding agent it is the whole problem: an agent acting
on a withdrawn fact writes wrong code, confidently, and the wrongness is invisible until it
runs.

So this stand measures three things at once, because measuring only the first is trivially
gamed by a system that returns nothing:

* **stale rate** - the retracted fact came back. Lower is better; 0 is perfect.
* **current rate** - the replacement came back. Higher is better. A system that answers
  "I have nothing" scores a perfect stale rate and a zero current rate, and the pair of
  numbers says so immediately.
* **over-retraction rate** - measured on control cases where two facts are simply *different*
  and both remain true. A system that suppresses one of those is not careful, it is lossy.
  Without this column, "delete on any doubt" would look like the winning strategy.

**Arms.** `nevertwice` through the same public entry point a user gets (`api.capture_session`
then `api.recall`); `mem0` through `Memory.add` / `Memory.search`, one call per session so both
extract from the same unit the same number of times; and `naive`, an append-only
markdown store with BM25 retrieval and no supersession mechanism at all. The naive arm is the
floor and it is the point: if it also scores well, the benchmark does not separate systems and
must be thrown away rather than published. A benchmark that only one vendor's architecture
fails is a benchmark about that vendor.

Every arm sees the same sessions in the same order, uses the same local embedder, and runs
against the owner's already-running Ollama, so nothing is billed and nothing leaves the
machine.

    python research/supersession_bench.py --probe                # the five-fact Mem0 probe
    python research/supersession_bench.py --arms nevertwice,naive
    python research/supersession_bench.py --arms nevertwice,mem0,naive --out results.json
    python research/supersession_bench.py --pool run1.json run2.json --with mem0.json --out pooled.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import sandbox_guard  # noqa: E402 - must precede any nevertwice import

sandbox_guard.isolate(prefix="nevertwice_supersession_")

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("NEVERTWICE_EMBED_MODEL", "bge-m3")
#: One extraction model for every arm. The Mem0 probe of 2026-08-30 used `qwen3-coder:30b`,
#: so that is the default here: a different model on either side would confound the
#: architecture being measured with the extractor's ability.
LLM = os.environ.get("SUPERSESSION_LLM", "qwen3-coder:30b")

DATASET = HERE / "data" / "supersession_v1.json"

#: The exact probe Mem0 2.0.19 was measured on (2026-08-30, polygon `mem0_eval/`), kept
#: verbatim so this repository can reproduce that comparison rather than cite it. Five turns,
#: two queries, one superseded fact each.
PROBE = {
    "name": "mem0_probe_2026_08_30",
    "cases": [
        {
            "id": "probe-extraction",
            "shape": "value_replaced",
            "sessions": [
                ["The memory system extracts knowledge using Cerebras gpt-oss-120b in the cloud.",
                 "We benchmarked the embedding model and recall at 1% FPR was 0.220.",
                 "The vault lives at D:/Obsidian/Claude_Memory and is under git."],
                ["We switched extraction off Cerebras entirely; it now runs on local Ollama only."],
            ],
            "query": "what does the memory system use for extraction",
            "current": ["ollama"],
            "superseded": ["cerebras"],
        },
        {
            "id": "probe-recall",
            "shape": "value_replaced",
            "sessions": [
                ["We benchmarked the embedding model and recall at 1% FPR was 0.220."],
                ["The embedding model was retrained and recall at 1% FPR is now 0.475."],
            ],
            "query": "what is the recall at 1% FPR",
            "current": ["0.475"],
            "superseded": ["0.220"],
        },
    ],
}


# ── scoring ───────────────────────────────────────────────────────────────────────────────

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson interval. Normal approximation puts 0/12 at [0, 0], which reads as certainty."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - r) / d), min(1.0, (c + r) / d))


_SEP = re.compile(r"[\s_\-]+")


def _norm(text: str) -> str:
    """Fold separators so a marker survives the extractor's punctuation choices.

    Run one on the raw text and seven of the twenty control cases failed on nothing but
    spelling: the marker said `two approval` and the note said `two-approval`, the marker
    said `8 kb` and the note said `8KB`. A benchmark that scores those as a memory failure
    is measuring hyphens.
    """
    return _SEP.sub(" ", text.lower())


def _hit(markers: list[str], text: str) -> bool:
    low = _norm(text)
    return any(_norm(m) in low for m in markers)


def classify(items: list[str], case: dict) -> dict:
    """Per returned item: does it *assert* the retracted fact, or merely name it?

    The first scoring pass matched the retracted value anywhere in the returned text, and
    it was wrong in a way that flattered no one and confused everything. Nevertwice answered
    the extraction query with a decision note titled *disable-cerebras-integration* - the
    current truth, which cannot be stated without naming the thing that was disabled - and a
    substring test called that stale. Mem0 returned the original sentence *"extracts
    knowledge using Cerebras gpt-oss-120b"*, which asserts the retracted state as fact.
    Those are opposite outcomes and the metric could not tell them apart.

    So an item counts as a **stale assertion** only when it carries a retracted marker and
    **no** current marker: a text naming both is describing the change, not asserting the old
    state. The rank of the first such item is reported too, because a system that surfaces a
    stale assertion at position five is not in the same condition as one that puts it first.
    """
    stale_ranks = [i + 1 for i, t in enumerate(items)
                   if _hit(case.get("superseded", []), t)
                   and not _hit(case.get("current", []), t)]
    return {"stale_returned": bool(stale_ranks),
            "stale_rank": stale_ranks[0] if stale_ranks else None,
            "current_returned": any(_hit(case.get("current", []), t) for t in items)}


def score(rows: list[dict]) -> dict:
    """Fold per-case outcomes into the three rates plus the token floor.

    Control cases carry no `superseded` markers and are scored on over-retraction only;
    supersession cases are scored on stale and current. Mixing them into one denominator
    would let a system trade one axis for the other invisibly.
    """
    sup = [r for r in rows if r["shape"] != "control"]
    ctl = [r for r in rows if r["shape"] == "control"]
    stale = sum(1 for r in sup if r["stale_returned"])
    current = sum(1 for r in sup if r["current_returned"])
    over = sum(1 for r in ctl if not r["current_returned"])
    chars = [r["chars_returned"] for r in rows]
    out = {
        "n_supersession": len(sup),
        "n_control": len(ctl),
        "stale_rate": round(stale / len(sup), 4) if sup else None,
        "stale_ci": [round(x, 4) for x in wilson(stale, len(sup))] if sup else None,
        "current_rate": round(current / len(sup), 4) if sup else None,
        "current_ci": [round(x, 4) for x in wilson(current, len(sup))] if sup else None,
        "over_retraction_rate": round(over / len(ctl), 4) if ctl else None,
        "over_retraction_ci": [round(x, 4) for x in wilson(over, len(ctl))] if ctl else None,
        "mean_chars_returned": round(sum(chars) / len(chars), 1) if chars else 0.0,
        "max_chars_returned": max(chars) if chars else 0,
        "stale_at_rank_1": sum(1 for r in sup if r.get("stale_rank") == 1),
    }
    if ctl and any("current_live" in r for r in ctl):
        lost = [r for r in ctl if not r["current_returned"]]
        out["control_retired_by_memory"] = sum(1 for r in lost if r.get("current_retired"))
        out["control_never_written"] = sum(1 for r in lost if r.get("current_absent"))
        out["control_written_but_unranked"] = sum(
            1 for r in lost if r.get("current_live") and not r["current_returned"])
        n_ctl = len(ctl)
        out["true_over_retraction_rate"] = round(out["control_retired_by_memory"] / n_ctl, 4)
    drifted = [r for r in rows if r.get("notes_in_cyrillic")]
    if any("notes_written" in r for r in rows):
        wrote = sum(r.get("notes_written", 0) for r in rows)
        cyr = sum(r.get("notes_in_cyrillic", 0) for r in rows)
        out["notes_written"] = wrote
        out["notes_in_cyrillic"] = cyr
        out["language_drift_rate"] = round(cyr / wrote, 4) if wrote else 0.0
        out["cases_touched_by_drift"] = len(drifted)
    return out


# ── arm: nevertwice ───────────────────────────────────────────────────────────────────────

def _store_state(project: str, case: dict) -> dict:
    """Where the current fact ended up: live, retired, or never written.

    `current_returned=False` on a control case was reported as over-retraction, and that was
    three different failures wearing one number. A fact can be missing because the memory
    retired it (the failure this column is for), because retrieval ranked it below k, or
    because extraction never wrote it at all - and only the first is the memory being too
    eager. Reading the store settles it instead of inferring it.
    """
    import sandbox_guard as sg                                  # noqa: PLC0415
    root = Path(sg.store())
    live = retired = False
    drift = 0
    total = 0
    for folder in ("Patterns", "Mistakes", "Decisions"):
        d = root / folder
        if not d.exists():
            continue
        for md in list(d.glob(f"*-{project}-*.md")) + list(
                (d / "Superseded").glob(f"*-{project}-*.md") if (d / "Superseded").exists() else []):
            body = md.read_text(encoding="utf-8", errors="replace")
            total += 1
            if sum(1 for ch in body if "\u0400" <= ch <= "\u04ff") > 20:
                drift += 1
            if _hit(case["current"], body):
                if md.parent.name == "Superseded":
                    retired = True
                else:
                    live = True
    return {"current_live": live, "current_retired": retired,
            "current_absent": not (live or retired), "notes_written": total,
            "notes_in_cyrillic": drift}


def run_nevertwice(cases: list[dict], k: int) -> dict:
    """The public path: one `capture_session` per session, then `recall`.

    A separate project per case, so one case cannot retrieve another's notes - the same
    isolation a real user gets from working in different repositories, and without it the
    distractor sessions of case 7 would pollute case 8's ranking for reasons that have
    nothing to do with supersession.
    """
    os.environ["NEVERTWICE_CLOUD"] = "none"          # local only: nothing billed, nothing sent
    os.environ["NEVERTWICE_MODEL"] = LLM
    os.environ.setdefault("NEVERTWICE_EMBED_MODEL", EMBED_MODEL)
    try:
        from nevertwice import api
    except Exception as e:                            # pragma: no cover - import-time only
        return {"blocked": f"nevertwice import failed ({type(e).__name__}: {e})"}

    rows, t0 = [], time.time()
    for i, case in enumerate(cases):
        project = f"sup{i:03d}"
        try:
            for j, session in enumerate(case["sessions"]):
                api.capture_session("\n".join(session), project=project,
                                    session_id=f"{project}-s{j}", trigger="ingest")
        except Exception as e:
            rows.append({**_blank(case), "error": f"{type(e).__name__}: {e}"})
            continue
        hits = api.recall(case["query"], project=project, k=k)
        items = [hit_text(h) for h in hits]
        rows.append({**_row(case, items), **_store_state(project, case),
                     "evidence_spans_returned": sum(len(h.get("evidence") or []) for h in hits)})
        print(f"  [{i + 1}/{len(cases)}] {case['id']}  hits={len(hits)}"
              f"  stale={rows[-1]['stale_returned']}@{rows[-1]['stale_rank']}", flush=True)
    out = {"rows": rows, **score(rows), "seconds": round(time.time() - t0, 1),
           "config": f"ollama {LLM} + {EMBED_MODEL}, k={k}",
           "store_bytes": store_bytes(), **evidence_cost()}
    return out


def hit_text(h: dict) -> str:
    """What a recall hit hands the agent, as one string the markers are matched against:
    title, description, prevention and - since J1 - the verbatim evidence lines. The stale
    and current markers see exactly the text a user's agent would see; characters per query
    are counted on the same string."""
    parts = [str(h.get(f) or "") for f in ("title", "description", "prevention")]
    parts += [str(s) for s in (h.get("evidence") or [])]
    return " ".join(p for p in parts if p)


def store_bytes() -> int:
    """Bytes of typed notes in the sandbox store, live and retired - the size cost cap of J1
    is read from this number against a run without spans."""
    import sandbox_guard as sg                                  # noqa: PLC0415
    root = Path(sg.store())
    total = 0
    for folder in ("Patterns", "Mistakes", "Decisions"):
        d = root / folder
        if d.exists():
            total += sum(p.stat().st_size for p in d.rglob("*.md"))
    return total


def evidence_cost() -> dict:
    """The alignment layer's own running cost in this process (ms, notes, spans), for the
    latency cap; zeros when the layer is off or the module is absent."""
    try:
        from nevertwice import api                              # noqa: PLC0415
        ev = api._evidence          # the same module object the write path counted into
    except Exception:                                           # noqa: BLE001
        return {"evidence": {"enabled": False}}
    st = dict(ev.STATS)
    st["enabled"] = ev.enabled()
    st["spans_per_note_max"] = ev.MAX_SPANS
    st["span_chars_max"] = ev.SPAN_CHARS
    st["ms_per_session"] = round(st["ms"] / st["sessions"], 3) if st.get("sessions") else 0.0
    return {"evidence": st}


# ── arm: mem0 ─────────────────────────────────────────────────────────────────────────────

def run_mem0(cases: list[dict], k: int) -> dict:
    """Mem0 2.0.19 through its documented local configuration.

    Blocked rather than skipped when the package is absent: an arm that quietly disappears
    from a comparison table is how a missing competitor becomes a won one.
    """
    try:
        from mem0 import Memory
    except ImportError:
        return {"blocked": "mem0 not installed here - `pip install mem0ai ollama`"}
    for var in ("MEM0_TELEMETRY", "MEM0_TELEMETRY_ENABLED", "ANONYMIZED_TELEMETRY"):
        os.environ.setdefault(var, "False")

    base = Path(tempfile.mkdtemp(prefix="nevertwice_sup_mem0_"))
    cfg = {
        "llm": {"provider": "ollama", "config": {
            "model": LLM, "temperature": 0.0, "ollama_base_url": OLLAMA_BASE}},
        "embedder": {"provider": "ollama", "config": {
            "model": EMBED_MODEL, "ollama_base_url": OLLAMA_BASE}},
        "vector_store": {"provider": "qdrant", "config": {
            "collection_name": "sup", "path": str(base / "qdrant"), "on_disk": True,
            "embedding_model_dims": 1024}},
    }
    # ONE instance, cases separated by `user_id`. A per-case instance is the obvious shape
    # and it does not work: Mem0 keeps a process-global migrations store under ~/.mem0 and
    # the second `Memory.from_config` in a process dies with "already accessed by another
    # instance of Qdrant client". Seventy-nine of eighty cases failed that way on the first
    # run and the arm still produced a summary line - which is exactly how a broken arm gets
    # published as a win, so the per-case error is now counted and printed.
    try:
        mem = Memory.from_config(cfg)
    except Exception as e:
        return {"blocked": f"Mem0 init failed ({type(e).__name__}: {e})"}

    rows, t0 = [], time.time()
    for i, case in enumerate(cases):
        uid = f"sup{i:03d}"
        try:
            # One `add` per session, not per sentence: `capture_session` gets a whole session
            # in one call, so a per-sentence loop here would hand Mem0 twice the extraction
            # opportunities and charge it twice the latency for the same corpus. Same unit,
            # same count, same order.
            for session in case["sessions"]:
                mem.add("\n".join(session), user_id=uid)
            res = mem.search(case["query"], filters={"user_id": uid}, limit=k)
            got = res.get("results", res) if isinstance(res, dict) else res
            texts = [r.get("memory", "") for r in got] if isinstance(got, list) else []
        except Exception as e:
            rows.append({**_blank(case), "error": f"{type(e).__name__}: {e}"})
            print(f"  [{i + 1}/{len(cases)}] {case['id']}  ERROR {type(e).__name__}", flush=True)
            continue
        rows.append(_row(case, texts))
        print(f"  [{i + 1}/{len(cases)}] {case['id']}  hits={len(texts)}"
              f"  stale={rows[-1]['stale_returned']}@{rows[-1]['stale_rank']}", flush=True)
    shutil.rmtree(base, ignore_errors=True)
    failed = sum(1 for r in rows if r.get("error"))
    if failed > len(rows) * 0.1:
        return {"blocked": f"{failed} of {len(rows)} cases errored - "
                           f"first: {next(r['error'] for r in rows if r.get('error'))}",
                "rows": rows}
    return {"rows": rows, **score(rows), "errors": failed,
            "seconds": round(time.time() - t0, 1),
            "config": f"mem0 ollama {LLM} + {EMBED_MODEL}, limit={k}"}


# ── arm: naive append-only markdown + BM25 ────────────────────────────────────────────────

_WORD = re.compile(r"[a-z0-9_.]+")


def run_naive(cases: list[dict], k: int) -> dict:
    """The floor: keep every turn, retrieve by term overlap, never retract anything.

    This is what a person does by hand before installing anything, and it is the arm that
    decides whether this benchmark is worth publishing. It has no extraction model, so it is
    also the only arm whose result cannot be blamed on the LLM.
    """
    rows, t0 = [], time.time()
    for case in cases:
        docs = [t for s in case["sessions"] for t in s]
        q = set(_WORD.findall(case["query"].lower()))
        df = {}
        toks = [set(_WORD.findall(d.lower())) for d in docs]
        for ts in toks:
            for w in ts:
                df[w] = df.get(w, 0) + 1
        n = len(docs)
        scored = []
        for d, ts in zip(docs, toks):
            s = sum(math.log(1 + n / df[w]) for w in q & ts)
            scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        texts = [d for s, d in scored[:k] if s > 0]
        rows.append(_row(case, texts))
    return {"rows": rows, **score(rows), "seconds": round(time.time() - t0, 1),
            "config": f"append-only markdown + BM25-style IDF overlap, k={k}"}


# ── plumbing ──────────────────────────────────────────────────────────────────────────────

def _blank(case: dict) -> dict:
    return {"id": case["id"], "shape": case["shape"], "n_hits": 0,
            "stale_returned": False, "stale_rank": None, "current_returned": False,
            "chars_returned": 0, "returned": []}


def _row(case: dict, items: list[str]) -> dict:
    return {"id": case["id"], "shape": case["shape"], "n_hits": len(items),
            **classify(items, case),
            "chars_returned": sum(len(t) for t in items),
            "returned": items}


ARMS = {"nevertwice": run_nevertwice, "mem0": run_mem0, "naive": run_naive}


def compare_arms(loaded: dict[str, dict[str, dict]]) -> list[dict]:
    """Pair every arm against every other on the SAME supersession cases (exact McNemar).

    `loaded` maps an arm name to its rows keyed by case id. Arms are paired in name order, so
    a pointer such as `pairs[1].p_mcnemar` in the evidence register names the same pair on
    every rebuild.
    """
    sys.path.insert(0, str(HERE))
    from uncertainty import mcnemar_exact                       # noqa: PLC0415

    names = sorted(loaded)
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = [cid for cid in loaded[a] if cid in loaded[b]
                      and loaded[a][cid]["shape"] != "control"]
            # discordant pairs only: cases where exactly one arm returned a stale assertion
            a_only = sum(1 for cid in shared
                         if loaded[a][cid]["stale_returned"] and not loaded[b][cid]["stale_returned"])
            b_only = sum(1 for cid in shared
                         if loaded[b][cid]["stale_returned"] and not loaded[a][cid]["stale_returned"])
            pairs.append({
                "a": a, "b": b, "n": len(shared),
                f"stale_only_{a}": a_only, f"stale_only_{b}": b_only,
                "discordant": a_only + b_only,
                "p_mcnemar": mcnemar_exact(a_only, b_only),
            })
    return pairs


def compare(files: list[Path]) -> dict:
    """Pair every arm against every other on the SAME cases, and test the difference.

    Two Wilson intervals that fail to overlap are strong evidence, but they are not the test:
    the arms saw identical cases, so the paired one is available and is what the rest of this
    repository uses. Arms run in separate processes - Mem0 lives in its own environment - so
    the comparison reads result files rather than running the arms itself.
    """
    loaded: dict[str, dict[str, dict]] = {}
    datasets = set()
    for f in files:
        blob = json.loads(Path(f).read_text(encoding="utf-8"))
        datasets.add(blob["dataset"]["sha256"])
        for name, res in blob["arms"].items():
            if res.get("blocked"):
                continue
            loaded[name] = {r["id"]: r for r in res["rows"]}
    if len(datasets) > 1:
        return {"error": f"result files come from different datasets: {sorted(datasets)}"}
    return {"dataset_sha256": datasets.pop() if datasets else None, "arms": sorted(loaded),
            "pairs": compare_arms(loaded)}


ENGINE_ARM = "nevertwice"


def _r4(pair) -> list[float]:
    return [round(x, 4) for x in pair]


def pool(engine_files: list[Path], other_files: list[Path] | None = None) -> dict:
    """Pool several runs of the engine arm into one artifact, the other arms beside them.

    Two runs of the same commit on the same corpus read stale 0.017 and 0.067 (2026-09-02):
    the extraction model is not deterministic at temperature 0, so one run of this stand is not
    a result. The published rate is pooled over case-runs with the per-run values kept beside
    it; the paired tests are computed on the first run, where the arms saw identical cases, and
    repeated per run against Mem0. Until 2026-09-06 this lived in a session scratchpad, which
    meant the committed artifact could not be rebuilt by anyone else.

    Every file must come from the same dataset (content hash). Engine files contribute their
    `nevertwice` arm to the pool and any other unblocked arm to the artifact; `other_files`
    contribute their unblocked arms. An arm that appears in two files is an error rather than
    a silent choice.
    """
    runs: list[dict] = []
    others: dict[str, dict] = {}
    meta: dict | None = None
    shas: set[str] = set()
    for f in [Path(p) for p in engine_files]:
        blob = json.loads(f.read_text(encoding="utf-8"))
        shas.add(blob["dataset"]["sha256"])
        arm = blob["arms"].get(ENGINE_ARM)
        if not arm or arm.get("blocked"):
            raise ValueError(f"{f}: no {ENGINE_ARM} arm to pool")
        runs.append(arm)
        if meta is None:
            meta = blob
        for name, res in blob["arms"].items():
            if name != ENGINE_ARM and not res.get("blocked") and name not in others:
                others[name] = res
    for f in [Path(p) for p in (other_files or [])]:
        blob = json.loads(f.read_text(encoding="utf-8"))
        shas.add(blob["dataset"]["sha256"])
        for name, res in blob["arms"].items():
            if name == ENGINE_ARM or res.get("blocked"):
                continue
            if name in others:
                raise ValueError(f"arm {name!r} appears in two result files - which one?")
            others[name] = res
    if len(shas) != 1:
        raise ValueError(f"result files come from different datasets: {sorted(shas)}")
    assert meta is not None

    def sup(a: dict) -> list[dict]:
        return [r for r in a["rows"] if r["shape"] != "control"]

    def ctl(a: dict) -> list[dict]:
        return [r for r in a["rows"] if r["shape"] == "control"]

    stale_k = sum(1 for a in runs for r in sup(a) if r["stale_returned"])
    cur_k = sum(1 for a in runs for r in sup(a) if r["current_returned"])
    sup_n = sum(len(sup(a)) for a in runs)
    # true over-retraction only: the memory retired a still-true fact (not "ranked below k",
    # not "never written"), which is what `_store_state` records per control row
    over_k = sum(1 for a in runs for r in ctl(a)
                 if not r["current_returned"] and r.get("current_retired"))
    ctl_n = sum(len(ctl(a)) for a in runs)
    per_run_stale = [round(sum(1 for r in sup(a) if r["stale_returned"]) / len(sup(a)), 4)
                     for a in runs]
    per_run_cur = [round(sum(1 for r in sup(a) if r["current_returned"]) / len(sup(a)), 4)
                   for a in runs]
    pooled = {
        "runs": len(runs),
        "stale": {"k": stale_k, "n": sup_n, "rate": round(stale_k / sup_n, 4),
                  "ci": _r4(wilson(stale_k, sup_n)), "per_run": per_run_stale},
        "current": {"k": cur_k, "n": sup_n, "rate": round(cur_k / sup_n, 4),
                    "ci": _r4(wilson(cur_k, sup_n)), "per_run": per_run_cur},
        "over_retraction": {"k": over_k, "n": ctl_n,
                            "rate": round(over_k / ctl_n, 4) if ctl_n else None,
                            "ci": _r4(wilson(over_k, ctl_n))},
        "mean_chars_returned": round(sum(a["mean_chars_returned"] for a in runs) / len(runs), 1),
    }

    arms: dict[str, dict] = {ENGINE_ARM: runs[0]}
    for i, a in enumerate(runs[1:], start=2):
        arms[f"{ENGINE_ARM}_run{i}"] = a
    arms.update(others)

    first = {ENGINE_ARM: {r["id"]: r for r in runs[0]["rows"]}}
    first.update({n: {r["id"]: r for r in res["rows"]} for n, res in others.items()})

    per_run_pairs = []
    if "mem0" in others:
        m0 = {r["id"]: r for r in others["mem0"]["rows"]}
        for a in runs:
            rows = {r["id"]: r for r in a["rows"]}
            ids = [i for i, r in rows.items() if r["shape"] != "control" and i in m0]
            per_run_pairs.append({
                "nevertwice_only": sum(1 for i in ids
                                       if rows[i]["stale_returned"] and not m0[i]["stale_returned"]),
                "mem0_only": sum(1 for i in ids
                                 if m0[i]["stale_returned"] and not rows[i]["stale_returned"]),
            })

    n_sup, n_ctl = len(sup(runs[0])), len(ctl(runs[0]))
    ds = dict(meta["dataset"])
    ds.update({"cases": n_sup + n_ctl, "supersession_cases": n_sup, "control_cases": n_ctl})
    spread = " and ".join(f"{v:.4f}" for v in per_run_stale)
    note = (f"{len(runs)} runs of the same commit on the same corpus with the same models read "
            f"stale {spread}. The extraction model is not deterministic at temperature 0, so the "
            "published rate is pooled over case-runs and the per-run values are kept beside it. "
            "One run of this stand is not a result.")
    return {"arms": arms, "k": meta["k"], "llm": meta["llm"], "embedder": meta["embedder"],
            "dataset": ds, "pooled_nevertwice": pooled, "pooled_note": note,
            "pairs": compare_arms(first), "pairs_per_engine_run": per_run_pairs}


def load_dataset(path: Path) -> dict:
    """The corpus, with the hash the artifact is pinned by.

    `--dataset` may be given relative to the repository root, which is how `research/reproduce.py`
    prints every command, or absolute. The recorded path is repository-relative with forward
    slashes so an artifact produced on Windows reads the same everywhere; a corpus outside the
    repository keeps its own path, and the hash is what pins it either way."""
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    data["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        data["path"] = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        data["path"] = path.as_posix()
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", action="store_true",
                    help="run the five-fact Mem0 probe instead of the committed dataset")
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--arms", default="nevertwice,naive")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="first N cases only (a smoke run)")
    ap.add_argument("--out", default="")
    ap.add_argument("--compare", nargs="*", default=None,
                    help="result files to pair against each other instead of running an arm")
    ap.add_argument("--pool", nargs="+", default=None, metavar="RUN",
                    help="engine result files to pool into one artifact (each must carry the "
                         "nevertwice arm); the other arms in them are carried along")
    ap.add_argument("--with", dest="others", nargs="*", default=[], metavar="FILE",
                    help="result files whose other arms (mem0, naive) go into the pooled artifact")
    args = ap.parse_args()

    if args.pool:
        try:
            res = pool([Path(f) for f in args.pool], [Path(f) for f in args.others])
        except (ValueError, OSError, KeyError) as e:
            print(f"pool: {e}")
            return 2
        p = res["pooled_nevertwice"]
        print(f"pooled {p['runs']} engine runs on {res['dataset']['name']}: "
              f"stale {p['stale']['rate']} {p['stale']['ci']} per run {p['stale']['per_run']} | "
              f"current {p['current']['rate']} {p['current']['ci']} | "
              f"over-retraction {p['over_retraction']['rate']} | "
              f"{p['mean_chars_returned']} chars/query")
        for pr in res["pairs"]:
            print(f"  {pr['a']} vs {pr['b']}: n={pr['n']} discordant {pr['discordant']} "
                  f"p={pr['p_mcnemar']:.3g}")
        for i, pr in enumerate(res["pairs_per_engine_run"], start=1):
            print(f"  run {i} vs mem0: nevertwice-only {pr['nevertwice_only']}, "
                  f"mem0-only {pr['mem0_only']}")
        if args.out:
            Path(args.out).write_text(json.dumps(res, indent=1, ensure_ascii=False),
                                      encoding="utf-8")
            print("wrote", args.out)
        return 0

    if args.compare:
        res = compare([Path(f) for f in args.compare])
        print(json.dumps(res, indent=1))
        if args.out:
            Path(args.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
            print("wrote", args.out)
        return 0

    if args.probe:
        data = dict(PROBE, sha256="n/a (inline probe)", path="research/supersession_bench.py")
    else:
        p = Path(args.dataset)
        if not p.exists():
            print(f"dataset not found: {p}\nbuild it: python research/gen_supersession_dataset.py")
            return 2
        data = load_dataset(p)

    cases = data["cases"][:args.limit] if args.limit else data["cases"]
    print(f"dataset {data['name']}  n={len(cases)}  sha256={data['sha256'][:16]}")
    print(f"store   {sandbox_guard.store()}\n")

    out = {"dataset": {k: data[k] for k in ("name", "sha256", "path")},
           "n_cases": len(cases), "k": args.k, "llm": LLM, "embedder": EMBED_MODEL,
           "arms": {}}
    for name in [a.strip() for a in args.arms.split(",") if a.strip()]:
        fn = ARMS.get(name)
        if fn is None:
            print(f"- {name}: unknown arm (have: {', '.join(ARMS)})")
            continue
        print(f"- {name}")
        res = fn(cases, args.k)
        out["arms"][name] = res
        if res.get("blocked"):
            print(f"  BLOCKED: {res['blocked']}\n")
            continue
        print(f"  stale {res['stale_rate']} {res['stale_ci']} "
              f"(rank-1: {res['stale_at_rank_1']}) | "
              f"current {res['current_rate']} {res['current_ci']} | "
              f"over-retraction {res['over_retraction_rate']} | "
              f"{res['mean_chars_returned']} chars/query | {res['seconds']}s")
        if "true_over_retraction_rate" in res:
            print(f"    of the controls that came back empty: "
                  f"{res['control_retired_by_memory']} retired by the memory, "
                  f"{res['control_never_written']} never written, "
                  f"{res['control_written_but_unranked']} written but out of the top {args.k} "
                  f"-> true over-retraction {res['true_over_retraction_rate']}")
        if res.get("notes_written"):
            print(f"    extraction wrote {res['notes_written']} notes, "
                  f"{res['notes_in_cyrillic']} of them in Cyrillic on an all-English corpus "
                  f"(drift {res['language_drift_rate']})")
        print()

    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
