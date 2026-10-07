---
name: hermes-voice-conversation-setup
description: "Set up and choose Hermes voice interaction paths: Telegram voice-message walkie-talkie, Discord voice-channel live calls, and local CLI voice mode. Includes prerequisite probes, config checks, commands, and user-facing recommendation patterns."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [Hermes, Voice, TTS, STT, Telegram, Discord, Voice-Mode, Setup]
---

# Hermes Voice Conversation Setup

Use when the user asks to call Hermes, speak personally, use voice, receive voice replies, configure STT/TTS, or troubleshoot Hermes voice mode across Telegram, Discord, or CLI.

## Default recommendation

Lead with the user's desired UX, not with implementation detail:

1. **Fastest now: Telegram voice messages** — user sends a voice note; Hermes transcribes it and can answer with a voice bubble. This feels like walkie-talkie, not a real call.
2. **Closest to a real call: Discord voice channel** — Hermes bot joins the user's Discord voice channel, listens, transcribes, and speaks replies. Recommend this when the user says "anrufen", "persönlich sprechen", "live call", or similar.
3. **Local laptop conversation: CLI voice mode** — use `hermes`, `/voice on`, then `Ctrl+B` to record/silence-stop. Good for local desktop use, not remote/mobile calling.

For this user, be decisive: recommend Discord Voice for a real live call, and mention Telegram voice messages as immediately usable if already configured.

## Prerequisite probes

Before promising a voice path is ready, check the current setup. Typical probes:

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

Config locations to inspect:

```bash
# Main config
~/.hermes/config.yaml

# Secrets / gateway platform credentials
~/.hermes/.env
```

Important config keys:

```yaml
stt:
  enabled: true
  provider: local        # local | groq | openai | mistral | xai | named command provider such as parakeet
  local:
    model: small         # tiny/base/small/medium/large-v3
    language: ''         # auto-detect; set 'en'/'de' to stop wrong language detection

tts:
  provider: edge         # free default; alternatives include openai/elevenlabs/neutts/minimax/mistral/gemini/xai/kittentts/piper
  edge:
    voice: en-US-AriaNeural

voice:
  auto_tts: true
  record_key: ctrl+b
  max_recording_seconds: 120
  silence_threshold: 200
  silence_duration: 3
```

## Telegram voice-message path

Telegram supports voice input and voice replies through the gateway. It is the quickest path when Telegram is already set up.

If the user says they will send a “Memo/Momo/Voice Memo”, treat that as **incoming Speech-to-Text**, not necessarily outgoing TTS. Configure `stt.*` first; `/voice on` / `/voice tts` only controls spoken replies.

Hermes Telegram voice notes are transcribed by Hermes' configured STT provider. External macOS dictation apps such as Spokenly can be the user's system dictation standard, but they do **not** automatically drive Telegram gateway STT unless explicitly integrated separately.

Required basics:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USERS=<numeric-user-id>
# Optional for proactive delivery / cron target:
TELEGRAM_HOME_CHANNEL=<chat-id>
```

Voice-message behavior:

- User sends a Telegram voice message.
- Gateway downloads/caches audio and STT transcribes it when `stt.enabled: true`.
- Hermes replies normally; when voice/TTS mode is enabled, it can also deliver a Telegram voice bubble.
- Telegram voice bubbles need Opus/OGG. `ffmpeg` is the practical prerequisite for converting non-Opus provider output.

Useful commands in Telegram/Discord text channels:

```text
/voice          Toggle voice mode
/voice on       Voice replies when the user sends a voice message
/voice tts      Voice replies for all messages
/voice off      Disable voice replies
/voice status   Show current state
```

If asked for a "call", be explicit that Telegram voice messages are not a live call; they are turn-based walkie-talkie.

## Discord voice-channel live call path

This is the closest Hermes-native equivalent to "calling" the assistant.

Requirements:

- Discord bot configured for text gateway use.
- Bot invited with voice permissions:
  - Join/Connect voice channels
  - Speak
  - Detect when users are speaking
  - Message Content Intent for text command handling when using normal text-message routing; slash/voice-only setups can keep this opt-in if the adapter supports it.
- `discord.py[voice]` / PyNaCl available (`nacl: ok` in probe).
- STT and TTS configured.
- `.env` includes at least:

```env
DISCORD_BOT_TOKEN=...
DISCORD_ALLOWED_USERS=<numeric-discord-user-id>
```

### Live coworker mode

When the user wants Hermes to feel like a real always-available coworker in Discord voice — walking through the city, giving several tasks, receiving mobile previews — do not keep everything in the synchronous agent turn. Use a two-layer design:

1. **Fast voice host/router:** classify utterances, speak short acknowledgements immediately, answer status questions, and avoid long monologues.
2. **Background worker queue:** persist voice jobs under the active profile, dispatch coding/build tasks to Codex or a Hermes/Anthropic worker, and tag the user in Discord/Telegram when started/done/blocked.

Implementation guidance:

- Voice task requests should return quickly after a spoken ack such as “Alles klar, ich starte das im Hintergrund …”. Do **not** read long job IDs or user IDs aloud; keep IDs/details in Discord text only.
- For natural live calls, split the interaction path: a tiny local/router path speaks immediate feedback (`Bin dran`, `Alles klar…`) without invoking the heavy model, while the stronger model/worker handles the real task concurrently. Avoid making the user wait silently for GPT-5-class turns.
- Treat short backchannels (`okay`, `ja`, `yeah`, `mhm`) as acknowledgements/noise: do not start a full model turn and do not answer with another ack.
- Persist job state and artifacts so results survive gateway restarts.
- Mark Discord job updates as non-conversational metadata when possible so status messages do not poison future history/backfill.
- If standalone Codex CLI is unauthenticated but Hermes' own provider works, fall back to a bounded `hermes -z '<task>' --yolo` worker in an isolated git workdir instead of blocking the voice UX.
- See `references/discord-live-coworker-and-bilingual-voice.md` and `references/discord-voice-receive-qa.md` for detailed patterns and validation checklists.

Usage:

```text
# User joins a Discord voice channel first.
# In a Discord text channel where the bot is present:
/voice join      Bot joins the user's current voice channel
/voice channel   Alias for /voice join
/voice leave     Disconnect
/voice status    Show mode and connected channel
```

When the bot joins, it listens to each user's audio stream independently, transcribes speech, sends text through Hermes, and speaks the answer back via TTS. With a successfully installed continuous mixer, keep the receiver running during TTS and use conservative authorized-user barge-in; only the legacy one-shot fallback pauses listening for echo prevention. Never infer mixer or autojoin recovery from config alone—verify runtime logs and a real speak-during-TTS test. If the session should survive a restart, require the `discord.voice_fx.autojoin.*` triad to be set and confirm the live logs show rejoin + receiver + packet flow again.

When the user wants audible reassurance during long voice turns, use an **on-demand working sound**, not permanent channel ambience: keep the mixer silent while idle, start a subtle loop only around voice-originated agent work, duck it under acknowledgements/TTS, and stop it in `finally`. Reference-count concurrent turns per guild. See `references/discord-voice-working-sound.md`.

For a personalized always-on companion, implement join greetings at the Discord transport edge: greet only an authorized human who newly enters or moves into the bot's current channel, schedule TTS without blocking the voice-state callback, and configure a varied phrase pool under `discord.voice_fx.join_greeting_*` (not one robotic fixed sentence). Restarting while the user is already present does not emit a join transition; live-test by leaving and re-entering. Detailed pattern: `references/discord-live-coworker.md`.

### Realtime latency and streaming speech

When the user wants the call to feel instant, diagnose **end-of-speech to first audible output** by phase rather than blaming the LLM alone: silence/VAD wait, STT startup/inference, model first token, TTS synthesis, and mixer queueing. A greeting fast path may bypass the LLM entirely, so changing the main model will not improve its STT/TTS delay.

For this user's preferred architecture, present A/B/C tradeoffs before a major rebuild, then recommend a persistent streaming STT + lightweight voice-front agent + strong background workers. The front agent should converse, acknowledge, classify, and delegate; it should not replace the strong worker for substantive tasks. Do not inject unstable partial transcripts into normal history. Full measurement and architecture guidance: `references/discord-realtime-latency.md`.

### Live coworker / walking companion pattern

When the user wants a *natural always-on coworker* rather than a turn-based bot, split the design into two layers:

1. **Fast voice host**: Discord voice input is routed through a tiny classifier. Casual speech continues through the normal Hermes conversation path. Spoken build/task requests get an immediate short TTS acknowledgement ("Alles klar, ich starte das im Hintergrund…") so the call never waits on a full agent turn.
2. **Background job layer**: Persist the task as a voice job under the active profile (for example `voice_jobs/jobs/<job_id>.json`), dispatch a worker asynchronously, and post non-conversational Discord status updates tagging the user (`started`, `running`, `done`, `blocked`). Keep these updates marked non-conversational where the adapter supports it so status chatter does not poison chat history.
3. **Worker routing**: Prefer Codex CLI for coding/build tasks when authenticated; if standalone Codex returns auth failures (401 / missing bearer) but Hermes' own provider works, fall back to `hermes -z '<task>' --yolo` in an isolated git workdir. This keeps the voice-worker path usable while stronger Anthropic/Seal/Codex integrations are being wired in.
4. **Mobile delivery**: Copy generated safe artifacts (`.html`, `.md`, images, scripts, etc.) into a per-job artifacts directory and include tap-ready paths/links or screenshots in the Discord completion update. Start with files/screenshots before adding public tunnels.

Verification for this mode:

- Unit-test task/status heuristics and job-store persistence.
- Smoke-test worker success with a fake worker that creates an `index.html` artifact.
- Smoke-test Codex-auth-failure fallback to Hermes oneshot.
- Restart the supervised gateway from outside gateway env (`env -u HERMES_GATEWAY_SESSION -u _HERMES_GATEWAY ... hermes --profile <profile> gateway restart`) and confirm logs show Discord connected, VoiceReceiver started, and voice autojoin active when configured.

## CLI local voice mode

Use for direct laptop conversation when the user is at the machine running Hermes:

```bash
hermes
# then inside the interactive CLI:
/voice on
# Press Ctrl+B to record; silence auto-stops after configured duration.
```

The record key is configured via `voice.record_key` in `~/.hermes/config.yaml`.

## Installation / setup fixes to capture when missing

Do not save transient "missing binary" facts as durable constraints. Capture the fix and run it when appropriate.

Python extras:

```bash
pip install "hermes-agent[voice]"       # CLI microphone + audio playback
pip install "hermes-agent[messaging]"   # Discord + Telegram gateway, includes discord.py[voice]
pip install "hermes-agent[tts-premium]" # premium TTS providers, e.g. ElevenLabs
pip install "hermes-agent[all]"         # all common extras
```

System packages:

```bash
# macOS
brew install portaudio ffmpeg opus
brew install espeak-ng   # only needed for some local TTS providers such as NeuTTS

# Ubuntu/Debian
sudo apt install portaudio19-dev ffmpeg libopus0
sudo apt install espeak-ng
```

Local STT option inside the Hermes runtime venv:

```bash
cd ~/.hermes/hermes-agent
. venv/bin/activate
python -m pip install -U faster-whisper
python -m hermes_cli.main config set stt.enabled true
python -m hermes_cli.main config set stt.provider local
python -m hermes_cli.main config set stt.local.model small
python -m hermes_cli.main config set stt.local.language ''
```

### Nvidia Parakeet MLX STT provider

When the user asks for Nvidia/Parakeet voice-to-text instead of Whisper, configure a Hermes command STT provider using `parakeet-mlx` (Apple Silicon / MLX):

```bash
cd ~/.hermes/hermes-agent
. venv/bin/activate
python -m pip install -U parakeet-mlx
CMD="$HOME/.hermes/hermes-agent/venv/bin/parakeet-mlx {input_path} --model {model} --output-format txt --output-dir {output_dir} --output-template transcript --chunk-duration 120"
python -m hermes_cli.main config set stt.enabled true
python -m hermes_cli.main config set stt.provider parakeet
python -m hermes_cli.main config set stt.providers.parakeet.type command
python -m hermes_cli.main config set stt.providers.parakeet.model mlx-community/parakeet-tdt-0.6b-v3
python -m hermes_cli.main config set stt.providers.parakeet.format txt
python -m hermes_cli.main config set stt.providers.parakeet.timeout 600
python -m hermes_cli.main config set stt.providers.parakeet.command "$CMD"
```

If the user wants an allowed-language subset (e.g. only English/German/Spanish), use `scripts/parakeet_stt_limited.py` and install `langid`; see `references/parakeet-mlx-stt-provider.md`.

For another Hermes profile, set `HERMES_HOME` or use `--profile` consistently, e.g.:

```bash
HERMES_HOME=~/.hermes/profiles/general python -m hermes_cli.main config set stt.enabled true
HERMES_HOME=~/.hermes/profiles/general python -m hermes_cli.main config set stt.provider local
HERMES_HOME=~/.hermes/profiles/general python -m hermes_cli.main config set stt.local.model small
```

No-key TTS options include Edge TTS and some local providers; cloud alternatives need their provider API keys.

## Fix wrong detected language in voice transcription

If the user says English voice notes are being interpreted as Russian (or another wrong language), do **not** only set `stt.local.language` while a different provider is active. First check `stt.provider`.

For Whisper/local STT, pin the spoken language:

```bash
~/.hermes/hermes-agent/venv/bin/hermes config set stt.provider local
~/.hermes/hermes-agent/venv/bin/hermes config set stt.local.language en
# For German later:
# ~/.hermes/hermes-agent/venv/bin/hermes config set stt.local.language de
```

If `stt.provider` is a command provider such as `parakeet`, `stt.local.language` does not affect it. Either extend that provider's command/wrapper to pass a language hint (if supported) or switch to `local` Whisper for reliable fixed-language transcription.

## Verification pattern

After configuring STT, verify the actual transcription path before declaring success. A deterministic macOS smoke test:

```bash
cd ~/.hermes/hermes-agent
. venv/bin/activate
say -v Anna -o /tmp/hermes-stt-test.aiff 'Hallo Hermes, dies ist ein Test für die Sprachnachricht.'
ffmpeg -y -loglevel error -i /tmp/hermes-stt-test.aiff /tmp/hermes-stt-test.ogg
python - <<'PY'
import json, sys
sys.path.insert(0, '$HOME/.hermes/hermes-agent')
from tools.transcription_tools import transcribe_audio
print(json.dumps(transcribe_audio('/tmp/hermes-stt-test.ogg'), ensure_ascii=False, indent=2))
PY
```

Expected shape: `success: true`, `provider` matching the configured STT provider (`local`, `parakeet`, etc.), and transcript close to the spoken sentence. For profile-specific config, run with `HERMES_HOME=~/.hermes/profiles/<name>` and repeat the same probe.

For Discord voice specifically, do a real receive/playback QA when the user says Hermes is not hearing them. Monitor the profile log while they speak and verify the chain `UDP/RTP → decoded audio → utterance complete → Transcribed → Voice input from user → TTS audio saved → Playing TTS`. See `references/discord-voice-receive-qa.md` for the exact probe and interpretation.

Also test a short silence sample. Silence/background-noise should not become a fake user utterance:

```bash
ffmpeg -y -loglevel error -f lavfi -i anullsrc=r=16000:cl=mono -t 0.8 /tmp/hermes-discord-silence.wav
HERMES_HOME=~/.hermes/profiles/general python - <<'PY'
import json
from tools.transcription_tools import transcribe_audio
for p in ['/tmp/hermes-stt-test.ogg', '/tmp/hermes-discord-silence.wav']:
    print(p, json.dumps(transcribe_audio(p), ensure_ascii=False, indent=2))
PY
```

If using a command STT wrapper such as Parakeet, make sure the wrapper writes transcript text only to `{output_path}` and suppresses child CLI status output (`stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL` or equivalent). Hermes' command STT runner falls back to stdout when the output file is empty; status text like `transcription complete. Outputs saved in ...` can otherwise be misrouted as a Discord voice input.

Restart active gateways after STT/config changes so Telegram/Discord voice use the new config. If the profile gateway is launchd-supervised, restart it from outside the gateway environment rather than starting a second foreground gateway:

```bash
# normal shell, not from inside the running gateway process
env -u HERMES_GATEWAY_SESSION -u _HERMES_GATEWAY -u HERMES_SESSION_SOURCE -u HERMES_SESSION_KEY -u HERMES_HOME \
  ~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile general gateway restart
```

Use `gateway run --replace` only when you intentionally want a foreground long-lived gateway and no supervised service is already running. Telegram may still show short-lived `getUpdates` conflicts while the old polling session expires.

## User-facing response pattern

When asked "finde einen weg das ich dich anrufen kann":

- Say **yes**.
- Offer two choices:
  - "Sofort: Telegram Voice Messages" if Telegram is configured.
  - "Bester echter Call: Discord Voice Channel" as the recommended path.
- State the exact missing blocker, usually `DISCORD_BOT_TOKEN`/Discord bot setup, instead of listing every possible configuration branch.
- If TTS is available, optionally demonstrate with a short generated audio reply.

Keep it concise and actionable; the user prefers decisive ship-fast guidance.

## Spoken acknowledgements for voice tasks

When the user says they’ll “take care of it” or gives a voice command, answer with one concrete action phrase, not vague reassurance. Say what is already happening:

- Calendar: "Ich erstelle den Termin."
- Mail: "Ich lese deine Mails." / "Ich sende die E-Mail."
- Research: "Ich recherchiere das."
- Browser check: "Ich prüfe das im Browser."
- Background work: "Ich setze das im Hintergrund um."
- Worker handoff: "Ich starte einen Hintergrund-Agenten."

Use the action-specific phrase as the first audible response. Keep details and IDs in text, not speech.

## Voice command routing

When the user gives a voice command or asks Hermes to “take care of it”, do **not** answer with vague reassurance like “Ich kümmere mich darum”. Instead, speak one short sentence that names the concrete action that is already underway:

- Calendar: "Ich erstelle den Termin."
- Mail: "Ich lese deine Mails." / "Ich sende die E-Mail."
- Research: "Ich recherchiere das."
- Browser check: "Ich prüfe das im Browser."
- Background work: "Ich setze das im Hintergrund um."
- Worker handoff: "Ich starte einen Hintergrund-Agenten."

Use the action-appropriate phrase as the first audible response. Keep details and IDs in text, not in speech. See `references/voice-acknowledgement-pattern.md` for the mapping and examples.

## TTS voice selection

When the user asks for a better voice, treat it as an audition/selection task, not only a config write. Present several short samples when possible, return `MEDIA:` links, and ask the user to pick the name. If they ask for “mehr Auswahl”, produce more candidates instead of defending the current choice.

Set Hermes TTS voices through the CLI, not direct config patching:

```bash
hermes config set tts.edge.voice <EDGE_VOICE_NAME>
hermes config set tts.provider elevenlabs
hermes config set tts.elevenlabs.voice_id <VOICE_ID>
```

For JARVIS / Iron Man voice requests, acknowledge that online community voices may exist in German and English. The practical requirement for Hermes is an API-capable provider, stable voice ID, or a custom command provider wrapper. Prefer ElevenLabs when the user can provide a voice-library link or voice ID; ask for the concrete link instead of claiming no such voice exists.

If no API-ready voice link is available and the user wants a private-project JARVIS vibe, use the custom command-provider fallback in `references/tts-voice-selection-and-jarvis.md`: British Edge TTS (`en-GB-RyanNeural`) plus restrained ffmpeg post-processing, registered as `tts.providers.jarvis`. Copy/adapt `scripts/jarvis_style_tts.py`, verify it produces a real MP3, then set `tts.provider=jarvis`.

## References

- `references/voice-mode-doc-notes.md` — condensed Hermes Voice Mode notes: commands, package/system requirements, config skeleton, Discord live-call specifics, and probe interpretation.
- `references/voice-acknowledgement-pattern.md` — concrete spoken ack mapping for calendar, mail, research, browser checks, and background handoffs.
- `references/voice-command-routing.md` — session note for control phrases like "new chat" and the rule that substantive background work should hand off to native `delegate_task` rather than a second CLI worker.
- `references/tts-voice-selection-and-jarvis.md` — voice audition workflow, useful Edge TTS German female candidates, and JARVIS/ElevenLabs integration guidance.
- `references/parakeet-mlx-stt-provider.md` — Nvidia Parakeet MLX command-provider setup, model id, profile config, and language allowlist wrapper notes.
- `references/stt-language-selection.md` — fixing wrong-language STT results (e.g. English detected as Russian), including provider caveat for `parakeet` vs `local` Whisper.
- `references/discord-live-coworker.md` — implementation notes for always-on Discord voice coworker mode: fast acks, persistent voice jobs, Codex/Hermes fallback workers, Discord status tagging, artifact delivery, and verification.
- `references/discord-realtime-latency.md` — phase-by-phase latency measurement, persistent Parakeet MLX streaming (including dedicated thread affinity), lightweight voice-front routing, speculative partial-transcript handling, and p50/p95 verification targets.
- `references/discord-voice-working-sound.md` — Perplexity-style on-demand work bed: idle silence, agent-turn lifecycle, ducking, concurrent-turn reference counting, tests, and live audition.
- `references/discord-voice-receive-qa.md` — live QA/debug checklist for proving Discord voice receive/playback end-to-end with log evidence (`UDP/RTP → decoded → STT → Voice input → TTS playback`).
- `references/discord-voice-routing-runtime-notes.md` — session-derived notes on persona-driven TTS overrides, cloned runtime config, guild-serialized playback, and playback lifecycle events.
- `references/discord-autojoin-and-supervised-restart.md` — live-call restart recipe: set the autojoin triad, restart from outside the gateway environment, and verify rejoin/receiver/packet logs.
- `scripts/parakeet_stt_limited.py` — reusable command-provider wrapper that runs Parakeet and blocks transcripts outside an ISO language allowlist.
- `scripts/jarvis_style_tts.py` — reusable command-provider wrapper for a JARVIS-inspired private-project voice using Edge TTS + ffmpeg post-processing.

## Pitfalls

- **Do not call Telegram voice messages a real call.** Set expectations: turn-based voice notes.
- **Do not promise Discord Voice is ready without checking `.env` for Discord credentials.** `discord`/`nacl` packages being installed only proves the Python side is available.
- **Gateway restart may be needed after `.env` changes.** Platform tokens are read at process start.
- **Discord autojoin is runtime state, not config state.** After a supervised restart, confirm the logs show `Discord voice autojoin active`, `VoiceReceiver started`, and packet flow before assuming the bot recovered its channel state.
- **`/voice join` requires the user to already be in a Discord voice channel.** The bot joins the user's current VC.
- **Discord allowed users should be numeric IDs.** Username-based allowlists may need extra Discord intents.
- **Telegram voice bubbles depend on audio format conversion.** If a TTS provider emits MP3/WAV, ensure `ffmpeg` is present for Opus/OGG delivery.
- **CLI voice mode requires microphone/audio dependencies.** Missing `sounddevice` affects local CLI recording, not necessarily Telegram/Discord gateway voice-message handling.
- **Distinguish incoming STT from outgoing voice replies.** For “I’ll send you a voice memo”, set up `stt.enabled/provider/local.model`; only enable `/voice on` or `/voice tts` if the user wants audio replies too.
- **Multi-profile Telegram gateways can conflict if token sourcing is wrong or duplicated.** Check running processes and each profile’s effective `HERMES_HOME` / `.env` token source when logs show repeated `getUpdates` conflicts. Two different profiles are fine only when they use distinct bot tokens; one bot token must have only one active polling gateway.
- **Foreground over notify-on-complete in Telegram sessions.** Restart/probe commands should generally be foreground with a bounded timeout; background completion notifications can flood Telegram with raw stdout if configured to auto-deliver.
- **Do not route control phrases through the model turn.** Treat "new chat" / "new session" / "neuer Chat" as immediate voice-router commands so the session resets before any normal assistant reply.
- **Do not schedule generic detached pre-acks if the final response may arrive first.** Spoken acknowledgements must be truthful and owned by the path that actually carries the work; otherwise the user hears duplicate or late reassurance.
- **Prefer native `delegate_task` for substantive voice-originated work.** Keep the voice front-end short; do not invent a second CLI worker layer when the native background handoff is available.
- **Do not persist speaker/persona overrides into the user’s global TTS config.** Clone the config at runtime, apply provider/voice/model/speed overrides there, and keep the changes transport-local.
- **Serialize Discord playback per guild.** Two clips can overlap even when TTS itself succeeded; guard playback with a guild-scoped lock and emit both start/completion lifecycle events.
- **Verify mixer activation at runtime.** `discord.voice_fx.enabled: true` is not proof that the mixer path is live; check actual runtime behavior when speaking during TTS.
