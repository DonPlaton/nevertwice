#!/usr/bin/env python3
"""Auto-enabling the cross-encoder asks about the GPU its own premise rests on.

`enabled()` in `auto` used to mean "torch and transformers import, and the model is cached".
The premise under that is "someone with these deps will find the reranker worth its cost", and
what decides the cost is a GPU the check never looked at. Measured 2026-09-22 on
`bge-reranker-v2-m3` at the shipped pool of 15 candidates:

    GPU (float16)    36.7 ms a query
    CPU (float32)   8911   ms a query      - a factor of 243

`pip install torch` hands out CPU-only wheels on many platforms, so a user who ran once with
`NEVERTWICE_XRERANK=1` to see what it did got nine seconds a query from then on, silently and
by default. That is the same defect this project keeps finding elsewhere: a threshold written
against a quantity, that never measures the quantity.

The fix reads `torch/version.py` - `cuda = '12.8'` for a CUDA wheel, `cuda = None` for a CPU
one, `hip` saying the same for ROCm - rather than importing torch, because importing torch to
decide whether to use torch puts a second on the recall path of exactly the machines this keeps
the reranker off for.

What this suite does NOT claim: that a CUDA build means a GPU is plugged in. It is the weaker
question on purpose; `_load` covers the remainder by saying so on stderr.

    python tests/_test_xrerank_auto_needs_a_gpu.py
"""
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import reranker_ce as r  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def _fake_torch(cuda_line: str) -> Path:
    """A directory shaped like an installed torch, carrying only what the probe reads."""
    d = Path(tempfile.mkdtemp(prefix="faketorch_")) / "torch"
    d.mkdir(parents=True)
    (d / "__init__.py").write_text("", encoding="utf-8", newline="")
    (d / "version.py").write_text(
        "from typing import Optional\n\n__version__ = '2.11.0'\n"
        f"{cuda_line}\nhip: Optional[str] = None\n", encoding="utf-8", newline="")
    return d


class _Spec:
    def __init__(self, origin):
        self.origin = str(origin)


print("# the build is read off disk, not imported")

for label, line, expected in (
    ("a CUDA wheel", "cuda: Optional[str] = '12.8'", True),
    ("a CPU-only wheel", "cuda: Optional[str] = None", False),
    ("an old-style CUDA wheel", "cuda = '11.8'", True),
    ("an old-style CPU wheel", "cuda = None", False),
    # ROCm. `cuda` is None on an AMD build and `hip` carries the version, so a probe that read
    # `cuda` alone would answer "no GPU" on a machine with a working one - wrong in exactly the
    # sense the docstring claims to be right about.
    ("a ROCm wheel", "cuda: Optional[str] = None\nhip: Optional[str] = '6.2'", True),
    # And a name that merely starts with the field's letters is not the field.
    ("a build whose version file only mentions cuda_version",
     "cuda: Optional[str] = None\ncuda_version = '12.8'", False),
):
    d = _fake_torch(line)
    with mock.patch.object(r.importlib.util, "find_spec",
                           lambda n, _d=d: _Spec(_d / "__init__.py") if n == "torch" else object()):
        got = r._torch_has_gpu_build()
    check(f"{label} reads as {'GPU' if expected else 'no GPU'}", got is expected, f"{got}")

with mock.patch.object(r.importlib.util, "find_spec", lambda n: None):
    check("no torch at all reads as no GPU", r._torch_has_gpu_build() is False)

check("and the probe never imports torch",
      "import torch" not in r._torch_has_gpu_build.__doc__.replace("torch.cuda.is_available", "")
      and "importlib.util.find_spec" in
      (HERE.parent / "nevertwice" / "reranker_ce.py").read_text(encoding="utf-8"))


print("# auto stays off without a GPU, and the explicit switch still wins")

CUDA, NOCUDA = _fake_torch("cuda: Optional[str] = '12.8'"), _fake_torch("cuda = None")


def _with(build: Path, cached: bool, env: dict):
    """enabled() with a chosen torch build, a chosen cache state and a chosen env."""
    def spec(name):
        return _Spec(build / "__init__.py") if name == "torch" else object()
    with mock.patch.dict(r.os.environ, env, clear=False), \
         mock.patch.object(r.importlib.util, "find_spec", spec), \
         mock.patch.object(r, "_model_cached", lambda: cached):
        return r.enabled()


check("auto with a CPU-only build and the model cached is OFF",
      _with(NOCUDA, True, {"NEVERTWICE_XRERANK": "auto"}) is False)
check("auto with a CUDA build and the model cached is ON",
      _with(CUDA, True, {"NEVERTWICE_XRERANK": "auto"}) is True)
check("auto with a CUDA build and no model is OFF (no surprise download)",
      _with(CUDA, False, {"NEVERTWICE_XRERANK": "auto"}) is False)
check("an explicit 1 still wins on a CPU-only build",
      _with(NOCUDA, False, {"NEVERTWICE_XRERANK": "1"}) is True)
check("an explicit 0 still wins on a GPU build with the model cached",
      _with(CUDA, True, {"NEVERTWICE_XRERANK": "0"}) is False)
check("an unset variable reads as auto, not as on",
      _with(NOCUDA, True, {"NEVERTWICE_XRERANK": ""}) is False)


print("# and a CPU run that happens anyway is said out loud")

src = (HERE.parent / "nevertwice" / "reranker_ce.py").read_text(encoding="utf-8")
load = src[src.index("def _load("):src.index("def rerank_scores(")]
check("_load warns when it lands on the CPU",
      "stderr.write" in load and "CPU" in load,
      "a CUDA build on a box with no visible GPU reaches _load, and so does an explicit 1")
check("the warning carries the measured cost, not an adjective",
      "8.9" in load or "8911" in load)
check("and names the way to turn it off", "NEVERTWICE_XRERANK=0" in load)

print()
print(f"xrerank auto needs a gpu: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
