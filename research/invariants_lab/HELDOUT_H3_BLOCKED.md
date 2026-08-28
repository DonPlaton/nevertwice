# H3 — blocked: there is no network path to any public repository host

**Task H3.** Clone the thirty repositories `HELDOUT_H2.md` selected, census them, and build the
answer key. It did not run, and the reason is not a decision anybody here made.

---

## 1. What was tried, and what each said

Recorded on **2026-08-28**, immediately after `H1` froze the code and opened the seal.

| route | result |
|---|---|
| `git ls-remote https://github.com/pallets/click.git` | `OpenSSL SSL_connect: SSL_ERROR_SYSCALL in connection to github.com:443` |
| `git ls-remote git@github.com:…` | `ssh: Could not resolve hostname github.com` |
| `git` over `ssh.github.com:443` | `Could not resolve hostname ssh.github.com` |
| `git ls-remote https://gitlab.com/…` | same `SSL_ERROR_SYSCALL` |
| `curl https://github.com/` direct | exit 000 |
| `curl https://github.com/` through the machine's proxy | exit 000 |
| `gh auth status` | *"The token in keyring is invalid"* |
| `pip download radon` | `SSL: UNEXPECTED_EOF_WHILE_READING`, three retries |

**DNS resolves nothing.** `socket.gethostbyname` fails with `getaddrinfo failed` for
`github.com`, `pypi.org`, `ollama.com` and `example.com` alike.

**Routing is fine.** Raw TCP to `140.82.121.4:443` (a GitHub address) and to `1.1.1.1:443`
both connect.

**The local proxy resolves names and then carries nothing.** `curl -v -x http://127.0.0.1:10809`
to `codeload.github.com:443` gets `HTTP/1.1 200 Connection established` — the proxy resolved the
name and opened the tunnel — and then:

```
* CONNECT tunnel established, response 200
* schannel: failed to receive handshake, SSL/TLS connection failed
curl: (35) schannel: failed to receive handshake, SSL/TLS connection failed
```

The tunnel is up and its upstream sends nothing back. That is a service on the owner's machine,
and `GOAL.md` §2 puts the owner's machine out of bounds: nothing here reconfigures a resolver,
edits a hosts file, disables certificate verification, or restarts a proxy.

**This is not a blip.** `RATCHET_R3.md` recorded the same two failures on **2026-08-27**, one day
earlier, when `radon` could not be installed and the GitHub fallback clone was refused. It is a
persistent property of this machine.

## 2. What it costs, precisely

| gate | status |
|---|---|
| **G-A** every mechanism is finished | **met** — F1–F5 are done, each named piece built or its absence documented |
| **G-B** out-of-sample numbers at declared thresholds | **UNEVALUABLE.** Not failed — *unevaluated*. There is no held-out corpus and no way to build one |
| **G-C** the memory measurably works better, harm bounded | measurable in part; see below |

**G-B is unevaluable, and that is not the same as failed.** Every threshold in
`PREREGISTRATION-SHIP.md` §3 stands, unmet and untested. Nothing in this run may be reported as
an out-of-sample number, and the sentence *"every published number is in sample"* — which
`GOAL-SHIP.md` §0.3 wrote as the problem to solve — remains true at the end of the run as it was
at the start.

## 3. What was preserved so the work is not lost

The blocking is environmental and temporary. Everything H3 needs is committed and will run
unchanged the moment a name resolves:

- **`HELDOUT_H2.md`** — thirty repositories, six domains, the selection criteria, and a
  falsifiable prediction per block, all committed **before** the block was discovered, so the
  predictions are not contaminated by knowing the corpus was never built;
- **`PREREGISTRATION-SHIP.md`** — every threshold, sample size, test and consequence for Phases V
  and E, with the 29-file code freeze recorded against it;
- **the corpus argument** — every harness takes `--corpus heldout` and writes to a separate
  artifact, so H3 is `clone → census → mutate → measure` with a flag, not a code change;
- **the seal** — open, recorded against the frozen hashes, and
  `tests/_test_corpus_freeze.py` fails if any frozen file moves before Phase V runs.

**The one command H3 needs**, once the network is back:

```bash
python research/invariants_lab/clone_heldout.py            # H2's manifest, into corpus_heldout/
python research/invariants_lab/corpus_census.py  --corpus heldout
python research/invariants_lab/mutate.py         --corpus heldout
python research/invariants_lab/verify_mutants.py --corpus heldout
```

## 4. What was done instead

`GOAL.md` §6.2: *a task that fails twice is `BLOCKED`; write down why, move to the next unblocked
task.* Phase V depends on H3 and is blocked with it. **Phase E does not have to be**: its subject
is whether the memory system works better with the mechanisms than without, and that is a question
about an agent's behaviour rather than about a corpus.

So Phase E was built and run on `corpus_dev`, and **every number it produces is labelled
in-sample** — the mechanisms were tuned on those repositories and an end-to-end result there is
weaker than one on repositories they have never seen. The half of Phase E that does *not* weaken
is **harm**: whether a false flag causes an agent to change correct code is a property of the
mechanism's output and the agent's response, not a generalisation claim about a codebase.
