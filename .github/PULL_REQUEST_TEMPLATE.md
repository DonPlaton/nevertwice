**What this changes**

<!-- One or two sentences. Link the issue if there is one. -->

**How it was verified**

<!-- Which tests you ran or added. `python tests/_test_<area>.py` per suite. -->

**Checklist**

- [ ] Tests pass locally (the suites under `tests/` you touched, or all of them)
- [ ] No new required dependencies (the core stays stdlib-only)
- [ ] Hot paths stay quiet: no prints or network calls on SessionStart / PreToolUse
- [ ] Docs updated if behavior or config changed
