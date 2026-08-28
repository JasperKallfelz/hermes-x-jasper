# Features

Every switch below lives in `~/.hermes/config.yaml`. The shipped `config.example.yaml` sets sane defaults for all of them; this page explains what they actually do and what each one costs you.

Config keys marked **\[patch\]** only exist after `patches/voice-and-desktop-features.patch` is applied. Other sections explicitly distinguish v0.20.6 upstream behavior from the remaining patch delta.

The experimental Pi RPC keys are not part of that stable overlay. They exist
only in an explicitly separate checkout created from `modules/pi-runtime/`.

---

## Memory

```yaml
memory:
  memory_enabled: true
  user_profile_enabled: true
  write_approval: false
  provider: ""        # "" | openviking | mem0 | hindsight | holographic | retaindb | byterover
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
  max_concurrent_children: 8    # unified cap: parallel AND background children
  max_spawn_depth: 3            # how deep a child may itself delegate (minimum 1)
  subagent_auto_approve: false
```

`delegate_task` spawns subagents for parallel work. The useful trick: point `model`/`provider` at something cheap and fast, so a big model orchestrates while small models do the legwork. Empty values inherit the parent's provider and credentials.

In v0.20.6, `max_concurrent_children` is the single cap for both synchronous fan-out and concurrent background delegation; the old `max_async_children` key is gone (`hermes config migrate` folds it in). Upstream defaults to `10` children and flat depth `1`; this starter deliberately uses `8` with `max_spawn_depth: 3`.

Keep `subagent_auto_approve: false`. It is the difference between subagents that ask before doing something irreversible and subagents that do not.

## Browser with auto-CDP **\[patch\]**

```yaml
browser:
  cdp_url: "http://127.0.0.1:9222"
  auto_launch_local_cdp: true   # [patch]
  allow_private_urls: false
```

Hermes v0.20.6 already supports `browser.cdp_url`, interactive `/browser connect`, and an opt-in snapshot of the active Chromium profile through `browser.use_real_profile`. With `cdp_url` pointed at a local DevTools endpoint, it attaches to that Chrome debugging profile instead of starting a disposable headless session.

`auto_launch_local_cdp` remains the patch's contribution: when a configured loopback endpoint is not up, Hermes starts Chrome on demand with a dedicated persistent debugging profile. The patch also pins DevTools to `127.0.0.1` on every managed/manual launch path. You log into sites once in that dedicated window and its profile persists. An explicitly set `BROWSER_TOOL_AUTO_CDP` environment variable overrides the config for that process; use `1` to enable or `0` to disable. Remote CDP endpoints never trigger a local launch.

> This is the single most powerful and most dangerous setting in the file. The agent inherits every session stored in that debugging profile. See [SECURITY.md](../SECURITY.md).

`allow_private_urls: false` keeps the agent off `localhost` and your LAN. Leave it that way.

## Code execution

```yaml
code_execution:
  mode: project    # project | strict
  timeout: 300
  max_tool_calls: 50
```

`execute_code` runs Python that calls Hermes tools over RPC. The point is context economy: intermediate tool results stay inside the script instead of being pasted into the model's context window. A 200-result search becomes one summary line. In v0.20.6 `mode` accepts only `project` (session cwd + active venv) or `strict` (isolated temp dir + `sys.executable`); there is no `none`. The upstream defaults are 300 seconds and 50 RPC tool calls. Keep `timeout` a short-orchestration guardrail — give a long test gate a larger budget in `.hermes-gates.json` instead of raising it here.

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

Hermes v0.20.6 already exposes per-call `provider` and `speed` arguments in the
model-facing `text_to_speech` tool. The patch does not duplicate those. It adds
keyword-only `provider_override`, `voice_override`, `model_override`, and
`speed_override` arguments for trusted transport/runtime callers, applying
voice and model values to both built-in and named-provider config without
mutating the loaded config. Those internal names are deliberately absent from
the model schema.

### The JARVIS-style voice

Upstream supports **command providers**: any binary that turns a text file into an audio file can be a TTS backend. `config.example.yaml` wires one up:

```yaml
tts:
  providers:
    jarvis:
      type: command
      command: "python3 ~/hermes-x-jasper/scripts/jarvis_style_tts.py {input_path} {output_path}"
      output_format: mp3
      timeout: 120
      voice_compatible: true
```

Switch to it with `tts.provider: jarvis`. `scripts/jarvis_style_tts.py` synthesizes with Edge TTS, then runs an ffmpeg filter chain — comms-band filtering, compression, a touch of echo and chorus — for a filtered assistant-console voice. It is a style, not an impersonation of anyone.

Tune it without editing the script:

```bash
HERMES_TTS_VOICE=en-GB-RyanNeural HERMES_TTS_RATE=-4% HERMES_TTS_PITCH=-5Hz
```

Use an absolute path in `command` if you cloned the starter somewhere other than `~/hermes-x-jasper`.

## Speech to text

```yaml
stt:
  enabled: true
  provider: local       # local | openai | groq | mistral | xai | elevenlabs | deepinfra
  local:
    model: base         # tiny | base | small | medium | large-v3
    language: ""        # "" = auto-detect
```

`local` runs faster-whisper on your machine — free, private, no API key. Bigger models are more accurate and slower.

> Upstream has **no** command-provider hook for STT (unlike TTS). `scripts/parakeet_stt_limited.py` therefore is not wired in through config: it is a standalone helper you can call directly. It transcribes with Parakeet MLX, then guards the result — if the transcript comes back in a language that is neither German nor English, it re-runs faster-whisper with the language pinned and picks the more plausible output.

---

## Discord voice — parked

The Discord voice stack (continuous mixer, barge-in, join greetings, voice jobs, streaming STT) has been split out of the main patch and is not currently shipped in this starter. It will return as a separate patch once stabilised.

## Experimental Pi RPC runtime — opt-in module

The separate [Pi runtime module](../modules/pi-runtime/README.md) pins Pi 0.84.3
and a `linux/arm64` image ID. Hermes remains the control plane; Pi is the
contained coding runtime. It is off by default, is never installed by root
`setup.sh`, and never receives a mutable image tag.

The reviewed tuple is base
`306db2776c6b6f1acc85c31c4dabba3263f0e9fd`, feature
`c1093d23837bab98013bc9929d0d2679416601e5`, and image
`sha256:e89f45110e9277902bafbf49009e842bc9e38180e668fea8a6ff3dcdb2dd2cdf`.
Evidence is 1,419 offline tests with zero failures/skips/retries, two identical
no-cache builds, Docker E2E 5/5, and independent READY review with no P0–P2.
No credentials or live config are included. The authenticated provider/model
E2E is a current gap because the prior OAuth expired; trusted-local manual auth
is outside contained execution and outside module setup.

---

## Turning things off

Everything here degrades cleanly. `streaming.enabled: false` gives you block replies. Removing `browser.cdp_url` gives you the stock headless browser. Nothing in the stable patch is load-bearing for the rest of Hermes. Pi rollback is switching to Hermes and removing use of its separate installation, not mutating the stable checkout.
