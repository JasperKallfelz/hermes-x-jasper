# Voice acknowledgement pattern

Use this when Hermes is speaking in Discord/Telegram voice or doing a live call-style turn.

## Rule

The first spoken acknowledgement should name the concrete action, not the intention.

Avoid:
- "Ich kümmere mich darum"
- "Alles klar, ich mach das"
- "Bin dran"

Prefer:
- "Ich erstelle den Termin."
- "Ich lese deine Mails."
- "Ich sende die E-Mail."
- "Ich recherchiere das."
- "Ich prüfe das im Browser."
- "Ich setze das im Hintergrund um."
- "Ich starte einen Hintergrund-Agenten."

## English equivalents

- "I’m creating the calendar event."
- "I’m reading your emails."
- "I’m sending the email."
- "I’m researching that."
- "I’m checking that in the browser."
- "I’m handling that in the background."
- "I’m starting a background agent."

## When to use

- Live Discord voice calls
- Telegram voice replies
- Any turn where the user asked Hermes to take care of something
- Fast audio acks before a long worker turn

## Notes

- Keep IDs, URLs, and status detail in text, not speech.
- If the action is ambiguous, pick the most concrete user-visible verb that matches the tool call.
- The ack should sound like work has started, not like a promise.
