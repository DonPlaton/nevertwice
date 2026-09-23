#!/usr/bin/env python3
"""A9 (Q5): generate `research/data/cross_project_v1.json` - 100 cases for
`research/cross_project_bench.py`, the stand that measures whether the principle layer (A1/A3
write side, A4 universal-mode recall, A5 the promoter) actually keeps an identifier out of a
DIFFERENT project's memory while still letting the underlying lesson travel.

Each case has three projects and a decoy:
  - project_a, project_c: two DIFFERENT projects that independently learned the SAME
    underlying rule (reused from research/data/principle_twins_v1.json's 40 positive
    paraphrase pairs, one phrasing per project). Each side carries its own planted
    identifiers of all four classes the plan names (IP, host, path, entity) TWICE, in two
    shapes: a short synthetic SESSION TRANSCRIPT ('session', ~1-2 kB, an IP in a log line, a
    hostname in a command, a path in a traceback, an entity/product name in the prose - what
    `--extract` mode feeds to the real extractor) and a one-line raw 'description' (the
    cheaper `--oracle-principles` control's note body). Each side's `principle` field is the
    clean, already de-identified paraphrase - the ground truth `--oracle-principles` writes
    directly, and what `--extract` asks a real model to reproduce on its own.
  - project_b: the TARGET - a prompt about to repeat the same mistake/pattern, phrased around
    the topic without stating the rule outright (a real user's prompt, not a lesson).
  - a distractor project with an unrelated rule (a different topic entirely), clean, no
    planted identifiers - noise the bench's arms must not surface as if it were relevant.

Deterministic (seeded by case index), so the same dataset regenerates identically:

    python research/gen_cross_project_dataset.py            # writes data/cross_project_v1.json
    python research/gen_cross_project_dataset.py --check     # validates an existing file's shape
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

TWINS_PATH = HERE / "data" / "principle_twins_v1.json"
OUT_PATH = HERE / "data" / "cross_project_v1.json"
N_CASES = 100
IDENTIFIER_CLASSES = ("ip", "host", "path", "entity")


def _planted(case_i: int, project_letter: str) -> dict:
    """One value per identifier class, deterministic and DISTINCT between project_a and
    project_c of the same case - two different projects, two different concrete identifiers,
    same underlying rule."""
    # project_letter folds into every value so A and C never collide within a case, and
    # case_i folds in so no two cases share an identifier either (a leak test must not pass
    # by accident because two cases happened to plant the same string).
    salt = (case_i * 7 + (1 if project_letter == "a" else 5)) % 250
    o2, o3, o4 = (salt + 10) % 256, (salt * 3 + 20) % 256, (salt * 5 + 30) % 256
    return {
        "ip": f"10.{o2}.{o3}.{o4}",
        "host": f"svc-{project_letter}{case_i:03d}.internal.example",
        "path": f"/var/lib/app-{project_letter}{case_i:03d}/state.db",
        "entity": f"queue-shard-{project_letter}{case_i:03d}",
    }


def _raw_description(phrasing: str, planted: dict) -> str:
    """The shape a real extractor's raw `description` field would take BEFORE any
    de-identification - the planted identifiers are exactly what `principle_scan` exists to
    strip from the `principle` field, and exactly what SHOULD still leak through in `all`
    mode's cross-project display (via `_note_snippet`), since `description` is never scanned."""
    return (f"Learned this on {planted['host']} ({planted['ip']}): {phrasing} Traced through "
           f"{planted['path']}, entity {planted['entity']}.")


def _synthetic_session(topic: str, phrasing: str, planted: dict) -> str:
    """A short (~1-2 kB) synthetic session transcript for `--extract` mode: the shared rule is
    LEARNED IN CONTEXT (a flaky failure gets diagnosed) rather than just stated, so a real
    extractor has to actually distil it, not copy a given sentence - and each identifier class
    sits in the natural place the plan names: an IP in a log line, a hostname in a command, a
    path in a traceback, an entity/product name in the prose. `_raw_description` above plants
    the same four values in one compact sentence for the (cheaper) oracle/description path;
    this is the same identifiers, spread across a believable dialogue for the extraction path."""
    return (
        f"User: we keep hitting a flaky failure around {topic.replace('-', ' ')} - can you dig "
        f"into it with me?\n"
        f"Assistant: pulling the logs first.\n"
        f"  [2026-09-01 10:14:02] connect timeout to {planted['ip']}:5432 after 3 retries\n"
        f"Assistant: that host is still up, so let's reproduce it directly:\n"
        f"  $ ssh {planted['host']} 'systemctl status app'\n"
        f"  active (running) - not the host itself, then.\n"
        f"Assistant: here's the traceback from the failing run:\n"
        f"  Traceback (most recent call last):\n"
        f'    File "{planted["path"]}", line 42, in run_check\n'
        f"      raise RuntimeError('{planted['entity']} health check failed')\n"
        f"Assistant: found it. {phrasing}\n"
        f"User: good catch - let's make sure this does not bite us again on the next pass.\n"
        f"Assistant: agreed, writing it down as a lesson so it is not relearned the hard way.\n"
    )


def generate(n_cases: int = N_CASES) -> dict:
    twins = json.loads(TWINS_PATH.read_text(encoding="utf-8"))
    positives = twins["positives"]
    if not positives:
        raise ValueError(f"{TWINS_PATH}: no positive pairs to draw rules from")

    cases = []
    for i in range(n_cases):
        rule_idx = i % len(positives)
        variant = i // len(positives)          # 0, 1, 2, ... - which repeat of this rule
        rule = positives[rule_idx]
        distractor_idx = (rule_idx + 1 + (i % (len(positives) - 1))) % len(positives)
        distractor_rule = positives[distractor_idx]

        case_id = f"cpv1-{i + 1:03d}"
        proj_a = f"cpv1_{i + 1:03d}_alpha"
        proj_c = f"cpv1_{i + 1:03d}_gamma"
        proj_b = f"cpv1_{i + 1:03d}_beta"
        proj_d = f"cpv1_{i + 1:03d}_delta"

        planted_a = _planted(i, "a")
        planted_c = _planted(i, "c")

        cases.append({
            "id": case_id,
            "topic": rule["topic"],
            "variant": variant,
            "project_a": {
                "project": proj_a,
                "title": f"{rule['topic']} - {case_id}",
                "description": _raw_description(rule["a"], planted_a),
                "principle": rule["a"],
                "planted": planted_a,
                "session": _synthetic_session(rule["topic"], rule["a"], planted_a),
            },
            "project_c": {
                "project": proj_c,
                "title": f"{rule['topic']} - {case_id}",
                "description": _raw_description(rule["b"], planted_c),
                "principle": rule["b"],
                "planted": planted_c,
                "session": _synthetic_session(rule["topic"], rule["b"], planted_c),
            },
            "project_b": {
                "project": proj_b,
                "prompt": f"About to touch the {rule['topic'].replace('-', ' ')} path again - "
                         f"anything I should watch out for before I ship this?",
            },
            "distractor": {
                "project": proj_d,
                "title": f"{distractor_rule['topic']} - unrelated",
                "description": distractor_rule["a"],
                "principle": distractor_rule["a"],
            },
        })
    return {
        "_schema": "A9 (Q5) cross-project recall stand. Each case: project_a and project_c "
                   "independently learned the SAME rule (from principle_twins_v1.json), each "
                   "with its OWN planted identifiers (ip/host/path/entity) woven into a short "
                   "synthetic session transcript ('session', for --extract's real extraction "
                   "pass) and into a one-line raw description ('description', for the cheaper "
                   "oracle-principles path) - never scanned - plus a clean, already de-"
                   "identified 'principle' ground truth (--oracle-principles only; --extract "
                   "asks a real model to produce its own). project_b is the target with a "
                   "topically-related prompt. distractor is an unrelated rule with no planted "
                   "identifiers, noise for the ranking.",
        "identifier_classes": list(IDENTIFIER_CLASSES),
        "n_cases": len(cases),
        "cases": cases,
    }


def validate(data: dict) -> list[str]:
    problems: list[str] = []
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        return ["no cases"]
    ids = set()
    for i, c in enumerate(cases):
        for key in ("project_a", "project_c", "project_b", "distractor"):
            if key not in c:
                problems.append(f"case {i}: missing {key!r}")
        cid = c.get("id")
        if not cid:
            problems.append(f"case {i}: missing id")
        elif cid in ids:
            problems.append(f"case {i}: duplicate id {cid!r}")
        else:
            ids.add(cid)
        for side in ("project_a", "project_c"):
            row = c.get(side) or {}
            planted = row.get("planted") or {}
            for cls in IDENTIFIER_CLASSES:
                if not planted.get(cls):
                    problems.append(f"case {i} {side}: no planted {cls!r}")
            principle = row.get("principle", "")
            for cls, val in planted.items():
                if val and val in principle:
                    problems.append(f"case {i} {side}: planted {cls!r} leaked into 'principle' "
                                    f"itself - the fixture is supposed to keep it clean")
            session = row.get("session", "")
            if not isinstance(session, str) or not session.strip():
                problems.append(f"case {i} {side}: missing 'session' transcript")
                continue
            if not (500 <= len(session) <= 2500):
                problems.append(f"case {i} {side}: session is {len(session)} chars, "
                                f"want roughly 1-2 kB")
            for cls, val in planted.items():
                if val and val not in session:
                    problems.append(f"case {i} {side}: planted {cls!r} ({val!r}) is missing "
                                    f"from its own session transcript")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(OUT_PATH), help="output path (default: %(default)s)")
    parser.add_argument("--n", type=int, default=N_CASES, help="number of cases (default: %(default)s)")
    parser.add_argument("--check", action="store_true",
                        help="validate an existing dataset file instead of generating one")
    args = parser.parse_args(argv)

    if args.check:
        data = json.loads(Path(args.out).read_text(encoding="utf-8"))
        problems = validate(data)
        print(f"[gen_cross_project_dataset] {data.get('n_cases', 0)} case(s) in {args.out}")
        if problems:
            print(f"[gen_cross_project_dataset] {len(problems)} problem(s):")
            for p in problems[:20]:
                print(f"  - {p}")
            return 1
        print("[gen_cross_project_dataset] shape OK")
        return 0

    data = generate(args.n)
    problems = validate(data)
    if problems:
        print(f"[gen_cross_project_dataset] refusing to write - {len(problems)} problem(s):",
             file=sys.stderr)
        for p in problems[:20]:
            print(f"  - {p}", file=sys.stderr)
        return 1
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8", newline="")
    print(f"[gen_cross_project_dataset] wrote {data['n_cases']} case(s) to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
