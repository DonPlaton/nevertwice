#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A2: the hooks the scheduler calls around a stand (scheduler._run_stand) - the tree check, the
model probe, the balance preflight and the barrier read (rev1 §1.3, §4.5, D8, D10, Q10, Q24; the auditor's B-CL).

* tree_check (Q10, D10): git's one fixed argv (tree_check.argv) as a child of the scheduler's one spawn path, its
  repository the one argv exception; a non-zero exit refuses by name; parse + verdict against the anchor - FREEZE-V3's
  for a scored stand (none given: refused), the listing's own HEAD before there is one (smoke, debug). The stand plan's
  commit and dirty flag are set from it, so the run records say what the tree was (Q2).
* model_probe (D8, Q24): a 1-token completion on the scheduler's own port with thinking disabled and no /u/ prefix -
  the call is recorded as arm=scheduler, outside every unit's attribution (R4); the answer's model, which STATUS
  carries, must be one word.
* preflight (§4.5): GET /user/balance on the scheduler's port, each answer appended to <runs>/_launch/balance.jsonl
  (chained), a balance that was not read named as such. A 402 halts; a balance below 2x the stand's projected cost
  halts (incidents.balance_ok), and so does a scored stand with no projection. R-BAL (§4.5: "if the PILOT finds the
  balance endpoint unavailable, the 402 halt alone applies"): a scored stand whose balance was not read halts unless
  FREEZE-V3 declares that fallback (balance_fallback - the pilot's finding); a smoke or debug stand records it and goes
  on. The currency is FREEZE-V3's, the projection's.
* barrier_read: the change-log reader's answer, or {"changelog": None} when there is none - a smoke stand then writes
  "unread" and a scored stand is refused by the scheduler (B-CL). The reader itself is A7.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

HERE = Path(__file__).resolve().parent
LOOPBACK = "127.0.0.1"
PROBE_PATH = "/chat/completions"                 # DeepSeek's chat path; the proxy forwards the /chat/ prefix
BALANCE_PATH = "/user/balance"                   # allowed on the scheduler's port only (_llm_proxy)
PROBE_BODY = {"messages": [{"role": "user", "content": "ok"}], "max_tokens": 1, "thinking": {"type": "disabled"}}
TREE_TIMEOUT_S = 120.0
HTTP_TIMEOUT_S = 60.0


class HookError(RuntimeError):
    """A hook could not give the scheduler a value it may write, or the stand must not start (§4.5 halts)."""


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def _tree_mod():
    return _load("v3_tree_check_for_hooks", HERE / "tree_check.py")


def _incidents():
    return _load("v3_incidents_for_hooks", HERE / "incidents.py")


def _http(port: int, method: str, path: str, token: str, body: Any = None) -> tuple[int | None, Any]:
    """One loopback request with the scheduler's bearer token: (status, parsed JSON or raw text), or (None, error)."""
    conn = http.client.HTTPConnection(LOOPBACK, int(port), timeout=HTTP_TIMEOUT_S)
    try:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": f"Bearer {token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        try:
            return resp.status, json.loads(raw or b"null")
        except ValueError:
            return resp.status, raw.decode("utf-8", "replace")
    except (OSError, http.client.HTTPException) as e:
        return None, {"error": type(e).__name__}
    finally:
        conn.close()


class Hooks:
    """The scheduler's hooks for one stand. ``proxy``: the run_v3_proxy handle (its scheduler port and token);
    ``git``: the pinned git binary; ``anchor``: FREEZE-V3's commit (None before the freeze - smoke and debug only);
    ``projected_cost``: the stand's projected cost in ``currency`` (A9's formula; None - smoke and debug only);
    ``changelog``: the change-log reader (A7), called as changelog(stand, block) -> dict, or None."""

    def __init__(self, *, repo: str | os.PathLike, git: str | os.PathLike, proxy: Any, anchor: str | None = None,
                 projected_cost: float | None = None, currency: str = "USD", balance_fallback: bool = False,
                 changelog: Callable[[str, str], Mapping[str, Any]] | None = None, gate: Any = None,
                 clock: Callable[[], Any] | None = None) -> None:
        self.repo, self.git, self.proxy = Path(repo), Path(git), proxy
        self.anchor, self.projected_cost, self.currency = anchor, projected_cost, currency
        self.balance_fallback = balance_fallback              # R-BAL: FREEZE-V3's record of the pilot's finding
        self.changelog, self.gate, self.clock = changelog, gate, clock
        self.sched = self.sp = None

    def bind(self, sched: Any, sp: Any) -> None:
        """The scheduler and the stand plan these hooks serve (the scheduler takes the hooks at construction)."""
        self.sched, self.sp = sched, sp

    def _need_bound(self) -> None:
        if self.sched is None or self.sp is None:
            raise HookError("the hooks are not bound to a scheduler and a stand plan (bind first)")

    # ── the tree check (Q10, D10) ──────────────────────────────────────────────────────────────────────────────
    def tree_check(self) -> dict:
        self._need_bound()
        TC = _tree_mod()
        if self.anchor is None and self.sched.tag == "scored":
            raise HookError("a scored stand's tree check needs FREEZE-V3's anchor")
        arm_dir = self.sched.c.runs_root / self.sp.stand / "_harness" / "git"
        n = 1                                        # the next fresh tree-<n>: every check has its own directory
        while (arm_dir / f"tree-{n}").exists() or (arm_dir / f"tree-{n}.home").exists():
            n += 1
        repo, git = str(self.repo), str(self.git)

        def build(d: Any) -> Any:
            SC = sys.modules[type(self.sched).__module__]
            return SC.LaunchSpec(argv=tuple(TC.argv(git, repo)), declared={}, path_dirs=(str(self.git.parent),),
                                 argv_exception={i: Path(repo) for i in TC.ARGV_EXCEPTION}, pipes=True,
                                 stderr_path=str(Path(d.home) / "stderr.git.log"))
        child, _d = self.sched.spawn_child(build, role="tree-check", stand=self.sp.stand, run="_harness", arm="git",
                                           unit=f"tree-{n}")
        try:
            out, _err = child.process.communicate(timeout=TREE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            child.kill_tree()
            child.process.wait(10.0)
            raise HookError(f"git status did not finish in {TREE_TIMEOUT_S:.0f} s") from None
        rc = child.process.returncode
        if rc != 0:
            raise HookError(f"git status exited {rc} - no listing, no verdict (Q10)")
        st = TC.parse(out)
        anchor = self.anchor if self.anchor is not None else st.head
        v = TC.verdict(st, anchor=anchor, exists=lambda p: (self.repo / p).exists())
        v["anchor"] = anchor
        v["anchor_source"] = "freeze" if self.anchor is not None else "head"
        self.sp.commit, self.sp.dirty = st.head, not v["clean"]           # Q2: the run records say what the tree was
        return v

    # ── the model probe (D8, Q24) ──────────────────────────────────────────────────────────────────────────────
    def model_probe(self) -> str:
        self._need_bound()
        port, token = self.proxy.ports.get("scheduler"), self.proxy.tokens.get("scheduler")
        if not isinstance(port, int) or not token:
            raise HookError("the proxy has no scheduler port or token (D8's probe runs there)")
        model = _load("v3_run_proxy_for_hooks", HERE / "run_v3_proxy.py").PINNED_MODEL
        status, body = _http(port, "POST", PROBE_PATH, token, {"model": model, **PROBE_BODY})
        if status != 200:
            raise HookError(f"the model probe answered {status}: {body if status is None else ''}".rstrip(": "))
        got = body.get("model") if isinstance(body, dict) else None
        if not (isinstance(got, str) and got and not any(c.isspace() for c in got)):
            raise HookError(f"the model probe's answer names no one-word model ({got!r}) - STATUS cannot carry it")
        return got

    # ── the balance preflight (§4.5) ───────────────────────────────────────────────────────────────────────────
    def preflight(self, stand: str) -> dict:
        self._need_bound()
        port, token = self.proxy.ports.get("scheduler"), self.proxy.tokens.get("scheduler")
        if not isinstance(port, int) or not token:
            raise HookError("the proxy has no scheduler port or token (the balance is asked there)")
        status, body = _http(port, "GET", BALANCE_PATH, token)
        balance = None
        if status == 200 and isinstance(body, dict):
            for info in body.get("balance_infos") or []:
                if isinstance(info, dict) and info.get("currency") == self.currency:
                    try:
                        balance = float(info.get("total_balance"))
                    except (TypeError, ValueError):
                        balance = None
        ok = None if balance is None or self.projected_cost is None \
            else _incidents().balance_ok(balance, self.projected_cost)
        read = "ok" if balance is not None else \
            f"unread: {'no answer' if status is None else f'status {status}'}" + ("" if status != 200 else
                                                                               f" without a {self.currency} balance")
        rec = {"stand": stand, "status": status, "currency": self.currency, "balance": balance, "balance_read": read,
               "projected_cost": self.projected_cost, "balance_ok": ok,
               "utc": (self.clock() if self.clock else self.sched.clock.utc()).isoformat()}
        self.sched.launch._append_jsonl(self.sched.c.runs_root / "_launch" / "balance.jsonl", rec)
        if status == 402:
            raise HookError(f"{stand}: the balance endpoint answered 402 - the stand waits for the owner (§4.5)")
        if self.projected_cost is None and self.sched.tag == "scored":
            raise HookError(f"{stand}: a scored stand needs its projected cost for the balance rule (§4.5)")
        if balance is None and self.sched.tag == "scored" and not self.balance_fallback:
            raise HookError(f"{stand}: the balance was not read ({read}) and FREEZE-V3 declares no fallback - only the "
                            f"pilot's finding lets the 402 rule stand alone (§4.5, R-BAL); the stand does not start")
        if ok is False:
            raise HookError(f"{stand}: balance {balance} {self.currency} is below 2x the projected {self.projected_cost} "
                            f"- the stand waits for the owner (§4.5)")
        return rec

    # ── the barrier read (Q11, D7) ─────────────────────────────────────────────────────────────────────────────
    def barrier_read(self, stand: str, block: str) -> dict:
        if self.changelog is None:
            return {"changelog": None, "reader": None}   # smoke: STATUS says "unread"; scored: the scheduler refuses
        return dict(self.changelog(stand, block))
