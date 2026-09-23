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

# ── the identifier-bound population (Q5 G5.1, registered .loop/PREREG-Q5-IDENTIFIER-BOUND-
# 2026-09-23.md BEFORE any number from it): the first real --extract run (24c0af4) showed the
# extractor writes DESCRIPTIONS without the planted identifiers, so the `all` arm's production
# channel had nothing to carry and G5.1's positive control could not fire by construction (H5).
# Here the identifier IS the lesson - the rule cannot be stated without it - so a faithful
# extractor keeps it in title/description, and `all` has something real to leak.
IB_OUT_PATH = HERE / "data" / "cross_project_ib_v1.json"
IB_N_CASES = 50
IB_CLASS_COUNTS = {"ip": 13, "host": 13, "path": 12, "entity": 12}         # sums to 50, per PREREG
def _ib_planted(i: int, letter: str, cls: str) -> str:
    """One identifier for identifier-bound case `i`'s `letter` side, class `cls` - guaranteed
    DISTINCT from the original 100-case population's `_planted()` by construction, not by an
    index shift into the same derivation. Two shift attempts were tried and both failed
    empirically (found generating this file, 2026-09-24, neither ever shipped): `_planted`'s ip
    salt is `case_i % 250`, and the original population's 100 cases x 2 letters already occupy
    close to that whole space, so no 50-wide shifted window of case indices avoids every ip
    collision - a multiple of 250 (1000) reproduced 26 values one for one; a non-multiple (150,
    chosen to dodge the SAME-letter collision specifically) still left 13 CROSS-letter
    collisions, because the salt formula's two letter constants (`a`=1, `c`=5) create their own
    forbidden residues no single offset avoids alongside the same-letter one.

    So: no shift, no shared derivation. `ip` uses `172.16.` - `_planted` always hardcodes `10.`
    as the first octet, so this cannot coincide with any `_planted()` output for ANY case index,
    not just this population's own range. `host`/`path`/`entity` carry an `ib` marker `_planted`
    never emits (its own format is `svc-{letter}{i:03d}...`, never `svc-ib{letter}{i:03d}...`) -
    a structural difference, not a numeric one, so it needs no collision search at all."""
    n = f"{i:03d}"
    if cls == "ip":
        o2 = 11 if letter == "a" else 22
        return f"172.16.{o2}.{i % 256}"
    if cls == "host":
        return f"svc-ib{letter}{n}.internal.example"
    if cls == "path":
        return f"/var/lib/app-ib{letter}{n}/state.db"
    return f"queue-shard-ib{letter}{n}"

_IB_RULE = {
    "ip": "the replica at {val} lags behind after a deploy; read from the primary until it "
         "catches up",
    "host": "{val} must be drained before every deploy, or in-flight requests get dropped",
    "path": "never write under {val} while the migrator is running, or the state file corrupts",
    "entity": "{val} silently drops messages over 4 MB; split large payloads before publishing",
}
_IB_B_PROMPT = {
    "ip": "One of our replicas seems to be lagging right after a deploy - should I read from "
         "it or go straight to primary?",
    "host": "About to kick off a deploy - is there a service that needs draining first?",
    "path": "The migrator is running right now - is it safe to write to the state file?",
    "entity": "I need to publish a larger payload to the queue - anything that could drop it "
             "silently?",
}
_IB_INVESTIGATION = {
    "ip": "  [2026-09-24 09:02:11] read latency spike traced to replica {val}\n"
         "Assistant: that replica is still catching up on WAL replay from the last deploy.\n",
    "host": "  $ ssh {val} 'systemctl status app'\n"
           "  active (running), but mid-drain from the last rollout - requests are being "
           "dropped.\n",
    "path": "  Traceback (most recent call last):\n"
           '    File "{val}", line 88, in apply_migration\n'
           "      raise OSError('state file busy - migrator holds the lock')\n",
    "entity": "Assistant: the publisher logs show {val} silently truncating anything over "
             "4 MB.\n",
}


def _ib_class_for_index(i: int) -> str:
    """1-based case index -> identifier class, in the exact counts the registration fixes: 13
    ip, 13 host, 12 path, 12 entity (.loop/PREREG-Q5-IDENTIFIER-BOUND-2026-09-23.md)."""
    if i <= 13:
        return "ip"
    if i <= 26:
        return "host"
    if i <= 38:
        return "path"
    return "entity"


def _ib_session(cls: str, val: str) -> str:
    """A short synthetic transcript where the identifier is woven into the INVESTIGATION and
    the closing lesson line states the rule WITH the identifier in it - unlike the original
    100-case population's `_synthetic_session`, where the stated lesson (`phrasing`) never
    contains the identifier at all. Here it must: the identifier IS the lesson."""
    rule = _IB_RULE[cls].format(val=val)
    inv = _IB_INVESTIGATION[cls].format(val=val)
    return (
        "User: we keep running into trouble around this - can you help me get to the bottom "
        "of it?\n"
        "Assistant: let me check what's going on.\n"
        f"{inv}"
        f"Assistant: found it. {rule}\n"
        "User: good catch - let's write that down so we don't relearn it the hard way.\n"
        "Assistant: agreed, noting it now.\n"
    )


def generate_identifier_bound(n_cases: int = IB_N_CASES) -> dict:
    """50 new cases, ids `cpv1-ib-001`..`-050`, generated deterministically beside the existing
    100 (unchanged) - per .loop/PREREG-Q5-IDENTIFIER-BOUND-2026-09-23.md, registered before any
    number from this population. One identifier PER CASE (not all four); project_a and
    project_c each get their own spelling of the same class; project_b's prompt is about the
    same situation without the identifier."""
    twins = json.loads(TWINS_PATH.read_text(encoding="utf-8"))["positives"]
    if not twins:
        raise ValueError(f"{TWINS_PATH}: no positive pairs to draw a distractor rule from")

    cases = []
    for i in range(1, n_cases + 1):
        cls = _ib_class_for_index(i)
        case_id = f"cpv1-ib-{i:03d}"
        proj_a, proj_c = f"cpv1_ib_{i:03d}_alpha", f"cpv1_ib_{i:03d}_gamma"
        proj_b, proj_d = f"cpv1_ib_{i:03d}_beta", f"cpv1_ib_{i:03d}_delta"

        val_a = _ib_planted(i, "a", cls)
        val_c = _ib_planted(i, "c", cls)
        # NOT `.capitalize()`: the identifier is the FIRST token of the "host"/"entity"
        # templates, and `str.capitalize()` lowercases the rest of the string too - on those
        # two classes it silently mutated the planted identifier's own case ("svc-a014..." ->
        # "Svc-a014...") and the exact-substring check below then reported it missing (found
        # while generating this population, 2026-09-24 - never shipped). A fixed capitalized
        # lead-in sidesteps the whole class of bug instead of special-casing which template
        # starts with `{val}`.
        rule_a = f"Lesson: {_IB_RULE[cls].format(val=val_a)}."
        rule_c = f"Lesson: {_IB_RULE[cls].format(val=val_c)}."
        distractor_rule = twins[(i - 1) % len(twins)]

        cases.append({
            "id": case_id,
            "topic": f"identifier-bound-{cls}",
            "identifier_class": cls,
            "project_a": {
                "project": proj_a, "title": f"identifier-bound {cls} - {case_id} - a",
                "description": rule_a, "principle": rule_a, "planted": {cls: val_a},
                "session": _ib_session(cls, val_a),
            },
            "project_c": {
                "project": proj_c, "title": f"identifier-bound {cls} - {case_id} - c",
                "description": rule_c, "principle": rule_c, "planted": {cls: val_c},
                "session": _ib_session(cls, val_c),
            },
            "project_b": {"project": proj_b, "prompt": _IB_B_PROMPT[cls]},
            "distractor": {"project": proj_d,
                          "title": f"{distractor_rule['topic']} - unrelated",
                          "description": distractor_rule["a"],
                          "principle": distractor_rule["a"]},
        })
    return {
        "_schema": "Q5 G5.1 identifier-bound population (.loop/PREREG-Q5-IDENTIFIER-BOUND-"
                   "2026-09-23.md): 50 cases, one planted identifier class per case, the "
                   "identifier IS the lesson - a faithful extractor cannot state the rule "
                   "without it, so the `all` arm's PRODUCTION channel (title/description) has "
                   "something real to carry. project_a and project_c each get their OWN "
                   "spelling of a same-class identifier. project_b's prompt is the same "
                   "situation without the identifier. Kept beside, not replacing, the original "
                   "100-case (de-identified) population.",
        "identifier_classes": list(IDENTIFIER_CLASSES),
        "class_counts": IB_CLASS_COUNTS,
        "n_cases": len(cases),
        "cases": cases,
    }


def validate_identifier_bound(data: dict) -> list[str]:
    problems: list[str] = []
    cases = data.get("cases")
    if not isinstance(cases, list) or len(cases) != IB_N_CASES:
        return [f"expected {IB_N_CASES} cases, got "
               f"{len(cases) if isinstance(cases, list) else 0!r}"]
    ids: set = set()
    class_counts = {cls: 0 for cls in IDENTIFIER_CLASSES}
    for i, c in enumerate(cases):
        for key in ("project_a", "project_c", "project_b", "distractor"):
            if key not in c:
                problems.append(f"case {i}: missing {key!r}")
        cid = c.get("id")
        if not cid or not str(cid).startswith("cpv1-ib-"):
            problems.append(f"case {i}: id {cid!r} does not match cpv1-ib-NNN")
        elif cid in ids:
            problems.append(f"case {i}: duplicate id {cid!r}")
        else:
            ids.add(cid)
        cls = c.get("identifier_class")
        if cls not in IDENTIFIER_CLASSES:
            problems.append(f"case {i}: bad identifier_class {cls!r}")
            continue
        class_counts[cls] += 1
        vals = set()
        for side in ("project_a", "project_c"):
            row = c.get(side) or {}
            planted = row.get("planted") or {}
            if set(planted) != {cls}:
                problems.append(f"case {i} {side}: planted must carry EXACTLY {{{cls!r}}} "
                                f"(one identifier per case), got {sorted(planted)}")
            val = planted.get(cls)
            if not val:
                problems.append(f"case {i} {side}: no planted {cls!r}")
                continue
            vals.add(val)
            # Inverted from the original population's check: here the identifier MUST survive
            # into 'principle' (the rule cannot be stated without it), not be kept out of it.
            principle = row.get("principle", "")
            if val not in principle:
                problems.append(f"case {i} {side}: planted {cls!r} ({val!r}) is missing from "
                                f"'principle' - the rule cannot be stated without it")
            description = row.get("description", "")
            if val not in description:
                problems.append(f"case {i} {side}: planted {cls!r} ({val!r}) is missing from "
                                f"'description'")
            session = row.get("session", "")
            if val not in session:
                problems.append(f"case {i} {side}: planted {cls!r} ({val!r}) is missing from "
                                f"'session'")
        if len(vals) < 2:
            problems.append(f"case {i}: project_a and project_c must use DIFFERENT spellings "
                            f"of the {cls!r} identifier (own spelling per project), got {vals}")
        prompt = ((c.get("project_b") or {}).get("prompt") or "")
        for side in ("project_a", "project_c"):
            val = ((c.get(side) or {}).get("planted") or {}).get(cls)
            if val and val in prompt:
                problems.append(f"case {i}: project_b's prompt must be WITHOUT the identifier, "
                                f"found {val!r} in it")
    for cls, want in IB_CLASS_COUNTS.items():
        if class_counts[cls] != want:
            problems.append(f"class {cls!r}: expected {want} case(s), got {class_counts[cls]}")
    return problems


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
    parser.add_argument("--out", default="", help="output path (default depends on --identifier-bound)")
    parser.add_argument("--n", type=int, default=0, help="number of cases (default depends on "
                        "--identifier-bound)")
    parser.add_argument("--identifier-bound", action="store_true",
                        help="generate/check the 50-case identifier-bound population "
                             "(cross_project_ib_v1.json) instead of the original 100")
    parser.add_argument("--check", action="store_true",
                        help="validate an existing dataset file instead of generating one")
    args = parser.parse_args(argv)

    if args.identifier_bound:
        out_path = Path(args.out) if args.out else IB_OUT_PATH
        n = args.n or IB_N_CASES
        gen_fn, val_fn = generate_identifier_bound, validate_identifier_bound
    else:
        out_path = Path(args.out) if args.out else OUT_PATH
        n = args.n or N_CASES
        gen_fn, val_fn = generate, validate

    if args.check:
        data = json.loads(out_path.read_text(encoding="utf-8"))
        problems = val_fn(data)
        print(f"[gen_cross_project_dataset] {data.get('n_cases', 0)} case(s) in {out_path}")
        if problems:
            print(f"[gen_cross_project_dataset] {len(problems)} problem(s):")
            for p in problems[:20]:
                print(f"  - {p}")
            return 1
        print("[gen_cross_project_dataset] shape OK")
        return 0

    data = gen_fn(n)
    problems = val_fn(data)
    if problems:
        print(f"[gen_cross_project_dataset] refusing to write - {len(problems)} problem(s):",
             file=sys.stderr)
        for p in problems[:20]:
            print(f"  - {p}", file=sys.stderr)
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8", newline="")
    print(f"[gen_cross_project_dataset] wrote {data['n_cases']} case(s) to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
