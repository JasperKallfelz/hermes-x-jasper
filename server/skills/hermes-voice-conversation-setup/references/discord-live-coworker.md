# Discord Live Coworker / A+ Voice Architecture

Use when Hermes should feel like an always-on colleague in Discord voice rather than a turn-by-turn bot. Keep Discord as a transport while isolating reusable voice-domain and orchestration logic so LiveKit/WebRTC can be added later.

## Target UX

- Continuous conversation from phone/headphones.
- Immediate short acknowledgements; no silent wait on a large model.
- User can interrupt TTS (barge-in) without losing the new utterance.
- Coding/build work runs as durable background jobs.
- Existing Hermes tools (mail, calendar, browser, personal tasks) keep working until dedicated workers are genuinely executable.
- Worker completion is concise in voice and detailed in Discord text/artifacts.
- Different workers can carry `speaker_id` / `voice_profile` metadata with graceful fallback to the current TTS voice.

## A+ architecture

```text
Discord transport ─┐
Future LiveKit ─────┼─> Voice engine
                   │   ├─ events / turn state / barge-in
                   │   ├─ mixer / TTS playback
                   │   └─ speaker personas
                   └─> Hermes orchestrator
                       ├─ fast conversation host
                       ├─ typed worker registry
                       ├─ persistent job store
                       └─ background workers
```

Keep voice-domain primitives dependency-light: event types/bus, speaker-persona registry, and conservative interruption state. Do not inject them as model tools or couple them to Discord classes.

## Continuous mixer and barge-in

`discord.VoiceClient.play()` validates with `isinstance(source, discord.AudioSource)`. Merely implementing `read()` and `is_opus()` is insufficient. The mixer must inherit `discord.AudioSource` when Discord is installed, with an import-safe fallback base when the optional dependency is absent.

Recommended behavior:

1. Install one continuous mixer per guild.
2. Keep the inbound `VoiceReceiver` running while mixer speech is active.
3. Snapshot active speakers without consuming receiver buffers.
4. Only allow authorized users to interrupt.
5. Require sustained decoded speech (about 320 ms; tune by config), not one RTP packet.
6. Call `mixer.stop_speech()` under a short lock; do not clear inbound audio.
7. Continue silence detection/STT on the preserved utterance.
8. Reset one-shot interruption state when the turn completes, including STT-empty/error returns.

Expected latency is threshold plus listen-loop cadence; a 320 ms threshold with a 200 ms loop normally interrupts within roughly 0.4–0.6 s.

Emit transport-neutral events where practical: `speech_started`, `turn_completed`, `tts_started`, and `tts_interrupted`. Note that `tts_started` should be emitted before or atomically with queueing playback if exact ordering matters.

## Join greetings

For an always-on companion, handle Discord voice-state transitions locally at the transport edge rather than launching an agent turn:

1. Trigger only when `after.channel` differs from `before.channel` and matches the bot voice client's current channel. This covers fresh joins and moves into the bot channel, but not mute/deafen updates.
2. Ignore bots and apply the normal Discord user/role authorization check before speaking.
3. Schedule greeting synthesis/playback as a task so the Discord event callback returns immediately.
4. Route the phrase through the installed continuous mixer. A greeting is a distinct UX feature, so it may bypass tool-ack enablement while still respecting its own `join_greeting_enabled` gate.
5. Keep phrases configurable in `config.yaml`, for example `join_greeting_phrases: ["Hello.", "Hello."]`; do not add a non-secret environment variable.
6. Add behavior tests for allowed user + same channel, wrong channel, bot member, and disabled greeting.

A gateway restart does not itself produce a join transition for someone already present. Live verification requires leaving and re-entering (or moving out and back in) after restart.

## Fast host and worker routing

- Suppress short backchannels such as `okay`, `ja`, `yeah`, `mhm`, `hm`, `uh`, and `äh`; do not launch a full turn or answer with another ack.
- Handle greetings and job-status questions locally.
- Background only tasks whose routed worker is currently executable.
- Coding/build requests may be persisted and dispatched to Codex/Hermes in an isolated git workdir.
- Mail, calendar, browser, research, and personal requests must continue through the normal Hermes tool path until their dedicated worker is operational. **Do not create a typed-but-unsupported job and return early**; that silently regresses existing capabilities.
- A worker registry may expose unsupported kinds as `needs_input`, but gateway routing must not select those workers by default.

Useful job fields: id, title, transcript/prompt, source, status, worker kind/name, speaker ID, voice profile, error, result summary, artifacts, timestamps.

## Persona and completion announcements

Metadata alone is only a foundation. Verify the full path:

1. Job stores `speaker_id` and `voice_profile`.
2. Completion notification includes a short `voice_announcement` plus persona metadata.
3. Gateway schedules TTS and passes persona metadata into voice playback.
4. Playback resolves the persona; if provider-specific voice override is unsupported by the current TTS API, use the configured global voice without failing.
5. Put job IDs, paths, logs, and long summaries in Discord text—not spoken audio.

Do not claim distinct voices are active merely because metadata exists. Confirm the TTS API accepts a per-call voice/provider override or state clearly that current playback is using fallback voice.

## Persistent job and artifact handling

- Store JSON jobs under the active profile: `voice_jobs/jobs/<job_id>.json`.
- Use isolated workdirs: `voice_jobs/work/<job_id>/`.
- Copy safe outputs into `voice_jobs/artifacts/<job_id>/`.
- Save raw worker output as an artifact; never dump it into chat.
- Dataclass defaults should preserve loading of older JSON jobs missing newly added fields. Add a regression test for this.
- Try Codex CLI for coding tasks; if standalone auth fails while Hermes' OpenAI-Codex provider works, use a bounded `hermes -z '<task>' --yolo` fallback.

## Verification checklist

Before reporting completion:

1. Run focused tests for mixer, voice domain, voice jobs, and existing voice-command regressions.
2. Test real `isinstance(VoiceMixer(), discord.AudioSource)` with the installed discord.py.
3. Simulate missing `discord` and verify the mixer module still imports.
4. Test conservative barge-in threshold and one-shot behavior.
5. Add an adapter-level test proving barge-in calls `stop_speech()` and preserves receiver data.
6. Test unsupported mail/calendar routing falls through to normal Hermes rather than returning a blocked job.
7. Test older persisted job JSON remains loadable.
8. Test completion persona metadata reaches `play_in_voice_channel`.
9. Run `py_compile` on touched files and `git diff --check`.
10. Independently review the diff; fix blocking issues, then rerun affected tests.
11. Restart the supervised profile gateway from outside inherited gateway state.
12. Inspect live logs for Discord connection, `VoiceReceiver started`, mixer installation, and absence of `source must be an AudioSource`.
13. Perform a real voice-channel test: speak while TTS is active and confirm interruption plus successful transcription of the interrupting utterance.

A green unit suite is not deployment verification. If restart/live checks remain undone, report the implementation as tested but not fully deployed.

## Safe restart

```bash
env -u HERMES_GATEWAY \
    -u HERMES_GATEWAY_SESSION \
    -u _HERMES_GATEWAY \
    -u HERMES_SESSION_PLATFORM \
    -u HERMES_SESSION_SOURCE \
    -u HERMES_SESSION_KEY \
    hermes --profile <profile> gateway restart
```

If Hermes says it is refusing to restart from inside the gateway process, inspect inherited gateway/session markers and clear the markers above rather than starting a second gateway. Then read the profile gateway logs. Do not start a second foreground gateway on top of a launchd-supervised instance.

## Pitfalls

- Legacy one-shot playback pauses the receiver for echo prevention; no true barge-in is possible on that path.
- `voice_fx.enabled: true` is configuration intent, not proof the mixer installed.
- Do not clear receiver buffers during interruption.
- Do not interpret one RTP packet as substantive speech.
- Do not route useful mail/calendar/browser requests into non-executable worker stubs.
- Do not claim per-agent voices are working until metadata reaches real TTS/playback.
- Do not stop after tests if the task asked to build/run/verify; restart and inspect runtime logs.
- Preserve unrelated dirty work: snapshot the pre-existing diff, scope changed files, and never reset/clean/checkout a user's repository.
