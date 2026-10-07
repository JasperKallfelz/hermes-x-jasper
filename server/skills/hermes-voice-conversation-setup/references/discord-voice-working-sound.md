# Discord voice working sound

Use this pattern when the user wants audible feedback while Hermes is reasoning or using tools, similar to Perplexity's work sound. The goal is a subtle confidence signal—not permanent music in the voice channel.

## UX contract

- Keep the mixer silent while idle.
- Start a quiet looping bed when a Discord **voice-originated** agent turn begins.
- Keep it active through reasoning and tool execution.
- Duck it under verbal acknowledgements or TTS.
- Stop it in a `finally` block when the agent turn ends, including failures and cancellation.
- Do not enable it for ordinary Discord text turns.
- Use a short fade-in and conservative gain; let the user tune loudness after a live audition.

A useful synthesized default is a low, slowly pulsing pad: two slightly detuned sine partials, gentle tremolo, and very low filtered noise. A deterministic synthesized loop avoids shipping an asset and makes tests reproducible. Mixer gain around `0.10` and ducked gain around `0.025` is a restrained starting point, not a universal standard.

## Lifecycle pattern

1. Install one continuous `discord.AudioSource` mixer when joining voice, but do **not** attach the ambient bed yet. Log `ambient=on-demand` rather than claiming it is already audible.
2. Expose an adapter method such as `set_voice_working_sound(guild_id, active)` that attaches or clears the cached loop through the mixer's thread-safe `set_ambient()` method.
3. Reference-count active work per guild. Concurrent turns must not let the first completed turn silence another turn that is still running.
4. In the gateway, resolve the voice guild from the voice-originated event, start the sound immediately before `_run_agent()`, and stop it in the surrounding `finally` block.
5. On voice leave/disconnect, clear the guild count and ambient child. Keep speech playback independent so final TTS still works after the bed stops.

## Tests

- Starting twice installs the bed once and increments the count.
- Stopping once while another turn remains does not clear it.
- The final balanced stop calls `set_ambient(None)`.
- Disabled ambient or a missing mixer is a silent no-op.
- Agent exceptions still execute the stop path.
- Mixer remains silent when connected but idle.

## Live verification

After restart, verify the current profile log shows:

- `Voice mixer installed (... ambient=on-demand)`
- a `Voice working sound started` line during a real spoken task
- a matching `Voice working sound stopped` line before the final spoken reply

Then audition in the actual Discord channel. Unit tests prove lifecycle, but only a live listen proves the loop is subtle enough and does not mask speech.
