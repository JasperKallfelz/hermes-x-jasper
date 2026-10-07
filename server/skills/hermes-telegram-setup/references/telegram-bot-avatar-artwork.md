# Telegram bot avatars for Hermes

Session-derived notes for bot profile photos.

## Preferred visual rule

When the user asks for Hermes/Telegram bot avatars that match the Hermes-Agent website, default to **crop-only official website artwork** first.

- Use the official site assets as the source of truth.
- Preserve the website's original ultramarine/white halftone/Xerox look.
- Do **not** add extra icons, overlays, rings, labels, or modern app-icon frames unless the user explicitly asks.
- Keep the main motif inside the central ~80% so the circular Telegram crop stays readable.
- Labels belong only on contact sheets or review sheets, never on the avatar itself.

## Proven source assets

The following official website assets were used successfully as crop sources:

- `hero-art.webp` → General / core Hermes branding crop
- `badge.webp` → Legacy / pure badge crop
- `feature-connect.webp` → OpenACP / communication crop
- `feature-memory.webp` → Opus / cognition crop
- `feature-automation.webp` → Core crop
- `feature-tasks.webp` → Mail crop
- `feature-browse.webp` → Sentinel crop
- `feature-sandbox.webp` → Voron crop

## Operational checks

1. Produce the avatar as a square PNG.
2. Upload it to BotFather with Telethon via the user session.
3. Verify BotFather replied with success.
4. Independently verify the side effect by resolving the bot username, fetching its newest Telegram profile photo, downloading the 640×640 copy, and comparing it to the source image after resizing.
5. If the user wants a clean set, build a contact sheet for review, but keep the avatars themselves free of text.

## Common pitfall

BotFather's bot selector is a regular reply keyboard, not an inline callback keyboard. Telethon button `.data` can be `None`; don't click by `data`.
