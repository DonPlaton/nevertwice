#!/usr/bin/env python3
"""PREREG-V3 A8 M31 (the auditor's R-M31-SCOPE = (i) and Q-SPLIT-2/3/5 = O-a, 2026-09-30): between a product venv's
download (lock_install --download-only) and its install (--install-from), every import its adapter makes and every
module of the product its config selects must be provided by the very wheels the install will use. Offline: nothing is
installed, run or unpacked; the result, m31.json, is written beside the download record and bound to it by sha256.

* part 1 - the adapter and the files copied beside it (run_v3_plan.ARMS: adapter and code), read by AST: import, from
  and importlib.import_module/__import__ with a literal - a non-literal or a relative one there is listed as
  unresolved AND is a problem by file and line (our code is checkable; the auditor's condition to K2), while in the
  product's closure such an import is an unresolved record for E5 only when a declared M31_FACTORIES record explains it
  (file:line -> the arm's factories or configs) - otherwise it is a problem by name (R-M31-UNRES);
* part 2 - each third-party top-level name to the distribution whose RECORD installs it, from the wheels themselves (read
  in the zip; every wheel checked against the lock's sha256 again first - a moved one refuses);
* part 3 - the product's modules the adapter imports and those its config selects through the product's factories
  (M31_FACTORIES, declared before the download: a class or key the wheel does not have is a problem by name, fixed
  through a gate) and the config module of each selected provider the product loads only by a non-literal import
  (M31_FACTORIES' configs, B-M31-CFG), then their closure inside the product's wheel (relative imports resolved, parent packages included);
  every import that leaves the product is checked as in part 2;
* the standard library is the base interpreter's own sys.stdlib_module_names (Q-SPLIT-2), asked under the contract in
  main() (-I -B) and named in the result with its sha256.
Q-SPLIT-3: an unprovided name BLOCKS when the adapter imports it (unless under an except ImportError), when a module the
adapter imports or its config selects imports it (lazy or not), or at module level anywhere in the closure; a lazy or
optional one elsewhere (a beside file's, a deep module's function) is lazy_unprovided - E5 names it and the A8 probe runs
the real path. Any blocking name is an amendment to PREREG-V3 (the auditor's text) before the install - never a silent
add; lock_install --install-from refuses a product without a clean m31.json of its download.

    python research/v3/m31_check.py --venv mem0_v3 --run d1
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOW_PREFIX = "a8-pypi-"
DOWNLOAD_RECORD = "download_record.json"
#: The arms each product venv runs (run_v3_plan.ARMS names their adapters and beside files). cognee_v3's adapter is A8's
#: own: its M31 comes before its window (draft, check, window - the auditor's order).
VENV_ARMS = {"mem0_v3": ("mem0", "mem0-store"), "graphiti_v3": ("zep-graphiti",), "langmem_v3": ("langmem", "langmem-store")}
#: Q-SPLIT-5 = O-a: the product modules each arm's config selects through the product's factories, declared before the
#: download - mem0's utils/factory.py maps a provider to a class path (a string, or a tuple whose first item is one);
#: arm_mem0.mem0_config names LLM deepseek (mem0) or NO_LLM's ollama (mem0-store), embedder ollama, vector store qdrant.
#: "configs" (B-M31-CFG, the auditor 2026-09-30): the config module of a selected provider that the product loads only
#: by a non-literal import - mem0/vector_stores/configs.py:49 __import__(f"mem0.configs.vector_stores.{provider}") - an
#: entry of the closure like a factory's target, so its imports are checked too.
#: "explains" (R-M31-UNRES, the auditor 2026-09-30): each unresolved import of the product's closure, by file:line, and
#: what covers it - "select" (the factories' targets above) or "configs"; mem0 2.2.0 has exactly the two M31 d1
#: recorded: utils/factory.py:31 (import_module of a factory's class path) and vector_stores/configs.py:49 (__import__
#: of a provider's config). An unexplained one is a problem; so is an explanation of a line that is no such import.
M31_FACTORIES = {arm: {"file": "mem0/utils/factory.py",
                       "select": {"LlmFactory": (llm,), "EmbedderFactory": ("ollama",), "VectorStoreFactory": ("qdrant",)},
                       "configs": ("mem0.configs.vector_stores.qdrant",),
                       "explains": {"mem0/utils/factory.py:31": "select", "mem0/vector_stores/configs.py:49": "configs"}}
                 for arm, llm in (("mem0", "deepseek"), ("mem0-store", "ollama"))}
OPTIONAL_EXC = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})
_RECORD = re.compile(r"[^/]+\.dist-info/RECORD")


class M31Refused(RuntimeError):
    """A download M31 cannot judge - named, no verdict written."""


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def _li():
    return _load("v3_lock_install_for_m31", HERE / "lock_install.py")


def _catches_import(handlers) -> bool:
    for h in handlers:
        if h.type is None:
            return True
        for n in (h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]):
            if isinstance(n, ast.Name) and n.id in OPTIONAL_EXC:
                return True
    return False


def imports_of(src: bytes, fname: str) -> tuple[list, list]:
    """(sites, unresolved): each import once - {module, names, level, line, lazy (inside a function), optional (in the
    body of a try that catches an import error)}; a non-literal import_module/__import__ is unresolved, by line."""
    try:
        tree = ast.parse(src.decode("utf-8"), fname)
    except (UnicodeDecodeError, SyntaxError) as e:
        return [], [{"line": 0, "why": f"does not parse ({type(e).__name__})"}]
    sites, unresolved = [], []

    def walk(node, lazy: bool, optional: bool) -> None:
        if isinstance(node, ast.Import):
            for a in node.names:
                sites.append({"module": a.name, "names": [], "level": 0, "line": node.lineno, "lazy": lazy,
                              "optional": optional})
        elif isinstance(node, ast.ImportFrom):
            sites.append({"module": node.module or "", "names": [a.name for a in node.names], "level": node.level,
                          "line": node.lineno, "lazy": lazy, "optional": optional})
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
            if name in ("import_module", "__import__"):
                a0 = node.args[0] if node.args else None
                if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                    sites.append({"module": a0.value, "names": [], "level": 0, "line": node.lineno, "lazy": lazy,
                                  "optional": optional})
                else:
                    unresolved.append({"line": node.lineno, "why": f"{name} with a non-literal argument"})
        lazy = lazy or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        if isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            for part in node.body:
                walk(part, lazy, optional or _catches_import(node.handlers))
            for part in node.handlers + node.orelse + node.finalbody:
                walk(part, lazy, optional)
            return
        for c in ast.iter_child_nodes(node):
            walk(c, lazy, optional)

    walk(tree, False, False)
    return sites, unresolved


class Wheels:
    """The download's wheels, each checked against the lock's sha256 first, read in memory: which distribution's RECORD
    installs each top-level name, and a module's source by its dotted name."""

    def __init__(self, wheel_dir: Path, lock: list[dict]):
        moved = _li().verify_wheels(Path(wheel_dir), lock)
        if moved:
            raise M31Refused(f"the wheels moved since the download: {moved}")
        self.zips: dict = {}
        self.where: dict = {}                       # a RECORD path -> its wheel's file name
        self.provides: dict = {}                    # a top-level name -> {distributions}
        self.requested = {e["name"] for e in lock if e.get("requested")}
        for e in lock:
            fn = e["filename"]
            z = zipfile.ZipFile(io.BytesIO((Path(wheel_dir) / fn).read_bytes()))
            recs = [n for n in z.namelist() if _RECORD.fullmatch(n)]
            if len(recs) != 1:
                raise M31Refused(f"{fn}: {len(recs)} RECORD files in the wheel, not one")
            self.zips[fn] = z
            for row in csv.reader(io.StringIO(z.read(recs[0]).decode("utf-8"))):
                if not row or not row[0] or row[0].startswith(("/", "..")):
                    continue
                path = row[0]
                first = path.split("/")[0]
                if first.endswith((".dist-info", ".data")):
                    continue
                self.where[path] = fn
                top = first[:-3] if first.endswith(".py") else first.split(".")[0] if "." in first else first
                self.provides.setdefault(top, set()).add(e["name"])

    def product_tops(self) -> set:
        return {t for t, d in self.provides.items() if d & self.requested}

    def source(self, dotted: str):
        """(its RECORD path, its bytes, it is a package) - None for a module no wheel ships as source."""
        base = dotted.replace(".", "/")
        for path, pkg in ((f"{base}.py", False), (f"{base}/__init__.py", True)):
            if path in self.where:
                return path, self.zips[self.where[path]].read(path), pkg
        return None

    def is_module(self, dotted: str) -> bool:
        base = dotted.replace(".", "/")
        return any(p == f"{base}.py" or p.startswith(f"{base}/") or (p.startswith(f"{base}.") and p.endswith((".pyd", ".so")))
                   for p in self.where)


def _target(site: dict, current: str, is_pkg: bool) -> str:
    if not site["level"]:
        return site["module"]
    parts = current.split(".") if is_pkg else current.split(".")[:-1]
    parts = parts[:len(parts) - (site["level"] - 1)] if site["level"] > 1 else parts
    return ".".join(parts + ([site["module"]] if site["module"] else []))


def factory_target(wh: Wheels, fac_file: str, cls: str, key: str) -> str | None:
    """The module a factory class's provider_to_class maps ``key`` to, in the product's wheel - None if not found."""
    if fac_file not in wh.where:
        return None
    tree = ast.parse(wh.zips[wh.where[fac_file]].read(fac_file).decode("utf-8"), fac_file)
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == cls):
            continue
        for st in node.body:
            if isinstance(st, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "provider_to_class" for t in st.targets) \
                    and isinstance(st.value, ast.Dict):
                for k, v in zip(st.value.keys, st.value.values):
                    if isinstance(k, ast.Constant) and k.value == key:
                        v = v.elts[0] if isinstance(v, ast.Tuple) and v.elts else v
                        if isinstance(v, ast.Constant) and isinstance(v.value, str) and "." in v.value:
                            return v.value.rsplit(".", 1)[0]
    return None


def load_arm_sources(arms) -> dict:
    """{arm: {"adapter": (file name, bytes), "beside": {name: bytes}}} from run_v3_plan's own tables (ARMS, CODE_SOURCES)."""
    PL = _load("v3_run_plan_for_m31", HERE / "run_v3_plan.py")
    out = {}
    for arm in arms:
        spec = PL.ARMS[arm]
        out[arm] = {"adapter": (spec.adapter, (PL.ARMS_DIR / spec.adapter).read_bytes()),
                    "beside": {n: Path(PL.CODE_SOURCES[n]).read_bytes() for n in spec.code}}
    return out


def run_m31(*, venv_name: str, run: str, runs_root: Path, stdlib, stdlib_source: str = "given", arms_sources=None,
            factories=None, venv_arms=None) -> dict:
    """M31 of download ``run`` of ``venv_name``: parts 1-3, written to m31.json beside the download record and returned.
    Refused (M31Refused, nothing written) for a venv with no product arms, a missing or unclean download, a record whose
    lock is not its lock_sha256, or a wheel that moved or has no RECORD."""
    venv_arms = VENV_ARMS if venv_arms is None else venv_arms
    factories = M31_FACTORIES if factories is None else factories
    arms = venv_arms.get(venv_name)
    if not arms:
        raise M31Refused(f"{venv_name}: no product arms declared (VENV_ARMS) - no M31 (a venv that is no product has none)")
    window = WINDOW_PREFIX + venv_name
    base_dir = Path(runs_root) / "_install" / window / run
    drp = base_dir / DOWNLOAD_RECORD
    if not drp.is_file():
        raise M31Refused(f"{window} {run}: no download record")
    raw = drp.read_bytes()
    dr = json.loads(raw)
    if dr.get("window") != window or dr.get("run") != run or dr.get("phase") != "download":
        raise M31Refused(f"{window} {run}: {DOWNLOAD_RECORD} is not this window run's download record")
    if dr.get("problems") != []:
        raise M31Refused(f"{window} {run}: the download has problems: {dr.get('problems')}")
    lock = dr.get("lock") or []
    if not lock or _li().lock_sha256(lock) != dr.get("lock_sha256"):
        raise M31Refused(f"{window} {run}: the record's lock is not its lock_sha256")
    wh = Wheels(Path(dr.get("wheel_dir") or ""), lock)
    stdlib = frozenset(stdlib)
    sources = arms_sources if arms_sources is not None else load_arm_sources(arms)
    products = wh.product_tops()
    part1, part3, unresolved, problems, entries, closure_unres = [], [], [], [], {}, []
    for arm in arms:
        src = sources[arm]
        beside = {Path(n).stem for n in src["beside"]}
        files = [(src["adapter"][0], src["adapter"][1], True)] + [(n, b, False) for n, b in sorted(src["beside"].items())]
        for fname, data, is_adapter in files:
            sites, unres = imports_of(data, fname)
            unresolved += [{"where": f"{fname}:{u['line']}", "why": u["why"]} for u in unres]
            # the auditor's condition to K2: our own code is checkable - a non-literal or relative import in the
            # adapter or a file beside it is a problem, not only unresolved (in the product's closure it stays a record)
            problems += [f"part 1 {arm}: {fname}:{u['line']} {u['why']} - our adapter's imports are literal and "
                         "absolute, or M31 cannot check them" for u in unres]
            for s in sites:
                if s["level"]:
                    unresolved.append({"where": f"{fname}:{s['line']}", "why": f"a relative import (level {s['level']})"})
                    problems.append(f"part 1 {arm}: {fname}:{s['line']} a relative import (level {s['level']}) - our "
                                    "adapter's imports are literal and absolute, or M31 cannot check them")
                    continue
                top = s["module"].split(".")[0]
                if top in stdlib or top in beside or top == "__future__":
                    continue
                part1.append({"arm": arm, "name": top, "module": s["module"], "where": f"{fname}:{s['line']}",
                              "lazy": s["lazy"], "optional": s["optional"],
                              "blocking": not s["optional"] if is_adapter else not (s["lazy"] or s["optional"]),
                              "provided_by": sorted(wh.provides.get(top, ()))})
                if is_adapter and top in products:
                    for t in [s["module"]] + [f"{s['module']}.{n}" for n in s["names"]]:
                        if wh.is_module(t):
                            entries.setdefault(t, f"{arm}: {fname}:{s['line']}")
        fac = factories.get(arm)
        for cls, keys in sorted((fac or {}).get("select", {}).items()):
            for key in keys:
                mod = factory_target(wh, fac["file"], cls, key)
                if mod is None or not wh.is_module(mod):
                    problems.append(f"part 3 {arm}: {cls}[{key!r}] not found in {fac['file']} of the download's wheels "
                                    f"(M31_FACTORIES is fixed through a gate)")
                else:
                    entries.setdefault(mod, f"{arm}: {cls}[{key!r}]")
        for mod in (fac or {}).get("configs", ()):         # B-M31-CFG: a selected provider's config module
            if not wh.is_module(mod):
                problems.append(f"part 3 {arm}: the config module {mod} is not in the download's wheels (M31_FACTORIES "
                                "is fixed through a gate)")
            else:
                entries.setdefault(mod, f"{arm}: config {mod}")
    queue, seen = list(entries), set()
    while queue:
        dotted = queue.pop(0)
        if dotted in seen:
            continue
        seen.add(dotted)
        parts = dotted.split(".")
        queue += [".".join(parts[:i]) for i in range(1, len(parts)) if ".".join(parts[:i]) not in seen]
        got = wh.source(dotted)
        if got is None:
            continue
        path, data, is_pkg = got
        sites, unres = imports_of(data, path)
        unresolved += [{"where": f"{path}:{u['line']}", "why": u["why"]} for u in unres]
        closure_unres += [{"where": f"{path}:{u['line']}", "why": u["why"]} for u in unres]
        entry = dotted in entries
        for s in sites:
            target = _target(s, dotted, is_pkg)
            top = target.split(".")[0]
            if top in products:
                queue += [t for t in [target] + [f"{target}.{n}" for n in s["names"]] if wh.is_module(t) and t not in seen]
                continue
            if not top or top in stdlib or top == "__future__":
                continue
            part3.append({"module_of": dotted, "entry": entries.get(dotted), "name": top, "module": target,
                          "where": f"{path}:{s['line']}", "lazy": s["lazy"], "optional": s["optional"],
                          "blocking": entry or not (s["lazy"] or s["optional"]),
                          "provided_by": sorted(wh.provides.get(top, ()))})
    # R-M31-UNRES: the closure's unresolved imports against what the arms declare explains them
    explained: dict = {}
    for arm in arms:
        fac = factories.get(arm) or {}
        for where, kind in sorted((fac.get("explains") or {}).items()):
            if kind not in ("select", "configs") or not fac.get(kind):
                problems.append(f"part 3 {arm}: M31_FACTORIES explains {where} by {kind!r}, which the arm does not "
                                "declare - it explains nothing (R-M31-UNRES)")
            else:
                explained.setdefault(where, kind)
    found = {u["where"]: u["why"] for u in closure_unres}
    problems += [f"part 3: {where} {why} in the product's closure - no M31_FACTORIES record explains it (R-M31-UNRES; "
                 "declared before the install, fixed through a gate)" for where, why in sorted(found.items())
                 if where not in explained]
    problems += [f"part 3: M31_FACTORIES explains {where}, which is no unresolved import of the download's closure "
                 "(R-M31-UNRES; the declaration is exact)" for where in sorted(set(explained) - set(found))]
    sites = part1 + part3
    unprovided = sorted({s["name"] for s in sites if s["blocking"] and not s["provided_by"]})
    lazy_unprovided = sorted({s["name"] for s in sites if not s["blocking"] and not s["provided_by"]} - set(unprovided))
    stdlib_list = json.dumps(sorted(stdlib)).encode("utf-8")
    record = {"venv": venv_name, "window": window, "run": run, "arms": list(arms),
              "download_record_sha256": hashlib.sha256(raw).hexdigest(), "lock_sha256": dr["lock_sha256"],
              "stdlib": {"source": stdlib_source, "count": len(stdlib), "sha256": hashlib.sha256(stdlib_list).hexdigest()},
              "factories": {a: factories[a] for a in arms if a in factories}, "entries": entries,
              "parts": {"1": part1, "2": {s["name"]: s["provided_by"] for s in sorted(sites, key=lambda x: x["name"])},
                        "3": part3},
              "closure": sorted(seen), "unresolved": unresolved,
              "explained": {w: k for w, k in sorted(explained.items()) if w in found}, "unprovided": unprovided,
              "lazy_unprovided": lazy_unprovided, "problems": problems,
              "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (base_dir / "m31.json").write_bytes((json.dumps(record, indent=1, sort_keys=True, default=list) + "\n").encode("utf-8"))
    return record


STDLIB_PROBE = "import json, sys; print(json.dumps({'python': sys.version.split()[0], 'names': sorted(sys.stdlib_module_names)}))"


def base_stdlib(c, L, *, python: Path, window: str, run: str, parent_env) -> tuple[list, str]:
    """Q-SPLIT-2 = O-a: the base interpreter's own standard library names - asked under the contract, isolated and
    without bytecode (install_v3_data._step, its own boundary check); a failed or unclean step refuses."""
    IV = _li()._iv()
    rc, out, err, chk = IV._step(c, L, stand=f"_install.{window}", run=run, arm="m31",
                                 argv=[os.fspath(python), "-I", "-B", "-c", STDLIB_PROBE], path_dirs=[Path(python).parent],
                                 parent_env=parent_env, native=None, fs=None, check_id=f"m31-{window}-{run}-stdlib")
    probs = IV.check_problems("m31", IV.check_summary(chk))
    if rc != 0 or probs:
        raise M31Refused(f"the base's standard library could not be read (exit {rc}): {probs or err[-200:]!r}")
    got = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
    return got["names"], f"sys.stdlib_module_names of the base {python} (Python {got['python']})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M31: a product download's wheels against its adapter (R-M31-SCOPE (i))")
    ap.add_argument("--venv", required=True, choices=sorted(VENV_ARMS))
    ap.add_argument("--run", required=True, help="the download's run label (lock_install --download-only)")
    args = ap.parse_args(argv)
    LI = _li()
    L = _load("v3_launch", HERE / "launch.py")
    c = L.Contract.default()
    py = LI.base_python(c, LI.VENVS[args.venv]["base"], LI._fpb().BASES)
    try:
        names, source = base_stdlib(c, L, python=py, window=WINDOW_PREFIX + args.venv, run=args.run,
                                    parent_env=dict(os.environ))
        rec = run_m31(venv_name=args.venv, run=args.run, runs_root=c.runs_root, stdlib=names, stdlib_source=source)
    except M31Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: rec[k] for k in ("venv", "run", "download_record_sha256", "unprovided", "lazy_unprovided",
                                          "problems")}, indent=1))
    return 0 if not rec["unprovided"] and not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
