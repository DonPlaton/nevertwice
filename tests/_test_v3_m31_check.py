#!/usr/bin/env python3
"""PREREG-V3 A8 M31 (the auditor's R-M31-SCOPE = (i), Q-SPLIT-2/3/5 = O-a, 2026-09-30): research/v3/m31_check.py on
synthetic wheels, offline - between a product's download and its install, every import its adapter makes and every
module the adapter's config selects in the product must be provided by the very wheels the install will use.

* part 1: the adapter and the files copied beside it, by AST (import, from, import_module with a literal);
* part 2: each top-level name to the distribution whose RECORD installs it - read in the zip, never unpacked, after
  every wheel is checked against the lock's sha256 again;
* part 3: the product's modules the adapter imports and those its config selects through the product's factories
  (M31_FACTORIES, declared before the download: a factory not found in the wheel is a problem by name), then their
  closure inside the product's wheel;
* Q-SPLIT-3: an unprovided name blocks when the adapter imports it, when a selected or adapter-imported module of the
  product imports it, or at module level anywhere in the closure; a lazy or optional one elsewhere is lazy_unprovided;
* the standard library is the base interpreter's own list (Q-SPLIT-2; given here, spawned in main);
* m31.json binds to the download record by its sha256; a download with problems or a moved wheel gives no verdict.

    python tests/_test_v3_m31_check.py
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


M31_PATH = ROOT / "research" / "v3" / "m31_check.py"
M = _load("v3_m31_check_t", M31_PATH) if M31_PATH.is_file() else None
LI = _load("v3_lock_install_m31t", ROOT / "research" / "v3" / "lock_install.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def holds(fn):
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def wheel(dist: str, version: str, files: dict, *, record: bool = True) -> tuple[str, bytes]:
    """A wheel: its files, a dist-info with METADATA and (unless record=False) a RECORD naming every file."""
    di = f"{dist}-{version}.dist-info"
    members = {**{p: s.encode() if isinstance(s, str) else s for p, s in files.items()},
               f"{di}/METADATA": f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n".encode()}
    if record:
        members[f"{di}/RECORD"] = ("".join(f"{p},," + "\n" for p in members) + f"{di}/RECORD,,\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p, b in members.items():
            z.writestr(p, b)
    return f"{dist}-{version}-py3-none-any.whl", buf.getvalue()


PRODX = {
    "prodx/__init__.py": "from .core import Memory\n",
    "prodx/core.py": "import json\nimport depa\nimport prodx.lazy\nimport prodx.modlevel\n"
                     "from prodx.utils.factory import EmbedderFactory\n\nclass Memory:\n    pass\n",
    "prodx/utils/__init__.py": "",
    "prodx/utils/factory.py": "class EmbedderFactory:\n    provider_to_class = {\n"
                              "        'ollama': 'prodx.embeddings.ollama.OllamaEmbedding',\n"
                              "        'openai': ('prodx.embeddings.openai.OpenAIEmbedding', None),\n    }\n",
    "prodx/embeddings/__init__.py": "",
    "prodx/embeddings/ollama.py": "def make():\n    try:\n        from ollama import Client\n    except ImportError:\n"
                                  "        raise\n    return Client\n",
    "prodx/embeddings/openai.py": "import openai_sdk\n",
    "prodx/lazy.py": "def f():\n    import lazydep\n    return lazydep\n",
    "prodx/modlevel.py": "import modleveldep\n",
}
ADAPTER = (b"import json\nimport os\nimport base as B\n"
           b"def build():\n    import prodx\n    from prodx.core import Memory\n    import depa\n    import adapterdep\n"
           b"    try:\n        import optdep\n    except ImportError:\n        optdep = None\n    return Memory\n")
BESIDE = {"base.py": b"import json\n", "_http_count.py": b"def install():\n    try:\n        import httpx\n"
                                                         b"    except ImportError:\n        httpx = None\n"}
ARMS = {"prodx-arm": {"adapter": ("arm_prodx.py", ADAPTER), "beside": BESIDE}}
FACTORIES = {"prodx-arm": {"file": "prodx/utils/factory.py", "select": {"EmbedderFactory": ("ollama",)}}}
STDLIB = frozenset({"json", "os", "sys", "re", "pathlib"})
TMP = Path(tempfile.mkdtemp(prefix="nvt3_m31_"))


def world(tag: str, wheels: list, *, problems=(), tamper: str | None = None) -> tuple[Path, Path]:
    runs = TMP / tag / "runs" / "v3"
    base = runs / "_install" / "a8-pypi-prodx_v3" / "d1"
    wd = runs / "_fetch.a8-pypi-prodx_v3" / "d1" / "fetch" / "j1" / "wheels"
    base.mkdir(parents=True)
    wd.mkdir(parents=True)
    lock = []
    for fn, data in wheels:
        (wd / fn).write_bytes(data)
        lock.append({"name": fn.split("-")[0], "version": fn.split("-")[1], "filename": fn, "url": f"https://x/{fn}",
                     "sha256": sha(data), "requested": fn.startswith("prodx-"), "licence": "MIT", "requested_extras": []})
    if tamper:
        (wd / tamper).write_bytes((wd / tamper).read_bytes() + b"!")
    dr = {"window": "a8-pypi-prodx_v3", "run": "d1", "phase": "download", "problems": list(problems), "lock": lock,
          "lock_sha256": LI.lock_sha256(lock), "wheel_dir": str(wd), "pip": {"version": "25.2", "args": []}}
    (base / "download_record.json").write_bytes((json.dumps(dr, indent=1, sort_keys=True) + "\n").encode())
    return runs, base


def m31(runs: Path, **over):
    if M is None:
        raise AttributeError("no research/v3/m31_check.py")
    kw = dict(venv_name="prodx_v3", run="d1", runs_root=runs, stdlib=STDLIB, arms_sources=ARMS, factories=FACTORIES,
              venv_arms={"prodx_v3": ("prodx-arm",)})
    kw.update(over)
    return M.run_m31(**kw)


def names(rec, key):
    return sorted((rec or {}).get(key) or [])


try:
    W_PRODX = wheel("prodx", "1.0.0", PRODX)
    W_DEPA = wheel("depa", "2.0.0", {"depa/__init__.py": ""})
    W_OLLAMA = wheel("ollama", "0.5.0", {"ollama/__init__.py": "class Client: pass\n"})

    print("- a download whose wheels provide all but some names -")
    runs, base = world("base", [W_PRODX, W_DEPA])
    rec, err = holds(lambda: m31(runs))
    check("M3-1 (part 2): each third-party top-level name maps to the distribution whose RECORD installs it - prodx to "
          "prodx, depa to depa - the standard library and the files beside the adapter are no third party",
          err is None and rec["parts"]["2"].get("prodx") == ["prodx"] and rec["parts"]["2"].get("depa") == ["depa"]
          and "json" not in rec["parts"]["2"] and "base" not in rec["parts"]["2"], str(err or (rec or {}).get("parts", {}).get("2")))
    check("M3-2 (part 1, Q-SPLIT-3): a name the adapter imports and no wheel provides blocks (adapterdep, lazily in build() "
          "yet the adapter's own); an optional one (optdep, under except ImportError) and a beside file's optional one "
          "(httpx) are lazy_unprovided only",
          err is None and "adapterdep" in names(rec, "unprovided") and "optdep" not in names(rec, "unprovided")
          and {"optdep", "httpx"} <= set(names(rec, "lazy_unprovided")), f"{err} {names(rec, 'unprovided')} {names(rec, 'lazy_unprovided')}")
    check("M3-3 (part 3): the factory-selected module (EmbedderFactory 'ollama' -> prodx.embeddings.ollama) is followed; "
          "its import of ollama - lazy and under try, yet in a SELECTED module - blocks while no wheel provides it",
          err is None and "ollama" in names(rec, "unprovided")
          and any(s.get("name") == "ollama" and s.get("blocking") and "prodx/embeddings/ollama.py" in s.get("where", "")
                  for s in rec["parts"]["3"]), f"{err} {names(rec, 'unprovided')}")
    check("M3-4 (part 3, Q-SPLIT-3): in the closure, a module-level import no wheel provides blocks (modleveldep, "
          "prodx/modlevel.py); a lazy one is lazy_unprovided only (lazydep, prodx/lazy.py)",
          err is None and "modleveldep" in names(rec, "unprovided") and "lazydep" not in names(rec, "unprovided")
          and "lazydep" in names(rec, "lazy_unprovided"), f"{err} {names(rec, 'unprovided')} {names(rec, 'lazy_unprovided')}")
    check("M3-5: a provider the config does not select is never followed (openai_sdk of prodx.embeddings.openai is named "
          "nowhere)", err is None and "openai_sdk" not in json.dumps(rec), "")
    raw_dr = (base / "download_record.json").read_bytes()
    written = json.loads((base / "m31.json").read_bytes()) if (base / "m31.json").is_file() else {}
    check("M3-6: m31.json is written beside the download record, bound to it by sha256 (and to its lock), with the "
          "standard library's source named - and it is what the call returned",
          written.get("download_record_sha256") == sha(raw_dr) and written.get("lock_sha256") == json.loads(raw_dr)["lock_sha256"]
          and written.get("unprovided") == names(rec, "unprovided") and (written.get("stdlib") or {}).get("count") == len(STDLIB),
          str({k: written.get(k) for k in ("download_record_sha256", "stdlib")})[:300])

    print("\n- the same product with every name provided -")
    W_ADD = [wheel(n, "1.0", {f"{n}/__init__.py": ""}) for n in ("adapterdep", "modleveldep")]
    runs2, base2 = world("clean", [W_PRODX, W_DEPA, W_OLLAMA, *W_ADD])
    rec2, err2 = holds(lambda: m31(runs2))
    check("M3-7: with ollama, adapterdep and modleveldep in the download, nothing is unprovided (the lazy and optional "
          "names stay listed, never blocking) - the clean M31 --install-from needs",
          err2 is None and rec2["unprovided"] == [] and rec2["problems"] == [] and "lazydep" in names(rec2, "lazy_unprovided"),
          str(err2 or (rec2["unprovided"], rec2["problems"])))

    print("\n- part 1 is checkable (the auditor's condition to K2, 2026-09-30): our imports are literal and absolute -")
    ADAPTER_NL = ADAPTER + b"import importlib\ndef late(n):\n    return importlib.import_module(n)\n"
    BESIDE_REL = {**BESIDE, "helper.py": b"from . import base\n"}
    runs17, _b17 = world("part1", [W_PRODX, W_DEPA, W_OLLAMA, *W_ADD])
    r17, e17 = holds(lambda: m31(runs17, arms_sources={"prodx-arm": {"adapter": ("arm_prodx.py", ADAPTER_NL),
                                                                     "beside": BESIDE_REL}}))
    p17 = (r17 or {}).get("problems") or []
    u17 = [u.get("where", "") for u in (r17 or {}).get("unresolved") or []]
    check("M3-17: a non-literal import_module in our adapter is a problem in m31.json by name (its file and line) - and "
          "still listed as unresolved; our code must be checkable, so --install-from refuses it",
          e17 is None and any("arm_prodx.py:16" in p and "non-literal" in p for p in p17) and "arm_prodx.py:16" in u17,
          str(e17 or (p17, u17))[:400])
    check("M3-18: a relative import in a file beside the adapter is a problem in m31.json by name (helper.py:1)",
          e17 is None and any("helper.py:1" in p and "relative import" in p for p in p17), str(e17 or p17)[:400])
    PRODX_NL = {**PRODX, "prodx/modlevel.py": "import modleveldep\nimport importlib\n\ndef g(n):\n"
                                              "    return importlib.import_module(n)\n"}
    runs19, _b19 = world("part3", [wheel("prodx", "1.0.0", PRODX_NL), W_DEPA, W_OLLAMA, *W_ADD])
    r19, e19 = holds(lambda: m31(runs19))
    check("M3-19: in the product's closure a non-literal import stays a record for E5 (unresolved) - never a problem: "
          "the vendor's code is what it is",
          e19 is None and r19["problems"] == [] and any("prodx/modlevel.py:5" in u.get("where", "") for u in r19["unresolved"]),
          str(e19 or (r19["problems"], r19["unresolved"]))[:400])
    real_part1 = {}
    try:
        _real_arms = sorted({a for v in M.VENV_ARMS.values() for a in v})
        _real_src = M.load_arm_sources(_real_arms)
        for a in _real_arms:
            for fname, data in [_real_src[a]["adapter"], *sorted(_real_src[a]["beside"].items())]:
                s_, un_ = M.imports_of(data, fname)
                bad_ = [f"{fname}:{u['line']} {u['why']}" for u in un_] + [f"{fname}:{x['line']} relative"
                                                                           for x in s_ if x["level"]]
                if bad_:
                    real_part1[a] = real_part1.get(a, []) + bad_
    except Exception as e:  # noqa: BLE001 - the row FAILs by name
        real_part1 = {"error": f"{type(e).__name__}: {e}"}
    check("M3-20: the real adapters of VENV_ARMS and every file beside them import only literally and absolutely - "
          "their M31 has no part-1 problem to refuse (the pacer's httpx/httpx2 imports are literal)",
          real_part1 == {}, str(real_part1)[:400])

    print("\n- B-M31-CFG (the auditor 2026-09-30): the config module of a selected provider is declared and followed -")
    PRODX_CFG = {**PRODX, "prodx/configs/__init__.py": "", "prodx/configs/store/__init__.py": "",
                 "prodx/configs/store/x.py": "import json\nimport cfgdep\n\nclass XConfig:\n    pass\n",
                 "prodx/store_configs.py": "def load(p):\n    return __import__(f'prodx.configs.store.{p}')\n"}
    FAC_CFG = {"prodx-arm": {**FACTORIES["prodx-arm"], "configs": ("prodx.configs.store.x",)}}
    runs21, _b21 = world("cfg", [wheel("prodx", "1.0.0", PRODX_CFG), W_DEPA, W_OLLAMA, *W_ADD])
    r21, e21 = holds(lambda: m31(runs21, factories=FAC_CFG))
    r21n, e21n = holds(lambda: m31(runs21))
    check("M3-21 (B-M31-CFG): a provider's config module the product imports only by a non-literal __import__ is declared "
          "beside its factory selection and followed - its module-level import of an unprovided name blocks (cfgdep, "
          "prodx/configs/store/x.py); undeclared, it is outside the closure",
          e21 is None and "cfgdep" in names(r21, "unprovided") and "prodx.configs.store.x" in (r21.get("entries") or {})
          and any("prodx/configs/store/x.py" in s_.get("where", "") and s_.get("blocking") for s_ in r21["parts"]["3"])
          and e21n is None and "cfgdep" not in names(r21n, "unprovided"),
          str(e21 or (names(r21, "unprovided"), sorted((r21 or {}).get("entries") or {})))[:400])
    r22, e22 = holds(lambda: m31(runs21, factories={"prodx-arm": {**FACTORIES["prodx-arm"],
                                                                  "configs": ("prodx.configs.store.nosuch",)}}))
    check("M3-22 (B-M31-CFG): a declared config module the download's wheels do not have is a problem by name",
          e22 is None and any("prodx.configs.store.nosuch" in p_ and "config" in p_ for p_ in r22["problems"]),
          str(e22 or r22["problems"])[:300])

    print("\n- refusals: no verdict -")
    r3, e3 = holds(lambda: m31(world("probs", [W_PRODX], problems=["job 1 failed"])[0]))
    check("M3-8: a download with problems gets no verdict - refused by name, no m31.json",
          e3 is not None and "M31Refused" in e3 and "problems" in e3
          and not (TMP / "probs" / "runs" / "v3" / "_install" / "a8-pypi-prodx_v3" / "d1" / "m31.json").exists(), str(e3 or r3))
    runs4, base4 = world("moved", [W_PRODX, W_DEPA], tamper=W_DEPA[0])
    r4, e4 = holds(lambda: m31(runs4))
    check("M3-9: a wheel whose bytes moved since the download is refused by name (the lock's sha256 again) - no m31.json",
          e4 is not None and "M31Refused" in e4 and W_DEPA[0] in e4 and not (base4 / "m31.json").exists(), str(e4 or r4))
    runs5, base5 = world("norecord", [W_PRODX, wheel("depa", "2.0.0", {"depa/__init__.py": ""}, record=False)])
    r5, e5 = holds(lambda: m31(runs5))
    check("M3-10: a wheel without its RECORD is refused by name", e5 is not None and "RECORD" in e5, str(e5 or r5))
    runs6, base6 = world("factory", [W_PRODX, W_DEPA])
    r6, e6 = holds(lambda: m31(runs6, factories={"prodx-arm": {"file": "prodx/utils/factory.py",
                                                                "select": {"EmbedderFactory": ("gemini",)}}}))
    check("M3-11 (Q-SPLIT-5 = O-a): a declared factory selection the wheel does not have is a problem by name (the table "
          "is fixed through a gate) - never skipped", e6 is None and any("gemini" in p and "EmbedderFactory" in p
                                                                         for p in r6["problems"]), str(e6 or r6.get("problems")))
    runs16, base16 = world("locksha", [W_PRODX, W_DEPA])
    dr16 = json.loads((base16 / "download_record.json").read_bytes())
    dr16["lock_sha256"] = "1" * 64
    (base16 / "download_record.json").write_bytes((json.dumps(dr16, indent=1, sort_keys=True) + "\n").encode())
    r16, e16 = holds(lambda: m31(runs16))
    check("M3-16: a download record whose lock is not its lock_sha256 is refused by name - no m31.json",
          e16 is not None and "M31Refused" in e16 and "lock_sha256" in e16 and not (base16 / "m31.json").exists(),
          str(e16 or r16))
    # a clean download of the venv itself, so that its arms are the only reason left to refuse
    runs7, base7 = world("noprod", [W_PRODX])
    r7, e7 = holds(lambda: m31(runs7, venv_arms={}))
    r7b, e7b = holds(lambda: m31(runs7, venv_arms={"prodx_v3": ()}))
    check("M3-12: a venv that is no product, or has no declared arms, gets no M31 - refused by name, no m31.json",
          all(e is not None and "M31Refused" in e and "no product arms declared" in e for e in (e7, e7b))
          and not (base7 / "m31.json").exists(), str([e7 or r7, e7b or r7b])[:400])

    print("\n- the real tables -")
    PL = _load("v3_run_plan_m31t", ROOT / "research" / "v3" / "run_v3_plan.py")
    VA = getattr(M, "VENV_ARMS", None) if M else None
    check("M3-13: VENV_ARMS names each product venv's arms - mem0_v3: mem0 and mem0-store, graphiti_v3: zep-graphiti, "
          "langmem_v3: langmem and langmem-store (cognee_v3's adapter is A8's: its M31 before its window) - each an arm "
          "of the plan whose adapter and beside files M31 reads",
          VA == {"mem0_v3": ("mem0", "mem0-store"), "graphiti_v3": ("zep-graphiti",), "langmem_v3": ("langmem", "langmem-store")}
          and all(a in PL.ARMS and PL.ARMS[a].code for arms in VA.values() for a in arms), str(VA))
    src = (ROOT / "research" / "v3" / "arms" / "arm_mem0.py").read_text(encoding="utf-8")
    provs = {n.values[[k.value for k in n.keys if isinstance(k, ast.Constant)].index("provider")].value
             for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Dict)
             and "provider" in [k.value for k in n.keys if isinstance(k, ast.Constant)]}
    FA = getattr(M, "M31_FACTORIES", {}) if M else {}
    sel = {(a, f, k) for a in ("mem0", "mem0-store") for f, ks in (FA.get(a) or {}).get("select", {}).items() for k in ks}
    check("M3-14 (Q-SPLIT-5 = O-a): M31_FACTORIES selects in mem0's utils/factory.py exactly the providers arm_mem0's config "
          "names - LLM deepseek (mem0) and ollama (mem0-store's NO_LLM), embedder ollama, vector store qdrant",
          provs == {"deepseek", "ollama", "qdrant"} and sel == {
              ("mem0", "LlmFactory", "deepseek"), ("mem0", "EmbedderFactory", "ollama"), ("mem0", "VectorStoreFactory", "qdrant"),
              ("mem0-store", "LlmFactory", "ollama"), ("mem0-store", "EmbedderFactory", "ollama"),
              ("mem0-store", "VectorStoreFactory", "qdrant")}
          and all((FA.get(a) or {}).get("file") == "mem0/utils/factory.py" for a in ("mem0", "mem0-store")), f"{provs} {sorted(sel)}")
    cfgs = {a: tuple((FA.get(a) or {}).get("configs") or ()) for a in ("mem0", "mem0-store")}
    check("M3-23 (B-M31-CFG): M31_FACTORIES declares the config module of each selected provider mem0 loads by a "
          "non-literal import - mem0/vector_stores/configs.py:49 __import__(mem0.configs.vector_stores.<provider>): "
          "qdrant's, for both arms", cfgs == {"mem0": ("mem0.configs.vector_stores.qdrant",),
                                              "mem0-store": ("mem0.configs.vector_stores.qdrant",)}, str(cfgs))
    print("\n- the base's standard library (Q-SPLIT-2 = O-a) -")
    if M is not None:
        IV = LI._iv()
        saved = (IV._step, IV.check_summary, IV.check_problems)
        seen: list = []

        def fake_step(c_, L_, **k):
            seen.append(k)
            ok = k["argv"][-1] == M.STDLIB_PROBE
            return (0 if ok else 1), json.dumps({"python": "3.12.10", "names": ["json", "os"]}).encode(), b"", None
        IV._step, IV.check_summary, IV.check_problems = fake_step, (lambda chk: {}), (lambda k, s: [])
        try:
            got15, e15 = holds(lambda: M.base_stdlib(object(), object(), python=TMP / "py312" / "python.exe",
                                                     window="a8-pypi-mem0_v3", run="d1", parent_env={}))
            IV._step = lambda c_, L_, **k: (1, b"", b"boom", None)
            _g, e15b = holds(lambda: M.base_stdlib(object(), object(), python=TMP / "py312" / "python.exe",
                                                   window="a8-pypi-mem0_v3", run="d1", parent_env={}))
        finally:
            IV._step, IV.check_summary, IV.check_problems = saved
        argv = (seen[0].get("argv") if seen else None) or []
        check("M3-15: the standard library is the BASE interpreter's own sys.stdlib_module_names - asked under the contract "
              "(install_v3_data._step), isolated and without bytecode, its version named; a failed step refuses by name",
              e15 is None and got15[0] == ["json", "os"] and "3.12.10" in got15[1] and argv[:3] == [str(TMP / "py312" / "python.exe"), "-I", "-B"]
              and seen[0].get("arm") == "m31" and e15b is not None and "M31Refused" in e15b, f"{e15} {argv[:3]} {e15b}")
    else:
        check("M3-15: the standard library is the BASE interpreter's own sys.stdlib_module_names", False, "no module")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 m31 check: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
