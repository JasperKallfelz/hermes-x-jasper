#!/usr/bin/env python3
"""Hermes STT wrapper: bilingual German/English transcription guard.

Primary: Parakeet MLX (fast local multilingual ASR).
Fallback: faster-whisper with explicit English/German when the transcript is
classified as an unwanted language (e.g. Russian from English speech).
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

ALLOWED = {"en", "de"}
DEFAULT_LANGUAGE = "de"
MIN_DETECT_CHARS = 18  # very short memos like "ok" are too ambiguous to reject safely

GERMAN_MARKERS = {
    "ich", "du", "wir", "der", "die", "das", "ein", "eine", "einen", "und",
    "oder", "nicht", "mit", "mir", "mich", "dir", "dich", "bitte", "danke",
    "bau", "baue", "mach", "mache", "schick", "schicke", "erklär", "erkläre",
    "verstehe", "gerade", "aufgabe", "deutsch", "englisch",
}
ENGLISH_MARKERS = {
    "i", "you", "we", "the", "a", "an", "and", "or", "not", "with", "me",
    "my", "your", "please", "thanks", "build", "make", "create", "send",
    "explain", "understand", "task", "english", "german",
}


def _clean_words(text: str) -> list[str]:
    import re
    return re.findall(r"[a-zäöüß']+", text.lower())


def foreign_language(text: str) -> str | None:
    """Detect clearly-unwanted languages that the de/en scoring cannot see.

    Bug history: Cyrillic Parakeet output scored 0:0 in language_scores
    (the word regex only matches Latin letters), so margin was 0, lang was
    None, and the Russian transcript passed straight through as 'ambiguous
    but allowed'. Catch non-Latin scripts and confident langid foreign
    classifications explicitly before the ambiguity path.
    """
    import re
    stripped = re.sub(r"\s", "", text)
    if not stripped:
        return None
    cyrillic = len(re.findall(r"[\u0400-\u04FF]", stripped))
    if cyrillic / len(stripped) > 0.15:
        return "ru"
    # Other non-Latin scripts (CJK, Arabic, Greek, ...) are equally unwanted.
    non_latin = len(re.findall(r"[^\x00-\x7FäöüÄÖÜßéèêàâçñ«»„“”‚’…–—°€]", stripped))
    if non_latin / len(stripped) > 0.4:
        return "other"
    if len(text.strip()) >= MIN_DETECT_CHARS:
        try:
            import langid  # type: ignore
            lang, score = langid.classify(" ".join(text.strip().split()))
            # langid scores are log-probs; strongly negative = confident.
            if lang not in ALLOWED and score < -50:
                return lang
        except Exception:
            pass
    return None


def language_scores(text: str) -> tuple[str | None, float, dict[str, float]]:
    """Return (best_lang, confidence_margin, raw_scores) for German/English.

    `langid` alone is brittle on short conversational fragments, so combine it
    with simple function-word markers. Positive margin means the best language is
    more plausible than the other. Ambiguous/short speech falls back to German.
    """
    clean = " ".join(text.strip().split())
    if len(clean) < MIN_DETECT_CHARS:
        return None, 0.0, {"de": 0.0, "en": 0.0}

    scores = {"de": 0.0, "en": 0.0}
    words = _clean_words(clean)
    if words:
        scores["de"] += sum(1.0 for w in words if w in GERMAN_MARKERS)
        scores["en"] += sum(1.0 for w in words if w in ENGLISH_MARKERS)
    if any(ch in clean.lower() for ch in "äöüß"):
        scores["de"] += 2.0

    try:
        import langid  # type: ignore
        lang, _score = langid.classify(clean)
        if lang in scores:
            scores[lang] += 2.0
    except Exception:
        pass

    best = "de" if scores["de"] >= scores["en"] else "en"
    margin = abs(scores["de"] - scores["en"])
    return best, margin, scores


def detect_language(text: str) -> str | None:
    lang, margin, _scores = language_scores(text)
    return lang if margin >= 1.0 else None


def transcribe_with_whisper(input_path: Path, language: str, model_size: str = "small") -> str:
    """Fallback transcription with fixed language to avoid bad auto-detect."""
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(input_path), language=language, beam_size=5)
    return " ".join(seg.text.strip() for seg in segments).strip()


def fallback_bilingual(input_path: Path) -> str | None:
    """Try explicit German and English; pick the more plausible output.

    Default is German on ambiguity, matching the operator's preference.  This is used
    when Parakeet produces an unwanted/uncertain language, not on every turn, so
    the live path stays fast.
    """
    candidates: list[tuple[float, str, str | None, str]] = []
    for lang in ("de", "en"):
        try:
            text = transcribe_with_whisper(input_path, lang)
        except Exception:
            continue
        detected, margin, raw_scores = language_scores(text)
        score = raw_scores.get(lang, 0.0) - raw_scores.get("en" if lang == "de" else "de", 0.0)
        if detected == lang:
            score += 2.0
        if lang == DEFAULT_LANGUAGE:
            score += 0.35
        # Prefer outputs with real words over empty/hallucinated fragments.
        score += min(len(_clean_words(text)), 12) * 0.05
        candidates.append((score + margin * 0.1, lang, detected, text))

    candidates.sort(key=lambda item: item[0], reverse=True)
    for _score, lang, detected, text in candidates:
        if text and (detected in ALLOWED or detected is None or lang == DEFAULT_LANGUAGE):
            return text
    return candidates[0][3] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path")
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--model", default="mlx-community/parakeet-tdt-0.6b-v3")
    parser.add_argument("--parakeet-bin", default="parakeet-mlx")
    args = parser.parse_args()

    input_path = Path(args.input_path).expanduser().resolve()
    output_path = Path(args.output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hermes-parakeet-") as tmp:
        cmd = [
            args.parakeet_bin,
            str(input_path),
            "--model", args.model,
            "--output-format", "txt",
            "--output-dir", tmp,
            "--output-template", "transcript",
            "--chunk-duration", "120",
        ]
        # parakeet-mlx prints status text like
        # "transcription complete. Outputs saved in ..." to stdout. Hermes'
        # command STT runner falls back to stdout when the output file is empty,
        # so suppress child stdout/stderr to avoid treating status text as speech
        # during Discord silence/background-noise segments.
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        transcript_file = Path(tmp) / "transcript.txt"
        if not transcript_file.exists():
            txt_files = sorted(Path(tmp).glob("*.txt"))
            if not txt_files:
                output_path.write_text("", encoding="utf-8")
                return 0
            transcript_file = txt_files[0]
        transcript = transcript_file.read_text(encoding="utf-8").strip()

    lang, margin, _scores = language_scores(transcript)

    # Hard gate first: Cyrillic/CJK/etc. output previously slipped through the
    # ambiguity path because the de/en scorer is blind to non-Latin scripts.
    foreign = foreign_language(transcript)
    if foreign is not None:
        fallback = fallback_bilingual(input_path)
        if fallback and foreign_language(fallback) is None:
            output_path.write_text(fallback, encoding="utf-8")
            return 0
        output_path.write_text(
            f"[Voice memo ignored: detected language '{foreign}'. Allowed languages: English, German.]",
            encoding="utf-8",
        )
        return 0

    if lang is None:
        # Ambiguous but allowed: keep the fast Parakeet transcript. The agent's
        # default language is German, so short/unclear turns naturally stay DE.
        output_path.write_text(transcript, encoding="utf-8")
        return 0

    if lang not in ALLOWED:
        fallback = fallback_bilingual(input_path)
        if fallback:
            output_path.write_text(fallback, encoding="utf-8")
            return 0
        output_path.write_text(
            f"[Voice memo ignored: detected language '{lang}'. Allowed languages: English, German.]",
            encoding="utf-8",
        )
        return 0

    # If Parakeet's language signal is weak, ask the bilingual fallback to break
    # the tie. This improves German-vs-English selection without paying the
    # Whisper cost for clearly detected turns.
    if margin < 2.0:
        fallback = fallback_bilingual(input_path)
        if fallback:
            output_path.write_text(fallback, encoding="utf-8")
            return 0

    output_path.write_text(transcript, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
