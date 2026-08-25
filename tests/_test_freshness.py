#!/usr/bin/env python3
"""The other half of the evidence contract: a number may not outlive the code that made it.

`tests/_test_evidence_manifest.py` proves *document -> artifact*: every number printed in a
governed document resolves to a claim, and the claim agrees with its raw file. That check was
green while all 133 claims carried the same `commit` - `05cfdc96`, a directory move - because
nothing anywhere compared a claim against the engine that produced it. The published corpus
described the v1.0.0 ranker four review rounds after it had been rewritten.

This suite enforces *artifact -> code*:

1. **Closure** - `tools/produced_by.py` resolves a claim's `command` to the repository files it
   imports, including the deferred `_sibling(...)` loader that is the only path to
   `nevertwice/rankers.py`. A closure that misses the ranker would declare every retrieval
   number independent of the ranker.
2. **Freshness** - for a published claim, `git log -1 -- <file>` for each of those files is an
   ancestor of the claim's `commit`. If the code moved afterwards, the number is stale.
3. **Withdrawal is not free** - a claim exempted with `stale` must state a reason, must be
   cited nowhere, and must contribute no accounted number, so a withdrawn figure cannot sit
   quietly in a document.
4. **Mutation** - the checks are re-run against deliberately broken inputs, because a freshness
   check that passes on a manifest with the wrong commit is not a check.

Standard library only. Needs git history, so it is skipped with a visible line - never silently
- when the checkout has none.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "tools"))

import check_freshness as cf          # noqa: E402
import produced_by as pb              # noqa: E402

MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _has_history() -> bool:
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD~1"],
                       capture_output=True, text=True)
    return r.returncode == 0


# --------------------------------------------------------------- the closure


def test_the_closure_reaches_the_engine() -> None:
    print("\n- produced_by names the code, not just the harness -")
    deps = pb.closure("python research/longmem_eval.py")
    check("the entry point comes first", deps[0] == "research/longmem_eval.py", deps[0])
    check("the closure reaches memory_hook", "nevertwice/memory_hook.py" in deps)
    # The regression that motivated the deferred-import pass: rankers.py is imported nowhere
    # by an `import` statement - only through `_sibling("rankers")` inside memory_hook.
    check("the closure reaches the ranker through the deferred loader",
          "nevertwice/rankers.py" in deps,
          "rankers.py is only reachable via _sibling(); a closure without it would call every "
          "retrieval number independent of the ranker")
    check("third-party and stdlib imports are not tracked",
          not any(d.startswith(("json", "os/", "numpy")) for d in deps))


def test_every_claim_declares_its_closure() -> None:
    print("\n- every claim names the files that produced it -")
    claims = MANIFEST["claims"]
    missing = [c["id"] for c in claims if not c.get("produced_by")]
    check("no claim is missing produced_by", not missing, ", ".join(missing[:5]))

    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                             capture_output=True, text=True)
    known = set(tracked.stdout.split()) if tracked.returncode == 0 else None
    if known is None:
        print("       (skipped: no git index here)")
        return
    unknown = sorted({p for c in claims for p in c.get("produced_by", [])
                      if p not in known})
    check("every produced_by path is a tracked file", not unknown,
          ", ".join(unknown[:5]))


def test_the_closure_is_current() -> None:
    """The manifest's stored closure must equal what the resolver computes today.

    A stored closure that has quietly gone out of date is worse than none: the freshness check
    would keep passing while the file that actually changed is no longer being watched.
    """
    print("\n- the stored closure matches the resolver -")
    drifted = []
    cache: dict[str, list[str]] = {}
    for claim in MANIFEST["claims"]:
        command = claim["command"]
        if command not in cache:
            try:
                cache[command] = pb.closure(command)
            except pb.UnresolvableCommand as exc:
                drifted.append(f"{claim['id']}: {exc}")
                cache[command] = []
                continue
        if cache[command] and claim.get("produced_by") != cache[command]:
            drifted.append(f"{claim['id']}: stored closure differs from the resolver")
    check("no stored closure has drifted", not drifted, "; ".join(drifted[:4]))


# ------------------------------------------------------------- the ratchet


def test_no_published_number_outlives_its_code() -> None:
    print("\n- every published number traces to code no newer than its commit -")
    if not _has_history():
        print("       (skipped: this checkout has no git history)")
        return
    git = cf.Git()
    failures, declared, cited = cf.check(MANIFEST, git)
    check("no published claim names code that moved after it was measured",
          not failures,
          "; ".join(f"{f['claim']['id']} ({len(f['moved'])} files)" for f in failures[:4]))
    check("no withdrawn claim is still cited", not cited,
          ", ".join(c["id"] for c in cited[:5]))
    print(f"       ({len(MANIFEST['claims']) - len(declared)} published, "
          f"{len(declared)} withdrawn)")


def test_withdrawal_costs_something() -> None:
    print("\n- a withdrawn claim is withdrawn everywhere -")
    stale = [c for c in MANIFEST["claims"] if c.get("stale")]
    check("at least one claim is withdrawn (B8 withdrew the pre-review corpus)", bool(stale),
          "none - if the corpus was genuinely re-measured, delete this check with a note")

    vague = [c["id"] for c in stale if len(str(c.get("stale", "")).strip()) < 20]
    check("every withdrawal reason is a sentence, not a shrug", not vague,
          ", ".join(vague[:5]))

    # The reason must name the gate, so the owner can act on it rather than re-derive it.
    GATES = ("dataset", "vault", "API", "GPU", "historical", "reproduce", "seed", "store")
    unnamed = [c["id"] for c in stale
               if not any(g.lower() in str(c["stale"]).lower() for g in GATES)]
    check("every withdrawal names the gate that blocks re-measurement", not unnamed,
          ", ".join(unnamed[:5]))

    docs = [ROOT / d for d in MANIFEST["scope"]["docs"]]
    texts = {d.name: d.read_text(encoding="utf-8") for d in docs if d.exists()}
    published_forms = {p for c in MANIFEST["claims"] if not c.get("stale")
                       for p in c["printed"]}

    def distinctive(form: str) -> bool:
        """Is this printed form specific enough for a substring search to mean anything?

        `8`, `20` and `half` are not. A bare one- or two-character token matches a list
        marker, a `k` value, a percent-encoded fragment inside a badge URL and half the prose
        in the repository, and a form with no digit is not a number at all. Those cases are
        governed properly - token by token, against the declared non-metrics - by
        `tests/_test_evidence_manifest.py`. This check is the coarse backstop for the forms a
        substring hit genuinely convicts: `0.766`, `2,188 ms`, `-86%`.
        """
        return len(form) >= 3 and any(ch.isdigit() for ch in form)

    still_printed = []
    for claim in stale:
        for printed in claim["printed"]:
            if printed in published_forms or not distinctive(printed):
                continue           # another, live claim legitimately prints the same token
            for name, text in texts.items():
                if printed in text:
                    still_printed.append(f"{claim['id']} -> {name} still prints {printed!r}")
    check("no withdrawn number is still printed in a governed document", not still_printed,
          "; ".join(still_printed[:4]))


# --------------------------------------------------------------- mutations


def test_mutations_turn_it_red() -> None:
    """Break the inputs on purpose; each break must be caught.

    Exit criterion B8(f) asks for one of these - reverting a generator by a commit. The other
    three cover the ways the ratchet could be defeated without touching a generator at all.
    """
    print("\n- the checks fail when they should -")
    if not _has_history():
        print("       (skipped: this checkout has no git history)")
        return

    published = [c for c in MANIFEST["claims"] if not c.get("stale")]
    if not published:
        check("there is a published claim to mutate", False,
              "every claim is withdrawn; the mutations below cannot run")
        return
    victim = published[0]

    # 1. B8(f): roll the claim back to just before the newest change in its own closure.
    #    That is the state "revert one generator by one commit" leaves behind, and it must be
    #    caught. Stamping at the parent of the *newest* commit in the closure is what makes
    #    the mutation bite: stamping at the parent of an arbitrary file leaves every other
    #    file still an ancestor, and the check would stay green for the right reason.
    newest = subprocess.run(
        ["git", "-C", str(ROOT), "rev-list", "-1", "HEAD", "--", *victim["produced_by"]],
        capture_output=True, text=True).stdout.strip()
    stamp = subprocess.run(["git", "-C", str(ROOT), "rev-parse", f"{newest}^"],
                           capture_output=True, text=True).stdout.strip()
    if not stamp:
        check("the closure has a parent commit to roll back to", False, f"newest={newest}")
        return
    mutated = json.loads(json.dumps(MANIFEST))
    for claim in mutated["claims"]:
        if claim["id"] == victim["id"]:
            claim["commit"] = stamp
    failures, _, _ = cf.check(mutated, cf.Git())
    check("a claim stamped before the newest code in its own closure is caught",
          any(f["claim"]["id"] == victim["id"] for f in failures),
          f"stamped {stamp[:9]} (parent of {newest[:9]}) and the check stayed green")

    # 2. A claim with no closure must not pass by default.
    mutated = json.loads(json.dumps(MANIFEST))
    for claim in mutated["claims"]:
        if claim["id"] == victim["id"]:
            claim.pop("produced_by", None)
    failures, _, _ = cf.check(mutated, cf.Git())
    check("a claim with no produced_by is caught",
          any(f["claim"]["id"] == victim["id"] for f in failures))

    # 3. Withdrawal must not be a way to keep publishing.
    mutated = json.loads(json.dumps(MANIFEST))
    for claim in mutated["claims"]:
        if claim["id"] == victim["id"]:
            claim["stale"] = "pretending this is withdrawn"
            claim["cited_in"] = ["README.md"]
    _, _, cited = cf.check(mutated, cf.Git())
    check("declaring a claim stale while still citing it is caught",
          any(c["id"] == victim["id"] for c in cited))

    # 4. The closure resolver must not silently drop the deferred loader again.
    saved = pb.DEFERRED_IMPORTERS
    try:
        pb.DEFERRED_IMPORTERS = ()
        deps = pb.closure("python research/longmem_eval.py")
        check("dropping the deferred-import pass loses the ranker",
              "nevertwice/rankers.py" not in deps,
              "rankers.py was still found, so this mutation proves nothing")
    finally:
        pb.DEFERRED_IMPORTERS = saved


def main() -> int:
    for fn in (test_the_closure_reaches_the_engine,
               test_every_claim_declares_its_closure,
               test_the_closure_is_current,
               test_no_published_number_outlives_its_code,
               test_withdrawal_costs_something,
               test_mutations_turn_it_red):
        fn()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
