#!/usr/bin/env python3
"""PREREG-V3 A8 B-NLP NLP-3b (the auditor's PASSIVE check, Q-NLP-2): research/v3/arms/arm_mem0.py's nlp_state - spawns
NO child and imports no product: the adapter module is loaded in this process (it imports mem0 only when a unit
starts) and nlp_state reads an injected module table and an injected installed-check:

* what spaCy's state is, as mem0 left it - read, never loaded: mem0.utils.spacy_models' four names by exactly
  NLP_NAMES (a name the module does not have is None, never a default; a failed flag that is not a bool is None);
  whether the module was imported at all; the model's distribution by its metadata only (importlib.metadata - spaCy
  itself is never imported to ask);
* NLP_NAMES are exactly the names probe_a8's M0_SOURCE facts find in mem0's real-shaped spacy_models (the adapter and
  the probe's source facts cannot drift apart), and probe_a8.m0_nlp_active passes on nlp_state's own answer;
* counters() carries nlp_state()'s answer under "nlp" (the AST of the adapter; the live answer is
  _test_v3_arm_mem0's, under the lock).

    python tests/research/_test_v3_arm_mem0_nlp.py
"""
from __future__ import annotations

import ast
import atexit
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


A = _load("v3_arm_mem0_nlp_t", ROOT / "research" / "v3" / "arms" / "arm_mem0.py")
PA = _load("v3_probe_a8_nlp_t", ROOT / "research" / "v3" / "probe_a8.py")
PASSED = FAILED = 0
RAISED: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def ok(fn) -> bool:
    try:
        return bool(fn())
    except Exception as e:  # noqa: BLE001 - the row reads it
        RAISED.append(f"{type(e).__name__}: {e}")
        return False


SM = "mem0.utils.spacy_models"
LOADED = SimpleNamespace(_nlp_full=object(), _nlp_lemma=object(), _load_failed_full=False, _load_failed_lemma=False)
asked: list = []


def installed(name):
    asked.append(name)
    return True


print("- nlp_state: read, never loaded -")
st = A.nlp_state(modules={SM: LOADED}, installed=installed)
check("both models loaded, no failed flag, the model installed - by exactly NLP_NAMES",
      ok(lambda: st == {"names": {k: v for k, v in A.NLP_NAMES.items() if k != "module"}, "module": True, "nlp_full": True,
                        "nlp_lemma": True, "failed_full": False, "failed_lemma": False, "is_package": True}
         and asked == [A.NLP_NAMES["model"]]), str(st))
st = A.nlp_state(modules={}, installed=lambda n: False)
check("the module never imported: module False, every state unknown (None), the model's install as asked",
      ok(lambda: st["module"] is False and st["nlp_full"] is None and st["nlp_lemma"] is None and st["failed_full"] is None
         and st["failed_lemma"] is None and st["is_package"] is False), str(st))
st = A.nlp_state(modules={SM: SimpleNamespace(_nlp_full=None, _nlp_lemma=None, _load_failed_full=True,
                                              _load_failed_lemma=False)}, installed=installed)
check("nothing loaded after the adds, a failed flag set: exactly that, read", ok(lambda: st["nlp_full"] is False
      and st["nlp_lemma"] is False and st["failed_full"] is True and st["failed_lemma"] is False), str(st))
st = A.nlp_state(modules={SM: SimpleNamespace(_nlp_lemma=object(), _load_failed_full="yes")}, installed=installed)
check("a name the module does not have is None (never a default), a failed flag that is not a bool is None",
      ok(lambda: st["nlp_full"] is None and st["nlp_lemma"] is True and st["failed_full"] is None
         and st["failed_lemma"] is None), str(st))
check("the real installed-check reads metadata only: a distribution this interpreter has is True, one it has not is False",
      ok(lambda: A._installed("pip") is True and A._installed("nvt3-no-such-distribution-x") is False
         and "spacy" not in sys.modules), str(sorted(m for m in sys.modules if "spacy" in m)))

before = set(sys.modules)
check("called as counters() calls it - the live module table, the real metadata check - it imports nothing: no spaCy, "
      "no mem0 module appears", ok(lambda: A.nlp_state()["module"] is False
                                   and not {m for m in set(sys.modules) - before if m.split(".")[0] in ("spacy", "mem0")}),
      str(sorted(set(sys.modules) - before)[:10]))

print("\n- the adapter and the probe's source facts agree -")
TMP = Path(tempfile.mkdtemp(prefix="nvt3_arm_mem0_nlp_"))
atexit.register(shutil.rmtree, TMP, True)
(TMP / "mem0" / "utils").mkdir(parents=True)
(TMP / "mem0" / "utils" / "spacy_models.py").write_bytes(
    b"import threading\n\n_nlp_full = None\n_nlp_lemma = None\n_load_failed_full = False\n_load_failed_lemma = False\n"
    b"_lock = threading.Lock()\n\n\ndef _ensure_model_available():\n    import spacy\n"
    b"    if not spacy.util.is_package(\"en_core_web_sm\"):\n        download(\"en_core_web_sm\")\n\n\n"
    b"def get_nlp_full():\n    global _nlp_full, _load_failed_full\n    if _nlp_full is not None:\n        return _nlp_full\n"
    b"    return None\n")
facts = PA.nlp_facts(TMP)
check("NLP_NAMES are exactly the names the probe's source facts find in mem0's spacy_models (and its model)",
      ok(lambda: {k: facts[PA.M0_NLP_NAMES[k]]["value"] for k in PA.M0_NLP_NAMES}
         == {k: v for k, v in A.NLP_NAMES.items() if k != "module"}), str({k: (v or {}).get("value") for k, v in facts.items()}))
check("... and the module NLP_NAMES reads is spacy_models' own dotted name", A.NLP_NAMES["module"] == SM)
r = PA.m0_nlp_active(A.nlp_state(modules={SM: LOADED}, installed=installed), facts, catcher_lines=0)
check("probe_a8.m0_nlp_active passes on nlp_state's own answer, and is blocked:nlp-off on an unloaded one",
      ok(lambda: r["ok"] is True and PA.m0_nlp_active(A.nlp_state(modules={}, installed=installed), facts,
                                                      catcher_lines=0).get("blocked") == "blocked:nlp-off"), str(r))

print("\n- counters() carries it -")
tree = ast.parse((ROOT / "research" / "v3" / "arms" / "arm_mem0.py").read_text(encoding="utf-8"))
cnt = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "counters"), None)
rets = [n.value for n in ast.walk(cnt) if isinstance(n, ast.Return)] if cnt else []
keys = [(k.value if isinstance(k, ast.Constant) else None, v) for r_ in rets if isinstance(r_, ast.Dict)
        for k, v in zip(r_.keys, r_.values)]
check("the adapter's counters() answers nlp_state() under \"nlp\" - no argument, so the live module table and the real "
      "metadata check", ok(lambda: any(k == "nlp" and isinstance(v, ast.Call) and getattr(v.func, "id", None) == "nlp_state"
                                        and not v.args and not v.keywords for k, v in keys)), str([k for k, _ in keys]))
check("no row's condition raised", RAISED == [], str(RAISED))
print(f"\nv3 arm_mem0 nlp: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
