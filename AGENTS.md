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
  `5fc308a70719a83cccdbba4c0e39c23f5a8239d5` (v0.20.6, tag `v2026.8.27`) and
  patched in place, leaving intentional tracked working-tree changes. This repo
  vendors none of it.
- **Feature patch** (`patches/voice-and-desktop-features.patch`): auto-CDP
  launch plus loopback binding, internal TTS voice/model runtime overrides,
  and Telegram keyboard cleanup, each with tests. Hermes v0.20.6 already owns
  persistent `browser.cdp_url`, interactive browser connect, and model-facing
  TTS provider/speed arguments; the patch does not duplicate them. It
  deliberately excludes the Discord voice stack and the upstream
  detach-running-turn feature.
- **Config overlay** (`config.example.yaml`): only keys this starter turns on,
  reconciled to the v0.20.6 schema. `delegation.max_concurrent_children` (8)
  is the single unified cap for both synchronous and background children — the
  old `max_async_children` is gone. `code_execution.mode` is `project` or
  `strict` only, with the upstream 300-second/50-call limits. There is no
  `second_brain:` section (not an upstream key).
- **Heavy-work routing.** Long jobs (AI coding CLIs, builds, full test suites)
  run under `terminal(background=true, notify_on_complete=true)` with the
  `process` tool — never `execute_code`, `nohup`, `disown`, `setsid`, or `&`.
- **Subscription-only coding wrappers** (`coder-stack/`): drive your separately
  installed, separately authenticated Claude Code and Codex CLIs. They never call
  a model API directly and never fall back to `OPENROUTER_API_KEY` or other
  provider billing. `setup.sh` only reports whether the CLIs are present.
- **Opt-in Second Brain** (`second-brain/`): a local CLI configured through its
  own manifest, not Hermes config; nothing runs unless you invoke it.
- **Opt-in Messaging bridges** (`messaging/`): macOS-first `launchd` installers
  for a loopback-only WhatsApp Baileys bridge (self-chat trigger, port 3000) and
  a `signal-cli` JSON-RPC daemon (Note-to-Self trigger, `127.0.0.1:8080`). The
  scripts point at the WhatsApp bridge in the pinned upstream checkout
  (`scripts/whatsapp-bridge`) — no upstream bridge code is vendored — render
  `launchd` plist templates, and print pairing/health/env guidance. Nothing runs
  unless you invoke it, and no real numbers, sessions, or machine paths ship.
- **Experimental opt-in Pi runtime** (`modules/pi-runtime/`): a manifest-locked
  distribution for a separate checkout at base
  `306db2776c6b6f1acc85c31c4dabba3263f0e9fd` plus feature
  `c1093d23837bab98013bc9929d0d2679416601e5`. Hermes remains the control plane;
  Pi 0.84.3 is the contained coding runtime. Setup never authenticates, edits
  live config, installs globally, or runs Docker. The immutable linux/arm64
  image is
  `sha256:e89f45110e9277902bafbf49009e842bc9e38180e668fea8a6ff3dcdb2dd2cdf`;
  evidence is 1,419 offline tests and Docker 5/5, while authenticated model E2E
  is a current gap because the prior OAuth expired. Nothing runs unless the
  separate module is explicitly installed and activated.
- This public starter does not reproduce the maintainer's private live setup.
