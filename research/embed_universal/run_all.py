import subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent   # scripts resolve against THIS folder, not the
                                         # caller's cwd (review 2026-08 Dc4)
steps = [("gen_corpus.py", "CORPUS"), ("gen_pairs.py", "PAIRS"), ("train.py", "TRAIN"), ("evaluate.py", "EVAL")]
for script, tag in steps:
    t0 = time.time()
    r = subprocess.run([sys.executable, "-u", str(HERE / script)], cwd=str(HERE))
    print(f"[chain] {tag} rc={r.returncode} ({time.time()-t0:.0f}s)", flush=True)
    if r.returncode != 0:
        print("[chain] ABORT", flush=True)
        break
else:
    print("[chain] ALL STEPS COMPLETE", flush=True)
