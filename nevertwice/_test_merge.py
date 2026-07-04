#!/usr/bin/env python3
"""Self-check for merge.py (conflict-aware vault merge). Verifies the structured auto-merge of
frontmatter collisions (recurrence, supersession, tags) and that genuine body/scalar divergence
falls through to git. Pure logic — no git, no files."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge as M          # noqa: E402


def _note(body="the fact body", **fm):
    fm.setdefault("date", "2026-01-01")
    fm.setdefault("project", "p")
    fm.setdefault("type", "mistake")
    front = "\n".join(f"{k}: {v}" for k, v in fm.items())
    return f"---\n{front}\n---\n\n{body}\n"


def test_recurrence_takes_the_max():
    base = _note(recurrence="1")
    ours = _note(recurrence="3")
    theirs = _note(recurrence="5")
    out = M.merge_note(base, ours, theirs)
    assert out is not None and "recurrence: 5" in out, out
    print("ok test_recurrence_takes_the_max")


def test_supersession_wins():
    base = _note()
    ours = _note()                                       # still live
    theirs = _note(status="superseded", superseded_by="2026-02-02-p-decision-new")
    out = M.merge_note(base, ours, theirs)
    assert out is not None
    assert "status: superseded" in out and "superseded_by: 2026-02-02-p-decision-new" in out
    print("ok test_supersession_wins")


def test_tags_union():
    base = _note(tags='["a"]')
    ours = _note(tags='["a", "b"]')
    theirs = _note(tags='["a", "c"]')
    out = M.merge_note(base, ours, theirs)
    assert out is not None
    for t in ("a", "b", "c"):
        assert f'"{t}"' in out, (t, out)
    print("ok test_tags_union")


def test_divergent_body_falls_through():
    ours = _note(body="one version of the fact")
    theirs = _note(body="a genuinely different fact")
    assert M.merge_note(_note(), ours, theirs) is None      # git should surface this
    print("ok test_divergent_body_falls_through")


def test_scalar_disagreement_falls_through():
    ours = _note(project="alpha")
    theirs = _note(project="beta")                          # same body, but a real scalar clash
    assert M.merge_note(_note(), ours, theirs) is None
    print("ok test_scalar_disagreement_falls_through")


def test_driver_writes_merged_to_ours(tmp=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "base").write_text(_note(recurrence="1"), encoding="utf-8")
        (d / "ours").write_text(_note(recurrence="2"), encoding="utf-8")
        (d / "theirs").write_text(_note(recurrence="4"), encoding="utf-8")
        rc = M._driver(str(d / "base"), str(d / "ours"), str(d / "theirs"))
        assert rc == 0
        assert "recurrence: 4" in (d / "ours").read_text(encoding="utf-8")
        # a divergent-body merge must signal conflict (rc=1) AND leave real conflict markers
        # in %A: a custom driver returning 1 with a clean file makes rebase silently drop
        # "theirs" (the verified data-loss path, code-review 2026-07)
        (d / "theirs").write_text(_note(body="a genuinely different fact"), encoding="utf-8")
        assert M._driver(str(d / "base"), str(d / "ours"), str(d / "theirs")) == 1
        out = (d / "ours").read_text(encoding="utf-8")
        assert "<<<<<<<" in out and "=======" in out and ">>>>>>>" in out
        assert "a genuinely different fact" in out          # theirs preserved, not dropped
    print("ok test_driver_writes_merged_to_ours")


def test_missing_status_never_emits_none():
    ours = _note()                                          # no status field at all
    theirs = _note(status="current")
    out = M.merge_note(_note(), ours, theirs)
    assert out is not None and "status: current" in out and "None" not in out
    print("ok test_missing_status_never_emits_none")


def test_divergent_retirements_conflict():
    # both sides retired the same note via DIFFERENT successors — a real disagreement;
    # silently keeping one would drop the other supersession link with no trace
    ours = _note(status="superseded", superseded_by="2026-02-01-p-decision-a")
    theirs = _note(status="superseded", superseded_by="2026-02-02-p-decision-b")
    assert M.merge_note(_note(), ours, theirs) is None
    print("ok test_divergent_retirements_conflict")


def test_end_to_end_real_git_merge():
    """The driver through REAL git (register + .gitattributes + a branch merge). The in-process
    suite was green while the registered command itself was broken (the -m form), so this
    exercises the actual subprocess path git runs."""
    import subprocess
    import tempfile

    def git(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)

    with tempfile.TemporaryDirectory() as d:
        repo = Path(d) / "vault"
        repo.mkdir()
        if git(repo.parent, "--version").returncode != 0:   # no git on this box → skip
            print("ok test_end_to_end_real_git_merge (skipped: no git)")
            return
        git(repo, "init", "-q", "-b", "master")
        git(repo, "config", "user.email", "t@t")
        git(repo, "config", "user.name", "t")
        assert M.register(repo)
        cfg = git(repo, "config", "merge.nevertwice.driver").stdout
        assert "merge.py" in cfg and "-m nevertwice" not in cfg   # absolute file path, not -m
        note = repo / "note.md"
        note.write_text(_note(recurrence="1"), encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "base")
        # branch a bumps recurrence to 3; master bumps to 5 → structured auto-merge keeps 5
        git(repo, "checkout", "-qb", "a")
        note.write_text(_note(recurrence="3"), encoding="utf-8")
        git(repo, "commit", "-aqm", "a")
        git(repo, "checkout", "-q", "master")
        note.write_text(_note(recurrence="5"), encoding="utf-8")
        git(repo, "commit", "-aqm", "m")
        r = git(repo, "merge", "a")
        assert r.returncode == 0, r.stderr + r.stdout
        assert "recurrence: 5" in note.read_text(encoding="utf-8")
    print("ok test_end_to_end_real_git_merge")


if __name__ == "__main__":
    test_recurrence_takes_the_max()
    test_supersession_wins()
    test_tags_union()
    test_divergent_body_falls_through()
    test_scalar_disagreement_falls_through()
    test_driver_writes_merged_to_ours()
    test_missing_status_never_emits_none()
    test_divergent_retirements_conflict()
    test_end_to_end_real_git_merge()
    print("\nall merge self-checks passed")
