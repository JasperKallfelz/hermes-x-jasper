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

The checkout is not at the pinned commit, or something already modified it. Find out which:

```bash
git -C ~/hermes-agent rev-parse HEAD          # should be b56aafc2ef...
git -C ~/hermes-agent status --short          # should be empty
git -C ~/hermes-agent apply --check -v ~/hermes-cli-starter/patches/voice-and-desktop-features.patch
```

If the patch is *already applied*, `setup.sh` detects that and skips it — this error means something else. To get back to a known-good state:

```bash
cd ~/hermes-agent
git checkout -- .                       # drop all local changes, including the patch
git checkout --detach b56aafc2ef6befd96ecf00bf4788031cf4be169b
```

Then re-run `./setup.sh`.

### `~/hermes-agent has uncommitted changes`

Deliberate. The installer will not check out over your edits and destroy them. Commit them, `git stash` them, or install somewhere else with `--install-dir ~/src/hermes-agent`.

### `Python 3.11+ is required`

macOS: `brew install python@3.11`. Debian/Ubuntu: `sudo apt install python3.11 python3.11-venv`. Make sure the new one is what `python3` resolves to (`python3 -V`).

### The upstream installer fails

`setup.sh` delegates to upstream's `setup-hermes.sh` on purpose — it owns the venv and the dependency set. If it fails, run it directly to see the real error:

```bash
cd ~/hermes-agent && bash setup-hermes.sh
```

That is an upstream problem; take it to [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/issues), not here.

### `voice deps failed to install`

A warning, not a failure — the install continues. Voice needs `ffmpeg` present *before* the Python packages:

```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu
~/hermes-agent/.venv/bin/pip install edge-tts faster-whisper langid
```

If you do not want voice at all: `./setup.sh --skip-voice`.

---

## Config

### My config was not updated

By design. `setup.sh` never modifies an existing `~/.hermes/config.yaml`. Fold the overlay in yourself:

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

1. **It is a \[patch\] key and the patch is not applied.** Hermes ignores keys it does not know. Check: `git -C ~/hermes-agent diff --stat` should show ~18 changed files.
2. **It needs a plugin that is not installed.** `memory.provider: holographic` and `context.engine: lcm` both name engines that ship separately. Without the plugin they are inert.

### `expected a YAML mapping at the top level`

Your `config.yaml` is malformed (or is a list). `merge_config.py` refuses to touch a file it cannot parse rather than guessing. Fix the YAML, or move it aside and let `setup.sh` write a fresh one.

---

## Telegram / Discord

### The bot ignores me

Almost always the allowlist. Your numeric id must be in `TELEGRAM_ALLOWED_USERS` / `DISCORD_ALLOWED_USERS` in `~/.hermes/.env`. Get it from [@userinfobot](https://t.me/userinfobot) (Telegram) or by right-clicking yourself with Developer Mode on (Discord).

If the allowlist is *empty*, fix that immediately — that is not "the bot is broken", that is "anyone can use your agent". See [SECURITY.md](../SECURITY.md).

### Discord: `PrivilegedIntentsRequired` on startup

The bot lacks its privileged intents. Discord Developer Portal → your app → **Bot** → **Privileged Gateway Intents** → enable **MESSAGE CONTENT** and **SERVER MEMBERS** → restart.

### Discord: the bot reads text but not voice

Voice needs `ffmpeg` and the *Connect* + *Speak* permissions in the channel. Also confirm `discord.voice_fx.enabled: true` and that the patch is applied — without it there is no voice pipeline at all.

### Discord: it joins the channel but never hears me

The gateway is not receiving audio (a Discord-side or network issue, usually), or every utterance is being discarded as too short. Turn up the logs and look for `Voice RTP packet` / `Voice decoded audio` lines:

- No RTP packets at all → Discord is not delivering UDP audio. Check firewall/VPN.
- Packets, but `unmapped ssrc` → the speaker could not be resolved to a user; usually fixed by leaving and rejoining the channel.

### It interrupts itself constantly / never lets me interrupt

Barge-in sensitivity. Raise `barge_in_min_ms` (e.g. `600`) if background noise keeps cutting the bot off; lower it (e.g. `200`) if talking over it does nothing.

### It cuts me off mid-sentence

Raise `streaming_stt_endpoint_silence` (e.g. `0.8`) — that is how long a pause has to be before your turn counts as finished.

---

## Voice quality

### `ffmpeg not found on PATH`

`brew install ffmpeg` / `sudo apt install ffmpeg`. Both `jarvis_style_tts.py` and Edge TTS need it.

### The JARVIS voice does not run

Check the command path in `config.yaml` actually exists — if you cloned the starter somewhere other than `~/hermes-cli-starter`, `tts.providers.jarvis.command` still points at the old path. Use an absolute path. Test it standalone:

```bash
echo "Systems online." > /tmp/in.txt
python3 scripts/jarvis_style_tts.py /tmp/in.txt /tmp/out.mp3
```

### Streaming STT stays off

It requires `parakeet-mlx`, which requires Apple Silicon. On Intel Macs and Linux it will not install and Hermes falls back to the file-based `stt.provider` — that is the intended behaviour, not a bug. Verify: `~/hermes-agent/.venv/bin/pip show parakeet-mlx`.

---

## Publishing a fork

### `audit_public found something`

It prints `file:line: [rule] message` for every hit. Real secret? Remove it, **rotate it**, and rewrite the history if it was ever committed. Genuine false positive? Add an `audit:allow` marker to that line, or use a clearer placeholder (`<YOUR_TOKEN>`, `user@example.com`, `~/path`).

Add your own strings to catch:

```bash
PUBLIC_AUDIT_DENYLIST="my-real-name,my-server.example" make audit
```

### CI fails on the patch check

Upstream moved, or the patch was regenerated against a different tree. The pinned commit is the contract: it appears in `setup.sh`, `verify.sh`, `.github/workflows/ci.yml` and `README.md`, and all four must agree.

---

## Still stuck?

- Bug in **this starter** (installer, patch, scripts, config) → open an issue here.
- Bug in **Hermes Agent** (the agent, gateway, tools, adapters) → [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/issues).

Never paste a real token, key, or chat log into an issue. Redact first.
