# H2 — the held-out repositories, chosen and predicted before a single clone ran

**Task H2.** `corpora.py`'s seal is closed while this page is written. Nothing below was chosen by
looking at what a repository contains, because nothing here has been cloned.

Skill: `hypothesis-generation`. Its discipline is the one that matters here — a prediction written
after the data is not a prediction, and `BLAST_RADIUS_D5.md` already has one that failed usefully:
`flask` was selected in C1 as a *hard* block and turned out to be the checker's **best** block at
precision 0.836. A wrong prediction is worth more than no prediction, and it is worth exactly
nothing if it was written afterwards.

---

## 1. What the eight development repositories are, and why that is a problem

`django`, `pytest`, `sphinx`, `scrapy`, `httpx`, `flask`, `fastapi`, `requests`. Web frameworks,
test tooling, documentation tooling, HTTP clients. **One neighbourhood**: pure-Python application
and web infrastructure, heavy on decorators and keyword arguments, light on numerics, no C
extensions on the hot path, no data-plane code, no CLI-shaped programs.

Precision ranged **0.174 to 0.836** across those eight, and `D5` concluded that whatever governs
precision is a property of the codebase. Eight values of that property, drawn from one
neighbourhood, is not a sample of codebases — it is a sample of one kind of codebase measured eight
times.

## 2. Selection criteria, fixed here

Every repository must satisfy all of:

1. **not one of the eight**, and not a fork or vendored copy of one;
2. **multi-author** — ≥ 100 contributors, so the answer key is not one person's habits;
3. **active** — a commit in the last 12 months, so the code is maintained rather than archived;
4. **permissive licence** — MIT, BSD, Apache-2.0, PSF or ISC. No GPL, no unlicensed;
5. **Python 3 today**, with enough history to yield mutants — ≥ 1,000 commits touching `.py`;
6. **a real package**, not a monorepo of examples, not a tutorial, not a collection of notebooks.

Nothing about *what the code looks like* is a criterion, because that is the thing being measured.

## 3. The domains, and why these six

The mechanism reads signatures, references and loops. The properties that plausibly govern how it
behaves are: how densely a codebase annotates, how much of it is `*args` passthrough, how much
lives behind dynamic dispatch, how large its public surface is, and how disciplined its
deprecations are. The six domains are chosen to **vary those properties**, not to be a survey of
Python.

| # | domain | why it should behave differently from the eight |
|---|---|---|
| 1 | **scientific and numeric** | array APIs with enormous keyword surfaces and heavy `*args` passthrough; deprecation ladders measured in years |
| 2 | **data engineering and storage** | dataframe and ORM code where the public surface is generated or dynamically dispatched, so a static reference analysis has less to hold |
| 3 | **infrastructure and CLI** | command-line tools: shallow call graphs, many small entry points, and configuration objects passed everywhere |
| 4 | **async and networking** | protocol code with callback tables and dispatch dictionaries — references that are not calls |
| 5 | **ML libraries and tooling** | large, fast-moving APIs with weak backwards-compatibility policies and frequent signature churn |
| 6 | **packaging, build and typing tooling** | code whose own subject matter is Python structure; unusually high annotation density and unusually careful about compatibility |

## 4. The candidate set — 30 repositories, six domains

Thirty rather than twenty-five, because some will fail a criterion on inspection (archived,
relicensed, too little Python history) and the target is **≥ 25 surviving**.

| domain | repositories |
|---|---|
| scientific / numeric | `numpy/numpy`, `scipy/scipy`, `sympy/sympy`, `astropy/astropy`, `networkx/networkx` |
| data engineering | `pandas-dev/pandas`, `sqlalchemy/sqlalchemy`, `apache/airflow`, `dask/dask`, `pola-rs/polars` (its Python package) |
| infrastructure / CLI | `ansible/ansible`, `pallets/click`, `psf/black`, `pypa/virtualenv`, `saltstack/salt` |
| async / networking | `aio-libs/aiohttp`, `python-trio/trio`, `encode/starlette`, `paramiko/paramiko`, `celery/celery` |
| ML libraries | `scikit-learn/scikit-learn`, `huggingface/transformers`, `pytorch/vision`, `keras-team/keras`, `optuna/optuna` |
| packaging / typing | `pypa/pip`, `pypa/setuptools`, `python/mypy`, `pytest-dev/tox`, `PyCQA/pylint` |

`encode/starlette` shares an author group with `httpx`; it is kept deliberately as the **nearest**
block to the development set, so the out-of-sample drop can be read against a block that is barely
out of sample.

## 5. Per-block predictions, written before the clone

Each is a **candidate prediction**, not a finding. Stated so that a wrong one is visible.

### 5.1 · The headline prediction

> **`blast_radius` under `decidable-only` will lose recall out of sample, and the loss will be
> larger than the loss between any two development blocks.**

Reasoning: the abstention policy emits only what it can *simulate*, and simulation depends on
resolving a call site to a definition. Domains 1, 2 and 5 pass arguments through `**kwargs`
layers far more than the eight do, and every such layer is a site the policy must decline.
`corpus_dev` gave 0.600 [0.55, 0.65]; **the prediction is 0.40–0.55 pooled**, and
`PREREGISTRATION-SHIP.md` set the floor at 0.45 before this was written.

**What would refute it:** pooled recall at or above 0.60, which would say the development set was
not a special case and the in-sample worry was unnecessary.

### 5.2 · Per domain

| domain | prediction | what would refute it |
|---|---|---|
| scientific / numeric | **lowest recall** of the six — `*args`/`**kwargs` passthrough defeats the simulation, and `decidable-only` abstains rather than guessing | recall within 0.05 of the pooled figure |
| data engineering | **lowest precision**, on generated and dynamically dispatched surfaces where a name resolves to something the AST never sees | precision at or above the pooled figure |
| infrastructure / CLI | **closest to the development set** — shallow call graphs are what the eight look like | a recall gap of more than 0.10 either way |
| async / networking | **highest share of `name`-kind references**, so P1 (`no-bare-mentions`) removes more here than anywhere else — callback tables mention functions without calling them | P1 removes a smaller share than in domain 3 |
| ML libraries | **highest base rate of eligible commits** — fast-moving APIs break callers most often | eligible rate below the corpus median |
| packaging / typing | **highest precision** — annotation density makes signatures resolvable, and these projects are careful | precision below the pooled figure |

### 5.3 · The ratchet

> **The ratchet's flag rate will be *higher* out of sample, not lower.**

Reasoning: it fires on any callable that got worse on any of four axes, and the development set is
unusually disciplined about function size. `numpy`, `salt` and `ansible` contain far longer
functions with far more branches. `corpus_dev` gave 0.358; **the prediction is 0.35–0.50**.

**What would refute it:** a pooled flag rate below 0.30, which would mean the eight were harder
than average and the ratchet's silence problem is smaller than it looks.

### 5.4 · `scale`'s static half

> **The mined quadratic class will be larger and the detector's recall will stay near zero.**

`numpy`, `pandas`, `scikit-learn` and `airflow` have performance work as a routine activity, so
message mining should find far more than the 16 candidates eight repositories produced.
`tests/_test_scale.py` pins six shapes the detector cannot see, five of which are the ordinary ways
to write a pairwise scan. **The prediction is ≥ 40 confirmed fixes and recall below 0.20.**

**What would refute it:** recall at or above 0.30, which is the threshold
`PREREGISTRATION-SHIP.md` §3 already set for keeping the static half.

## 6. What this page cannot do

It cannot make the held-out corpus a random sample of Python. Thirty repositories chosen by a
person who has read about Python for years is a **purposive** sample, and every number that comes
out of it is a number about *large, popular, actively maintained, permissively licensed Python
packages* — not about Python code. That sentence belongs beside every pooled figure in Phase V,
and it is written here so it cannot be added later as a caveat that softens a disappointing result.
