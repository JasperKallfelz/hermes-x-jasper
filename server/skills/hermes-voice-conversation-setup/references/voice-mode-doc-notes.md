# Hermes Voice Mode notes

Condensed from Hermes Agent voice documentation and a setup session where the user wanted to "call" the agent.

## Platform modes

- CLI voice mode: `hermes` interactive session, `/voice on`, press `Ctrl+B` to record. Hermes auto-detects silence and responds.
- Telegram / Discord text channels: `/voice`, `/voice on`, `/voice tts`, `/voice off`, `/voice status` control voice replies.
- Discord voice channels: `/voice join` makes the bot join the voice channel the user is currently in; `/voice leave` disconnects.

## Package and system requirements

Python extras:

```bash
pip install "hermes-agent[voice]"       # CLI microphone + playback
pip install "hermes-agent[messaging]"   # Telegram/Discord gateway, Discord voice support
pip install "hermes-agent[tts-premium]" # optional premium providers
pip install "hermes-agent[all]"         # broad install
```

System packages:

```bash
# macOS
brew install portaudio ffmpeg opus
brew install espeak-ng

# Ubuntu/Debian
sudo apt install portaudio19-dev ffmpeg libopus0
sudo apt install espeak-ng
```

`ffmpeg` is important for Telegram voice bubbles because Telegram prefers Opus/OGG. Some TTS providers emit MP3/WAV and need conversion.

## STT/TTS config skeleton

```yaml
stt:
  enabled: true
  provider: local
  local:
    model: small

tts:
  provider: edge
  edge:
    voice: en-US-AriaNeural

voice:
  auto_tts: true
  record_key: ctrl+b
  max_recording_seconds: 120
  silence_duration: 3
  silence_threshold: 200
```

## Discord live-call specifics

- Invite bot with Connect/Join, Speak, and Detect Speaking permissions.
- Message Content Intent is needed for text command handling.
- Use numeric Discord IDs in `DISCORD_ALLOWED_USERS` when possible; username allowlists may require extra intents.
- The user must join a voice channel before running `/voice join`.
- Hermes should pause listening while speaking so it does not process its own TTS.

## Setup-session probe pattern

```bash
python_bin="$HOME/.hermes/hermes-agent/venv/bin/python"
[ -x "$python_bin" ] || python_bin=python3
printf 'python: '; "$python_bin" -V
for bin in ffmpeg opusenc hermes; do printf '%s: ' "$bin"; command -v "$bin" || true; done
"$python_bin" - <<'PY'
mods=['sounddevice','faster_whisper','edge_tts','discord','telegram','nacl']
import importlib.util
for m in mods:
    print(f'{m}:', 'ok' if importlib.util.find_spec(m) else 'missing')
PY
```

Interpretation: missing `sounddevice` blocks local CLI microphone mode, but not necessarily Telegram voice-message or Discord gateway paths. Missing `DISCORD_BOT_TOKEN` / Discord `.env` entries means Discord live-call is not configured even if Python packages are present.
