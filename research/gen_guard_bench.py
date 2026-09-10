#!/usr/bin/env python3
"""Build the guard corpus, deterministically, from a table anyone can audit (ledger J6).

The README says a guard catches a repeated mistake at zero context tokens until it fires. The
mechanism is tested; the *rate* at which it catches repeats, and the rate at which it cries
wolf, has never been measured, and no competitor holds a position in the tool-call hook that a
comparison could be made against. This corpus is the stand's input: past mistakes as the store
would hold them, and tool calls an agent might make later - some repeating the mistake, some
merely near it.

**What a case is.** One mistake note (title, what happened, prevention - the three fields the
guard generator reads), at least two *positive* tool calls that repeat it (Edit, Write or Bash
payloads, phrased differently), and at least three *negatives* that do not, of which at least
one is *hard*: the same identifiers used correctly, the shape a regex over-fires on. Every
call is labelled with the note it should surface, or `null`.

**Two families**, because a guard that loses to a linter is a worse linter:
* `generic` - pitfalls a standard linter or scanner already models (bare `except`, `shell=True`,
  a hard-coded key). Here the fair rival is `ruff`/`bandit`, and the table says whether the
  rival fires - scored in the rival's favour, as `research/cheap_baselines_rules.json` does.
* `project` - facts only this project's history holds: the store that must not be written,
  the argument that is milliseconds, the call that must precede another, the model that must
  not be pulled. No linter knows them. If guards do not beat the linter here, they do not
  earn their place.

Phrasing is varied by hand rather than generated, so a reader can see exactly how wide the
variation is and how narrow the hard negatives are.

    python research/gen_guard_bench.py            # writes research/data/guard_bench_v1.json
    python research/gen_guard_bench.py --check    # verify the committed file matches
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "guard_bench_v1.json"

PROJECT = "svc"          # every note belongs to one synthetic project; scope tests use path globs


def _bash(cmd: str, hard: bool = False) -> dict:
    return {"tool": "Bash", "text": cmd, "hard": hard}


def _edit(path: str, code: str, hard: bool = False) -> dict:
    return {"tool": "Edit", "path": path, "text": code, "hard": hard}


def _write(path: str, code: str, hard: bool = False) -> dict:
    return {"tool": "Write", "path": path, "text": code, "hard": hard}


# ── the table ─────────────────────────────────────────────────────────────────────────────
#
# (stem, family, title, what happened, prevention, linter: (caught, by), positives, negatives)
#
# A negative marked hard=True shares identifiers with the positives and is correct code.

MISTAKES = [
    # ── generic: a standard linter or scanner models these ────────────────────────────────
    ("m-sql-fstring", "generic", "SQL built by f-string",
     "the user id was interpolated into the query with an f-string and execute() ran it; a crafted id read another user's rows",
     "pass parameters to execute(), never build SQL text from variables",
     (True, "bandit S608 / ruff S608 (hardcoded SQL expression)"),
     [_edit("app/db.py", 'cur.execute(f"SELECT * FROM users WHERE id = {user_id}")'),
      _write("app/repo.py", 'def get_user(cur, uid):\n    return cur.execute(f"select name from users where id={uid}").fetchone()')],
     [_edit("app/db.py", 'cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))', hard=True),
      _edit("app/db.py", 'cur.execute("SELECT count(*) FROM users")'),
      _edit("app/log.py", 'log.info(f"loaded user {user_id}")')]),
    ("m-sql-format", "generic", "SQL built by percent formatting",
     "a query string was built with % formatting before execute()",
     "use the driver's parameter binding; the query text must not contain the value",
     (True, "bandit S608"),
     [_edit("app/db.py", 'cur.execute("DELETE FROM sessions WHERE user = \'%s\'" % name)'),
      _edit("app/db.py", 'q = "UPDATE users SET name = \'%s\' WHERE id = %d" % (name, uid)\ncur.execute(q)')],
     [_edit("app/db.py", 'cur.execute("UPDATE users SET name = %s WHERE id = %s", (name, uid))', hard=True),
      _edit("app/fmt.py", 'label = "%s (%d)" % (name, uid)'),
      _edit("app/db.py", 'cur.executemany("INSERT INTO t VALUES (%s)", rows)')]),
    ("m-float-money", "generic", "money kept as float",
     "prices were parsed with float() and a sum came out a cent short",
     "use Decimal for money; never float()",
     (False, "no standard linter models the domain of a float"),
     [_edit("billing/cart.py", "total += float(item.price)"),
      _edit("billing/invoice.py", 'amount = float(row["price"]) * qty')],
     [_edit("billing/cart.py", 'total += Decimal(item.price)', hard=True),
      _edit("physics/sim.py", "velocity = float(row[2])"),
      _edit("billing/cart.py", "count = int(item.qty)")]),
    ("m-json-status", "generic", "response.json() without checking the status",
     "a 500 page was parsed as JSON and the traceback pointed at the parser, not the server",
     "check response.ok or raise_for_status() before .json()",
     (False, "no linter models the order of status check and parse"),
     [_edit("client/api.py", "data = requests.get(url, timeout=10).json()"),
      _edit("client/api.py", "r = session.post(url, json=body)\npayload = r.json()")],
     [_edit("client/api.py", "r = requests.get(url, timeout=10)\nr.raise_for_status()\ndata = r.json()", hard=True),
      _edit("client/api.py", "data = json.loads(text)"),
      _edit("client/api.py", "if r.ok:\n    data = r.json()", hard=True)]),
    ("m-bare-except", "generic", "bare except swallowed a KeyboardInterrupt",
     "a bare except: hid the real error and ate Ctrl-C",
     "catch the specific exception; never a bare except:",
     (True, "ruff E722"),
     [_edit("app/parse.py", "try:\n    n = int(s)\nexcept:\n    n = 0"),
      _write("app/util.py", "def safe(fn):\n    try:\n        return fn()\n    except:\n        return None")],
     [_edit("app/parse.py", "try:\n    n = int(s)\nexcept ValueError:\n    n = 0", hard=True),
      _edit("app/parse.py", "except (TypeError, ValueError) as e:\n    log.warning(e)"),
      _edit("docs/notes.md", "The parser must except nothing silently.")]),
    ("m-mutable-default", "generic", "mutable default argument shared across calls",
     "a list default accumulated values between calls",
     "default to None and create the list inside the function",
     (True, "ruff B006"),
     [_edit("app/collect.py", "def add(x, acc=[]):\n    acc.append(x)\n    return acc"),
      _write("app/opts.py", "def build(name, opts={}):\n    opts[name] = True\n    return opts")],
     [_edit("app/collect.py", "def add(x, acc=None):\n    acc = [] if acc is None else acc\n    acc.append(x)\n    return acc", hard=True),
      _edit("app/collect.py", "def add(x, limit=10):\n    return [x] * limit"),
      _edit("app/collect.py", "DEFAULTS = {}\n")]),
    ("m-mutate-iter", "generic", "list mutated while iterating over it",
     "items were removed from the list inside a for loop over the same list and every other one was skipped",
     "iterate over a copy or build a new list",
     (True, "ruff B909 (mutation during iteration)"),
     [_edit("app/clean.py", "for it in items:\n    if it.stale:\n        items.remove(it)"),
      _edit("app/clean.py", "for k in cache:\n    if old(k):\n        cache.pop(k)")],
     [_edit("app/clean.py", "for it in list(items):\n    if it.stale:\n        items.remove(it)", hard=True),
      _edit("app/clean.py", "items = [it for it in items if not it.stale]"),
      _edit("app/clean.py", "stack.pop()\nqueue.remove(head)")]),
    ("m-eval", "generic", "eval() on user input",
     "a filter expression from the request went through eval()",
     "never eval() dynamic input; parse it or use ast.literal_eval for literals",
     (True, "bandit S307"),
     [_edit("app/filter.py", "result = eval(expr)"),
      _edit("app/cfg.py", 'value = eval(os.environ["FILTER"])')],
     [_edit("app/filter.py", "result = ast.literal_eval(expr)", hard=True),
      _edit("app/filter.py", "def evaluate(expr):\n    return parser.parse(expr)"),
      _edit("docs/eval.md", "Evaluation of the model runs nightly.")]),
    ("m-shell-true", "generic", "subprocess with shell=True and a string command",
     "a filename with a semicolon ran a second command",
     "pass a list and never shell=True",
     (True, "bandit S602"),
     [_edit("app/run.py", 'subprocess.run(f"convert {name} out.png", shell=True)'),
      _edit("app/run.py", "out = subprocess.check_output(cmd, shell=True)")],
     [_edit("app/run.py", 'subprocess.run(["convert", name, "out.png"], check=True)', hard=True),
      _edit("app/run.py", "subprocess.run(cmd, shell=False)"),
      _bash("bash -c 'echo hi'")]),
    ("m-eq-none", "generic", "== None instead of is None",
     "an object with a custom __eq__ compared equal to None",
     "compare with is None / is not None",
     (True, "ruff E711"),
     [_edit("app/x.py", "if value == None:\n    return"),
      _edit("app/x.py", "assert result != None")],
     [_edit("app/x.py", "if value is None:\n    return", hard=True),
      _edit("app/x.py", "if value == NONE_SENTINEL:\n    return"),
      _edit("app/x.py", "none_count = sum(1 for v in vals if v is None)")]),
    ("m-type-eq", "generic", "type(x) == comparison instead of isinstance",
     "a subclass failed the type check",
     "use isinstance()",
     (True, "ruff E721"),
     [_edit("app/t.py", "if type(x) == dict:\n    merge(x)"),
      _edit("app/t.py", "ok = type(v) == str")],
     [_edit("app/t.py", "if isinstance(x, dict):\n    merge(x)", hard=True),
      _edit("app/t.py", "kind = type(x).__name__"),
      _edit("app/t.py", "if x.type == \"dict\":\n    merge(x)")]),
    ("m-os-system", "generic", "os.system ran a shell string",
     "an argument with spaces broke the command and a quote injection was possible",
     "subprocess.run with a list",
     (True, "bandit S605"),
     [_edit("app/sh.py", 'os.system(f"rm -rf {tmp}")'),
      _edit("app/sh.py", 'os.system("git pull")')],
     [_edit("app/sh.py", 'subprocess.run(["git", "pull"], check=True)', hard=True),
      _edit("app/sh.py", "platform = os.system_alias()"),
      _edit("app/sh.py", "info = os.uname()")]),
    ("m-pickle", "generic", "pickle.load on downloaded data",
     "a cache file from a network share was unpickled",
     "json for data; pickle only for data you wrote yourself in the same process family",
     (True, "bandit S301"),
     [_edit("app/cache.py", "obj = pickle.load(open(path, 'rb'))"),
      _edit("app/cache.py", "state = pickle.loads(blob)")],
     [_edit("app/cache.py", "obj = json.load(open(path, encoding='utf-8'))", hard=True),
      _edit("app/cache.py", "pickle_path = base / 'cache.pkl'"),
      _edit("app/cache.py", "torch.save(model.state_dict(), path)")]),
    ("m-yaml-load", "generic", "yaml.load without a safe loader",
     "a config file constructed arbitrary objects",
     "yaml.safe_load",
     (True, "bandit S506"),
     [_edit("app/cfg.py", "cfg = yaml.load(f)"),
      _edit("app/cfg.py", "cfg = yaml.load(text, Loader=yaml.Loader)")],
     [_edit("app/cfg.py", "cfg = yaml.safe_load(f)", hard=True),
      _edit("app/cfg.py", "yaml.dump(cfg, f)"),
      _edit("app/cfg.py", "loader = ConfigLoader(path)")]),
    ("m-verify-false", "generic", "TLS verification disabled",
     "verify=False was left in a client after a local test",
     "never verify=False outside a marked test fixture",
     (True, "bandit S501"),
     [_edit("client/http.py", "requests.get(url, verify=False, timeout=10)"),
      _edit("client/http.py", "session.verify = False")],
     [_edit("client/http.py", "requests.get(url, verify=CA_BUNDLE, timeout=10)", hard=True),
      _edit("client/http.py", "verified = verify_signature(payload)"),
      _edit("client/http.py", "assert resp.ok, 'verify failed'")]),
    ("m-md5-password", "generic", "md5 for a password hash",
     "passwords were hashed with md5",
     "a KDF (bcrypt/argon2/scrypt); md5 and sha1 only for non-security checksums",
     (True, "bandit S324"),
     [_edit("auth/pw.py", "digest = hashlib.md5(password.encode()).hexdigest()"),
      _edit("auth/pw.py", "h = hashlib.sha1(pw.encode()).hexdigest()")],
     [_edit("auth/pw.py", "h = bcrypt.hashpw(pw.encode(), bcrypt.gensalt())", hard=True),
      _edit("auth/pw.py", "digest = hashlib.sha256(blob).hexdigest()"),
      _edit("docs/auth.md", "md5 sums are published for the release archives")]),
    ("m-open-no-with", "generic", "open() without a context manager",
     "a file handle leaked when an exception was raised before close()",
     "with open(...) as f",
     (True, "ruff SIM115"),
     [_edit("app/io.py", "f = open(path)\ndata = f.read()\nf.close()"),
      _edit("app/io.py", "lines = open(path).readlines()")],
     [_edit("app/io.py", "with open(path, encoding='utf-8') as f:\n    data = f.read()", hard=True),
      _edit("app/io.py", "is_open = door.open_state()"),
      _edit("app/io.py", "data = path.read_text(encoding='utf-8')")]),
    ("m-div-len", "generic", "division by len() with no empty guard",
     "average() raised ZeroDivisionError on an empty list",
     "handle the empty case before dividing by len()",
     (False, "no linter models an unguarded divide by len()"),
     [_edit("stats/mean.py", "def average(nums):\n    return sum(nums) / len(nums)"),
      _edit("stats/mean.py", "ratio = hits / len(rows)")],
     [_edit("stats/mean.py", "def average(nums):\n    return sum(nums) / len(nums) if nums else 0.0", hard=True),
      _edit("stats/mean.py", "n = len(rows)"),
      _edit("stats/mean.py", "ratio = hits / max(1, len(rows))", hard=True)]),
    ("m-requests-no-timeout", "generic", "requests call without a timeout",
     "a hung upstream blocked the worker forever",
     "always pass timeout= to requests",
     (True, "bandit S113"),
     [_edit("client/http.py", "r = requests.get(url)"),
      _edit("client/http.py", "r = requests.post(url, json=body)")],
     [_edit("client/http.py", "r = requests.get(url, timeout=(3, 10))", hard=True),
      _edit("client/http.py", "requests_total += 1"),
      _edit("client/http.py", "r = session.get(url, timeout=5)")]),
    ("m-exec", "generic", "exec() on a string from the outside",
     "a plugin string was exec()'d",
     "never exec() dynamic input",
     (True, "bandit S102"),
     [_edit("plugins/load.py", "exec(source)"),
      _edit("plugins/load.py", "exec(compile(code, name, 'exec'))")],
     [_edit("plugins/load.py", "module = importlib.import_module(name)", hard=True),
      _edit("plugins/load.py", "executor.submit(job)"),
      _edit("plugins/load.py", "execution_time = t1 - t0")]),
    # ── project: facts only this project's history holds ──────────────────────────────────
    ("m-live-store", "project", "test code wrote to the owner's live memory store",
     "a test resolved the vault to D:/Obsidian/Claude_Memory and wrote fixtures into it",
     "tests and research scripts run inside sandbox_guard.isolate(); the live store path never appears in test code",
     (True, "tools/check_sandbox.py - a project-local lint that fails the build when a script reaches the store without a sandbox"),
     [_write("tests/_test_new.py", 'VAULT = Path("D:/Obsidian/Claude_Memory")\n(VAULT / "Patterns" / "x.md").write_text("fixture")'),
      _bash('NEVERTWICE_VAULT=D:/Obsidian/Claude_Memory python tests/_test_memory_v3.py')],
     [_write("tests/_test_new.py", 'import _env_guard\nfrom _sandbox import make_sandbox\nd = make_sandbox(m, "new_")', hard=True),
      _edit("docs/CONFIG.md", "The owner's store lives at D:/Obsidian/Claude_Memory and is never touched by tests.", hard=True),
      _bash("python tests/_test_memory_v3.py")]),
    ("m-render-empty", "project", "render_chart(rows) raised on an empty list",
     "render_chart was called on an empty result set and crashed the report",
     "guard with `if rows:` before render_chart(rows)",
     (False, "a project API contract no linter knows"),
     [_edit("report/build.py", "render_chart(rows)\nsave(out)"),
      _edit("report/build.py", "for grp in groups:\n    render_chart(grp.rows)")],
     [_edit("report/build.py", "if rows:\n    render_chart(rows)", hard=True),
      _edit("report/build.py", "chart = render_table(rows)"),
      _edit("report/build.py", "rows = fetch(query)")]),
    ("m-timeout-ms", "project", "set_timeout got seconds, not milliseconds",
     "set_timeout(5) meant five milliseconds and every call timed out",
     "set_timeout takes milliseconds: pass seconds * 1000",
     (False, "a unit convention no linter knows"),
     [_edit("client/conn.py", "client.set_timeout(5)"),
      _edit("client/conn.py", "set_timeout(30)  # thirty seconds")],
     [_edit("client/conn.py", "client.set_timeout(5 * 1000)", hard=True),
      _edit("client/conn.py", "client.set_timeout(TIMEOUT_MS)", hard=True),
      _edit("client/conn.py", "client.set_retries(5)")]),
    ("m-auth-order", "project", "connect() before authenticate() hangs",
     "the client hung because connect() was called before authenticate()",
     "call client.authenticate() before client.connect()",
     (False, "a call-order contract no linter knows"),
     [_edit("client/conn.py", "c = Client()\nc.connect()\nc.authenticate(token)"),
      _edit("client/conn.py", "client.connect()\nclient.send(msg)")],
     [_edit("client/conn.py", "c = Client()\nc.authenticate(token)\nc.connect()", hard=True),
      _edit("client/conn.py", "c.disconnect()"),
      _edit("docs/client.md", "connect() must follow authenticate().")]),
    ("m-lock-finally", "project", "acquire_lock without release in finally",
     "an exception between acquire_lock() and release_lock() left the vault locked",
     "release_lock() in a finally block, or use the context manager",
     (False, "resource pairing across a try is not a linter rule"),
     [_edit("store/write.py", "acquire_lock()\nwrite(note)\nrelease_lock()"),
      _edit("store/write.py", "if acquire_lock(timeout_s=5):\n    do_work()\n    release_lock()")],
     [_edit("store/write.py", "acquire_lock()\ntry:\n    write(note)\nfinally:\n    release_lock()", hard=True),
      _edit("store/write.py", "with vault_lock():\n    write(note)", hard=True),
      _edit("store/write.py", "release_notes = load()")]),
    ("m-force-push", "project", "git push --force rewrote a shared branch",
     "a force push dropped a collaborator's commits",
     "never git push --force; --force-with-lease only on your own branch, and never on master",
     (False, "no linter reads shell commands for git flags"),
     [_bash("git push --force origin invariants/v3"),
      _bash("git push -f origin HEAD")],
     [_bash("git push origin invariants/v3", hard=True),
      _bash("git push --force-with-lease origin feature/x", hard=True),
      _bash("git log --force-with-lease 2>/dev/null; git status")]),
    ("m-global-pip", "project", "pip install into the global interpreter",
     "a competitor package was installed into the system Python and broke the hook's imports",
     "install into the polygon venv: <venv>/Scripts/python.exe -m pip install",
     (False, "no linter reads shell commands"),
     [_bash("pip install mem0ai"),
      _bash("python -m pip install graphiti-core kuzu")],
     [_bash("D:/Coding/_nevertwice_polygon/mem0_eval/.venv/Scripts/python.exe -m pip install mem0ai", hard=True),
      _bash("pip list | grep mem0"),
      _bash("python -m pip download graphiti-core --no-deps -d meta")]),
    ("m-ollama-owner", "project", "ollama pull on the owner's daemon",
     "a model was pulled without asking; the owner's Ollama is not the project's",
     "never ollama pull / rm / stop; use the models that are already listed",
     (False, "no linter reads shell commands"),
     [_bash("ollama pull qwen3:8b"),
      _bash("ollama rm qwen2.5:3b && ollama pull qwen2.5:7b")],
     [_bash("ollama list", hard=True),
      _bash("ollama show qwen3-coder:30b", hard=True),
      _bash("curl -s http://localhost:11434/api/tags")]),
    ("m-secrets-read", "project", "the secrets file was printed",
     "cat .secrets.env put an API key into the transcript",
     "never read, print or commit .secrets.env or twin_calibration.json",
     (True, "a secret scanner on the transcript would flag the key; gitleaks on the commit"),
     [_bash("cat nevertwice/.secrets.env"),
      _bash("git add nevertwice/.secrets.env && git commit -m 'env'")],
     [_bash('echo ".secrets.env" >> .gitignore', hard=True),
      _bash("ls -la nevertwice | grep -c env"),
      _edit("docs/CONFIG.md", "Keys live in `.secrets.env`, which is git-ignored.", hard=True)]),
    ("m-hook-print", "project", "a debug print on the hook's hot path",
     "a print() in memory_hook.py landed in the hook's stdout, which the agent parses as JSON",
     "the hook prints only the payload JSON; log() goes to stderr and the log file",
     (False, "prints are legal Python; the constraint is this file's protocol"),
     [_edit("nevertwice/memory_hook.py", 'print("debug: hits", hits)'),
      _edit("nevertwice/memory_hook.py", "print(f\"recall took {dt:.1f}s\")")],
     [_edit("nevertwice/memory_hook.py", 'log(f"recall took {dt:.1f}s")', hard=True),
      _edit("research/asof_bench.py", 'print(f"  [{i}] {case[\'id\']}")', hard=True),
      _edit("nevertwice/memory_hook.py", "print(json.dumps(payload))", hard=True)]),
    ("m-hook-sleep", "project", "time.sleep on the hook's hot path",
     "a sleep in the PreToolUse path added a visible pause before every edit",
     "never block the hook; retry in the detached catch-up process",
     (False, "a sleep is legal Python"),
     [_edit("nevertwice/memory_hook.py", "time.sleep(2)\nhits = retrieve()"),
      _edit("nevertwice/memory_hook.py", "while busy():\n    time.sleep(0.5)")],
     [_edit("nevertwice/memory_hook.py", "deadline = time.time() + 2", hard=True),
      _edit("research/latency_bench.py", "time.sleep(1)  # let the daemon settle", hard=True),
      _edit("nevertwice/memory_hook.py", "sleep_hint = env_int('NEVERTWICE_SLEEP_HINT', 0)")]),
    ("m-embed-cap", "project", "session text capped at 2000 chars before embedding",
     "our arm embedded the first 2000 characters of each session while the competitors embedded the whole one",
     "embed whole sessions; the cap is the embedder's context, handled by halving on HTTP 400",
     (False, "a slice is legal Python"),
     [_edit("research/longmem_eval.py", "vec = embed(text[:2000])"),
      _edit("research/head_to_head.py", "docs = [s[:2000] for s in pool.values()]")],
     [_edit("research/longmem_eval.py", "vec = embed(text)", hard=True),
      _edit("research/longmem_eval.py", "preview = text[:200]"),
      _edit("research/longmem_eval.py", "chunks = text[:20000]", hard=True)]),
    ("m-cuda-cpu", "project", "training left on the CPU",
     "device was hard-coded to cpu and a run took twelve hours instead of twenty minutes",
     "torch.device('cuda') on this machine; fall back only behind torch.cuda.is_available()",
     (False, "no linter knows the machine has a GPU"),
     [_edit("train/run.py", 'device = torch.device("cpu")'),
      _edit("train/run.py", "model.to('cpu')\nbatch = batch.to('cpu')")],
     [_edit("train/run.py", 'device = torch.device("cuda" if torch.cuda.is_available() else "cpu")', hard=True),
      _edit("train/run.py", 'device = torch.device("cuda")'),
      _edit("train/eval.py", "cpu_count = os.cpu_count()")]),
    ("m-naive-now", "project", "naive datetime.now() mixed with aware timestamps",
     "arithmetic between a naive datetime.now() and an aware parsed timestamp raised TypeError",
     "datetime.now().astimezone() everywhere a timestamp may be compared",
     (False, "naivety of a datetime is not a linter rule"),
     [_edit("nevertwice/memory_hook.py", "started = datetime.now()\nage = started - parsed"),
      _edit("nevertwice/emit.py", "stamp = datetime.now().isoformat()")],
     [_edit("nevertwice/memory_hook.py", "started = datetime.now().astimezone()", hard=True),
      _edit("nevertwice/memory_hook.py", "today = datetime.now().strftime('%Y-%m-%d')", hard=True),
      _edit("nevertwice/memory_hook.py", "now_ms = time.time() * 1000")]),
    ("m-json-ascii", "project", "json.dumps escaped Cyrillic into \\u sequences",
     "a Russian note was written as \\u041f... and Obsidian showed escapes",
     "json.dumps(..., ensure_ascii=False) for anything a person reads",
     (False, "a default argument is not a linter rule"),
     [_edit("nevertwice/digest.py", "out.write_text(json.dumps(data, indent=1))"),
      _edit("tools/render_claims.py", "text = json.dumps(rows)")],
     [_edit("nevertwice/digest.py", "out.write_text(json.dumps(data, indent=1, ensure_ascii=False))", hard=True),
      _edit("nevertwice/digest.py", "data = json.loads(text)"),
      _edit("nevertwice/digest.py", "print(json.dumps(payload))  # hook payload: ASCII-safe by design", hard=True)]),
    ("m-git-add-all", "project", "git add -A swept a cache file into the commit",
     "a generated cache landed in the repository with git add -A",
     "add files by name; never git add -A or git add . in this repository",
     (False, "no linter reads shell commands"),
     [_bash("git add -A && git commit -m 'wip'"),
      _bash("git add . ; git commit -m 'fix'")],
     [_bash("git add research/asof_bench.py tests/research/_test_asof_bench.py", hard=True),
      _bash("git add -p nevertwice/api.py", hard=True),
      _bash("git status --short")]),
    ("m-rm-results", "project", "rm -rf on the results directory",
     "committed artifacts were deleted to 'start clean' and the register lost its raw files",
     "never delete research/results; a re-measure overwrites one artifact at a time",
     (False, "no linter reads shell commands"),
     [_bash("rm -rf research/results"),
      _bash("rm -rf research/results/*.json && python research/asof_bench.py")],
     [_bash("rm -rf /tmp/nevertwice_asof_*", hard=True),
      _bash("ls research/results", hard=True),
      _bash("git checkout -- research/results/asof_v1.json")]),
    ("m-hardcoded-key", "project", "an API key literal in source",
     "a key was pasted into a script and committed",
     "read keys from the environment; never a literal",
     (True, "gitleaks / a secret scanner"),
     [_edit("research/gen.py", 'api_key = "sk-abcdefghijklmnopqrstuvwxyz0123456789"'),
      _edit("research/gen.py", 'headers = {"Authorization": "Bearer csk-abcdefghijklmnopqrstuvwxyz"}')],
     [_edit("research/gen.py", 'api_key = os.environ["CEREBRAS_API_KEY"]', hard=True),
      _edit("research/gen.py", 'api_key = "ollama"  # the OpenAI-compatible endpoint ignores it', hard=True),
      _edit("docs/CONFIG.md", "Set CEREBRAS_API_KEY in .secrets.env.")]),
    ("m-print-secret", "project", "a token was printed to the console",
     "print(token) put the token into the session transcript, which the memory then read",
     "never print a key or token; print its presence or length",
     (False, "what a variable holds is not a linter's business"),
     [_edit("research/gen.py", "print(api_key)"),
      _edit("research/gen.py", 'print("using token", token)')],
     [_edit("research/gen.py", "print(bool(api_key))", hard=True),
      _edit("research/gen.py", 'print("key present:", bool(os.environ.get("API_KEY")))', hard=True),
      _edit("research/gen.py", 'print("tokens per call", n_tokens)')]),
    ("m-open-encoding", "project", "open() without encoding on Windows",
     "a UTF-8 note was read with the cp1251 default and every Cyrillic letter broke",
     "open(path, encoding='utf-8') for every text file",
     (False, "ruff has no default-encoding rule enabled here"),
     [_edit("nevertwice/store_state.py", "with open(path) as f:\n    text = f.read()"),
      _edit("tools/render_claims.py", "lines = open(p).readlines()")],
     [_edit("nevertwice/store_state.py", "with open(path, encoding='utf-8') as f:\n    text = f.read()", hard=True),
      _edit("nevertwice/store_state.py", "with open(path, 'rb') as f:\n    blob = f.read()", hard=True),
      _edit("nevertwice/store_state.py", "text = path.read_text(encoding='utf-8')")]),
    ("m-taskkill-slash", "project", "taskkill /PID under Git Bash",
     "Git Bash rewrote /PID into a path and taskkill printed usage instead of killing",
     "use PowerShell Stop-Process from Git Bash, or taskkill //PID",
     (False, "no linter reads shell commands"),
     [_bash("taskkill /PID 10868 /F"),
      _bash("taskkill /IM python.exe /F")],
     [_bash('powershell -NoProfile -Command "Stop-Process -Id 10868 -Force"', hard=True),
      _bash("taskkill //PID 10868 //F", hard=True),
      _bash("tasklist | grep python")]),
    ("m-cd-compound", "project", "cd inside a compound command",
     "a cd in a compound command triggered a permission prompt and the next command ran elsewhere",
     "use absolute paths or git -C; never cd && in the tool's shell",
     (False, "no linter reads shell commands"),
     [_bash("cd D:/Coding/nevertwice && python -m pytest -q"),
      _bash("cd research; python asof_bench.py --limit 3")],
     [_bash("git -C D:/Coding/nevertwice status", hard=True),
      _bash("python D:/Coding/nevertwice/research/asof_bench.py --limit 3", hard=True),
      _bash("echo $PWD")]),
    ("m-foreground-sleep", "project", "a long sleep in the foreground shell",
     "sleep 300 blocked the tool call and hit the timeout",
     "never sleep in the foreground; poll with a background waiter or a Monitor",
     (False, "no linter reads shell commands"),
     [_bash("sleep 300; tail campaign.log"),
      _bash("while true; do sleep 60; done")],
     [_bash("sleep 2; cat status.txt", hard=True),
      _bash("tail -f campaign.log &", hard=True),
      _bash("python research/asof_bench.py --limit 3")]),
    ("m-vault-env-test", "project", "a test pinned NEVERTWICE_HOME alone",
     "the test pinned NEVERTWICE_HOME but an exported NEVERTWICE_VAULT still won and the live store was written",
     "pin both variables, or call sandbox_guard.isolate(), which does",
     (True, "tools/check_sandbox.py flags a file naming NEVERTWICE_HOME without NEVERTWICE_VAULT"),
     [_write("tests/_test_x.py", 'os.environ["NEVERTWICE_HOME"] = str(tmp)\nimport memory_hook as m'),
      _bash("NEVERTWICE_HOME=/tmp/x python tests/_test_x.py")],
     [_write("tests/_test_x.py", "import _env_guard\nimport memory_hook as m", hard=True),
      _write("tests/_test_x.py", 'os.environ["NEVERTWICE_HOME"] = str(tmp)\nos.environ["NEVERTWICE_VAULT"] = str(tmp)', hard=True),
      _edit("docs/CONFIG.md", "NEVERTWICE_HOME is the legacy name of NEVERTWICE_VAULT.")]),
    ("m-commit-master", "project", "a commit landed on master",
     "work was committed straight to master instead of the working branch",
     "never commit to master; work on invariants/v3 or a feature branch",
     (False, "no linter reads shell commands"),
     [_bash("git checkout master && git commit -am 'quick fix'"),
      _bash("git switch master; git merge invariants/v3; git push")],
     [_bash("git checkout invariants/v3 && git commit -m 'feat: x'", hard=True),
      _bash("git log master..invariants/v3 --oneline", hard=True),
      _bash("git branch --show-current")]),
    ("m-sync-scripts", "project", "the installed hook copy was edited by hand",
     "a file under ~/.claude/scripts was edited directly and the repository drifted from what runs",
     "edit the repository; sync with tools/sync_install.py --apply and only with permission",
     (False, "no linter knows which copy is canonical"),
     [_edit("C:/Users/Platon/.claude/scripts/memory_hook.py", "INJECT_BUDGET_CHARS = 3000"),
      _bash("cp nevertwice/memory_hook.py C:/Users/Platon/.claude/scripts/memory_hook.py")],
     [_edit("nevertwice/memory_hook.py", "INJECT_BUDGET_CHARS = env_int('NEVERTWICE_INJECT_BUDGET', 2200)", hard=True),
      _bash("python tools/sync_install.py --check", hard=True),
      _bash("ls C:/Users/Platon/.claude/scripts")]),
]


def build() -> dict:
    mistakes, calls = [], []
    for stem, family, title, desc, prevention, (caught, by), positives, negatives in MISTAKES:
        assert len(positives) >= 2 and len(negatives) >= 3, stem
        assert any(n.get("hard") for n in negatives), f"{stem}: no hard negative"
        mistakes.append({"stem": stem, "family": family, "project": PROJECT, "title": title,
                         "desc": desc, "prevention": prevention,
                         "linter_or_test": {"caught": caught, "by": by}})
        for i, p in enumerate(positives):
            calls.append({"id": f"{stem}-p{i + 1}", "label": stem, "family": family, **{k: v for k, v in p.items() if k != "hard"},
                          "hard": False})
        for i, n in enumerate(negatives):
            calls.append({"id": f"{stem}-n{i + 1}", "label": None, "family": family, "near": stem,
                          **{k: v for k, v in n.items() if k != "hard"}, "hard": bool(n.get("hard"))})
    return {
        "name": "guard_bench_v1",
        "schema_version": 1,
        "purpose": ("Labelled tool calls for the active-memory stand (ledger J6): past mistakes as notes, "
                    "tool calls that repeat them (positives) and tool calls that do not (negatives, some "
                    "sharing the identifiers) - so a guard's catch rate and false-alarm rate can be read at "
                    "a matched false-positive rate against the cheap rivals: a linter, prompt recall, a "
                    "cold-start pack."),
        "provenance": ("Written by hand in research/gen_guard_bench.py; generic pitfalls follow the "
                       "engine's anti-pattern rules and the live-validation tasks, project pitfalls are "
                       "this repository's own recorded mistakes, restated as a synthetic project's."),
        "limitations": [
            "Single-author, synthetic, and small: it supports a precision/recall reading at a matched "
            "false-positive rate, not a population estimate.",
            "Positives are the author's idea of a repeat; a real agent's repeat may be phrased in a way "
            "no row here anticipates - the stand measures the rows, not the world.",
            "The linter column is scored in the linter's favour, as research/cheap_baselines_rules.json does.",
        ],
        "project": PROJECT,
        "mistakes": mistakes,
        "calls": calls,
        "counts": {"mistakes": len(mistakes),
                   "generic": sum(1 for x in mistakes if x["family"] == "generic"),
                   "project": sum(1 for x in mistakes if x["family"] == "project"),
                   "positives": sum(1 for c in calls if c["label"]),
                   "negatives": sum(1 for c in calls if not c["label"]),
                   "hard_negatives": sum(1 for c in calls if not c["label"] and c["hard"])},
    }


def dump(data: dict) -> bytes:
    return (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="verify the committed file matches this table")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    data = build()
    raw = dump(data)
    digest = hashlib.sha256(raw).hexdigest()
    if args.check:
        p = Path(args.out)
        ok = p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == digest
        print(f"{'ok' if ok else 'MISMATCH'}  {p}  sha256={digest[:16]}")
        return 0 if ok else 1
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_bytes(raw)
    c = data["counts"]
    print(f"wrote {args.out}  mistakes {c['mistakes']} (generic {c['generic']}, project {c['project']})  "
          f"positives {c['positives']}  negatives {c['negatives']} (hard {c['hard_negatives']})  sha256={digest[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
