#!/usr/bin/env bash
# Anamnesis 25-second demo — the "it remembered" moment, for the README GIF.
# Self-contained: uses a throwaway temp store (your real vault is untouched).
# Best with Ollama running (semantic recall); falls back to lexical without it.
#
#   bash examples/demo.sh
#   # to record a GIF:  asciinema rec -c "bash examples/demo.sh" demo.cast
#   #                   agg demo.cast demo.gif      (https://github.com/asciinema/agg)
set -e
cd "$(dirname "$0")/.."
export ANAMNESIS_VAULT="$(mktemp -d)/anamnesis-demo"
mkdir -p "$ANAMNESIS_VAULT"
say() { printf '\n\033[1;36m%s\033[0m\n' "$1"; sleep 1; }
run() { printf '\033[2m$ %s\033[0m\n' "$2"; sleep 0.6; eval "$2"; sleep 1.2; }

say "① Session one. Your agent hits a bug and learns a lesson:"
run x "python anamnesis/remember.py --project demo --type mistake \
  --title 'CUDA OOM at batch=64 on the GPU' \
  --prevention 'lower batch size or enable gradient checkpointing'"
run x "python anamnesis/remember.py --project demo --type pattern \
  --title 'Crash-safe writes' \
  --prevention 'write to a tmp file then os.replace — never partial files'"
run x "python anamnesis/remember.py --project demo --type decision \
  --title 'Chose Postgres over Mongo' \
  --prevention 'relational integrity mattered more than schema flexibility'"

say "② Days later. A fresh agent, a new prompt. Does it remember?"
run x "python anamnesis/memory_search.py 'training keeps crashing out of gpu memory' demo"

say "③ Different topic — still finds the right lesson, not keyword soup:"
run x "python anamnesis/memory_search.py 'how should I persist files safely' demo"

say "④ And it knows when it doesn't know (calibrated abstention):"
run x "python anamnesis/memory_search.py 'xyzzy nonsense unrelated gibberish' demo"

say "That's it. Plain Markdown + Git. No DB, no server, no cloud. → github.com/DonPlaton/anamnesis"
rm -rf "$(dirname "$ANAMNESIS_VAULT")"
