#!/usr/bin/env python3
"""PREREG-V3 A8 C4b (the auditor's Q-C4-4, Q-C4-5): research/v3/image_install.py - spawns NO child. The docker CLI and
the fetch window are injected (a fake docker with an image store, a fake window that leaves the OCI job's blobs), and
_docker_step runs against a stub launch whose build_env is the real one:

* read only first: docker version and info recorded (server version, DockerRootDir, containerd snapshotter), the
  image set before; a daemon that does not answer stops everything before the window;
* the window: the OCI job for the declared repository, the declared hosts; a host the job reached without a catcher
  tunnel, or a failed job, stops before any docker change;
* offline: every blob re-read from the disk (a changed one stops it); the ref nvt3/<repo>:<digest12> must be absent;
  the headroom rule free - 3 x the layers >= 10 GiB (at the edge it passes, one byte under is blocked:disk:<name>);
  an OCI layout tar of the PLATFORM manifest on D:, from the verified blobs only, with the ref's annotations;
* the load and the store-independent checks: RootFS.Layers == diff_ids, the config's config keys equal, the Id the
  config digest (classic) or the manifest digest (containerd), recorded; the image set after = before + exactly the
  new image - an extra or a lost image is a problem;
* every docker call: DOCKER_CONFIG an empty directory in its unit's HOME (B-C4B-CWD: never its cwd, which the contract's
  spawn refuses when not empty), no DOCKER_HOST even when the parent has one, no proxy variable - and the stub spawn
  runs the REAL L.check_cwd and L.assert_env on what _docker_step hands it (the producer -> consumer seam); the docker
  CLI is the contract's named exception; the manifest declares both image windows exactly as the code does;
* C4B-2: the engine must say linux/amd64 (Server.Os, Server.Arch recorded) before the window; C4B-3: the ref is absent
  only on exit 1 with "No such image" on stderr.

    python tests/_test_v3_image_install.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace

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


II = _load("v3_image_install_t", ROOT / "research" / "v3" / "image_install.py")
L = _load("v3_launch_image_t", ROOT / "research" / "v3" / "launch.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


MAN_T = II.MAN_TYPES[0]
CONFIG = {"architecture": "amd64", "os": "linux", "config": {"Env": ["A=1"], "Cmd": ["./letta"]},
          "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "d" * 64, "sha256:" + "c" * 64]}}
CFG = json.dumps(CONFIG).encode()
L1, L2 = b"layer one" * 100, b"layer two" * 300
MAN = json.dumps({"schemaVersion": 2, "mediaType": MAN_T, "config": {"digest": sha(CFG), "size": len(CFG)},
                  "layers": [{"digest": sha(L1), "size": len(L1)}, {"digest": sha(L2), "size": len(L2)}]}).encode()
SUMMARY = {"ok": True, "kind": "oci", "repo": "letta/letta", "tag": "0.10.0", "index_digest": "sha256:" + "a" * 64,
           "manifest_digest": sha(MAN), "manifest_media_type": MAN_T, "manifest_bytes": len(MAN),
           "config": {"digest": sha(CFG), "size": len(CFG)},
           "layers": [{"digest": sha(L1), "size": len(L1)}, {"digest": sha(L2), "size": len(L2)}],
           "requests": [{"host": II.AUTH}, {"host": II.REGISTRY}, {"host": II.CDN_HOSTS[0]}], "token": {"value": "<redacted>"}}
CLEAN = {"complete": True, "native": {"hits": 0, "loopback_hits": 0}, "fs": {"fs_hits": 0}}
TMP = Path(tempfile.mkdtemp(prefix="nvt3_image_install_"))
REF = II.ref_for("letta/letta", sha(MAN))


class FakeWindow:
    def __init__(self, *, summary=None, tamper=None, untunnelled=None, drop=None, problems=(), same_size=None):
        self.summary, self.tamper, self.untunnelled = summary or dict(SUMMARY), tamper, untunnelled
        self.same_size = same_size
        self.drop, self.problems = drop, list(problems)
        self.seen: dict = {}

    def run_child_window(self, c, L_, **kw):
        self.seen.update(kw)
        unit = Path(c.runs_root) / "_fetch" / kw["window"] / kw["run"] / "j0"
        blobs = unit / "oci" / "blobs" / "sha256"
        blobs.mkdir(parents=True)
        for b in (MAN, CFG, L1, L2):
            (blobs / sha(b).split(":")[1]).write_bytes(b)
        if self.tamper:
            (blobs / sha(self.tamper).split(":")[1]).write_bytes(b"changed on the disk")
        if self.drop:
            (blobs / sha(self.drop).split(":")[1]).unlink()
        if self.same_size:                          # other bytes, the very same length
            (blobs / sha(self.same_size).split(":")[1]).write_bytes(b"x" * len(self.same_size))
        hosts = [h for h in kw["hosts"] if h != self.untunnelled]
        return {"problems": list(self.problems), "check": {"complete": True}, "issuers": [], "catcher": [{"host": h, "tunnelled": True} for h in hosts],
                "jobs": [{"unit": str(unit), "summary": [self.summary]}]}


class FakeDocker:
    """A docker CLI over an image store: {id: [refs]}."""

    def __init__(self, *, store="containerd", ref_present=False, load_rc=0, daemon_rc=0, inspect_patch=None, extra=None,
                 lose=None, absent_rc=None, dirty=(), engine=("linux", "amd64"), absent_err=None, fail_arms=()):
        self.images = {"sha256:" + "1" * 64: ["owner/thing:latest"], "sha256:" + "2" * 64: ["owner/other:1"]}
        if ref_present:
            self.images["sha256:" + "3" * 64] = [REF]
        self.store, self.load_rc, self.daemon_rc = store, load_rc, daemon_rc
        self.inspect_patch, self.extra, self.lose = inspect_patch or {}, extra, lose
        self.absent_rc, self.dirty = absent_rc, set(dirty)
        self.engine, self.absent_err, self.fail_arms = engine, absent_err, set(fail_arms)
        self.calls: list = []
        self.loaded_tar = None

    def __call__(self, argv, arm, check_id):
        rc, out, err, chk = self._call(argv, arm)
        if arm in self.dirty:                      # the call's own boundary check saw an egress hit
            chk = {**chk, "native": {"hits": 1, "loopback_hits": 0}}
        return rc, out, err, chk

    def _call(self, argv, arm):
        self.calls.append(list(argv))
        a = argv[0]
        if arm == "absent" and self.absent_rc is not None:
            return self.absent_rc, b"", self.absent_err or b"", CLEAN
        if arm == "absent" and self.absent_err is not None:
            return 1, b"", self.absent_err, CLEAN
        if arm in self.fail_arms:                  # that call fails with nothing on stdout
            return 1, b"", b"Error response from daemon: something broke", CLEAN
        if a in ("version", "info") and self.daemon_rc:
            return self.daemon_rc, b"", b"cannot connect", CLEAN
        if a == "version":
            return 0, json.dumps({"Client": {"Os": "windows", "Arch": "amd64"},
                                  "Server": {"Version": "29.1.3", "Os": self.engine[0], "Arch": self.engine[1]}}).encode(), b"", CLEAN
        if a == "info":
            return 0, json.dumps({"DockerRootDir": "/var/lib/docker", "Driver": "overlayfs",
                                  "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]}).encode(), b"", CLEAN
        if argv[:2] == ["image", "ls"]:
            return 0, "\n".join(sorted(self.images)).encode(), b"", CLEAN
        if argv[:2] == ["image", "inspect"]:
            iid = next((i for i, refs in self.images.items() if argv[2] in refs), None)
            if iid is None:
                return 1, b"", f"Error response from daemon: No such image: {argv[2]}".encode(), CLEAN
            got = {"Id": iid, "RootFS": {"Type": "layers", "Layers": list(CONFIG["rootfs"]["diff_ids"])},
                   "Config": {**CONFIG["config"], "Labels": None}, **self.inspect_patch}
            return 0, json.dumps([got]).encode(), b"", CLEAN
        if a == "load":
            self.loaded_tar = Path(argv[2])
            if self.load_rc:
                return self.load_rc, b"", b"load failed", CLEAN
            with tarfile.open(self.loaded_tar) as t:
                index = json.loads(t.extractfile("index.json").read())
            ref = index["manifests"][0]["annotations"]["org.opencontainers.image.ref.name"]
            iid = sha(CFG) if self.store == "classic" else sha(MAN)
            self.images[iid] = [ref]
            if self.extra:
                self.images[self.extra] = ["surprise:1"]
            if self.lose:
                self.images.pop(self.lose, None)
            return 0, f"Loaded image: {ref}\n".encode(), b"", CLEAN
        return 2, b"", b"unexpected", CLEAN


def contract(tag):
    base = TMP / tag
    return L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                      owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "q",
                      conservation_root=base / "cv")


BIG = 1 << 50


def run(tag, *, window=None, docker=None, free=BIG, name="letta", volume=Path("C:\\"), free_bytes=None):
    c = contract(tag)
    w, d = window or FakeWindow(), docker or FakeDocker()
    try:
        rec = II.run_image_install(c, L, w, name=name, run="d1", via_port=1, parent_env={}, python=Path("py.exe"),
                                   docker=d, free_bytes=free_bytes or (lambda p: free), docker_volume=volume)
        err = None
    except Exception as e:  # noqa: BLE001 - a refusal is the row's to read
        rec, err = {}, e
    return rec, c, w, d, err


print("- a whole image, the containerd store -")
rec, C, W, D, err = run("ok")
check("the install has no problem", err is None and rec.get("problems") == [], f"{err} {rec.get('problems')}")
check("read only first: docker version and info recorded - the server version, DockerRootDir, the containerd "
      "snapshotter - and the image set before, all ahead of the window", [x[:2] for x in D.calls[:3]]
      == [["version", "--format"], ["info", "--format"], ["image", "ls"]]
      and rec.get("docker", {}).get("server_version") == "29.1.3" and rec["docker"]["docker_root_dir"] == "/var/lib/docker"
      and rec["docker"]["containerd_snapshotter"] is True and rec.get("images_before") == 2
      and rec["docker"].get("server_os") == "linux" and rec["docker"].get("server_arch") == "amd64", str(rec.get("docker")))
check("the window runs the declared repository's OCI job on exactly the declared hosts",
      W.seen.get("window") == "a8-docker-letta" and W.seen.get("hosts") == [II.REGISTRY, II.AUTH, *II.CDN_HOSTS]
      and W.seen.get("jobs") == [II.oci_job("letta")] and II.oci_job("letta")["repo"] == "letta/letta", str(W.seen.get("hosts")))
tar_path = D.loaded_tar
with tarfile.open(tar_path) as t:
    names = sorted(t.getnames())
    idx = json.loads(t.extractfile("index.json").read())
    layout = json.loads(t.extractfile("oci-layout").read())
    member = {n: t.extractfile(n).read() for n in names if n.startswith("blobs/")}
check("the layout tar holds the PLATFORM manifest (never an index), its config and layers from the verified blobs, and "
      "the ref's annotations", layout == {"imageLayoutVersion": "1.0.0"} and len(idx["manifests"]) == 1
      and idx["manifests"][0]["digest"] == sha(MAN) and idx["manifests"][0]["mediaType"] == MAN_T
      and idx["manifests"][0]["annotations"] == {"io.containerd.image.name": f"docker.io/{REF}",
                                                 "org.opencontainers.image.ref.name": REF}
      and member == {f"blobs/sha256/{sha(b).split(':')[1]}": b for b in (MAN, CFG, L1, L2)}, str(names))
check("the tar lies on the runs volume (D:), its sha256 recorded", str(tar_path).startswith(str(C.runs_root))
      and rec.get("layout_sha256") == hashlib.sha256(tar_path.read_bytes()).hexdigest(), str(tar_path))
check("after the load: the store is containerd (the Id is the manifest digest), the image set is the set before plus "
      "exactly the new image", rec.get("store") == "containerd" and rec.get("image_id") == sha(MAN)
      and rec.get("images_new") == [sha(MAN)] and rec.get("ref") == REF, str({k: rec.get(k) for k in ("store", "images_new")}))
check("the headroom is recorded on the volume of Docker's data", rec.get("headroom", {}).get("ok") is True
      and rec["headroom"]["volume"] == "C:\\", str(rec.get("headroom")))

check("Q-C4-4: the ref is nvt3/<repo>:<the manifest digest's first 12 hex> - written out, not the code's own function",
      rec.get("ref") == "nvt3/letta/letta:" + hashlib.sha256(MAN).hexdigest()[:12], str(rec.get("ref")))

print("\n- the classic store -")
rec, C, W, D, err = run("classic", docker=FakeDocker(store="classic"))
check("the classic store: the Id is the config digest, recorded as such", rec.get("store") == "classic"
      and rec.get("image_id") == sha(CFG) and rec.get("problems") == [], str(rec.get("problems")))

print("\n- refusals and problems by name -")
LAYERS = sum(len(b) for b in (L1, L2))
rec, C, W, D, err = run("edge", free=II.HEADROOM + 3 * LAYERS)
check("Q-C4-5: free - 3 x the layers exactly at 10 GiB passes", rec.get("problems") == [] and rec["headroom"]["ok"] is True)
rec, C, W, D, err = run("under", free=II.HEADROOM + 3 * LAYERS - 1)
check("Q-C4-5: one byte under is blocked:disk:letta by name - an owner item - and nothing is loaded",
      any(p.startswith("blocked:disk:letta") for p in rec.get("problems") or []) and D.loaded_tar is None
      and not any(x[0] == "load" for x in D.calls), str(rec.get("problems")))
rec, C, W, D, err = run("present", docker=FakeDocker(ref_present=True))
check("Q-C4-4: a ref already in the store is refused by name, nothing loaded",
      any("already in the store" in p for p in rec.get("problems") or []) and not any(x[0] == "load" for x in D.calls),
      str(rec.get("problems")))
rec, C, W, D, err = run("absent_unknown", docker=FakeDocker(absent_rc=-1))
check("Q-C4-4: an inspect of the ref that neither finds it (exit 0) nor says it is absent (exit 1) - a timeout, a daemon "
      "error - is a problem by name, nothing loaded", any("absence could not be read" in p for p in rec.get("problems") or [])
      and not any(x[0] == "load" for x in D.calls), str(rec.get("problems")))
rec, C, W, D, err = run("dirty_version", docker=FakeDocker(dirty={"version"}))
check("a read-only docker call whose own boundary check counted an egress hit is a problem by name and stops "
      "everything before the window", any("docker version check counted 1 egress" in p for p in rec.get("problems") or [])
      and not W.seen and not any(x[0] == "load" for x in D.calls), str(rec.get("problems")))
rec, C, W, D, err = run("dirty_absent", docker=FakeDocker(dirty={"absent"}))
check("the ref's absence read under a dirty boundary check stops before the load",
      any("docker absent check counted 1 egress" in p for p in rec.get("problems") or [])
      and not any(x[0] == "load" for x in D.calls), str(rec.get("problems")))
rec, C, W, D, err = run("dirty_load", docker=FakeDocker(dirty={"load"}))
check("a dirty boundary check on the load itself is a problem in the record",
      any("docker load check counted 1 egress" in p for p in rec.get("problems") or []), str(rec.get("problems")))
rec, C, W, D, err = run("window_problem", window=FakeWindow(problems=["the window check is not complete"]))
check("a problem of the window stops before any docker change", "the window check is not complete" in (rec.get("problems") or [])
      and not any(x[0] == "load" or x[:2] == ["image", "inspect"] for x in D.calls), str(rec.get("problems")))
rec, C, W, D, err = run("dropped", window=FakeWindow(drop=L1))
check("a blob missing on the disk after the window stops it before the load",
      any("is not on the disk" in p for p in rec.get("problems") or []) and not any(x[0] == "load" for x in D.calls),
      str(rec.get("problems")))
BADSIZE = {**SUMMARY, "layers": [SUMMARY["layers"][0], {**SUMMARY["layers"][1], "size": len(L2) + 1}]}
rec, C, W, D, err = run("size", window=FakeWindow(summary=BADSIZE))
check("a blob of the right digest but not the summary's declared size stops it before the load",
      any("not its digest's bytes or its size" in p for p in rec.get("problems") or [])
      and not any(x[0] == "load" for x in D.calls), str(rec.get("problems")))
for label, engine in (("windows", ("windows", "amd64")), ("arm64", ("linux", "arm64"))):
    rec, C, W, D, err = run(f"engine_{label}", docker=FakeDocker(engine=engine))
    check(f"C4B-2: an engine that says {engine[0]}/{engine[1]} is refused by name before the window - the image would "
          f"land in another engine", any(f"the daemon's engine is {engine[0]}/{engine[1]}" in p for p in rec.get("problems") or [])
          and not W.seen and not any(x[0] == "load" for x in D.calls)
          and rec.get("docker", {}).get("server_os") == engine[0], str(rec.get("problems")))
rec, C, W, D, err = run("absent_daemon_err", docker=FakeDocker(absent_err=b"Error response from daemon: i/o timeout"))
check("C4B-3: exit 1 from the ref's inspect without \"No such image\" on stderr (a daemon error) is not absence - a "
      "problem by name, nothing loaded", any("absence could not be read" in p for p in rec.get("problems") or [])
      and not any(x[0] == "load" for x in D.calls), str(rec.get("problems")))
rec, C, W, D, err = run("absent_rc125", docker=FakeDocker(absent_rc=125, absent_err=b"Error: No such image: x"))
check("C4B-3: \"No such image\" on stderr with an exit other than 1 is not absence either - both are required",
      any("absence could not be read" in p for p in rec.get("problems") or [])
      and not any(x[0] == "load" for x in D.calls), str(rec.get("problems")))
rec, C, W, D, err = run("dirty_load_named", docker=FakeDocker(dirty={"load"}))
check("D-C4b-2: a dirty load stays a problem (the run exits 1) and the record names the image left loaded",
      any("docker load check counted 1 egress" in p for p in rec.get("problems") or []) and rec.get("loaded_ref") == REF,
      str({k: rec.get(k) for k in ("problems", "loaded_ref")}))
rec, C, W, D, err = run("same_size", window=FakeWindow(same_size=L2))
check("a blob of the right size but other bytes on the disk stops it before the load",
      any("not its digest's bytes or its size" in p for p in rec.get("problems") or [])
      and not any(x[0] == "load" for x in D.calls), str(rec.get("problems")))
asked: list = []
rec, C, W, D, err = run("default_volume", volume=None, free_bytes=lambda p: asked.append(p) or BIG)
check("with no volume given, the headroom is read on C:\\ (Docker Desktop's data)", asked == [Path("C:\\")]
      and rec.get("headroom", {}).get("volume") == "C:\\", str(asked))
rec, C, W, D, err = run("ls_before_fail", docker=FakeDocker(fail_arms={"ls-before"}))
check("an image ls that fails before the window is a problem by name, and no window runs",
      any("docker image ls failed before the window" in p for p in rec.get("problems") or []) and not W.seen,
      str(rec.get("problems")))
rec, C, W, D, err = run("inspect_fail", docker=FakeDocker(fail_arms={"inspect"}))
check("an inspect that fails after the load is a problem by name - no JSON crash on its empty output",
      err is None and any("cannot be inspected" in p for p in rec.get("problems") or []), f"{err!r} {rec.get('problems')}")
rec, C, W, D, err = run("down", docker=FakeDocker(daemon_rc=1))
check("a daemon that does not answer stops everything before the window",
      any("daemon is not answering" in p for p in rec.get("problems") or []) and not W.seen, str(rec.get("problems")))
rec, C, W, D, err = run("jobfail", window=FakeWindow(summary={**SUMMARY, "ok": False, "error": "Refused: rate-limited"}))
check("a failed OCI job stops before any docker change - no inspect, no load",
      any("the OCI job failed" in p for p in rec.get("problems") or [])
      and not any(x[0] == "load" or x[:2] == ["image", "inspect"] for x in D.calls), str(rec.get("problems")))
rec, C, W, D, err = run("past", window=FakeWindow(untunnelled=II.CDN_HOSTS[0]))
check("a host the job reached with no catcher tunnel is a problem, and nothing is loaded",
      any("past the catcher" in p for p in rec.get("problems") or []) and not any(x[0] == "load" for x in D.calls),
      str(rec.get("problems")))
rec, C, W, D, err = run("tamper", window=FakeWindow(tamper=L2))
check("a blob changed on the disk after the window stops it before the load",
      any("not its digest's bytes" in p for p in rec.get("problems") or []) and not any(x[0] == "load" for x in D.calls),
      str(rec.get("problems")))
for label, patch, want in (("RootFS.Layers", {"RootFS": {"Layers": ["sha256:" + "0" * 64]}}, "RootFS.Layers"),
                           ("Config", {"Config": {"Env": ["B=2"], "Cmd": ["./letta"]}}, "['Env']"),
                           ("Id", {"Id": "sha256:" + "9" * 64}, "neither the config's nor the manifest's")):
    d = FakeDocker(inspect_patch=patch)
    rec, C, W, D, err = run(f"post_{label}", docker=d)
    check(f"Q-C4-4: the loaded image's {label} not what the blobs say is a problem by name",
          any(want in p for p in rec.get("problems") or []), str(rec.get("problems")))
rec, C, W, D, err = run("extra", docker=FakeDocker(extra="sha256:" + "8" * 64))
check("Q-C4-4: an extra image beside the new one is a problem", any("not the set before plus exactly" in p for p in rec.get("problems") or []))
rec, C, W, D, err = run("lost", docker=FakeDocker(lose="sha256:" + "1" * 64))
check("Q-C4-4: an image of the owner's lost in the load is a problem", any("not the set before plus exactly" in p
                                                                           for p in rec.get("problems") or []))
rec, C, W, D, err = run("loadfail", docker=FakeDocker(load_rc=1))
check("a failed load is a problem by name", any("docker load failed" in p for p in rec.get("problems") or []))
rec, C, W, D, err = run("nope", name="nope")
check("an undeclared image is refused by name", isinstance(err, II.ImageRefused) and "no declared image" in str(err), repr(err))
c2 = contract("used")
(c2.runs_root / "_install" / "a8-docker-letta" / "d1").mkdir(parents=True)
try:
    II.run_image_install(c2, L, FakeWindow(), name="letta", run="d1", via_port=1, parent_env={}, python=Path("py"),
                         docker=FakeDocker(), free_bytes=lambda p: BIG)
    used = "accepted"
except Exception as e:  # noqa: BLE001
    used = f"{type(e).__name__}: {e}"
check("a used run label is refused by name", "used before" in used, used)

print("\n- every docker call's environment -")
seen: dict = {}


class StubW:
    def __init__(self, *a, **k):
        self.native = None

    def begin_check(self, cid):
        seen["check"] = cid

    def end_check(self, cid):
        return CLEAN


class Proc:
    returncode = 0

    def communicate(self, timeout=None):
        return b"{}", b""


def stub_spawn(c, argv, **kw):
    """B-C4B-CWD (the auditor): the one function the stub replaces is the one that checks - so it runs the REAL checks
    of launch.spawn on what _docker_step hands it."""
    seen["argv"], seen["env"] = list(argv), dict(kw["env"])
    seen["reasons"] = (L.check_cwd(c, kw["cwd"], kw["record"])
                       + L.assert_env(c, kw["env"], parent_env=kw["parent_env"], catcher_url=kw["catcher_url"])
                       + L.assert_argv(c, argv))
    return SimpleNamespace(process=Proc(), kill_tree=lambda: None)


Lstub = SimpleNamespace(make_unit_dirs=L.make_unit_dirs, build_env=L.build_env, Witnesses=StubW, spawn=stub_spawn,
                        NativeEgressWitness=lambda: None, FsWitness=lambda s: None, watched_set=lambda c: [])
c3 = contract("envs")
parent = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "DOCKER_HOST": "tcp://evil.example:2375",
          "DOCKER_CONFIG": r"C:\Users\someone\.docker", "HTTPS_PROXY": "http://evil.example:8080"}
rc, out, errb, chk = II._docker_step(c3, Lstub, docker_exe=II.DOCKER, argv=["version"], stand="_install.x", run="d1",
                                     arm="version", parent_env=parent, native=None, fs=None, check_id="t")
cfgdir = Path(seen["env"].get("DOCKER_CONFIG", "")) if seen.get("env") else Path("")
check("B-C4B-CWD: what _docker_step hands the spawn passes the contract's own checks - check_cwd (the cwd fresh and "
      "EMPTY), assert_env and assert_argv - with no reason", seen.get("reasons") == [], str(seen.get("reasons")))
check("every docker call: DOCKER_CONFIG is an empty directory in its unit (never the parent's), no DOCKER_HOST even "
      "when the parent has one, no proxy variable, the system docker CLI", seen.get("argv", [None])[0] == str(II.DOCKER)
      and cfgdir.is_dir() and list(cfgdir.iterdir()) == [] and str(c3.runs_root) in str(cfgdir)
      and "DOCKER_HOST" not in seen["env"] and not any(k.upper() in ("HTTPS_PROXY", "HTTP_PROXY") for k in seen["env"]),
      str(sorted(seen.get("env", {}))))

check("the docker CLI image_install runs is the contract's named exception (launch.Contract.default)",
      L.Contract.default().binary_exceptions.get(str(II.DOCKER)) == "the system docker CLI", str(II.DOCKER))

print("\n- the manifest declares both image windows as the code does -")
MANW = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))["windows"]
mw = {k[len(II.WINDOW_PREFIX):]: v for k, v in MANW.items() if k.startswith(II.WINDOW_PREFIX)}
check("every declared image has its a8-docker window - exactly the registry, the token service and the CDN hosts, its "
      "repository, one redirect - and the manifest declares no other", set(mw) == set(II.IMAGES)
      and all(v["hosts"] == [II.REGISTRY, II.AUTH, *II.CDN_HOSTS] and v["repo"] == II.IMAGES[k]["repo"]
              and v["cdn_hosts"] == list(II.CDN_HOSTS) and v["max_redirects"] == 1 for k, v in mw.items()), str(mw)[:300])

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 image install: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
