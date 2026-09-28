#!/usr/bin/env python3
"""PREREG-V3 §4.6 (K87, C12 [RULING]), TB4.13: the recording-vs-raw-forward A/B - two raw-forward runs and two
recording runs of each A/B arm on the first 3 smoke haystacks, decided by ab_rule.ab_verdict. It runs before the
anchor and blocks it; its runs are tagged debug.

The auditor's rulings (TB4.13, 2026-09-28), as this module implements them:

* D-AB-1: a module of its own - run_v3.run_smoke is not touched; its parts are reused (s1_order, s4_smoke_units,
  unit_tokens, questions_for, boundary_canaries, preflight, forecast, WallCapGate, SmokeDeps).
* D-AB-2: the units are the first AB_UNITS units of the S4 smoke (the S1 order's positions 481-483), computed once;
  every leg records its unit ids and input sha256s, and no verdict is made unless the four legs name the same.
* D-AB-3, Q-AB-5 O-a: ONE proxy process for the four runs, legs in ABBA order (LEGS); each run is its own debug stand
  "<stand>-ab-<n>-<leg>", its run id the leg's name. Each product arm with a write port has a raw twin
  "<arm>-abraw" (ab_proxy_config): the same ArmConfig but for its name and mode (and, at run time, its token and
  ports). A recording leg's children speak to the arm's ports, a raw leg's to its twin's - through LegView, so the
  plan launcher, the answerer and the scheduler see the same arm name in all four legs (Q-AB-7); the mapping is in
  the record.
* D-AB-5, Q-AB-1, Q-AB-4: every leg's metrics come from ONE function (unit_metrics) over the scheduler's unit records,
  never from the proxy (a raw leg writes no calls.jsonl): the product's logical LLM calls and tokens as the adapter
  counts them in the write and question stages (ARM_SOURCES: mem0's LLMUsage, our engine's _LLM_STATS) plus the
  reader's calls and usage as the answerer records them; items = the end_write footprint's retrievable count; wall =
  the write and question stages' active seconds; lost_share = accounting.lost_operations with no proxy calls - the
  adapter's view on both legs (the recording legs' full P1 is not in the verdict). Per run: the mean over the units.
* Q-AB-2: the added time to first byte is the recording legs' own-hop samples over the median of the raw legs'
  (ab_rule.added_over), from the proxy's in-memory own_hop_ms; hop_benchmark (a local echo) is recorded beside it.
* D-AB-4: the fallback checks run over all four legs in one loop - a local generation call (the adapter's, or the
  Ollama leg's fallback_local) on any leg, or the declared thinking field injected on some legs only, refuses the
  verdict by name.
* T6 / Q-AB-6 O-a: a raw twin leaves no calls.jsonl line, no bodies/<twin>/ and no flags.jsonl line; its Ollama leg's
  ollama.jsonl lines (written the same in both modes) are counted and named in the record.
* Q-AB-3: the preflight carries the forecast of every A/B (run_v3.forecast, which refuses an arm without a
  WRITER_BOUNDS entry - no DeepSeek spend without a forecast, on a debug run too).
* D-AB-8: no incident gate - a raw leg's calls are not recorded, so a gate reading calls.jsonl would see the recording
  legs only; an upstream failure shows in the metrics of the leg it hit. But §4.5's stops act the same on every leg:
  a 401, 402 or 403 on any ArmConfig (the proxy's in-memory upstream_statuses, kept in both modes) stops the A/B by
  name - StopGate refuses the next unit, no later leg runs, exit EXIT_STOP - and every later A/B is refused until
  OWNER_FILE is written beside the stopped one's ab.json (stopped_without_owner): the owner's word, never a repeat.
  exit_code() adds no allowance of any kind.

The record: <runs>/_ab/<stand>-ab-<n>/ab.json (written once): the arms, the units, the legs in order with their
times, stands, mappings, per-unit and per-run metrics (each metric's source named), fallback counts, the thinking
field's count, client_abandoned, the own-hop sample counts, the twins' Ollama lines; the proxy config's sha256 and
each twin's config difference; hop_benchmark; each arm's verdict; every problem. Exit 0 only when every arm is in
tolerance and nothing is wrong.
"""
from __future__ import annotations

import hashlib
import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
AB_RAW_SUFFIX = "-abraw"
LEGS = ("raw1", "rec1", "rec2", "raw2")          # ABBA (D-AB-3): a steady drift stays inside the raw range
AB_UNITS = 3                                     # §4.6: "on the first 3 smoke haystacks"
AB_TAG = "debug"
METRICS = ("calls", "tokens_in", "tokens_out", "lost_share", "items", "wall_s")
STOP_STATUSES = ("401", "402", "403")           # §4.5's stops: the key, the balance, the account
OWNER_FILE = "owner.json"                       # beside a stopped A/B's ab.json: the owner's word that it may go on
EXIT_STOP = 3                                   # a stop: the owner's to resolve, never a repeat


class ABError(ValueError):
    """An A/B the rulings do not allow, or a leg whose numbers cannot be compared; the reason is named."""


def _rv():
    import importlib.util  # noqa: PLC0415
    import sys  # noqa: PLC0415
    mod = sys.modules.get("v3_run_v3_for_ab")
    if mod is None:
        spec = importlib.util.spec_from_file_location("v3_run_v3_for_ab", HERE / "run_v3.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["v3_run_v3_for_ab"] = mod
        spec.loader.exec_module(mod)
    return mod


def twin(arm: str) -> str:
    return arm + AB_RAW_SUFFIX


# ── the proxy: one process, a raw twin per product arm (Q-AB-5) ────────────────────────────────────────────────

def ab_proxy_config(config: Mapping[str, Any], arms: Sequence[str]) -> tuple[dict, dict[str, str]]:
    """(the config with a raw twin appended for each of ``arms`` that has a write port, {arm: its twin}). A twin is
    its arm's entry with only "arm" and "mode" changed."""
    out = json.loads(json.dumps(dict(config)))
    names = {a["arm"] for a in out["arms"]}
    twins: dict[str, str] = {}
    for a in list(out["arms"]):
        if a["arm"] not in arms or a.get("mode") != "record" or not a.get("write_port", True):
            continue
        t = twin(a["arm"])
        if t in names:
            raise ABError(f"{t} is already an arm of the proxy config")
        out["arms"].append({**a, "arm": t, "mode": "raw"})
        twins[a["arm"]] = t
    missing = sorted(set(arms) - set(twins))
    if missing:
        raise ABError(f"A/B arms {missing} have no recording write port in the proxy config - nothing to raw-forward")
    return out, twins


def twin_diff(config: Mapping[str, Any], arm: str) -> list[str]:
    """The keys in which the twin's config entry differs from its arm's - exactly ["arm", "mode"] when it is right."""
    by = {a["arm"]: a for a in config["arms"]}
    a, t = by[arm], by[twin(arm)]
    return sorted(k for k in set(a) | set(t) if a.get(k) != t.get(k))


class LegView:
    """The proxy as one leg sees it: each mapped arm name bound to another ArmConfig's ports and token (a raw leg: the
    twin's); everything else is the handle's."""

    def __init__(self, h: Any, mapping: Mapping[str, str]) -> None:
        self._h, self.mapping = h, dict(mapping)
        arms = dict((h.ports or {}).get("arms") or {})
        self.ports = {**h.ports, "arms": {**arms, **{a: arms[t] for a, t in self.mapping.items()}}}
        self.tokens = {**h.tokens, **{a: h.tokens[t] for a, t in self.mapping.items()}}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._h, name)


# ── the ids ────────────────────────────────────────────────────────────────────────────────────────────────────

def next_ab_id(status_path: str | Path, stand: str) -> tuple[str, int]:
    """("<stand>-ab-<n>", n): one past the highest n any STAND START "<stand>-ab-<n>-<leg>" names; 1 on a new file."""
    SL = _rv().load("status_log.py", smoke=True)
    pat = re.compile(rf"{re.escape(stand)}-ab-([1-9][0-9]*)-({'|'.join(LEGS)})")
    p, highest = Path(status_path), 0
    if p.exists():
        for line in p.read_bytes().decode("utf-8").split("\n"):
            if line.strip():
                _ts, ev, _utc = SL.parse_line(line)
                m = pat.fullmatch(ev.ident) if ev.kind == "STAND-START" else None
                if m:
                    highest = max(highest, int(m.group(1)))
    return f"{stand}-ab-{highest + 1}", highest + 1


# ── the metrics: one function for every leg (D-AB-5) ──────────────────────────────────────────────────────────

def _mem0_usage(c: Mapping[str, Any]) -> tuple[int, int, int]:
    u = c.get("llm_usage")
    if not isinstance(u, Mapping):
        raise ABError("mem0's counters carry no llm_usage (Q-AB-1) - its calls and tokens are unmeasured")
    return int(u["calls"]), int(u["prompt_tokens"]), int(u["completion_tokens"])


def _engine_usage(c: Mapping[str, Any]) -> tuple[int, int, int]:
    s = c.get("llm_stats")
    if not isinstance(s, Mapping):
        raise ABError("the engine's counters carry no llm_stats - its calls and tokens are unmeasured")
    calls = int(s.get("cloud", 0)) + int(s.get("ollama", 0))
    if calls and not ("prompt_tokens" in s and "eval_tokens" in s):
        raise ABError(f"the engine made {calls} LLM calls and counted no tokens - never a 0 that looks measured")
    return calls, int(s.get("prompt_tokens", 0)), int(s.get("eval_tokens", 0))


def _http_generate(c: Mapping[str, Any]) -> int:
    counts = ((c.get("http") or {}).get("counts") or {}) if isinstance(c.get("http"), Mapping) else {}
    return int((counts.get("ollama:generate") or {}).get("attempts", 0))


def _engine_generate(c: Mapping[str, Any]) -> int:
    return int((c.get("llm_stats") or {}).get("ollama", 0))


def _mem0_failed(c: Mapping[str, Any]) -> int:
    return int((c.get("llm_usage") or {}).get("failed", 0))


def _engine_failed(c: Mapping[str, Any]) -> int:
    return int((c.get("llm_stats") or {}).get("fail", 0))


#: arm -> (its logical LLM calls and tokens from its own counters, its local generation calls, its failed LLM calls -
#: which a product may swallow, so they are recorded beside the verdict). An arm not here has no product-side source
#: the A/B can use, and is refused by name (the ab-arm's comes with A8).
ARM_SOURCES: dict[str, tuple[Callable[[Mapping], tuple[int, int, int]], Callable[[Mapping], int],
                             Callable[[Mapping], int]]] = {
    "mem0": (_mem0_usage, _http_generate, _mem0_failed),
    "nevertwice": (_engine_usage, _engine_generate, _engine_failed),
}
SOURCE_NAMES = {"mem0": "arm_mem0 LLMUsage (mem0.llm.client.chat.completions.create, response.usage)",
                "nevertwice": "runner_nevertwice counters llm_stats (_LLM_STATS cloud+ollama, prompt/eval tokens)"}


def _stage_sum(fn: Callable[[Mapping], Any], wc: Mapping, qc: Mapping, cumulative: bool) -> Any:
    """The unit's total over its two stages: a memory-store arm's question counters include its write snapshot."""
    w, q = fn(wc), fn(qc)
    if isinstance(w, tuple):
        return tuple(y if cumulative else x + y for x, y in zip(w, q))
    return q if cumulative else w + q


def unit_metrics(arm: str, run: str, unit: str, w: Any, q: Mapping[str, Any]) -> dict:
    """One (arm, run, unit)'s A/B metrics from the scheduler's records alone - the same for every leg."""
    AC = _rv().load("accounting.py", smoke=True)
    SM = _rv().load("run_v3_smoke.py", smoke=True)
    who = f"{arm}/{run}/{unit}"
    if arm not in ARM_SOURCES:
        raise ABError(f"{arm}: no product-side counter the A/B can read (ARM_SOURCES) - it cannot be an A/B arm yet")
    if getattr(w, "aborted", None) or q.get("aborted"):
        raise ABError(f"{who}: the unit was aborted ({getattr(w, 'aborted', None) or q.get('aborted')}) - no A/B "
                      f"metric from it")
    if not isinstance(getattr(w, "counters", None), Mapping) or not isinstance(q.get("counters"), Mapping):
        raise ABError(f"{who}: a stage without its counters (B-WCTR) - the metrics are unmeasured")
    usage, generate, failed = ARM_SOURCES[arm]
    cum = bool(q.get("counters_include_write"))
    calls, tin, tout = _stage_sum(usage, w.counters, q["counters"], cum)
    local_gen = _stage_sum(generate, w.counters, q["counters"], cum)
    llm_failed = _stage_sum(failed, w.counters, q["counters"], cum)
    r_calls = r_in = r_out = 0
    unrecovered = 0
    for r in q.get("reads") or []:
        if r.get("unrecovered"):
            unrecovered += 1
            continue
        ans = r.get("answer") or {}
        r_calls += len(ans.get("request_keys") or [])
        u = ans.get("usage") or {}
        r_in += int(u.get("prompt_tokens", 0))
        r_out += int(u.get("completion_tokens", 0))
    ops = [{**op, "unit": unit} for op in (w.ops or [])]
    lost = AC.lost_operations(ops, [], arm=arm, run=run) if ops else {"lost_ops": [], "logical_writes": 0}
    n_ops = lost["logical_writes"]
    return {"calls": calls + r_calls, "tokens_in": tin + r_in, "tokens_out": tout + r_out,
            "lost_share": (len(lost["lost_ops"]) / n_ops) if n_ops else 0.0,
            "items": SM.retrievable(w.footprint), "wall_s": float(w.active_s) + float(q.get("active_s") or 0.0),
            "writer_calls": calls, "reader_calls": r_calls, "reads_unrecovered": unrecovered,
            "local_generation": local_gen, "llm_failed": llm_failed}


def run_means(per_unit: Mapping[str, Mapping[str, float]]) -> dict:
    return {m: statistics.fmean(float(v[m]) for v in per_unit.values()) for m in METRICS}


# ── the proxy's in-memory counters around a leg ────────────────────────────────────────────────────────────────

def _snapshot(h: Any) -> dict:
    return {"counters": h.control.counters(), "ollama": h.control.ollama()}


def _delta(before: Mapping, after: Mapping, name: str) -> dict:
    b, a = before["counters"].get(name) or {}, after["counters"].get(name) or {}
    ob, oa = before["ollama"].get(name) or {}, after["ollama"].get(name) or {}
    hop_b = len(b.get("own_hop_ms") or [])
    return {"thinking_injected": a.get("thinking_injected", 0) - b.get("thinking_injected", 0),
            "client_abandoned": a.get("client_abandoned", 0) - b.get("client_abandoned", 0),
            "requests": a.get("requests", 0) - b.get("requests", 0),
            "own_hop_ms": list((a.get("own_hop_ms") or [])[hop_b:]),
            "own_hop_dropped": a.get("own_hop_dropped", 0) - b.get("own_hop_dropped", 0),
            "fallback_local": (oa.get("fallback_local") or 0) - (ob.get("fallback_local") or 0)}


def _stops(base: Mapping[str, Any], now: Mapping[str, Any]) -> dict:
    """{ArmConfig: {status: new count}} of STOP_STATUSES the upstream answered since ``base`` - any ArmConfig of the
    process (arms, twins, the scheduler's port), raw or recording alike (the proxy counts them in memory, D-AB-8)."""
    out: dict = {}
    for name, c in now.items():
        was = (base.get(name) or {}).get("upstream_statuses") or {}
        got = {s: n - was.get(s, 0) for s, n in (c.get("upstream_statuses") or {}).items()
               if s in STOP_STATUSES and n - was.get(s, 0) > 0}
        if got:
            out[name] = got
    return out


class StopGate:
    """D-AB-8 (the auditor's condition): the A/B's gate - it has no incident gate (a raw leg writes no calls.jsonl),
    but a 401, 402 or 403 on any ArmConfig stops it: no new unit starts (raising here, the scheduler's B-OPEN closes
    the stand), no later leg runs, and it is not repeated - it waits for the owner (§4.5)."""

    def __init__(self, control: Any, base: Mapping[str, Any]) -> None:
        self.control, self.base = control, base

    def admits_new_unit(self) -> bool:
        seen = _stops(self.base, self.control.counters())
        if seen:
            raise ABError(f"D-AB-8: the upstream answered {seen} - no new unit starts; the A/B stops (§4.5)")
        return True


def stopped_without_owner(runs_root: str | Path) -> list[Path]:
    """The ab.json of every earlier A/B that stopped on 401/402/403 and has no OWNER_FILE beside it (§4.5)."""
    out = []
    for f in sorted(Path(runs_root, "_ab").glob("*/attempt-*/ab.json")):
        if json.loads(f.read_bytes().decode("utf-8")).get("stop") and not (f.parent / OWNER_FILE).exists():
            out.append(f)
    return out


def exit_code(problems: Sequence[str], verdicts: Mapping[str, Any], stop: Mapping[str, Any] | None) -> int:
    """0 only when nothing is wrong and every arm's verdict is in tolerance - no allowance of any kind (a test's
    timing-noise tolerance is the test's, never the harness's); EXIT_STOP for a 401/402/403 stop; 1 otherwise."""
    if stop:
        return EXIT_STOP
    ok = not problems and bool(verdicts) and all(v.get("in_tolerance") is True for v in verdicts.values())
    return 0 if ok else 1


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_bytes().decode("utf-8").split("\n") if x.strip()]


# ── the A/B ────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class ABResult:
    rc: int
    record_path: Path | None
    problems: list


def run_ab(cfg: Any, *, stand: str, arm_names: Sequence[str], deps: Any, thinking_branch: str = "unset",
           thinking_routes: Mapping[str, str] | None = None, hop_n: int = 200) -> ABResult:
    """The A/B of ``arm_names`` on ``stand``'s smoke (see the module docstring). ``deps`` is a run_v3.SmokeDeps."""
    RV = _rv()
    c, L = deps.contract, deps.L
    if stand not in RV.SMOKE_STANDS:
        raise ABError(f"an A/B of {stand} is not possible - only {RV.SMOKE_STANDS} have a smoke")
    unknown = sorted(set(arm_names) - set(cfg.arms))
    if unknown or not arm_names:
        raise ABError(f"arms {unknown or list(arm_names)} are not in the run config's arms {sorted(cfg.arms)}")
    not_ab = sorted(a for a in arm_names if a not in ARM_SOURCES)
    if not_ab:
        raise ABError(f"arms {not_ab} have no product-side counter the A/B can read (ARM_SOURCES)")
    arms = {a: cfg.arms[a] for a in arm_names}
    routes = dict(thinking_routes or {})
    SC, PL, P, H, SL, SM, TP, PT, AR = (RV.load(f, smoke=True) for f in (
        "scheduler.py", "run_v3_plan.py", "run_v3_proxy.py", "run_v3_hooks.py", "status_log.py", "run_v3_smoke.py",
        "templates.py", "points.py", "ab_rule.py"))
    held = stopped_without_owner(c.runs_root)
    if held:
        raise ABError(f"an earlier A/B stopped on 401/402/403 and waits for the owner (§4.5, D-AB-8): {held[0]} has "
                      f"no {OWNER_FILE} beside it - no A/B starts before the owner's word")
    ab_id, n = next_ab_id(deps.status_path, stand)

    # the units: computed once, the first AB_UNITS of the smoke (D-AB-2)
    order = RV.s1_order(deps.lists_dir, expected_sha256=deps.s1_sha256)
    su = RV.s4_smoke_units(deps.lme_records(), order, stand_id=ab_id)
    units = list(su["units"][:AB_UNITS])
    if len(units) != AB_UNITS:
        raise ABError(f"the smoke has {len(units)} units, not {AB_UNITS}")
    unit_ids = [u.unit_id for u in units]
    unit_shas = [u.input_sha256 for u in units]
    count, cut = deps.cl100k
    ut = RV.unit_tokens(units, count)
    t4, t4c5 = deps.templates
    questions = RV.questions_for(units, template=t4, template_abstain=t4c5, locomo_question=deps.locomo_question)

    # the forecast and the preflight, before any spawn (Q-AB-3)
    session_texts = {u.unit_id: [PL.session_text(s) for s in u.sessions] for u in units}
    measured = RV.bytes_per_cl100k_token([t for v in session_texts.values() for t in v], count)
    measured["source"] = f"cl100k {deps.cl100k_source[:12]} over the {AB_UNITS} A/B units' session texts"
    prompts = {k: TP.render(t, {"context": "", "question": q}) for k, (t, q) in questions.items()}
    fc = RV.forecast({a: ar.llm for a, ar in arms.items()}, session_texts, prompts, runs=len(LEGS),
                     max_token_bytes=deps.max_token_bytes, measured=measured)
    pf = RV.preflight(c, L, arms, stand_id=ab_id, config_sha256=cfg.sha256, decl=deps.decl, now=deps.now_utc,
                      forecast=fc)
    attempt = f"attempt-{pf['position']:05d}"

    # the boundary, and ONE proxy with a raw twin per arm (Q-AB-5)
    env = dict(deps.environ)
    wiring = RV.boundary_canaries(L, c, env)
    W = L.Witnesses(c, native=deps.native, fs=deps.fs, canaries=wiring["canaries"])
    ab_dir = Path(c.runs_root) / "_ab" / ab_id / attempt
    base = P.build_config({a: {"llm": ar.llm, "llm_transport": ar.llm_transport, "embeds_via_ollama": ar.embeds_via_ollama,
                               "reader": True, **({"thinking_route": routes[a]} if a in routes else {})}
                           for a, ar in arms.items()},
                          run_dir=Path(c.runs_root) / "_ab" / ab_id / "_proxy" / attempt, thinking_branch=thinking_branch,
                          **dict(deps.proxy_route), embed_tokenizer=deps.embed_tokenizer)
    pcfg, twins = ab_proxy_config(base, list(arms))
    diffs = {a: twin_diff(pcfg, a) for a in twins}
    bad = {a: d for a, d in diffs.items() if d != ["arm", "mode"]}
    if bad:
        raise ABError(f"a twin differs from its arm beyond name and mode: {bad} (Q-AB-5)")
    secrets = P.build_secrets([*arms, *twins.values()], roles=("scheduler",), canaries=wiring["proxy"])
    h = deps.start_proxy(c, python=cfg.proxy_python, key_file=cfg.key_file, config=pcfg, secrets=secrets,
                         unit=L.make_unit_dirs(c, ab_id, "_harness", "proxy", attempt), parent_env=env, witnesses=W)
    problems: list[str] = []
    legs: list[dict] = []
    stop: dict | None = None
    status = SL.StatusLog(Path(deps.status_path))
    base_counters = h.control.counters()
    try:
        for i, leg in enumerate(LEGS, 1):
            stand_id = f"{ab_id}-{leg}"
            mapping = {a: twins[a] for a in arms} if leg.startswith("raw") else {}
            view = LegView(h, mapping)
            before = _snapshot(h)
            rec: dict = {"leg": leg, "stand": stand_id, "order": i, "mapping": {a: mapping.get(a, a) for a in arms},
                         "t0": deps.now_utc()}
            try:
                hooks = H.Hooks(repo=c.repo_root, git=cfg.git, proxy=h, projected_cost=fc["usd_total"], currency="USD")
                sched = SC.Scheduler(c, h.control, status, L, deps.clock, deps.ollama_ctl, tag=AB_TAG, witnesses=W,
                                     parent_env=env, catcher_url=P.catcher_url(h), hooks=hooks,
                                     canaries=wiring["scheduler"]["canaries"],
                                     home_canaries=wiring["scheduler"]["home_canaries"])
                blocks = PL.blocks_for(stand_id, unit_ids, block_plan=SC.BlockPlan)
                unit_block = {u: bp.block for bp in blocks for u in bp.units}
                launchers = {a: deps.make_launcher(a, ar, stand_id=stand_id, proxy=view, unit_block=unit_block,
                                                   unit_chars={u.unit_id: u.chars for u in units})
                             for a, ar in arms.items()}
                answer = PL.Answerer(stand_id, questions=questions,
                                     reader=lambda arm, run, unit, view=view: (
                                         view.ports["arms"][arm]["reader"], f"/u/{run}.{unit}/v1/chat/completions",
                                         view.tokens[arm]),
                                     post=deps.post, count=count, cut=cut, answers_root=c.runs_root,
                                     reaskable=SC.ReaskableError)
                sp, _pst = PL.stand_plan(stand_id, units, launchers, standplan=SC.StandPlan, read_req=SC.ReadReq,
                                         runs=(leg,), campaign_seed=cfg.campaign_seed, unit_tokens=ut, medians={},
                                         answer=answer, embed_tag=cfg.embed_tag, dated=True, points=lambda a: ("B",),
                                         k_at=PT.K_AT, smaps=su["smaps"], truncate=deps.truncate, bodies_dir=h.run_dir)
                hooks.bind(sched, sp)
                hooks.gate = RV.WallCapGate(StopGate(h.control, base_counters),
                                            deadline=deps.monotonic() + RV.SMOKE_WALL_CAP_H * 3600,
                                            monotonic=deps.monotonic, cap_h=RV.SMOKE_WALL_CAP_H)
                res = sched.run_stand(sp, blocks, judges=(), order=i)
            except Exception as e:  # noqa: BLE001 - the leg's failure is named; no later leg runs
                problems.append(f"{leg}: the stand did not complete: {type(e).__name__}: {e}")
                rec["t1"] = deps.now_utc()
                legs.append(rec)
                seen = _stops(base_counters, h.control.counters())
                stop = {"leg": leg, "statuses": seen} if seen else None
                break
            rec["t1"] = deps.now_utc()
            after = _snapshot(h)
            runs = SM.arm_runs(res)
            rec["units"] = {a: sorted(runs.get((a, leg), {}).get("write", {})) for a in arms}
            rec["unit_input_sha256"] = unit_shas
            rec["metrics"], rec["means"], rec["proxy"] = {}, {}, {}
            for a in arms:
                ar_ = runs.get((a, leg)) or {"write": {}, "questions": {}}
                try:
                    per = {u: unit_metrics(a, leg, u, ar_["write"][u], ar_["questions"].get(u) or {})
                           for u in sorted(ar_["write"])}
                    rec["metrics"][a] = per
                    rec["means"][a] = run_means(per) if per else None
                except ABError as e:
                    problems.append(f"{leg}: {e}")
                rec["proxy"][a] = _delta(before, after, mapping.get(a, a))
            legs.append(rec)
            seen = _stops(base_counters, after["counters"])
            if seen:                                       # D-AB-8: no later leg - the owner's, never a repeat
                stop = {"leg": leg, "statuses": seen}
                break
    finally:
        pstop = dict(deps.stop_proxy(h))
    if pstop.get("killed") or pstop.get("rc") not in (0,):
        problems.append(f"the proxy did not stop by itself: {pstop}")
    if stop:
        stop["rule"] = "§4.5 (D-AB-8): the A/B stops by name, is not repeated, and waits for the owner"
        problems.insert(0, f"STOP: {stop['leg']}: the upstream answered {stop['statuses']} - the A/B stops, is not "
                           f"repeated, and waits for the owner (§4.5, D-AB-8; {OWNER_FILE} beside ab.json lets the next "
                           f"one start)")

    # the checks over all four legs, in one loop each (D-AB-2, D-AB-4, T6)
    done = [lg for lg in legs if "means" in lg]
    if [lg["leg"] for lg in done] != list(LEGS):
        problems.append(f"legs completed {[lg['leg'] for lg in done]}, not {list(LEGS)} - no verdict")
    for lg in done:
        for a in arms:
            if lg["units"].get(a) != sorted(unit_ids):
                problems.append(f"{lg['leg']}: {a} ran units {lg['units'].get(a)}, not the A/B's {sorted(unit_ids)} "
                                f"(D-AB-2)")
            gen = sum(m.get("local_generation", 0) for m in (lg["metrics"].get(a) or {}).values())
            fl = lg["proxy"][a]["fallback_local"]
            if gen or fl:
                problems.append(f"{lg['leg']}: {a} made local generation calls (adapter {gen}, Ollama leg "
                                f"fallback_local {fl}) - a fallback on this leg (D-AB-4)")
            if lg["proxy"][a]["own_hop_dropped"]:
                problems.append(f"{lg['leg']}: {a}'s own-hop samples were dropped past the proxy's cap")
    for a in arms:
        inj = {lg["leg"]: lg["proxy"][a]["thinking_injected"] > 0 for lg in done}
        if len(set(inj.values())) > 1:
            problems.append(f"{a}: the declared thinking field was injected on legs {sorted(k for k, v in inj.items() if v)} "
                            f"only, not on {sorted(k for k, v in inj.items() if not v)} (D-AB-4)")
    calls_log = _jsonl(h.run_dir / "calls.jsonl")
    flags_log = _jsonl(h.run_dir / "flags.jsonl")
    ollama_log = _jsonl(h.run_dir / "ollama.jsonl")
    t6 = {}
    for a, t in twins.items():
        t6[t] = {"calls_jsonl": sum(1 for x in calls_log if x.get("arm") == t),
                 "bodies": (h.run_dir / "bodies" / t).exists(),
                 "flags_jsonl": sum(1 for x in flags_log if x.get("arm") == t),
                 "ollama_jsonl_lines": sum(1 for x in ollama_log if x.get("arm") == t)}
        if t6[t]["calls_jsonl"] or t6[t]["bodies"] or t6[t]["flags_jsonl"]:
            problems.append(f"T6: the raw twin {t} left recording traces {t6[t]}")

    # the verdicts
    verdicts: dict = {}
    if not problems and not stop:
        for a in arms:
            by = {lg["leg"]: lg for lg in done}
            rec_s = [v for lg in ("rec1", "rec2") for v in by[lg]["proxy"][a]["own_hop_ms"]]
            raw_s = [v for lg in ("raw1", "raw2") for v in by[lg]["proxy"][a]["own_hop_ms"]]
            if not rec_s or not raw_s:
                problems.append(f"{a}: no own-hop samples on the recording or the raw legs - no added-TTFB figure")
                continue
            verdicts[a] = AR.ab_verdict([by["raw1"]["means"][a], by["raw2"]["means"][a]],
                                        [by["rec1"]["means"][a], by["rec2"]["means"][a]],
                                        client_abandoned=sum(by[lg]["proxy"][a]["client_abandoned"] for lg in LEGS),
                                        ttfb_added_ms=AR.added_over(rec_s, raw_s))
            if not verdicts[a]["in_tolerance"]:
                problems += [f"{a}: out of tolerance - {f}" for f in verdicts[a]["failures"]]
    hop = AR.hop_benchmark(n=hop_n) if hop_n else None
    record = {"ab": ab_id, "attempt": attempt, "stand": stand, "tag": AB_TAG, "arms": sorted(arms),
              "units": {"ids": unit_ids, "input_sha256": unit_shas, "order_positions": su["order_positions"][:AB_UNITS]},
              "legs": legs, "twins": twins, "twin_diff": diffs, "t6": t6,
              "proxy_config_sha256": hashlib.sha256(json.dumps(pcfg, sort_keys=True).encode("utf-8")).hexdigest(),
              "thinking_branch": thinking_branch, "thinking_routes": routes,
              "sources": {a: {"writer": SOURCE_NAMES[a], "reader": "run_v3_plan.Answerer usage and request_keys",
                              "items": "end_write footprint retrievable", "wall": "scheduler active_s, write + questions",
                              "lost_share": "accounting.lost_operations(ops, calls=[]) - the adapter's view (Q-AB-4)"}
                          for a in arms},
              "forecast_usd": fc["usd_total"], "preflight_position": pf["position"], "proxy_stop": pstop,
              "hop_benchmark": hop, "verdicts": verdicts, "stop": stop, "problems": problems}
    ab_dir.mkdir(parents=True, exist_ok=True)
    path = ab_dir / "ab.json"
    with open(path, "xb") as f:
        f.write(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str).encode("utf-8"))
    for a, v in sorted(verdicts.items()):
        deps.out(f"AB {ab_id} {a} in_tolerance={v['in_tolerance']} ttfb_added_p50={v['ttfb_added_ms']['p50']:.3f}ms "
                 f"p95={v['ttfb_added_ms']['p95']:.3f}ms")
    for p in problems:
        deps.err(f"problem: {p}")
    return ABResult(rc=exit_code(problems, verdicts, stop), record_path=path, problems=problems)
