# Discord voice routing runtime notes

Session-derived implementation notes for Hermes voice routing.

## What to preserve

- Keep the normal Hermes turn path for substantive voice requests so native tools and `delegate_task` remain available.
- If you need a lightweight live router, use it only for transport-edge acknowledgements and metadata; do not replace the main agent path with a dedicated fast-path that bypasses Hermes.
- Apply speaker/persona selection before TTS synthesis, not after playback.
- Runtime voice persona overrides should be applied to a cloned TTS config, never by mutating loaded config in place.
- Pass provider/voice/model/speed overrides through the TTS call boundary so the transport can vary voice per speaker without persisting config changes.
- Discord playback should be serialized per guild. A single guild can have overlapping TTS requests; guard playback with a guild-scoped lock.
- Emit lifecycle events around playback, including both start and completion, so the transport can track clips cleanly.

## Testing pattern

Use tests that verify:

1. The original TTS config object is unchanged after applying runtime overrides.
2. A speaker persona resolves into provider/voice/model/speed overrides before synthesis.
3. Two simultaneous guild clips do not overlap and lifecycle events fire in order.
4. The transport still preserves short spoken acknowledgements while the real task is handled by Hermes.

## Common pitfall

Do not encode speaker voice/persona data into the persistent TTS config file just to make routing work. That leaks transport-specific state into global configuration and makes later voice changes sticky across unrelated sessions.
