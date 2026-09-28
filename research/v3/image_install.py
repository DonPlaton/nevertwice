#!/usr/bin/env python3
"""PREREG-V3 A8 C4b (the auditor's Q-C4-1..5): an arm's container image by digest - letta, FalkorDB - loaded into the
owner's Docker store from verified blobs, with no pull, no login and no setting changed.

1. Read-only, before anything: ``docker version`` and ``docker info`` (recorded: the server version, its Os and Arch,
   DockerRootDir, the storage driver and whether the containerd snapshotter is on), and the image set. The engine must
   say linux/amd64, else it is refused by name before the window (C4B-2: the default pipe follows Docker Desktop's
   engine mode, and a Windows-containers engine would take the image into the wrong store). Every docker call is a
   contract spawn of the system docker CLI with DOCKER_CONFIG = an empty directory in its unit's HOME - never its cwd,
   which the spawn requires empty (B-C4B-CWD) - so no credential and no config of the owner's is read, and no
   DOCKER_HOST (the allowlist never passes one).
2. ONE declared window ``a8-docker-<name>`` - hosts exactly the registry, its token service and the declared CDN
   host(s) (fetch_manifest.json) - running the fetch child's OCI job (research/v3/fetch_child.py, oci_job): the newest
   release by the numbers, its one linux/amd64 manifest, config and layers checked on the disk as they stream. Every
   host the job reached must have been tunnelled by the catcher.
3. After the window, offline: every blob re-read from the disk against its digest and size; the ref
   ``nvt3/<repo>:<manifest digest's first 12>`` must be ABSENT from the store - absent means exit 1 with "No such
   image" on stderr, anything else is "could not be read" (C4B-3); the headroom rule on the volume that
   holds Docker's data (C: when unknown): free - 3 x the layers' compressed size >= 10 GiB, else blocked:disk:<name>
   for the owner; an OCI image-layout tar of the PLATFORM manifest, built on D: from the verified blobs only.
4. ``docker load -i`` that tar, then store-independent checks on ``docker image inspect <ref>``: RootFS.Layers ==
   the config's rootfs.diff_ids, every key of the config's "config" equal in .Config, and .Id - the config digest on
   the classic store, the manifest digest on the containerd store; which one it is, is recorded. The image set after
   the load must be the set before plus exactly the new image. From a successful load on, the record names the loaded
   ref (loaded_ref), so a later problem - a dirty boundary check on the load included - leaves it named.

The record (<runs>/_install/a8-docker-<name>/<run>/image_record.json) carries all of it, the OCI job's summary (no
token, no signed query), the tar's sha256, and every problem by name; a problem stops what comes after it. Removing
the image later does not give C: its space back unless the VHD is compacted - a line for E5 (Q-C4-5).

    python research/v3/image_install.py --image letta --run d1
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
WINDOW_PREFIX = "a8-docker-"
REGISTRY, AUTH, SERVICE = "registry-1.docker.io", "auth.docker.io", "registry.docker.io"
CDN_HOSTS = ("production.cloudflare.docker.com",)
IMAGES = {"letta": {"repo": "letta/letta"}, "falkordb": {"repo": "falkordb/falkordb"}}
DOCKER = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
HEADROOM = 10 * (1 << 30)
LAYER_MULTIPLE = 3
META_MAX = 16 * (1 << 20)
BLOB_MAX = 8 * (1 << 30)
DISK_FLOOR = 100 * (1 << 30)                     # the fetch window's floor on D: (fetch_manifest.json "disk")
MAN_TYPES = ("application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json")


class ImageRefused(RuntimeError):
    """An image, a store or a check this module's rules do not allow - named, never worked around."""


def _iv():
    mod = sys.modules.get("v3_install_v3_data_for_image")
    if mod is None:
        spec = importlib.util.spec_from_file_location("v3_install_v3_data_for_image", HERE / "install_v3_data.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["v3_install_v3_data_for_image"] = mod
        spec.loader.exec_module(mod)
    return mod


def oci_job(name: str) -> dict:
    """The window's fetch-child job for a declared image (fetch_child.oci_job)."""
    repo = IMAGES[name]["repo"]
    return {"kind": "oci", "repo": repo, "registry": REGISTRY, "auth": AUTH, "service": SERVICE,
            "cdn_hosts": list(CDN_HOSTS), "hosts": [REGISTRY, AUTH, *CDN_HOSTS],
            "platform": {"os": "linux", "architecture": "amd64"}, "max_meta_bytes": META_MAX, "max_blob_bytes": BLOB_MAX}


def ref_for(repo: str, manifest_digest: str) -> str:
    return f"nvt3/{repo}:{manifest_digest.split(':', 1)[1][:12]}"


def verify_blobs(blob_dir: Path, summary: dict) -> list[str]:
    """Every blob the image needs, re-read from the disk: the platform manifest (its bytes), the config and each layer
    (their digests and sizes)."""
    want = [(summary["manifest_digest"], summary.get("manifest_bytes")), (summary["config"]["digest"], summary["config"]["size"]),
            *[(x["digest"], x["size"]) for x in summary["layers"]]]
    problems = []
    for digest, size in want:
        f = blob_dir / digest.split(":", 1)[1]
        if not f.is_file():
            problems.append(f"the blob {digest[:19]} is not on the disk")
            continue
        data = f.read_bytes()
        if "sha256:" + hashlib.sha256(data).hexdigest() != digest or (size is not None and len(data) != size):
            problems.append(f"the blob {digest[:19]} on the disk is not its digest's bytes or its size")
    return problems


def headroom(free_bytes: int, layers: list[dict]) -> tuple[bool, int]:
    """Q-C4-5: (ok, what is left) - the volume's free space minus 3 x the layers' compressed size must stay >= 10 GiB."""
    left = int(free_bytes) - LAYER_MULTIPLE * sum(int(x["size"]) for x in layers)
    return left >= HEADROOM, left


def build_layout(blob_dir: Path, summary: dict, ref: str, dest: Path) -> str:
    """An OCI image-layout tar of the PLATFORM manifest (never the whole index), from the verified blobs only; returns
    its sha256."""
    mdig = summary["manifest_digest"]
    mbytes = (blob_dir / mdig.split(":", 1)[1]).read_bytes()
    index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
             "manifests": [{"mediaType": summary.get("manifest_media_type") or MAN_TYPES[0], "digest": mdig,
                            "size": len(mbytes), "annotations": {"io.containerd.image.name": f"docker.io/{ref}",
                                                                 "org.opencontainers.image.ref.name": ref}}]}
    members = [("oci-layout", json.dumps({"imageLayoutVersion": "1.0.0"}).encode()),
               ("index.json", json.dumps(index, sort_keys=True).encode())]
    for d in [mdig, summary["config"]["digest"], *[x["digest"] for x in summary["layers"]]]:
        members.append((f"blobs/sha256/{d.split(':', 1)[1]}", blob_dir / d.split(":", 1)[1]))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w", format=tarfile.PAX_FORMAT) as tar:
        for name, src in members:
            info = tarfile.TarInfo(name)
            info.mtime, info.mode = 0, 0o644
            if isinstance(src, bytes):
                info.size = len(src)
                tar.addfile(info, io.BytesIO(src))
            else:
                info.size = src.stat().st_size
                with open(src, "rb") as f:
                    tar.addfile(info, f)
    h = hashlib.sha256()
    with open(dest, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def post_load(inspect: dict, config: dict, summary: dict) -> tuple[list[str], str | None]:
    """The store-independent checks (Q-C4-4): (problems, "classic" | "containerd" | None)."""
    problems = []
    diff_ids = (config.get("rootfs") or {}).get("diff_ids")
    if (inspect.get("RootFS") or {}).get("Layers") != diff_ids:
        problems.append("the loaded image's RootFS.Layers are not the config's rootfs.diff_ids")
    got = inspect.get("Config") or {}
    wrong = sorted(k for k, v in (config.get("config") or {}).items() if got.get(k) != v)
    if wrong:
        problems.append(f"the loaded image's Config differs from the config's in {wrong}")
    store = {summary["config"]["digest"]: "classic", summary["manifest_digest"]: "containerd"}.get(inspect.get("Id"))
    if store is None:
        problems.append(f"the loaded image's Id {str(inspect.get('Id'))[:19]} is neither the config's nor the manifest's digest")
    return problems, store


def _docker_step(c, L, *, docker_exe: Path, argv: list[str], stand: str, run: str, arm: str, parent_env, native, fs,
                 check_id: str) -> tuple[int | None, bytes, bytes, dict]:
    """One docker CLI call: a contract spawn under its own boundary check, DOCKER_CONFIG an empty directory in its unit,
    no proxy variable, no DOCKER_HOST."""
    unit = L.make_unit_dirs(c, stand, run, arm, arm[0] + "1")
    cfg = Path(unit.home) / "docker_config_empty"   # B-C4B-CWD: the cwd must stay empty for the spawn's check_cwd
    cfg.mkdir(parents=True, exist_ok=True)
    env = L.build_env(c, parent_env=parent_env, unit=unit, path_dirs=[docker_exe.parent],
                      declared={"DOCKER_CONFIG": os.fspath(cfg)}, catcher_url="", proxies=False)
    W = L.Witnesses(c, native=native if native is not None else L.NativeEgressWitness(),
                    fs=fs if fs is not None else L.FsWitness(L.watched_set(c)))
    W.begin_check(check_id)
    try:
        child = L.spawn(c, [os.fspath(docker_exe), *argv], env=env, cwd=unit.cwd,
                        record={"role": "install", "stand": None, "run": run, "arm": arm, "unit": arm[0] + "1"},
                        parent_env=parent_env, catcher_url="", witnesses=W, requirement="required",
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = child.process.communicate(timeout=3600)
            rc = child.process.returncode
        except subprocess.TimeoutExpired:
            child.kill_tree()
            rc, out, err = None, b"", b""
    finally:
        chk = W.end_check(check_id)
    return rc, out, err, chk


def _write(base: Path, record: dict) -> dict:
    record["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (base / "image_record.json").write_bytes(
        (json.dumps(record, indent=1, sort_keys=True, default=list) + "\n").encode("utf-8"))
    return record


def run_image_install(c, L, F, *, name: str, run: str, via_port: int, parent_env, python: Path,
                      docker_exe: Path = DOCKER, docker: Callable | None = None, docker_volume: Path | None = None,
                      free_bytes: Callable[[Path], int] | None = None, native=None, fs=None,
                      child_env_extra: dict | None = None, need_bytes: int = DISK_FLOOR, volume: Path | None = None) -> dict:
    """The whole image (see the module docstring). ``F`` is research/v3/fetch_a3; ``docker`` (argv, arm, check_id) ->
    (rc, stdout, stderr, check) and ``free_bytes`` are the test's; a real run passes neither."""
    if name not in IMAGES:
        raise ImageRefused(f"{name!r} is no declared image ({sorted(IMAGES)})")
    IV = _iv()
    repo, window = IMAGES[name]["repo"], WINDOW_PREFIX + name
    base = c.runs_root / "_install" / window / run
    if base.exists():
        raise ImageRefused("this install run label was used before")
    base.mkdir(parents=True)
    stand = f"_install.{window}"
    free_bytes = free_bytes or (lambda p: shutil.disk_usage(p).free)
    record: dict = {"window": window, "run": run, "image": name, "repo": repo, "problems": []}

    def dk(argv: list[str], arm: str) -> tuple[int | None, str, str]:
        if docker is not None:
            rc, out, err, chk = docker(argv, arm, f"install-{window}-{run}-{arm}")
        else:
            rc, out, err, chk = _docker_step(c, L, docker_exe=docker_exe, argv=argv, stand=stand, run=run, arm=arm,
                                             parent_env=parent_env, native=native, fs=fs,
                                             check_id=f"install-{window}-{run}-{arm}")
        summ = IV.check_summary(chk)
        record.setdefault("docker_calls", []).append({"argv": list(argv), "rc": rc, "check": summ})
        record["problems"] += IV.check_problems(f"docker {arm}", summ)
        text = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out)
        etext = err.decode("utf-8", "replace") if isinstance(err, bytes) else str(err or "")
        return rc, text, etext

    # 1. read only: the daemon's facts and the image set before
    rc, out, _ = dk(["version", "--format", "{{json .}}"], "version")
    rc2, out2, _ = dk(["info", "--format", "{{json .}}"], "info")
    if rc != 0 or rc2 != 0:
        record["problems"].append(f"docker version/info failed ({rc}, {rc2}) - the daemon is not answering")
        return _write(base, record)
    ver, info = json.loads(out), json.loads(out2)
    status = [list(x) for x in info.get("DriverStatus") or []]
    server = ver.get("Server") or {}
    record["docker"] = {"server_version": server.get("Version"), "server_os": server.get("Os"),
                        "server_arch": server.get("Arch"), "docker_root_dir": info.get("DockerRootDir"),
                        "driver": info.get("Driver"), "driver_status": status,
                        "containerd_snapshotter": any(x and x[0] == "driver-type" and "snapshotter" in str(x[-1]) for x in status)}
    if (server.get("Os"), server.get("Arch")) != ("linux", "amd64"):
        record["problems"].append(f"the daemon's engine is {server.get('Os')}/{server.get('Arch')}, not linux/amd64 - "
                                  f"the image would land in another engine (C4B-2); refused before the window")
        return _write(base, record)
    rc, out, _ = dk(["image", "ls", "--all", "--quiet", "--no-trunc"], "ls-before")
    if rc != 0:
        record["problems"].append("docker image ls failed before the window")
        return _write(base, record)
    before = sorted(set(out.split()))
    record["images_before"] = len(before)
    if record["problems"]:                         # a read-only call under a dirty boundary check: no window
        return _write(base, record)
    # 2. the window: the OCI job, through the catcher
    job = oci_job(name)
    rec = F.run_child_window(c, L, window=window, hosts=list(job["hosts"]), jobs=[job], python=python, via_port=via_port,
                             run=run, parent_env=parent_env, native=native, fs=fs, child_env_extra=child_env_extra,
                             need_bytes=need_bytes, volume=volume)
    record["window_record"] = {k: rec[k] for k in ("problems", "check", "issuers")}
    tunnelled = sorted({x["host"] for x in rec["catcher"] if x.get("tunnelled")})
    record["window_record"]["tunnelled_hosts"] = tunnelled
    record["problems"] += list(rec["problems"])
    summary = (rec["jobs"][0]["summary"] or [{}])[0] if rec.get("jobs") else {}
    record["oci"] = summary
    reached = sorted({r["host"] for r in summary.get("requests") or []})
    past = [h for h in reached if h not in tunnelled]
    if past:
        record["problems"].append(f"the OCI job reached {past} with no catcher tunnel - past the catcher")
    if record["problems"] or not summary.get("ok"):
        if not summary.get("ok"):
            record["problems"].append(f"the OCI job failed: {summary.get('error')}")
        return _write(base, record)
    # 3. offline: the blobs from the disk, the ref's absence, the headroom, the layout tar on D:
    blob_dir = Path(rec["jobs"][0]["unit"]) / "oci" / "blobs" / "sha256"
    record["problems"] += verify_blobs(blob_dir, summary)
    ref = ref_for(repo, summary["manifest_digest"])
    record["ref"] = ref
    if record["problems"]:
        return _write(base, record)
    rc, _, err = dk(["image", "inspect", ref], "absent")
    if rc == 0:
        record["problems"].append(f"the ref {ref} is already in the store - refused, nothing loaded (Q-C4-4)")
    elif rc != 1 or "No such image" not in err:    # C4B-3: a timeout or a daemon error says nothing about the ref
        record["problems"].append(f"the ref {ref}'s absence could not be read (docker image inspect exit {rc}) - "
                                  f"nothing loaded (Q-C4-4)")
    if record["problems"]:
        return _write(base, record)
    vol = docker_volume or Path("C:\\")
    ok, left = headroom(free_bytes(vol), summary["layers"])
    record["headroom"] = {"volume": str(vol), "left_after_bytes": left, "floor_bytes": HEADROOM, "ok": ok}
    if not ok:
        record["problems"].append(f"blocked:disk:{name} - {vol} would keep {left >> 30} GiB after the load, under "
                                  f"{HEADROOM >> 30} GiB (Q-C4-5): an owner item")
        return _write(base, record)
    tar_path = base / f"{name}.oci.tar"
    record["layout_sha256"] = build_layout(blob_dir, summary, ref, tar_path)
    # 4. the load and the store-independent checks
    rc, out, _ = dk(["load", "--input", os.fspath(tar_path)], "load")
    record["load_output_tail"] = out[-400:]
    if rc != 0:
        record["problems"].append(f"docker load failed (exit {rc})")
        return _write(base, record)
    record["loaded_ref"] = ref                     # D-C4b-2: named from here on, whatever comes after
    rc, out, _ = dk(["image", "inspect", ref], "inspect")
    if rc != 0:
        record["problems"].append(f"the loaded ref {ref} cannot be inspected")
        return _write(base, record)
    inspect = (json.loads(out) or [{}])[0]
    config = json.loads((blob_dir / summary["config"]["digest"].split(":", 1)[1]).read_bytes())
    probs, store = post_load(inspect, config, summary)
    record["problems"] += probs
    record["store"], record["image_id"] = store, inspect.get("Id")
    rc, out, _ = dk(["image", "ls", "--all", "--quiet", "--no-trunc"], "ls-after")
    after = sorted(set(out.split())) if rc == 0 else None
    new = sorted(set(after or []) - set(before))
    record["images_after"], record["images_new"] = (len(after) if after is not None else None), new
    if after is None or set(before) - set(after) or new != [inspect.get("Id")]:
        record["problems"].append(f"the image set after the load is not the set before plus exactly the new image: "
                                  f"new {new}, lost {sorted(set(before) - set(after or []))[:3]}")
    return _write(base, record)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A8: an arm's container image by digest (Q-C4-1..5)")
    ap.add_argument("--image", required=True, choices=sorted(IMAGES))
    ap.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    L = _load("v3_launch", HERE / "launch.py")
    F = _load("v3_fetch_a3", HERE / "fetch_a3.py")
    c = L.Contract.default()
    port = L.network_via_port(c)
    if port is None:
        print("no declared hop: <runs>\\_config\\network.json is missing", file=sys.stderr)
        return 2
    rec = run_image_install(c, L, F, name=args.image, run=args.run, via_port=port, parent_env=dict(os.environ),
                            python=c.polygon_root / "py314" / "python.exe")
    print(json.dumps({k: rec.get(k) for k in ("window", "ref", "store", "image_id", "headroom", "problems")}, indent=1))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
