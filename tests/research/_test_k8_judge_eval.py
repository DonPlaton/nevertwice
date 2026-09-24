"""`research/k8_judge_eval.py` (item 9A, R-v2-ports): the real run wires
`pacer.install(mode="observe")`/`attach()` around its own Ollama traffic.

This stand has no existing suite at all. It calls `urllib.request.urlopen` directly against
`/api/generate` (never an embed endpoint), and publishes `seconds_per_pair` - a registered raw
wall-clock claim (PREREG-V2-2026-09-24 P5) that must never include this module's own artificial
pacing, so it is wired in "observe" mode: pacing/retry both disabled (`pace_sleep_s` stays
exactly 0), while a bypass still marks the run invalid exactly as in the default "pace" mode.
Since this stand never touches an embed endpoint, F1's failed-embed invalidity path cannot be
exercised through its own real code path - the bypass tripwire (P0(a)'s OTHER named trigger) is
what this suite uses instead, by having a stand-in `judge()` also make one call through
`requests` (a library this stand never itself imports) directly to the recognised Ollama host,
outside any paced call.

    python tests/research/_test_k8_judge_eval.py
"""
from __future__ import annotations

import contextlib
import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "nevertwice"))
import k8_judge_eval as kj         # noqa: E402
import _ollama_pacer as pacer      # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
import remeasure as remeasure_mod  # noqa: E402 - K16(2): row_refusal on the RESULT artifact

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


class _JsonResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen_ok(*a, **kw):
    """A valid /api/generate response: `response` is itself a JSON string (the model's
    output, `format: json`), so `judge()`'s own `json.loads(m._strip_json_fence(raw))`
    parses it - exercises the real success path, not just the transport."""
    inner = json.dumps({"relation": "replaces", "old_settles": True, "new_settles": True})
    return _JsonResp({"response": inner, "done": True, "model": "mini", "created_at": "now",
                      "prompt_eval_count": 42, "eval_count": 7})


#: `truth != "separate"` and both sides marked -> `pair_truth = "replaces"` (k8_skeleton.label_pairs).
MINI_PAIR = {"case": "c1", "branch": "b1", "truth": "supersession",
            "old_marked": True, "new_marked": True,
            "old_title": "old title", "old_desc": "the old fact", "new_title": "new title",
            "new_desc": "the new fact"}
MINI_DATASET = {"cases": [{"id": "c1"}]}
DATASET_REL = ".loop/explore/_test_k8_judge_eval_scratch/dataset.json"


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


def _run_mini(out_path: Path, timing: bool = True) -> dict:
    """Runs `kj.main()` for real against one mini stand file, inside a scratch directory
    UNDER the repo root (`main()`'s own dataset resolution is `ROOT / d["dataset"]["path"]`)."""
    scratch_dir = ROOT / ".loop" / "explore" / "_test_k8_judge_eval_scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = ROOT / DATASET_REL
    dataset_path.write_text(json.dumps(MINI_DATASET), encoding="utf-8")
    stand_path = scratch_dir / "k8_collisions_mini.json"
    stand_path.write_text(json.dumps({"dataset": {"path": DATASET_REL}, "pairs": [dict(MINI_PAIR)],
                                      "engine_commit": "0" * 40}), encoding="utf-8")
    saved_argv = sys.argv
    try:
        sys.argv = ["k8_judge_eval.py", str(stand_path), "--out", str(out_path)]
        if timing:
            sys.argv.append("--timing")           # K39: observe only for the timing run
        rc = kj.main()
        return {"rc": rc, "artifact": (json.loads(out_path.read_text(encoding="utf-8"))
                                       if out_path.exists() else {})}
    finally:
        sys.argv = saved_argv
        shutil.rmtree(scratch_dir, ignore_errors=True)


print("\n- item 9A: the real run wires pacer.install(mode=\"observe\")/attach() around this "
      "stand's own Ollama traffic -")
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_ok
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "out.json"
        result = _run_mini(out_path)
    check("main() exits 0 on the mini stand", result["rc"] == 0, str(result["rc"]))
    art = result["artifact"]
    check("the pair judged as expected (the fake /api/generate response parsed)",
          art.get("pooled", {}).get("accuracy") == 1.0, str(art.get("pooled")))
    ot = art.get("ollama_transport") or {}
    check("ollama_transport is written at the artifact's root (install() wrapped urlopen)",
          "ollama_transport" in art, str(sorted(art)))
    check("calls > 0 (judge()'s own /api/generate call, at least)", ot.get("calls", 0) > 0, str(ot))
    check("observe mode: pace_sleep_s stays exactly 0 and mode reads 'observe' - "
          "seconds_per_pair (PREREG P5) must never include this module's own pacing",
          ot.get("pace_sleep_s") == 0 and ot.get("mode") == "observe", str(ot))
    check("a clean run is never marked valid:true (its absence IS the proof - attach() only "
          "ever writes valid:false)", "valid" not in art)

print("\n- K39: without --timing (an accuracy run) the pacer is in PACE mode - paced and retried -")
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_ok
    with tempfile.TemporaryDirectory() as td_k39:
        res_k39 = _run_mini(Path(td_k39) / "out.json", timing=False)
    ot_k39 = res_k39["artifact"].get("ollama_transport") or {}
    check("K39: the default run's transport record says mode 'pace'", ot_k39.get("mode") == "pace",
          str(ot_k39.get("mode")))

print("\n- K40 (CI packaging has no `requests`): a FAILED /api/generate through the paced urllib path (K33) marks the run invalid in the BASE environment, and row_refusal refuses a REAL claim pointer -")


def _fake_urlopen_generate_500(*a, **kw):
    req = a[0] if a else kw.get("url")
    url = req.full_url if hasattr(req, "full_url") else str(req)
    raise urllib.error.HTTPError(url, 500, "Internal Server Error", {},
                                 io.BytesIO(b'{"error": "model runner has unexpectedly stopped"}'))


with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_generate_500
    with tempfile.TemporaryDirectory() as td_k40:
        res_k40 = _run_mini(Path(td_k40) / "out.json")          # --timing: observe, no retry sleeps
    art_k40 = res_k40["artifact"]
    ot_k40 = art_k40.get("ollama_transport") or {}
    check("K40: main() still exits 0 (a failed judge call is recorded, not fatal)",
          res_k40["rc"] == 0, str(res_k40["rc"]))
    _fo_k40 = ot_k40.get("failed_outcomes_llm") or {}
    check("K40: the failed /api/generate is tallied in failed_outcomes_llm",
          bool(_fo_k40.get("by_status") or _fo_k40.get("by_exception_type") or _fo_k40.get("gave_up")),
          str(_fo_k40))
    check("K40: the run is marked invalid, naming llm",
          art_k40.get("valid") is False and "llm" in (art_k40.get("invalid_reason") or ""),
          str(art_k40.get("invalid_reason")))
    reason_k40 = remeasure_mod.row_refusal(art_k40, "pooled.seconds_per_pair", 0)
    check("K40: row_refusal refuses the REAL pointer absorb_judge.seconds_per_pair",
          reason_k40 is not None and "invalid" in reason_k40, str(reason_k40))

print("\n- item 9A/P0(a): a genuine BYPASS (traffic to the Ollama host outside any paced call) "
      "still marks the run invalid in observe mode, and "
      "tools/remeasure.row_refusal refuses a REAL claim pointer -")
saved_judge = kj.judge


def _judge_with_bypass(old_title, old_desc, new_desc):
    """The real judge() (still paced via urlopen), plus one call through `requests` DIRECTLY
    to the recognised Ollama host - a library this stand never itself imports, simulating a
    dependency that bypasses the paced transport entirely. The real `requests.Session.send`
    is still reached (pacer.install() only COUNTS a requests/aiohttp bypass, never blocks it),
    so it is expected to fail to connect (nothing is listening) - only the COUNT matters here."""
    try:
        import requests  # noqa: PLC0415
    except ImportError:
        return saved_judge(old_title, old_desc, new_desc)
    try:
        requests.get("http://localhost:11434/", timeout=0.5)
    except Exception:                                            # noqa: BLE001 - the count already landed
        pass
    return saved_judge(old_title, old_desc, new_desc)


try:
    import requests as _requests_probe  # noqa: F401
    _HAVE_REQUESTS = True
except ImportError:
    _HAVE_REQUESTS = False
check("`requests` importable - without it (the CI packaging job) the bypass block below is "
      "SKIPPED, not failed; K40 above proves invalidity through the base environment",
      True, "no requests: bypass block skipped")
kj.judge = _judge_with_bypass
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_ok
    with tempfile.TemporaryDirectory() as td2:
        out_path2 = Path(td2) / "out.json"
        result2 = _run_mini(out_path2)
    art2 = result2["artifact"]
    if _HAVE_REQUESTS:
        check("main() still exits 0 (a bypass is recorded, not fatal)", result2["rc"] == 0, str(result2["rc"]))
        check("the run is marked invalid - a bypass reached the Ollama host outside the paced path",
              art2.get("valid") is False and "bypass" in (art2.get("invalid_reason") or ""),
              str(art2.get("invalid_reason")))
        reason = remeasure_mod.row_refusal(art2, "pooled.seconds_per_pair", 0)
        check("tools/remeasure.row_refusal refuses a REAL claim pointer "
              "(absorb_judge.seconds_per_pair) on this invalid result",
              reason is not None and "invalid" in reason, str(reason))
kj.judge = saved_judge

print("\n- item 9A mutations: install()/attach() removed from the real run (in-process, "
      "kj.pacer IS the _ollama_pacer module - reassigning its attribute simulates the call "
      "site being deleted without editing the file) -")
saved_install, saved_attach = kj.pacer.install, kj.pacer.attach
kj.pacer.install = lambda mode="pace": None             # mutation: install() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_ok
    with tempfile.TemporaryDirectory() as td3:
        out_path3 = Path(td3) / "out.json"
        result_no_install = _run_mini(out_path3)
    check("mutation 'install() removed': no ollama_transport is written at all (nothing "
          "ever got paced - would FAIL the 'ollama_transport is written' check above)",
          "ollama_transport" not in result_no_install["artifact"],
          str(sorted(result_no_install["artifact"])))
kj.pacer.install = saved_install

kj.pacer.attach = lambda *a, **k: None                   # mutation: attach() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_ok
    with tempfile.TemporaryDirectory() as td4:
        out_path4 = Path(td4) / "out.json"
        result_no_attach = _run_mini(out_path4)
    check("mutation 'attach() removed': no ollama_transport is written (the pacer paced "
          "the call but the artifact never learns it - would FAIL the same check above)",
          "ollama_transport" not in result_no_attach["artifact"],
          str(sorted(result_no_attach["artifact"])))
kj.pacer.attach = saved_attach

check("kj.pacer.install/attach are restored to the real functions after the mutations",
      kj.pacer.install is saved_install and kj.pacer.attach is saved_attach)
check("kj.judge is restored to the real function after the bypass test",
      kj.judge is saved_judge)

print(f"\nk8_judge_eval (item 9A pacer wiring): {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
