# Hermes CLI Starter `alpha`

> [!WARNING]
> **Alpha.** This starter is under active development and not yet stable. Interfaces, the feature patch, and the config layout may change without notice. Expect rough edges — pin what you depend on.

A one-command setup for a **personalised [Hermes Agent](https://github.com/NousResearch/hermes-agent)**: persistent memory, subagent delegation, a browser that drives your *real* Chrome, code execution, streaming replies, and polished Telegram + TTS voice ergonomics.

> [!IMPORTANT]
> **This is an unofficial community starter. It is not affiliated with, endorsed by, or maintained by Nous Research.**
> Hermes Agent itself is theirs (MIT). This repo only contains a pinned installer, a feature patch, example config, and a couple of voice scripts. For the real thing, go to [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).

---

## What you get

| Feature | What it does | Source |
| --- | --- | --- |
| **Persistent memory** | Curated long-term memory + a user profile injected into the system prompt | upstream |
| **Context engine** | Built-in compressor, or swap in LCM (Lossless Context Management) | upstream |
| **Delegation** | `delegate_task` spawns subagents, optionally on a cheaper/faster model | upstream |
| **Code execution** | `execute_code` runs Python that calls tools over RPC, keeping results out of the context window | upstream |
| **Streaming** | Token-by-token replies on chat platforms | upstream |
| **Custom TTS** | Any local binary as a TTS provider (`type: command`) | upstream |
| **Auto-CDP browser** | Points Hermes at your logged-in Chrome and launches it on demand — no more bot walls | **patch** |
| **TTS runtime overrides** | Per-call provider/voice overrides for the TTS tool | **patch** |
| **Telegram polish** | Location-request keyboards are cleaned up automatically after a share | **patch** |
| **JARVIS-style voice** | Edge TTS + an ffmpeg filter chain for a filtered assistant voice | script |
| **Local Second Brain starter** | Optional approved-root scanner, SQLite state, dry-run sync, and explicit OpenViking CLI export of approved excerpts | module |

"patch" = added by `patches/voice-and-desktop-features.patch`. "script" = `scripts/`.
"module" = the independent `second-brain/` Python package in this repo.

> [!NOTE]
> The Discord voice stack (voice mixer, barge-in, join greeting, streaming STT, voice jobs) has been split out of the main patch and is **not currently shipped** here. It is being rearchitected (isolated Node media gateway) and will return once it is stable.

---

## Requirements

- **macOS** (Apple Silicon or Intel) or **Linux**
- **Python 3.11+**
- **git**
- **ffmpeg** — required for the JARVIS-style TTS script (`brew install ffmpeg` / `sudo apt install ffmpeg`)
- **An API key** from at least one model provider (OpenRouter is the easiest single key)
- Optional: a Telegram bot, Google Chrome (for the auto-CDP browser)

---

## Quick start

```bash
git clone https://github.com/example/hermes-cli-starter.git
cd hermes-cli-starter

./setup.sh --dry-run     # see exactly what it will do — nothing is written
./setup.sh               # do it
```

`setup.sh` is idempotent — re-run it any time. It will:

1. Clone **NousResearch/hermes-agent** into `~/hermes-agent` and check out the pinned, tested commit
2. Apply the feature patch (skipped if already applied)
3. Run **upstream's own** `setup-hermes.sh` (skipped when `~/hermes-agent/venv/bin/hermes` already exists)
4. Install optional voice dependencies (`--skip-voice` to opt out)
5. Copy `.env.example` → `~/.hermes/.env` and the config overlay → `~/.hermes/config.yaml`

It **never** overwrites an existing `~/.hermes/config.yaml` or `.env`. If you already have a config, it prints a merge command instead (see [Merging into an existing config](#merging-into-an-existing-config)).

Useful flags:

```bash
./setup.sh --install-dir ~/src/hermes-agent   # where upstream gets cloned
./setup.sh --hermes-home ~/.hermes            # where config + state live
./setup.sh --skip-voice                       # no TTS/STT dependencies
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

## Architecture

```
this repo                     what it touches
─────────────────────────────────────────────────────────────────────
setup.sh ──────────────────►  clones NousResearch/hermes-agent @ pinned commit
                              runs upstream setup-hermes.sh (venv/bin/hermes)
                                 │
patches/voice-and-           ──►│ git apply
desktop-features.patch          │
                                ▼
                       ~/hermes-agent/          (patched upstream checkout)
                         tools/                 browser_tool (auto-CDP), tts_tool
                         hermes_cli/            browser_connect (auto-CDP launch)
                         plugins/platforms/     telegram adapter polish
                                ▲
config.example.yaml ───────────►│ merge (never blind-overwrites)
.env.example ──────────────────►│
                                ▼
                       ~/.hermes/               config.yaml, .env, state
scripts/jarvis_style_tts.py ─►  wired in as a `type: command` TTS provider
scripts/parakeet_stt_limited.py  local bilingual STT helper
```

The patch is deliberately small (~370 lines across 5 files): the auto-CDP browser, TTS runtime overrides, and Telegram keyboard cleanup — plus tests. Nothing is vendored: upstream stays a clean git checkout you can `git diff` against at any time.

Pinned upstream commit: **`b56aafc2ef6befd96ecf00bf4788031cf4be169b`** (Hermes Agent v0.17.0).

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

This repo includes a public-repository-safe Second Brain helper at [second-brain/README.md](second-brain/README.md). It is not a bundled Hermes plugin and does not run automatically. Treat it as a local CLI you can install beside Hermes.

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
git apply --reverse ~/hermes-cli-starter/patches/voice-and-desktop-features.patch
```

**Reset the checkout to clean upstream:**

```bash
cd ~/hermes-agent
git checkout -- .            # drop the patch and any local edits
git checkout main && git pull
```

**Move to a newer upstream:** the patch is written against the pinned commit and may not apply to a newer one. Check before committing to it:

```bash
git -C ~/hermes-agent apply --check ~/hermes-cli-starter/patches/voice-and-desktop-features.patch
```

If that fails, stay on the pinned commit — or re-roll the patch against the newer tree and open a PR.

**Your config is never destroyed:** every `merge_config.py --apply` leaves a `config.yaml.bak-<timestamp>` next to it.

---

## Development

```bash
make help      # list targets
make test      # pytest
make audit     # scan for secrets, PII, local paths
make verify    # everything, incl. `git apply --check` against fresh upstream (needs network)
```

CI runs the same checks on every push. It uses no secrets and has `contents: read` only.

---

## Docs

- [docs/FEATURES.md](docs/FEATURES.md) — what each feature does and how to turn it on
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — when it doesn't work
- [SECURITY.md](SECURITY.md) · [CONTRIBUTING.md](CONTRIBUTING.md)

## License

MIT — see [LICENSE](LICENSE). Hermes Agent is © Nous Research, also MIT.
