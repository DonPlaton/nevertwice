#!/usr/bin/env python3
"""PREREG-V3 A9 (the auditor's Q-IND-1 = (a); the driver plan's A5, Q1-Q12 = (a)): the S1 pilot - stand S1 driven end to
end through run_v3._drive_stand for the pilot's measures, never a scored result (smoke loads, no scorer, the runs tree
only).

* s1_inputs: S1's smoke units (positions 481-500 of the committed ls1 order, loaders.lme_smoke_units), their cl100k
  tokens, each question with S1's template and its question_date (B1), the points each arm is read at - read_points
  through the PLAN's own points instance (R4: one instance decides what is read and what is forecast) - and each
  rendered-V arm's pinned template;
* run_pilot: the stand id S1-smoke-<n> from STATUS (Q8 = (a)); under <runs>/<stand id>/_pilot/<attempt>/ it writes
  reads.json (per arm and point), m33.json (a rendered V's lines against the cap, per arm, run and unit, and a stand
  line), m34.json (each store before and after the unit's reads), one artifact per point B, K and V through
  artifact.build on the BASE stand S1 (artifact.py reads Row V on base stands: the auditor's T-RV) and pilot.json. A
  pilot row is a partial arm_decl and the answer rows - ids, sha256s, counts, v_cut; never text; at V a V≡ arm is only
  its pointer and a refused one is left out, named in v_not_read. The output never lands in research/v3/results (R7).
  An unmeasurable M33 or M34 cell is a problem by name; a store that changed during the reads is a finding (M34: an E5
  line and a decision before the campaign), never folded into "unchanged".
* main (A6): `run_v3_pilot.py --stand S1 --arms a,b --runs r1,r2 --config <run config> [--mem0-probe-run <run>]` - S1
  alone; --mem0-probe-run checked before anything is read (C3), the run config read before the machine's contract
  (F4), the real deps through run_v3.cli_deps (M30: mem0's writer bound from its probe record).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
PILOT_STAND = "S1"
KIND = "_pilot"
RESULTS_DIR = HERE / "results"
SPLIT = "smoke:481-500"
POINT_ORDER = ("B", "K", "V")


def _rv():
    """run_v3, loaded once under its own name (the pilot drives its _drive_stand)."""
    name = "v3_run_v3_for_pilot"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, HERE / "run_v3.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def plan_points(PL: Any) -> Any:
    """The plan's own points instance (R4): the one stand_plan reads Row V through."""
    return PL._mod("v3_points_for_plan", "points.py")


def s1_inputs(deps: Any, *, stand_id: str, order: int, arm_names: Sequence[str], PL: Any) -> Any:
    """S1's StandInputs for the pilot (see the module docstring)."""
    RV = _rv()
    LD, PT = RV.load("loaders.py", smoke=True), plan_points(PL)
    ids = RV.s1_order(deps.lists_dir, expected_sha256=deps.s1_sha256)
    units = LD.lme_smoke_units(deps.lme_records(), ids)
    PL.check_ids((), [u.unit_id for u in units])
    if deps.stand_templates is None:
        raise RV.CLIError("the pilot needs S1's template (deps.stand_templates)")
    t1 = deps.stand_templates(PILOT_STAND)
    count, _cut = deps.cl100k

    def points(arm: str) -> tuple[str, ...]:
        return PT.read_points(stand_id, arm)
    for a in arm_names:                                  # a declared V≡ that does not hold refuses the pilot by name
        try:
            points(a)
        except PT.PointError as e:
            raise RV.CLIError(f"{stand_id}: the points of {a}: {e}") from None
    v_templates = {}
    for a in arm_names:
        if "V" in points(a) and (PT.V_DEFAULTS.get(a) or {}).get("shape") == "rendered":
            if deps.arm_template is None:
                raise RV.CLIError(f"{a}: its V context is rendered and the pilot has no arm_template to pin it")
            v_templates[a] = deps.arm_template(f"{a}:V")
    return RV.StandInputs(stand_id=stand_id, order=order, units=units, smaps={},
                          item_texts={u.unit_id: [i.text for s in u.sessions for i in s.items] for u in units},
                          unit_tokens=RV.unit_tokens(units, count), questions=RV.lme_questions_for(units, template=t1),
                          points=points, v_templates=v_templates)


def _cells(res: Mapping[str, Any]):
    """(arm, run, unit, the write stage's record, the question stage's record) over every block of the stand."""
    for b in res.get("blocks") or []:
        for arm, per in (b.get("write") or {}).items():
            for (run, unit), w in per.items():
                yield arm, run, unit, w, ((b.get("questions") or {}).get(arm) or {}).get((run, unit))


def _rows(q: Mapping[str, Any] | None) -> list:
    return [r for r in ((q or {}).get("reads") or []) if isinstance(r, Mapping)]


def reads_table(res: Mapping[str, Any]) -> dict:
    """{arm: {point: {reads, answered, unrecovered, last_cut}}}."""
    out: dict = {}
    for arm, _run, _unit, _w, q in _cells(res):
        for r in _rows(q):
            c = out.setdefault(arm, {}).setdefault(r.get("point"), {"reads": 0, "answered": 0, "unrecovered": 0,
                                                                   "last_cut": 0})
            c["reads"] += 1
            a = r.get("answer")
            if isinstance(a, Mapping):
                c["answered"] += 1
                c["last_cut"] += bool(a.get("last_cut"))
            if r.get("unrecovered"):
                c["unrecovered"] += 1
    return out


M33_SUM = ("facts", "facts_whole", "facts_partial", "facts_lost", "entities", "entities_whole", "entities_partial",
           "entities_lost")


def m33_table(res: Mapping[str, Any]) -> tuple[dict, list]:
    """M33: every rendered V answer's v_cut, per arm, run and unit, and a stand line per arm and run; an unmeasured
    v_cut is a problem by name, never counted as whole."""
    table: dict = {}
    problems: list[str] = []
    for arm, run, unit, _w, q in _cells(res):
        rows = [r for r in _rows(q) if (r.get("answer") or {}).get("shape") == "rendered"]
        if not rows:
            continue
        cell = {"v_reads": len(rows), "last_cut": 0, "unmeasured": 0, "tokens_full": [], **{k: 0 for k in M33_SUM}}
        for r in rows:
            a = r["answer"]
            cell["last_cut"] += bool(a.get("last_cut"))
            vc = a.get("v_cut")
            if isinstance(vc, Mapping):
                for k in M33_SUM:
                    cell[k] += int(vc.get(k) or 0)
                cell["tokens_full"].append(vc.get("tokens_full"))
            else:
                cell["unmeasured"] += 1
                problems.append(f"M33: {arm}/{run}/{unit}/{r.get('qid')}: {vc if vc is not None else 'no v_cut'}")
        cell["last_cut_share"] = cell["last_cut"] / cell["v_reads"]
        table.setdefault(arm, {}).setdefault(run, {})[unit] = cell
    for arm, runs in table.items():
        for run, units in runs.items():
            cells = [c for u, c in units.items() if u != "_stand"]
            stand = {k: sum(c[k] for c in cells) for k in ("v_reads", "last_cut", "unmeasured", *M33_SUM)}
            stand["last_cut_share"] = stand["last_cut"] / stand["v_reads"] if stand["v_reads"] else None
            units["_stand"] = stand
    return table, problems


def _seal_of(w: Any) -> Any:
    return w.get("seal") if isinstance(w, Mapping) else getattr(w, "seal", None)


def m34_table(res: Mapping[str, Any], *, plans: Mapping[str, Any], tree_manifest: Any) -> tuple[dict, list, list]:
    """M34: each (arm, run, unit)'s store before its reads - the write stage's seal - and after them: a directory store
    by the seal's own manifest function over its sealed path, a state store by the read stage's counters.store_state
    (the adapter's own digest function); a memory arm's store lives in its process (A8, deferred). Returns (the table,
    the findings - a store that changed - and the problems - a cell that could not be measured)."""
    table: dict = {}
    findings: list[str] = []
    problems: list[str] = []
    for arm, run, unit, w, q in _cells(res):
        who = f"{arm}/{run}/{unit}"
        seal = _seal_of(w)
        persistence = getattr(getattr(plans.get(arm), "spec", None), "store_persistence", None)
        reads = len(_rows(q))
        if persistence == "memory":
            cell: Any = "unmeasured: a memory arm's store lives in its process - A8 (deferred)"
        elif not isinstance(seal, Mapping):
            cell = "unmeasured: no write seal (the write stage did not seal its store)"
        elif seal.get("kind") == "state":
            after = ((q or {}).get("counters") or {}).get("store_state")
            if isinstance(after, Mapping) and isinstance(after.get("sha256"), str):
                cell = {"kind": "state", "reads": reads, "before": seal.get("sha256"), "after": after["sha256"],
                        "changed": after["sha256"] != seal.get("sha256"), "counts": after.get("counts")}
            else:
                cell = f"unmeasured: {after if isinstance(after, str) else 'no store_state in the read counters'}"
        elif isinstance(seal.get("manifest"), Mapping) and seal.get("store"):
            try:
                digest, man = tree_manifest(seal["store"])
            except OSError as e:
                cell = f"unmeasured: the sealed store cannot be read ({type(e).__name__})"
            else:
                was = seal["manifest"]
                changed = sorted(k for k in set(was) | set(man) if was.get(k) != man.get(k))
                cell = {"kind": "directory", "reads": reads, "before": seal.get("sha256"), "after": digest,
                        "changed": digest != seal.get("sha256"), "changed_paths": changed}
        else:
            cell = "unmeasured: the seal names neither a manifest nor a state"
        if isinstance(cell, str):
            problems.append(f"M34: {who}: {cell}")
        elif cell["changed"]:
            findings.append(f"M34: {who}: the store changed during the reads ({cell['kind']}: "
                            f"{cell.get('changed_paths') or 'digest'})")
        table.setdefault(arm, {}).setdefault(run, {})[unit] = cell
    return table, findings, problems


def _records_of(runs_root: Path, stand_id: str) -> dict:
    """{status_id: {path, sha256}} of the stand's run records (the scheduler's records.jsonl)."""
    out = {}
    p = Path(runs_root) / "_launch" / "records.jsonl"
    for line in (p.read_text(encoding="utf-8").split("\n") if p.is_file() else []):
        if line.strip():
            r = json.loads(line)
            if str(r.get("path", "")).startswith(f"{stand_id}/_records/"):
                out[r["status_id"]] = {"path": r["path"], "sha256": r["sha256"]}
    return out


def _answers(res: Mapping[str, Any], arm: str, point: str) -> list:
    return [r["answer"] for a, _run, _unit, _w, q in _cells(res) if a == arm for r in _rows(q)
            if r.get("point") == point and isinstance(r.get("answer"), Mapping)]


def pilot_artifacts(res: Mapping[str, Any], *, inp: Any, arms: Sequence[str], out_dir: Path, runs_root: Path,
                    lme_sha256: str | None, list_sha256: str, A: Any, PT: Any) -> tuple[dict, dict, list]:
    """One artifact per point through artifact.build on the base stand (see the module docstring). Returns ({point:
    {path, sha256}}, v_not_read, problems)."""
    out_dir = Path(out_dir)
    if RESULTS_DIR.resolve() in (out_dir.resolve(), *out_dir.resolve().parents):
        raise ValueError(f"{out_dir}: a pilot artifact never lands in research/v3/results (R7)")
    recs = _records_of(runs_root, inp.stand_id)
    man = {"dataset_sha256": lme_sha256 or "unknown", "list_sha256": list_sha256, "split": SPLIT}
    isha = A.sha256_of(man)
    tree = res.get("tree_start") or {}
    written, v_not_read, problems = {}, {}, []
    for point in POINT_ORDER:
        rows, sids = {}, []
        for arm in arms:
            if point in inp.points(arm):
                arm_sids = sorted(s for s, r in recs.items() if r["path"].endswith(f".{arm}.json"))
                answers = _answers(res, arm, point)
                rows[arm] = {"arm_decl": {"system": arm, "point": point, "tier": "product", "input_sha256": isha,
                                          "pilot": True},
                             "runs": [{"status_id": s} for s in arm_sids], "answers": answers,
                             "questions": [{"qid": a.get("qid"), "invalid": None} for a in answers]}
                sids += arm_sids
            elif point == "V":
                vr = PT.v_row(arm)
                if "equivalent_to" in vr:
                    rows[arm] = {"equivalent_to": vr["equivalent_to"]}
                else:
                    v_not_read[arm] = vr
        if not sids:
            continue
        try:
            doc = A.build(stand=PILOT_STAND, point=point, tier="product", arms=rows, brackets=None,
                          input_manifest=man, model_version={"response_model": "the STATUS START line's model",
                                                             "changelog_newest": "unread"},
                          commit=str(tree.get("anchor") or ""), dirty=not bool(tree.get("clean")),
                          status_ids=sorted(set(sids)), run_files={s: recs[s] for s in sorted(set(sids))},
                          declared_axes=list(A.DECLARED_AXES[point]))
            path = A.write(doc, out_dir / "artifacts")
            written[point] = {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
        except Exception as e:  # noqa: BLE001 - a pilot artifact that cannot be built is a problem by name
            problems.append(f"artifact {point}: {type(e).__name__}: {e}")
    return written, v_not_read, problems


def run_pilot(cfg: Any, *, arm_names: Sequence[str], runs: Sequence[str], deps: Any) -> int:
    """The S1 pilot (see the module docstring): 0 only when the stand completed clean and every M33 and M34 cell and
    every artifact was measured."""
    RV = _rv()
    unknown = sorted(set(arm_names) - set(cfg.arms))
    if unknown or not arm_names:
        raise RV.CLIError(f"arms {unknown or list(arm_names)} are not in the run config's arms {sorted(cfg.arms)}")
    arms = {a: cfg.arms[a] for a in arm_names}
    PL, A = RV.load("run_v3_plan.py", smoke=True), RV.load("artifact.py", smoke=True)
    PT = plan_points(PL)
    base = RV.load("arms/base.py", smoke=True, name="v3_arm_base_for_pilot")
    PL.check_ids(runs, ())
    stand_id, n = RV.next_smoke_id(deps.status_path, PILOT_STAND)
    inp = s1_inputs(deps, stand_id=stand_id, order=n, arm_names=list(arms), PL=PL)

    def post(*, res, log, answer, run_dir, out_dir, plans):
        reads = reads_table(res)
        m33, p33 = m33_table(res)
        m34, findings, p34 = m34_table(res, plans=plans, tree_manifest=base.tree_manifest)
        arts, v_not_read, pa = pilot_artifacts(res, inp=inp, arms=list(arms), out_dir=out_dir,
                                               runs_root=deps.contract.runs_root, lme_sha256=deps.lme_sha256,
                                               list_sha256=deps.s1_sha256, A=A, PT=PT)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, obj in (("reads.json", reads), ("m33.json", m33), ("m34.json", m34)):
            (out_dir / name).write_bytes(A.render(obj))
        pilot = {"stand": stand_id, "arms": sorted(arms), "runs": list(runs),
                 "points": {a: list(inp.points(a)) for a in arms}, "v_not_read": v_not_read, "artifacts": arts,
                 "findings": findings, "problems": p33 + p34 + pa,
                 "launch": {a: (pl.record() if hasattr(pl, "record") else {"arm": a}) for a, pl in plans.items()}}
        (out_dir / "pilot.json").write_bytes(A.render(pilot))
        for f in findings:
            deps.out(f"finding: {f}")
        return [f"pilot: {p}" for p in pilot["problems"]]
    return RV._drive_stand(cfg, inp, arms, runs, deps, kind=KIND, post=post)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="run_v3_pilot.py", description="PREREG-V3 A9: the S1 pilot")
    ap.add_argument("--stand", required=True)
    ap.add_argument("--arms", required=True)
    ap.add_argument("--runs", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--mem0-probe-run", help="the mem0 probe run whose record bounds mem0's writer (Q-C6-5)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    RV = _rv()
    args = parser().parse_args(argv)
    if args.stand != PILOT_STAND:
        raise RV.CLIError(f"a pilot of {args.stand} is not possible - the A9 pilot reads {PILOT_STAND} alone")
    arm_names = [a for a in args.arms.split(",") if a]
    RV.check_mem0_probe_run(arm_names, args.mem0_probe_run)       # before anything is read (C3)
    raw = RV.read_run_config(args.config)                          # F4: before the machine's contract
    c = RV.load("launch.py", smoke=True).Contract.default()
    cfg = RV.load_run_config(args.config, secrets_dir=c.secrets_dir, raw=raw)
    return run_pilot(cfg, arm_names=arm_names, runs=[r for r in args.runs.split(",") if r],
                     deps=RV.cli_deps(c, cfg, arm_names, args.mem0_probe_run))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except _rv().CLIError as e:
        sys.stderr.write(f"refused: {e}\n")
        sys.exit(2)
