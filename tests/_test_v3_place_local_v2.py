#!/usr/bin/env python3
"""PREREG-V3 O2 (the auditor, after A3.j j1): research/v3/place_local_v2.py, offline, on temporary roots.

research/data/*.json is gitignored, so the v2 pins (S4 LoCoMo, S9 LME-S v2) exist only in the main working tree - never
in a clean worktree, an anchor or a restore tree. j1 stopped on exactly that. Now:

* location() puts a local-v2 pin at <pins_root>/local-v2/<sha256>/<basename>, never under the code's own tree, so a
  reader in a clean worktree finds it and verify() binds it;
* the place step copies a source only after its bytes are the pin's (sha256 and size), re-hashes the copy before it is
  exposed (a .partial, then os.replace), never overwrites a different file (P12: an identical one is already-placed),
  and writes a place record under <runs_root>/_fetch/local-v2/<run>/ - a used run label is refused;
* every refusal is a named problem in the record, and a refused pin leaves nothing at its place.

    python tests/_test_v3_place_local_v2.py
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
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


CP = _load("v3_corpus_pin_pl", ROOT / "research" / "v3" / "corpus_pin_v3.py")
P = _load("v3_place_local_v2", ROOT / "research" / "v3" / "place_local_v2.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_placev2_"))
LOCAL = sorted(n for n, p in CP.PINS_DECLARED.items() if p["source"] == "local-v2")
BODY = {"locomo10": b'[{"qa": [], "sample_id": "s0"}]\n', "longmemeval_s": b'[{"question_id": "q0"}]\n' * 3}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def table() -> dict:
    """The declared table with the local-v2 pins bound to small bodies of the same names and paths."""
    t = copy.deepcopy(CP.PINS_DECLARED)
    for n, b in BODY.items():
        t[n].update(sha256=sha(b), bytes=len(b))
    return t


T = table()


def roots(tag: str, bodies: dict | None = None) -> tuple[Path, Path, Path]:
    """(source root laid out like the main working tree, pins_root, runs_root)."""
    src = TMP / tag / "src"
    for n, b in (BODY if bodies is None else bodies).items():
        f = src / Path(*T[n]["path"].split("/"))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b)
    return src, TMP / tag / "runs" / "_pins", TMP / tag / "runs"


def place(tag: str, *, bodies: dict | None = None, run: str = "l1", **kw) -> tuple[dict, Path, Path, Path]:
    src, pr, rr = roots(tag, bodies)
    return P.place(src, pr, rr, run, pins=T, **kw), src, pr, rr


def dest(pr: Path, name: str) -> Path:
    return pr / "local-v2" / T[name]["sha256"] / T[name]["path"].rsplit("/", 1)[-1]


print("\n- location(): a local-v2 pin lives in the polygon, never in the code's tree -")
check("the table has exactly the two v2 pins as local-v2 (S4 LoCoMo, S9 LME-S v2)", LOCAL == ["locomo10", "longmemeval_s"], str(LOCAL))
PR0 = TMP / "loc" / "_pins"
check("location() puts a local-v2 pin at <pins_root>/local-v2/<sha256>/<basename>",
      all(CP.location(n, hf_hub=None, pins_root=PR0) == PR0 / "local-v2" / CP.PINS[n]["sha256"] / CP.PINS[n]["path"].rsplit("/", 1)[-1]
          for n in LOCAL), str([CP.location(n, hf_hub=None, pins_root=PR0) for n in LOCAL]))
check("... and never under the code's own tree (research/data is gitignored: a clean worktree has no such file)",
      not any(CP.REPO in CP.location(n, hf_hub=None, pins_root=PR0).parents for n in LOCAL))
check("the table still names the repository path as the pin's source", all(CP.PINS[n]["path"].startswith("research/data/") for n in LOCAL))

print("\n- a clean placement -")
rec, src, pr, rr = place("ok")
check("both pins placed, no problem, at their polygon places, bytes identical",
      rec["problems"] == [] and sorted(rec["placed"]) == LOCAL and all(rec["placed"][n]["status"] == "placed" for n in LOCAL)
      and all(dest(pr, n).read_bytes() == BODY[n] for n in LOCAL), str(rec))
check("the record names each pin's sha256, size, source path and place (relative to pins_root)",
      all(rec["placed"][n] == {"status": "placed", "sha256": T[n]["sha256"], "bytes": T[n]["bytes"], "source": T[n]["path"],
                               "dest": dest(pr, n).relative_to(pr).as_posix()} for n in LOCAL), str(rec["placed"]))
on_disk = json.loads((rr / "_fetch" / "local-v2" / "l1" / "place_record.json").read_bytes())
check("the place record is written under <runs_root>/_fetch/local-v2/<run>/", on_disk["placed"] == rec["placed"]
      and on_disk["window"] == "local-v2" and on_disk["run"] == "l1" and on_disk["problems"] == [])
check("a clean worktree's reader finds each pin through location() and verify() binds it",
      all(CP.verify(n, CP.location(n, hf_hub=None, pins_root=pr, pins=T), pins=T)["sha256"] == T[n]["sha256"] for n in LOCAL))
check("no .partial is left behind", not list(pr.rglob("*.partial")))

print("\n- P12: again, and a different file at the place -")
mtimes = {n: dest(pr, n).stat().st_mtime_ns for n in LOCAL}
rec2 = P.place(src, pr, rr, "l2", pins=T)
check("P12: an identical file already at its place is already-placed, not rewritten",
      rec2["problems"] == [] and all(rec2["placed"][n]["status"] == "already-placed" for n in LOCAL)
      and all(dest(pr, n).stat().st_mtime_ns == mtimes[n] for n in LOCAL), str(rec2))
dest(pr, "locomo10").write_bytes(b"other bytes\n")
rec3 = P.place(src, pr, rr, "l3", pins=T)
check("L3: a different file at the place is never overwritten - a named problem, its bytes kept",
      any(p.startswith("L3 locomo10:") for p in rec3["problems"]) and dest(pr, "locomo10").read_bytes() == b"other bytes\n"
      and "locomo10" not in rec3["placed"], str(rec3["problems"]))
try:
    P.place(src, pr, rr, "l1", pins=T)
    reused = False
except Exception as e:  # noqa: BLE001 - only the named refusal counts
    reused = isinstance(e, P.PlaceRefused) and "was used before" in str(e)
check("a run label used before is refused before anything is copied", reused)
_real_location = P.CP.location
OUTSIDE = TMP / "outside" / "locomo10.json"
P.CP.location = lambda name, **kw: OUTSIDE
try:
    ss, prs, rrs = roots("outside_t")
    P.place(ss, prs, rrs, "l1", pins=T)
    guarded = False
except Exception as e:  # noqa: BLE001
    guarded = isinstance(e, P.PlaceRefused) and "does not point into pins_root" in str(e)
finally:
    P.CP.location = _real_location
check("a location() that points outside pins_root is refused before anything is written (the main tree is never a place)",
      guarded and not OUTSIDE.exists())

print("\n- the source and the copy are both bound by the pin -")
rec4, _, pr4, _ = place("bad_src", bodies={"locomo10": b"CONTENT-7f3a not the pin\n", "longmemeval_s": BODY["longmemeval_s"]})
check("L2: a source that is not the pinned bytes is never copied - a named problem, nothing at its place",
      any(p.startswith("L2 locomo10:") for p in rec4["problems"]) and not dest(pr4, "locomo10").exists()
      and rec4["placed"]["longmemeval_s"]["status"] == "placed", str(rec4["problems"]))
try:
    rec5, _, pr5, _ = place("no_src", bodies={"longmemeval_s": BODY["longmemeval_s"]})
except Exception as e:  # noqa: BLE001 - a crash is not the named problem
    rec5, pr5 = {"problems": [f"crashed: {type(e).__name__}"]}, TMP / "no_src" / "runs" / "_pins"
check("L1: a pin with no source file is a named problem", any(p.startswith("L1 locomo10:") for p in rec5["problems"])
      and not dest(pr5, "locomo10").exists(), str(rec5["problems"]))


def bad_copy(s, d) -> None:
    shutil.copyfile(s, d)
    with open(d, "ab") as f:
        f.write(b"x")


rec6, _, pr6, _ = place("bad_copy", copy=bad_copy)
check("L4: a copy that is not the pinned bytes is re-hashed and refused - nothing placed, no .partial left",
      all(any(p.startswith(f"L4 {n}:") for p in rec6["problems"]) for n in LOCAL)
      and not any(dest(pr6, n).exists() for n in LOCAL) and not list(pr6.rglob("*.partial")), str(rec6["problems"]))
print("\n- O2-a: the same size with other bytes is caught by the sha256, at the source, the place and the copy -")


def flip(b: bytes) -> bytes:
    return bytes([b[0] ^ 1]) + b[1:]


rec7, _, pr7, _ = place("same_size_src", bodies={"locomo10": flip(BODY["locomo10"]), "longmemeval_s": BODY["longmemeval_s"]})
check("O2-a L2: a source of the pinned size but other bytes is never copied",
      any(p.startswith("L2 locomo10:") and "sha256" in p for p in rec7["problems"]) and not dest(pr7, "locomo10").exists(),
      str(rec7["problems"]))
src8, pr8, rr8 = roots("same_size_dest")
dest(pr8, "locomo10").parent.mkdir(parents=True, exist_ok=True)
dest(pr8, "locomo10").write_bytes(flip(BODY["locomo10"]))
rec8 = P.place(src8, pr8, rr8, "l1", pins=T)
check("O2-a L3: a file of the pinned size but other bytes at the place is never overwritten",
      any(p.startswith("L3 locomo10:") and "sha256" in p for p in rec8["problems"])
      and dest(pr8, "locomo10").read_bytes() == flip(BODY["locomo10"]), str(rec8["problems"]))


def flip_copy(s, d) -> None:
    Path(d).write_bytes(flip(Path(s).read_bytes()))


rec9, _, pr9, _ = place("same_size_copy", copy=flip_copy)
check("O2-a L4: a copy of the pinned size but other bytes is refused - nothing placed, no .partial",
      all(any(p.startswith(f"L4 {n}:") and "sha256" in p for p in rec9["problems"]) for n in LOCAL)
      and not any(dest(pr9, n).exists() for n in LOCAL) and not list(pr9.rglob("*.partial")), str(rec9["problems"]))
check("the problems never print a file's bytes", not any("CONTENT-7f3a" in p for p in rec4["problems"] + rec6["problems"]),
      str(rec4["problems"]))

print("\n- the CLI -")
check("the CLI takes the source root explicitly (no default: it is the main working tree, named by the GO)",
      P.main.__doc__ is not None and "--source-root" in P.main.__doc__)

print("\n- B1: CI runs 3.10 -")
_NEWER = ("hashlib.file_digest", "datetime.UTC", "import tomllib", "except*", "StrEnum", "typing.Self", "TaskGroup",
          "itertools.batched", "contextlib.chdir")
_found = sorted(f"{p.name}: {n}" for p in (ROOT / "research" / "v3").glob("*.py")
                for n in _NEWER if n in p.read_bytes().decode("utf-8"))
big = TMP / "big.bin"
big.write_bytes(bytes(range(256)) * 4096 + b"x")                 # 1 MiB + 1: more than one streamed block
check("G2: the streamed sha256 of a file larger than one block is the whole file's",
      P._sha256(big) == hashlib.sha256(big.read_bytes()).hexdigest())
check("B1: no 3.11+ API in research/v3 (hashlib.file_digest broke place_local_v2 on 3.10 - CI 76cb0e9)", _found == [],
      str(_found))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 place local-v2: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
