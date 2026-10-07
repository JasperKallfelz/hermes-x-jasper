# Hermes x Jasper: portable server snapshot

This directory packages the reusable parts of the running server as of **2026-10-06**. It contains code and neutral examples, never a backup of an account. The installation has its own private home and does not import the maintainer's profiles.

## Install on your own machine

Requirements: Git and Python 3.11. macOS is the primary target; the agent and Second Brain core are portable, while Apple data adapters, Keychain and launchd helpers require macOS. Disk use is dominated by the upstream checkout and Python dependencies.

```bash
git clone https://github.com/JasperKallfelz/hermes-x-jasper.git
cd hermes-x-jasper
PYTHON=python3.11 ./setup-server.sh --dry-run --with-lcm
PYTHON=python3.11 ./setup-server.sh --with-lcm
~/hermes-server/bin/hermes setup
~/hermes-server/bin/hermes
```

`hermes setup` connects **your own** provider or subscription. Fill in only your own credentials in `~/hermes-server/home/.env`; never put a populated copy in this repository. The Claude/Codex coding wrappers require separately installed and authenticated CLIs. They do not share the maintainer's subscriptions.

The destination must be new. Pass `--root /path/to/fresh-directory` to choose another location. Existing installations are refused before any changes. On failure the partial directory is kept for inspection; retry in a new directory. Setup does not replace a global `hermes` executable, launch a service, connect a messenger, or start a schedule.

For the local context-intake and process-observation modules, choose the flags during installation:

```bash
PYTHON=python3.11 ./setup-server.sh --root ~/hermes-server-full \
  --with-lcm --with-context-inbox --with-observatory
```

The Context Inbox observes conversations in this new Hermes installation and stages typed intake. Process Observatory stores bounded operational metadata. Neither flag imports existing mailboxes or messenger history. `--with-auto-titler` optionally activates the bundled third-party session-title plugin; its own README documents its title-generation configuration changes.

## Included components

| Component | Packaged behavior |
| --- | --- |
| Hermes core | Public upstream commit pinned in `source-lock.json`, version 0.21.1; fetched during setup |
| Runtime patch | Codex context recovery, usage/event continuity, early cancellation, Telegram idle-session handling and a desktop session fix; matching tests included |
| LCM 0.20.0 | Installed server plugin source; opt-in lossless context engine; MIT license retained |
| Second Brain | Source, tests, empty install manifest, example manifests and local UI assets |
| Context and memory | Context Inbox, temporary memory, typed intake, ownership rules, source adapters, OpenViking sync and evaluation |
| Reflection and operations | Dreaming, provenance, publication queue, lifecycle tracking, Process Observatory and improvement-review adapters |
| Coding | Existing subscription-only `hermes-coder` and `hermes-coder-flow`; their server binaries match the existing public snapshot |
| Skills | Selected reusable agent-operation skills and references, stripped of project-specific case studies |
| Voice | Local Parakeet STT, Jarvis-style TTS and cron-output pruning helpers; invoked manually |
| Desktop | Upstream desktop source plus the runtime patch; no signed/prebuilt desktop application is redistributed |
| Auto Titler | Optional 0.2.3.1 plugin source, Apache-2.0 license retained; no installed state or personal configuration |

The original July installer (`../setup.sh`) and its smaller Second Brain remain available for legacy users. **Use `setup-server.sh` for this snapshot.** The two Second Brain source trees are alternatives; do not install both into one Python environment.

## Private data and excluded items

Not shipped: `.env`, auth files, OAuth/API tokens, cookies, Keychain entries, MCP connection headers/URLs, private host addresses, phone numbers or channel allowlists, USER/MEMORY/SOUL files, chat history, databases, inboxes, documents, attachments, cron jobs, logs, backups and model weights. Examples use neutral project names; test conversations are synthetic.

This is deliberately not a byte-for-byte server backup. Personal/project skills and machine-specific watchdogs, service-repair scripts, profile mirroring, scheduled mail collectors and background jobs are excluded. Recreate any desired automation against your own accounts and paths. The unintegrated Telegram turn-control prototype is excluded: its integration tests failed against the actual server adapter. The separate Tailscale plugin is not vendored because the installed copy did not contain redistributable license evidence; use your own Tailscale network and the appropriate upstream plugin.

## Second Brain and OpenViking

The Second Brain is installed in the new Python environment. Its initial manifest is `second-brain/manifest.json` inside the installation’s private `home` directory: **no source roots, dreaming disabled**. Add only folders you explicitly want to index. Source adapters, mailbox/messenger collectors and Apple data readers are available in source but are not activated by setup.

```bash
~/hermes-server/bin/hermes-second-brain --help
~/hermes-server/bin/hermes-second-brain ownership-check
```

OpenViking is a separate service. The inspected server uses version **0.4.16**; install/configure your own instance using [OpenViking's documentation](https://github.com/volcengine/openviking). Use your own storage, embedding/model provider and credentials. `examples/openviking.example.json` shows the configuration shape; replace the placeholders before running it. Keep its endpoint on loopback unless you deliberately configure authenticated remote access. In your private Hermes config, choose `memory.provider: openviking` and configure `memory.openviking.endpoint` for that instance. Until then, the installer uses Hermes' built-in curated memory and optional LCM.

See [the Second Brain manual](second-brain/README.md) for individual tools. Its examples assume the conventional `~/.hermes` layout; adapt them to your install's `home` directory and pass the private manifest/database explicitly. The installed `bin/hermes` launcher routes Context Inbox and Observatory spools into the new home. Running companion scripts directly requires the same explicit paths. The larger `second-brain/config/manifest.example.json` is a reference template, not the installer's active manifest. It contains disabled example profiles and proposed source roots for review.

## Desktop, voice and other connections

For a desktop development build, use the pinned core's `apps/desktop/README.md`. From the installed `core` directory, run `npm install`, then use `npm run dev` in `apps/desktop` with `HERMES_HOME` set to the installation's `home` directory and `HERMES_DESKTOP_HERMES_ROOT` set to its `core` directory. This repository does not supply a notarized application or claim desktop build verification.

Voice dependencies are optional: `edge-tts`, `faster-whisper`, `langid`, FFmpeg, and `parakeet-mlx` on Apple Silicon. The helpers' `--help` describes their inputs. Pair your own Telegram/Discord/WhatsApp/Signal accounts; the legacy `messaging/` module contains self-chat bridge examples. Configure Composio or any other MCP service with a new connection owned by you. No shared connection is provided.

## Verify and inspect

```bash
python3 scripts/verify_server_snapshot.py
python3 scripts/audit_public.py . --history
./scripts/gitleaks_scan.sh
python3 -m pytest tests/test_server_snapshot.py -q
cd server/second-brain
PYTHONPATH=src python3.11 -m unittest discover -s tests -q
```

The Gitleaks gate scans both the publication tree and Git history. `checksums.json` covers every bundled source file. To prepare source without installing packages, pass `--source-only`; its generated launchers are not runnable until a Python environment is installed. Patch application and source preparation are distinct from a provider-authenticated live conversation.

Optional environment checks are separate: set `HERMES_TEST_LIVE_CODEX=1` to run the installed Codex subscription/feature contract probes. On the inspected server, the current Codex CLI could not attest that every feature was disabled. The Second Brain improvement-review adapter therefore refuses that run; this snapshot preserves that behavior. Automatic improvement review is not enabled by setup. This limitation is separate from ordinary Hermes chat and the existing coding wrappers. SQLCipher-dependent tests require a separate SQLCipher installation; macOS sandbox tests require a supported Python installation.

Bundled third-party licenses live next to their modules. Upstream Hermes is fetched from its public repository and retains its own license and attribution.
