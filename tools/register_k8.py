#!/usr/bin/env python3
"""Register the three K8 step-zero numbers the ledger promised at the merge.

K8 shipped with a measurement before its gate, and the gate itself quotes it: how well a skeleton
test can tell a replacement from a different fact on the same topic (`research/k8_skeleton.py`),
how accurate the sleep-time judge is on pairs whose truth is known and what a verdict costs in
tokens (`research/k8_judge_eval.py`), and how many contested pairs a week the layer would actually
see on a real store (`research/k8_vault_dryrun.py`). The ledger entry K8 says the judge's numbers
are "registered at merge as the family `absorb_judge.*`"; this tool is that registration, plus the
skeleton's AUC and the vault's inflow, under the same freshness rules as every other register tool.

Two figures the register keeps apart on purpose. The skeleton AUC is published for the
**description** text and for the **`[facts]` block** separately, each from one named measure rather
than "the best of five": a maximum over five measures is a choice, and a claim that records a
choice as a measurement is how a page comes to print a number no command reproduces.

    python tools/register_k8.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"
sys.path.insert(0, str(ROOT / "tools"))
import git_status  # noqa: E402  the shared reading of `git status --porcelain -z`

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                                # noqa: BLE001
    pass

COLLISIONS = ("research/results/k8_collisions_explicit.json research/results/k8_collisions_implicit.json "
              "research/results/k8_collisions_asof.json")
SKELETON_CMD = f"python research/k8_skeleton.py {COLLISIONS} --out research/results/k8_step0.json"
JUDGE_CMD = f"python research/k8_judge_eval.py {COLLISIONS} --out research/results/k8_judge_eval.json"
#: the vault path is a placeholder - the store is private and named nowhere in the register. It is
#: one bare word on purpose: `produced_by` splits a command with `shlex`, and an apostrophe in a
#: placeholder such as "<the owner's vault>" is an unclosed quote.
VAULT_CMD = ("python research/k8_vault_dryrun.py <vault> --weeks 4 --today 2026-09-16 "
             "--out research/results/k8_vault_dryrun.json")

#: the stands the collision record covers, and how a statement names each
STANDS = {"explicit": "the explicit-replacement corpus",
          "implicit": "the implicit-replacement corpus",
          "asof": "the as-of corpus, dated two days apart"}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _dirty_files() -> set[str]:
    #: One reading of `git status` for every registrar - `tools/git_status.py`, held by
    #: `tests/_test_git_status_parsing.py`. The copy that used to sit here (in ten files, three
    #: spellings) read a rename as a single path called "old -> new", kept the quotes git puts
    #: around any path with a space or a non-ascii byte, and turned that path's octal escapes
    #: into slashes. So `git mv` on a file inside a claim's closure left this guard blind.
    return git_status.dirty_files(ROOT)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    import math                                                  # noqa: PLC0415
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - r) / d), 4), round(min(1.0, (c + r) / d), 4)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    existing = {c["id"] for c in manifest["claims"]}
    head = _git("rev-parse", "HEAD")
    code_time = int(_git("log", "-1", "--format=%ct", head))
    import produced_by as pb                                     # noqa: PLC0415
    dirty = _dirty_files()
    new: list[dict] = []

    def add(cid, statement, value, printed, unit, n, ci, pointer, *, dataset, env, command, raw,
            note=None, derivation=None, raw_gap=None):
        if cid in existing:
            return
        closure = pb.closure(command)
        bad = sorted(p for p in closure if p in dirty)
        if bad:
            raise SystemExit(f"working tree modifies {bad[0]} - commit first")
        c = {"id": cid, "statement": statement, "value": value, "printed": list(printed), "unit": unit,
             "n": n, "pointer": pointer, "dataset": dataset, "environment": env, "command": command,
             "raw": raw, "cited_in": [], "commit": head, "produced_by": closure,
             "ci": ({"method": "wilson", "level": 0.95, "low": ci[0], "high": ci[1]} if ci else None)}
        if note:
            c["note"] = note
        if derivation:
            c["derivation"] = derivation
        if raw_gap:
            c["raw_gap"] = raw_gap
        new.append(c)

    def fresh(raw: str) -> dict | None:
        p = ROOT / raw
        if not p.is_file():
            print(f"  {raw}: absent - re-run its command")
            return None
        if p.stat().st_mtime < code_time:
            print(f"  {raw}: predates HEAD - re-run its command before registering it")
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    # ── the judge: accuracy on pairs of known truth, and the price of a verdict ──────────────
    raw = "research/results/k8_judge_eval.json"
    art = fresh(raw)
    base = dict(dataset="supersession_v1", env="local_supersession_stand", command=JUDGE_CMD, raw=raw)
    if art:
        P = art["pooled"]
        n_scored, n_judged = int(P["n_scored"]), int(P["n_judged"])
        k_acc = int(round(P["accuracy"] * n_scored))
        add("absorb_judge.accuracy",
            f"the sleep-time judge rules correctly on {P['accuracy'] * 100:.1f}% of the {n_scored} same-slug "
            f"collision pairs whose truth the stand knows",
            P["accuracy"], [f"{P['accuracy']:.3f}", f"{P['accuracy']:.4f}"], "rate", n_scored,
            wilson(k_acc, n_scored), "pooled.accuracy",
            note=("Measured outside any session chain: the pairs are the collision record "
                  "`research/k8_collisions.py` made, so there is no warm-cache confound and no hook "
                  "millisecond in the number. A replacement or a restatement should read `replaces`, a "
                  "different fact `separate`; pairs whose truth is unclear are judged too and reported "
                  "apart in `pooled.unclear_verdicts`."), **base)
        for cls in ("replaces", "separate"):
            d = P[cls]
            tp, fp, fn = int(d["tp"]), int(d["fp"]), int(d["fn"])
            add(f"absorb_judge.{cls}_precision",
                f"of the pairs the judge calls `{cls}`, {d['precision'] * 100:.1f}% are {cls} in truth "
                f"({tp} of {tp + fp})",
                d["precision"], [f"{d['precision']:.3f}", f"{d['precision']:.4f}"], "rate", tp + fp,
                wilson(tp, tp + fp), f"pooled.{cls}.precision", **base)
            add(f"absorb_judge.{cls}_recall",
                f"the judge finds {d['recall'] * 100:.1f}% of the pairs that are {cls} in truth "
                f"({tp} of {tp + fn})",
                d["recall"], [f"{d['recall']:.3f}", f"{d['recall']:.4f}"], "rate", tp + fn,
                wilson(tp, tp + fn), f"pooled.{cls}.recall", **base)
        t = P["tokens_per_pair"]
        add("absorb_judge.tokens_per_pair",
            f"one verdict costs {t['mean']:.1f} model tokens on average ({t['prompt_mean']:.0f} of prompt, "
            f"{t['eval_mean']:.0f} of answer)",
            t["mean"], [f"{t['mean']:.1f}", f"{t['mean']:.0f}"], "tokens", n_judged, None,
            "pooled.tokens_per_pair.mean",
            note=("The whole price of layer 3: the judge runs once a contested pair at consolidation, "
                  "never in the hook. The engine charges this figure to a backend that reports no token "
                  "counts (`TOKENS_PER_PAIR_EST`)."), **base)
        add("absorb_judge.pairs_judged",
            f"the judge was run over all {n_judged} same-slug collision pairs the three stands recorded",
            n_judged, [str(n_judged)], "pairs", n_judged, None, "pooled.n_judged", **base)
        add("absorb_judge.seconds_per_pair",
            f"a verdict takes {P['seconds_per_pair']:.2f} seconds on a warm local model",
            P["seconds_per_pair"], [f"{P['seconds_per_pair']:.2f}"], "seconds", n_judged, None,
            "pooled.seconds_per_pair", **base)

    # ── the skeleton test: can similarity alone decide, before any model is called? ──────────
    raw = "research/results/k8_step0.json"
    art = fresh(raw)
    base = dict(dataset="supersession_v1", env="stdlib_only", command=SKELETON_CMD, raw=raw)
    if art:
        for stand, how in STANDS.items():
            d = (art.get("stands") or {}).get(stand)
            if not d:
                continue
            n = int(d["by_pair_truth"].get("replaces", 0)) + int(d["by_pair_truth"].get("separate", 0))
            for key, field, what in (
                    ("description", "plain:jaccard",
                     "the extractor's description text, stop words and boilerplate removed, stemmed"),
                    ("facts_block", "facts:jaccard",
                     "the verbatim `[facts]` block, where both notes carry one")):
                r = (d.get("results") or {}).get(field)
                if not r or r.get("auc_replaces_vs_separate") is None:
                    continue
                v = r["auc_replaces_vs_separate"]
                add(f"skeleton.{stand}.auc_{key}",
                    f"on {how}, telling a replacement from a different fact by overlap of {what} separates "
                    f"them with an AUC of {v:.3f}",
                    v, [f"{v:.3f}", f"{v:.2f}"], "AUC", n, None,
                    f'stands.{stand}.results["{field}"].auc_replaces_vs_separate',
                    note=("One named measure (Jaccard overlap of the skeletons), not the best of the five the "
                          "artifact computes: a maximum over measures is a choice, and the artifact carries "
                          "all five for anyone who wants the spread. This is why layer 1 decides by proof "
                          "rules and not by similarity - at this AUC a threshold that loses no still-true "
                          "fact also keeps most real replacements apart."), **base)

    # ── the vault: what the layer would cost on a real store, read-only ──────────────────────
    raw = "research/results/k8_vault_dryrun.json"
    art = fresh(raw)
    base = dict(dataset="owner_vault_snapshot", env="stdlib_only", command=VAULT_CMD, raw=raw)
    if art:
        weeks = int(art["weeks"])
        add("vault.contested_per_week",
            f"on a real store the sleep-time judge would see at most {art['contested_per_week']:.0f} contested "
            f"pairs a week - the upper bound, since a note written before the stamp existed cannot be told "
            f"from a slug retirement",
            art["contested_per_week"], [f"{art['contested_per_week']:.0f}", f"{art['contested_per_week']:.1f}"],
            "pairs a week", int(art["contested_total"]), None, "contested_per_week",
            note=(f"{art['collision_events']} same-slug collisions from other sessions in the {weeks} weeks to "
                  f"{art['today']}, over {art['notes_scanned']:,} typed notes. Rules 2 and 4 are inert on this "
                  f"store: {art['facts_seen']['notes_with_facts_block_in_window']} of "
                  f"{art['facts_seen']['notes_in_window']:,} notes written in the window carry a `[facts]` "
                  f"block, so every collision is a contested pair. Against the run's token budget this is "
                  f"headroom of about three times."),
            raw_gap="the vault is private, so the counts are published and the notes behind them are not",
            **base)
        add("vault.collision_events",
            f"the same dry run found {art['collision_events']} same-slug collisions in {weeks} weeks "
            f"({art['by_branch'].get('r', 0)} on the retirement branch, {art['by_branch'].get('d', 0)} on the "
            f"same-day absorb)",
            int(art["collision_events"]), [str(art["collision_events"])], "collisions",
            int(art["notes_scanned"]), None, "collision_events",
            raw_gap="the vault is private, so the counts are published and the notes behind them are not",
            **base)
        add("vault.notes_scanned",
            f"the dry run read {art['notes_scanned']:,} typed notes without writing one",
            int(art["notes_scanned"]), [f"{art['notes_scanned']:,}", str(art["notes_scanned"])], "notes",
            int(art["notes_scanned"]), None, "notes_scanned",
            note=("`tests/_test_k8_vault_dryrun_readonly.py` proves the word read-only: every write-capable "
                  "function of the hook is spied and the store fingerprinted, and three planted writes each "
                  "turn the check red."),
            raw_gap="the vault is private, so the counts are published and the notes behind them are not",
            **base)

    for c in new:
        print(f"  + {c['id']} = {c['value']}")
    if args.dry_run or not new:
        print(f"{'would register' if args.dry_run else 'registered'} {len(new)} claim(s)")
        return 0
    manifest["claims"].extend(new)
    Path(args.manifest).write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"registered {len(new)} claim(s) at {head[:7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
