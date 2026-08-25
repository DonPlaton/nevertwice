# Threat model

Nevertwice reads your agent's sessions and writes what it learned into files you own, then
feeds some of that back into a future session. Every one of those steps crosses a boundary
where the thing on one side is trusted and the thing on the other is not, and this document
says which is which.

Two rules keep it from becoming decoration:

- **every boundary names an owner** — the component responsible for enforcing it, so a gap has
  somewhere to be fixed rather than being everybody's problem;
- **every claim names a test** — a check that runs in CI. `tests/_test_threat_model.py` parses
  this file and fails if a claim names a check that does not exist, so the document cannot
  drift away from the code without something going red.

A claim with no test is not a claim, it is a hope. Where this system does *not* defend
something, it is written down as a **residual risk** rather than left out.

## What is trusted

Your own Markdown store, your own machine, and the Python standard library. The core has no
third-party runtime dependencies, so there is no supply chain to compromise below it. Nothing
leaves the machine unless you configure a cloud backend with your own key.

## What is not trusted

Agent transcripts, anything imported from another memory system, note frontmatter (a file can
be hand-edited, synced from another machine, or arrive in a pull request), and every value
crossing an MCP or hook boundary.

---

## Boundary: session capture

- **Owner:** `nevertwice/memory_hook.py` — `redact_secrets`, `_looks_injected`
- **Trusted:** the file paths the host hands over.
- **Untrusted:** every byte of transcript content. A transcript contains whatever the user
  pasted, whatever a tool returned, and whatever a web page said.
- **Claim:** Secrets in eight formats — cloud keys, tokens, inline passwords, private-key
  blocks, bearer JWTs and connection strings — are redacted before anything is written to
  disk. — `tests/_test_security_policy.py::every secret variant is redacted`
- **Claim:** Redaction holds through the whole write path, not only in the function: a note
  carrying a live-looking key does not contain it on disk. —
  `tests/_test_security_policy.py::and the secret is not in the file on disk`
- **Residual risk:** redaction is pattern-based. A secret in a format nobody has written a
  pattern for is stored. The mitigation that does not depend on patterns is that the store is
  local and under your own git.

## Boundary: extraction to store

- **Owner:** `nevertwice/memory_hook.py` write path, via `nevertwice/api.py::remember_lessons`
- **Trusted:** nothing. A "lesson" is whatever an LLM or a caller proposed.
- **Untrusted:** lesson titles, bodies and prevention text.
- **Claim:** A lesson shaped like an instruction is refused rather than stored, and an ordinary
  lesson is still written — a refusal that rejected everything would be worthless. —
  `tests/_test_security_policy.py::an injection-shaped lesson is not written`
- **Claim:** A note title cannot escape the store. Titles containing `../`, `..\`, path
  separators, reserved device names, or 300 characters all resolve inside the vault. —
  `tests/_test_security_policy.py::no title escapes the store`
- **Residual risk:** an instruction that reads as ordinary prose is stored, because it *is*
  ordinary prose. This is the honest limit of shape-based detection, and the defence below it
  is corroboration, not classification.

## Boundary: note file to reader

- **Owner:** `nevertwice/memory_hook.py` — `_read_frontmatter`, `_coerce_recurrence`
- **Trusted:** the store's directory layout.
- **Untrusted:** the contents of any note file. Files are hand-editable by design, sync
  between machines, and in a shared store arrive from other people.
- **Claim:** Only the leading frontmatter block is parsed; a second header planted in the body
  cannot re-assign a note's project or type. —
  `tests/_test_security_policy.py::a second header in the body does not override the project`
- **Claim:** A recurrence count read from a file is bounded. Recurrence is the ranking signal,
  and left unbounded a single note claiming `recurrence: 999999999` outranks the entire store
  forever — memory poisoning through arithmetic rather than through content. —
  `tests/_test_security_policy.py::an absurd recurrence is capped, not trusted`
- **Claim:** A negative or non-numeric recurrence cannot down-weight recall. —
  `tests/_test_security_policy.py::a negative recurrence cannot down-weight recall`

## Boundary: imported memory

- **Owner:** `nevertwice/migrate.py`, writing through the same refusal path as everything else
- **Trusted:** nothing at all. An export is a file from another vendor's system, possibly
  produced by someone else.
- **Untrusted:** every record, including its claimed author and timestamp.
- **Claim:** An injection payload inside a third-party export is not written into the store,
  while the legitimate records in the same file still arrive. —
  `tests/_test_security_policy.py::the injection payload is not written into the store`
- **Claim:** Every imported note is labelled with where it came from, so a borrowed claim can
  never be mistaken for something this store worked out itself. —
  `tests/_test_security_policy.py::every imported note is labelled as imported`
- **Residual risk:** provenance is recorded, not verified. `source_author` is whatever the
  export said.

## Boundary: guard lifecycle

- **Owner:** `nevertwice/outcomes.py`, `nevertwice/guards.py`
- **Trusted:** nothing. Feedback arrives from an agent, and an agent can be manipulated.
- **Untrusted:** outcome reports, session identifiers, and their volume.
- **Claim:** Fifty positive outcomes from one session promote nothing — both promotion and
  demotion count **distinct** sessions. —
  `tests/_test_security_policy.py::fifty accepts from one session promote nothing`
- **Claim:** Unattributed feedback moves the published rates but no threshold, so a caller
  cannot promote or retire a guard by repeating itself. —
  `tests/_test_security_policy.py::unattributed feedback promotes nothing either`
- **Claim:** Displaying a warning is never evidence that it helped; `fired` is telemetry and
  is not an input to the lifecycle. —
  `tests/_test_outcomes.py::displaying a warning proves nothing`

## Boundary: the store on disk

- **Owner:** `sandbox_guard.py` and `tools/check_sandbox.py`
- **Trusted:** the vault the user configured.
- **Untrusted:** every script in this repository. Three separate incidents (2026-08-13, 08-18,
  08-25) had test or example code write to the owner's real store.
- **Claim:** A script that resolves the store without declaring a sandbox is caught
  statically, before it runs — the only place it *can* be caught, because by the time an
  unguarded import has resolved the vault the next write is already addressed to the real
  store. — `tests/_test_properties.py::the lint flags the undeclared script`
- **Claim:** With a real store exported the supported way, a guarded process still resolves
  elsewhere and leaves that store byte-unchanged. —
  `tests/_test_properties.py::THE INCIDENT CLASS: the store did NOT resolve to the exported live path`

## Boundary: the MCP surface

- **Owner:** `nevertwice/mcp_server.py`
- **Trusted:** the transport.
- **Untrusted:** every message. An MCP client is another program, and a malformed or hostile
  message must not take the server down.
- **Claim:** No malformed JSON-RPC message crashes the server — unparseable text, wrong
  version, absent params, a 5000-character method name, a non-scalar id. —
  `tests/_test_properties.py::no malformed message crashes the server`
- **Claim:** A notification (a message with no `id`) is never answered, because answering one
  is a protocol violation that confuses every conforming client. —
  `tests/_test_properties.py::a notification (no id) is never answered`

## Boundary: outbound network

- **Owner:** `nevertwice/config.py` — `NEVERTWICE_LOCAL_ONLY`, the cloud router
- **Trusted:** the endpoints you configured, with your own keys.
- **Untrusted:** the decision about *which* project's content may leave the machine.
- **Claim:** A project on the local-only list never reaches a cloud backend. —
  `tests/_test_failure_injection.py::denylist: 'project_gamma' used cloud`
- **Residual risk:** once content reaches a configured cloud backend, this project's
  guarantees end and the provider's begin.

---

## Known gaps

Written down because a threat model that lists only what it defends is marketing.

- **HTML-comment injection is not detected.** An instruction hidden in `<!-- ... -->` is
  invisible in rendered Markdown and is not currently flagged. Pinned by
  `tests/_test_security_policy.py::KNOWN GAP: an HTML-comment payload is not flagged`, which
  fails the day the gap closes so this section gets updated rather than quietly becoming
  wrong.
- **Plausible-false facts.** A wrong-but-ordinary lesson is indistinguishable by form from a
  correct one; only about a quarter are blocked. Measured, published, and unsolved — see
  [`research/POISONING.md`](../research/POISONING.md).
- **Provenance is recorded, not verified**, for imports and for author fields generally.
- **Pattern-based redaction** cannot cover a secret format nobody has seen.
