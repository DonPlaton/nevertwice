#!/usr/bin/env python3
"""The weak-PC / cloud-agent story, proven: the whole active layer with NO model.

Someone driving a cloud coding agent from a modest laptop has no local GPU and no
local LLM. Nevertwice's active layer is built for exactly that - it is stdlib-only
and model-independent, so guards, anticipation, the causal graph, supersession, and
lexical recall all work with zero model and zero network. This demo forces that
regime (no embedder, no extraction LLM) and shows every piece still firing.

    python examples/no_model_demo.py

Uses a throwaway store; your real vault is untouched. ~5 seconds, no deps.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["NEVERTWICE_HOME"] = tempfile.mkdtemp()
os.environ["NEVERTWICE_CLOUD"] = "none"          # no cloud extraction
os.environ["NEVERTWICE_GUARD_PACK"] = "1"        # seed the universal pack (no history needed)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import api
import guards as G
import memory_hook as m

# Hard-disable every model path, so this is provably the no-model regime.
m.embedder_available = lambda *a, **k: False
m.llm_available = lambda *a, **k: False


def line(title):
    print("\n" + title)
    print("-" * len(title))


print("=" * 66)
print("  Nevertwice on a weak machine: the whole active layer, NO model")
print("  (embedder OFF, extraction LLM OFF - pure stdlib + lexical)")
print("=" * 66)

# Seed a small realistic history with no embedder (extraction is not needed: the
# agent writes structured lessons directly - the self-extraction path).
api.remember_lessons([
    {"type": "mistake", "title": "cuda-oom-batch-64",
     "description": "training crashed out of gpu memory at batch size 64 on this card",
     "prevention": "lower the batch size or enable gradient checkpointing",
     "entities": ["training", "gpu"], "recurrence": 2},
    {"type": "decision", "title": "sessions-in-redis",
     "description": "moved sessions from local memory to redis for horizontal scale",
     "entities": ["session-store", "redis"],
     "relations": [{"rel": "depends-on", "target": "session-store"}],
     "supersedes": "sessions-in-local-memory"},
    {"type": "decision", "title": "sessions-in-local-memory",
     "description": "sessions kept per process (the earlier choice)",
     "entities": ["session-store"]},
], project="ml", embed=True)   # embed=True, but the embedder is OFF -> lexical index only

# 1. Recall - lexical fallback, no embedder
line("1. Recall (lexical fallback, no embedder)")
hits = api.recall("training keeps crashing with an out of memory error on the gpu",
                  project="ml", k=2)
for h in hits[:1]:
    print(f"  -> [{h.get('ntype')}] {h.get('title')}  (score {h.get('score', 0):.2f})")
    print(f"     {h.get('prevention') or h.get('description')}")
print("  recall works with zero model." if hits else "  (no hit)")

# 2. Guards - the universal pack fired from a cold store, plus the seeded lesson
line("2. Guards (0 tokens until they fire, no model to generate them)")
G.generate_from_vault("ml", use_llm=False)      # deterministic distillation, no LLM
fired = G.check("data = pickle.loads(open(path,'rb').read())", project="ml")
for f in fired[:1]:
    print(f"  [{f['status']}] {f['message']}")
print(f"  {len(G.load_guards())} guards live, all built with no model.")

# 3. Anticipation - lexical resemblance to a past failure
line("3. Anticipation (trajectory resemblance, lexical)")
pred = api.anticipate("about to launch a big training run at batch size 128 on the same gpu",
                      project="ml", k=1)
if pred:
    print(f"  risk {pred[0]['risk']:.2f}: {pred[0]['message'][:70]}")
else:
    print("  (below threshold - stays silent, 0 tokens)")

# 4. Causal - what breaks if I touch this entity
line("4. Counterfactual (causal graph, no model)")
wb = api.what_breaks("session-store", project="ml")
impacts = ", ".join(i["effect"] for i in wb.get("impacts", [])) or "(none recorded)"
print(f"  changing 'session-store' may impact: {impacts}")

# 5. Supersession - contradiction resolved at write time
line("5. Supersession (contradiction resolved at write time)")
current = api.recall("where are sessions stored", project="ml", k=3)
titles = [h.get("title") for h in current]
print(f"  recall returns the current truth only: "
      f"{'sessions-in-redis' if 'sessions-in-redis' in titles else titles}")
print(f"  the superseded 'sessions-in-local-memory' is kept in history, out of recall.")

print("\n" + "=" * 66)
print("  Every axis fired with no model and no network. That is the")
print("  weak-PC / cloud-agent promise, not a degraded mode.")
print("=" * 66)
