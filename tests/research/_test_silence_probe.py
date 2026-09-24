"""`research/silence_probe.py` (item 9A, R-v2-ports): the real run wires
`pacer.install()`/`attach()` around its own extraction + embedding traffic.

Two calls happen per silent case: `m.generate_json` (the diagnostic re-run of the extraction
prompt - faked here, no LLM needed to prove the wiring) and `api.capture_session` (the public
path, faked the same way `_test_asof_bench.py`/`_test_facts_dilution_probe.py` do: writes ONE
note directly via `memory_hook.write_typed_note`, which itself calls `embed_text` - the traffic
this suite exists to prove gets paced).

    python tests/research/_test_silence_probe.py
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "nevertwice"))
import silence_probe as sp          # noqa: E402
import _ollama_pacer as pacer       # noqa: E402
import memory_hook as m             # noqa: E402
from nevertwice import api as nt_api  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
import remeasure as remeasure_mod   # noqa: E402 - K16(2): row_refusal on the RESULT artifact

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


def _fake_urlopen_failing_embed(*a, **kw):
    """Every `/api/embed` call fails with a genuine (non-port-exhaustion) 500 - the F1
    failure P0(a) exists to catch. Any other endpoint succeeds, so the run itself does not
    crash on something unrelated to embedding."""
    req = a[0] if a else kw.get("url")
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if pacer._is_embed_path(url):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, io.BytesIO(b"busy"))
    return _JsonResp({"models": []})


def _fake_generate_json(prompt, project=None):
    """Stands in for the real (LLM-driven) diagnostic re-extraction - no network call at
    all, since this suite only needs to prove the SEPARATE `api.capture_session` traffic
    (below) gets paced. Returns no items, a valid, deterministic reading."""
    return {}


def _fake_capture_session(text, project=None, session_id=None, trigger=None):
    """Stands in for the real (LLM-driven) extractor: writes ONE note directly via the
    store's own API. `write_typed_note` itself calls `embed_text` once per note (the same
    wiring `_test_asof_bench.py`/`_test_facts_dilution_probe.py` rely on), which is the
    traffic this suite exists to prove gets paced."""
    m.write_typed_note(m.TYPE_FOLDER["pattern"],
                       {"title": f"t-{session_id}", "description": f"note for {session_id}",
                        "principle": "a fake principle, hermetic test only"},
                       project or "silence_test", "2026-09-24", [], "pattern")


MINI_CASE = {"id": "mini-case", "shape": "value_replaced",
            "sessions": [["Settled it: the timeout is 30 seconds."],
                        ["Came back to it. The timeout is 5 seconds."]],
            "current": ["5 second"], "superseded": ["30 second"]}
MINI_DATASET = {"name": "mini", "cases": [MINI_CASE]}
#: silence_probe.silent_cases() reads this ASOF artifact - one row whose session-one is
#: "written == 0" is what makes `main()` pick `mini-case` up at all.
MINI_ASOF = {"arms": {"nevertwice": {"rows": [
    {"id": "mini-case", "store": {"s0": {"written": 0}}}]}}}


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


def _run_mini(out_path: Path) -> dict:
    """Runs `sp.main()` for real (`--save`) against MINI_DATASET/MINI_ASOF, inside a scratch
    directory under the repo root."""
    scratch_dir = sp.ROOT / ".loop" / "explore" / "_test_silence_probe_scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    data_path = scratch_dir / "mini.json"
    asof_path = scratch_dir / "asof.json"
    data_path.write_text(json.dumps(MINI_DATASET), encoding="utf-8")
    asof_path.write_text(json.dumps(MINI_ASOF), encoding="utf-8")
    saved_argv = sys.argv
    saved_capture = nt_api.capture_session
    saved_generate = m.generate_json
    nt_api.capture_session = _fake_capture_session
    m.generate_json = _fake_generate_json
    try:
        sys.argv = ["silence_probe.py", "--dataset", str(data_path), "--asof", str(asof_path),
                   "--save", "--out", str(out_path)]
        rc = sp.main()
        return {"rc": rc, "artifact": (json.loads(out_path.read_text(encoding="utf-8"))
                                       if out_path.exists() else {})}
    finally:
        sys.argv = saved_argv
        nt_api.capture_session = saved_capture
        m.generate_json = saved_generate
        shutil.rmtree(scratch_dir, ignore_errors=True)


print("\n- item 9A: the real run wires pacer.install()/attach() around this stand's own "
      "Ollama traffic; a failed embed marks the artifact invalid and "
      "tools/remeasure.row_refusal refuses a REAL claim pointer -")
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "out.json"
        result = _run_mini(out_path)
    check("main() exits 0 on the mini dataset", result["rc"] == 0, str(result["rc"]))
    art = result["artifact"]
    check("ollama_transport is written on the artifact (install() wrapped the embedder call)",
          "ollama_transport" in art, str(sorted(art)))
    check("the artifact is marked invalid - every /api/embed call failed",
          art.get("valid") is False and "embed" in (art.get("invalid_reason") or ""),
          str(art.get("invalid_reason")))
    reason = remeasure_mod.row_refusal(art, 'by_kind["no items returned"]', 0)
    check("tools/remeasure.row_refusal refuses a REAL claim pointer "
          "(silence.no_items) on this invalid result",
          reason is not None and "invalid" in reason, str(reason))

print("\n- item 9A mutations: install()/attach() removed from the real run (in-process, "
      "sp.pacer IS the _ollama_pacer module - reassigning its attribute simulates the call "
      "site being deleted without editing the file) -")
saved_install, saved_attach = sp.pacer.install, sp.pacer.attach
sp.pacer.install = lambda: None                        # mutation: install() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td2:
        out_path2 = Path(td2) / "out.json"
        result_no_install = _run_mini(out_path2)
    check("mutation 'install() removed': no ollama_transport is written at all (nothing "
          "ever got paced - would FAIL the 'ollama_transport is written' check above)",
          "ollama_transport" not in result_no_install["artifact"],
          str(sorted(result_no_install["artifact"])))
sp.pacer.install = saved_install

sp.pacer.attach = lambda *a, **k: None                  # mutation: attach() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td3:
        out_path3 = Path(td3) / "out.json"
        result_no_attach = _run_mini(out_path3)
    check("mutation 'attach() removed': no ollama_transport is written and the artifact "
          "stays WRONGLY valid (the pacer paced the call but the artifact never learns it - "
          "would FAIL the same checks above)",
          "ollama_transport" not in result_no_attach["artifact"]
          and "valid" not in result_no_attach["artifact"],
          str(sorted(result_no_attach["artifact"])))
sp.pacer.attach = saved_attach

check("sp.pacer.install/attach are restored to the real functions after the mutations",
      sp.pacer.install is saved_install and sp.pacer.attach is saved_attach)

print(f"\nsilence_probe (item 9A pacer wiring): {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
