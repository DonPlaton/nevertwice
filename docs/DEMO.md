# Demo

`examples/demo.sh` is a self-contained 25-second demo. It seeds a throwaway store with three
lessons, then shows a fresh query recalling the right one (and abstaining on nonsense). Your real
vault is never touched.

```bash
bash examples/demo.sh          # run it (best with Ollama running → semantic recall)
```

## The full-scenario demo — every mechanism, with numbers

`examples/scenario_demo.py` is the complete tour: it seeds a throwaway vault with a realistic
multi-session history of one web-app project, then exercises **every** mechanism end to end on the
real system and prints what each one buys — in tokens and errors prevented. Nothing is mocked,
nothing hits the network.

```bash
python examples/scenario_demo.py            # the narrated tour + a scoreboard
python examples/scenario_demo.py --json     # machine-readable metrics
python examples/scenario_demo.py --scale=20 # the same advantages across ~220 notes / 20 projects
```

On the seeded project it shows, measured live:

| mechanism | what it does | result |
|---|---|---|
| **Recall + token economy** | surface the right lesson for a task | **5.9× leaner** than dumping the store (93 vs 553 tokens) |
| **Guards (A)** | catch a repeat before it happens | 4 guards, **0 tokens** until one fires; benign edits stay silent |
| **Anticipation (B)** | predict the failure the plan is heading toward | flags an N+1 repeat on a *new* endpoint (risk 0.30) |
| **Counterfactual (C)** | "what breaks if I change X?" | a synthesized answer in **2.2× fewer tokens** than a note dump |
| **Supersession** | resolve a contradiction at write time | 1 fact revised; recall returns only the current truth |

At `--scale=20` the same store holds ~220 notes across 20 projects: the recall-vs-dump economy
grows to **44×** (the ratio scales with the store), and the offline guard generator catches **3/4**
of a battery of repeat-actions (the default LLM generator is far more precise — the live study
measures a **−86%** real error rate, `research/LIVE_VALIDATION.md`).

The large-scale *quantitative* benchmarks live in [`../nevertwice/research/`](../nevertwice/research/):
retrieval on 940 LongMemEval sessions, the 200-task improvement-per-token study, the live guard
validation, and the eff-vs-capability curve. This demo is the qualitative counterpart — all
mechanisms, one realistic project, visible advantages.

## The README GIF

The README embeds **`docs/tour.gif`** — a ~45-second tour recorded from the real system on a
throwaway vault. Act 1 is the "memory that acts" beat from `guard_demo.py` (a mistake recorded
once; days later the guard fires *before* the agent repeats it). Act 2 walks the remaining
mechanisms from `scenario_demo.py` with live numbers: recall + token economy, anticipation,
counterfactual, supersession, scoreboard. Every figure on screen is measured during the recording;
nothing is mocked.

To re-record it (or a variant) on Linux/macOS:

```bash
# 1. record (https://github.com/asciinema/asciinema)
asciinema rec -c "python examples/guard_demo.py" guard.cast

# 2. render to GIF (https://github.com/asciinema/agg)
agg --theme monokai --speed 1.2 guard.cast docs/guard.gif
```

On Windows (no asciinema): generate the asciicast v2 file programmatically — run the demo
functions in-process, capture writes with virtual timestamps into `[t, "o", data]` JSONL, then
render with `agg`. That is how `docs/tour.gif` was produced. Keep it under ~3 MB so it loads
inline on GitHub, and land the `⛔ guard fires` line in the first few seconds — that is the
moment that earns the star.
