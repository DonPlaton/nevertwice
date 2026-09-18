#!/usr/bin/env python3
"""The active-memory stand: does a guard catch a repeated mistake, at what false-alarm rate, and
against which cheap rival? (ledger J6)

The README says a guard distilled from a past mistake fires when the agent is about to repeat
it, at zero context tokens until it does. That is a claim about a mechanism working. This stand
asks the two questions a reviewer asks next: how often does it catch the repeat, and how often
does it cry wolf on code that merely looks like one? Both are read at a **matched false-positive
rate**, with the machinery of `research/matched_conditions.py`, so no arm wins by firing more.

Arms:
* `guards_deterministic` - the engine's own no-model path: `propose_from_mistake(use_llm=False)`
  writes a regex per mistake note, `guards.check` runs it on the tool call. Zero tokens until a
  hit, then one line.
* `guards_llm` - the same, with the regex written by the local model (`--llm`; the pattern is
  cached per note so a re-score costs nothing; the arm blocks itself above 10% empty patterns).
* `universal_pack` - the cold-start pack: no history, no model, the eleven patterns every
  project gets. Its hits count as right only for the mistakes whose own pattern it carries.
* `linter_or_test` - would `ruff`, `bandit` or a secret scanner fire? Read from the corpus
  table, scored in the linter's favour (as `research/cheap_baselines_rules.json` does): it
  never false-alarms, and it catches what the table says it catches.
* `prompt_recall` - the cheap alternative inside this product: `api.recall` over the mistake
  notes with the tool call as the query, the right note in the top three counts as a catch. It
  is charged the tokens of the three notes on **every** call, because that is what injection
  costs whether or not the note was needed.
* `never` - the floor: silence.

Per arm: recall of the *right* guard at FPR <= 0.05 on negatives, precision, the false-positive
rate on the hard negatives alone (same identifiers, correct code), recall on the generic and
the project subsets, tokens per call, latency per check. Every number goes to the artifact with
the corpus hash.

    python research/guard_bench.py                  # deterministic arms, no model
    python research/guard_bench.py --llm --save     # + the model-written patterns (GPU)
    python research/guard_bench.py --limit 5        # smoke
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - one store sandbox for the whole repo

sandbox_guard.isolate(prefix="nevertwice_guardbench_")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
import guards as G  # noqa: E402
import matched_conditions as MC  # noqa: E402 - confusion / rates / matched-rate machinery

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                  # noqa: BLE001
    pass

CORPUS = HERE / "data" / "guard_bench_v1.json"
OUT = ROOT / "research" / "results" / "guard_bench_v1.json"
LLM = os.environ.get("SUPERSESSION_LLM", "qwen3-coder:30b")
EMBED_MODEL = os.environ.get("NEVERTWICE_EMBED_MODEL", "bge-m3")
LLM_CACHE = HERE / "data" / "guard_bench_llm_cache.json"     # *_cache.json: never committed
TARGET_FPR = 0.05
RECALL_K = 3
#: An approximation the stand states rather than hides: about 1.3 tokens per whitespace word.
TOKENS_PER_WORD = 1.3


def load_corpus(path: Path = CORPUS) -> dict:
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    data["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        data["path"] = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        data["path"] = path.as_posix()
    return data


def notes_of(corpus: dict) -> list[dict]:
    """The mistakes as the note metas the guard generator reads."""
    return [{"stem": x["stem"], "project": x["project"], "ntype": "mistake", "title": x["title"],
             "desc": x["desc"], "prevention": x["prevention"], "recurrence": 3}
            for x in corpus["mistakes"]]


def est_tokens(text: str) -> int:
    return int(round(len((text or "").split()) * TOKENS_PER_WORD))


# ── arms ──────────────────────────────────────────────────────────────────────────────────
# Every arm returns (predictions, info). A prediction is (label, fired_stem, score) as
# matched_conditions.confusion reads it: `fired_stem` is the mistake the arm surfaced (None for
# silence), right only when it equals the label; `score` is what the threshold sweeps.
# `info` carries tokens_total, ms_total, and anything the arm wants published.

def _check_calls(ledger: list[dict], born: dict, corpus: dict, project: str) -> tuple[list, dict]:
    preds, tokens, t_total = [], 0, 0.0
    for c in corpus["calls"]:
        t0 = time.perf_counter()
        hits = G.check(c["text"], project=project, path=c.get("path"), tool=c.get("tool"), guards=ledger)
        t_total += time.perf_counter() - t0
        fired = [born.get(h["id"]) for h in hits]
        fired = [f for f in fired if f]
        if not fired:
            preds.append((c["label"], None, 0.0))
            continue
        stem = c["label"] if c["label"] in fired else fired[0]
        preds.append((c["label"], stem, 1.0))
        tokens += sum(est_tokens(h["message"]) for h in hits)
    return preds, {"tokens_total": tokens, "ms_total": round(t_total * 1000, 3)}


def arm_guards_deterministic(corpus: dict, notes: list[dict]) -> tuple[list, dict]:
    ledger, born, none = [], {}, []
    for n in notes:
        g = G.propose_from_mistake(n, use_llm=False)
        if g is None:
            none.append(n["stem"])
            continue
        if G.register(ledger, g):
            born[g["id"]] = n["stem"]
    preds, info = _check_calls(ledger, born, corpus, corpus["project"])
    info.update({"n_guards": len(ledger), "notes_without_pattern": none,
                 "patterns": {born[g["id"]]: g["pattern"] for g in ledger}})
    return preds, info


def _llm_pattern(n: dict, cache: dict) -> tuple[str, str, bool]:
    """(pattern, message, from_cache) for one note, through the engine's own generator prompt."""
    key = f"{LLM}|{n['stem']}|{hashlib.sha1((n['title'] + n['desc'] + n['prevention']).encode('utf-8')).hexdigest()[:12]}"
    if key in cache:
        return cache[key]["pattern"], cache[key]["message"], True
    try:
        res = m.generate_json(G._GEN_PROMPT.format(title=n["title"], desc=n["desc"][:400],
                                                    prevention=n["prevention"][:400]), project=n["project"])
    except Exception:                                  # noqa: BLE001 - counted as empty
        res = {}
    pat = str((res or {}).get("pattern", "") or "").strip()
    msg = str((res or {}).get("message", "") or "").strip()
    cache[key] = {"pattern": pat, "message": msg}
    return pat, msg, False


def arm_guards_llm(corpus: dict, notes: list[dict]) -> tuple[list, dict]:
    os.environ["NEVERTWICE_CLOUD"] = "none"
    os.environ["NEVERTWICE_MODEL"] = LLM
    m.OLLAMA_MODEL = LLM               # the engine binds the name at import; the shell may export another
    cache = json.loads(LLM_CACHE.read_text(encoding="utf-8")) if LLM_CACHE.exists() else {}
    ledger, born, empty, unsafe, fresh = [], {}, [], [], 0
    for n in notes:
        pat, msg, cached = _llm_pattern(n, cache)
        fresh += 0 if cached else 1
        if not pat or not msg:
            empty.append(n["stem"])
            continue
        if not G.safe_pattern(pat):
            unsafe.append({"stem": n["stem"], "pattern": pat})
            continue
        g = G.make_guard(pat, msg, project=n["project"], born_from=[n["stem"]])
        if g and G.register(ledger, g):
            born[g["id"]] = n["stem"]
    LLM_CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    info = {"llm": m.OLLAMA_MODEL, "n_guards": len(ledger), "empty_patterns": empty, "unsafe_patterns": unsafe,
            "fresh_generations": fresh, "patterns": {born[g["id"]]: g["pattern"] for g in ledger}}
    if notes and len(empty) > 0.10 * len(notes):
        return [], {**info, "blocked": f"{len(empty)} of {len(notes)} notes came back with an empty pattern"}
    preds, cost = _check_calls(ledger, born, corpus, corpus["project"])
    return preds, {**info, **cost}


def arm_universal_pack(corpus: dict, notes: list[dict]) -> tuple[list, dict]:
    ledger = G.universal_pack()
    by_pattern = {}
    for n in notes:
        pat = G._deterministic_pattern(n)
        if pat:
            by_pattern.setdefault(pat, []).append(n["stem"])
    # a pack guard is "born from" every mistake whose own pattern it is
    born = {}
    preds, tokens, t_total = [], 0, 0.0
    for c in corpus["calls"]:
        t0 = time.perf_counter()
        hits = G.check(c["text"], project=None, path=c.get("path"), tool=c.get("tool"), guards=ledger)
        t_total += time.perf_counter() - t0
        if not hits:
            preds.append((c["label"], None, 0.0))
            continue
        stems = [s for h in hits for s in by_pattern.get(next((g["pattern"] for g in ledger if g["id"] == h["id"]), ""), [])]
        stem = c["label"] if c["label"] in stems else (stems[0] if stems else "universal-pack")
        preds.append((c["label"], stem, 1.0))
        tokens += sum(est_tokens(h["message"]) for h in hits)
    return preds, {"n_guards": len(ledger), "tokens_total": tokens, "ms_total": round(t_total * 1000, 3),
                   "covers": sorted(s for stems in by_pattern.values() for s in stems
                                    if any(g["pattern"] in by_pattern for g in ledger))}


def arm_linter(corpus: dict, notes: list[dict]) -> tuple[list, dict]:
    caught = {x["stem"]: bool(x["linter_or_test"]["caught"]) for x in corpus["mistakes"]}
    preds = []
    for c in corpus["calls"]:
        if c["label"]:
            preds.append((c["label"], c["label"] if caught[c["label"]] else None, 1.0 if caught[c["label"]] else 0.0))
        else:
            preds.append((None, None, 0.0))        # generous: a linter never false-alarms here
    return preds, {"tokens_total": 0, "ms_total": 0.0,
                   "note": "read from the corpus table, scored in the linter's favour: it fires on every "
                           "positive the table says it catches and never on a negative"}


def arm_prompt_recall(corpus: dict, notes: list[dict], embed: bool = True) -> tuple[list, dict]:
    """The cheap rival inside the product: recall the mistake notes by the tool call itself."""
    import api                                                   # noqa: PLC0415
    if not embed:
        m.embed_text = lambda *a, **k: None                      # text-only store: lexical recall
    project = corpus["project"]
    written = {}
    recs = []
    for n in notes:
        stem = m.write_typed_note("Mistakes", {"title": n["title"], "description": n["desc"],
                                               "prevention": n["prevention"], "confidence": 0.9},
                                  project, "2026-09-01", ["bench"], "mistake")
        if stem:
            written[stem] = n["stem"]
            recs.append((stem, "mistake", project, n["title"], n["desc"], n["prevention"], 0.9))
    m.update_embeddings(recs)
    embedded = sum(1 for e in m.load_embed_cache().values() if isinstance(e, dict) and e.get("vec"))
    preds, tokens, t_total = [], 0, 0.0
    for c in corpus["calls"]:
        t0 = time.perf_counter()
        hits = api.recall(c["text"], project=project, k=RECALL_K)
        t_total += time.perf_counter() - t0
        tokens += sum(est_tokens(f"{h.get('title', '')} {h.get('description', '')} {h.get('prevention', '')}")
                      for h in hits)                              # charged whether or not it helped
        if not hits:
            preds.append((c["label"], None, 0.0))
            continue
        stems = [written.get(h["stem"]) for h in hits]
        top = float(hits[0].get("score") or 0.0)
        stem = c["label"] if c["label"] in stems else stems[0]
        preds.append((c["label"], stem, max(top, 1e-6)))
    # the sweep knob is the top score, normalised by the largest seen so the grid applies
    mx = max((s for _, _, s in preds), default=1.0) or 1.0
    preds = [(lab, st, round(sc / mx, 4)) for lab, st, sc in preds]
    return preds, {"tokens_total": tokens, "ms_total": round(t_total * 1000, 3), "k": RECALL_K,
                   "notes_written": len(written), "notes_embedded": embedded,
                   "mode": "semantic + lexical" if embedded else "lexical only (no embedder)"}


def arm_never(corpus: dict, notes: list[dict]) -> tuple[list, dict]:
    return [(c["label"], None, 0.0) for c in corpus["calls"]], {"tokens_total": 0, "ms_total": 0.0}


ARMS = {
    "guards_deterministic": arm_guards_deterministic,
    "guards_llm": arm_guards_llm,
    "universal_pack": arm_universal_pack,
    "linter_or_test": arm_linter,
    "prompt_recall": arm_prompt_recall,
    "never": arm_never,
}


# ── scoring ───────────────────────────────────────────────────────────────────────────────

def curve(preds: list) -> list[dict]:
    out = []
    for t in MC.GRID:
        row = MC.rates(MC.confusion(preds, t))
        row["threshold"] = t
        out.append(row)
    return out


def subset_rates(preds: list, calls: list[dict], threshold: float, pick) -> dict:
    sub = [(p, c) for p, c in zip(preds, calls) if pick(c)]
    if not sub:
        return {}
    return MC.rates(MC.confusion([p for p, _ in sub], threshold))


def score_arm(preds: list, calls: list[dict], info: dict) -> dict:
    n = len(calls) or 1
    cv = curve(preds)
    matched = MC.at_matched_fpr(cv, TARGET_FPR)
    zero = MC.at_zero_false_alarms(cv)
    thr = matched["threshold"] if matched else None
    out = {"n_calls": len(calls), "curve": cv, f"at_fpr_{TARGET_FPR}": matched, "at_zero_false_alarms": zero,
           "tokens_per_call": round(info.get("tokens_total", 0) / n, 2),
           "ms_per_call": round(info.get("ms_total", 0.0) / n, 4)}
    #: The class split is a DIAGNOSTIC - which kinds of repeat an arm catches - not a result that
    #: has to earn a threshold first. Gating it on `thr` meant the one arm part 3.7 is about,
    #: `guards_deterministic` (26 guards distilled from this project's own notes), had no split at
    #: all: it sits at FPR 0.179 and never reaches the target, so the harness declined to answer
    #: the only question being asked of it. Every arm now gets a split at its OWN operating point,
    #: and the point is recorded beside it so a split taken at 0.179 can never be read as one
    #: taken at 0.
    split_thr = thr if thr is not None else (cv[0]["threshold"] if cv else None)
    if split_thr is not None:
        at = next((r for r in cv if r["threshold"] == split_thr), None)
        out["split_at"] = {"threshold": split_thr,
                           "false_positive_rate": (at or {}).get("false_positive_rate"),
                           "matched_target_fpr": thr is not None}
        out["hard_negatives"] = subset_rates(preds, calls, split_thr, lambda c: c["label"] is None and c.get("hard"))
        out["generic"] = subset_rates(preds, calls, split_thr, lambda c: c["family"] == "generic")
        out["project"] = subset_rates(preds, calls, split_thr, lambda c: c["family"] == "project")
    if thr is not None:
        pos = sum(1 for c in calls if c["label"])
        out["recall_ci"] = list(MC.wilson(int(round((matched["recall"] or 0.0) * pos)), pos))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--arms", default="guards_deterministic,universal_pack,linter_or_test,prompt_recall,never")
    ap.add_argument("--llm", action="store_true", help="add the model-written patterns arm (GPU)")
    ap.add_argument("--no-embed", action="store_true", help="prompt_recall on a text-only store")
    ap.add_argument("--limit", type=int, default=0, help="first N mistakes only (a smoke run)")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    corpus = load_corpus(Path(args.corpus))
    if args.limit:
        keep = {x["stem"] for x in corpus["mistakes"][:args.limit]}
        corpus["mistakes"] = [x for x in corpus["mistakes"] if x["stem"] in keep]
        corpus["calls"] = [c for c in corpus["calls"] if (c["label"] or c.get("near")) in keep]
    notes = notes_of(corpus)
    names = [a for a in args.arms.split(",") if a] + (["guards_llm"] if args.llm else [])
    print(f"corpus {corpus['name']}  mistakes {len(notes)}  calls {len(corpus['calls'])}  sha256={corpus['sha256'][:16]}")
    print(f"store  {sandbox_guard.store()}\n")
    out = {"corpus": {"name": corpus["name"], "sha256": corpus["sha256"], "path": corpus["path"],
                      "counts": corpus["counts"]},
           "target_fpr": TARGET_FPR, "recall_k": RECALL_K, "tokens_per_word": TOKENS_PER_WORD,
           "llm": LLM if args.llm else None, "embedder": None if args.no_embed else EMBED_MODEL, "arms": {}}
    for name in names:
        fn = ARMS.get(name)
        if fn is None:
            print(f"- {name}: unknown arm (have: {', '.join(ARMS)})")
            continue
        t0 = time.time()
        preds, info = fn(corpus, notes, embed=not args.no_embed) if name == "prompt_recall" else fn(corpus, notes)
        if info.get("blocked"):
            out["arms"][name] = {"blocked": info["blocked"], **{k: v for k, v in info.items() if k != "blocked"}}
            print(f"- {name}: BLOCKED - {info['blocked']}\n")
            continue
        sc = score_arm(preds, corpus["calls"], info)
        sc["info"] = {k: v for k, v in info.items() if k not in ("tokens_total", "ms_total")}
        sc["seconds"] = round(time.time() - t0, 1)
        out["arms"][name] = sc
        mt = sc.get(f"at_fpr_{TARGET_FPR}")
        hn = sc.get("hard_negatives") or {}
        print(f"- {name}")
        if mt:
            print(f"  at FPR<={TARGET_FPR}: recall {mt['recall']} {sc.get('recall_ci')}  precision {mt['precision']}  "
                  f"fpr {mt['false_positive_rate']}  | hard-negative fpr {hn.get('false_positive_rate')}  "
                  f"| generic recall {(sc.get('generic') or {}).get('recall')}  project recall {(sc.get('project') or {}).get('recall')}")
        else:
            print(f"  no operating point reaches FPR<={TARGET_FPR}")
        print(f"  tokens/call {sc['tokens_per_call']}  ms/call {sc['ms_per_call']}  "
              + (f"guards {info.get('n_guards')}" if "n_guards" in info else "") + "\n")
    if args.save:
        Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
