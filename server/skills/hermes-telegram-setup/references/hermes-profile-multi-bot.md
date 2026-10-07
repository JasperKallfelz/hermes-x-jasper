# Hermes Multi-Profile / Multi-Bot Pattern

Detailed walkthrough of running multiple Telegram bots through Hermes profiles. Companion to the umbrella skill.

> **For the install/start pattern, see the umbrella SKILL.md's "Multi-bot routing" section.** It covers three forms (wrapper / explicit `HERMES_HOME` / install the wrapper) depending on whether `~/.local/bin/general` is installed. This reference doc focuses on the broader picture: anatomy, what's shared, inspection, and edge cases.

## Why profiles

The Hermes gateway reads `TELEGRAM_BOT_TOKEN` once at startup from a single source. If two gateway processes pointed at the same `.env`, they'd both connect with the same token, race on updates, and the second would 409-conflict. The clean solution is **profiles**: each profile has its own `HERMES_HOME` directory, its own `.env`, its own launchd service label, and its own gateway.

The trade-off: a second gateway process consumes a small amount of memory/CPU (~150-250 MB on a Mac with default skills). For a personal setup running 2-3 bots, this is negligible.

## Anatomy of a profile

```
~/.hermes/                          # default profile (HERMES_HOME for default)
├── .env                            # contains default TELEGRAM_BOT_TOKEN
├── gateway_state.json
├── logs/gateway.log
├── sessions.db                     # the default profile's session store
├── skills/                         # bundled + user skills
├── cron/
└── ...

~/.hermes/profiles/general/         # general profile
├── .env                            # contains general TELEGRAM_BOT_TOKEN
├── gateway_state.json
├── logs/gateway.log
├── sessions.db                     # the general profile's session store (separate)
├── skills/                         # own copy (use --clone to seed from default)
├── cron/
└── ...
```

The `general` command works **if the wrapper at `~/.local/bin/general` is installed**. Check first:

```bash
command -v general
```

If it exists, it's typically a 2-line shell wrapper that exec's the underlying CLI with `-p general`:

```sh
#!/bin/sh
exec hermes -p general "$@"
```

If the wrapper is missing (which is common after manual profile setup, or when `hermes profile create` was run by a version that didn't auto-install wrappers), **use the explicit `HERMES_HOME` form instead** — this is the only form that always works:

```bash
HERMES_HOME=$HOME/.hermes/profiles/general \
  $HOME/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway install
```

The active profile (for any non-prefixed `hermes` invocation) is determined by `~/.hermes/active_profile` — don't change it lightly.

## Step-by-step: add a second bot

```bash
# 1. Create the profile, cloning config and .env from default
hermes profile create general --clone \
  --description "General-purpose bot for projects"

# 2. Edit the new profile's .env — change TELEGRAM_BOT_TOKEN to the new bot's
#    (and add TELEGRAM_BOT_USERNAME for the userbot helper)
$EDITOR ~/.hermes/profiles/general/.env

# 3. Install the second gateway as a launchd service.
#    See umbrella SKILL.md "Multi-bot routing" for the three forms.
#    Short version: use `general gateway install` if the wrapper exists,
#    otherwise the explicit HERMES_HOME form. ALWAYS do this — a gateway
#    started by hand has no auto-restart and will die silently.
general gateway install
# OR (if no wrapper):
HERMES_HOME=$HOME/.hermes/profiles/general \
  $HOME/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway install

# 4. Start it
general gateway start
# OR (if no wrapper) with the same HERMES_HOME prefix

# 5. Verify — both signals should be present
launchctl list | grep hermes.gateway
# Should show:
#   12345  0  ai.hermes.gateway           ← default, mail bot
#   67890  0  ai.hermes.gateway-general   ← general, new bot
cat ~/.hermes/profiles/general/gateway_state.json | python3 -m json.tool
# Look for: "gateway_state": "running", platforms.telegram.state: "connected"

# 6. End-to-end test: send a real message TO the bot from another account,
#    watch the log for an "inbound message" line, confirm the bot responds.
#    Sending a message via the Telegram HTTP API (curl) does NOT test this —
#    it bypasses the gateway entirely. See pitfall #8 in the umbrella SKILL.md.
```

## What is and isn't shared between profiles

| Resource | Shared? |
|---|---|
| Model (provider, model name, API key) | Yes — both profiles read the same `OPENROUTER_API_KEY` etc. |
| Skills (`~/.hermes/profiles/*/skills/`) | **No** — each profile has its own copy. `hermes profile create --clone` copies the default's skills; updates to one don't affect the other (use `hermes profile sync-skills` if available) |
| Sessions DB | **No** — completely separate. `/sessions` on the general profile shows only general bot sessions |
| Cron jobs | **No** — each profile has its own cron schedule |
| Memory | **No** — each profile has its own memory store |
| Launchd service | **No** — different labels: `ai.hermes.gateway` vs `ai.hermes.gateway-general` |
| Backups | **No** — backup each profile's home independently |

## The "agent personality" is the same

The user perceives the same Hermes personality on both bots. They get the same model, the same SOUL.md (unless edited per profile), the same default toolsets. The profile is just a routing namespace.

## Cron + cron deliveries into a specific bot

If you have cron jobs that should deliver to the *general* bot (not the mail one), set `TELEGRAM_HOME_CHANNEL` and `TELEGRAM_BOT_TOKEN` correctly in the *general* profile's `.env`, and run cron via `general cron list` / `general cron create`. The cron scheduler reads from the active profile.

## Dashboard port conflicts

The Hermes dashboard binds to `127.0.0.1:9120` by default. If you run `hermes dashboard` from both profiles simultaneously, the second one will fail with `EADDRINUSE`. Either:
- Run only one dashboard at a time
- Or pass `--port 9121` to the second profile: `general dashboard --port 9121`

In practice most users run the dashboard on the default profile and use the gateway for everything else.

## Inspecting a misbehaving second gateway

```bash
# State
cat ~/.hermes/profiles/general/gateway_state.json | python3 -m json.tool

# Live logs
tail -f ~/.hermes/profiles/general/logs/gateway.log

# Errors
tail -f ~/.hermes/profiles/general/logs/errors.log

# Stop / restart / uninstall
general gateway stop
general gateway restart
general gateway uninstall
```

If `general` doesn't exist, prepend the `HERMES_HOME=...` env var and call `python -m hermes_cli.main gateway <sub>` directly.

## When to consider one bot with topics instead of two profiles

If the user is just trying to get "isolated project sessions" and the existing bot is unused or underutilized, **prefer the existing bot + `/topic` mode** (zero setup, no second gateway process). Multi-profile is the right call only when the user explicitly wants:
- Separate bot identities (e.g., a personal bot vs. a work bot)
- Different Telegram usernames visible to other users
- Different launchd services that can be stopped independently
- Different cron schedules or session isolation for compliance reasons
