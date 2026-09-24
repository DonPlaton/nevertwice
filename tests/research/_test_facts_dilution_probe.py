"""`research/facts_dilution_probe.py` (item 4d, R-v2-ports): the real run wires
`pacer.install()`/`attach()` around its own extraction + embedding traffic.

This stand has no `--dry` mode at all (unlike cross_project_bench.py/principle_twins.py) -
every run does real ingest (`api.capture_session`, which needs a real LLM) and real
retrieval (`api.recall`/`strip_facts_variant`, which need a real embedder). Hermetic here
means: `api.capture_session` is replaced with a stand-in that writes a note DIRECTLY via
`memory_hook.write_typed_note` (no LLM at all - confirmed empirically that
`write_typed_note` itself calls `embed_text` once per note, which is all this suite needs
to prove the wiring, not a real ingest/recall pipeline), and `urllib.request.urlopen` is
faked so that one embed call never reaches a real Ollama.

    python tests/research/_test_facts_dilution_probe.py
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
import facts_dilution_probe as fdp  # noqa: E402
import _ollama_pacer as pacer       # noqa: E402
import memory_hook as m             # noqa: E402
from nevertwice import api as nt_api  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


class _JsonResp:
    def __init__(self, payload: dict) -> None:
        self._p = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(*a, **kw):
    return _JsonResp({"embedding": [0.1, 0.2, 0.3]})


def _fake_capture_session(text, project=None, session_id=None, trigger=None):
    """Stands in for the real (LLM-driven) extractor: writes ONE note directly via the
    store's own API. `write_typed_note` itself calls `embed_text` once per note (verified
    empirically - this stand has no cheaper hook than that to exercise the embedder at
    all), which is the traffic this suite exists to prove gets paced."""
    m.write_typed_note(m.TYPE_FOLDER["pattern"],
                       {"title": "t", "description": f"note for {session_id}",
                        "principle": "a fake principle, hermetic test only"},
                       project or "dil_test", "2026-09-24", [], "pattern")


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


MINI_DATASET = {"name": "mini", "cases": [
    {"id": "c1", "shape": "control", "sessions": [["hi there"]],
     "query": "what did we say", "current": []}]}


def _run_mini(out_path: Path) -> dict:
    """Runs `fdp.main()` for real (--save) against MINI_DATASET, inside a scratch
    directory UNDER the repo root (main()'s own `--dataset` path resolution requires
    `Path(args.dataset).resolve().relative_to(ROOT)` - anything outside the repo raises)."""
    scratch_dir = fdp.ROOT / ".loop" / "explore" / "_test_facts_dilution_scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    data_path = scratch_dir / "mini.json"
    data_path.write_text(json.dumps(MINI_DATASET), encoding="utf-8")
    saved_argv = sys.argv
    saved_capture = nt_api.capture_session
    nt_api.capture_session = _fake_capture_session
    try:
        sys.argv = ["facts_dilution_probe.py", "--dataset", str(data_path), "--limit", "1",
                   "--save", "--out", str(out_path)]
        rc = fdp.main()
        return {"rc": rc, "artifact": (json.loads(out_path.read_text(encoding="utf-8"))
                                       if out_path.exists() else {})}
    finally:
        sys.argv = saved_argv
        nt_api.capture_session = saved_capture
        shutil.rmtree(scratch_dir, ignore_errors=True)


print("\n- item 4d: the real run wires pacer.install()/attach() around its own traffic -")
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "out.json"
        result = _run_mini(out_path)
    check("main() exits 0 on the mini dataset", result["rc"] == 0, str(result["rc"]))
    art = result["artifact"]
    check("ollama_transport is written (install() actually wrapped the embedder call)",
          "ollama_transport" in art, str(sorted(art)))
    check("calls > 0 (write_typed_note's own embed call, at least)",
          art.get("ollama_transport", {}).get("calls", 0) > 0, str(art.get("ollama_transport")))

print("\n- item 4d mutations: install()/attach() removed from the real run (in-process, "
      "fdp.pacer IS the _ollama_pacer module - reassigning its attribute simulates the "
      "call site being deleted without editing the file) -")
saved_install, saved_attach = fdp.pacer.install, fdp.pacer.attach
fdp.pacer.install = lambda: None                       # mutation: install() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen
    with tempfile.TemporaryDirectory() as td2:
        out_path2 = Path(td2) / "out.json"
        result_no_install = _run_mini(out_path2)
    check("mutation 'install() removed': no ollama_transport is written at all (nothing "
          "ever got paced - would FAIL the 'ollama_transport is written' check above)",
          "ollama_transport" not in result_no_install["artifact"],
          str(sorted(result_no_install["artifact"])))
fdp.pacer.install = saved_install

fdp.pacer.attach = lambda *a, **k: None                # mutation: attach() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen
    with tempfile.TemporaryDirectory() as td3:
        out_path3 = Path(td3) / "out.json"
        result_no_attach = _run_mini(out_path3)
    check("mutation 'attach() removed': no ollama_transport is written (the pacer paced "
          "the call but the artifact never learns it - would FAIL the same check above)",
          "ollama_transport" not in result_no_attach["artifact"],
          str(sorted(result_no_attach["artifact"])))
fdp.pacer.attach = saved_attach

check("fdp.pacer.install/attach are restored to the real functions after the mutations",
      fdp.pacer.install is saved_install and fdp.pacer.attach is saved_attach)

print(f"\nfacts_dilution_probe (item 4d pacer wiring): {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
