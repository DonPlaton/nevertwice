# The frozen environment for reproducing Nevertwice's research (GOAL F7).
#
# There is almost nothing here, and that is the point: the core and every research script run on
# the Python standard library alone, so a "frozen environment" is a pinned interpreter and a copy
# of the repository. Nothing is pip-installed, so there is no lockfile to drift, no wheel to go
# missing from an index, and no transitive dependency that can change under a reader three years
# from now.
#
#   docker build -t nevertwice-repro .
#   docker run --rm nevertwice-repro                 # regenerate and verify every artifact
#   docker run --rm nevertwice-repro --verify        # hash what is committed, regenerate nothing
#
# The image reproduces the DETERMINISTIC artifacts. It deliberately cannot reproduce the three
# that need this machine, a GPU, or a third-party dataset - `research/reproduce.py` lists each
# with the reason, and a run that skipped them silently would be worth less than no run at all.

# Pinned by digest, not by tag: `3.12-slim` moves, and an environment that moves is not frozen.
# Resolved from the registry on 2026-08-26 with
#   docker buildx imagetools inspect python:3.12-slim
# so this line is a fact somebody else can re-derive rather than a number typed from memory.
FROM python@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17 AS repro

# `PYTHONHASHSEED=0` is load-bearing rather than decorative. Set iteration order feeds a
# length-sort tie-break in the extractive-summary baseline, and under a random seed that arm
# produced different counts between runs - the F7 runner is what caught it. The code now breaks
# ties explicitly, so this pin is belt and braces; both are cheaper than a result nobody can
# reproduce.
ENV PYTHONHASHSEED=0 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /nevertwice
COPY . .

# No `pip install`. The core declares zero dependencies, and the research scripts add none.
# If this line ever needs to exist, the reproduction package has become something a reader
# has to trust rather than check.

ENTRYPOINT ["python", "research/reproduce.py"]
