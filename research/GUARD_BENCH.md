# Guards on the tool-call hook: catch rate, false alarms, and the cheap rivals

The README says a guard distilled from a past mistake fires when the agent is about to repeat
it, at zero context tokens until it does. That is a statement about a mechanism working. This
page is the measurement the statement lacked: how often the guard catches the repeat, how often
it cries wolf on code that merely looks like one, and whether anything cheaper does the same
job. Every arm is read at the same false-alarm budget, with the machinery of
[`matched_conditions.py`](matched_conditions.py), so no arm wins by firing more.

## The corpus

[`research/data/guard_bench_v1.json`](data/guard_bench_v1.json), written deterministically by
[`gen_guard_bench.py`](gen_guard_bench.py) from a table anyone can audit. A case is one mistake
note - title, what happened, prevention, the three fields the guard generator reads - with tool
calls that repeat it (Edit, Write or Bash payloads, phrased differently) and tool calls that do
not, at least one of them *hard*: the same identifiers, used correctly. Two families: **generic**
pitfalls a standard linter models (a bare `except`, `shell=True`, a hard-coded key), where the
table also says whether `ruff`, `bandit` or a scanner fires - scored in the rival's favour, as
[`BASELINES.md`](BASELINES.md) does; and **project** pitfalls only this project's history holds
(the store that must not be written, the argument that is milliseconds, the daemon that is not
ours), which no linter knows. The counts are in the register.

## Arms

* **guards, engine's no-model patterns** - `propose_from_mistake(use_llm=False)`: an anti-pattern
  rule where one applies, else the most distinctive code token lifted from the note.
* **guards, model-written patterns** - the same generator with the local model writing the
  regex, cached per note; the arm blocks itself above one empty pattern in ten.
* **cold-start pack** - the eleven patterns every project gets with no history.
* **linter or scanner** - reads the table: fires on every repeat the table says it catches and
  never on a negative.
* **prompt recall** - `api.recall` over the mistake notes with the tool call as the query; a
  catch when the right note is in the top three; charged the three notes' tokens on every call,
  because that is what injection costs whether or not the note was needed.
* **silence** - the floor.

## Reading

Recall of the *right* guard at a false-alarm budget of one negative in twenty or fewer, its
precision, the false-alarm rate on the hard negatives alone, recall on the project family alone,
tokens per call and latency per check. A guard that fires on the right risk but names the wrong
mistake is a false alarm, not partial credit.

<!-- claims:guard-bench -->
| arm | recall of the right guard | precision | hard-negative false alarms | project-only recall | tokens / call | ms / check |
|---|---|---|---|---|---|---|
| **guards, engine's no-model patterns** | 0.380 at FPR 0.179 (over budget) | - | 0.268 | 0.154 | 3.320 | withdrawn |
| **guards, model-written patterns** | 0.348 at FPR 0.155 (over budget) | - | 0.225 | 0.404 | 5.975 | withdrawn |
| cold-start pack (no history) | 0.196 | 0.818 | 0.000 | 0.000 | 1.150 | withdrawn |
| linter or scanner (scored in its favour) | 0.457 | 1.000 | 0.000 | 0.154 | 0.000 | withdrawn |
| prompt recall over the notes (top three) | 0.022 | 0.400 | 0.042 | 0.038 | 103.560 | withdrawn |
| silence (floor) | 0.000 | - | 0.000 | 0.000 | 0.000 | withdrawn |

<sub>**Withdrawn** - ms / check for guards, engine's no-model patterns, guards, model-written patterns, cold-start pack (no history), prompt recall over the notes (top three): a timing is published only from an observe-mode run on an idle machine; the campaign timed it in pace mode beside other GPU work; the value is the campaign's and the statement predates it - rewrite the statement before any restore</sub>

<sub>**Withdrawn** - ms / check for linter or scanner (scored in its favour), silence (floor): a timing is published only from an observe-mode run on an idle machine; the campaign timed it in pace mode beside other GPU work</sub>

<sub>no operating point under the false-alarm budget for guards, engine's no-model patterns, guards, model-written patterns - a guard fires or it does not, and firing catches the repeats shown at the false-alarm rate shown; their hard-negative and project-only cells are read where the arm fires, not at the budget the rows below use.</sub>
<!-- /claims:guard-bench -->

The gate written before the run (`.loop/GOAL-CLOSE.md`, J6): a guard arm keeps the README's
sentence if it catches at least half the repeats at the budget, beats the linter on the project
family, and spends at most a tenth of prompt recall's tokens. **Missed**, on the clause that
decides it: neither guard arm has an operating point under the budget, because a guard is binary -
the threshold a matched comparison would sweep lives inside the regex, and the regex either fires
or does not. Firing, the engine's patterns catch a little over a third of the repeats and
false-alarm on a sixth of the calls that repeat nothing; the model-written patterns catch the same
share overall. The deterministic generator lifts the most distinctive token from the note, and that
token is exactly what a hard negative shares with the repeat.

The other two clauses can be read for the first time, because until 2026-09-18 the stand computed
the class split only for arms that reached the budget - so the two arms the project-class question
is about were the only ones with no project figure, and the clause was scored as missed for want of
a number. Read now, at each arm's own firing rate rather than at the budget: the model-written
patterns catch about two and a half times the project-class repeats the linter catches, and the
engine's no-model patterns catch exactly what the linter does - which is what the ledger recorded in
track O before the model arm had a row. Tokens: both guard arms spend a small fraction of what
prompt recall spends, inside the tenth the clause allows. The figures are withdrawn until the
campaign that follows the part-4 review re-measures them; the table above prints whatever is live.

That does not turn the gate: it was written at a false-alarm budget this arm never reaches, and a
gate is not re-judged after the run it failed. What it says is narrower and worth stating exactly.
On the class a linter cannot reach - the facts only this project's history holds - the model-written
guard catches two and a half times what the linter does, and pays for it with a false alarm on
about a sixth of the clean calls, where the linter pays none. The linter catches most generic
repeats and few project ones at no false alarms; prompt recall over the notes catches nothing at
any threshold and spends the most tokens. The README's sentence is unchanged by this page and says
only what was measured at the budget. The next mechanism, if there is one, needs its gate written
first: a guard that reads the surrounding lines or the file's role, not a longer regex.

## What this does not show

- Single-author, synthetic, small. It supports a reading at a matched false-alarm rate, not a
  population estimate.
- A repeat is the author's idea of a repeat; a real agent may phrase one in a way no row here
  anticipates. The stand measures the rows.
- The linter column is generous by design: where it was arguable whether a rule would fire, the
  answer recorded is yes.

## Reproducing

```
python research/gen_guard_bench.py --check
python research/guard_bench.py --llm --save
```
