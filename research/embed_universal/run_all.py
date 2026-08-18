import subprocess, sys, time
steps = [("gen_corpus.py", "CORPUS"), ("gen_pairs.py", "PAIRS"), ("train.py", "TRAIN"), ("evaluate.py", "EVAL")]
for script, tag in steps:
    t0 = time.time()
    r = subprocess.run([sys.executable, "-u", script])
    print(f"[chain] {tag} rc={r.returncode} ({time.time()-t0:.0f}s)", flush=True)
    if r.returncode != 0:
        print("[chain] ABORT", flush=True)
        break
else:
    print("[chain] ALL STEPS COMPLETE", flush=True)
