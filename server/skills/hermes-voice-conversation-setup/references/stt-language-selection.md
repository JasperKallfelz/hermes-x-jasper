# STT language selection / wrong-language detection

Use when Hermes transcribes an English voice memo as Russian or another unintended language.

## Key lesson

Check the active STT provider before changing language settings. `stt.local.language` only affects the built-in local Whisper path. It does **not** affect a named command provider such as `parakeet`.

## Fast fix for English voice notes

```bash
~/.hermes/hermes-agent/venv/bin/hermes config set stt.provider local
~/.hermes/hermes-agent/venv/bin/hermes config set stt.local.language en
```

For German:

```bash
~/.hermes/hermes-agent/venv/bin/hermes config set stt.provider local
~/.hermes/hermes-agent/venv/bin/hermes config set stt.local.language de
```

For auto-detect again:

```bash
~/.hermes/hermes-agent/venv/bin/hermes config set stt.local.language ''
```

## Parakeet caveat

If `stt.provider: parakeet`, language hints must be supported by the `parakeet-mlx` command or the wrapper script itself. Setting `stt.local.language=en` while `stt.provider=parakeet` gives a false sense of success because the effective provider did not change.

## Verification

After the config change, read the effective config or run a real transcription smoke test. At minimum verify:

```text
stt.provider: local
stt.local.language: en
```

Then send/produce a short English voice memo and confirm the transcript is English before telling the user it is fixed.
