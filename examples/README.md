# Examples

Run these in order; each is stdlib-only and uses a throwaway store (your real vault is untouched).

| Script | What you see | Time |
|---|---|---|
| `python examples/guard_demo.py` | A recorded mistake becomes a guard; the guard fires before the repeat | ~5 s |
| `python examples/scenario_demo.py` | The full loop on a realistic project: recall, guards, anticipation, counterfactual, supersession, with live token counts | ~10 s |
| `python examples/demo.py` | Recall vs dump-everything, measured | ~5 s |
| `python examples/scenario_demo.py --scale=20` | The same scenario on a ~220-note store: token savings grow to ~44x | ~30 s |

`sample-store/` is a tiny pre-seeded vault the demos can read, so nothing here
needs Ollama, a cloud key, or a GPU.
