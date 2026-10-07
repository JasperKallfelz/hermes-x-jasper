# Voice command routing notes

Session learning: spoken control phrases must be handled as explicit transport commands before normal model routing.

## What to route immediately

- "new chat"
- "new session"
- localized variants like "neuer Chat"

These should reset the voice/session context directly and should not wait for a model turn first.

## Spoken ack pattern

When the user says they will "take care of it", answer with one concrete action phrase, e.g.:

- Calendar: "Ich erstelle den Termin."
- Mail: "Ich lese deine Mails." / "Ich sende die E-Mail."
- Research: "Ich recherchiere das."
- Browser check: "Ich prüfe das im Browser."
- Background work: "Ich setze das im Hintergrund um."
- Worker handoff: "Ich starte einen Hintergrund-Agenten."

## Worker routing

For substantive background work, prefer the native `delegate_task` path over inventing a second CLI worker layer. Keep the voice front-end short and immediate; the heavy work should happen behind the scenes and report back in text/status updates.
