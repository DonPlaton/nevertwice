#!/usr/bin/env python3
"""PREREG-V3 A8 B-NLP NLP-2b (the auditor's rule, 07:19; Q-NLP-1 = O-a): the spaCy model mem0's pinned source checks
for, fetched in ONE declared window and installed offline into mem0_v3 - so mem0 never downloads it at run time (an
egress the catcher would stop) and never runs with spaCy off (§3.4).

1. Before anything, offline: the mem0_v3 install record (lock_install) must hold no problem; spaCy's LOCKED version is
   its lock's; pip's own args are its record's; the model's NAME is the installed mem0's own source fact
   (probe_a8.nlp_facts: m0_nlp_model, declared before any read of 2.2.0) - a blocked fact refuses, to the auditor.
2. ONE window ``a8-spacy-model`` - hosts exactly raw.githubusercontent.com, api.github.com, github.com and the two GitHub
   CDN hosts (fetch_manifest.json) - running fetch_child's gh_model job: the version compatibility.json lists first
   under that spaCy's major.minor, which must be its newest; the release's one wheel asset, checked against its digest
   as it streams. Every host the job reached must have been tunnelled by the catcher.
3. After the window, offline: the file re-read from the disk against the job's sha256 and the asset's size (and the
   digest when there is one); pip ``--require-hashes --no-deps --no-index --find-links <the model's directory>`` with a
   one-line requirement ``<model>==<version> --hash=sha256:<sha>`` and lock_install's declared offline environment;
   then, isolated, spacy.util.is_package(<model>) and importlib.metadata's version of it. Each is a contract step with
   its own boundary check (install_v3_data._step).

The record: <runs>/_install/a8-spacy-model/<run>/model_record.json.

    python research/v3/model_install.py --run m1 --install-run <the mem0_v3 install's run>
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
WINDOW = "a8-spacy-model"
RAW, API, WEB = "raw.githubusercontent.com", "api.github.com", "github.com"
CDN_HOSTS = ("objects.githubusercontent.com", "release-assets.githubusercontent.com")
REPO = "explosion/spacy-models"
COMPAT_PATH = "/explosion/spacy-models/master/compatibility.json"
VENV = "mem0_v3"
META_MAX = 16 * (1 << 20)
ASSET_MAX = 512 * (1 << 20)
DISK_FLOOR = 100 * (1 << 30)
CHECK_CODE = ("import importlib.metadata as md, json, sys, spacy.util\n"
              "m = sys.argv[1]\n"
              "print(json.dumps({'is_package': bool(spacy.util.is_package(m)), 'version': md.version(m)}))\n")


class ModelRefused(RuntimeError):
    """A record, a fact or a check this module's rules do not allow - named, never worked around."""


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def gh_job(model: str, spacy: str) -> dict:
    """The window's fetch-child job (fetch_child.gh_model_job)."""
    return {"kind": "gh_model", "model": model, "spacy": spacy, "repo": REPO, "raw_host": RAW, "api_host": API,
            "web_host": WEB, "compat_path": COMPAT_PATH, "cdn_hosts": list(CDN_HOSTS), "hosts": [RAW, API, WEB, *CDN_HOSTS],
            "max_meta_bytes": META_MAX, "max_asset_bytes": ASSET_MAX}


def default_step(c, L, *, stand: str, run: str, venv: Path, parent_env, native, fs) -> Callable:
    """The real offline step: install_v3_data._step, a contract spawn under its own boundary check."""
    IV = _load("v3_install_v3_data_for_model", HERE / "install_v3_data.py")

    def step(argv: list[str], arm: str, declared: dict | None):
        return IV._step(c, L, stand=stand, run=run, arm=arm, argv=argv, path_dirs=[Path(argv[0]).parent],
                        parent_env=parent_env, native=native, fs=fs, check_id=f"install-{WINDOW}-{run}-{arm}",
                        declared=declared)
    return step


def _write(base: Path, record: dict) -> dict:
    record["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (base / "model_record.json").write_bytes((json.dumps(record, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return record


def run_model_install(c, L, F, *, run: str, install_run: str, venv: Path, via_port: int, parent_env, python: Path,
                      step: Callable | None = None, native=None, fs=None, child_env_extra: dict | None = None,
                      need_bytes: int = DISK_FLOOR, volume: Path | None = None) -> dict:
    """The whole model (see the module docstring). ``F`` is research/v3/fetch_a3; ``step`` (argv, arm, declared) ->
    (rc, stdout, stderr, check) is the test's; a real run passes none."""
    LI = _load("v3_lock_install_for_model", HERE / "lock_install.py")
    PA = _load("v3_probe_a8_for_model", HERE / "probe_a8.py")
    IV = _load("v3_install_v3_data_for_model", HERE / "install_v3_data.py")
    # 1. offline, before anything: the mem0_v3 install, its locked spaCy, the model's name from mem0's own source
    irec_path = c.runs_root / "_install" / f"a8-pypi-{VENV}" / install_run / "install_record.json"
    if not irec_path.is_file():
        raise ModelRefused(f"no {VENV} install record for run {install_run!r} - the venv comes first")
    irec = json.loads(irec_path.read_bytes())
    if irec.get("problems"):
        raise ModelRefused(f"the {VENV} install record has problems: {irec['problems'][:2]}")
    spacy = next((e.get("version") for e in irec.get("lock") or [] if e.get("name") == "spacy"), None)
    if not spacy:
        raise ModelRefused(f"no spacy in the {VENV} lock - the [nlp] extra did not land (B-NLP)")
    site = next((s for s in Path(venv).rglob("site-packages") if s.is_dir()), None)
    if site is None:
        raise ModelRefused(f"{venv} has no site-packages")
    facts = PA.nlp_facts(site)
    mf = facts["m0_nlp_model"]
    if mf.get("blocked"):
        raise ModelRefused(f"the model's name is not a source fact: {mf['blocked']} - to the auditor")
    model = mf["value"]
    base = c.runs_root / "_install" / WINDOW / run
    if base.exists():
        raise ModelRefused("this model install run label was used before")
    base.mkdir(parents=True)
    record: dict = {"window": WINDOW, "run": run, "install_run": install_run, "venv": str(venv), "spacy": spacy,
                    "model": model, "model_source": mf["source"], "problems": []}
    venv_py = IV.venv_python(Path(venv))
    step = step or default_step(c, L, stand=f"_install.{WINDOW}", run=run, venv=Path(venv), parent_env=parent_env,
                                native=native, fs=fs)
    # 2. the window: the gh_model job, through the catcher
    job = gh_job(model, spacy)
    rec = F.run_child_window(c, L, window=WINDOW, hosts=list(job["hosts"]), jobs=[job], python=python, via_port=via_port,
                             run=run, parent_env=parent_env, native=native, fs=fs, child_env_extra=child_env_extra,
                             need_bytes=need_bytes, volume=volume)
    tunnelled = sorted({x["host"] for x in rec["catcher"] if x.get("tunnelled")})
    record["window_record"] = {**{k: rec[k] for k in ("problems", "check", "issuers")}, "tunnelled_hosts": tunnelled}
    record["problems"] += list(rec["problems"])
    summary = (rec["jobs"][0]["summary"] or [{}])[0] if rec.get("jobs") else {}
    record["job"] = summary
    past = sorted({r["host"] for r in summary.get("requests") or []} - set(tunnelled))
    if past:
        record["problems"].append(f"the gh_model job reached {past} with no catcher tunnel - past the catcher")
    if not summary.get("ok"):
        record["problems"].append(f"the gh_model job failed: {summary.get('error')}")
    if record["problems"]:
        return _write(base, record)
    # 3. offline: the file from the disk, pip with the one hashed line, the check
    mdir = Path(rec["jobs"][0]["unit"]) / "model"
    f = mdir / summary["asset"]["name"]
    data = f.read_bytes() if f.is_file() else b""
    sha = hashlib.sha256(data).hexdigest()
    digest = summary["asset"].get("digest")
    if sha != summary["file"]["sha256"] or len(data) != summary["asset"]["size"] or (digest and digest != f"sha256:{sha}"):
        record["problems"].append(f"the model file on the disk is not the job's sha256 and size ({len(data)} bytes)")
        return _write(base, record)
    record.update(version=summary["version"], sha256=sha, cdn_host=summary.get("cdn_host"), tls_only=summary.get("tls_only"))
    req = base / "model_requirement.txt"
    req.write_bytes(f"{model}=={summary['version']} --hash=sha256:{sha}\n".encode("utf-8"))
    argv = LI.install_argv(venv_py, list((irec.get("pip") or {}).get("args") or []), req, mdir)
    record["install_argv"] = argv
    rc, out, _, chk = step(argv, "pip", LI.offline_env(c))
    record["install_rc"], record["install_check"] = rc, IV.check_summary(chk)
    record["problems"] += IV.check_problems("pip", record["install_check"])
    if rc != 0:
        record["problems"].append(f"pip's offline install of the model failed (exit {rc})")
    if record["problems"]:
        return _write(base, record)
    rc, out, _, chk = step([os.fspath(venv_py), "-I", "-B", "-c", CHECK_CODE, model], "check", None)
    record["check_rc"], record["check_check"] = rc, IV.check_summary(chk)
    record["problems"] += IV.check_problems("check", record["check_check"])
    try:
        got = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        got = None
    record["check"] = got
    if rc != 0 or not isinstance(got, dict):
        record["problems"].append(f"the model check failed (exit {rc})")
    else:
        if got.get("is_package") is not True:
            record["problems"].append(f"spacy.util.is_package({model!r}) is {got.get('is_package')!r}")
        if got.get("version") != summary["version"]:
            record["problems"].append(f"the installed {model} is {got.get('version')}, not the fetched {summary['version']}")
    return _write(base, record)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A8 B-NLP: the spaCy model mem0's source checks for, into mem0_v3")
    ap.add_argument("--run", required=True)
    ap.add_argument("--install-run", required=True, help="the mem0_v3 lock install's run label")
    args = ap.parse_args(argv)
    L = _load("v3_launch", HERE / "launch.py")
    F = _load("v3_fetch_a3", HERE / "fetch_a3.py")
    c = L.Contract.default()
    port = L.network_via_port(c)
    if port is None:
        print("no declared hop: <runs>\\_config\\network.json is missing", file=sys.stderr)
        return 2
    rec = run_model_install(c, L, F, run=args.run, install_run=args.install_run, venv=c.polygon_root / VENV, via_port=port,
                            parent_env=dict(os.environ), python=c.polygon_root / "py314" / "python.exe")
    print(json.dumps({k: rec.get(k) for k in ("model", "version", "sha256", "cdn_host", "tls_only", "problems")}, indent=1))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
