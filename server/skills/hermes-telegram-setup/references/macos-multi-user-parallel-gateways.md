# Parallel Hermes Gateways on One Mac

Session-derived note: a shared Mac can run Hermes for multiple macOS users at the same time, with each user owning a separate `~/.hermes` tree, separate Telegram bot token, separate gateway service, and separate memory/session stores.

## Practical rules

- Run Hermes under each macOS user account, not by sharing one Hermes home.
- Each user should configure a distinct `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USERS`.
- Launchd labels and profile names should be unique per user/profile.
- If both users expose dashboards locally, move one to a different port to avoid `EADDRINUSE`.
- A user-scoped launch agent only runs while that macOS user is logged in; for 24/7 operation, use a user-scoped LaunchDaemon or keep the account logged in.
- Do not point both users at the same `.env` or bot token: Telegram will accept only one live gateway per token, and the second connection can conflict.

## Verification checklist

1. `launchctl list` shows a separate gateway label for each profile/user.
2. Each profile's `gateway_state` reports `running` and `connected`.
3. Messages sent to one bot never appear in the other user's bot/session.
4. Dashboard ports do not overlap.
