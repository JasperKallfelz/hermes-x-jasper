# Hermes CLI Starter `alpha`

> [!WARNING]
> **Alpha.** This starter is under active development and not yet stable. Interfaces, the feature patch, and the config layout may change without notice. Expect rough edges — pin what you depend on.

**A reproducible personal-agent layer for [Hermes Agent](https://github.com/NousResearch/hermes-agent).** It keeps the upstream agent intact, then adds a small, auditable set of interaction features and companion modules: durable memory and delegation from Hermes itself; real-browser control, Telegram/TTS ergonomics, subscription-backed coding workflows, a local Second Brain starter, and optional self-only messaging bridges.

This is not a fork that silently drifts. The installer checks out one **pinned, tested upstream commit**, applies one **reversible patch**, and keeps private configuration and secrets outside this repository. The optional modules are explicitly separated from the core runtime, so you can adopt only the pieces you understand and need.

> [!IMPORTANT]
> **This is an unofficial community starter. It is not affiliated with, endorsed by, or maintained by Nous Research.**
> Hermes Agent itself is theirs (MIT). This repository contains a pinned installer, feature patch, example configuration, voice helpers, an optional Second Brain starter, and an independently maintained public coding-wrapper snapshot. For the core agent, see [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).

---

## Why this setup

- **Power without a black box.** Hermes supplies the agent loop, tools, memory, delegation, code execution, and platform adapters. This starter keeps custom runtime changes visible in one patch and makes optional systems separate modules.
- **Reproducible instead of fragile.** The pinned upstream revision gives the installer, patch, config, documentation, and tests one known base. You can review the exact delta, then upgrade deliberately rather than discover an upstream breakage after the fact.
- **Personal context with clear boundaries.** Your keys, channel allowlists, and normal Hermes state live under `~/.hermes/`, never in git. The Second Brain scans only approved roots; messaging bridges are loopback-only and do nothing until installed.
- **Agentic coding without silent API bills.** The coding wrappers use your authenticated Claude Code and Codex CLIs, preserve an isolated worktree, run deterministic gates, and route an independent vendor review before any integration decision.
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
              coder-stack: isolated, reviewed CLI worktrees · second-brain: approved-root sync
```

The separation is the design: **upstream** remains the agent platform; the **patch** is the small runtime delta; `~/.hermes/` is your private runtime; and `coder-stack/`, `second-brain/`, and `messaging/` are opt-in companion modules with their own boundaries. That makes each layer understandable, reviewable, and independently removable.

---

## What you get

| System | What it does | Origin |
| --- | --- | --- |
| **Persistent memory** | Curated long-term memory plus a user profile, injected into the system prompt | upstream |
| **Context engine** | Built-in compressor, or swap in LCM (Lossless Context Management) | upstream |
| **Delegation** | `delegate_task` spawns subagents; optional routing lets a strong coordinator use faster workers | upstream |
| **Code execution** | `execute_code` runs Python that calls tools over RPC, so bulky intermediate results stay out of the context window | upstream |
| **Streaming + custom TTS** | Token-by-token replies and command-based local speech providers | upstream |
| **Auto-CDP browser** | Uses a dedicated, real Chrome debugging profile rather than a disposable headless session; can start it on demand | **patch** |
| **TTS overrides + Telegram polish** | Per-call voice/provider selection and automatic cleanup of location-request keyboards | **patch** |
| **JARVIS-style voice** | Edge TTS plus an ffmpeg filter chain for a filtered assistant voice | script |
| **Claude/Codex coding flow** | Subscription-CLI routing, deterministic gates, isolated worktrees, and opposite-vendor review | vendored module |
| **Local Second Brain starter** | Approved-root scanning, local state, dry-run sync, and explicit OpenViking CLI export of approved excerpts | module |
| **Messaging bridges (WhatsApp + Signal)** | Opt-in macOS launchd setup for a loopback WhatsApp self-chat bridge and a `signal-cli` JSON-RPC daemon | module |

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
- Optional coding flow: separately installed **Claude Code** and **Codex** CLIs,
  each authenticated to an eligible subscription. The wrappers do not use the
  ordinary Hermes provider API keys.

---

## Quick start

```bash
git clone https://github.com/JasperKallfelz/hermes-x-jasper.git
cd hermes-x-jasper

./setup.sh --dry-run     # see exactly what it will do — nothing is written
./setup.sh               # do it
```

`setup.sh` is idempotent — re-run it any time. It will:

1. Install the public `hermes-coder` and `hermes-coder-flow` wrappers into
   `~/.local/bin` by default. It does not install or authenticate either vendor CLI.
2. Clone **NousResearch/hermes-agent** into `~/hermes-agent` and check out the pinned, tested commit
3. Apply the feature patch (skipped if already applied)
4. Run **upstream's own** `setup-hermes.sh` (skipped when `~/hermes-agent/venv/bin/hermes` already exists)
5. Install optional voice dependencies (`--skip-voice` to opt out)
6. Copy `.env.example` → `~/.hermes/.env` and the config overlay → `~/.hermes/config.yaml`

It **never** overwrites an existing `~/.hermes/config.yaml` or `.env`. If you already have a config, it prints a merge command instead (see [Merging into an existing config](#merging-into-an-existing-config)).

Useful flags:

```bash
./setup.sh --install-dir ~/src/hermes-agent   # where upstream gets cloned
./setup.sh --hermes-home ~/.hermes            # where config + state live
./setup.sh --skip-voice                       # no TTS/STT dependencies
./setup.sh --skip-coder-stack                 # do not install coding wrappers
./setup.sh --coder-bin-dir ~/bin              # choose a user-writable PATH directory
./setup.sh --replace-coder-stack              # back up, then replace differing wrappers
```

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

## Subscription-backed coding wrappers

The optional wrappers in [`coder-stack/`](coder-stack/) are a public-safe,
self-contained snapshot from source commit
`2a74f958cc1eb226584fdc51dfe72cebfc22ddab`. They drive the locally installed
Claude Code and Codex CLIs; they do not call model APIs directly and they do not
reuse `OPENROUTER_API_KEY` or other ordinary Hermes provider credentials.
This workflow is subscription-only: there is no direct-API fallback in these
wrappers.

Install and authenticate the vendor CLIs yourself, then check them without
starting model inference:

```bash
claude auth status
codex login status
hermes-coder --doctor both
```

Both CLIs must be on `PATH` for the full cross-vendor flow, and their accounts
must have subscriptions that permit CLI coding use. `setup.sh` only reports
whether the commands are present; it never installs a vendor CLI, opens a login
flow, reads auth output, or stores credentials. If `~/.local/bin` is not already
on `PATH`, add it using the normal mechanism for your shell.

Examples:

```bash
hermes-coder --task implement --lane normal --workdir "$PWD" \
  "Implement the requested change and run its focused tests."

hermes-coder-flow --source "$PWD" --lane auto \
  "Implement the requested change and update its tests."
```

The flow requires a clean source repository and a tracked
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

second-brain/ and messaging/                       separate opt-in local modules
  their own manifests/installers                    never started by normal Hermes setup
```

### The layers

1. **Upstream is the foundation.** Nous Research's Hermes Agent owns the agent loop: model providers, memory, context management, tools, code execution, delegation, platform adapters, and streaming.
2. **This repository makes a known deployment repeatable.** `setup.sh` fetches the exact upstream revision the patch was verified against, then invokes the upstream installer rather than reimplementing it. The result is still a plain checkout of upstream Hermes, not a vendored copy.
3. **The patch is narrow and inspectable.** `patches/voice-and-desktop-features.patch` adds the auto-CDP browser path, TTS runtime overrides, and Telegram keyboard cleanup. It can be checked with `git diff`, skipped, or reversed as one unit. The Discord voice stack is deliberately not part of this current patch.
4. **Your private runtime is separate.** `~/.hermes/config.yaml` contains configuration and `~/.hermes/.env` contains keys and allowlists. The installer only seeds missing files; it never commits secrets or blindly overwrites an existing setup. Preview the config overlay before you merge it.
5. **Companion systems are independent by design.** The coding wrappers are a public-safe snapshot that orchestrates locally authenticated subscription CLIs; the Second Brain and messaging bridges each have their own opt-in installation and security boundaries. They extend a personal workflow without quietly widening the core agent's permissions.

The patch intentionally leaves tracked working-tree changes in the upstream checkout: that is the audit trail. You can inspect the exact delta against the pinned base at any time, reset it, or reverse it without losing upstream Hermes.

Pinned upstream commit: **`3ef6bbd201263d354fd83ec55b3c306ded2eb72a`** (Hermes Agent v0.19.0, release tag `v2026.7.20`).

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
- The agent can **run code and use your browser session**. Treat it as software running as you.
- `browser.cdp_url` points at your *real, logged-in* Chrome. Anything you are logged into, the agent is logged into.
- Before publishing any fork of this repo: `make audit`.

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

**Your config is never destroyed:** every `merge_config.py --apply` leaves a `config.yaml.bak-<timestamp>` next to it.

---

## Development

```bash
make help      # list targets
make test      # pytest
make audit     # scan for secrets, PII, local paths (custom scanner)
make gitleaks  # deterministic gitleaks gate: current tree + full git history
make verify    # everything, incl. gitleaks gate and `git apply --check` against fresh upstream (needs network)
```

CI runs the same checks on every push. It uses no secrets and has `contents: read` only.

---

## Docs

- [docs/FEATURES.md](docs/FEATURES.md) — what each feature does and how to turn it on
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — when it doesn't work
- [SECURITY.md](SECURITY.md) · [CONTRIBUTING.md](CONTRIBUTING.md)

## License

MIT — see [LICENSE](LICENSE). Hermes Agent is © Nous Research, also MIT.
