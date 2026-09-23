#!/usr/bin/env python3
"""K8 step 3 - the judge's accuracy on pairs whose truth is known, and its price in tokens.

The layer-3 judge (the same-fact prompt of ledger K7, `memory_hook._JUDGE_PROMPT`) is run over
every same-slug collision pair `research/k8_collisions.py` recorded, outside any session chain -
no cache confound, no hook millisecond - and each verdict is scored against the pair's truth:
a replacement or a restatement should read `replaces`, a different fact `separate`. Precision and
recall per class, the confusion matrix, and the tokens each verdict cost (`prompt_eval_count` +
`eval_count` from the Ollama response) are the numbers layer 3 publishes and the number K7 was
really worth. Pairs whose truth is unclear (a boilerplate item under the fact's title) are judged
too and reported apart.

The call goes to the Ollama API directly so the token counts are visible; the prompt, the
options and the parsing are the engine's.

    python research/k8_judge_eval.py research/results/k8_collisions_explicit.json \
        research/results/k8_collisions_implicit.json research/results/k8_collisions_asof.json \
        --out research/results/k8_judge_eval.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - the declaration the sandbox lint reads, before any nevertwice import
sandbox_guard.isolate(prefix="nevertwice_k8_")
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
sys.path.insert(0, str(HERE))
from k8_skeleton import label_pairs  # noqa: E402
import _provenance as prov  # noqa: E402 - measured_at: {commit, utc, dirty} on the artifact

MODEL = os.environ.get("SUPERSESSION_LLM", "qwen3-coder:30b")
URL = os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/generate"
EXPECTED = {"replaces": "replaces", "restates": "replaces", "separate": "separate"}


def judge(old_title: str, old_desc: str, new_desc: str) -> dict:
    prompt = m._JUDGE_PROMPT.format(old=f"{old_title} - {(old_desc or '')[:600]}", new=(new_desc or "")[:600])
    payload = json.dumps({"model": MODEL, "prompt": prompt, "format": "json", "stream": False, "think": False,
                          "options": {"temperature": 0.0, "num_ctx": 16384}}).encode("utf-8")
    req = urllib.request.Request(URL, data=payload, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read())
    raw = (data.get("response") or "").strip()
    try:
        parsed = json.loads(m._strip_json_fence(raw))
    except Exception:                                            # noqa: BLE001 - an unparsable answer is "none"
        parsed = {}
    rel = str((parsed or {}).get("relation", "")).strip().lower() if isinstance(parsed, dict) else ""
    verdict = "replaces" if rel.startswith("replace") else ("separate" if rel.startswith(("separate", "different")) else None)
    return {"verdict": verdict, "old_settles": (parsed or {}).get("old_settles") if isinstance(parsed, dict) else None,
            "new_settles": (parsed or {}).get("new_settles") if isinstance(parsed, dict) else None,
            "prompt_tokens": data.get("prompt_eval_count"), "eval_tokens": data.get("eval_count"),
            "seconds": round(time.time() - t0, 2)}


def prf(rows: list[dict], cls: str) -> dict:
    tp = sum(1 for r in rows if r["expected"] == cls and r["verdict"] == cls)
    fp = sum(1 for r in rows if r["expected"] != cls and r["verdict"] == cls)
    fn = sum(1 for r in rows if r["expected"] == cls and r["verdict"] != cls)
    p = tp / (tp + fp) if tp + fp else None
    rc = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4) if p is not None else None,
            "recall": round(rc, 4) if rc is not None else None}


def summarise(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["expected"]]
    toks = [r["prompt_tokens"] + r["eval_tokens"] for r in rows
            if isinstance(r.get("prompt_tokens"), int) and isinstance(r.get("eval_tokens"), int)]
    out = {"n_judged": len(rows), "n_scored": len(scored),
           "by_truth": dict(Counter(r["pair_truth"] for r in rows)),
           "confusion": {f"{r['expected']}->{r['verdict']}": 0 for r in scored},
           "replaces": prf(scored, "replaces"), "separate": prf(scored, "separate"),
           "unanswered": sum(1 for r in rows if r["verdict"] is None),
           "accuracy": round(sum(1 for r in scored if r["verdict"] == r["expected"]) / len(scored), 4) if scored else None,
           "tokens_per_pair": {"mean": round(statistics.mean(toks), 1) if toks else None,
                               "median": statistics.median(toks) if toks else None,
                               "min": min(toks) if toks else None, "max": max(toks) if toks else None,
                               "prompt_mean": round(statistics.mean(r["prompt_tokens"] for r in rows if isinstance(r.get("prompt_tokens"), int)), 1) if toks else None,
                               "eval_mean": round(statistics.mean(r["eval_tokens"] for r in rows if isinstance(r.get("eval_tokens"), int)), 1) if toks else None},
           "seconds_per_pair": round(statistics.mean(r["seconds"] for r in rows), 2) if rows else None,
           "unclear_verdicts": dict(Counter(r["verdict"] for r in rows if r["pair_truth"] == "unclear"))}
    for r in scored:
        out["confusion"][f"{r['expected']}->{r['verdict']}"] += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    corpus: dict = {}
    stands: dict = {}
    for f in a.files:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        ds = ROOT / d["dataset"]["path"]
        if ds.exists():
            for c in json.loads(ds.read_text(encoding="utf-8"))["cases"]:
                corpus[c["id"]] = c
        else:
            # also-fix (xhigh review): a missing dataset used to silently score against
            # whatever corpus the earlier files in `a.files` had already loaded (or an
            # empty one, on the first file) - fail loudly instead of reporting a number
            # that looks legitimate but is missing this file's ground truth entirely.
            print(f"missing dataset for {f}: {ds} does not exist", file=sys.stderr)
            return 1
        label_pairs(d["pairs"], corpus)
        stands[Path(f).stem.replace("k8_collisions_", "")] = d
    all_rows: list[dict] = []
    per_stand: dict = {}
    t0 = time.time()
    for name, d in stands.items():
        rows = []
        pairs = d["pairs"][:a.limit] if a.limit else d["pairs"]
        for i, p in enumerate(pairs):
            try:
                v = judge(p["old_title"], p["old_desc"], p["new_desc"])
            except Exception as e:                               # noqa: BLE001 - recorded, the run goes on
                v = {"verdict": None, "error": f"{type(e).__name__}: {e}", "prompt_tokens": None,
                     "eval_tokens": None, "seconds": 0.0}
            row = {"stand": name, "case": p["case"], "branch": p["branch"], "pair_truth": p["pair_truth"],
                   "expected": EXPECTED.get(p["pair_truth"]), **v}
            rows.append(row)
            print(f"  [{name} {i + 1}/{len(pairs)}] {p['case']:22s} {p['pair_truth']:9s} -> {v['verdict']}"
                  f"  tok {v.get('prompt_tokens')}+{v.get('eval_tokens')}  {v['seconds']}s", flush=True)
        per_stand[name] = summarise(rows)
        all_rows += rows
    out = {"model": MODEL, "prompt": "memory_hook._JUDGE_PROMPT", "temperature": 0.0,
           "engine_commit": next(iter(stands.values())).get("engine_commit"),
           "pooled": summarise(all_rows), "per_stand": per_stand, "rows": all_rows,
           "seconds": round(time.time() - t0, 1)}
    print(json.dumps({"pooled": out["pooled"], "per_stand": {k: {kk: vv for kk, vv in v.items() if kk != "confusion"}
                                                             for k, v in per_stand.items()}}, indent=1))
    if a.out:
        prov.stamp(out)
        Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("written", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
