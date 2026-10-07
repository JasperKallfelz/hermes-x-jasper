# TTS voice selection and JARVIS-style requests

Session learning from a Hermes voice setup conversation.

## User-facing selection workflow

When the user asks for a better voice, do not pick only one and stop unless they explicitly asked to auto-choose. Generate or present a small audition set and let the user choose.

Good audition set for German female Edge TTS:

- `de-DE-SeraphinaMultilingualNeural` — modern, multilingual German female; this user explicitly chose the sample `seraphina-de.mp3`, so make it the default when they ask for the selected female voice.
- `de-DE-AmalaNeural` — softer/classic German female
- `de-DE-KatjaNeural` — clearer/neutral German female
- `de-AT-IngridNeural` — Austrian German female
- `de-CH-LeniNeural` — Swiss German female
- `en-US-AvaMultilingualNeural` / `en-US-EmmaMultilingualNeural` — multilingual female options that may sound natural but can carry accent

Generate short samples via Edge TTS using the Hermes venv, then return `MEDIA:` links and ask for the chosen name.

Example:

```bash
mkdir -p "$HOME/.hermes/audio_cache/voice-tests-female"
$HOME/.hermes/hermes-agent/venv/bin/python - <<'PY'
import asyncio, edge_tts, pathlib
text = "Hello, hier ist eine weibliche Stimme für Hermes. Sag mir einfach, welche natürlicher klingt — dann stelle ich sie fest ein."
voices = {
    'seraphina-de': 'de-DE-SeraphinaMultilingualNeural',
    'amala-de': 'de-DE-AmalaNeural',
    'katja-de': 'de-DE-KatjaNeural',
    'ingrid-at': 'de-AT-IngridNeural',
    'leni-ch': 'de-CH-LeniNeural',
    'ava-multilingual': 'en-US-AvaMultilingualNeural',
    'emma-multilingual': 'en-US-EmmaMultilingualNeural',
}
outdir = pathlib.Path.home()/'.hermes/audio_cache/voice-tests-female'
async def make(name, voice):
    path = outdir/f'{name}.mp3'
    await edge_tts.Communicate(text, voice, rate='+4%', pitch='-1Hz').save(str(path))
    print(f'{name}: {voice}: {path}')
asyncio.run(asyncio.gather(*(make(n,v) for n,v in voices.items())))
PY
```

Note: if `asyncio.gather` outside a running loop causes issues in a given Python version, wrap it in `async def main()` and `asyncio.run(main())`.

Set selected Edge voice through Hermes config, not direct file patching:

```bash
~/.hermes/hermes-agent/venv/bin/hermes config set tts.edge.voice de-DE-SeraphinaMultilingualNeural
```

## JARVIS / Iron Man voice requests

The user may specifically say they want the real JARVIS voice and point out that 1:1 voices exist online in German and English. Do not flatly argue that it is impossible. Respond with the practical integration distinction:

- A demo web voice is not enough for Hermes live voice.
- Hermes needs a TTS provider usable from the runtime, ideally an API + stable voice/model ID.
- ElevenLabs is the best Hermes-native path because Hermes supports `tts.provider: elevenlabs` and `tts.elevenlabs.voice_id`.
- Other community voice sites (e.g. FakeYou, Jammable/Voicify/Kits-like services) may have JARVIS-style voices, but only integrate cleanly if they expose an API or a downloadable/local model that can be wrapped as a custom command provider.

Safe wording:

> Ja, es gibt online JARVIS-/Iron-Man-Voice-Modelle. Für Hermes brauche ich aber eine nutzbare TTS-Quelle mit API oder Voice-ID, nicht nur eine Demo-Webseite. Schick mir den Link; wenn es ElevenLabs oder eine API-fähige Quelle ist, stelle ich sie ein.

ElevenLabs setup shape:

```bash
hermes config set tts.provider elevenlabs
hermes config set tts.elevenlabs.voice_id <VOICE_ID>
# ELEVENLABS_API_KEY must be in ~/.hermes/.env or otherwise available to Hermes.
```

If the user provides a link, inspect whether it exposes a voice ID/API. If not, ask for either an ElevenLabs voice-library link, an API key/provider, or permission to use a custom command provider wrapper.

## JARVIS-inspired custom command provider fallback

When the user wants a JARVIS/Iron-Man style voice for a private project and no API-ready community voice is available, build an integration-safe **inspired style** rather than an exact actor/film clone:

1. Use a British, calm Edge TTS voice as the base (`en-GB-RyanNeural` worked well for the JARVIS vibe; `en-GB-ThomasNeural` is a deeper alternative).
2. Generate audio with `edge_tts` at slightly slower/deeper settings (`rate='-4%'`, `pitch='-5Hz'`).
3. Post-process with `ffmpeg`: comms-band EQ, compand, light crystalizer, small echo, restrained chorus.
4. Register it as a Hermes custom command TTS provider with `voice_compatible: true` so Telegram can deliver a voice bubble.

Reusable script: `scripts/jarvis_style_tts.py`.

Install/copy the script into a runtime path, then configure:

```bash
chmod +x ~/.hermes/scripts/jarvis_style_tts.py
HERMES="$HOME/.hermes/hermes-agent/venv/bin/hermes"
$HERMES config set tts.providers.jarvis.type command
$HERMES config set 'tts.providers.jarvis.command' '$HOME/.hermes/hermes-agent/venv/bin/python $HOME/.hermes/scripts/jarvis_style_tts.py {input_path} {output_path}'
$HERMES config set tts.providers.jarvis.output_format mp3
$HERMES config set tts.providers.jarvis.timeout 180
$HERMES config set tts.providers.jarvis.voice_compatible true
$HERMES config set tts.provider jarvis
```

Verify before declaring success:

```bash
printf 'Good evening. Systems are online.' > /tmp/jarvis_test.txt
~/.hermes/hermes-agent/venv/bin/python ~/.hermes/scripts/jarvis_style_tts.py /tmp/jarvis_test.txt ~/.hermes/audio_cache/jarvis_style_test.mp3
file ~/.hermes/audio_cache/jarvis_style_test.mp3
```

Then use `text_to_speech` once to confirm Hermes dispatches through provider `jarvis` and, ideally, returns an audio-as-voice media tag.

## Pitfalls

- Do not claim an exact celebrity/film voice can be cloned locally by default. Distinguish what exists online from what can be integrated.
- Do not stop after setting one voice if the user asked for "mehr Auswahl". Produce a sample set.
- Do not use direct patching on `~/.hermes/config.yaml` for protected Hermes config; use `hermes config set ...`.
- Avoid Google/duckduckgo CAPTCHA rabbit holes for finding public voice pages. If search blocks, state that a concrete link from the user is the fastest path, or search provider-specific pages directly.
