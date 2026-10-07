# Discord realtime voice latency

Use this note when the user wants Discord voice to feel instant, asks how long a spoken turn takes, or proposes starting inference before the sentence ends.

## Measure the pipeline before changing models

Treat time-to-first-audio as separate phases:

1. Last inbound speech packet → end-of-turn decision (fixed silence/VAD).
2. End-of-turn → stable transcript (STT startup + inference).
3. Transcript → first LLM token or direct fast-path decision.
4. First token → first playable TTS audio.
5. Queue/mixer delay before playback.

Read timestamps from the active profile's gateway log. Pair each utterance conservatively; concurrent utterances or an already-running turn can make a later TTS event belong to a different input. Report ranges and identify uncertainty rather than presenting a mismatched pair as exact.

A greeting such as `hey` may bypass the LLM entirely. Its latency is still end-of-turn + STT + TTS, so changing the main model cannot fix that path.

## Common file-based STT bottleneck

The Discord receiver may buffer audio until a silence threshold, write a WAV file, and only then call `transcribe_audio()`. A command STT provider can then spawn a fresh process for every utterance. In this architecture, recording is active while the user speaks, but **transcription and inference do not begin until the utterance is committed**.

Do not describe this as streaming merely because audio is continuously captured.

## Improvement ladder

### A. Low-risk latency pass

- Make the end-of-turn silence threshold configurable and benchmark roughly 350–500 ms instead of a long fixed delay.
- Use low/no reasoning for the conversational voice host.
- Enable provider fast/priority processing only when the selected model supports it.
- Keep spoken output short and begin TTS at the first stable sentence when the response transport supports token streaming.
- Preserve heavy models for background work.

This reduces post-speech delay but does not make file-based STT incremental.

### B. Streaming front-agent architecture (recommended)

- Keep a persistent warm STT process/session; do not reload the model for every WAV.
- Feed 200–300 ms audio chunks and emit partial transcripts with stability markers.
- Use semantic/VAD end-of-turn commitment around 350–500 ms.
- Route casual conversation through a small, fast voice-front model.
- Route substantive tasks to strong background agents after an immediate brief acknowledgement.
- Permit speculative inference only from stable transcript prefixes; cancel/restart it when the prefix changes.
- Stream TTS sentence-by-sentence and keep mixer speech cancellable for barge-in.

This gives the best quality/complexity tradeoff and avoids weakening task execution quality.

### C. Full realtime speech-to-speech

Use a continuous STT → LLM → TTS session with semantic turn detection, cancellable speculative decoding, streaming audio synthesis, and barge-in. This gives the lowest latency but is a larger transport/runtime change and may add provider cost.

## Parakeet MLX on Apple Silicon: persistent streaming pattern

Before replacing Parakeet with a smaller model, benchmark **model/process startup separately from inference**. A command provider that launches `parakeet-mlx` for every utterance can spend several seconds reloading the same model even on a fast Apple Silicon Mac. A deterministic probe is:

```bash
TMP=$(mktemp -d)
say -v Anna 'Hello, hörst du mich?' -o "$TMP/sample.aiff"
ffmpeg -loglevel error -y -i "$TMP/sample.aiff" -ar 16000 -ac 1 "$TMP/sample.wav"
/usr/bin/time -p parakeet-mlx "$TMP/sample.wav" \
  --model mlx-community/parakeet-tdt-0.6b-v3 \
  --output-format txt --output-dir "$TMP" --output-template transcript
```

On an M1 Max/64 GB with `parakeet-mlx 0.5.2`, repeated fresh-process runs of a short German phrase measured about **3.2–3.35 s each**. This shows that choosing a smaller model is not the first optimization; keep the current 0.6B model warm.

`parakeet_mlx 0.5.2` exposes a real incremental API:

- `from_pretrained(hf_id_or_path, dtype=...)` loads the model once.
- `StreamingParakeet(model, context_size, depth=1, ...)` owns per-stream decoder/cache state.
- `add_audio(mx.array)` accepts 1-D audio chunks.
- `.result` combines finalized and draft tokens into an `AlignedResult`.

Implementation rules:

1. Load one shared model per process, off the asyncio event loop. **A lock alone is insufficient for MLX:** `asyncio.to_thread()` can move consecutive calls across pool threads, causing `There is no Stream(gpu, N) in current thread`. Create one dedicated `ThreadPoolExecutor(max_workers=1)` and run model load, `add_audio`, finalize, reset, and close on that same thread for the lifetime of the model.
2. Create a separate `StreamingParakeet` instance per authorized speaker/voice session; never share decoder/cache state across speakers. The shared model may be reused only through the dedicated serial executor.
3. Capture Discord PCM continuously, but aggregate it before `add_audio()`. With `parakeet-mlx 0.5.2`, 200 ms inference calls materially damaged German recognition in a real probe (`Hello, hörst du mich?` became nonsense), while **500 ms inference chunks** preserved the exact transcript. Keep the receiver polling at ~200 ms if desired, but buffer to at least 500 ms before decoding.
4. Convert Discord 48 kHz stereo s16 PCM to normalized 16 kHz mono float audio and perform decoding in a worker thread/queue so socket callbacks and the asyncio loop stay responsive. Preserve the original full PCM buffer independently for fallback.
5. The streaming context manager mutates the shared model's attention implementation on enter/exit. For concurrent per-speaker streams, set the same local-attention mode once on the shared model and do not let one stream's `__exit__` revert attention while another remains active. A `(256, 256)` context with depth 1 preserved quality in the measured setup.
6. Treat finalized tokens as stable; draft tokens may change. Do not append draft transcripts to normal conversation history.
7. Commit the final utterance after the configured endpoint silence (roughly 400–450 ms), flush any sub-500-ms tail through the decoder, then reset only that speaker's stream.
8. Tear down stream state on disconnect, speaker timeout, malformed audio, or guild reset. Preserve the existing WAV/file STT path when optional MLX imports, model loading, stream decoding, or the final transcript fail.
9. Log streaming availability/fallback reason and capture-end → transcript-ready timing, but never raw PCM or transcript text merely for latency instrumentation.

Measured reuse result on the same M1 Max/64 GB setup: one-time warmup about **1.65 s**; a 1.67-second synthetic German phrase decoded in **0.74 s** on the first warm stream and **0.58 s** on the next, with the transcript exact. Most compute overlaps the user's speech, so post-speech cost is primarily endpoint silence plus tail flush. Keep these as local benchmark evidence, not universal promises.

A bilingual command wrapper can hide a second latency trap: if low-confidence output causes separate German and English Whisper passes and each pass constructs a new `WhisperModel`, the fallback may load the model twice per utterance. Keep any fallback model warm, make correction asynchronous where safe, or skip expensive correction on the live conversational path and preserve it for offline voice memos.

## Design cautions

- Do not send every unstable partial transcript into normal conversation history; it can create contradictory turns and damage prompt-cache stability.
- Do not let a lightweight front model execute complex work merely to save latency. It should converse, classify, acknowledge, and delegate.
- Do not promise sub-second latency until first-audio timing has been measured end-to-end.
- A fixed greeting should be a local fast path, not an LLM turn. Configure a small varied phrase pool (for example `Hello`, `Hello`, `Willkommen zurück`) so repeated joins feel natural.
- Restarting while the user is already in the channel does not trigger a join greeting; test by leaving and re-entering.

## Verification targets

Record at least ten turns in each class:

- direct greeting
- short conversational question
- task handoff with immediate acknowledgement
- interruption during TTS

Report p50 and p95 for end-of-speech → transcript and end-of-speech → first audible output. Confirm that barge-in preserves the newly spoken utterance and that background tasks do not block the voice host.