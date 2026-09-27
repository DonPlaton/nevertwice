#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A6: research/v3/run_v3.py - the campaign CLI. This part: the smoke's pieces that need no child,
no proxy and no model (the run itself is part 2).

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
  record {status, complete, model}.
* main(): `stand --tag scored` is refused by name until A9; the smoke path is part 2.
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
    raise CLIError("the smoke run is part 2 of A6 - not in this commit")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CLIError as e:
        sys.stderr.write(f"refused: {e}\n")
        sys.exit(2)
