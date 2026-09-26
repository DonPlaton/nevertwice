"""`research/asof_bench.py`: the scoring behind the as-of stand, without an extractor.

A case is correct only when the old day returns the old fact without the new one and the new
day returns the new fact; the dateless floor answers both days with one ranking; the pooled
score keeps per-run rates; the Mem0 blocker is recorded, never a zero.
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
import asof_bench as ab  # noqa: E402
import _ollama_pacer as pacer  # noqa: E402
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
from nevertwice import api as nt_api  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
import remeasure as rm  # noqa: E402 - K16(2): row_refusal on the RESULT artifact

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


case = {"id": "val-http-timeout", "shape": "value_replaced", "domain": "the API client",
        "sessions": [["Settled it: the HTTP client timeout is 30 seconds.", "Tests stayed green."],
                     ["Came back to it. The HTTP client timeout is 5 seconds.", "Pairing session."]],
        "query": "what is the HTTP client timeout", "current": ["5 second", "5s"], "superseded": ["30 second", "30s"]}

print("\n- one case, two days -")
r = ab._row(case, ["http client timeout: the timeout is 30 seconds"], ["timeout: the HTTP client timeout is 5 seconds"])
check("old day right, new day right, both right", r["old_day_correct"] and r["new_day_correct"] and r["both_correct"])
r = ab._row(case, ["the timeout is 5 seconds now (was 30 seconds)"], ["the HTTP client timeout is 5 seconds"])
check("the old day is wrong when the new fact leaks into it", not r["old_day_correct"] and r["new_day_correct"] and not r["both_correct"])
r = ab._row(case, [], ["the timeout is 5 seconds"])
check("nothing on the old day is wrong on the old day", not r["old_day_correct"])
r = ab._row(case, ["the timeout is 30 seconds"], ["nothing relevant"])
check("the new day needs the replacement", r["old_day_correct"] and not r["new_day_correct"])

print("\n- the dateless floor -")
items = ab._naive_items(case, 5)
check("the floor ranks the case's own sentences by overlap with the query, timeout sentences first",
      len(items) == 4 and all("timeout" in s for s in items[:2]), str(items[:2]))
res = ab.run_naive([case], 5)
row = res["rows"][0]
check("with both facts in the same ranking the old day cannot be right", not row["old_day_correct"] and row["new_day_correct"])
check("the floor's score is over supersession cases", res["n_cases"] == 1 and res["both_correct_rate"] == 0.0)

print("\n- the score -")
rows = [{"shape": "value_replaced", "both_correct": True, "old_day_correct": True, "new_day_correct": True},
        {"shape": "value_replaced", "both_correct": False, "old_day_correct": True, "new_day_correct": False},
        {"shape": "control", "both_correct": False, "old_day_correct": False, "new_day_correct": False}]
sc = ab.score(rows)
check("controls are not counted", sc["n_cases"] == 2 and sc["both_correct_rate"] == 0.5 and sc["old_day_rate"] == 1.0)
check("a Wilson interval around the both-correct rate", sc["both_correct_ci"][0] < 0.5 < sc["both_correct_ci"][1])
check("errors counted", ab.score(rows + [{"shape": "value_replaced", "both_correct": False, "old_day_correct": False,
                                           "new_day_correct": False, "error": "x"}])["errors"] == 1)

print("\n- the days are fixed and ordered -")
check("first < between < second < after", ab.DAY_FIRST < ab.DAY_BETWEEN < ab.DAY_SECOND < ab.DAY_AFTER)

print("\n- J2: the item text is title + description, and why an old day failed -")
hits = [{"title": "beta dashboard flag gates new ui", "description": "The flag gates the new UI."}]
items = ab._items(hits)
check("the item text is title and description joined", items == ["beta dashboard flag gates new ui The flag gates the new UI."], str(items))
check("a hit with only a title still renders", ab._items([{"title": "t"}]) == ["t"])
ret_case = {"id": "ret-beta-flag", "shape": "retracted_no_replacement", "sessions": [[], []],
            "query": "what does the beta_dashboard flag control",
            "current": ["delet", "remov", "unconditional"], "superseded": ["gates the new ui"]}
r_hit = ab._row(ret_case, ["beta dashboard flag gates new ui The flag gates the new UI for beta users."], ["the flag was deleted"])
check("the marker is found in the description on the old day", r_hit["old_day_correct"] and r_hit["both_correct"])
r_para = ab._row(ret_case, ["a paraphrase that never says the marker phrase"], ["the flag was deleted"])
check("a paraphrase that drops the marker misses the old day", not r_para["old_day_correct"])
ok_row = {"old_day_correct": True}
check("no failure, no kind", ab.old_fail_kind(ok_row, {"s0": {"written": 1}}) is None)
check("nothing written for session one -> never_written",
      ab.old_fail_kind({"old_day_correct": False, "old_items": 0}, {"s0": {"written": 0}}) == "never_written")
check("written but nothing returned -> unranked",
      ab.old_fail_kind({"old_day_correct": False, "old_items": 0}, {"s0": {"written": 2}}) == "unranked")
check("returned, marker missed -> paraphrase",
      ab.old_fail_kind({"old_day_correct": False, "old_items": 1, "leak": False}, {"s0": {"written": 1}}) == "paraphrase")
check("returned with the new fact -> leak",
      ab.old_fail_kind({"old_day_correct": False, "old_items": 1, "leak": True}, {"s0": {"written": 1}}) == "leak")
check("no store state: the zero-item case cannot be told apart and reads as unranked",
      ab.old_fail_kind({"old_day_correct": False, "old_items": 0}, None) == "unranked")
sc = ab.score([{"shape": "narrowed", "both_correct": False, "old_day_correct": False, "new_day_correct": True,
                "old_fail_kind": "paraphrase", "store": {"s0": {"written": 1}}},
               {"shape": "narrowed", "both_correct": False, "old_day_correct": False, "new_day_correct": False,
                "old_fail_kind": "never_written", "store": {"s0": {"written": 0}}},
               {"shape": "narrowed", "both_correct": True, "old_day_correct": True, "new_day_correct": True,
                "old_fail_kind": None, "store": {"s0": {"written": 1}}}])
check("the score folds the kinds and counts session-one silence",
      sc["old_day_failures_by_kind"] == {"never_written": 1, "absorbed": 0, "unranked": 0, "paraphrase": 1, "leak": 0}
      and sc["s0_never_written"] == 1 and sc["s0_absorbed"] == 0, str(sc))

# K1b/K3: a first-session note the twin gate absorbed into session two's is credited to session
# one through `sources`, and an old-day miss on it is its own kind, not silence
sc2 = ab.old_fail_kind({"old_day_correct": False, "old_items": 1, "leak": False},
                       {"s0": {"written": 1, "absorbed": 1}})
check("every first-session note absorbed -> the old-day miss is `absorbed`, not never_written", sc2 == "absorbed", str(sc2))
sc3 = ab.old_fail_kind({"old_day_correct": False, "old_items": 1, "leak": False},
                       {"s0": {"written": 2, "absorbed": 1}})
check("one absorbed note beside one served -> the miss is read from the answer, not the absorb", sc3 == "paraphrase", str(sc3))

# ── R-v2-ports item 9A: the pacer is wired into every arm's own Ollama traffic ─────────

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


def _fake_capture_session(text, project=None, session_id=None, trigger=None, date=None):
    """Stands in for the real (LLM-driven) extractor: writes ONE note directly via the
    store's own API. `write_typed_note` itself calls `embed_text` once per note (the same
    wiring `_test_facts_dilution_probe.py` relies on), which is the traffic this suite
    exists to prove gets paced - `run_nevertwice` calls this twice per case (session 0 and
    session 1), so two embed attempts happen before `api.as_of` even runs."""
    m.write_typed_note(m.TYPE_FOLDER["pattern"],
                       {"title": f"t-{session_id}", "description": f"note for {session_id}",
                        "principle": "a fake principle, hermetic test only"},
                       project or "asof_test", "2026-09-24", [], "pattern")


MINI_CASE = {"id": "mini-case", "shape": "value_replaced",
            "sessions": [["Settled it: the timeout is 30 seconds."],
                        ["Came back to it. The timeout is 5 seconds."]],
            "query": "what is the timeout", "current": ["5 second"], "superseded": ["30 second"]}
MINI_DATASET = {"name": "mini", "cases": [MINI_CASE]}


@contextlib.contextmanager
def _isolated_pacer():
    # (в)/MX3: check(), not a bare assert - a real regression here must redden by name,
    # not crash the whole suite with an uncaught Traceback.
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


def _run_mini(out_path: Path, with_files: list | None = None) -> dict:
    """Runs `ab.main()` for real (`--runs 1 --out ...`) against MINI_DATASET, inside a
    scratch directory under the repo root. `with_files` (K25) is passed through as
    `--with FILE...` unchanged."""
    scratch_dir = ab.ROOT / ".loop" / "explore" / "_test_asof_bench_scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    data_path = scratch_dir / "mini.json"
    data_path.write_text(json.dumps(MINI_DATASET), encoding="utf-8")
    saved_argv = sys.argv
    saved_capture = nt_api.capture_session
    nt_api.capture_session = _fake_capture_session
    try:
        argv = ["asof_bench.py", "--dataset", str(data_path), "--arms", "nevertwice",
               "--runs", "1", "--out", str(out_path)]
        if with_files:
            argv += ["--with", *[str(f) for f in with_files]]
        sys.argv = argv
        rc = ab.main()
        return {"rc": rc, "artifact": (json.loads(out_path.read_text(encoding="utf-8"))
                                       if out_path.exists() else {})}
    finally:
        sys.argv = saved_argv
        nt_api.capture_session = saved_capture
        shutil.rmtree(scratch_dir, ignore_errors=True)


def _mini_dataset_sha256() -> str:
    return hashlib.sha256(json.dumps(MINI_DATASET).encode("utf-8")).hexdigest()


def _fake_urlopen_embeds_ok(*a, **kw):
    """Every `/api/embed` call succeeds - the K25 scenario needs the `nevertwice` arm
    itself CLEAN, so the only invalid arm in the artifact comes from the `--with` file."""
    req = a[0] if a else kw.get("url")
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if pacer._is_embed_path(url):
        return _JsonResp({"embeddings": [[0.1, 0.2, 0.3]]})
    return _JsonResp({"models": []})


print("\n- item 9A: the real run wires pacer.install()/attach() around every arm's own "
      "Ollama traffic; a failed embed marks the arm invalid and "
      "tools/remeasure.row_refusal refuses a REAL claim pointer on it -")
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "out.json"
        result = _run_mini(out_path)
    check("main() exits 0 on the mini dataset", result["rc"] == 0, str(result["rc"]))
    art = result["artifact"]
    arm = art.get("arms", {}).get("nevertwice", {})
    check("ollama_transport is written on the nevertwice arm (install() wrapped the "
          "embedder call)", "ollama_transport" in arm, str(sorted(arm)))
    check("the arm is marked invalid - every /api/embed call failed",
          arm.get("valid") is False and "embed" in (arm.get("invalid_reason") or ""),
          str(arm.get("invalid_reason")))
    reason = rm.row_refusal(art, 'arms["nevertwice"].both_correct_rate', 0)
    check("tools/remeasure.row_refusal refuses a REAL claim pointer "
          "(asof.nevertwice.both_correct) on this invalid result",
          reason is not None and "invalid" in reason, str(reason))

print("\n- item 9A mutations: install()/attach() removed from the real run (in-process, "
      "ab.pacer IS the _ollama_pacer module - reassigning its attribute simulates the "
      "call site being deleted without editing the file) -")
saved_install, saved_attach = ab.pacer.install, ab.pacer.attach
ab.pacer.install = lambda: None                        # mutation: install() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td2:
        out_path2 = Path(td2) / "out.json"
        result_no_install = _run_mini(out_path2)
    arm2 = result_no_install["artifact"].get("arms", {}).get("nevertwice", {})
    check("mutation 'install() removed': no ollama_transport is written at all (nothing "
          "ever got paced - would FAIL the 'ollama_transport is written' check above)",
          "ollama_transport" not in arm2, str(sorted(arm2)))
ab.pacer.install = saved_install

ab.pacer.attach = lambda *a, **k: None                  # mutation: attach() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td3:
        out_path3 = Path(td3) / "out.json"
        result_no_attach = _run_mini(out_path3)
    arm3 = result_no_attach["artifact"].get("arms", {}).get("nevertwice", {})
    #: Before (б) b-a this mutation left the arm WRONGLY valid. The calls>0 rule
    #: (`pacer.require_traffic`, K45) now refuses an Ollama arm whose record shows no paced call -
    #: so a lost attach() is caught too, by that rule's own reason.
    check("mutation 'attach() removed': no ollama_transport is written, and the arm is refused "
          "by the calls>0 rule instead of staying valid",
          "ollama_transport" not in arm3 and arm3.get("valid") is False
          and "0 paced Ollama calls" in (arm3.get("invalid_reason") or ""), str(sorted(arm3)))
ab.pacer.attach = saved_attach

check("ab.pacer.install/attach are restored to the real functions after the mutations",
      ab.pacer.install is saved_install and ab.pacer.attach is saved_attach)


print("\n- item 9A(c): merge_arm() carries a constituent's own invalidity into the pooled "
      "arm (K16(2)) - a version without that fix would WRONGLY drop it -")
_invalid_run = {"rows": [{"id": "x", "shape": "value_replaced", "both_correct": False,
                         "old_day_correct": False, "new_day_correct": False}],
               "n_cases": 1, "both_correct_rate": 0.0, "both_correct_ci": [0.0, 1.0],
               "old_day_rate": 0.0, "new_day_rate": 0.0, "errors": 0, "seconds": 1.0,
               "config": "zep test", "valid": False, "invalid_reason": "embed calls failed"}
_valid_run = {"rows": [{"id": "y", "shape": "value_replaced", "both_correct": True,
                       "old_day_correct": True, "new_day_correct": True}],
             "n_cases": 1, "both_correct_rate": 1.0, "both_correct_ci": [0.0, 1.0],
             "old_day_rate": 1.0, "new_day_rate": 1.0, "errors": 0, "seconds": 1.0,
             "config": "zep test"}
_merged = ab.merge_arm([_invalid_run, _valid_run])
check("merge_arm() propagates a constituent's valid:false into the merged arm",
      _merged.get("valid") is False and _merged.get("invalid_reason") == "embed calls failed",
      str(_merged))


def _merge_arm_without_k16(results):
    """The pre-fix shape of `merge_arm`: the identical merge, minus the K16(2) invalidity
    propagation - written out rather than monkeypatched, since the real fix is a few
    inline lines inside `merge_arm`, not a separable helper this suite could swap out."""
    live = [r for r in results if not r.get("blocked")]
    if not live:
        return results[0]
    if len(live) == 1:
        return live[0]
    rows = [dict(r, run=i) for i, res in enumerate(live) for r in res["rows"]]
    out = {"rows": rows, **ab.score(rows), "runs": len(live),
          "per_run": [res["both_correct_rate"] for res in live],
          "seconds": round(sum(float(res.get("seconds") or 0) for res in live), 1),
          "config": live[0].get("config", "")}
    if any("graphiti" in res for res in live):
        out["graphiti"] = live[0].get("graphiti")
        out["graphiti_per_run"] = [res.get("graphiti") for res in live]
    return out


_mutated_merge = _merge_arm_without_k16([_invalid_run, _valid_run])
check("mutation 'merge_arm dropping valid:false': the pooled arm stays WRONGLY valid "
      "(would FAIL the propagation check above)",
      "valid" not in _mutated_merge, str(_mutated_merge))


print("\n- item 9A/K25: an invalid --with arm invalidates the WHOLE artifact at the root, "
      "so tools/remeasure.row_refusal refuses a claim pointer on a DIFFERENT, otherwise-"
      "clean arm too -")
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_embeds_ok
    with tempfile.TemporaryDirectory() as td:
        zep_path = Path(td) / "zep.json"
        zep_path.write_text(json.dumps({
            "dataset": {"sha256": _mini_dataset_sha256()},
            "arms": {"zep": {"rows": [], "n_cases": 0, "both_correct_rate": 0.0,
                             "both_correct_ci": [0.0, 1.0], "old_day_rate": 0.0,
                             "new_day_rate": 0.0, "errors": 0, "seconds": 0.0,
                             "config": "graphiti test", "valid": False,
                             "invalid_reason": "graphiti embed calls bypassed the pacer"}}}),
            encoding="utf-8")
        out_path = Path(td) / "out.json"
        result = _run_mini(out_path, with_files=[zep_path])
    check("main() exits 0 with a --with file merged in", result["rc"] == 0, str(result["rc"]))
    art = result["artifact"]
    arms = art.get("arms", {})
    check("the nevertwice arm itself is clean (embeds succeeded)",
          arms.get("nevertwice", {}).get("valid") is not False, str(arms.get("nevertwice")))
    check("the merged zep arm carries its own invalidity",
          arms.get("zep", {}).get("valid") is False, str(arms.get("zep")))
    check("K25: the ROOT of the artifact is ALSO marked invalid, naming zep",
          art.get("valid") is False and "zep" in (art.get("invalid_reason") or ""),
          str(art.get("invalid_reason")))
    reason = rm.row_refusal(art, 'arms["nevertwice"].both_correct_rate', 0)
    check("K25: tools/remeasure.row_refusal refuses a REAL claim pointer on the CLEAN "
          "nevertwice arm too (asof.nevertwice.both_correct), because the root is invalid",
          reason is not None and "invalid" in reason, str(reason))

print("\n- item 9A/K25 mutation: root propagation removed -")
saved_propagate = ab._propagate_root_invalidity
ab._propagate_root_invalidity = lambda out, extra_reasons=None: None
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_embeds_ok
    with tempfile.TemporaryDirectory() as td2:
        zep_path2 = Path(td2) / "zep.json"
        zep_path2.write_text(json.dumps({
            "dataset": {"sha256": _mini_dataset_sha256()},
            "arms": {"zep": {"rows": [], "n_cases": 0, "both_correct_rate": 0.0,
                             "both_correct_ci": [0.0, 1.0], "old_day_rate": 0.0,
                             "new_day_rate": 0.0, "errors": 0, "seconds": 0.0,
                             "config": "graphiti test", "valid": False,
                             "invalid_reason": "graphiti embed calls bypassed the pacer"}}}),
            encoding="utf-8")
        out_path2 = Path(td2) / "out.json"
        result_mut = _run_mini(out_path2, with_files=[zep_path2])
    art_mut = result_mut["artifact"]
    reason_mut = rm.row_refusal(art_mut, 'arms["nevertwice"].both_correct_rate', 0)
    check("mutation 'root propagation removed': the root stays WRONGLY valid, and "
          "row_refusal no longer refuses the clean-looking nevertwice arm either (would "
          "FAIL the K25 checks above)",
          "valid" not in art_mut and reason_mut is None,
          str((art_mut.get("valid"), reason_mut)))
ab._propagate_root_invalidity = saved_propagate
check("ab._propagate_root_invalidity is restored to the real function",
      ab._propagate_root_invalidity is saved_propagate)

print("\n- K29 (the auditor, item 9C): a BLOCKED constituent is no longer dropped silently -")


def _blocked(reason="FalkorDB down"):
    return {"blocked": reason}


def _live(rate=1.0):
    return {"rows": [{"id": "z", "shape": "value_replaced", "both_correct": True,
                     "old_day_correct": True, "new_day_correct": True}],
           "n_cases": 1, "both_correct_rate": rate, "both_correct_ci": [0.0, 1.0],
           "old_day_rate": rate, "new_day_rate": rate, "errors": 0, "seconds": 1.0,
           "config": "zep test"}


print("\n- merge_arm(): every constituent blocked -> the arm stays PRESENT as blocked, not "
      "silently absent (P3: 'blocked (reason)', never absent) -")
merged_all_blocked = ab.merge_arm([_blocked("FalkorDB down")], ["a.json"])
check("merge_arm() returns a blocked-shaped dict, not results[0] bare",
      merged_all_blocked == {"blocked": "FalkorDB down (a.json)"}, str(merged_all_blocked))

print("\n- merge_arm(): one live + one blocked -> the live one is still pooled, but the "
      "merged arm is marked invalid, naming the file and the block reason -")
merged_mixed = ab.merge_arm([_live(1.0), _blocked("timeout")], ["live.json", "bad.json"])
check("merge_arm() mixed: the arm is marked invalid", merged_mixed.get("valid") is False,
      str(merged_mixed))
check("merge_arm() mixed: the reason names the file and the block reason",
      "bad.json" in (merged_mixed.get("invalid_reason") or "")
      and "timeout" in (merged_mixed.get("invalid_reason") or ""),
      str(merged_mixed.get("invalid_reason")))
check("merge_arm() mixed: the live constituent's own rate is still published (not thrown "
      "away just because a sibling was blocked)",
      merged_mixed.get("both_correct_rate") == 1.0 and merged_mixed.get("runs") == 1,
      str(merged_mixed))

print("\n- merge_arm(): a single LIVE result always writes 'runs' (used to return live[0] "
      "bare, no 'runs' key) -")
single_live = _live(0.75)
check("setup: the raw fixture itself carries no 'runs' key", "runs" not in single_live)
merged_single = ab.merge_arm([single_live])
check("merge_arm() on a single live result sets runs=1",
      merged_single.get("runs") == 1 and merged_single.get("both_correct_rate") == 0.75,
      str(merged_single))

print("\n- K29 mutation 'the merged arm not ALWAYS writing runs': reverted to bare live[0] -")


def _merge_arm_no_runs_key(results, files=None):
    live = [r for r in results if not r.get("blocked")]
    if not live:
        return results[0]
    if len(live) == 1:
        return live[0]                     # mutation: no runs key set
    return ab.merge_arm(results, files)


mutated_single = _merge_arm_no_runs_key([_live(0.75)])
check("mutation 'runs not always written': 'runs' is now WRONGLY absent (would FAIL the "
      "single-live 'runs=1' check above)", "runs" not in mutated_single, str(mutated_single))

print("\n- K29 mutation 'every constituent blocked -> dropped instead of kept as blocked' -")


def _merge_arm_drop_all_blocked(results, files=None):
    live = [r for r in results if not r.get("blocked")]
    if not live:
        return None                        # mutation: nothing to merge into - caller drops it
    return ab.merge_arm(results, files)


mutated_all_blocked = _merge_arm_drop_all_blocked([_blocked("FalkorDB down")], ["a.json"])
check("mutation 'all-blocked dropped': merge_arm now WRONGLY returns None instead of a "
      "present {'blocked': ...} arm (would FAIL the 'stays PRESENT as blocked' check above)",
      mutated_all_blocked is None, str(mutated_all_blocked))

print("\n- K29 mutation 'a mixed blocked constituent not checked': the blocked sibling is "
      "silently ignored, the pool stays wrongly valid -")


def _merge_arm_ignore_blocked_in_mix(results, files=None):
    live = [r for r in results if not r.get("blocked")]
    if not live:
        return results[0]
    if len(results) == 1:
        out = dict(results[0]); out.setdefault("runs", 1)
        return out
    rows = [dict(r, run=i) for i, res in enumerate(live) for r in res["rows"]]
    out = {"rows": rows, **ab.score(rows), "runs": len(live),
          "per_run": [res["both_correct_rate"] for res in live],
          "seconds": round(sum(float(res.get("seconds") or 0) for res in live), 1),
          "config": live[0].get("config", "")}
    invalid = next((res for res in live if res.get("valid") is False), None)
    if invalid is not None:
        out["valid"] = False
        out["invalid_reason"] = invalid.get("invalid_reason")
    # mutation: the `blocked` list is computed but never folded into `out["valid"]`
    return out


mutated_mixed = _merge_arm_ignore_blocked_in_mix([_live(1.0), _blocked("timeout")],
                                                 ["live.json", "bad.json"])
check("mutation 'mixed blocked constituent not checked': the merged arm stays WRONGLY "
      "valid (would FAIL the merge_arm() mixed 'marked invalid' check above)",
      "valid" not in mutated_mixed, str(mutated_mixed))

print("\n- end-to-end: main()'s --with loop no longer drops a blocked constituent silently -"
      " a --with file whose ONLY mention of an arm is blocked still reaches the artifact, "
      "and a --with file's own ROOT valid:false (clean arm) invalidates the whole run too -")
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_embeds_ok
    with tempfile.TemporaryDirectory() as td_k29:
        # scenario: two --with files for 'zep' - one BLOCKED, one clean. Before K29 this arm
        # would come back with runs=1 (the clean one only) and no trace of the blocked file.
        blocked_zep = Path(td_k29, "blocked.json")
        blocked_zep.write_text(json.dumps({"dataset": {"sha256": _mini_dataset_sha256()},
                                           "arms": {"zep": {"blocked": "FalkorDB down"}}}),
                               encoding="utf-8")
        clean_zep = Path(td_k29, "clean.json")
        clean_zep.write_text(json.dumps({"dataset": {"sha256": _mini_dataset_sha256()},
                                         "arms": {"zep": _live(1.0)}}), encoding="utf-8")
        out_path_k29 = Path(td_k29) / "out.json"
        result_k29 = _run_mini(out_path_k29, with_files=[blocked_zep, clean_zep])
    art_k29 = result_k29["artifact"]
    zep_k29 = art_k29.get("arms", {}).get("zep", {})
    check("K29: the zep arm is present (not silently dropped) with the blocked constituent named",
          "blocked.json" in (zep_k29.get("invalid_reason") or ""), str(zep_k29))
    check("K29: the zep arm is marked invalid, not silently clean",
          zep_k29.get("valid") is False, str(zep_k29))
    check("K29: root propagation reaches this too - a claim on the clean nevertwice arm is "
          "refused via the root",
          art_k29.get("valid") is False, str(art_k29.get("valid")))
    reason_k29 = rm.row_refusal(art_k29, 'arms["nevertwice"].both_correct_rate', 0)
    check("K29: tools/remeasure.row_refusal refuses the real claim pointer "
          "(asof.nevertwice.both_correct) via the root",
          reason_k29 is not None, str(reason_k29))

    # scenario: EVERY constituent for 'zep' is blocked across all --with files
    with tempfile.TemporaryDirectory() as td_k29b:
        blocked_only = Path(td_k29b, "blocked_only.json")
        blocked_only.write_text(json.dumps({"dataset": {"sha256": _mini_dataset_sha256()},
                                            "arms": {"zep": {"blocked": "FalkorDB down"}}}),
                                encoding="utf-8")
        out_path_k29b = Path(td_k29b) / "out.json"
        result_k29b = _run_mini(out_path_k29b, with_files=[blocked_only])
    art_k29b = result_k29b["artifact"]
    check("K29 (all blocked): the zep arm is PRESENT, not absent from arms",
          "zep" in art_k29b.get("arms", {}), str(sorted(art_k29b.get("arms", {}))))
    check("K29 (all blocked): the zep arm is exactly {'blocked': reason}",
          art_k29b["arms"]["zep"].get("blocked") == "FalkorDB down (blocked_only.json)",
          str(art_k29b["arms"].get("zep")))
    check("K29 (all blocked): the root stays VALID - a wholly-blocked arm is a declared "
          "absence (P3), not an invalid measurement",
          "valid" not in art_k29b, str(art_k29b.get("valid")))

print("\n- K29: a --with file's own ROOT valid:false, with a CLEAN arm, still invalidates -")
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_embeds_ok
    with tempfile.TemporaryDirectory() as td_k29c:
        root_bad_zep = Path(td_k29c, "root_bad.json")
        root_bad_zep.write_text(json.dumps({
            "dataset": {"sha256": _mini_dataset_sha256()}, "valid": False,
            "invalid_reason": "synthetic: this FILE's own root is invalid, not the zep arm",
            "arms": {"zep": _live(1.0)}}), encoding="utf-8")
        out_path_k29c = Path(td_k29c) / "out.json"
        result_k29c = _run_mini(out_path_k29c, with_files=[root_bad_zep])
    art_k29c = result_k29c["artifact"]
    check("K29: the zep ARM itself stays clean (only the FILE's root was invalid)",
          art_k29c.get("arms", {}).get("zep", {}).get("valid") is not False,
          str(art_k29c.get("arms", {}).get("zep")))
    check("K29: the ROOT is invalid anyway, naming the file",
          art_k29c.get("valid") is False and "root_bad.json" in (art_k29c.get("invalid_reason") or ""),
          str(art_k29c.get("invalid_reason")))
    reason_k29c = rm.row_refusal(art_k29c, 'arms["nevertwice"].both_correct_rate', 0)
    check("K29: row_refusal refuses the clean nevertwice arm too, via the root",
          reason_k29c is not None, str(reason_k29c))

    print("\n- K29 mutation 'a --with file's own root not checked' -")
    saved_propagate_k29 = ab._propagate_root_invalidity
    ab._propagate_root_invalidity = lambda out, extra_reasons=None: (
        saved_propagate_k29(out, None))     # mutation: extra_reasons (file roots) dropped
    with tempfile.TemporaryDirectory() as td_k29d:
        root_bad_zep2 = Path(td_k29d, "root_bad2.json")
        root_bad_zep2.write_text(json.dumps({
            "dataset": {"sha256": _mini_dataset_sha256()}, "valid": False,
            "invalid_reason": "synthetic: file root invalid",
            "arms": {"zep": _live(1.0)}}), encoding="utf-8")
        out_path_k29d = Path(td_k29d) / "out.json"
        result_k29d = _run_mini(out_path_k29d, with_files=[root_bad_zep2])
    art_k29d = result_k29d["artifact"]
    check("mutation 'file root not checked': the root now WRONGLY stays valid (would FAIL "
          "the 'ROOT is invalid anyway' check above)",
          "valid" not in art_k29d, str(art_k29d.get("valid")))
    ab._propagate_root_invalidity = saved_propagate_k29
    check("ab._propagate_root_invalidity restored after the K29 mutation",
          ab._propagate_root_invalidity is saved_propagate_k29)

print("\nK41: --runs N is N FRESH PROCESSES (supersession's 2026-09-22 fix) - measured at 64011bb, "
      "the in-process loop gave session-one-never-written 3 vs 8 and 10 of 60 cases discordant -")


def _k41_arm(rates, valid=None):
    rows = [{"id": f"c{j}", "shape": "value_replaced", "old_day_correct": ok, "new_day_correct": True,
             "both_correct": ok, "store": {"s0": {"written": 1}}} for j, ok in enumerate(rates)]
    a = {"rows": rows, **ab.score(rows), "seconds": 1.0, "config": "cfg",
         "ollama_transport": {"calls": 3}}
    if valid is False:
        a["valid"] = False
        a["invalid_reason"] = "ollama_transport.failed_outcomes_llm=1 (the extractor call failed)"
    return a


def _k41_blob(rates, commit="c" * 40, store="S1", with_naive=True, valid=None, sleep=True):
    arms = {"nevertwice": _k41_arm(rates, valid)}
    if sleep:
        arms["nevertwice_after_sleep"] = dict(_k41_arm(rates), adjudication={"pairs": 2})
    if with_naive:
        arms["naive"] = _k41_arm([True, False])
    arms["mem0"] = {"blocked": "by design"}
    return {"dataset": {"sha256": "x"}, "arms": arms, "store": store,
            "measured_at": {"commit": commit, "utc": "2026-09-24T19:00:00Z", "dirty": False}}


runs_ok = [("o.run1.json", _k41_blob([True, True, False, True])),
           ("o.run2.json", _k41_blob([True, False, False, True], store="S2", with_naive=False))]
pooled = ab.pool_engine_runs(runs_ok)
eng = pooled["nevertwice"]
check("K41: the engine arm is pooled over the run files (rows tagged 0/1, runs=2, per-run kept)",
      eng["runs"] == 2 and len(eng["rows"]) == 8 and {r["run"] for r in eng["rows"]} == {0, 1}
      and eng["per_run"] == [0.75, 0.5] and eng["run_files"] == ["o.run1.json", "o.run2.json"], str(eng.get("per_run")))
check("K41: the pooled rate is scored over all case-runs (5 of 8)", eng["both_correct_rate"] == 0.625,
      str(eng["both_correct_rate"]))
check("K41: the after-sleep reading is pooled the same way, with each run's adjudication",
      pooled["nevertwice_after_sleep"]["runs"] == 2
      and pooled["nevertwice_after_sleep"]["adjudication_per_run"] == [{"pairs": 2}, {"pairs": 2}])
check("K41: the other arms come from run 1 (they are measured once), mem0 is not carried",
      "naive" in pooled and "mem0" not in pooled)

bad = [runs_ok[0], ("o.run2.json", _k41_blob([True, True, True, True], store="S2", with_naive=False, valid=False))]
pooled_bad = ab.pool_engine_runs(bad)
out_bad = {"arms": dict(pooled_bad)}
ab._propagate_root_invalidity(out_bad, [])
check("K41: an invalid run 2 makes the pooled engine arm invalid, naming its file (P2)",
      pooled_bad["nevertwice"].get("valid") is False and "o.run2.json" in pooled_bad["nevertwice"]["invalid_reason"])
check("K41: ...and the root, so row_refusal refuses both the engine and the naive pointer",
      rm.row_refusal(out_bad, 'arms["nevertwice"].both_correct_rate', 0) is not None
      and rm.row_refusal(out_bad, 'arms["naive"].both_correct_rate', 0) is not None)
root_bad = [runs_ok[0], ("o.run2.json", dict(_k41_blob([True] * 4, store="S2", with_naive=False),
                                              valid=False, invalid_reason="file root"))]
check("K41: a run file's own ROOT invalid also invalidates the pooled arm",
      ab.pool_engine_runs(root_bad)["nevertwice"].get("valid") is False)
try:
    ab.pool_engine_runs([runs_ok[0], ("o.run2.json", _k41_blob([True] * 4, commit="d" * 40, store="S2"))])
    check("K41: runs from different commits are refused", False)
except ValueError as e:
    check("K41: runs from different commits are refused", "different commits" in str(e), str(e))
try:
    ab.pool_engine_runs([runs_ok[0], ("o.run2.json", _k41_blob([True] * 4))])
    check("K41: two runs sharing one store are refused", False)
except ValueError as e:
    check("K41: two runs sharing one store are refused", "share one store" in str(e), str(e))

print("\nK41: main() --runs 2 spawns one subprocess per run (run 1 all arms, later runs the engine "
      "only), never calls run_nevertwice in the parent, and a failed run pools nothing -")
saved_run, saved_nt = ab.subprocess.run, ab.run_nevertwice
parent_calls = {"n": 0}
spawned: list = []


def _fake_nt(*a, **k):
    parent_calls["n"] += 1
    raise AssertionError("run_nevertwice called in the parent process")


class _Rc:
    def __init__(self, rc):
        self.returncode = rc


def _fake_run_factory(fail_at=None):
    def _fake_run(cmd, *a, **kw):
        if not any(str(x).endswith("asof_bench.py") for x in cmd):
            return saved_run(cmd, *a, **kw)        # git et al. (provenance) go to the real one
        spawned.append(list(cmd))
        n = len(spawned)
        out = Path(cmd[cmd.index("--out") + 1])
        if n == fail_at:
            return _Rc(1)
        arms_arg = cmd[cmd.index("--arms") + 1].split(",")
        out.write_text(json.dumps(_k41_blob([True, n == 1, True, True], store=f"S{n}",
                                            with_naive="naive" in arms_arg)), encoding="utf-8")
        return _Rc(0)
    return _fake_run


ab.run_nevertwice = _fake_nt
try:
    with tempfile.TemporaryDirectory() as td_k41:
        ds = Path(td_k41) / "ds.json"
        ds.write_text(json.dumps({"name": "mini", "cases": [{"id": "c0", "shape": "value_replaced",
                                  "sessions": [["a"], ["b"]], "query": "q", "current": ["b"],
                                  "superseded": ["a"]}]}), encoding="utf-8")
        ab.subprocess.run = _fake_run_factory()
        saved_argv = sys.argv
        try:
            sys.argv = ["asof_bench.py", "--dataset", str(ds), "--arms", "nevertwice,naive", "--runs", "2",
                        "--sleep", "--out", str(Path(td_k41) / "pooled.json")]
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc_k41 = ab.main()
            except AssertionError as e:          # the parent ran the engine in-process: a named red below
                rc_k41 = f"crash: {e}"
        finally:
            sys.argv = saved_argv
        art_k41 = json.loads((Path(td_k41) / "pooled.json").read_text(encoding="utf-8")) \
            if (Path(td_k41) / "pooled.json").exists() else {}
        check("K41: main() exits 0 and writes the pooled artifact", rc_k41 == 0 and bool(art_k41), str(rc_k41))
        check("K41: two subprocesses, each with --runs 1; run 1 all arms, run 2 the engine arm only",
              len(spawned) == 2 and all(c[c.index("--runs") + 1] == "1" for c in spawned)
              and spawned[0][spawned[0].index("--arms") + 1] == "nevertwice,naive"
              and spawned[1][spawned[1].index("--arms") + 1] == "nevertwice"
              and all("--sleep" in c for c in spawned), str(spawned)[:300])
        check("K41: run_nevertwice was never called in the parent process", parent_calls["n"] == 0)
        check("K41: the pooled artifact carries runs=2 and both arms",
              (art_k41.get("arms", {}).get("nevertwice") or {}).get("runs") == 2 and "naive" in art_k41.get("arms", {}))
        spawned.clear()
        ab.subprocess.run = _fake_run_factory(fail_at=2)
        try:
            sys.argv = ["asof_bench.py", "--dataset", str(ds), "--arms", "nevertwice,naive", "--runs", "2",
                        "--out", str(Path(td_k41) / "pooled2.json")]
            with contextlib.redirect_stdout(io.StringIO()):
                rc_fail = ab.main()
        finally:
            sys.argv = saved_argv
        check("K41: a failed run pools nothing (exit 2, no pooled file)",
              rc_fail == 2 and not (Path(td_k41) / "pooled2.json").exists(), str(rc_fail))
finally:
    ab.subprocess.run, ab.run_nevertwice = saved_run, saved_nt
check("ab.subprocess.run and ab.run_nevertwice are restored", ab.subprocess.run is saved_run and ab.run_nevertwice is saved_nt)

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
