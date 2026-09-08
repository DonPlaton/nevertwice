"""`research/frontier_eval.py`: the accuracy-per-token stand's arithmetic, without a model.

The reader and the judge are local models and are not run here; what is pinned is everything
around them - the stratified sample, the context assembly under a character budget, the
per-arm summary over the caches (accuracy, Wilson interval, tokens from the reader's own
count), the brackets, and the judge-agreement figure.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

import json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import frontier_eval as fe  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


print("\n- the sample -")
data = [{"question_id": f"q{i}", "question_type": t, "question": "?", "answer": "a", "answer_session_ids": ["s"]}
        for i, t in enumerate(["multi-session", "temporal-reasoning", "multi-session", "knowledge-update",
                               "multi-session", "temporal-reasoning"])]
s = fe.stratified(data, 2)
check("per_type caps each type in corpus order", [e["question_id"] for e in s] == ["q0", "q1", "q2", "q3", "q5"])
check("zero means everything", fe.stratified(data, 0) == data)

print("\n- context assembly under a budget -")
items = [{"id": "a", "text": "x" * 50}, {"id": "b", "text": "y" * 50}, {"id": "c", "text": ""}, {"id": "d", "text": "z" * 50}]
ctx = fe._context_text(items, 2, 1000)
check("top-k items joined, empty ones skipped", ctx == "x" * 50 + "\n\n---\n\n" + "y" * 50)
ctx = fe._context_text(items, 4, 120)
check("the budget cuts the tail, counting separators: the last item is trimmed to the room left",
      len(ctx) <= 132 and ctx.startswith("x" * 50) and ctx.count("z") == 8, str(len(ctx)))
check("k=1 is one item", fe._context_text(items, 1, 1000) == "x" * 50)

print("\n- the summary over the caches -")
tmp = Path(fe.DATA)
saved = (fe._cache_path("answers"), fe._cache_path("verdicts"))
backup = {p: (p.read_text(encoding="utf-8") if p.exists() else None) for p in saved}
try:
    qids = [e["question_id"] for e in data]
    answers, verdicts = {}, {}

    def put(arm, k, qid, tokens, verdict, ctx="some context", judge="j"):
        akey = fe._akey("r", arm, k, qid, ctx)
        answers[akey] = {"answer": "a", "prompt_tokens": tokens, "context_chars": len(ctx)}
        verdicts[f"{judge}|{akey}"] = verdict
        return akey

    for arm in ("nevertwice_whole", "mem0"):
        for k in fe.KS:
            for i, qid in enumerate(qids):
                put(arm, k, qid, 100 * k + i, (i % 2 == 0) if arm == "mem0" else (i != 5))
    for qid in qids:
        put("none", 0, qid, 20, False)
        put("oracle", 99, qid, 900, True)
    for i, qid in enumerate(qids[:4]):
        verdicts[f"j2|{fe._akey('r', 'nevertwice_whole', 5, qid, 'some context')}"] = (i != 0)  # one disagreement
    fe._save(fe._cache_path("answers"), answers)
    fe._save(fe._cache_path("verdicts"), verdicts)
    res = fe.summarise(["nevertwice_whole", "mem0"], data, "r", "j", "j2")
    nt5 = res["arms"]["nevertwice_whole"]["5"]
    check("accuracy per arm and k from the verdicts", nt5["accuracy"] == round(5 / 6, 4) and nt5["n"] == 6)
    check("a Wilson interval brackets it", nt5["ci"][0] < nt5["accuracy"] < nt5["ci"][1])
    check("tokens come from the reader's own count, averaged", nt5["mean_prompt_tokens"] == round((500 * 6 + 15) / 6, 1))
    check("the other arm at another k", res["arms"]["mem0"]["1"]["accuracy"] == 0.5)
    check("brackets: none and oracle", res["brackets"]["none"]["accuracy"] == 0.0 and res["brackets"]["oracle"]["accuracy"] == 1.0)
    ja = res["judge_agreement"]
    check("judge agreement over the doubly judged answers", ja["n"] == 4 and ja["rate"] == 0.75, str(ja))
    check("the artifact names reader, judge and second judge",
          res["judge"] == "j" and res["second_judge"] == "j2" and "reader" in res)

    print("\n- an answer belongs to the context it was read from -")
    # The ranker moving is exactly what must invalidate an answer, and the only thing the cache
    # can see of the ranker is the bytes it produced. Before the digest was part of the key, a
    # re-run after a weight change served every answer from this file.
    k1 = fe._akey("r", "nevertwice_whole", 5, qids[0], "context A")
    k2 = fe._akey("r", "nevertwice_whole", 5, qids[0], "context B")
    check("a different context is a different key", k1 != k2)
    check("...and the same context is the same key",
          k1 == fe._akey("r", "nevertwice_whole", 5, qids[0], "context A"))
    check("the key still names reader, arm, k and question",
          k1.split("|")[:4] == ["r", "nevertwice_whole", "5", qids[0]])
    found = fe._find_akey({k1: {}}, "r", "nevertwice_whole", 5, qids[0])
    check("the summary finds an answer whatever context produced it", found == k1)
    check("a key from before the digest is not found (it is re-read, not reused)",
          fe._find_akey({f"r|nevertwice_whole|5|{qids[0]}": {}}, "r", "nevertwice_whole", 5, qids[0]) is None)
    check("another question is not found", fe._find_akey({k1: {}}, "r", "nevertwice_whole", 5, "other") is None)
finally:
    for p, text in backup.items():
        if text is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(text, encoding="utf-8")

print("\n- wilson -")
lo, hi = fe.wilson(5, 6)
check("wilson interval for 5 of 6", 0.4 < lo < 0.84 < hi <= 1.0)
check("wilson of nothing is empty", fe.wilson(0, 0) == (0.0, 0.0))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
