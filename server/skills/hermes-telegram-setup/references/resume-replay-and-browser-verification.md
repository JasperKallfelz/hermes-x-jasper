# `/resume` replay and browser verification notes

Session-derived notes for Telegram-based `/resume` flows where the user wants the bot interaction to be validated in Chrome.

## Key behavior observed

- The `/resume` parser accepts a session id plus replay flag in the form:
  - `/resume <session-id> --replay=1`
- If the session is already active, Hermes responds with an informational message like:
  - `📌 Already on session <session-id>.`
  - followed by `— Recent conversation —`
- A malformed command can be interpreted as part of the session id if whitespace/arguments are not normalized correctly, which yields a misleading `No session found matching ...` response.

## Safe verification pattern in Chrome

1. Open the target Telegram web session in Chrome.
2. Send the `/resume ... --replay=N` command.
3. Read the last part of the chat transcript from the page DOM after a short wait.
4. Confirm the response contains the expected conversation summary and not only the transport-level success.

## Troubleshooting hints

- If the bot says no session found for a string that visually contains both session id and flags, inspect the parser normalization first.
- For end-to-end verification, prefer the visible Telegram chat response over gateway state alone.
- When a profile-specific gateway was recently repaired or restarted, verify the active profile before concluding the `/resume` path is broken.
