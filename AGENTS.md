# Operator guidance

Use `execute_code` only for short Python orchestration that combines multiple
tool calls. Its 300-second timeout is a short-orchestration guardrail, not a
general compute budget.

Run AI coding CLIs, training jobs, large builds, full test suites, and iterative
build/test/fix loops with `terminal(background=true, notify_on_complete=true)`.
Track those jobs with the `process` tool until they finish, fail, or require
operator input.

Do not detach work with `nohup`, `disown`, shell `setsid`, or a trailing `&`.
Those mechanisms bypass Hermes process ownership and completion reporting.

The coding runners deliberately keep attempt, stage, wall-clock, and quality
gate budgets independently configurable. Tune the narrowest budget that is
actually limiting the job. In particular, give an individual long-running test
gate a larger `timeout_seconds` in `.hermes-gates.json`; do not increase the
global `code_execution.timeout` to accommodate it.

## What this starter ships (and what it does not)

- **Pinned upstream.** Hermes Agent is cloned at commit
  `3ef6bbd201263d354fd83ec55b3c306ded2eb72a` (v0.19.0, tag `v2026.7.20`) and
  patched in place, leaving intentional tracked working-tree changes. This repo
  vendors none of it.
- **Feature patch** (`patches/voice-and-desktop-features.patch`): auto-CDP
  browser, TTS runtime overrides, Telegram keyboard cleanup,
  each with tests. It deliberately excludes the Discord voice stack and the
  upstream detach-running-turn feature.
- **Config overlay** (`config.example.yaml`): only keys this starter turns on,
  reconciled to the v0.19 schema. `delegation.max_concurrent_children` (8) is the
  single unified cap for both synchronous and background children — the old
  `max_async_children` is gone. `code_execution.mode` is `project` or `strict`
  only. There is no `second_brain:` section (not an upstream key).
- **Heavy-work routing.** Long jobs (AI coding CLIs, builds, full test suites)
  run under `terminal(background=true, notify_on_complete=true)` with the
  `process` tool — never `execute_code`, `nohup`, `disown`, `setsid`, or `&`.
- **Subscription-only coding wrappers** (`coder-stack/`): drive your separately
  installed, separately authenticated Claude Code and Codex CLIs. They never call
  a model API directly and never fall back to `OPENROUTER_API_KEY` or other
  provider billing. `setup.sh` only reports whether the CLIs are present.
- **Opt-in Second Brain** (`second-brain/`): a local CLI configured through its
  own manifest, not Hermes config; nothing runs unless you invoke it.
- This public starter does not reproduce the maintainer's private live setup.
