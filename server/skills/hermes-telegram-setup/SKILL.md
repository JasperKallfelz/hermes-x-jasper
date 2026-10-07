---
name: hermes-telegram-setup
description: "Connect Hermes to Telegram end-to-end: single-bot setup (BotFather, token, user-ID, .env, restart, gateway health), multi-session spawning (DM topics, forum supergroups, real groups via telethon userbot), and multi-bot routing (multiple Hermes profiles each running its own gateway + bot)."
version: 2.0.0
author: Hermes Agent
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [Telegram, Integration, Setup, Messaging, Multi-Bot, Multi-Session, Profiles, Userbot, Spawning]
---

# Hermes Telegram Setup

Connect Hermes so the user can chat with it directly via Telegram.

## Prerequisites

- Hermes installed and running
- A Telegram account
- ~/.hermes/.env accessible

## Steps

1. **Create a Bot via @BotFather**
   - Open Telegram, search @BotFather
   - Send /newbot
   - Choose a display name (e.g. "Hermes Assistant") and a username ending in `_bot`
   - BotFather replies with a token: `1234567890:ABCdef...`
   - Keep this token secret — treat it like a password

2. **Get your Telegram User-ID**
   - Open @userinfobot in Telegram, send /start
   - It replies with your numeric user ID (e.g. `123456789`)
   - **⚠️ This is NOT the bot's ID.** The bot's ID is the number prefix of the token (e.g. `1234567890` in `1234567890:ABCdef...`). Confusing the two is the single most common setup bug — see pitfall below.

3. **Write the values into ~/.hermes/.env**
   Hermes guards this file — automated writes via `patch` or `sed` are blocked by the approval system.
   The user must confirm the edit or run it themselves in a terminal:

   ```bash
   # In a regular terminal (not inside Hermes):
   sed -i '' "s|# TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=<token>|" ~/.hermes/.env
   sed -i '' "s|# TELEGRAM_ALLOWED_USERS=.*|TELEGRAM_ALLOWED_USERS=<user-id>|" ~/.hermes/.env
   ```

   Minimal required vars:
   ```
   TELEGRAM_BOT_TOKEN=1234567890:ABCdef...
   TELEGRAM_ALLOWED_USERS=123456789
   ```

   Optional (for cron delivery into a specific chat):
   ```
   TELEGRAM_HOME_CHANNEL=123456789
   TELEGRAM_HOME_CHANNEL_NAME=Hermes
   ```

4. **Restart Hermes** so it picks up the new env vars.
   The gateway or CLI process reads .env at startup only.

5. **Verify** — send any message to the bot from the allowed user account.
   Hermes should respond.

## Pitfalls

- **Token shared in chat**: if the user pastes the token into the Hermes conversation, tell them to immediately revoke it at @BotFather with /revoke and generate a new one. Chat history is stored in the session DB.
- **Credential file protection**: ~/.hermes/.env is a protected path. Hermes will block automated writes to it. The user must perform the edit themselves in a terminal or approve the action explicitly.
- **TELEGRAM_ALLOWED_USERS is critical for security**: without it, anyone who discovers the bot username can send messages to Hermes. Always set it.
- **Webhook vs long-polling**: the default is long-polling (no extra config). Webhook mode requires TELEGRAM_WEBHOOK_URL and a publicly reachable HTTPS endpoint. Use long-polling for local/personal setups.
- **Hermes must be restarted** after .env changes — env vars are read at process start only.
- **Gateway not running ≠ bad .env**: even with a perfect .env (BOT_TOKEN, ALLOWED_USERS, HOME_CHANNEL all set), the bot stays silent until the gateway service is started. Check first with `hermes gateway status` — if it says "Gateway is not running", fix with `hermes gateway install` (registers a launchd/systemd user service) then `hermes gateway start`. The .env config is necessary but not sufficient; the gateway daemon is what actually long-polls Telegram. This is the single most common reason "my bot stopped responding" — the user (or a macOS update) killed the launchd service.
- **Cron deliveries target the gateway's connected chat, not the job's origin chat**: when a cron job has `deliver: 'all'` or `deliver: 'telegram:<chat_id>'`, the gateway must be online at fire time, and the chat_id must match a chat the bot is a member of. A cron job fired while the gateway is down produces `last_status: 'ok'` but the message goes nowhere.
- **Bot ID is NOT your user ID — and using it breaks delivery silently**: The number prefix of the bot token (e.g. `1234567890` in `1234567890:ABCdef...`) is the bot's own Telegram user ID, not yours. Setting `TELEGRAM_HOME_CHANNEL` or `TELEGRAM_ALLOWED_USERS` to this number causes two distinct failures, both easy to miss: (1) cron deliveries fail with `telegram.error.Forbidden: the bot can't send messages to the bot` because the bot is trying to message itself; (2) `ALLOWED_USERS` set to the bot's ID does NOT include you, so messages you send to the bot are silently dropped. Always fetch your real user ID via `@userinfobot` → `/start` and use that number in both vars. Symptom signature: bot is online (you can see it in your chat list), gateway is connected (logs show `✓ telegram connected`), but no message ever arrives in either direction.
- **Bots in groups only see @mentions / replies / slash commands by default** (`can_read_all_group_messages: false` on the bot, visible via `getMe`). If you add the bot to a group expecting it to chat with you like in a DM, the bot will appear deaf — a bare text like `hallo` may never reach it. Use an explicit `@botusername` mention or a slash command targeted at the bot, e.g. `/whoami@Hermesjdrbrjfifb_bot`, to verify the pipeline. To make a bot see every message in a group, the user (group owner) must promote it to admin with at least `post_messages`. Check `getMe` for `can_read_all_group_messages` before assuming group-mode is broken.
- **If a group message fails to appear, verify the active profile too**: for multi-bot setups the running gateway may be on `general` (service label `ai.hermes.gateway-general`) while you are testing the default profile. Use `hermes --profile general gateway status` and the matching profile log under `~/.hermes/profiles/general/logs/gateway.log` when debugging the general bot.

## Multi-Session / Per-Project Chat Spawning

**Use case:** one bot, many parallel project conversations, each with its own clean context (no context pollution between projects). The user types in a "main" chat ("let's work on Project X") and ends up chatting in a dedicated, isolated session for Project X.

**The hard constraint:** Telegram Bot API does **not** expose `createGroup` or `createSupergroup`. Bots cannot create new groups, period. Whatever spawning UX you build must work around this. The three patterns below trade off how closely they match "the bot spins up a brand new chat" against how much setup the user carries.

### Pattern A — Topics in a supergroup (one-time manual setup, fully native)
User creates a Telegram group, enables Topics in settings, adds the bot. The gateway's existing `createForumTopic` call (`gateway/platforms/telegram.py`) and `session.py`'s per-`thread_id` session keying give you isolated sessions for free. The user "spawns" by tapping Telegram's native "Create Topic" button. Scales to ~200 topics per supergroup. Visually a topic appears as a sidebar entry — close to "new chat" UX, but technically a sub-thread of one group.

**Forum permission gate:** promoting the bot to a generic admin is not enough. After Topics/forum mode is enabled, explicitly grant the bot `can_manage_topics` / Telethon `ChatAdminRights(manage_topics=True)`. If the bot was promoted before forum mode was enabled, re-apply its admin rights afterward. Verify from the Bot API with `getChat` (`is_forum: true`) and `getChatMember` (`status: administrator`, `can_manage_topics: true`) before debugging Hermes. Without this right, `createForumTopic` may misleadingly report `not a forum`, `forums_disabled`, or `Not enough rights to create a topic` even though the supergroup itself is correctly in forum mode.

Best for: solo use, fastest path, zero new infrastructure.

### Pattern B — `/topic` mode in a 1-on-1 DM (Bot API 9.4+)
Telegram Bot API 9.4 added topic support for direct messages with bots. Send `/topic` in the bot DM to enable multi-session mode, then `/topic <name>` to create a new isolated topic lane. No new groups at all — everything lives inside the existing DM, with topics as sidebar entries. The gateway already ships the full `/topic` handler (`gateway/slash_commands.py:2348`); no glue required.

Best for: keeping the chat list clean (one DM, not many groups), under ~100 active projects.

User-preference cue: if the user asks for "mehrere Chats", "echte Chats", or wants items visible in the Telegram chat list, do **not** sell Pattern B as the main answer. Explain B briefly if asked, then route to Pattern C because topics are not separate visible chats.

### Pattern C — Real separate groups via a userbot helper (only path for true "new group" UX)
The only way to spawn actual separate Telegram groups on demand is a **telethon user-client** that uses the user's own account, because bots can't create groups. One-time setup:
1. Get `api_id` + `api_hash` from https://my.telegram.org → "API development tools" → "Create new application".
2. Drop them in `~/.hermes/.env` as `TG_USER_API_ID` and `TG_USER_API_HASH`. Also add `TELEGRAM_BOT_USERNAME=<bot_username_without_at>`.
3. The helper at `~/.hermes/scripts/tg_userbot.py` (or the template in `references/multi-session-spawning.md`) does the one-time phone login, persists a session file, and then exposes `create-group <name>` for fully automated spawning.

The helper must also **promote the bot to admin** in the new group (with `post_messages`, `delete_messages`, `pin_messages`) — otherwise the bot stays deaf per the pitfall above. Once that's done, the user clicks the returned invite link, starts talking, and the bot responds to every message in the new group as if it were a DM.

Best for: 10–20+ parallel projects, per-group notification/permission control, the literal "new chat in my list per project" UX the user asked about.

**Tradeoffs of C:** uses the user's personal Telegram session (not just the bot token), so Telegram's anti-spam could theoretically flag the user-client for automation at high volume. For personal use at <100 groups/day this is fine; for higher volume, stay on Pattern A.

**Session routing is already correct** — the gateway keys sessions by `(platform=telegram, chat_id, thread_id)`, so each spawned chat (whether topic or group) automatically gets its own isolated Hermes session. The only thing to build is the spawner, not the routing.

For the full helper script template, env setup walkthrough, one-time auth flow, admin-promotion requirement, and the slash-command glue that wires it to `/new <name>`, see `references/multi-session-spawning.md`.

## Multi-Bot Routing (Hermes Profiles)

When the user wants multiple Telegram bots running in parallel — e.g., one mail bot, one general-purpose bot, one per-team bot — the answer is **Hermes profiles**. Each profile is an isolated Hermes home directory with its own `.env`, gateway, launchd service, sessions DB, and cron schedule. Both gateways connect to the SAME Hermes agent (model, memory, SOUL.md) — the profile is just a routing namespace.

If the bots live under separate macOS user accounts on the same machine, that is also supported: each user gets its own `~/.hermes`, its own gateway, and its own login state. The main extra constraints are unique bot tokens and non-overlapping dashboard ports.

### Setup

```bash
# 1. Create a profile, cloning config + .env from default
hermes profile create general --clone --description "General-purpose bot for projects"

# 2. Edit the new profile's .env to swap the Telegram token
$EDITOR ~/.hermes/profiles/general/.env
#    Change: TELEGRAM_BOT_TOKEN=***    Add:    TELEGRAM_BOT_USERNAME=YourNewBot_bot

# 3. Install + start the second gateway as a launchd service
#    Use the `general` wrapper if it exists, else the explicit HERMES_HOME form
command -v general && {
    general gateway install && general gateway start
} || {
    HERMES_HOME=$HOME/.hermes/profiles/general \
        $HOME/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway install
    HERMES_HOME=$HOME/.hermes/profiles/general \
        $HOME/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway start
}
```

**Always use `gateway install`** (which writes a launchd plist). A gateway started by hand (`gateway run` in a foreground terminal) has no auto-restart and dies silently on sleep/wake. This is the single biggest source of "the bot worked yesterday but is dead today" reports.

**`launchd` service label inspection:**
```bash
launchctl list | grep hermes.gateway
# Expected for two profiles:
#   12345  0  ai.hermes.gateway           ← default profile (mail bot)
#   67890  0  ai.hermes.gateway-general   ← general profile (project bot)
```
A missing line = launchd slot exists but process is dead.

### What is and isn't shared between profiles

| Resource | Shared? |
|---|---|
| Model (provider, model name, API key) | Yes |
| Skills (`~/.hermes/profiles/*/skills/`) | No — each profile has its own copy |
| Sessions DB | No — completely separate |
| Cron jobs | No — each profile has its own cron schedule |
| Memory | No — each profile has its own memory store |
| Launchd service | No — different labels (`ai.hermes.gateway` vs `ai.hermes.gateway-general`) |
| Backups | No — backup each profile's home independently |

### Dashboard port conflicts

The Hermes dashboard binds to `127.0.0.1:9120` by default. If you run `hermes dashboard` from both profiles simultaneously, the second fails with `EADDRINUSE`. Either run only one dashboard at a time, or pass `--port 9121` to the second.

For the full anatomy of profiles, edge cases, and inspection commands, see `references/hermes-profile-multi-bot.md`.

## Setting Bot Profile Photos via BotFather + Telethon

The Bot API has no method for changing a bot's profile photo. For bulk updates, use the authenticated Telethon user session to drive `@BotFather`.

### Default visual policy

When the user wants Hermes-branded Telegram avatars, start with **crop-only official website artwork**. Preserve the original Hermes-Agent website style and avoid adding icons, overlays, rings, labels, or app-style frames unless the user explicitly asks for extra graphics.

### Update flow

1. Send `/setuserpic` and wait for `Choose a bot to change profile photo.`
2. BotFather's bot selector is an **ordinary reply keyboard**, not an inline callback keyboard. Telethon button wrappers therefore have `data=None`. Do **not** call `message.click(data=button.data)` — with `None`, Telethon clicks the first button and silently updates the wrong bot.
3. Send the exact `@bot_username` as a normal text message (or use `message.click(text='@bot_username')`).
4. Wait for `OK. Send me the new profile photo for the bot.`
5. Upload a square PNG/JPEG with `conversation.send_file(path, force_document=False)`.
6. Require BotFather's `Success! Profile photo updated.` response.
7. Verify the external side effect independently: resolve the bot username with Telethon, call `photos.GetUserPhotosRequest(..., limit=1)`, download the newest 640×640 Telegram copy, and compare it with the source image after resizing. BotFather success alone is not enough.

### Avatar composition rules

- Use 1024×1024 source images.
- Keep the key motif inside the central ~80% so Telegram's circle crop stays legible.
- Leave labels and role names for contact sheets only.
- For the official Hermes site style, prefer a clean crop over adding new symbols.
- If the user later asks for a symbol overlay, treat that as a separate design pass, not the default.

Bulk runs should process bots sequentially with a short delay.

Related notes: `references/telegram-bot-avatar-artwork.md`

## Live Terminal-to-Telegram Mirroring

`/resume` and CLI handoff do **not** continuously mirror an open TUI into Telegram. `/resume` only binds a Telegram lane to a persisted session snapshot; later messages written by another live TUI are not pushed into Telegram automatically. A handoff transfers continuation to the destination rather than providing a simultaneous mirror.

For the operator's persistent one-way live view, use the sidecar installed at `~/.hermes/scripts/telegram_terminal_mirror.py` with:

- config: `~/.hermes/telegram_mirror.json` (`chat_id` plus `session_id -> thread_id/title` mappings),
- cursor state: `~/.hermes/telegram_mirror_state.json`,
- source DB: `~/.hermes/state.db`,
- token source: `~/.hermes/profiles/general/.env`,
- launchd label: `ai.hermes.telegram-terminal-mirror`, polling every 2 seconds.

The sidecar mirrors only visible `user`/`assistant` text and labels it `Du (Terminal)` / `Hermes (Terminal)`; it skips system/tool content. On first installation, run one bounded backfill, then load the launchd service. Telegram group rate limits can interrupt large backfills, so inspect the cursor state and rerun remaining rows with pacing rather than resetting the state (which would duplicate messages).

Verification:

1. `launchctl print gui/$(id -u)/ai.hermes.telegram-terminal-mirror` shows `state = running`.
2. Compare each configured cursor to the max visible message ID in `~/.hermes/state.db`; remaining count should be zero after backfill.
3. Confirm Telegram Web shows `Du (Terminal)` / `Hermes (Terminal)` messages in the expected topics.

This is one-way visibility. Sending a Telegram message back into an already-running TUI is not live bidirectional collaboration; use a proper handoff or build an event-bus-backed shared session for that.

## Verifying a Gateway is Alive

A gateway can be registered with launchd but still dead in practice (crashed, OOM'd, killed by macOS sleep). **Always verify on receipt of any "is the bot working?" question, and after any restart.** The four signals, fastest to slowest:

```bash
# 1. Is the process running? (fastest)
launchctl list | grep hermes.gateway
# Expected: <pid>  0  ai.hermes.gateway  ← healthy

# 2. Is the gateway state machine happy?
cat ~/.hermes/profiles/general/gateway_state.json | python3 -m json.tool
# Look for: "gateway_state": "running", platforms.telegram.state: "connected"

# 3. Is Telegram polling?
tail -5 ~/.hermes/profiles/general/logs/gateway.log
# Look for: "Telegram reconnected successfully" within the last few minutes

# 4. End-to-end test: send a real message TO the bot from another account
#    From a different Telegram account, send "/start" to the bot.
#    Then immediately: tail -f ~/.hermes/profiles/general/logs/gateway.log
#    You should see an "inbound message" line within ~1 second
```

**One-command health check** for the default profile:
```bash
launchctl list | grep -c hermes.gateway   # should be ≥1
cat ~/.hermes/gateway_state.json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['gateway_state'], d.get('platforms',{}).get('telegram',{}).get('state'))"
```

If the launchd line is missing or PID is `-`, the service is loaded but not running. If `gateway_state` is anything other than `running`, check the log. If Telegram state is not `connected`, the network/auth is broken but the process is alive.

**A gateway restart can take up to 180s during a long turn** — `gateway restart` initiates a drain that waits for the current agent turn to finish before exiting. If the user reports "gateway is broken after restart", check whether the drain is still in progress (`gateway_state: "draining"`).

**Test messages sent via Telegram HTTP API bypass the gateway** — `curl https://api.telegram.org/bot${TOKEN}/sendMessage` works even if the gateway is dead. It proves the token is valid, NOT that the gateway is healthy. A user who sends a message BACK to the bot and gets no response is the real test.

## Multi-Bot Pitfalls

- **Slash commands queue behind in-progress turns** — the gateway processes one message at a time per session. A `/topic` or `/stop` sent during a 5-minute agentic loop is QUEUED, not handled in parallel. From the user's side the bot looks "dead." Check the gateway log for the most recent "inbound message" entry before assuming broken.
- **Telegram-visible chats are not the same thing as live terminals** — the sidebar often shows only recently resumed sessions. When the user asks for "all open terminals" or the active ones, enumerate `tui_gateway.entry` / `slash_worker` processes and reconcile them with the profile session registry before answering.
- **`/resume` arguments need exact normalization** — when adding replay flags, parse `/resume <session-id> --replay=<n>` as a command plus flags, not a single opaque session string. If Telegram says `No session found matching '<id> --replay=1'`, the parser is swallowing the flag into the session id.
- **Bash heredoc + `***` glob expansion mangles tokens** — the bot token contains `:AAH...` patterns, and shell-globbing 3+ asterisks in a heredoc string will be expanded or cause a syntax error. Write Python scripts to `/tmp/foo.py` via `write_file` (no bash quoting), then `python3 /tmp/foo.py`.
- **Two gateways pointing at the same `.env` race** — both connect with the same token, the second 409-conflicts. Profiles give each gateway its own `.env` and launchd service label.
- **Separate macOS users are fine, but LaunchAgents are user-scoped** — if the parent user logs out, their gateway stops. Keep that user logged in for a long-lived agent, or move their gateway to a user-scoped daemon if you need boot-time availability.
- **Dashboard ports can still collide across users** — even with separate macOS accounts, only one process can bind a given localhost port at a time.
- **BotFather Mini App URLs expire fast** — if BotFather opens to a blank page or says `Session expired`, do not keep retrying the same link. Generate a fresh Main WebView URL from Telegram (via a user-client `RequestMainWebViewRequest`) and reopen it immediately.
- **Cron deliveries target the gateway's connected chat** — a cron job fired while the gateway is down produces `last_status: 'ok'` but the message goes nowhere.

## References

- Related umbrella: `hermes-voice-conversation-setup` — use when Telegram setup overlaps with voice messages, TTS/STT, or the user asks for a call/live conversation.
- `references/macos-multi-user-parallel-gateways.md` — notes for running Hermes in parallel under separate macOS users on the same machine.
- `references/telegram-env-vars.md` — full list of supported `TELEGRAM_*` env vars
- `references/multi-session-spawning.md` — Pattern C: telethon userbot helper for spawning real separate groups on demand (script template, env vars, one-time auth flow, admin-promotion requirement, slash-command glue)
- `references/hermes-profile-multi-bot.md` — anatomy of profiles, what's shared, inspection, dashboard port conflicts
- `references/telegram-api-constraints.md` — what Telegram bots CAN and CANNOT do (quick reference for "spawn a new chat" reasoning)
- `references/botfather-mini-app-session.md` — how to recover BotFather settings access when the Mini App URL is stale or the page says session expired
- `references/telegram-group-troubleshooting.md` — session-derived notes for debugging Telegram groups where bare text messages do not reach the bot
- `references/resume-replay-and-browser-verification.md` — `/resume` replay syntax and Chrome DOM verification notes
- `references/active-terminal-session-discovery.md` — how to reconcile live Hermes TUI workers, session registry entries, and Telegram-visible threads

## Scripts

- `scripts/tg_userbot.py` — telethon user-client helper. Subcommands: `whoami`, `list-sessions`, `create-group <name> [--welcome msg]`. Reads `TG_USER_API_ID` / `TG_USER_API_HASH` / `TELEGRAM_BOT_USERNAME` from `~/.hermes/.env`. Persists session to `~/.hermes/tg_userbot.session`.
