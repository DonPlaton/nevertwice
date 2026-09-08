"""`research/head_to_head.py`: the two guards that decide whether a run may become a row.

Neither needs a model. `accept()` is the rule that a run which retrieved nothing is a harness
failure rather than a product's score - the A-MEM pipeline arm published a row of zeros on
2026-09-06 because nothing checked. `_OllamaChromaEF` is the shim that makes every arm embed
with the same endpoint; chroma asks it for `embed_query` at search time, and the missing method
was what made that arm retrieve nothing in the first place.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import head_to_head as h2h  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def row(**over):
    base = {"recall@1": 0.4, "recall@3": 0.6, "recall@5": 0.7, "recall@10": 0.8, "mrr": 0.5,
            "n": 500, "version": "1.2.3", "label": "Some product", "_wall_s": 12.0,
            "measured_at": {"commit": "abc", "utc": "2026-09-06T00:00:00Z"}}
    base.update(over)
    return base


print("\n- a scored run is passed through untouched -")
good = row()
check("nothing is added or removed", h2h.accept("mem0", good) == good)

print("\n- a run that retrieved nothing is refused -")
empty = row(**{f"recall@{k}": 0.0 for k in h2h.KS}, mrr=0.0)
out = h2h.accept("amem_full", empty)
check("the row becomes a blocker", "blocked" in out and "recall@10" not in out, str(sorted(out)))
check("the blocker names the arm and the size of the run",
      "amem_full" in out["blocked"] and "500" in out["blocked"], out.get("blocked", ""))
check("the numbers are kept under `refused`, not thrown away",
      out["refused"]["recall@10"] == 0.0 and out["refused"]["n"] == 500)
check("the provenance of the run survives",
      out["version"] == "1.2.3" and out["measured_at"]["commit"] == "abc" and out["label"] == "Some product")

print("\n- the rule is about retrieving nothing at all, not about scoring badly -")
weak = row(**{"recall@1": 0.0, "recall@3": 0.0, "recall@5": 0.0, "recall@10": 0.002}, mrr=0.0004)
check("a run that found one answer in five hundred is still a row", h2h.accept("langmem", weak) == weak)
check("an arm that blocked earlier is left as it is",
      h2h.accept("zep", {"blocked": "needs Neo4j"}) == {"blocked": "needs Neo4j"})
empty_pool = row(n=0, **{f"recall@{k}": 0.0 for k in h2h.KS})
check("a run over no questions is left to the caller (n=0 has no meaning here)",
      h2h.accept("mem0", empty_pool) == empty_pool)

print("\n- the embedding shim answers chroma's query-side method -")
seen = []


class _Recording(h2h._OllamaChromaEF):
    """The shim with the network call replaced - `embed_query` must reach `__call__`."""

    def __call__(self, input):                                   # noqa: A002 - chroma's name
        seen.append(list(input))
        return [[0.1, 0.2]] * len(input)


ef = _Recording("bge-m3")
check("embed_query exists", hasattr(ef, "embed_query"))
check("it delegates to __call__ with the same input",
      ef.embed_query(["a question"]) == [[0.1, 0.2]] and seen == [["a question"]], str(seen))
check("chroma's protocol pieces are all present",
      all(hasattr(ef, a) for a in ("name", "get_config", "build_from_config", "default_space",
                                   "supported_spaces", "validate_config", "validate_config_update")))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
