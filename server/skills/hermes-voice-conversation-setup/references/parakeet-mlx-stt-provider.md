# Parakeet MLX STT command-provider notes

Use when a user wants Hermes Telegram voice memos to use Nvidia Parakeet instead of Whisper/local faster-whisper.

## Package

For Apple Silicon, `parakeet-mlx` provides a CLI around Nvidia Parakeet models via MLX:

```bash
cd ~/.hermes/hermes-agent
. venv/bin/activate
python -m pip install -U parakeet-mlx langid
```

`ffmpeg` must be installed for common Telegram audio formats.

## Model

Default Parakeet MLX model:

```text
mlx-community/parakeet-tdt-0.6b-v3
```

Quick direct CLI smoke test:

```bash
parakeet-mlx /tmp/hermes-stt-test.ogg \
  --model mlx-community/parakeet-tdt-0.6b-v3 \
  --output-format txt \
  --output-dir /tmp/parakeet-test \
  --output-template test \
  --chunk-duration 120
cat /tmp/parakeet-test/test.txt
```

## Hermes command-provider config

Hermes STT supports user-declared command providers under `stt.providers.<name>`. The command must write text to `{output_path}` or stdout.

Minimal Parakeet provider:

```bash
CMD="$HOME/.hermes/hermes-agent/venv/bin/parakeet-mlx {input_path} --model {model} --output-format txt --output-dir {output_dir} --output-template transcript --chunk-duration 120"
python -m hermes_cli.main config set stt.provider parakeet
python -m hermes_cli.main config set stt.providers.parakeet.type command
python -m hermes_cli.main config set stt.providers.parakeet.model mlx-community/parakeet-tdt-0.6b-v3
python -m hermes_cli.main config set stt.providers.parakeet.format txt
python -m hermes_cli.main config set stt.providers.parakeet.timeout 600
python -m hermes_cli.main config set stt.providers.parakeet.command "$CMD"
```

For profile-specific gateways, repeat with `HERMES_HOME=~/.hermes/profiles/<profile>` or `--profile <profile>` consistently.

## Language allowlist wrapper

If the user wants only specific languages, use the skill script `scripts/parakeet_stt_limited.py` (copy it to a stable path such as `~/.hermes/scripts/` or invoke it directly from the skill path if appropriate). It runs Parakeet, detects transcript language with `langid`, and blocks languages outside the allowlist. It intentionally allows very short transcripts because language detection is unreliable for one-word voice memos.

Example for English/German/Spanish only:

```bash
python -m pip install -U langid
CMD="$HOME/.hermes/hermes-agent/venv/bin/python $HOME/.hermes/scripts/parakeet_stt_limited.py {input_path} --output-path {output_path} --model {model} --parakeet-bin $HOME/.hermes/hermes-agent/venv/bin/parakeet-mlx --allowed en,de,es"
python -m hermes_cli.main config set stt.provider parakeet
python -m hermes_cli.main config set stt.providers.parakeet.type command
python -m hermes_cli.main config set stt.providers.parakeet.model mlx-community/parakeet-tdt-0.6b-v3
python -m hermes_cli.main config set stt.providers.parakeet.timeout 600
python -m hermes_cli.main config set stt.providers.parakeet.command "$CMD"
```

Verify via `tools.transcription_tools.transcribe_audio()` and check the returned `provider` is `parakeet`.
