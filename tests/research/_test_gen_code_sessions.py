"""`research/gen_code_sessions.py` (ledger J3): the program's half of the corpus is checkable
without a model - the fact draw is seeded, the gold follows from the draw, a compliant
transcript passes verification, a non-compliant one is retried, a leaked value is scrubbed and
a still-missing fact gets a marked fallback sentence. The model's half is faked here.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import gen_code_sessions as gcs  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def compliant(prompt: str, seed: int) -> dict:
    """A fake model that states every required sentence and nothing forbidden."""
    must = re.findall(r"^- (?!mistake:)(.+)$", prompt.split("The transcript MUST contain")[1].split("\n\n")[0], re.M)
    prevs = re.findall(r"Prevention \(state this sentence verbatim\): (.+)$", prompt, re.M)
    lines = ["user: let's look at the service today", "tool: $ pytest -q -> 1 failed: tests/test_api.py::test_ping"]
    lines += [f"assistant: Confirmed: {s}." for s in must if s != "(none)"]
    lines += [f"assistant: Lesson recorded: {p}" for p in prevs]
    lines += ["tool: edit app/api.py -> fixed the import", "assistant: green again."]
    return {"transcript": "\n".join(lines)}


calls = {"n": 0}


def flaky(prompt: str, seed: int) -> dict:
    """Omits the first required sentence and leaks a forbidden value on every attempt."""
    calls["n"] += 1
    d = compliant(prompt, seed)
    lines = d["transcript"].split("\n")
    conf = [i for i, ln in enumerate(lines) if ln.startswith("assistant: Confirmed:")]
    if conf:
        del lines[conf[0]]
    forbid = prompt.split("must NOT mention any of these values: ")[1].split(".\n")[0]
    first = forbid.split(", ")[0]
    if first != "(none)":
        lines.append(f"assistant: by the way it uses {first} too")
    return {"transcript": "\n".join(lines)}


print("\n- the draw is seeded and the gold follows from it -")
import random  # noqa: E402

p1 = gcs.plan_project(0, random.Random(gcs.SEED), 5)
p2 = gcs.plan_project(0, random.Random(gcs.SEED), 5)
check("the same seed draws the same project", [f["value"] for f in p1["facts"]] == [f["value"] for f in p2["facts"]])
check("eight facts of distinct kinds, two replaced, three lessons",
      len(p1["facts"]) == 8 and len({f["key"] for f in p1["facts"]}) == 8 and len(p1["replaced"]) == 2 and len(p1["lessons"]) == 3)
check("a replacement changes the value", all(r["old"] != r["new"] for r in p1["replaced"]))
qs = gcs.questions_for(p1)
types = {q["type"] for q in qs}
check("four question types", types == {"fact", "current", "lesson", "situation"}, str(types))
cur = [q for q in qs if q["type"] == "current"]
check("a current question carries the old value as a stale marker and points at the replacing session",
      all(q["stale_markers"] and q["markers"] and q["gold_sessions"][0].endswith("-s3") for q in cur), str(cur[:1]))
fact = [q for q in qs if q["type"] == "fact"]
check("a replaced fact is not also asked as a plain fact", not any(q["id"].split("-fact-")[1] in {r["key"] for r in p1["replaced"]} for q in fact))
sit = [q for q in qs if q["type"] == "situation"]
check("a situation question is a tool call whose answer is the note", all(q["tool"] and q["answer"].startswith("m-") for q in sit))
check("markers carry compact variants", "30s" in gcs.markers_for("timeout", "30 seconds") and gcs.markers_for("port", "8017") == ["8017"])

print("\n- verification, retries, scrubbing, fallback -")
must, forbid, phrases = gcs.required(p1, 0)
check("session one requires its facts and lessons and forbids the others' values", must and forbid and len(must) == len(phrases))
text = "\n".join(f"assistant: {ph}" for ph in phrases)
check("a compliant text verifies clean", gcs.verify(text, must, forbid) == ([], []))
missing, leaked = gcs.verify("assistant: nothing here " + forbid[0], must, forbid)
check("a missing fact and a leaked value are both reported", missing and leaked == [forbid[0]], f"{missing} {leaked}")

cache = {}
rec = gcs.generate_session(p1, 0, cache, call=compliant)
check("a compliant model needs one attempt and no fallback", rec["attempts"] == 1 and rec["fallback_sentences"] == [], str(rec)[:200])
check("the session is cached by its prompt", len(cache) == 1 and gcs.generate_session(p1, 0, cache, call=compliant) is rec)
calls["n"] = 0
rec2 = gcs.generate_session(p1, 1, {}, call=flaky)
check("a non-compliant model is retried the declared number of times", calls["n"] == gcs.RETRIES and rec2["attempts"] == gcs.RETRIES)
check("the still-missing fact gets a marked fallback sentence", len(rec2["fallback_sentences"]) == 1 and "For the record" in rec2["text"], str(rec2["fallback_sentences"]))
_, forbid1, _ = gcs.required(p1, 1)
check("a leaked forbidden value is scrubbed from the text", not any(gcs.hit([v], rec2["text"]) for v in forbid1))

print("\n- a small build -")
data = gcs.build(2, 5, {}, call=compliant, verbose=False)
c = data["counts"]
check("two projects, ten sessions, the questions counted by type",
      c["projects"] == 2 and c["sessions"] == 10 and c["questions"] == sum(c["by_type"].values()) and c["by_type"]["current"] == 4, str(c))
check("every question's gold session exists", all(any(s["id"] == g for s in p["sessions"]) for p in data["projects"] for q in p["questions"] for g in q["gold_sessions"]))
check("the noise session states no fact", all(not s["states"] and not s["replaces"] for p in data["projects"] for s in p["sessions"] if s["noise"]))
check("a fake caller leaves no runtime stamp", data["generator_runtime"] == {})
check("the dump is stable", gcs.dump(data) == gcs.dump(gcs.build(2, 5, {}, call=compliant, verbose=False)))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
