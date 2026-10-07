# Discord live coworker + bilingual voice notes

Session-derived implementation pattern for making Hermes feel like a live Discord voice coworker rather than a turn-based bot.

## Desired UX

- User joins Discord voice from phone/headphones while walking.
- Hermes stays in the voice room, listens continuously, and gives short spoken acknowledgements.
- Long tasks are handed off to background workers so the voice call remains responsive.
- Hermes tags the user in Discord/Telegram when a task starts, completes, blocks, or has a preview/artifact.
- Reply language follows the spoken language: German when German, English when clearly English; German is the default for short/ambiguous turns.

## Minimal architecture

1. **Voice host / router**
   - Receives transcript chunks from Discord voice.
   - Deduplicates recent near-identical transcripts.
   - Classifies utterance as casual chat, task request, status request, or cancel/stop.
   - For task requests, speaks an immediate short ack and returns without running a full agent turn.

2. **Persistent voice job queue**
   - Store jobs under the active profile, e.g. `~/.hermes/profiles/<profile>/voice_jobs/`.
   - Keep JSON job state: id, title, prompt, source, status, worker, artifacts, error, result summary.
   - Keep work/artifact dirs separate: `voice_jobs/work/<job_id>/`, `voice_jobs/artifacts/<job_id>/`.

3. **Background worker dispatch**
   - Prefer Codex CLI for coding/build tasks when authenticated.
   - If standalone Codex CLI fails with auth errors but Hermes' own provider works, fallback to `hermes -z '<task>' --yolo` in the isolated workdir.
   - Strip gateway/session env vars before launching nested Hermes/Codex so the worker does not inherit gateway-only state.
   - Copy safe artifacts (`.html`, `.css`, `.js`, `.py`, `.md`, `.json`, images, PDFs, txt) to the artifact dir.

4. **Discord updates**
   - Post concise non-conversational messages in the bound text channel.
   - Mention/tag the user for start/done/blocked.
   - Mark update metadata with `non_conversational` / `non_conversational_history` when using Discord adapter sends so status chatter does not poison history/backfill.

## Bilingual STT / response language pattern

For Parakeet command-provider STT, `stt.local.language` does not apply. Put language selection into the wrapper.

Recommended wrapper behavior:

- Allow only `de` and `en`.
- Use Parakeet for fast first pass.
- Suppress Parakeet stdout/stderr so status text like “transcription complete…” is not treated as speech by Hermes' command STT stdout fallback.
- Detect language with combined scoring:
  - `langid` result, if available.
  - German/English function-word markers.
  - German umlaut/ß bonus.
- Default to German on short/ambiguous utterances.
- If Parakeet output is non-allowed or weakly classified, run explicit Whisper fallback twice (`de`, `en`) and pick the more plausible candidate, with German tie-break.

For immediate voice acknowledgements/status in Discord:

- Detect reply language from the transcript using the same style of marker heuristic.
- German ack example: “Alles klar, ich starte das im Hintergrund. Ich tagge dich, wenn es fertig oder blockiert ist.”
- English ack example: “Got it, I’ll start that in the background. I’ll tag you when it’s ready or blocked.”
- Default/unclear ack should be German.

## Validation checklist

- German sample audio transcribes as German.
- English sample audio transcribes as English.
- Short/ambiguous input defaults to German.
- Spoken task request gets immediate TTS ack without waiting for full worker completion.
- `Was läuft gerade?` / `what is running?` returns recent voice job status in matching language.
- Background worker completion posts a concise tagged update with artifact paths or a blocker.
- Gateway restart shows Discord voice autojoin and VoiceReceiver active in logs.
