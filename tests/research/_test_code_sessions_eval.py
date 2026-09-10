"""`research/code_sessions_eval.py` (ledger J3): the scoring behind the code-session stand,
without a model. A fact is right when the reader's answer carries a marker; a `current` answer
that carries the old value and not the new one is stale; a situation is caught when a top-three
text carries the prevention sentence; the brackets are 0 and 1 by construction; the corpus gates
read off the summary. Contexts, answers and verdicts are faked into a temporary cache directory.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import gen_code_sessions as gcs  # noqa: E402
import code_sessions_eval as cse  # noqa: E402
import frontier_eval as fe  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def compliant(prompt: str, seed: int) -> dict:
    import re
    must = re.findall(r"^- (?!mistake:)(.+)$", prompt.split("The transcript MUST contain")[1].split("\n\n")[0], re.M)
    prevs = re.findall(r"Prevention \(state this sentence verbatim\): (.+)$", prompt, re.M)
    lines = [f"assistant: Confirmed: {s}." for s in must if s != "(none)"] + [f"assistant: Lesson: {p}" for p in prevs]
    return {"transcript": "\n".join(lines) or "assistant: routine maintenance, nothing to record"}


tmp = Path(tempfile.mkdtemp(prefix="codesess_test_"))
cse.DATA = tmp
cse._ctx_path = lambda arm: tmp / f"ctx_{arm}.json"
cse._cache_path = lambda stage: tmp / f"{stage}.json"

corpus = gcs.build(2, 5, {}, call=compliant, verbose=False)
corpus["sha256"] = "test"
qs = cse.questions(corpus)
pool = cse.pool_of(corpus)

print("\n- markers and hits -")
check("a marker hit folds separators and case", cse.hit(["beta_dashboard"], "the BETA-DASHBOARD flag") and not cse.hit(["8017"], "port 8018"))
check("lesson and situation questions carry the prevention sentence",
      all(q["prevention"] for q in qs if q["type"] in ("lesson", "situation")))
sit = next(q for q in qs if q["type"] == "situation")
check("a situation is caught when a top-three text carries the prevention",
      cse.situation_hit([{"text": "x"}, {"text": "note: " + sit["prevention"]}], sit["prevention"])
      and not cse.situation_hit([{"text": "x"}] * 3 + [{"text": sit["prevention"]}], sit["prevention"]))

print("\n- scoring from faked answers -")
reader, judge, arm = "reader", "judge", "nevertwice_full"
answers, verdicts, ctx = {}, {}, {}
for q in qs:
    if q["type"] == "situation":
        # the right note in the top three for the first project, absent for the second
        ctx[q["id"]] = ([{"text": "note: " + q["prevention"]}] if q["project"] == "cs000" else [{"text": "unrelated"}])
        continue
    context = f"ctx for {q['id']}"
    key = fe._akey(reader, arm, cse.K, q["id"], context)
    if q["type"] == "fact":
        ans = q["answer"] if q["project"] == "cs000" else "I do not know"
    elif q["type"] == "current":
        # first project answers the new value; the second repeats the old one - stale
        ans = q["answer"] if q["project"] == "cs000" else q["stale_markers"][0]
    else:
        ans = "the lesson"
        verdicts[f"{judge}|{key}"] = q["project"] == "cs000"
    answers[key] = {"answer": ans, "prompt_tokens": 100, "context_chars": len(context)}
sc = cse.score_answers(qs, answers, verdicts, ctx, reader, arm, judge)
n_fact = sum(1 for q in qs if q["type"] == "fact")
check("fact accuracy is the marker rate", sc["fact"]["n"] == n_fact and abs(sc["fact"]["accuracy"] - 0.5) < 0.01, str(sc["fact"]))
check("a current answer with the old value and not the new is stale",
      sc["current"]["accuracy"] == 0.5 and sc["current"]["stale_rate"] == 0.5, str(sc["current"]))
check("lesson accuracy comes from the judge", sc["lesson"]["accuracy"] == 0.5, str(sc["lesson"]))
check("situation recall is retrieval only", sc["situation"]["accuracy"] == 0.5 and "mean_prompt_tokens" not in sc["situation"], str(sc["situation"]))
check("core folds fact, current and lesson", sc["core"]["n"] == sc["fact"]["n"] + sc["current"]["n"] + sc["lesson"]["n"])
check("tokens are the reader's count", sc["fact"]["mean_prompt_tokens"] == 100.0)
none_sc = cse.score_answers(qs, {}, {}, {}, reader, "none", judge)
check("the none bracket scores situations at zero by construction", none_sc["situation"]["accuracy"] == 0.0)
orac_sc = cse.score_answers(qs, {}, {}, {}, reader, "oracle", judge)
check("the oracle bracket scores situations at one by construction", orac_sc["situation"]["accuracy"] == 1.0)

print("\n- the summary and the corpus gates -")
fe._save(cse._cache_path("answers"), answers)
fe._save(cse._cache_path("verdicts"), verdicts)
fe._save(cse._ctx_path(arm), {**ctx, "_ingest": {"sessions": 10, "errors": 0}})
res = cse.summarise([arm], qs, corpus, reader, judge)
check("the arm and its ingest are in the summary", arm in res["arms"] and res["ingest"][arm]["sessions"] == 10)
check("gates are None when a bracket is missing", res["corpus_gates"]["none_le_0.10"] is None and res["corpus_gates"]["corpus_separates"] is False)
# brackets answered: none knows nothing, oracle knows everything, naive half
for brk, right in (("none", False), ("oracle", True)):
    for q in qs:
        if q["type"] == "situation":
            continue
        key = fe._akey(reader, brk, cse.K, q["id"], f"{brk} ctx {q['id']}")
        if q["type"] == "lesson":
            verdicts[f"{judge}|{key}"] = right
            answers[key] = {"answer": "x", "prompt_tokens": 50, "context_chars": 1}
        else:
            answers[key] = {"answer": q["answer"] if right else "unknown", "prompt_tokens": 50, "context_chars": 1}
fe._save(cse._cache_path("answers"), answers)
fe._save(cse._cache_path("verdicts"), verdicts)
res = cse.summarise([arm], qs, corpus, reader, judge)
g = res["corpus_gates"]
check("with none at 0 and oracle at 1 the two bracket gates hold", g["none_le_0.10"] is True and g["oracle_ge_0.60"] is True, str(g))
check("the naive gate waits for the naive arm", g["naive_le_oracle_minus_0.20"] is None and g["corpus_separates"] is False)

print("\n- the floor's ranking -")
docs = {"a": "the API server listens on port 8017 and more", "b": "unrelated text about cats"}
check("term overlap ranks the session that shares the question's words first",
      cse._overlap_rank("what port does the API server listen on", docs, 2)[0] == "a")

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
