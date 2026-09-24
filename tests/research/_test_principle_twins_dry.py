"""`research/principle_twins.py` (A6, Q5): the dataset validator and `--dry`/`--help` paths,
with NO embedder call - the real sweep is deliberately not run (the GPU is busy with a
measurement campaign), so this suite only proves the plumbing that does not need one.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import principle_twins as pt  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


print("\n- the shipped dataset loads and validates clean -")
raw = pt.load_dataset(pt.DATA)
check("40 positive pairs", len(raw.get("positives") or []) == 40, str(len(raw.get("positives") or [])))
check("40 negative pairs", len(raw.get("negatives") or []) == 40, str(len(raw.get("negatives") or [])))
problems = pt.validate_dataset(raw)
check("no structural problems", not problems, "; ".join(problems[:5]))

print("\n- the validator actually catches a malformed dataset -")
raised = False
try:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.json"
        p.write_text(json.dumps({"positives": []}), encoding="utf-8")   # no "negatives" key
        pt.load_dataset(p)
except ValueError:
    raised = True
check("load_dataset raises ValueError on a missing top-level key", raised)

broken_rows = {
    "positives": [{"a": "same text twice", "b": "same text twice"}, {"a": "", "b": "y"}],
    "negatives": [{"a": "x"}],   # missing "b" entirely
}
problems2 = pt.validate_dataset(broken_rows)
check("an identical a/b pair is flagged", any("identical" in p for p in problems2), str(problems2))
check("an empty field is flagged", any("empty" in p for p in problems2), str(problems2))
check("a missing field is flagged", any("missing" in p for p in problems2), str(problems2))

print("\n- --dry validates and exits 0 on the shipped dataset, embeds/writes nothing -")
rc = pt.main(["--dry"])
check("--dry on the real dataset exits 0", rc == 0, str(rc))

print("\n- --dry on a malformed dataset exits nonzero instead of embedding it -")
with tempfile.TemporaryDirectory() as td2:
    bad_path = Path(td2) / "bad_shape.json"
    bad_path.write_text(json.dumps({"positives": [{"a": "x", "b": "x"}], "negatives": []}),
                        encoding="utf-8")
    rc_bad = pt.main(["--dry", "--data", str(bad_path)])
check("--dry on a broken dataset exits nonzero", rc_bad != 0, str(rc_bad))

print("\n- --help exits 0 and does not touch the embedder -")
try:
    pt.main(["--help"])
    help_rc = 0
except SystemExit as e:
    help_rc = e.code
check("--help exits 0 (argparse's own convention)", help_rc == 0, str(help_rc))

print("\n- the Wilson interval behaves at the edges -")
check("0/0 is (0, 0), not a division error", pt.wilson(0, 0) == (0.0, 0.0))
lo, hi = pt.wilson(0, 40)
check("0/40 has a positive upper bound (a zero count is not certainty)", hi > 0.0, str(hi))
check("0/40's upper bound stays well under 1", hi < 0.2, str(hi))
lo2, hi2 = pt.wilson(40, 40)
check("40/40's lower bound is high but not exactly 1", 0.8 < lo2 < 1.0, str(lo2))

print("\n- the sweep range matches the plan (0.75 to 0.95) -")
check("sweep starts at 0.75", pt.T_SWEEP[0] == 0.75, str(pt.T_SWEEP[0]))
check("sweep ends at 0.95", pt.T_SWEEP[-1] == 0.95, str(pt.T_SWEEP[-1]))

# item 4 (R-v2-ports): the real (non-dry) sweep wires pacer.install()/attach() around its
# own embedder traffic - a fake urlopen (never the stand's own wrapper) proves it end to
# end, on a 1-pair-each dataset so the run costs nothing real.
print("\n- item 4: the real sweep wires pacer.install()/attach() around its own traffic -")
import contextlib
import tempfile
import urllib.request
sys.path.insert(0, str(ROOT / "research"))
import _ollama_pacer as pacer  # noqa: E402


class _JsonResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(*a, **kw):
    return _JsonResp({"embedding": [0.1, 0.2, 0.3]})


@contextlib.contextmanager
def _isolated_pacer():
    assert not pacer.installed(), "a previous check left the pacer installed"
    saved_urlopen = urllib.request.urlopen
    pacer._reset_for_tests()
    try:
        yield
    finally:
        if pacer.installed():
            pacer.uninstall()
        urllib.request.urlopen = saved_urlopen
        pacer._reset_for_tests()


MINI_DATASET = {"positives": [{"a": "one small lesson", "b": "another small lesson"}],
                "negatives": [{"a": "a third distinct topic", "b": "a fourth distinct topic"}]}

with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen
    with tempfile.TemporaryDirectory() as td3:
        data_path = Path(td3) / "mini.json"
        out_path = Path(td3) / "out.json"
        data_path.write_text(json.dumps(MINI_DATASET), encoding="utf-8")
        rc3 = pt.main(["--data", str(data_path), "--out", str(out_path)])
        check("the real (non-dry) sweep exits 0 on a valid mini dataset", rc3 == 0, str(rc3))
        artifact = json.loads(out_path.read_text(encoding="utf-8"))
        ot = artifact.get("ollama_transport", {})
        check("ollama_transport is written (install() actually wrapped the embedder calls)",
              "ollama_transport" in artifact, str(sorted(artifact)))
        check("calls == 5: 1 liveness ping + 4 embeds (2 pairs x 2 sides, all distinct text)",
              ot.get("calls") == 5, str(ot))

print("\n- item 4 mutations: install()/attach() removed from the real sweep (in-process, "
      "pt.pacer IS the _ollama_pacer module - reassigning its attribute simulates the "
      "call site being deleted without editing the file) -")
saved_install, saved_attach = pt.pacer.install, pt.pacer.attach
pt.pacer.install = lambda: None                      # mutation: install() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen
    with tempfile.TemporaryDirectory() as td4:
        data_path = Path(td4) / "mini.json"
        out_path = Path(td4) / "out.json"
        data_path.write_text(json.dumps(MINI_DATASET), encoding="utf-8")
        pt.main(["--data", str(data_path), "--out", str(out_path)])
        art_no_install = json.loads(out_path.read_text(encoding="utf-8"))
    check("mutation 'install() removed': no ollama_transport is written at all (nothing "
          "ever got paced - would FAIL the 'ollama_transport is written' check above)",
          "ollama_transport" not in art_no_install, str(sorted(art_no_install)))
pt.pacer.install = saved_install

pt.pacer.attach = lambda *a, **k: None                # mutation: attach() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen
    with tempfile.TemporaryDirectory() as td5:
        data_path = Path(td5) / "mini.json"
        out_path = Path(td5) / "out.json"
        data_path.write_text(json.dumps(MINI_DATASET), encoding="utf-8")
        pt.main(["--data", str(data_path), "--out", str(out_path)])
        art_no_attach = json.loads(out_path.read_text(encoding="utf-8"))
    check("mutation 'attach() removed': no ollama_transport is written (the pacer paced "
          "the calls but the artifact never learns it - would FAIL the same check above)",
          "ollama_transport" not in art_no_attach, str(sorted(art_no_attach)))
pt.pacer.attach = saved_attach

check("pt.pacer.install/attach are restored to the real functions after the mutations",
      pt.pacer.install is saved_install and pt.pacer.attach is saved_attach)

print(f"\nprinciple twins (--dry, and item 4's real-sweep pacer wiring): {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
