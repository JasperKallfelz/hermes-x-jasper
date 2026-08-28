# Hermes CLI Starter v0.3.0

> [!WARNING]
> **Pre-1.0.** This starter is under active development. Interfaces, the feature patch, and the config layout may still change between releases; pin what you depend on.

**A reproducible personal-agent layer for [Hermes Agent](https://github.com/NousResearch/hermes-agent).** It keeps the upstream agent intact, then adds a small, auditable set of interaction features and companion modules: durable memory and delegation from Hermes itself; real-browser control, Telegram/TTS ergonomics, subscription-backed coding workflows, a local Second Brain starter, optional self-only messaging bridges, and an experimental opt-in contained Pi coding runtime.

This is not a fork that silently drifts. The installer checks out one **pinned, tested upstream commit**, applies one **reversible patch**, and keeps private configuration and secrets outside this repository. The optional modules are explicitly separated from the core runtime, so you can adopt only the pieces you understand and need.

> [!IMPORTANT]
> **This is an unofficial community starter. It is not affiliated with, endorsed by, or maintained by Nous Research.**
> Hermes Agent itself is theirs (MIT). This repository contains a pinned installer, feature patch, example configuration, voice helpers, an optional Second Brain starter, and an independently maintained public coding-wrapper snapshot. For the core agent, see [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).

---

## Why this setup

- **Power without a black box.** Hermes supplies the agent loop, tools, memory, delegation, code execution, and platform adapters. This starter keeps custom runtime changes visible in one patch and makes optional systems separate modules.
- **Reproducible instead of fragile.** The pinned upstream revision gives the installer, patch, config, documentation, and tests one known base. You can review the exact delta, then upgrade deliberately rather than discover an upstream breakage after the fact.
- **Personal context with clear boundaries.** Your keys, channel allowlists, and normal Hermes state live under `~/.hermes/`, never in git. The Second Brain scans only approved roots; messaging bridges are loopback-only and do nothing until installed.
- **Agentic coding without silent API bills.** The automated coding wrappers are Codex-only, preserve an isolated worktree, run deterministic gates, and use a fresh Codex process for independent review. Persistent Claude Deep Chat is a separate, explicit opt-in tool — never a fallback.
- **Capable interaction surfaces, explicitly enabled.** The patch can attach Hermes to a dedicated, logged-in Chrome debugging profile; Telegram and local TTS are polished without making them load-bearing. WhatsApp and Signal remain opt-in self-chat bridges.
- **Safe to adopt and undo.** Setup is idempotent, config overlays do not clobber existing values, allowlists protect reachable channels, and the patch can be inspected or reversed as a single git diff.

### System at a glance

```text
                  Your requests: local CLI · Telegram · optional self-chat bridges
                                             │
                                             ▼
                            Hermes Agent (pinned upstream core)
                memory · context · tools · delegation · code execution · streaming
                              │                         │
                    private runtime               auditable feature patch
                  ~/.hermes/ config + .env       auto-CDP · TTS overrides · Telegram polish
                              │                         │
                              └───────────────┬─────────┘
                                              ▼
                              Optional local companion systems
       coder-stack: reviewed CLI worktrees · second-brain: approved-root sync · Pi: contained coding
```

The separation is the design: **upstream** remains the agent platform; the **patch** is the small runtime delta; `~/.hermes/` is your private runtime; and `coder-stack/`, `second-brain/`, `messaging/`, and `modules/pi-runtime/` are opt-in companion modules with their own boundaries. The Pi module installs only into a separate checkout and does not alter the stable starter baseline.

---

## What you get

| System | What it does | Origin |
| --- | --- | --- |
| **Persistent memory** | Curated long-term memory plus a user profile, injected into the system prompt | upstream |
| **Context engine** | Built-in compressor, or swap in LCM (Lossless Context Management) | upstream |
| **Delegation** | `delegate_task` spawns subagents; optional routing lets a strong coordinator use faster workers | upstream |
| **Code execution** | `execute_code` runs Python that calls tools over RPC, so bulky intermediate results stay out of the context window | upstream |
| **Streaming + custom TTS** | Token-by-token replies, command-based speech providers, and model-facing per-call TTS provider/speed controls | upstream |
| **Auto-CDP browser** | Uses a dedicated, real Chrome debugging profile rather than a disposable headless session; can start it on demand | **patch** |
| **TTS persona overrides + Telegram polish** | Internal per-call voice/model overrides for transport callers and automatic cleanup of location-request keyboards | **patch** |
| **JARVIS-style voice** | Edge TTS plus an ffmpeg filter chain for a filtered assistant voice | script |
| **Codex-only coding flow + optional Deep Chat** | Codex subscription routing, deterministic gates, isolated worktrees, fresh-process review, and a separately invoked persistent Claude bridge | vendored module |
| **Local Second Brain starter** | Approved-root scanning, local state, dry-run sync, and explicit OpenViking CLI export of approved excerpts | module |
| **Messaging bridges (WhatsApp + Signal)** | Opt-in macOS launchd setup for a loopback WhatsApp self-chat bridge and a `signal-cli` JSON-RPC daemon | module |
| **Experimental Pi RPC runtime** | Hermes-controlled, contained Pi 0.84.3 coding runtime installed into a separate checkout | opt-in module |

**Origin legend:** **upstream** is provided by Hermes Agent; **patch** is added by `patches/voice-and-desktop-features.patch`; **script** lives in `scripts/`; **module** is an independent opt-in component in this repository. Nothing is vendored into the upstream Hermes checkout beyond the explicitly applied patch.

> [!NOTE]
> The Discord voice stack (voice mixer, barge-in, join greeting, streaming STT, voice jobs) has been split out of the main patch and is **not currently shipped** here. It is being rearchitected as an isolated Node media gateway and will return once stable.

---

## Requirements

- **macOS** (Apple Silicon or Intel) or **Linux**
- **Python 3.11+**
- **git**
- **ffmpeg** — required for the JARVIS-style TTS script (`brew install ffmpeg` / `sudo apt install ffmpeg`)
- **An API key** from at least one model provider for ordinary Hermes use (OpenRouter is the easiest single key)
- Optional: a Telegram bot, Google Chrome (for the auto-CDP browser)
- Optional automated coding flow: an installed and authenticated **Codex** CLI.
  The wrappers do not use ordinary Hermes provider API keys.
- Optional persistent Deep Chat: a separately installed and authenticated **Claude Code** CLI.
  Deep Chat is explicit-only and is never an automated fallback.
- Optional Pi module: Git and Python for setup/verification; native
  `linux/arm64` Docker is required only for the explicit image/reproducibility lanes.

---

## Quick start

```bash
git clone https://github.com/JasperKallfelz/hermes-x-jasper.git
cd hermes-x-jasper

./setup.sh --dry-run     # see exactly what it will do — nothing is written
./setup.sh               # do it
```

`setup.sh` is idempotent — re-run it any time. Existing upstream directories
must be either pristine at the pin or match the exact applied patch; staged or
unrelated changes, unexpected origins, and symlinked targets fail closed. It will:

1. Install the public `hermes-coder` and `hermes-coder-flow` wrappers into
   `~/.local/bin` by default. It does not install or authenticate either vendor CLI.
2. Clone **NousResearch/hermes-agent** into `~/hermes-agent` and check out the pinned, tested commit
3. Apply the feature patch with plain `git apply` (skipped if exactly applied)
4. Run **upstream's own** `setup-hermes.sh` whenever the private content-keyed environment marker is missing, stale, or broken
5. Install optional voice dependencies (`--skip-voice` to opt out)
6. Copy the empty `.env` example and, when config was absent before setup, merge missing starter keys after the upstream wizard while preserving its choices

It **never** overwrites a config that genuinely predated setup or an existing `.env`. A config created by the upstream wizard during this setup run is merged with missing starter keys using keep-existing semantics, so wizard selections win. A private `venv/.hermes-starter-complete.json` marker is written only after the exact pin/input/platform/interpreter/voice set, CLI version smoke test, and `pip check` all pass.

Useful flags:

```bash
./setup.sh --install-dir ~/src/hermes-agent   # where upstream gets cloned
./setup.sh --hermes-home ~/.hermes            # where config + state live
./setup.sh --skip-voice                       # no TTS/STT dependencies
./setup.sh --skip-coder-stack                 # do not install coding wrappers
./setup.sh --coder-bin-dir ~/bin              # choose a user-writable PATH directory
./setup.sh --replace-coder-stack              # back up, then replace differing wrappers
```

## Experimental opt-in Pi RPC runtime

[`modules/pi-runtime/`](modules/pi-runtime/README.md) is an explicit opt-in
distribution, separate from the normal `./setup.sh` flow and from the stable
Hermes v0.20.6 starter checkout. Hermes remains the control plane—owning
runtime selection, worktree assignment, approvals, accounting, and delivery
verification—while Pi 0.84.3 runs as the contained coding runtime.

Its immutable contract is Pi base
`306db2776c6b6f1acc85c31c4dabba3263f0e9fd`, reviewed feature
`c1093d23837bab98013bc9929d0d2679416601e5`, and image
`sha256:e89f45110e9277902bafbf49009e842bc9e38180e668fea8a6ff3dcdb2dd2cdf`
for `linux/arm64`. The exact patch is 775,861 bytes. Verified evidence is 1,419
offline tests with zero failures, skips, or retries; two matching clean no-cache
builds; Docker containment/egress E2E 5/5; and an independent READY review with
no P0–P2 findings.

No credentials, auth state, live config, or image bytes are included. There is
no current authenticated provider/model E2E because the previously available
Pi OAuth expired. Authentication, if deliberately performed, is a separate
trusted-local manual flow outside contained execution and is never run by setup
or verification.

Start with a no-write/no-network preview and an explicit separate directory:

```bash
modules/pi-runtime/setup.sh --dry-run /absolute/separate/hermes-pi-runtime
```

See the [module README](modules/pi-runtime/README.md) for exact setup,
verification, manual image build/activation, security boundaries, and rollback.
Rollback means stop using and archive/remove the separate installation; it does
not mutate the stable starter checkout. The independently maintained
`coder-stack/` remains unchanged by this integration.

### Then add your keys

```bash
$EDITOR ~/.hermes/.env      # every value starts empty
```

Pick one provider to begin — `OPENROUTER_API_KEY` gets you almost every model with a single key.

### Run it

```bash
cd ~/hermes-agent
./venv/bin/hermes
```

## Subscription-backed coding tools

The optional code in [`coder-stack/`](coder-stack/) is an exact, public-safe
snapshot of [`JasperKallfelz/hermes-coder-stack`](https://github.com/JasperKallfelz/hermes-coder-stack)
commit `c7fe0ad0d15b26e08635dcac6dfa446a61f0c4fc`, distributed under the **MIT License**. [`release/coder-stack-manifest.json`](release/coder-stack-manifest.json)
pins every file, mode, and SHA-256 digest.

`hermes-coder` and `hermes-coder-flow` are Codex-only automation. They do not
call model APIs directly, reuse `OPENROUTER_API_KEY`, or fall back to another
provider. Each implementation/review stage is a fresh Codex process. The
separate `hermes-deep-chat` command is an explicit persistent Claude tool; it is
never invoked by the automated flow.

Install and authenticate only the CLI for the tool you intend to use, then
check it without starting model inference:

```bash
codex login status
hermes-coder --doctor --requirement codex

# Optional Deep Chat only:
claude auth status
```

`setup.sh` reports whether the commands are present; it never installs a vendor
CLI, opens a login flow, reads auth output, or stores credentials. If
`~/.local/bin` is not already on `PATH`, add it using the normal mechanism for
your shell.

Examples:

```bash
hermes-coder --task implement --lane normal --workdir "$PWD" \
  "Implement the requested change and run its focused tests."

hermes-coder-flow --source "$PWD" --lane auto \
  "Implement the requested change and update its tests."

# Optional persistent Claude session:
hermes-deep-chat start "$PWD" my-chat -- "Initial task"
```

The Codex flow requires a clean source repository and a tracked
`.hermes-gates.json` unless `--no-gates` is explicitly used. It creates and
preserves an isolated branch/worktree for inspection; it does not commit,
merge, rebase, push, publish, or remove the worktree. See
[`coder-stack/README.md`](coder-stack/README.md) for budgets, exit codes, and
the model-free test commands.

---

## Setting up the chat platforms

Everything below goes in `~/.hermes/.env`. **Placeholders only in this repo — never commit a filled-in `.env`.**

### Model provider

```dotenv
OPENROUTER_API_KEY=
# or: NOUS_API_KEY= / OPENAI_API_KEY= / ANTHROPIC_API_KEY= / GEMINI_API_KEY=
```

### Telegram

1. Talk to [@BotFather](https://t.me/BotFather) → `/newbot` → it gives you a token
2. Get your own numeric user id from [@userinfobot](https://t.me/userinfobot)

```dotenv
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USERS=
```

> [!WARNING]
> `TELEGRAM_ALLOWED_USERS` is the only thing standing between your agent and whoever finds the bot. An agent with an empty allowlist will run tools for strangers. Set it.

---

## Architecture: what runs where, and why

```text
hermes-x-jasper (this repository)               private runtime on your machine
──────────────────────────────────               ───────────────────────────────
setup.sh                                         ~/hermes-agent/
  ├─ installs optional coding wrappers             └─ pinned upstream checkout
  ├─ clones NousResearch/hermes-agent                 ├─ upstream agent runtime
  ├─ checks out the tested commit                     └─ one applied feature patch
  ├─ invokes upstream setup-hermes.sh                         │
  └─ applies one auditable patch                              ▼
config.example.yaml ─────────────────────►       ~/.hermes/config.yaml
  safe overlay; existing values win                 runtime settings and preferences
.env.example ────────────────────────────►       ~/.hermes/.env
  placeholders only                                  provider keys + channel allowlists
scripts/jarvis_style_tts.py ────────────►       optional command TTS provider
coder-stack/bin/* ──────────────────────►       ~/.local/bin/hermes-coder{,-flow}

second-brain/, messaging/, modules/pi-runtime/     separate opt-in local modules
  their own manifests/installers                    never started by normal Hermes setup
```

### The layers

1. **Upstream is the foundation.** Nous Research's Hermes Agent owns the agent loop: model providers, memory, context management, tools, code execution, delegation, platform adapters, and streaming.
2. **This repository makes a known deployment repeatable.** `setup.sh` fetches the exact upstream revision the patch was verified against, then invokes the upstream installer rather than reimplementing it. The result is still a plain checkout of upstream Hermes, not a vendored copy.
3. **The patch is narrow and inspectable.** `patches/voice-and-desktop-features.patch` adds automatic launch plus loopback binding for a configured dedicated Chrome CDP profile, the internal voice/model TTS override seam not present upstream, and Telegram location-keyboard cleanup. Hermes v0.20.6 already owns `browser.cdp_url`, interactive browser connection, and model-facing TTS provider/speed arguments; those are not duplicated. The patch can be checked, skipped, or reversed as one unit. The Discord voice stack is deliberately not part of it.
4. **Your private runtime is separate.** `~/.hermes/config.yaml` contains configuration and `~/.hermes/.env` contains keys and allowlists. The installer only seeds missing files; it never commits secrets or blindly overwrites an existing setup. Preview the config overlay before you merge it.
5. **Companion systems are independent by design.** The coding wrappers are a public-safe snapshot that orchestrates locally authenticated subscription CLIs; the Second Brain and messaging bridges each have their own opt-in installation and security boundaries. They extend a personal workflow without quietly widening the core agent's permissions.
6. **Pi is an experimental contained runtime, not a new baseline.** Its module has a different exact upstream base and applies only to an explicit separate installation. Hermes stays in control, setup never authenticates or starts it, and removal of that installation is the rollback.

The patch intentionally leaves tracked working-tree changes in the upstream checkout: that is the audit trail. You can inspect the exact delta against the pinned base at any time, reset it, or reverse it without losing upstream Hermes.

Pinned upstream commit: **`5fc308a70719a83cccdbba4c0e39c23f5a8239d5`** (Hermes Agent v0.20.6, release tag `v2026.8.27`).

---

## Merging into an existing config

If `~/.hermes/config.yaml` already exists, `setup.sh` leaves it alone. Fold the feature overlay in yourself:

```bash
# 1. See the diff. Changes nothing.
python3 scripts/merge_config.py --base ~/.hermes/config.yaml --overlay config.example.yaml

# 2. Apply it. Only ADDS keys you don't have; your values win. Keeps a .bak.
python3 scripts/merge_config.py --base ~/.hermes/config.yaml --overlay config.example.yaml --apply

# 3. Or let the overlay win on conflicts (explicit opt-in):
python3 scripts/merge_config.py --base ~/.hermes/config.yaml --overlay config.example.yaml \
    --strategy overlay-wins --apply
```

The merge is `yaml.safe_load` only, writes atomically, and always backs up first.

---

## Optional Local Second Brain

This repo includes a public-repository-safe Second Brain helper at [second-brain/README.md](second-brain/README.md). It is not a bundled Hermes plugin, is not an upstream Hermes config section, and does not run automatically. Treat it as a local CLI you install and configure beside Hermes through its **own manifest** (`hermes-second-brain init`), not through `~/.hermes/config.yaml`. This public starter does not reproduce the maintainer's private Second Brain setup — production manifests, approved roots, scheduler labels, and account-specific glue are intentionally omitted and belong only in your own local config.

Install and test it:

```bash
cd second-brain
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install pytest
python -m pytest -q
```

Initialize a private manifest in a local workspace, then edit the approved roots:

```bash
mkdir -p ~/hermes-second-brain-local
cd ~/hermes-second-brain-local
hermes-second-brain init --manifest second-brain.toml
$EDITOR second-brain.toml
```

Use it:

```bash
hermes-second-brain --manifest second-brain.toml scan
hermes-second-brain --manifest second-brain.toml sync --dry-run
hermes-second-brain --manifest second-brain.toml sync --apply
```

Safe boundaries:

- Raw notes, inboxes, chat exports, and SQLite state stay local and ignored by git.
- Only files under manifest `[[approved_roots]]` are scanned.
- Secret-looking paths, virtualenvs, caches, build output, SQLite databases, JSONL logs, `.env` files, tokens, credentials, and private keys are skipped.
- `sync` is preview-only by default; only `sync --apply` invokes `ov add-resource` / `ov write`.
- The module performs basic redaction only; it is not a guarantee against secrets or PII in ordinary note content. Approve roots and inspect the scan before `--apply`.
- Production manifests, scheduler labels, account IDs, personal paths, and private integration glue are intentionally omitted. Add them only in private local config.

---

## Optional Messaging bridges (WhatsApp + Signal)

This repo includes an opt-in, macOS-first [`messaging/`](messaging/README.md)
module that wires personal WhatsApp and Signal into Hermes as **loopback-only**
bridges. Nothing runs until you invoke it, and no upstream bridge code is
vendored: the WhatsApp bridge is the one in the pinned upstream checkout
(`scripts/whatsapp-bridge`), and Signal is driven by the community `signal-cli`.

```bash
cd messaging
./setup_whatsapp.sh     # loopback Baileys bridge on :3000, self-chat trigger
./setup_signal.sh       # signal-cli JSON-RPC daemon on 127.0.0.1:8080
```

Both installers are idempotent, render a per-user `launchd` agent from a
template, and print clear next-step guidance (pairing QR, health checks, the
`SIGNAL_ACCOUNT`/`SIGNAL_HTTP_URL` lines for `~/.hermes/.env`). This public
starter does not reproduce the maintainer's private live setup: real numbers,
session data, and machine paths are supplied only when you run the installers.
See [messaging/README.md](messaging/README.md) for the HTTP API, capabilities,
honest limitations (no real voice/video calls), and security notes.

---

## Security

Read [SECURITY.md](SECURITY.md) before you expose this to anyone. The short version:

- **Set your allowlists.** An agent reachable by strangers is a shell reachable by strangers.
- **Secrets live in `~/.hermes/.env`**, never in `config.yaml`, never in git.
- **Second Brain raw data lives outside git.** Commit only example manifests with placeholder paths.
- **Pi auth is trusted-local and manual.** The module carries no credentials or current authenticated-E2E claim; runtime images must be immutable IDs.
- The agent can **run code and use your browser session**. Treat it as software running as you.
- `browser.cdp_url` points at a dedicated, persistent Chrome profile. Anything you log into in that debugging profile is available to the agent.
- Before publishing any fork of this repo: `make release-audit`.

---

## Updating and rolling back

The upstream checkout is a plain git repo, so the patch is fully reversible.

**Roll back the patch, keep Hermes:**

```bash
cd ~/hermes-agent
git apply --reverse ~/hermes-x-jasper/patches/voice-and-desktop-features.patch
```

**Reset the checkout to clean upstream:**

```bash
cd ~/hermes-agent
git checkout -- .            # drop the patch and any local edits
git checkout main && git pull
```

**Move to a newer upstream:** the patch is written against the pinned commit and may not apply to a newer one. Check before committing to it:

```bash
git -C ~/hermes-agent apply --check ~/hermes-x-jasper/patches/voice-and-desktop-features.patch
```

If that fails, stay on the pinned commit — or re-roll the patch against the newer tree and open a PR.

**Roll back the experimental Pi module:** switch the separate installation to
the Hermes runtime, stop using it, then archive or remove that separate
installation with your normal recoverable filesystem workflow. Do not reverse
the Pi patch inside the stable v0.20.6 starter checkout; the module never
installed there.

**Your config is never destroyed:** every changed `merge_config.py --apply` leaves an exclusive collision-safe backup next to it, and both replacement and backup preserve the source mode (for example `0600` or `0640`).

---

## Development

```bash
make help      # list targets
make test      # pytest
make audit     # scan for secrets, PII, local paths (custom scanner)
make install-gitleaks # checksum-verify Gitleaks 8.30.1 into this workspace
make gitleaks  # deterministic gitleaks gate: current tree + full git history
make release-audit # authoritative history, Gitleaks, diff, patch, artifact + hash gate
make verify    # everything, incl. gitleaks gate and `git apply --check` against fresh upstream (needs network)
```

PR/push CI checks out full history and runs the same release audit with the actual base range. Pi-sensitive changes additionally require the Linux release/desktop gate, exact Windows path test, and native ARM64 reproducibility/containment gate; the aggregate `check` fails on an unexpected skip. The v0.3.0 tag workflow repeats every gate, builds from the candidate Git object, rescans the exact unpacked upload, and grants write permission only to the final publish job. See [docs/RELEASING.md](docs/RELEASING.md).

---

## Docs

- [docs/FEATURES.md](docs/FEATURES.md) — what each feature does and how to turn it on
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — when it doesn't work
- [CHANGELOG.md](CHANGELOG.md) — release notes
- [SECURITY.md](SECURITY.md) · [CONTRIBUTING.md](CONTRIBUTING.md)

## License

MIT — see [LICENSE](LICENSE). Hermes Agent is © Nous Research, also MIT.
