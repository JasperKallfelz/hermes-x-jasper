# Troubleshooting

Start here:

```bash
./verify.sh          # is the starter itself healthy?
./setup.sh --dry-run # what would a re-run actually change?
```

`setup.sh` is idempotent, so re-running it is a safe first move.

---

## Install

### `patch does not apply to ~/hermes-agent`

The target is Hermes Agent v0.20.6, tag `v2026.8.27`, at the exact peeled
commit below. The checkout is not at that commit, or something already
modified it. Find out which:

```bash
git -C ~/hermes-agent rev-parse HEAD          # should be 5fc308a70719...
git -C ~/hermes-agent status --short          # clean, or only the exact patch tree
git -C ~/hermes-agent apply --reverse --check -v ~/hermes-x-jasper/patches/voice-and-desktop-features.patch
```

If the patch is *already applied*, `setup.sh` detects that and skips it — this error means something else. To get back to a known-good state:

```bash
cd ~/hermes-agent
git checkout -- .                       # drop all local changes, including the patch
git checkout --detach 5fc308a70719a83cccdbba4c0e39c23f5a8239d5
```

Then re-run `./setup.sh`.

### `~/hermes-agent has changes beyond the exact starter patch`

Deliberate. The installer compares complete file manifests in a disposable non-hardlinked clone, so it does not write candidate objects or indexes into your checkout. It will not check out over, merge into, or silently tolerate unrelated edits—including edits hidden by assume-unchanged or skip-worktree. Preserve them yourself, or install somewhere else with `--install-dir ~/src/hermes-agent`. Rejected origin URLs are never echoed because they may contain userinfo or query credentials.

### `Python 3.11+ is required`

macOS: `brew install python@3.11`. Debian/Ubuntu: `sudo apt install python3.11 python3.11-venv`. Make sure the new one is what `python3` resolves to (`python3 -V`).

### The upstream installer fails

`setup.sh` delegates to upstream's `setup-hermes.sh` on purpose. An executable alone is not completion: the private marker also binds the pin, dependency/installer digests, patch, platform, interpreter and requested voice set, then rechecks `hermes --version` and `pip check`. A missing/stale marker or broken executable reruns synchronization; unchanged verified reruns do not reinstall voice packages. If installation fails, no marker is written.

```bash
cd ~/hermes-agent && bash setup-hermes.sh
```

That is an upstream problem; take it to [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/issues), not here.

### `voice deps failed to install`

A warning, not a failure — the install continues. Voice needs `ffmpeg` present *before* the Python packages:

```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu
~/hermes-agent/venv/bin/pip install edge-tts faster-whisper langid
```

If you do not want voice at all: `./setup.sh --skip-voice`.

---

## Config

### My config was not updated

If the config existed before setup, it is intentionally untouched. If it was absent before setup, a config created by the upstream wizard is not treated as pre-existing: setup automatically adds missing starter keys after the wizard while keeping every wizard-selected value. For an older pre-existing config, fold the overlay in yourself:

```bash
python3 scripts/merge_config.py --base ~/.hermes/config.yaml --overlay config.example.yaml          # diff
python3 scripts/merge_config.py --base ~/.hermes/config.yaml --overlay config.example.yaml --apply  # write
```

The default strategy only *adds* keys you are missing. If you want the overlay to win on conflicts, pass `--strategy overlay-wins`.

### I merged and want it back

Every `--apply` leaves a backup next to the file:

```bash
ls ~/.hermes/config.yaml.bak-*
cp ~/.hermes/config.yaml.bak-<timestamp> ~/.hermes/config.yaml
```

### A setting does nothing

Two usual causes:

1. **It is a \[patch\] key and the patch is not applied.** Hermes ignores keys it does not know. Check with `git -C ~/hermes-agent apply --reverse --check ~/hermes-x-jasper/patches/voice-and-desktop-features.patch`.
2. **It needs a plugin that is not installed.** `memory.provider: holographic` and `context.engine: lcm` both name engines that ship separately. Without the plugin they are inert.

### `expected a YAML mapping at the top level`

Your `config.yaml` is malformed (or is a list). `merge_config.py` refuses to touch a file it cannot parse rather than guessing. Fix the YAML, or move it aside and let `setup.sh` write a fresh one.

---

## Telegram

### The bot ignores me

Almost always the allowlist. Your numeric id must be in `TELEGRAM_ALLOWED_USERS` in `~/.hermes/.env`. Get it from [@userinfobot](https://t.me/userinfobot).

If the allowlist is *empty*, fix that immediately — that is not "the bot is broken", that is "anyone can use your agent". See [SECURITY.md](../SECURITY.md).

---

## Voice quality

### `ffmpeg not found on PATH`

`brew install ffmpeg` / `sudo apt install ffmpeg`. Both `jarvis_style_tts.py` and Edge TTS need it.

### The JARVIS voice does not run

Check the command path in `config.yaml` actually exists — if you cloned the starter somewhere other than `~/hermes-x-jasper`, `tts.providers.jarvis.command` still points at the example path. Use an absolute path. Test it standalone:

```bash
echo "Systems online." > /tmp/in.txt
python3 scripts/jarvis_style_tts.py /tmp/in.txt /tmp/out.mp3
```

---

## Publishing a fork

### `audit_public found something`

It prints `file:line: [rule] message` for every hit and never prints the matched value. Real secret? Remove it, **rotate it**, and rewrite unpublished history. A genuine current false positive needs both a narrow trailing `audit:allow` comment and its exact path/rule/line hash in `security/audit-exceptions.json`; history additionally requires the immutable commit. Uninventoried markers, whole-file bypasses, Gitleaks inline allows, and `.gitleaksignore` do not bypass release gates.

Add your own strings to catch:

```bash
PUBLIC_AUDIT_DENYLIST="my-real-name,my-server.example" make audit
```

### CI fails on the patch check

Upstream moved, or the patch was regenerated against a different tree. The exact commit/tag/version tuple is the contract: it appears in the installer, verifier, CI, docs, and release-contract tests, and every copy must agree. The release gate uses plain `git apply --check --whitespace=error-all`; a three-way fallback is not accepted.

---

## Still stuck?

- Bug in **this starter** (installer, patch, scripts, config) → open an issue here.
- Bug in **Hermes Agent** (the agent, gateway, tools, adapters) → [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/issues).

Never paste a real token, key, or chat log into an issue. Redact first.
