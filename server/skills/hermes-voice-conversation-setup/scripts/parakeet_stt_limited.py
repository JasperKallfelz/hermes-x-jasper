#!/usr/bin/env python3
"""Hermes STT command-provider wrapper: Parakeet MLX with an allowlist language guard.

Usage from stt.providers.<name>.command:
  python scripts/parakeet_stt_limited.py {input_path} --output-path {output_path} --model {model}

Default allowlist is English/German/Spanish. Very short transcripts are allowed because
language detection is too ambiguous for one-word memos such as "ok" or "ja".
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

DEFAULT_ALLOWED = {"en", "de", "es"}
MIN_DETECT_CHARS = 18


def detect_language(text: str) -> str | None:
    clean = " ".join(text.strip().split())
    if len(clean) < MIN_DETECT_CHARS:
        return None
    try:
        import langid  # type: ignore
        lang, _score = langid.classify(clean)
        return lang
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path")
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--model", default="mlx-community/parakeet-tdt-0.6b-v3")
    parser.add_argument("--parakeet-bin", default="parakeet-mlx")
    parser.add_argument("--allowed", default="en,de,es", help="Comma-separated ISO language allowlist")
    parser.add_argument("--blocked-message", default="[Voice memo ignored: detected language '{lang}'. Allowed languages: {allowed}.]")
    args = parser.parse_args()

    allowed = {part.strip().lower() for part in args.allowed.split(",") if part.strip()}
    if not allowed:
        allowed = set(DEFAULT_ALLOWED)

    input_path = Path(args.input_path).expanduser().resolve()
    output_path = Path(args.output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hermes-parakeet-") as tmp:
        subprocess.run(
            [
                args.parakeet_bin,
                str(input_path),
                "--model", args.model,
                "--output-format", "txt",
                "--output-dir", tmp,
                "--output-template", "transcript",
                "--chunk-duration", "120",
            ],
            check=True,
        )
        transcript = (Path(tmp) / "transcript.txt").read_text(encoding="utf-8").strip()

    lang = detect_language(transcript)
    if lang is not None and lang not in allowed:
        output_path.write_text(
            args.blocked_message.format(lang=lang, allowed=", ".join(sorted(allowed))),
            encoding="utf-8",
        )
        return 0

    output_path.write_text(transcript, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
