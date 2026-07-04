# Security Policy

Nevertwice is a local-first tool. Your memory store is plain Markdown and Git on your own
machine, the core runs on the Python standard library with no third-party packages, and
nothing leaves your computer unless you opt into a cloud backend with your own API key.
That design removes most of the usual attack surface, but the project still takes security
seriously and welcomes reports.

## Supported versions

The latest release on the default branch is supported. Nevertwice is pre-1.0 in spirit even
at v1.0.0: fixes land on the default branch first.

## Reporting a vulnerability

Please report suspected vulnerabilities privately, not in a public issue.

- Preferred: open a [GitHub private security advisory](https://github.com/DonPlaton/nevertwice/security/advisories/new).
- Alternative: open a regular issue that says only "security report, please open a private
  channel" with no details, and a maintainer will follow up.

Please include the version or commit, your OS and Python version, a minimal reproduction,
and the impact you observed. A fix or a triage response is the goal within a reasonable
window for a solo-maintained project.

## What counts as a vulnerability here

Because the core is stdlib-only and offline by default, the security model centers on a few
specific surfaces:

- Memory or prompt injection that survives the extraction guards and poisons the store.
- Path traversal or symlink escape in the directory sweep, the `watch` daemon, or ingest.
- A way for a hostile transcript, repository, or planted file to make the tool execute code,
  exfiltrate data, or write outside the vault.
- Leakage of an API key or private path into a note, a log, or a commit.
- An outbound request being redirected away from the configured endpoint (SSRF).

## What is out of scope

- Anything requiring an attacker who already controls your shell or environment variables.
  If they can set your `PATH` or your env, they already run code as you.
- The optional cloud backends themselves (report those to the provider).
- Denial of service from a deliberately enormous local file you point the tool at, beyond
  the existing size caps.

## Hardening notes

- The store is the single source of truth and is plain text, so you can read, diff, and
  revert everything the tool writes.
- Secrets are redacted before any transcript is sent to a cloud backend, and provider key
  headers are scrubbed from logged error bodies.
- Outbound URLs are restricted to http and https schemes.
- Every subprocess call uses argument lists, never a shell string.
- No `pickle`, `eval`, `exec`, or YAML object construction is used anywhere in the core.

See [docs/CONFIG.md](docs/CONFIG.md) for the privacy and data-routing controls
(`NEVERTWICE_LOCAL_ONLY`, `NEVERTWICE_CLOUD_ONLY`) that keep chosen projects entirely offline.
