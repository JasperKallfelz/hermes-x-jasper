#!/usr/bin/env python3
"""JARVIS-inspired TTS command provider for Hermes.

Generates speech with Microsoft Edge's British Ryan voice, then applies a subtle
AI/helmet-assistant post-processing chain. This is intentionally a style match,
not a clone of any actor's voice.

Usage: jarvis_style_tts.py INPUT_TEXT_FILE OUTPUT_AUDIO_FILE
"""
import asyncio
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import edge_tts

VOICE = os.getenv("HERMES_TTS_VOICE", "en-GB-RyanNeural")
RATE = os.getenv("HERMES_TTS_RATE", "-4%")
PITCH = os.getenv("HERMES_TTS_PITCH", "-5Hz")


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(f"{cmd[0]} failed (exit {proc.returncode}):\n{stderr}")


async def synth(text: str, tmp_mp3: str) -> None:
    communicate = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH)
    await communicate.save(tmp_mp3)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: jarvis_style_tts.py INPUT_TEXT_FILE OUTPUT_AUDIO_FILE", file=sys.stderr)
        return 2

    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found on PATH (brew install ffmpeg / apt install ffmpeg)", file=sys.stderr)
        return 3

    input_path = pathlib.Path(sys.argv[1])
    output_path = pathlib.Path(sys.argv[2])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    text = input_path.read_text(encoding="utf-8").strip()
    if not text:
        text = "Systems online."

    with tempfile.TemporaryDirectory() as td:
        raw = str(pathlib.Path(td) / "raw.mp3")
        wav1 = str(pathlib.Path(td) / "raw.wav")
        mp3_out = str(pathlib.Path(td) / "jarvis.mp3")
        asyncio.run(synth(text, raw))

        # Convert to WAV, then apply a restrained JARVIS-like chain:
        # - highpass/lowpass: cleaner comms band
        # - compand: tighter broadcast dynamics
        # - crystalizer + aecho: polished AI-room presence
        # - slight chorus: synthetic width without sounding like a robot toy
        run(["ffmpeg", "-y", "-i", raw, "-ar", "48000", "-ac", "1", wav1])
        af = (
            "highpass=f=90,lowpass=f=7600,"
            "compand=attacks=0.03:decays=0.25:points=-80/-80|-35/-28|-12/-9|0/-3,"
            "crystalizer=i=0.25,"
            "aecho=0.8:0.18:42:0.18,"
            "chorus=0.45:0.55:45:0.18:0.25:2"
        )
        run(["ffmpeg", "-y", "-i", wav1, "-af", af, "-codec:a", "libmp3lame", "-b:a", "128k", mp3_out])
        # shutil.move, not Path.replace: the temp dir and the output path often
        # live on different filesystems (tmpfs), which os.rename cannot cross.
        shutil.move(mp3_out, str(output_path))

    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
