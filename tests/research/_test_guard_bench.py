"""`research/gen_guard_bench.py` and `research/guard_bench.py` (ledger J6): the corpus is
deterministic and shaped as the ledger demands; the arms' arithmetic is checkable without a
model - a deterministic guard fires on the repeat and not on the correct code, the linter arm
reads the table, the floor is silent, the matched-rate reading and the subsets come out of the
same confusion counts `matched_conditions` uses.
"""
import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import gen_guard_bench as gen  # noqa: E402
import guard_bench as gb  # noqa: E402
import _ollama_pacer as pacer  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
import remeasure as remeasure_mod  # noqa: E402 - K16(2)/K25: row_refusal on the RESULT artifact

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

# ── R-v2-ports item 9A: install(mode="observe") wired into every arm's own Ollama traffic ──
# guard_bench.py publishes a raw wall-clock claim (ms_per_call, PREREG-V2-2026-09-24 P5) that
# must never include this module's own artificial pacing - "observe" mode installs the
# identical hooks (a bypass or a failed embed still marks the run invalid) with pacing/retry
# both disabled (pace_sleep_s stays exactly 0, the proof a reader checks).

class _JsonResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen_failing_embed(*a, **kw):
    """Every `/api/embed` call fails with a genuine (non-port-exhaustion) 500 - the F1
    failure P0(a) exists to catch. Any other endpoint succeeds, so the run itself does not
    crash on something unrelated to embedding."""
    req = a[0] if a else kw.get("url")
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if pacer._is_embed_path(url):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, io.BytesIO(b"busy"))
    return _JsonResp({"models": []})


#: `guards_deterministic` (no notes.linter/model traffic at all, pure regex) stays CLEAN
#: alongside `prompt_recall` (which writes a note via `write_typed_note` and calls
#: `api.recall` - both embed) - one invalid arm beside one clean arm is exactly the K25
#: shape: a claim on the CLEAN arm, and one at the corpus root, must ALSO be refused.
MINI_CORPUS = {
    "name": "mini", "project": "svc", "sha256": "x", "path": "x",
    "mistakes": [{"stem": "m-bare", "family": "generic", "project": "svc",
                 "title": "bare except swallowed an error", "desc": "a bare except: hid the failure",
                 "prevention": "catch the specific exception, never a bare except:",
                 "linter_or_test": {"caught": True, "by": "ruff E722"}}],
    "calls": [
        {"id": "b-p1", "label": "m-bare", "family": "generic", "tool": "Edit", "path": "a.py",
         "text": "try:\n    x()\nexcept:\n    pass", "hard": False},
        {"id": "b-n1", "label": None, "near": "m-bare", "family": "generic", "tool": "Edit",
         "path": "a.py", "text": "except ValueError:\n    pass", "hard": True},
    ],
    "counts": {"mistakes": 1, "positives": 1, "negatives": 1, "hard_negatives": 1},
}


@contextlib.contextmanager
def _isolated_pacer():
    # (в)/MX3: check(), not a bare assert - a real regression here must redden by name, not
    # crash the whole suite with an uncaught Traceback.
    check("pacer starts uninstalled entering this block", not pacer.installed())
    if pacer.installed():
        pacer.uninstall()
    saved_urlopen = urllib.request.urlopen
    pacer._reset_for_tests()
    try:
        yield
    finally:
        if pacer.installed():
            pacer.uninstall()
        urllib.request.urlopen = saved_urlopen
        pacer._reset_for_tests()


def _run_mini(out_path: Path, corpus_path: Path, timing: bool = True) -> dict:
    """Runs `gb.main()` for real (`--corpus ... --arms guards_deterministic,prompt_recall
    --save --out ...`), with `--timing` (observe mode) unless `timing=False` (K39)."""
    saved_argv = sys.argv
    try:
        sys.argv = ["guard_bench.py", "--corpus", str(corpus_path),
                   "--arms", "guards_deterministic,prompt_recall", "--save", "--out", str(out_path)]
        if timing:
            sys.argv.append("--timing")
        rc = gb.main()
        return {"rc": rc, "artifact": (json.loads(out_path.read_text(encoding="utf-8"))
                                       if out_path.exists() else {})}
    finally:
        sys.argv = saved_argv


print("\n- item 9A: the real run wires pacer.install(mode=\"observe\")/attach() around every "
      "arm's own Ollama traffic; a failed embed marks the failing arm invalid AND propagates "
      "to the root (K25), and tools/remeasure.row_refusal refuses REAL claim pointers on the "
      "failing arm, a DIFFERENT clean arm, and the corpus root -")
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td:
        corpus_path = Path(td) / "corpus.json"
        corpus_path.write_text(json.dumps(MINI_CORPUS), encoding="utf-8")
        out_path = Path(td) / "out.json"
        result = _run_mini(out_path, corpus_path)
    check("main() exits 0 on the mini corpus", result["rc"] == 0, str(result["rc"]))
    art = result["artifact"]
    pr = art.get("arms", {}).get("prompt_recall", {})
    gd = art.get("arms", {}).get("guards_deterministic", {})
    check("ollama_transport is written on prompt_recall (install() wrapped the embedder call)",
          "ollama_transport" in pr, str(sorted(pr)))
    check("observe mode: pace_sleep_s stays exactly 0 and mode reads 'observe'",
          pr.get("ollama_transport", {}).get("pace_sleep_s") == 0
          and pr.get("ollama_transport", {}).get("mode") == "observe",
          str(pr.get("ollama_transport")))
    check("prompt_recall is marked invalid - every /api/embed call failed",
          pr.get("valid") is False and "embed" in (pr.get("invalid_reason") or ""),
          str(pr.get("invalid_reason")))
    check("guards_deterministic itself stays clean (no Ollama traffic at all)",
          gd.get("valid") is not False, str(gd.get("valid")))
    check("K25: the ROOT of the artifact is ALSO marked invalid, naming prompt_recall",
          art.get("valid") is False and "prompt_recall" in (art.get("invalid_reason") or ""),
          str(art.get("invalid_reason")))
    reason_arm = remeasure_mod.row_refusal(art, 'arms.prompt_recall.tokens_per_call', 0)
    check("tools/remeasure.row_refusal refuses a REAL claim pointer on the failing arm "
          "(guards.prompt_recall.tokens_per_call)", reason_arm is not None and "invalid" in reason_arm,
          str(reason_arm))
    reason_other = remeasure_mod.row_refusal(art, 'arms.guards_deterministic.tokens_per_call', 0)
    check("K25: row_refusal ALSO refuses a REAL claim pointer on the DIFFERENT, otherwise-"
          "clean arm (guards.guards_deterministic.tokens_per_call), because the root is invalid",
          reason_other is not None and "invalid" in reason_other, str(reason_other))
    reason_root = remeasure_mod.row_refusal(art, "corpus.counts.mistakes", 0)
    check("K25: row_refusal ALSO refuses a REAL claim pointer OUTSIDE any arm entirely "
          "(guards.dataset.mistakes -> corpus.counts.mistakes)",
          reason_root is not None and "invalid" in reason_root, str(reason_root))

print("\n- item 9A mutations: install()/attach()/root-propagation removed from the real run "
      "(in-process, gb.pacer/gb._propagate_root_invalidity ARE the real objects - reassigning "
      "them simulates the call sites being deleted without editing the file) -")
saved_install, saved_attach = gb.pacer.install, gb.pacer.attach
gb.pacer.install = lambda mode="pace": None            # mutation: install() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td2:
        corpus_path2 = Path(td2) / "corpus.json"
        corpus_path2.write_text(json.dumps(MINI_CORPUS), encoding="utf-8")
        out_path2 = Path(td2) / "out.json"
        result_no_install = _run_mini(out_path2, corpus_path2)
    pr2 = result_no_install["artifact"].get("arms", {}).get("prompt_recall", {})
    check("mutation 'install() removed': no ollama_transport is written at all (nothing "
          "ever got paced - would FAIL the 'ollama_transport is written' check above)",
          "ollama_transport" not in pr2, str(sorted(pr2)))
gb.pacer.install = saved_install

gb.pacer.attach = lambda *a, **k: None                  # mutation: attach() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td3:
        corpus_path3 = Path(td3) / "corpus.json"
        corpus_path3.write_text(json.dumps(MINI_CORPUS), encoding="utf-8")
        out_path3 = Path(td3) / "out.json"
        result_no_attach = _run_mini(out_path3, corpus_path3)
    pr3 = result_no_attach["artifact"].get("arms", {}).get("prompt_recall", {})
    check("mutation 'attach() removed': no ollama_transport is written and the arm stays "
          "WRONGLY valid (the pacer paced the call but the artifact never learns it - would "
          "FAIL the same checks above)",
          "ollama_transport" not in pr3 and "valid" not in pr3, str(sorted(pr3)))
gb.pacer.attach = saved_attach

saved_propagate = gb._propagate_root_invalidity
gb._propagate_root_invalidity = lambda out: None         # mutation: root propagation removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td4:
        corpus_path4 = Path(td4) / "corpus.json"
        corpus_path4.write_text(json.dumps(MINI_CORPUS), encoding="utf-8")
        out_path4 = Path(td4) / "out.json"
        result_no_prop = _run_mini(out_path4, corpus_path4)
    art4 = result_no_prop["artifact"]
    reason4 = remeasure_mod.row_refusal(art4, 'arms.guards_deterministic.tokens_per_call', 0)
    check("mutation 'root propagation removed': the root stays WRONGLY valid, and "
          "row_refusal no longer refuses the clean-looking guards_deterministic arm either "
          "(would FAIL the K25 checks above)",
          "valid" not in art4 and reason4 is None, str((art4.get("valid"), reason4)))
gb._propagate_root_invalidity = saved_propagate

print("\n- K39: the accuracy run (no --timing) is PACED and retried; only --timing (the owner's idle "
      "window, PLAN step 15) runs the pacer in observe mode -")
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td_k39:
        corpus_k39 = Path(td_k39) / "corpus.json"
        corpus_k39.write_text(json.dumps(MINI_CORPUS), encoding="utf-8")
        res_k39 = _run_mini(Path(td_k39) / "out.json", corpus_k39, timing=False)
    tr_k39 = res_k39["artifact"].get("arms", {}).get("prompt_recall", {}).get("ollama_transport") or {}
    check("K39: without --timing the transport record says mode 'pace'", tr_k39.get("mode") == "pace",
          str(tr_k39.get("mode")))

print("\n- G3 (the auditor's surviving mutation): guards_llm BLOCKS because every /api/generate failed "
      "(the patterns came back empty) - the blocked arm still carries failed_outcomes_llm and the "
      "ROOT is invalid, so no guards.* claim restores -")


def _fake_urlopen_failing_generate(*a, **kw):
    req = a[0] if a else kw.get("url")
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if "/api/generate" in url or "/api/chat" in url:
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {},
                                     io.BytesIO(b'{"error": "model runner has unexpectedly stopped"}'))
    return _JsonResp({"models": []})


saved_llm_cache = gb.LLM_CACHE
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_generate
    with tempfile.TemporaryDirectory() as td_g3:
        gb.LLM_CACHE = Path(td_g3) / "llm_cache.json"          # never the real research/data cache
        corpus_g3 = Path(td_g3) / "corpus.json"
        corpus_g3.write_text(json.dumps(MINI_CORPUS), encoding="utf-8")
        saved_argv = sys.argv
        try:
            sys.argv = ["guard_bench.py", "--corpus", str(corpus_g3), "--arms", "universal_pack",
                        "--llm", "--timing", "--save", "--out", str(Path(td_g3) / "out.json")]
            rc_g3 = gb.main()
            art_g3 = json.loads((Path(td_g3) / "out.json").read_text(encoding="utf-8"))
        finally:
            sys.argv = saved_argv
            gb.LLM_CACHE = saved_llm_cache
    llm_arm = art_g3.get("arms", {}).get("guards_llm", {})
    check("G3: guards_llm is blocked (every generate failed, every pattern came back empty)",
          rc_g3 == 0 and "blocked" in llm_arm, str(llm_arm)[:200])
    check("G3: the BLOCKED arm still carries the LLM failures in its transport record",
          ((llm_arm.get("ollama_transport") or {}).get("failed_outcomes_llm") or {}).get("by_status"),
          str(llm_arm.get("ollama_transport"))[:300])
    check("G3: the root is invalid, so a claim on another arm does not restore",
          art_g3.get("valid") is False
          and remeasure_mod.row_refusal(art_g3, 'arms.universal_pack.tokens_per_call', 0) is not None,
          str(art_g3.get("invalid_reason")))
check("gb.LLM_CACHE is restored to the real path", gb.LLM_CACHE is saved_llm_cache)

check("gb.pacer.install/attach and gb._propagate_root_invalidity are restored to the real "
      "functions after the mutations",
      gb.pacer.install is saved_install and gb.pacer.attach is saved_attach
      and gb._propagate_root_invalidity is saved_propagate)

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
