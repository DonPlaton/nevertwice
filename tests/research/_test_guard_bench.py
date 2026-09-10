"""`research/gen_guard_bench.py` and `research/guard_bench.py` (ledger J6): the corpus is
deterministic and shaped as the ledger demands; the arms' arithmetic is checkable without a
model - a deterministic guard fires on the repeat and not on the correct code, the linter arm
reads the table, the floor is silent, the matched-rate reading and the subsets come out of the
same confusion counts `matched_conditions` uses.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import gen_guard_bench as gen  # noqa: E402
import guard_bench as gb  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


print("\n- the corpus is deterministic and shaped as declared -")
a, b = gen.dump(gen.build()), gen.dump(gen.build())
check("two builds are byte-identical", a == b)
committed = gen.OUT
check("the committed corpus matches its generator",
      committed.exists() and hashlib.sha256(committed.read_bytes()).hexdigest() == hashlib.sha256(a).hexdigest(),
      "run python research/gen_guard_bench.py")
data = json.loads(a.decode("utf-8"))
c = data["counts"]
check("at least forty mistakes, two families", c["mistakes"] >= 40 and c["generic"] >= 15 and c["project"] >= 15, str(c))
check("at least eighty positives and one hundred twenty negatives", c["positives"] >= 80 and c["negatives"] >= 120, str(c))
by_stem = {}
for call in data["calls"]:
    by_stem.setdefault(call["label"] or call["near"], []).append(call)
check("every mistake has two positives and three negatives, one of them hard",
      all(sum(1 for x in v if x["label"]) >= 2 and sum(1 for x in v if not x["label"]) >= 3
          and any(x["hard"] for x in v if not x["label"]) for v in by_stem.values()))
check("every call names a tool and carries text", all(x.get("tool") and x.get("text") for x in data["calls"]))
check("every mistake says whether a linter catches it",
      all(isinstance(x["linter_or_test"]["caught"], bool) and x["linter_or_test"]["by"] for x in data["mistakes"]))
check("ids are unique", len({x["id"] for x in data["calls"]}) == len(data["calls"]))

print("\n- a mini corpus: the arms' arithmetic without a model -")
mini = {
    "name": "mini", "project": "svc", "sha256": "x", "path": "x",
    "mistakes": [
        {"stem": "m-bare", "family": "generic", "project": "svc", "title": "bare except swallowed an error",
         "desc": "a bare except: hid the failure", "prevention": "catch the specific exception, never a bare except:",
         "linter_or_test": {"caught": True, "by": "ruff E722"}},
        {"stem": "m-ms", "family": "project", "project": "svc", "title": "set_timeout got seconds",
         "desc": "client.set_timeout(5) meant five milliseconds", "prevention": "set_timeout takes milliseconds",
         "linter_or_test": {"caught": False, "by": "a unit convention"}},
    ],
    "calls": [
        {"id": "b-p1", "label": "m-bare", "family": "generic", "tool": "Edit", "path": "a.py", "text": "try:\n    x()\nexcept:\n    pass", "hard": False},
        {"id": "b-p2", "label": "m-bare", "family": "generic", "tool": "Write", "path": "b.py", "text": "except:\n    return None", "hard": False},
        {"id": "b-n1", "label": None, "near": "m-bare", "family": "generic", "tool": "Edit", "path": "a.py", "text": "except ValueError:\n    pass", "hard": True},
        {"id": "b-n2", "label": None, "near": "m-bare", "family": "generic", "tool": "Bash", "text": "git status", "hard": False},
        {"id": "m-p1", "label": "m-ms", "family": "project", "tool": "Edit", "path": "c.py", "text": "client.set_timeout(5)", "hard": False},
        {"id": "m-n1", "label": None, "near": "m-ms", "family": "project", "tool": "Edit", "path": "c.py", "text": "client.set_retries(5)", "hard": True},
    ],
    "counts": {},
}
notes = gb.notes_of(mini)
check("notes carry the three fields the generator reads", all(n["title"] and n["desc"] and n["prevention"] for n in notes))

preds, info = gb.arm_guards_deterministic(mini, notes)
labels = [p[0] for p in preds]
fired = {p[0] or f"neg{i}": p[1] for i, p in enumerate(preds)}
check("the bare-except guard fires on both repeats and names the right mistake",
      preds[0][1] == "m-bare" and preds[1][1] == "m-bare", str(preds))
check("it stays silent on the specific except and on an unrelated command", preds[2][1] is None and preds[3][1] is None, str(preds))
check("the project mistake gets a literal pattern from its dotted call (a bare `set_timeout(5)` would get none - "
      "the generator lifts backticks, dotted calls, quoted literals and asserts, nothing else)",
      info["n_guards"] == 2 and "m-ms" in info["patterns"], str(info))
check("the project repeat fires and the same-identifier negative does not", preds[4][1] == "m-ms" and preds[5][1] is None, str(preds))
sc = gb.score_arm(preds, mini["calls"], info)
mt = sc["at_fpr_0.05"]
check("a matched-rate point exists and reads the catch", mt is not None and mt["recall"] >= 0.5, str(mt))
check("subsets are read at the same operating point", "generic" in sc and "project" in sc and sc["generic"]["recall"] == 1.0, str(sc.get("generic")))
check("hard negatives are read separately", sc["hard_negatives"]["false_positive_rate"] is not None, str(sc.get("hard_negatives")))
check("tokens are charged only on a hit", sc["tokens_per_call"] > 0 and info["tokens_total"] > 0)

preds, info = gb.arm_linter(mini, notes)
check("the linter arm catches what the table says and nothing else",
      preds[0][1] == "m-bare" and preds[4][1] is None and all(p[1] is None for p in preds if p[0] is None), str(preds))
sc = gb.score_arm(preds, mini["calls"], info)
check("the linter's project recall is zero here", sc["project"]["recall"] == 0.0 and sc["generic"]["recall"] == 1.0, str(sc.get("project")))

preds, info = gb.arm_never(mini, notes)
sc = gb.score_arm(preds, mini["calls"], info)
check("the floor is silent: recall 0, fpr 0, tokens 0",
      sc["at_fpr_0.05"]["recall"] == 0.0 and sc["at_fpr_0.05"]["false_positive_rate"] == 0.0 and sc["tokens_per_call"] == 0)

preds, info = gb.arm_universal_pack(mini, notes)
check("the cold-start pack knows the bare except and not the project fact",
      preds[0][1] == "m-bare" and preds[4][1] is None, str(preds))

print("\n- the curve machinery -")
cv = gb.curve([("a", "a", 1.0), ("b", None, 0.0), (None, None, 0.0), (None, "a", 1.0)])
check("a binary arm has one non-trivial operating point", cv[0]["recall"] == 0.5 and cv[0]["false_positive_rate"] == 0.5)
check("the grid ends where nothing fires", cv[-1]["recall"] == 0.0 or cv[-1]["threshold"] == 1.0)

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
