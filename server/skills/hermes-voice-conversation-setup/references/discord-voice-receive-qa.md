# Discord Voice Receive QA / Debug Checklist

Use when the user says Hermes is in a Discord voice channel but does not seem to hear or answer them.

## Goal

Verify the full live path with real evidence, not assumptions:

1. Discord voice connection active.
2. UDP packets arrive at the voice socket listener.
3. RTP packets are parsed and are not only bot keepalives.
4. Opus frames decode into PCM buffers.
5. Silence detection emits an utterance.
6. STT transcribes the WAV.
7. Gateway receives `Voice input from user ...`.
8. TTS is generated and `Playing TTS in voice channel` appears.

## Useful live log probe

Run against the active profile log while the user speaks continuously for 30–90 seconds:

```bash
python - <<'PY'
import time, pathlib, re
p=pathlib.Path.home()/'.hermes/profiles/general/logs/agent.log'
pat=re.compile(r'Voice UDP|Voice RTP|Voice decoded|Voice utterance|SPEAKING|Voice input|Transcribing|Transcribed|response ready|TTS audio saved|Playing TTS|ERROR|WARNING|Traceback')
red=re.compile(r'[A-Za-z0-9_-]{20,}')
seen=[]
with p.open('r', errors='ignore') as f:
    f.seek(0,2)
    end=time.time()+60
    while time.time()<end:
        line=f.readline()
        if not line:
            time.sleep(0.1); continue
        if pat.search(line):
            line=red.sub('<redacted>', line)
            seen.append(line)
            print(line, end='', flush=True)
print('\n---SUMMARY---')
print('utterance=', any('Voice utterance complete' in s for s in seen))
print('voice_input=', any('Voice input from user' in s for s in seen))
print('tts_playing=', any('Playing TTS' in s for s in seen))
PY
```

Interpretation:

- No `Voice UDP packet seen`: Discord is not delivering audio to the listener; check join/channel/permissions/socket listener.
- UDP keepalive-sized packets only, no user RTP: user may not be transmitting, wrong channel, or speaking indicator/SSRC mapping issue.
- `Voice RTP packet` and `Voice decoded audio` but no utterance: silence threshold/min duration or buffer handling issue.
- `Voice utterance complete` but no `Transcribed`: STT command/provider issue.
- `Transcribed` but no `Voice input from user`: hallucination filter or empty transcript path.
- `Voice input from user` but no `Playing TTS`: normal agent turn, voice mode, or TTS/playback issue.

## Durable implementation lessons

- Add INFO-level, bounded debug logs at each pipeline stage while debugging (`UDP`, `RTP`, `decoded`, `utterance complete/discarded`). Keep counters bounded so normal logs do not flood.
- Short greeting/backchannel utterances should have a fast path before the full agent turn. Examples: `Hallo`, `hello`, `hörst du mich`, `can you hear me` → immediate spoken acknowledgement. Pure backchannels such as `okay`, `ja`, `yeah`, `mhm` should usually be swallowed (no model turn, no spoken reply) to avoid ack loops.
- For ordinary non-task voice input, consider a two-stage response: immediately schedule a tiny TTS ack (`Bin dran.` / `On it.`) and then let the normal/heavy model produce the real answer. The ack must not block the model turn.
- Do not speak long numeric IDs (Discord user IDs, job IDs) in live calls. Use friendly names in prompts and put IDs/status details in Discord text updates only.
- Do not send long Discord voice replies in full. Cap spoken responses and post the full text to Discord; long TTS pauses listening and makes the call feel like a black box.
- Treat substantive spoken debug/build/test requests as background jobs by default: speak a short ack, persist job state, run worker asynchronously, and post status/completion updates.
- Expand voice-task heuristics beyond build verbs to include debug/test/selftest/verify/background-agent language (`debugge`, `teste`, `Selbsttest`, `verifiziere`, `Background Agent`).
- When a task changes code, restart the supervised profile gateway from outside the gateway environment and verify reconnect/autojoin logs.

## Restart verification

```bash
env -u HERMES_GATEWAY_SESSION -u _HERMES_GATEWAY -u HERMES_SESSION_SOURCE -u HERMES_SESSION_KEY -u HERMES_HOME \
  ~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile general gateway restart
sleep 8
tail -n 100 ~/.hermes/profiles/general/logs/agent.log | grep -E 'Connected as|VoiceReceiver|Discord voice autojoin|Gateway running|Voice connection complete|ERROR|Traceback'
```

Expected evidence:

- `Connected as ...`
- `Voice connection complete.`
- `VoiceReceiver started`
- `Discord voice autojoin active ...`
- `Gateway running with ... platform(s)`
