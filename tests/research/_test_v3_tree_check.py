#!/usr/bin/env python3
"""PREREG-V3 TB4.11a A2 (A6): research/v3/tree_check.py - the tree check at the anchor, STAND START and STAND END
(rev1 §1.3, T16, P0e; the auditor's Q10 and D10).

* argv: exactly one fixed git command - status --porcelain=v2 --branch -z --untracked-files=all --ignored=traditional,
  with --no-optional-locks (git refreshes no index: the check writes nothing) - and one argv_exception index, the repo;
* parse: porcelain v2 -z - the head, tracked changes (1, 2 with its second path, u), untracked (?) and ignored (!);
* verdict (D10 a): dirty = HEAD is not the anchor, or a tracked change anywhere, or an untracked path under research/
  outside research/v3/results/ (M-TREE-results-dirty: the results directory is never dirty); .claude/ entries are
  recorded by name and never fail; the ignored listing is recorded; the heldout name check - codesess_code_heldout_*
  and *heldout* (any case) among untracked and ignored paths - with §1.3's exceptions: the auditor's process documents,
  the public-heldout logs, and __pycache__ bytecode of a TRACKED module (a module the listing does not name, on disk);
* a real temporary repository (a named SKIP without git): with --untracked-files=all the traditional ignored listing
  names each file inside an ignored directory, so a *heldout* file hidden there is seen (M-TREE-heldout-miss).

    python tests/research/_test_v3_tree_check.py
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

_spec = importlib.util.spec_from_file_location("v3_tree_check", ROOT / "research" / "v3" / "tree_check.py")
TC = importlib.util.module_from_spec(_spec)
sys.modules["v3_tree_check"] = TC
_spec.loader.exec_module(TC)
PASSED = FAILED = SKIPPED = 0
ANCHOR = "a" * 40


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def skip(name: str, why: str) -> None:
    global SKIPPED
    SKIPPED += 1
    print(f"  SKIP {name} - {why}")


def z(*entries: str) -> bytes:
    return b"".join(e.encode("utf-8") + b"\0" for e in entries)


print("- argv: one fixed command, one exception index -")
av = TC.argv("git", "D:/repo")
check("git -C <repo> --no-optional-locks status --porcelain=v2 --branch -z --untracked-files=all --ignored=traditional",
      av == ["git", "-C", "D:/repo", "--no-optional-locks", "status", "--porcelain=v2", "--branch", "-z",
             "--untracked-files=all", "--ignored=traditional"], str(av))
check("M-TREE-two-exception-indexes: exactly one argv_exception index, the repository's", TC.ARGV_EXCEPTION == {2: "repo"})
check("M-TREE-optional-locks: --no-optional-locks is there (git status refreshes no index: the check writes nothing)",
      "--no-optional-locks" in TC.ARGV_TAIL and TC.ARGV_TAIL.index("--no-optional-locks") < TC.ARGV_TAIL.index("status"))

print("\n- parse: porcelain v2 -z -")
out = z(f"# branch.oid {ANCHOR}", "# branch.head invariants/v3",
        "1 .M N... 100644 100644 100644 " + "b" * 40 + " " + "b" * 40 + " nevertwice/api.py",
        "2 R. N... 100644 100644 100644 " + "c" * 40 + " " + "c" * 40 + " R100 new name.py", "old name.py",
        "u UU N... 100644 100644 100644 100644 " + "d" * 40 + " " + "d" * 40 + " " + "d" * 40 + " conflict.py",
        "? research/scratch.txt", "! .loop/m5_check.py")
st = TC.parse(out)
check("the head and the branch", st.head == ANCHOR and st.branch == "invariants/v3")
check("tracked changes: an ordinary change, a rename with both paths (a path may hold a space), an unmerged path",
      st.tracked == [(".M", "nevertwice/api.py"), ("R.", "new name.py"), ("UU", "conflict.py")] and st.renamed_from == ["old name.py"],
      f"{st.tracked} {st.renamed_from}")
check("untracked and ignored paths", st.untracked == ["research/scratch.txt"] and st.ignored == [".loop/m5_check.py"])
try:
    TC.parse(z("# branch.oid " + ANCHOR, "X what"))
    bad = "accepted"
except TC.TreeError as e:
    bad = str(e)
check("an entry of an unknown kind refuses by name", "unknown" in bad, bad)

print("\n- verdict (D10 a) -")


def v(*entries: str, head: str = ANCHOR, disk: set[str] | None = None) -> dict:
    return TC.verdict(TC.parse(z(f"# branch.oid {head}", *entries)), anchor=ANCHOR,
                      exists=(lambda p: p in (disk or set())))


clean = v("! .loop/m5_check.py", "? .claude/settings.local.json", "? research/v3/results/S1_B_product.json")
check("clean: HEAD is the anchor, nothing tracked changed, research/ clean outside results/", clean["clean"] is True
      and clean["problems"] == [], str(clean))
check(".claude/ entries are recorded by name and never fail", clean["claude"] == [".claude/settings.local.json"])
check("the ignored listing is recorded", clean["ignored"] == [".loop/m5_check.py"])
check("M-TREE-results-dirty: research/v3/results/ is never dirty", clean["clean"] is True)
check("HEAD is not the anchor: dirty", not v(head="e" * 40)["clean"] and "anchor" in " ".join(v(head="e" * 40)["problems"]))
check("a tracked change anywhere: dirty", not v("1 .M N... 100644 100644 100644 " + "b" * 40 + " " + "b" * 40 + " README.md")["clean"])
check("an untracked path under research/ outside results/: dirty (P0e)", not v("? research/v3/tmp.json")["clean"])
check("an untracked path outside research/ is recorded, not dirty (D10 a)", v("? notes.txt")["clean"] is True
      and v("? notes.txt")["untracked"] == ["notes.txt"])
check("an ignored path under research/ is not dirty (it is ignored)", v("! research/v3/__pycache__/x.cpython-314.pyc")["clean"])

print("\n- the heldout name check (§1.3) -")
for label, entry in (("codesess_code_heldout_ cache", "! research/codesess_code_heldout_42.json"),
                     ("a *heldout* file, any case", "? data/My_HeldOut_set.jsonl"),
                     ("an ignored *heldout* file", "! .loop/campaign-v2-log/b7_heldout.log")):
    r = v(entry)
    check(f"{label}: a named problem", not r["clean"] and r["heldout"], str(r))
for label, entry in (("the auditor's HELDOUT-LABELING.md", "! .loop/HELDOUT-LABELING.md"),
                     ("heldout_clean.sh", "! .loop/heldout_clean.sh"),
                     ("the d1 public-heldout log", "! .loop/campaign-log/d1_heldout.log"),
                     ("the b4 public-heldout log", "! .loop/campaign-v2-log/b4_heldout.log"),
                     ("its .cm marker", "! .loop/campaign-v2-log/.cm_b4_heldout"),
                     ("its .mark marker", "! .loop/campaign-v2-log/.mark_b4_heldout")):
    check(f"exception: {label}", v(entry)["clean"] and not v(entry)["heldout"])
pyc = "! research/embed_universal/__pycache__/heldout_eval.cpython-314.pyc"
check("exception: __pycache__ bytecode of a TRACKED module (on disk, not listed untracked or ignored)",
      v(pyc, disk={"research/embed_universal/heldout_eval.py"})["clean"])
check("... but bytecode whose module is not tracked is a problem",
      not v(pyc, "? research/embed_universal/heldout_eval.py", disk={"research/embed_universal/heldout_eval.py"})["clean"]
      and not v(pyc, disk=set())["clean"])

print("\n- a real temporary repository -")
git = shutil.which("git")
if git is None:
    skip("the traditional ignored listing names each file in an ignored directory", "no git on this machine")
else:
    with tempfile.TemporaryDirectory(prefix="v3tree_") as td:
        repo = Path(td)
        env = {"GIT_CONFIG_GLOBAL": str(repo / "none"), "GIT_CONFIG_NOSYSTEM": "1", "PATH": str(Path(git).parent),
               "HOME": td, "USERPROFILE": td}
        runq = lambda *a: subprocess.run([git, *a], cwd=repo, env=env, capture_output=True, check=True)  # noqa: E731
        runq("init", "-q")
        (repo / ".gitignore").write_text("cache/\n", encoding="utf-8")
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        runq("add", ".")
        runq("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c")
        head = runq("rev-parse", "HEAD").stdout.decode().strip()
        (repo / "cache").mkdir()
        (repo / "cache" / "deep_heldout_copy.json").write_text("{}", encoding="utf-8")
        before = (repo / ".git" / "index").read_bytes()
        res = subprocess.run(TC.argv(git, str(repo)), env=env, capture_output=True, check=True)
        st = TC.parse(res.stdout)
        r = TC.verdict(st, anchor=head, exists=lambda p: (repo / p).exists())
        check("M-TREE-heldout-miss: a *heldout* file inside an ignored directory is listed and found",
              "cache/deep_heldout_copy.json" in st.ignored and r["heldout"] == ["cache/deep_heldout_copy.json"], str(st))
        check("the head is read and the index is left byte for byte (no refresh)",
              st.head == head and (repo / ".git" / "index").read_bytes() == before)

print(f"\nv3 tree check: {PASSED} passed, {FAILED} failed, {SKIPPED} skipped")
sys.exit(1 if FAILED else 0)
