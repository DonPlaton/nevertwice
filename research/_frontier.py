#!/usr/bin/env python3
"""RESEARCH helper - the billable generator behind gate G8.

F3's grid and F2's session-summary arm were both built with their *scoring* complete and their
*generator* missing, because generating costs the owner money (GOAL G8). This module is that
generator, and nothing else: it turns a prompt into a string using a frontier API, reports what
that cost, and refuses clearly when no key is configured.

It is deliberately a helper (`_`-prefixed, no `__main__`): it imports no project module and
never touches the store, so it neither needs nor may take a sandbox declaration of its own.

Two design decisions that decide whether the frontier cells mean anything:

* **The cell records the model it actually used, not the alias.** `frontier-a` and `frontier-b`
  are slots, not models. Each resolves through an environment variable so the owner picks who
  sits in them, and the resulting artifact names the resolved id - a grid whose rows say
  "frontier-a" and nothing else would be unreadable in six months.
* **The decoding difference is declared, not hidden.** The local cells decode greedily at
  `max_new_tokens=70`. Temperature is not settable on this model family at all, and thinking is
  on by default, so the frontier cells are *not* the same decoding procedure. That is recorded
  in every cell as `decoding` and `caveat` rather than being quietly treated as equivalent.

Cost is computed from a table checked against Anthropic's published prices on 2026-08-26 and
carrying that date, because a price table without one becomes a lie without any edit.
"""
from __future__ import annotations

import os

#: Slot -> environment variable -> default model id. The slots exist so the grid keeps two
#: frontier rows whatever the owner points them at; the defaults are two capability tiers of
#: the same family, which is the honest thing to say about a grid run from one key.
SLOTS = {
    "frontier-a": ("NEVERTWICE_FRONTIER_A_MODEL", "claude-opus-5"),
    "frontier-b": ("NEVERTWICE_FRONTIER_B_MODEL", "claude-sonnet-5"),
}

#: USD per million tokens, (input, output). Source: Anthropic published pricing, 2026-08-26.
#: Sonnet 5's introductory rate ($2/$10) expires 2026-08-31; the standing rate is used here so
#: an estimate printed after that date is not an under-estimate.
PRICES_2026_08_26 = {
    "claude-opus-5":     (5.00, 25.00),
    "claude-opus-4-8":   (5.00, 25.00),
    "claude-sonnet-5":   (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5":  (1.00,  5.00),
    "claude-fable-5":   (10.00, 50.00),
}
PRICES_AS_OF = "2026-08-26"

#: Room for adaptive thinking plus a one-sentence answer. The local cells cap the *answer* at
#: 70 tokens; capping a thinking model at 70 would truncate it mid-reasoning and measure the
#: cap rather than the model.
MAX_TOKENS = 2000

#: The grid asks for one sentence. Low effort is the setting that matches that request; it is
#: recorded in the cell so nobody later reads the row as "the model at full strength".
EFFORT = "low"

KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

#: A key in the environment is NOT approval to spend. It is there because the owner uses this
#: machine, and a suite that calls `main(["--model", "frontier-a"])` to check the refusal path
#: would otherwise bill them for finding out. G8 is a decision, so it gets its own switch, and
#: the switch is not something any test or example sets.
OPT_IN_VAR = "NEVERTWICE_ALLOW_BILLABLE"


class NoCredentials(RuntimeError):
    """Billable calls are not permitted right now - no key, or no explicit approval."""


def resolve(slot: str) -> str:
    """The model id sitting in a slot right now."""
    if slot not in SLOTS:
        raise KeyError(f"unknown frontier slot {slot!r}; known: {sorted(SLOTS)}")
    var, default = SLOTS[slot]
    return os.environ.get(var, default).strip() or default


def credentials_present() -> bool:
    """True when the SDK will find a key. Checks presence only - never prints a value."""
    return any(os.environ.get(v) for v in KEY_VARS)


def opted_in() -> bool:
    """True when the owner has explicitly approved spending on this run."""
    return os.environ.get(OPT_IN_VAR, "").strip() in ("1", "true", "yes", "on")


def billable_allowed() -> bool:
    """Both halves of gate G8: a key exists AND the owner said yes on this run."""
    return credentials_present() and opted_in()


def why_blocked() -> str:
    """One line naming exactly which half of the gate is shut."""
    if not credentials_present():
        return (f"no API key: set {KEY_VARS[0]} (or run `ant auth login`), then set "
                f"{OPT_IN_VAR}=1 to approve the spend")
    return f"a key is configured but the spend is not approved: set {OPT_IN_VAR}=1"


def price(model: str) -> tuple:
    """(input, output) USD per million tokens, or None when the model is not in the table."""
    return PRICES_2026_08_26.get(model)


def estimate(model: str, input_tokens: int, output_tokens: int) -> dict:
    """What a batch of this shape would cost, as data rather than as a printed sentence."""
    p = price(model)
    if p is None:
        return {"model": model, "input_tokens": input_tokens,
                "output_tokens": output_tokens, "usd": None,
                "note": f"{model} is not in the {PRICES_AS_OF} price table; cost unknown"}
    usd = input_tokens / 1e6 * p[0] + output_tokens / 1e6 * p[1]
    return {"model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "usd_per_mtok_in": p[0], "usd_per_mtok_out": p[1],
            "usd": round(usd, 4), "prices_as_of": PRICES_AS_OF}


def generate(model: str, prompt: str, max_tokens: int = MAX_TOKENS) -> tuple:
    """One completion. Returns (text, {input_tokens, output_tokens}).

    `anthropic` is imported here rather than at module scope for the same reason
    `capability_grid.run_local` imports torch inside the function: the core of this repository
    declares no third-party dependency, and a billable path that cannot even be imported
    without one would make every stdlib-only run fail at import time.
    """
    if not billable_allowed():
        raise NoCredentials(why_blocked() + ". Nothing was sent and nothing was billed.")
    import anthropic                                   # noqa: PLC0415 - billable path only

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        output_config={"effort": EFFORT},
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    usage = {"input_tokens": resp.usage.input_tokens,
             "output_tokens": resp.usage.output_tokens}
    return text, usage


def decoding_note(model: str) -> dict:
    """The declaration every frontier cell carries, so no reader assumes matched decoding."""
    return {
        "decoding": f"adaptive thinking, effort={EFFORT}, max_tokens={MAX_TOKENS}",
        "caveat": ("NOT the same decoding as the local cells, which are greedy at "
                   "max_new_tokens=70. Temperature is not settable on this model family and "
                   "thinking is on by default, so this row measures the model as it is "
                   "actually deployed rather than a matched sampler. Compare adoption across "
                   "cells with that in mind."),
        "resolved_model": model,
        "prices_as_of": PRICES_AS_OF,
    }
