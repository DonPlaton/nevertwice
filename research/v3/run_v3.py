#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A6: research/v3/run_v3.py - the campaign CLI: the smoke, end to end (the scored path waits for
A9).

* load(): the modules a smoke may load - score_v3.py and fafr_probe.py never (rev1 §9.4: "--smoke has no scorer");
* next_smoke_id(): the next "<stand>-smoke-<n>" from the STATUS file's STAND START lines (Q-12-7: order = n);
* load_run_config(): <runs>/_config/pilot.json - the proxy's python, the key file BY PATH (inside the secrets
  directory, never opened by the CLI: only the proxy reads it), git, the campaign seed, the embed tag, Ollama, and per
  arm its python, llm, transport and extra values; the file's sha256 recorded;
* s4_smoke_units(): the S4 smoke - one LoCoMo-format conversation per S1 smoke question (Q18 O-a), from the committed
  ls1 list (lists/S1.json, never an order computed here): the questions at positions 481-500 (smoke_rules.s1_tail);
  their units, one speaker map per unit (B-S4-SMAP) and each unit's item texts (the coverage denominator);
* unit_tokens(): each unit's input size in cl100k tokens (§5.2) - the ceiling's input (scheduler.ceiling_for);
* questions_for(): each question's template (the stand's; S4-cat5 for an abstention question) and its text as the
  benchmark asks it (templates.locomo_question, the category as the loader keeps it - B-CAT);
* probe(): the gate's send_probe - the scheduler's 1-token call on its own port (run_v3_hooks' probe body), its call
  record {status, complete, model};
* boundary_canaries() (part 2a): ONE launch.Canaries per stand feeds the homes, the scheduler's env check and the
  proxy's body scan (the auditor's condition: a planted value the proxy does not scan for would make P0h read 0);
* preflight() (Q-A6-1 O-a): every arm's interpreter declared before STAND START, each attempt chained in
  <runs>/_launch/preflight.jsonl and written whole, with the forecast; a refusal is exit 2, STATUS untouched;
* forecast() (Q-A6-2, Q-A6-3): the guaranteed upper bound (FORECAST_FORMULA, deepseek-flash's price as data) and an
  estimate that is not a bound (the reader's context at the bytes per cl100k token measured on the smoke's own text);
  hours are not forecast;
* WallCapGate, SMOKE_WALL_CAP_H (Q26): past the stand's 6 h wall ceiling no new unit starts;
* run_smoke() (part 2b): the units, the forecast, the preflight, one Canaries object, the proxy, the hooks and the
  incident gate under the wall ceiling, the scheduler's stand, the gate and then the proxy stopped, the SMOKE_FIELDS
  lines and <runs>/<stand id>/_smoke/<attempt>/{summary,run}.json with each arm-run's P0h counters; an attempt's
  directories are named by its preflight line (B-ATTEMPT: an attempt that fails before STAND START leaves the id
  unspent). SmokeDeps carries everything it reaches outside this module;
* main(): `stand --tag scored` is refused by name until A9; `stand --stand S4 --smoke` builds the real SmokeDeps (the
  contract, the witnesses, the pinned tokenizers and templates, the run config's arms, the proxy through the declared
  hop, .loop/campaign-v3-log/STATUS) and runs the smoke.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
SCORING = frozenset({"score_v3.py", "fafr_probe.py"})
SMOKE_STANDS = ("S4",)                  # templates.PENDING holds S1, S5, S7; S6 is refused by the plan (§5.6)


class CLIError(ValueError):
    """A run the preregistration does not allow, or an input the CLI cannot read; nothing was started."""


def load(file: str, *, smoke: bool, name: str | None = None):
    """research/v3/<file>, loaded once; a scorer never on a smoke (CLI-load-guard)."""
    if smoke and file in SCORING:
        raise CLIError(f"{file} is a scorer - a smoke loads none (rev1 §9.4)")
    name = name or f"v3_{Path(file).stem}_for_run_v3"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, HERE / file)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


# ── the smoke's id (Q-12-7) ────────────────────────────────────────────────────────────────────────────────────

def next_smoke_id(status_path: str | os.PathLike, stand: str) -> tuple[str, int]:
    """("<stand>-smoke-<n>", n): one past the highest n a STAND START line of the file names; 1 on a new file."""
    SL = load("status_log.py", smoke=True)
    p = Path(status_path)
    pat = re.compile(rf"{re.escape(stand)}-smoke-([1-9][0-9]*)")
    highest = 0
    if p.exists():
        for line in p.read_bytes().decode("utf-8").split("\n"):
            if not line.strip():
                continue
            try:
                _ts, ev, _utc = SL.parse_line(line)
            except ValueError as e:
                raise CLIError(f"{p}: a line the STATUS writer did not write ({e}) - no smoke id is guessed") from None
            m = pat.fullmatch(ev.ident) if ev.kind == "STAND-START" else None
            if m:
                highest = max(highest, int(m.group(1)))
    n = highest + 1
    return f"{stand}-smoke-{n}", n


# ── the run config ─────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ArmRun:
    python: Path
    llm: str | None
    llm_transport: str | None
    embeds_via_ollama: bool
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunConfig:
    proxy_python: Path
    key_file: Path                      # a path only: the CLI never opens it (the proxy reads it)
    git: Path
    campaign_seed: int
    embed_tag: str
    ollama_url: str
    arms: Mapping[str, ArmRun]
    sha256: str


RUN_KEYS = {"proxy_python", "key_file", "git", "campaign_seed", "embed_tag", "ollama_url", "arms"}
ARM_KEYS = {"python", "llm", "llm_transport", "embeds_via_ollama", "extra"}


def load_run_config(path: str | os.PathLike, *, secrets_dir: str | os.PathLike) -> RunConfig:
    """The run config (see the module docstring); every problem named, nothing defaulted."""
    raw = Path(path).read_bytes()
    try:
        d = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise CLIError(f"{path}: not a JSON config ({type(e).__name__})") from None
    if not isinstance(d, dict) or set(d) != RUN_KEYS:
        raise CLIError(f"{path}: the keys are {sorted(d) if isinstance(d, dict) else type(d).__name__}, "
                       f"not {sorted(RUN_KEYS)}")
    key = Path(d["key_file"])
    sec = Path(secrets_dir)
    if not key.is_absolute() or key.suffix != ".env" or os.path.normcase(os.path.abspath(key.parent)) != \
            os.path.normcase(os.path.abspath(sec)):
        raise CLIError(f"the key file must be an absolute *.env path directly inside {sec} - named by path only")
    if not (isinstance(d["campaign_seed"], int) and not isinstance(d["campaign_seed"], bool)):
        raise CLIError("campaign_seed is an int")
    arms = {}
    for name, a in (d["arms"] or {}).items():
        if not isinstance(a, dict) or set(a) != ARM_KEYS:
            raise CLIError(f"arm {name}: the keys are {sorted(a) if isinstance(a, dict) else a!r}, not {sorted(ARM_KEYS)}")
        py = Path(a["python"])
        if not py.is_absolute():
            raise CLIError(f"arm {name}: its python {a['python']!r} is not an absolute path")
        arms[name] = ArmRun(python=py, llm=a["llm"], llm_transport=a["llm_transport"],
                            embeds_via_ollama=bool(a["embeds_via_ollama"]), extra=dict(a["extra"] or {}))
    if not arms:
        raise CLIError(f"{path}: no arm")
    return RunConfig(proxy_python=Path(d["proxy_python"]), key_file=key, git=Path(d["git"]),
                     campaign_seed=d["campaign_seed"], embed_tag=str(d["embed_tag"]), ollama_url=str(d["ollama_url"]),
                     arms=arms, sha256=hashlib.sha256(raw).hexdigest())


# ── the S4 smoke's units (Q18 O-a, B-S4-SMAP) ──────────────────────────────────────────────────────────────────

#: The S1/S2 nested order's ids_sha256, verified by the auditor's independent implementation (ls1-go-2).
S1_IDS_SHA256 = "73841e1dff06ffe6c2b4d6eb95691b274a5d4ed39ebe3b5c92e47f95248e2da2"


def s1_order(lists_dir: str | os.PathLike | None = None, *, expected_sha256: str = S1_IDS_SHA256) -> list[str]:
    """The committed ls1 list (lists/S1.json) - never an order computed here; its ids must hash to their own
    ids_sha256 AND to the verified reference (a consistent but foreign list is refused)."""
    p = Path(lists_dir) if lists_dir is not None else HERE / "lists"
    f = p / "S1.json"
    if not f.is_file():
        raise CLIError(f"{f} does not exist - the S1 order comes from the ls1 run only")
    rec = json.loads(f.read_bytes().decode("utf-8"))
    ids = rec.get("ids")
    canon = json.dumps(ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not isinstance(ids, list) or hashlib.sha256(canon).hexdigest() != rec.get("ids_sha256"):
        raise CLIError(f"{f}: its ids do not hash to its ids_sha256 - the list is not the one ls1 wrote")
    if rec.get("ids_sha256") != expected_sha256:
        raise CLIError(f"{f}: its ids_sha256 is not the verified reference {expected_sha256[:12]}... - not the S1 order")
    return ids


def s4_smoke_units(lme_records: Iterable[Mapping], order: Sequence[str], *, stand_id: str) -> dict:
    """{units, samples, smaps, item_texts, order_positions} of the S4 smoke (see the module docstring)."""
    LD, PL = load("loaders.py", smoke=True), load("run_v3_plan.py", smoke=True)
    SR = load("smoke_rules.py", smoke=True)
    tail = SR.s1_tail(order)
    samples = LD.s4_smoke_samples(lme_records, order)
    ids = [s["sample_id"] for s in samples]
    if ids != [f"smoke-{q}" for q in tail]:
        raise CLIError("the smoke samples are not the S1 order's positions 481-500, in that order")
    units = LD.locomo_units(samples, ids, prefix=len(ids), stand=stand_id)
    PL.check_ids((), [u.unit_id for u in units])
    return {"units": units, "samples": samples, "smaps": PL.speaker_maps(samples),
            "item_texts": {u.unit_id: [i.text for s in u.sessions for i in s.items] for u in units},
            "order_positions": [order.index(q) + 1 for q in tail]}


def unit_tokens(units: Sequence[Any], count: Callable[[str], int]) -> dict[str, int]:
    """Each unit's input in cl100k tokens: its sessions' text as every text-API arm gets it (Q-45-4), joined."""
    PL = load("run_v3_plan.py", smoke=True)
    return {u.unit_id: count("\n".join(PL.session_text(s) for s in u.sessions)) for u in units}


def questions_for(units: Sequence[Any], *, template: Any, template_abstain: Any,
                  locomo_question: Callable[..., str]) -> dict[tuple[str, str], tuple[Any, str]]:
    """{(unit, qid): (its template, its text as asked)} - the stand's template, S4-cat5's for an abstention question;
    never a function of the arm (Q-48-6)."""
    out = {}
    for u in units:
        for q in u.questions:
            out[(u.unit_id, q.qid)] = (template_abstain if q.abstention else template,
                                       locomo_question(q.text, q.category))
    return out


# ── the gate's probe (Q25-INC) ─────────────────────────────────────────────────────────────────────────────────

def probe(post: Callable[..., tuple[int | None, Any]], port: int, token: str, *, timeout: float = 60.0) -> Callable[[], dict]:
    """The gate's send_probe: () -> the scheduler's 1-token call record on its own port - {status, complete, model}."""
    H = load("run_v3_hooks.py", smoke=True)
    RP = load("run_v3_proxy.py", smoke=True)

    def send() -> dict:
        status, body = post(port, H.PROBE_PATH, {"model": RP.PINNED_MODEL, **H.PROBE_BODY}, token, timeout=timeout)
        complete = status == 200 and isinstance(body, dict) and bool(body.get("choices"))
        return {"status": status, "complete": complete,
                "model": body.get("model") if isinstance(body, dict) else None}
    return send


# ── part 2: the stand's boundary, the preflight (Q-A6-1 O-a) and the stand's wall ceiling ─────────────────────

def boundary_canaries(L: Any, c: Any, environ: dict) -> dict:
    """ONE launch.Canaries per stand (the auditor's condition for run_smoke): the same object is planted in every
    unit's home (the scheduler's home_canaries, R-HOME-CANARY), checked in every child's environment (the scheduler's
    canaries) and scanned for in every request body (the proxy's canaries, run_v3_proxy.build_secrets) - a value
    planted in a home that the proxy does not look for would pass a request unflagged, and P0h would read 0. The decoy
    env goes into ``environ`` - the mapping later passed on as parent_env, never os.environ - and the runs-root decoy
    into <runs>/CLAUDE.md."""
    can = L.Canaries.generate()
    L.plant_decoy_env(can, environ=environ)
    L.plant_runs_root_decoy(c, can)
    return {"canaries": can, "scheduler": {"canaries": tuple(can.values.values()), "home_canaries": can},
            "proxy": dict(can.values)}


PREFLIGHT_LOG = ("_launch", "preflight.jsonl")


def preflight(c: Any, L: Any, arms: Mapping[str, ArmRun], *, stand_id: str, config_sha256: str,
              decl: Callable[..., dict], now: Callable[[], str], forecast: Mapping[str, Any] | None = None) -> dict:
    """Q-A6-1 O-a: before STAND START, every arm's interpreter declared (``decl``: run_v3_plan.python_decl). Each
    attempt - passed or refused - is appended, chained and fsynced, to <runs>/_launch/preflight.jsonl (launch's
    _append_jsonl, like balance.jsonl), and written whole to <runs>/_launch/preflight/<its line>-<stand id>.json: the
    candidate stand id, the arms, each arm's declaration or refusal, the time, the config's sha256 and the smoke's
    forecast (Q-A6-2: in the record before the first spawn). A refusal raises CLIError (stderr, exit 2) - STATUS
    untouched, the smoke id unspent."""
    rec: dict = {"stand_candidate": stand_id, "arms": sorted(arms), "config_sha256": config_sha256, "utc": now(),
                 "decl": {}, "refused": {}, "forecast": dict(forecast) if forecast is not None else None}
    for name in sorted(arms):
        try:
            rec["decl"][name] = decl(arms[name].python, arm=name)
        except ValueError as e:                                  # run_v3_plan.PlanError is a ValueError
            rec["refused"][name] = str(e)
    rec["ok"] = not rec["refused"]
    log = Path(c.runs_root).joinpath(*PREFLIGHT_LOG)
    L._append_jsonl(log, rec)
    pos = len([x for x in log.read_bytes().split(b"\n") if x.strip()])
    one = log.parent / "preflight" / f"{pos:05d}-{stand_id}.json"
    one.parent.mkdir(parents=True, exist_ok=True)
    with open(one, "xb") as f:                                   # one file per attempt, never over another
        f.write(json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8"))
    if not rec["ok"]:
        raise CLIError(f"{stand_id}: the preflight refused {sorted(rec['refused'])} - {rec['refused']} "
                       f"(recorded in {'/'.join(PREFLIGHT_LOG)} and {one.name}; no STAND START)")
    return {**rec, "position": pos}                          # the attempt's line in the chain: its directories' name


# ── the smoke's forecast (Q-A6-2, R-S4-COST): an upper bound, not pilot medians ────────────────────────────────

#: deepseek-flash's list prices - external DATA, read on 2026-09-28 from the URL (USD per 1M tokens; peak hours are
#: 01:00-04:00 and 06:00-10:00 UTC on weekdays, off-peak rates are half). The bound prices every input token as a
#: peak cache miss and every output token at the peak rate.
DEEPSEEK_FLASH_PRICE = {
    "url": "https://api-docs.deepseek.com/quick_start/pricing", "read": "2026-09-28", "currency": "USD",
    "per_tokens": 1_000_000, "model": "deepseek-flash", "model_version": "DeepSeek-V4.1-Flash",
    "input_cache_hit": {"off_peak": 0.003, "peak": 0.006}, "input_cache_miss": {"off_peak": 0.15, "peak": 0.3},
    "output": {"off_peak": 0.6, "peak": 1.2}}

#: The writer's bound per arm, read from the product's code (never measured). nevertwice under runner_nevertwice
#: (CLOUD=deepseek, CLOUD_FALLBACK=0, EXTRACT_RETRY=0): one capture_session per session op makes one extraction
#: generate_json (_engine_cards.py:995), which _json_api_call sends at most 1 + 2 retries times (_engine_store.py:813,
#: _engine_config.py:386) - 3 requests; each with max_tokens EXTRACT_NUM_PREDICT 4096 (_engine_store.py:993); its input
#: is the session text cut to MAX_TRANSCRIPT_CHARS 12,000 (one head+tail window, _engine_cards.py:967) plus the
#: template and the grounding - at most 13,000 ASCII bytes (the template's 5,644 characters, 120 slugs of at most 55,
#: 30 tags, the project, the language rule; the tags' length is not capped in the engine [U]).
WRITER_BOUNDS = {"nevertwice": {"requests_per_op": 3, "out_tokens": 4096, "fixed_in_bytes": 13_000,
                                "transcript_chars": 12_000}}
#: The reader (reader_judge.READER_PARAMS, points.BUDGET): at most 2 requests per question (the read and one re-ask),
#: max_tokens 1,024 each; its context at most 7,000 cl100k tokens.
READER_REQUESTS, READER_OUT, READER_CONTEXT_CL100K = 2, 1024, 7000

FORECAST_FORMULA = (
    "tokens(text) <= utf8_bytes(text) [A1: DeepSeek's tokenizer is byte-level BPE]; "
    "writer per session op: requests <= R, in <= R * (F + min(utf8_bytes(session), 4 * C)), out <= R * O "
    "[WRITER_BOUNDS: R, F, C, O]; "
    "reader per question: requests <= 2, in <= 2 * (utf8_bytes(prompt without context) + 7000 * B) + 1024, "
    "out <= 2 * 1024 [B: the longest pinned cl100k token in bytes; A2: the re-ask carries the first reply, "
    "<= 1024 tokens]; "
    "per arm: x runs; usd = in * input_cache_miss.peak + out * output.peak, per 1M tokens")


def bytes_per_cl100k_token(texts: Iterable[str], count: Callable[[str], int]) -> dict:
    """Q-A6-3 (2): the measured UTF-8 bytes per pinned-cl100k token over ``texts`` (the smoke units' session text,
    before the run) - {ratio, bytes, tokens}; the estimate's context size, never the bound's."""
    b = t = 0
    for x in texts:
        b += len(x.encode("utf-8"))
        t += count(x)
    if t <= 0:
        raise CLIError("no cl100k token in the texts - no ratio is guessed")
    return {"ratio": b / t, "bytes": b, "tokens": t}


def _per_arm(writers: Mapping[str, str | None], session_texts: Mapping[str, Sequence[str]],
             prompts: Mapping[tuple[str, str], str], *, runs: int, context_bytes: float,
             price: Mapping[str, Any]) -> tuple[dict, float]:
    n_ops = sum(len(v) for v in session_texts.values())
    per_arm: dict = {}
    total = 0.0
    for arm in sorted(writers):
        w = WRITER_BOUNDS.get(arm) if writers[arm] is not None else None
        wr = w["requests_per_op"] * n_ops if w else 0
        w_in = sum(w["requests_per_op"] * (w["fixed_in_bytes"] + min(len(t.encode("utf-8")), 4 * w["transcript_chars"]))
                   for v in session_texts.values() for t in v) if w else 0
        w_out = wr * w["out_tokens"] if w else 0
        rr = READER_REQUESTS * len(prompts)
        r_in = sum(READER_REQUESTS * (len(p.encode("utf-8")) + context_bytes) + READER_OUT for p in prompts.values())
        r_out = rr * READER_OUT
        tin, tout = runs * (w_in + r_in), runs * (w_out + r_out)
        usd = (tin * price["input_cache_miss"]["peak"] + tout * price["output"]["peak"]) / price["per_tokens"]
        per_arm[arm] = {"writer_requests": runs * wr, "writer_in_tokens": runs * w_in, "writer_out_tokens": runs * w_out,
                        "reader_requests": runs * rr, "reader_in_tokens": round(runs * r_in),
                        "reader_out_tokens": runs * r_out, "usd": round(usd, 4)}
        total += usd
    return per_arm, total


def forecast(writers: Mapping[str, str | None], session_texts: Mapping[str, Sequence[str]],
             prompts: Mapping[tuple[str, str], str], *, runs: int, max_token_bytes: int,
             measured: Mapping[str, Any] | None = None, price: Mapping[str, Any] = DEEPSEEK_FLASH_PRICE) -> dict:
    """The smoke's forecast - written into the preflight record before the first spawn. Two numbers (Q-A6-3): the
    guaranteed upper bound (FORECAST_FORMULA; R-BAL takes it) and, when ``measured`` is given
    (bytes_per_cl100k_token over the smoke's own session text, with its "source"), an estimate that is NOT a bound: the
    same formula with the reader's context at the measured bytes per cl100k token instead of the longest token's.
    ``writers``: arm -> its writer LLM (None: no writer); ``session_texts``: unit -> its sessions' text as the writer
    gets it; ``prompts``: (unit, qid) -> the reader prompt with an empty context. Hours are not forecast (Q-A6-2): the
    stand's wall ceiling bounds them. An arm whose writer has no bound, or another model, refuses."""
    for arm, llm in writers.items():
        if llm is not None and llm != price["model"]:
            raise CLIError(f"arm {arm}: its writer is {llm!r}, not {price['model']} - the price does not apply")
        if llm is not None and arm not in WRITER_BOUNDS:
            raise CLIError(f"arm {arm}: no upper bound for its writer's calls in WRITER_BOUNDS - no forecast, no smoke")
    n_ops = sum(len(v) for v in session_texts.values())
    per_arm, total = _per_arm(writers, session_texts, prompts, runs=runs,
                              context_bytes=READER_CONTEXT_CL100K * max_token_bytes, price=price)
    estimate = None
    if measured is not None:
        est_arm, est_total = _per_arm(writers, session_texts, prompts, runs=runs,
                                      context_bytes=READER_CONTEXT_CL100K * measured["ratio"], price=price)
        estimate = {"note": "estimate, not a bound", "bytes_per_cl100k_token": measured["ratio"],
                    "measured": {k: measured[k] for k in ("bytes", "tokens", "source") if k in measured},
                    "per_arm": est_arm, "usd_total": round(est_total, 4)}
    return {"note": "upper bound, not pilot medians", "formula": FORECAST_FORMULA, "runs": runs,
            "session_ops_per_run": n_ops, "questions_per_run": len(prompts), "max_token_bytes": max_token_bytes,
            "writer_bounds": {a: WRITER_BOUNDS[a] for a in sorted(writers) if writers[a] is not None},
            "price": dict(price), "per_arm": per_arm, "usd_total": round(total, 4), "estimate": estimate,
            "scheduler": "1 model probe (1 output token) before the stand; the gate's 1-token probes are counted after "
                         "the run - not bounded in advance",
            "hours": f"not forecast (Q-A6-2); the stand's wall ceiling is {SMOKE_WALL_CAP_H} h, the worst case "
                     f"{SMOKE_WALL_CAP_H} h plus one unit ceiling"}


#: Q-A6-2 / Q26: the smoke stand's wall ceiling - the declared debug ceiling of the pilot and the smoke, 6 h. A unit
#: already running ends by its own ceiling (scheduler.DEBUG_CEILING_S, also 6 h), so the worst case is 12 h.
SMOKE_WALL_CAP_H = 6.0


class WallCapGate:
    """The stand's wall ceiling (D5, the auditor's condition for the smoke): past ``deadline`` (a monotonic time) no
    new unit starts - admits_new_unit RAISES, so the scheduler's B-OPEN path closes the block and writes STAND END
    (a refusal that only waited would hang the stand). Units already running end by their own ceilings, so the worst
    case is the cap plus one unit ceiling. Before the deadline it defers to the incident gate it wraps."""

    def __init__(self, inner: Any, *, deadline: float, monotonic: Callable[[], float], cap_h: float) -> None:
        self.inner, self.deadline, self.monotonic, self.cap_h = inner, deadline, monotonic, cap_h
        self.tripped = False

    def admits_new_unit(self) -> bool:
        if self.monotonic() >= self.deadline:
            self.tripped = True
            raise CLIError(f"the stand's wall ceiling of {self.cap_h} h is reached - no new unit starts (D5)")
        return self.inner.admits_new_unit() if self.inner is not None else True

    def __getattr__(self, name: str) -> Any:                     # the incident gate's other methods, as they are
        return getattr(self.inner, name)


# ── part 2b: the smoke run ─────────────────────────────────────────────────────────────────────────────────────

#: B-SMOKE-FLAGS: the proxy's flag kinds that are also P0h counts (accounting.proxy_boundary_inputs) - one event each.
P0H_FLAGS = {"canary": "canary_hits", "owner_marker": "owner_marker_hits"}


def cl100k_max_token_bytes(bpe_file: str | os.PathLike) -> int:
    """The longest token of the pinned cl100k vocabulary in bytes - the bound's B (FORECAST_FORMULA)."""
    import base64  # noqa: PLC0415
    return max(len(base64.b64decode(line.split()[0])) for line in Path(bpe_file).read_bytes().splitlines()
               if line.strip())


@dataclass
class SmokeDeps:
    """Everything run_smoke reaches outside this module - main() builds the real ones; a test hands in its own (a tmp
    contract, witnesses over a tmp tree, an in-process proxy, fake arms). ``L`` is the ONE launch module instance every
    part is given (launch's fresh-directory record is per instance, the planner's O3)."""
    contract: Any
    L: Any
    native: Any
    fs: Any
    clock: Any
    ollama_ctl: Any
    monotonic: Callable[[], float]
    environ: Mapping[str, str]
    status_path: Path
    lme_records: Callable[[], Iterable[Mapping[str, Any]]]
    cl100k: tuple                                  # (count, cut) - tokens.cl100k_pair over the pinned file
    cl100k_source: str                             # its pin's sha256, for the estimate's record
    max_token_bytes: int
    truncate: Callable[[str], Any] | None          # §5.1's bge-m3 cut for the item arms
    templates: tuple                               # (S4, S4-cat5)
    locomo_question: Callable[..., str]
    decl: Callable[..., dict]                      # run_v3_plan.python_decl
    make_launcher: Callable[..., Any]              # (arm, ArmRun, *, stand_id, proxy, unit_block, unit_chars) -> launcher
    start_proxy: Callable[..., Any]                # run_v3_proxy.start's signature
    stop_proxy: Callable[[Any], Mapping[str, Any]]
    post: Callable[..., tuple]
    proxy_route: Mapping[str, Any]                 # {"via_port": n} (the declared hop) or a test's {"test_upstream": ...}
    now_utc: Callable[[], str]
    lists_dir: Path | None = None
    s1_sha256: str = S1_IDS_SHA256
    embed_tokenizer: Mapping[str, str] | None = None     # TB7: {path, sha256} of the pinned bge-m3 tokenizer.json
    out: Callable[[str], None] = print
    err: Callable[[str], None] = field(default=lambda s: sys.stderr.write(s + "\n"))


def run_smoke(cfg: RunConfig, *, stand: str, arm_names: Sequence[str], runs: Sequence[str], deps: SmokeDeps) -> int:
    """One smoke stand end to end (rev1 §9.4, Q-12-7): the units, the forecast, the preflight, one Canaries object for
    the homes, the scheduler and the proxy, the proxy, the hooks and the incident gate under the stand's wall ceiling,
    the scheduler's stand, then the gate and the proxy stopped (in that order: the gate's last poll may probe), the
    summary printed as SMOKE_FIELDS lines and written under <runs>/<stand id>/_smoke/ (summary.json, and run.json with
    each arm-run's P0h counters, the proxy's flags by arm and kind, and every problem). 0 only when the stand completed,
    no failure counter (run_v3_smoke.iter_problems), no P0h counter and no proxy flag (B-SMOKE-FLAGS: every
    zero-tolerance kind - model_mismatch, tool_violation, canary, owner_marker, thinking_call, unparsable,
    home_canary_missing) is above 0, the proxy stopped by itself and STATUS self-checks clean; every problem goes to
    stderr by name."""
    c, L = deps.contract, deps.L
    if stand not in SMOKE_STANDS:
        raise CLIError(f"a smoke of {stand} is not possible yet - only {SMOKE_STANDS} (templates.PENDING, §5.6)")
    unknown = sorted(set(arm_names) - set(cfg.arms))
    if unknown or not arm_names:
        raise CLIError(f"arms {unknown or list(arm_names)} are not in the run config's arms {sorted(cfg.arms)}")
    arms = {a: cfg.arms[a] for a in arm_names}
    SC, PL, P = load("scheduler.py", smoke=True), load("run_v3_plan.py", smoke=True), load("run_v3_proxy.py", smoke=True)
    H, G, IN = load("run_v3_hooks.py", smoke=True), load("run_v3_gate.py", smoke=True), load("incidents.py", smoke=True)
    SL, AC, SM = load("status_log.py", smoke=True), load("accounting.py", smoke=True), load("run_v3_smoke.py", smoke=True)
    TP, PT = load("templates.py", smoke=True), load("points.py", smoke=True)
    PL.check_ids(runs, ())
    stand_id, n = next_smoke_id(deps.status_path, stand)

    # the units, their tokens and questions (part 1)
    order = s1_order(deps.lists_dir, expected_sha256=deps.s1_sha256)
    su = s4_smoke_units(deps.lme_records(), order, stand_id=stand_id)
    units = su["units"]
    count, cut = deps.cl100k
    ut = unit_tokens(units, count)
    t4, t4c5 = deps.templates
    questions = questions_for(units, template=t4, template_abstain=t4c5, locomo_question=deps.locomo_question)

    # the forecast (Q-A6-2, Q-A6-3) and the preflight (Q-A6-1): before STAND START and before any spawn
    session_texts = {u.unit_id: [PL.session_text(s) for s in u.sessions] for u in units}
    measured = bytes_per_cl100k_token([t for v in session_texts.values() for t in v], count)
    measured["source"] = (f"cl100k {deps.cl100k_source[:12]} over the {sum(len(v) for v in session_texts.values())} "
                          f"session texts of {stand_id}'s {len(units)} units, before the run")
    prompts = {k: TP.render(t, {"context": "", "question": q}) for k, (t, q) in questions.items()}
    fc = forecast({a: ar.llm for a, ar in arms.items()}, session_texts, prompts, runs=len(runs),
                  max_token_bytes=deps.max_token_bytes, measured=measured)
    pf = preflight(c, L, arms, stand_id=stand_id, config_sha256=cfg.sha256, decl=deps.decl, now=deps.now_utc,
                   forecast=fc)
    # an attempt that fails before STAND START leaves the smoke id unspent (Q-A6-1): the next attempt takes the same
    # id, so the attempt's own directories are named by its preflight line - never "not fresh"
    attempt = f"attempt-{pf['position']:05d}"

    # the boundary: one Canaries object (the auditor's condition), the witnesses, the proxy
    env = dict(deps.environ)
    wiring = boundary_canaries(L, c, env)
    W = L.Witnesses(c, native=deps.native, fs=deps.fs, canaries=wiring["canaries"])
    smoke_dir = Path(c.runs_root) / stand_id / "_smoke" / attempt
    pcfg = P.build_config({a: {"llm": ar.llm, "llm_transport": ar.llm_transport,
                               "embeds_via_ollama": ar.embeds_via_ollama, "reader": True} for a, ar in arms.items()},
                          run_dir=Path(c.runs_root) / stand_id / "_proxy" / attempt, **dict(deps.proxy_route),
                          embed_tokenizer=deps.embed_tokenizer)
    secrets = P.build_secrets(list(arms), roles=("scheduler",), canaries=wiring["proxy"])
    h = deps.start_proxy(c, python=cfg.proxy_python, key_file=cfg.key_file, config=pcfg, secrets=secrets,
                         unit=L.make_unit_dirs(c, stand_id, "_harness", "proxy", attempt), parent_env=env,
                         witnesses=W)
    problems: list[str] = []
    gd = hooks = answer = None
    res = None
    try:
        status = SL.StatusLog(Path(deps.status_path))
        hooks = H.Hooks(repo=c.repo_root, git=cfg.git, proxy=h, projected_cost=fc["usd_total"], currency="USD")
        sched = SC.Scheduler(c, h.control, status, L, deps.clock, deps.ollama_ctl, tag="smoke", witnesses=W,
                             parent_env=env, catcher_url=P.catcher_url(h), hooks=hooks,
                             canaries=wiring["scheduler"]["canaries"],
                             home_canaries=wiring["scheduler"]["home_canaries"])
        blocks = PL.blocks_for(stand_id, [u.unit_id for u in units], block_plan=SC.BlockPlan)
        unit_block = {u: bp.block for bp in blocks for u in bp.units}
        launchers = {a: deps.make_launcher(a, ar, stand_id=stand_id, proxy=h, unit_block=unit_block,
                                           unit_chars={u.unit_id: u.chars for u in units}) for a, ar in arms.items()}
        answer = PL.Answerer(stand_id, questions=questions,
                             reader=lambda arm, run, unit: (h.ports["arms"][arm]["reader"],
                                                            f"/u/{run}.{unit}/v1/chat/completions", h.tokens[arm]),
                             post=deps.post, count=count, cut=cut, answers_root=c.runs_root,
                             reaskable=SC.ReaskableError)
        sp, _pst = PL.stand_plan(stand_id, units, launchers, standplan=SC.StandPlan, read_req=SC.ReadReq, runs=runs,
                                 campaign_seed=cfg.campaign_seed, unit_tokens=ut, medians={}, answer=answer,
                                 embed_tag=cfg.embed_tag, dated=True, points=lambda a: ("B",), k_at=PT.K_AT,
                                 smaps=su["smaps"], truncate=deps.truncate, bodies_dir=h.run_dir)
        hooks.bind(sched, sp)
        model = hooks.model_probe()                    # the gate's expected model (the planner's O1 (a): one 1-token call)
        smoke_dir.mkdir(parents=True, exist_ok=True)
        gd = G.GateDriver(IN.IncidentGate(), calls_path=h.run_dir / "calls.jsonl", status=status,
                          send_probe=probe(deps.post, h.ports["scheduler"], h.tokens["scheduler"]),
                          id_prefix=f"{stand_id}-inc", expected_models=[model],
                          record=lambda rec: L._append_jsonl(smoke_dir / "incidents.jsonl", rec))
        hooks.gate = WallCapGate(gd, deadline=deps.monotonic() + SMOKE_WALL_CAP_H * 3600, monotonic=deps.monotonic,
                                 cap_h=SMOKE_WALL_CAP_H)
        gd.start()
        res = sched.run_stand(sp, blocks, judges=(), order=n)
    except Exception as e:  # noqa: BLE001 - the stand's failure is named; the gate and the proxy still stop below
        problems.append(f"the stand did not complete: {type(e).__name__}: {e}")
        res = getattr(e, "result", None) if res is None else res
    finally:
        if gd is not None:
            gd.stop()
            problems += [f"gate: {p}" for p in gd.problems]
        pstop = dict(deps.stop_proxy(h))
    if pstop.get("killed") or pstop.get("rc") not in (0,):
        problems.append(f"the proxy did not stop by itself: {pstop}")
    smoke_dir.mkdir(parents=True, exist_ok=True)
    record = {"stand": stand_id, "order": n, "attempt": attempt, "arms": sorted(arms), "runs": list(runs), "config_sha256": cfg.sha256,
              "preflight_forecast_usd": fc["usd_total"], "preflight_ok": pf["ok"], "proxy_stop": pstop,
              "wall_cap_h": SMOKE_WALL_CAP_H, "gate": type(getattr(hooks, "gate", None)).__name__,
              "wall_cap_tripped": bool(getattr(getattr(hooks, "gate", None), "tripped", False)),
              "canary_hashes": dict(wiring["canaries"].hashes())}
    log = AC.load_proxy(h.run_dir)
    boundary = {f"{a}/{r}": AC.proxy_boundary_inputs(log.calls, log.catcher, arm=a, run=r) for a in arms for r in runs}
    record["boundary"] = boundary
    # B-SMOKE-FLAGS (the auditor): every zero-tolerance flag the proxy wrote is a problem by kind and arm - a canary or
    # an owner marker is ONE event with its P0h count (said once, in the P0h line); any other kind stands alone
    flags: dict[str, dict[str, int]] = {}
    for fl in log.flags:
        per = flags.setdefault(str(fl.get("arm")), {})
        per[str(fl.get("kind"))] = per.get(str(fl.get("kind")), 0) + 1
    record["flags"] = flags
    for k, b in boundary.items():
        hit = {f: b[f] for f in P0H_FLAGS.values() if b.get(f)}
        if hit:
            arm = k.split("/", 1)[0]
            same = {kind: flags[arm][kind] for kind in P0H_FLAGS if flags.get(arm, {}).get(kind)}
            problems.append(f"P0h: {k} {hit} - a planted canary or an owner marker reached the proxy"
                            + (f" (the same events as {arm}'s flags {same} - counted once)" if same else ""))
    for a, kinds in sorted(flags.items()):
        for kind, cnt in sorted(kinds.items()):
            if kind in P0H_FLAGS and any((boundary.get(f"{a}/{r}") or {}).get(P0H_FLAGS[kind]) for r in runs):
                continue                                             # said in the P0h line above
            problems.append(f"flag: {kind} {a} x{cnt}" + (" - with no P0h count" if kind in P0H_FLAGS else ""))
    if res is not None and answer is not None and "blocks" in res:
        try:
            summ = SM.summarize(res, log, stand=stand_id, key_question=answer.key_question,
                                item_texts=su["item_texts"], run_dir=h.run_dir,
                                no_writer=[a for a, ar in arms.items() if ar.llm is None])
            for line in SM.render(summ).splitlines():
                deps.out(line)
            SM.write(summ, smoke_dir / "summary.json")
            problems += [f"smoke: {p}" for p in SM.iter_problems(summ)]     # its failures by name (the exit code's)
        except Exception as e:  # noqa: BLE001 - named; the record below still says what the stand did
            problems.append(f"no smoke summary: {type(e).__name__}: {e}")
    problems += [f"STATUS: {p}" for p in SL.self_check(Path(deps.status_path))]
    record["problems"] = problems
    with open(smoke_dir / "run.json", "xb") as f:
        f.write(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8"))
    for p in problems:
        deps.err(f"problem: {p}")
    return 0 if not problems else 1


def plan_launcher_factory(cfg: RunConfig, c: Any) -> Callable[..., Any]:
    """main()'s make_launcher: run_v3_plan.PlanLauncher per arm, one CodeStager for the stand."""
    PL, SC = load("run_v3_plan.py", smoke=True), load("scheduler.py", smoke=True)
    stager = PL.CodeStager(c.runs_root)

    def make(arm: str, ar: ArmRun, *, stand_id: str, proxy: Any, unit_block: Mapping[str, str],
             unit_chars: Mapping[str, int]) -> Any:
        return PL.PlanLauncher(arm, stand=stand_id, python=ar.python, proxy=proxy, stager=stager,
                               unit_block=unit_block, embed_tag=cfg.embed_tag, dated=True, unit_chars=unit_chars,
                               extra=ar.extra).launcher(SC.ChildArmLauncher)
    return make


# ── the command line ───────────────────────────────────────────────────────────────────────────────────────────

def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="run_v3.py", description="PREREG-V3 campaign CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("stand", help="run one stand")
    st.add_argument("--stand", required=True)
    mode = st.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--tag", choices=("scored",))
    st.add_argument("--arms", required=True)
    st.add_argument("--runs", required=True)
    st.add_argument("--config", required=True)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.cmd == "stand" and args.tag == "scored":
        raise CLIError("--tag scored waits for A9 (the judges, the change log, the assembly) - refused")
    if args.stand not in SMOKE_STANDS:
        raise CLIError(f"a smoke of {args.stand} is not possible yet - only {SMOKE_STANDS} (templates.PENDING, §5.6)")
    import datetime as dt  # noqa: PLC0415
    import time  # noqa: PLC0415
    L, SC, PL = load("launch.py", smoke=True), load("scheduler.py", smoke=True), load("run_v3_plan.py", smoke=True)
    P, LD, CP = load("run_v3_proxy.py", smoke=True), load("loaders.py", smoke=True), load("corpus_pin_v3.py", smoke=True)
    TK, TP = load("tokens.py", smoke=True), load("templates.py", smoke=True)
    c = L.Contract.default()
    cfg = load_run_config(args.config, secrets_dir=c.secrets_dir)
    pins, hub = Path(c.runs_root) / "_pins", Path(c.hf_home or Path(c.polygon_root) / "hf_cache") / "hub"
    bpe = CP.location("tiktoken_cl100k_base", hf_hub=hub, pins_root=pins)
    bpe_sha = CP.PINS["tiktoken_cl100k_base"]["sha256"]
    bge = CP.location("bge_m3_tokenizer_json", hf_hub=hub, pins_root=pins)
    deps = SmokeDeps(
        contract=c, L=L, native=L.NativeEgressWitness(), fs=L.FsWitness(L.watched_set(c)), clock=SC.SystemClock(),
        ollama_ctl=load("sched_ctl.py", smoke=True).OllamaCtl(), monotonic=time.monotonic, environ=os.environ,
        status_path=Path(c.repo_root) / ".loop" / "campaign-v3-log" / "STATUS",       # PREREG-V3 rev1: the STATUS file
        lme_records=lambda: LD.read_pinned("lme_s_cleaned", hf_hub=hub, pins_root=pins),
        cl100k=TK.cl100k_pair(bpe, expected_sha256=bpe_sha), cl100k_source=bpe_sha,
        max_token_bytes=cl100k_max_token_bytes(bpe),
        truncate=TK.Truncator(TK.bge_m3_spans(bge, expected_sha256=CP.PINS["bge_m3_tokenizer_json"]["sha256"])).cut,
        templates=(TP.stand_template("S4", pins_root=pins), TP.stand_template("S4-cat5", pins_root=pins)),
        locomo_question=lambda q, cat: TP.locomo_question(q, cat, pins_root=pins), decl=PL.python_decl,
        make_launcher=plan_launcher_factory(cfg, c),
        start_proxy=lambda c_, **kw: P.start(c_, spawn=L.spawn_proxy, **kw), stop_proxy=P.stop, post=P.post,
        proxy_route={"via_port": L.network_via_port(c)},
        embed_tokenizer={"path": str(bge), "sha256": CP.PINS["bge_m3_tokenizer_json"]["sha256"]},
        now_utc=lambda: dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return run_smoke(cfg, stand=args.stand, arm_names=[a for a in args.arms.split(",") if a],
                     runs=[r for r in args.runs.split(",") if r], deps=deps)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CLIError as e:
        sys.stderr.write(f"refused: {e}\n")
        sys.exit(2)
