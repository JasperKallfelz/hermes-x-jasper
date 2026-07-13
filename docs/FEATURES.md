# Features

Every switch below lives in `~/.hermes/config.yaml`. The shipped `config.example.yaml` sets sane defaults for all of them; this page explains what they actually do and what each one costs you.

Features marked **\[patch\]** only exist after `patches/voice-and-desktop-features.patch` is applied. Plain upstream Hermes silently ignores those keys.

---

## Memory

```yaml
memory:
  memory_enabled: true
  user_profile_enabled: true
  write_approval: false
  provider: ""        # "" | holographic | mem0 | hindsight | openviking | retaindb
```

Hermes keeps a bounded, curated memory and a user profile, both injected into the system prompt. It writes to them on its own as it learns things about you.

- `write_approval: true` makes every memory write ask first. Turn this on if unprompted "I noticed you prefer X" entries bother you.
- `provider` swaps the built-in store for an external engine. **Only one at a time**, and the plugin has to be installed separately — `holographic` and `lcm` are not bundled with Hermes. Leave it empty unless you have installed one.

## Context engine

```yaml
context:
  engine: compressor  # compressor | lcm
```

`compressor` is the built-in lossy summarizer that kicks in near the model's context limit. `lcm` (Lossless Context Management) preserves the full history instead of summarizing it, but must be installed as a `plugins/context_engine/lcm/` plugin first. Setting `engine: lcm` without the plugin does nothing useful.

## Delegation

```yaml
delegation:
  orchestrator_enabled: true
  model: ""                     # e.g. "google/gemini-3-flash-preview"
  provider: ""                  # e.g. "openrouter"
  max_concurrent_children: 3
  subagent_auto_approve: false
```

`delegate_task` spawns subagents for parallel work. The useful trick: point `model`/`provider` at something cheap and fast, so a big model orchestrates while small models do the legwork. Empty values inherit the parent's provider and credentials.

Keep `subagent_auto_approve: false`. It is the difference between subagents that ask before doing something irreversible and subagents that do not.

## Browser with auto-CDP **\[patch\]**

```yaml
browser:
  cdp_url: "http://127.0.0.1:9222"
  auto_launch_local_cdp: true   # [patch]
  allow_private_urls: false
```

Stock Hermes drives a fresh headless browser, which anti-bot systems block on sight and which is logged into nothing. With `cdp_url` pointed at a local DevTools endpoint, it attaches to **your real Chrome** instead — your cookies, your sessions, your logins.

`auto_launch_local_cdp` is the patch's contribution: when that endpoint is not up, Hermes starts a Chrome with remote debugging enabled on demand, using a dedicated profile directory. You log into the sites you care about once, in that window, and it persists.

> This is the single most powerful and most dangerous setting in the file. The agent inherits every session you have. See [SECURITY.md](../SECURITY.md).

`allow_private_urls: false` keeps the agent off `localhost` and your LAN. Leave it that way.

## Code execution

```yaml
code_execution:
  mode: project    # project | none
  timeout: 300
  max_tool_calls: 50
```

`execute_code` runs Python that calls Hermes tools over RPC. The point is context economy: intermediate tool results stay inside the script instead of being pasted into the model's context window. A 200-result search becomes one summary line. `mode: none` disables it.

## Streaming

```yaml
streaming:
  enabled: true
  transport: auto
  edit_interval: 0.8
```

Replies appear token by token on chat platforms instead of arriving as one late block. `edit_interval` trades latency against platform rate limits — dropping it below ~0.5 s will get you throttled by Telegram.

---

## Text to speech

```yaml
tts:
  provider: edge        # edge | jarvis | openai | elevenlabs | piper | ...
  edge:
    voice: en-US-AriaNeural
```

`edge` (Microsoft Edge TTS) is free, needs no API key, and is the default. It requires `ffmpeg`.

### The JARVIS-style voice

Upstream supports **command providers**: any binary that turns a text file into an audio file can be a TTS backend. `config.example.yaml` wires one up:

```yaml
tts:
  providers:
    jarvis:
      type: command
      command: "python3 ~/hermes-friends-starter/scripts/jarvis_style_tts.py {input_path} {output_path}"
      output_format: mp3
      timeout: 120
      voice_compatible: true
```

Switch to it with `tts.provider: jarvis`. `scripts/jarvis_style_tts.py` synthesizes with Edge TTS, then runs an ffmpeg filter chain — comms-band filtering, compression, a touch of echo and chorus — for a filtered assistant-console voice. It is a style, not an impersonation of anyone.

Tune it without editing the script:

```bash
HERMES_TTS_VOICE=en-GB-RyanNeural HERMES_TTS_RATE=-4% HERMES_TTS_PITCH=-5Hz
```

Use an absolute path in `command` if you cloned the starter somewhere other than `~/hermes-friends-starter`.

## Speech to text

```yaml
stt:
  enabled: true
  provider: local       # local | openai | groq | mistral | elevenlabs
  local:
    model: base         # tiny | base | small | medium | large-v3
    language: ""        # "" = auto-detect
```

`local` runs faster-whisper on your machine — free, private, no API key. Bigger models are more accurate and slower.

> Upstream has **no** command-provider hook for STT (unlike TTS). `scripts/parakeet_stt_limited.py` therefore is not wired in through config: it is a standalone helper you can call directly, and the Parakeet path proper is the Discord streaming STT below. The script transcribes with Parakeet MLX, then guards the result — if the transcript comes back in a language that is neither German nor English, it re-runs faster-whisper with the language pinned and picks the more plausible output.

---

## Discord voice **\[patch\]**

This is the bulk of the patch: a Discord bot you talk to like a phone call rather than a chatbot.

```yaml
discord:
  voice_fx:
    enabled: true
    ack_enabled: true            # speak before the first tool call
    ambient_enabled: false
    ambient_gain: 0.18
    duck_gain: 0.06
    speech_gain: 1.0

    join_greeting_enabled: true  # [patch]
    join_greeting_phrases:       # [patch]
      - "Hey, I'm here."
      - "Hello, ready when you are."
      - "Welcome back."

    barge_in_enabled: true       # [patch]
    barge_in_min_ms: 320         # [patch]
```

**Continuous mixer.** Instead of playing one clip at a time, the patch keeps a mixer running on the voice connection. That is what makes ambient beds, ducking and interruption possible at all.

**Acknowledgements** (`ack_enabled`). The agent says something short — "let me look that up" — before its first tool call, so a ten-second pause does not feel like a dropped call. There is also a quiet "work bed" under long turns, mixed locally and stopped before the reply.

**Barge-in** (`barge_in_enabled`). Start talking while the bot is talking and it stops. `barge_in_min_ms: 320` is the guard against coughs, keyboard noise and "mhm" backchannels triggering it — lower it if it feels unresponsive, raise it if it cuts out for nothing.

**Join greeting.** When an *allowed* user joins the bot's channel, it says one of `join_greeting_phrases`. Set your own. They are spoken through the configured TTS voice, so keep them short and write them in whatever language your voice speaks.

**Voice commands.** Spoken shortcuts, recognised in German and English: *new chat* / *neuer Chat*, *stop* / *stopp*, *agent status*, *voice commands* / *Sprachbefehle*.

**Voice jobs.** A long request ("go build X and test it") does not block the call. It becomes a detached background agent; the call stays responsive, and *agent status* tells you where things are.

### Streaming STT **\[patch\]** — Apple Silicon only

```yaml
    streaming_stt_enabled: false
    streaming_stt_model: "mlx-community/parakeet-tdt-0.6b-v3"
    streaming_stt_endpoint_silence: 0.45
    streaming_stt_chunk_ms: 500
```

Off by default. When on, Hermes keeps a Parakeet MLX model resident and decodes Discord audio **incrementally**, instead of writing a WAV at the end of each utterance and shipping it to a file-based STT provider. The result is a noticeably shorter gap between you finishing a sentence and the agent starting to think.

Needs `parakeet-mlx`, which needs Apple Silicon (`setup.sh` installs it there automatically). On any other machine, leave it off — Hermes falls back to the file-based `stt.provider` with no complaints.

`streaming_stt_endpoint_silence` is how long a pause has to be before your turn is considered finished. Raise it if you get cut off mid-thought; lower it if the bot is slow to react.

---

## Turning things off

Everything here degrades cleanly. `voice_fx.enabled: false` gives you a text-only Discord bot. `streaming.enabled: false` gives you block replies. Removing `browser.cdp_url` gives you the stock headless browser. Nothing in the patch is load-bearing for the rest of Hermes.
