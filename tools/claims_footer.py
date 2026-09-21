#!/usr/bin/env python3
"""Reading the evidence manifest, and the one evidence line a figure caption carries.

This is `tools/render_claims.py` minus the renderers, and it exists for one reason: what a chart
footer needs from the manifest is stable, and the page renderers around it are not.

The tax it removes. `research/_figstyle.py` needs `footer()` so that a caption under a chart and
a number in a table cannot disagree. It used to reach it by importing `render_claims`, which put
that 1,200-line module into the produced_by closure of every command that saves a figure. The
closure is what `tests/_test_freshness.py` compares a claim's commit against, so ANY edit to
`render_claims.py` - adding a renderer for a new generated region, changing a table's column
order, anything with no bearing on any measurement - marked those claims stale.
`forgetting.coverage_gain_at_20pct` was re-stamped by hand seven times for exactly this, and
every one of those re-stamps was a human deciding that a freshness failure did not mean what the
mechanism said it meant. A check people learn to overrule stops being a check.

So the part a figure depends on lives here, where it changes when the manifest's SHAPE changes
and at no other time, and `render_claims.py` imports it back rather than the other way around.
A renderer added tomorrow costs a figure nothing.

`Withdrawn` and `Claims` come with it because `footer()` cannot be used without them, and a
caller holding a `Claims` from one module and passing it to a `footer` from another is the kind
of seam that works until someone adds an isinstance check.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


class Withdrawn(Exception):
    """A renderer asked for a number that is no longer published.

    Raised rather than returned so that withdrawal cannot be forgotten: any renderer that
    touches a stale claim aborts, and the region it was building is replaced by the notice
    the caller writes. Task B8 withdrew 118 of 133 claims at once; a design where publishing a
    withdrawn number requires only *forgetting* a check would not have survived that.
    """

    def __init__(self, claim_id: str, reason: str):
        super().__init__(f"{claim_id} is withdrawn: {reason}")
        self.claim_id = claim_id
        self.reason = reason


class Claims:
    """Lookup over the manifest, so a renderer names a claim id, never a number."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self._by_id = {c["id"]: c for c in manifest["claims"]}

    def value(self, claim_id: str):
        claim = self.get(claim_id)
        if claim.get("stale"):
            raise Withdrawn(claim_id, claim["stale"])
        return claim["value"]

    def is_withdrawn(self, claim_id: str) -> bool:
        return bool(self.get(claim_id).get("stale"))

    def has(self, claim_id: str) -> bool:
        return claim_id in self._by_id

    def get(self, claim_id: str) -> dict:
        try:
            return self._by_id[claim_id]
        except KeyError:
            raise KeyError(f"no claim {claim_id!r} in the manifest") from None


def footer(c: Claims, claim_id: str) -> str:
    """One evidence line for a chart caption: n, dataset, model, commit, command.

    Task C5 puts one of these under every published figure; the renderer lives here so a
    caption cannot say something the manifest does not.
    """
    claim = c.get(claim_id)
    manifest = load_manifest()
    bits = []
    if claim["n"]:
        bits.append(f"n={claim['n']}")
    if claim["dataset"]:
        bits.append(manifest["datasets"][claim["dataset"]]["name"])
    bits.append(claim["unit"])
    env = manifest["environments"].get(claim["environment"] or "", {})
    for key in ("reader", "embedder"):
        if env.get(key):
            bits.append(f"{key}: {env[key]}")
    if claim["ci"]:
        bits.append(f"95% CI {claim['ci']['low']:.3f}-{claim['ci']['high']:.3f}")
    bits.append(f"commit {claim['commit'][:7]}" if claim["commit"]
                else "commit unrecorded")
    bits.append(f"`{claim['command']}`")
    if claim.get("stale"):
        bits.append(f"**withdrawn** - {claim['stale']}")
    return " · ".join(bits)
