# Multi-Session Project Spawning — Pattern C (Userbot Helper)

The full pattern for spawning real separate Telegram groups on demand. Use this when Pattern A (topics in supergroup) or Pattern B (DM topic lanes) doesn't match the user's desired UX — typically when the user explicitly says "I want a new group per project" or "I want 10–20 separate chats in my chat list."

See the SKILL.md "Multi-Session / Per-Project Chat Spawning" section for the high-level pattern comparison.

## Why this is needed

Telegram's Bot API does not expose `createGroup` or `createSupergroup`. Bots cannot create new chats. Period. The only "create" methods the API gives bots are `createChatInviteLink` (link for a chat that already exists) and `createForumTopic` (topic inside a group that already exists). So if you want a real new group, the only path is a **telethon user-client** that uses the user's own Telegram account.

## Architecture

```
┌────────────────────┐    /new <name>     ┌────────────────────┐
│  User's main DM    │ ─────────────────► │  Hermes bot        │
│  with the bot      │                    │  (gateway)         │
└────────────────────┘                    └─────────┬──────────┘
                                                    │ shells out
                                                    ▼
                                         ┌────────────────────┐
                                         │ tg_userbot.py      │
                                         │ (telethon)         │
                                         └─────────┬──────────┘
                                                   │ creates real group
                                                   │ adds bot
                                                   │ promotes bot to admin
                                                   │ posts welcome msg
                                                   ▼
                                         ┌────────────────────┐
                                         │ New project group  │
                                         │ (sidebar entry)    │
                                         └────────────────────┘
```

The bot's `terminal` tool invokes the helper. The helper returns JSON with the new group's `chat_id` and `invite_link`. The bot posts the link back in the main DM; the user clicks, switches to the new group, and starts talking. Each new group is auto-routed to its own isolated Hermes session because the gateway already keys sessions by `(platform, chat_id, thread_id)`.

## One-time setup

### 1. Get Telegram user API credentials

The user must do this themselves — it requires logging into my.telegram.org with their phone.

- Go to https://my.telegram.org
- Log in with the phone number of the Telegram account that will own the spawned groups
- Click "API development tools"
- Click "Create new application"
- Fill in any app name + short name (e.g. "Hermes Spawner" / "hermes_spawner")
- You'll get back an `api_id` (a number, looks like `12345678`) and an `api_hash` (a 32-char string, looks like `a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6`)

### 2. Add credentials to `~/.hermes/.env`

Three new env vars (alongside the existing `TELEGRAM_*` ones):

```bash
TG_USER_API_ID=                              # from step 1
TG_USER_API_HASH=    # from step 1
TELEGRAM_BOT_USERNAME=yourbot_bot                    # bot's @username WITHOUT the @
```

For `TELEGRAM_BOT_USERNAME`, look up the bot's username via the Bot API:
```bash
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" | jq -r .result.username
```

### 3. One-time phone authentication

Run the helper once interactively. It will prompt for the phone number and the login code Telegram sends:

```bash
source ~/.hermes/hermes-agent/venv/bin/activate
pip install telethon   # one-time
python ~/.hermes/scripts/tg_userbot.py whoami
# → Phone number (international format): +1**********
# → Login code from Telegram: ******
# → { "id": ..., "username": ..., ... }
```

After this succeeds, a session file is persisted at `~/.hermes/tg_userbot.session` and **all future invocations reuse it** — no more prompts, fully headless.

## The helper script

`~/.hermes/scripts/tg_userbot.py` — full telethon implementation. Subcommands:

- `whoami` — print the logged-in user (sanity check after auth)
- `list-sessions` — list groups the user is currently in
- `create-group <name> [--welcome "msg"] [--about "desc"]` — the workhorse

`create-group` does, in order:
1. `CreateChannelRequest(megagroup=True, title=<name>)` — creates the supergroup
2. `GetFullChannelRequest` to get any auto-exported invite link
3. Fallback: `ExportChatInviteRequest` for a private invite link
4. `InviteToChannelRequest` to add the bot
5. **`EditAdminRequest` to promote the bot to admin** with `post_messages`, `delete_messages`, `pin_messages`, `invite_users` — this is the step that makes the bot see every message in the group. Without it, the bot is deaf in groups.
6. `send_message` to post a welcome message (defaults to a templated "this is an isolated Hermes session" message; override with `--welcome`)
7. Prints JSON to stdout: `{ ok, group_id, group_title, group_link, welcome_message_id, created_by }`

The script:
- Reads credentials from `~/.hermes/.env` automatically (defensive — works in fresh shells/cron contexts)
- Uses `SESSION_PATH = ~/.hermes/tg_userbot.session` for the persistent telethon session
- Returns non-zero exit codes on failure (so the bot's `terminal()` tool can detect errors)
- Falls back to a generic Telegram error message if any step fails (logs the real reason to stderr; the bot sees the JSON stdout)

## Wiring it to a slash command (optional)

The bot can shell out to the helper from any slash handler. Example glue, ~30 lines, that you'd add to `gateway/slash_commands.py` (or run as a one-shot script the bot invokes):

```python
# Inside the slash command handler, e.g. /new <name>
import subprocess, json
name = args.strip()
if not name:
    return "Usage: /new <project name>"

result = subprocess.run(
    ["python", "~/.hermes/scripts/tg_userbot.py", "create-group", name],
    capture_output=True, text=True, timeout=60,
)
if result.returncode != 0:
    return f"❌ Failed to spawn group: {result.stderr}"

payload = json.loads(result.stdout)
return (
    f"✅ Created *{payload['group_title']}*\n\n"
    f"👉 [Open the project group]({payload['group_link']})\n\n"
    f"Each project is an isolated session. Switch to it and start talking."
)
```

Trigger the command in the user's main DM with the bot. The bot creates the group, returns the link, user clicks and chats.

## Bot admin promotion — the step that's easy to forget

`getMe` returns `can_read_all_group_messages: false` for the vast majority of bots. This means the bot, when added to a group as a regular member, only sees:
- Messages that @mention the bot
- Replies to the bot's own messages
- Slash commands (e.g. `/start`, `/help`)

For a project-session bot to feel like a DM — the user types anything, the bot responds — **the bot must be promoted to admin in every spawned group**. The helper does this automatically; if you write your own, do not skip the `EditAdminRequest` step. Symptom of forgetting: bot is in the group, you can see it as a member, you type "hello", nothing happens. The fix is to promote it to admin via the userbot or manually in Telegram's group settings.

## Security model

The user is logged into the userbot as **themselves**. Anything the userbot does is indistinguishable from something the user did manually in the Telegram app:
- Creating groups
- Adding people (in our case, the bot)
- Sending messages
- Promoting to admin

For personal use this is fine. Things to be aware of:
- If someone gains access to `~/.hermes/tg_userbot.session`, they have full access to the user's Telegram account. Treat that file like a private key. Chmod 600.
- Telegram's anti-spam may flag high-volume user-client activity. If you spawn >100 groups/day or do lots of bulk operations, expect friction. For personal use (10–20 groups) this is not a concern.
- The user-client bypasses the bot's `TELEGRAM_ALLOWED_USERS` allowlist — it's a separate auth path. The bot-side allowlist still governs which *users* can talk to the bot in any spawned group; the userbot is only used to create the chat surface.

## Session storage & persistence

The telethon session is a binary file at `~/.hermes/tg_userbot.session`. It survives across `hermes` restarts. To re-auth (e.g. password changed, session revoked from another device), delete the file and re-run `whoami` to trigger a fresh interactive login.

## Testing the full flow

```bash
# 1. Sanity: are we logged in?
python ~/.hermes/scripts/tg_userbot.py whoami

# 2. Spawn a real group
python ~/.hermes/scripts/tg_userbot.py create-group "Smoke Test Project" \
  --welcome "Hi from Hermes 🚀 This is an isolated test session."

# 3. Open the returned t.me invite link in Telegram. You should see:
#    - A new group "Smoke Test Project" in your chat list
#    - The bot listed as an admin (rank "Hermes Project Bot")
#    - A pinned welcome message
#    - You can type anything and the bot responds

# 4. From your main DM with the bot, type: /new "Second Test Project"
#    (assuming you wired the slash command glue above)
#    The bot should reply with a clickable invite link.
```

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| Helper hangs at phone prompt, never returns | telethon version mismatch, or venv not sourced | `source ~/.hermes/hermes-agent/venv/bin/activate && pip install -U telethon` |
| `ApiIdInvalidError` on first run | `TG_USER_API_ID` is wrong / not numeric | Re-grab from my.telegram.org; must be a plain integer |
| Helper returns OK but bot stays deaf in new group | Bot added but not promoted to admin | Add `EditAdminRequest` step; check via Telegram UI (bot should show "admin" badge) |
| `UserPrivacyRestrictedError` when adding bot | Bot's privacy settings block being added by non-contacts; only matters in some configs | Resolve via BotFather: `/setprivacy` → Disable |
| Cron-context failures: `ModuleNotFoundError: telethon` | Cron jobs run in fresh venv-less shells | Helper uses `~/.hermes/hermes-agent/venv/bin/python` directly via shebang, or invoke with `source ... && python ...` |
| Invite link is `t.me/c/<id>` (no real invite) | Group is too new and has no exported invite; private-link fallback returned a placeholder | Wait a few seconds, retry, or use `ExportChatInviteRequest` to generate a real one |
