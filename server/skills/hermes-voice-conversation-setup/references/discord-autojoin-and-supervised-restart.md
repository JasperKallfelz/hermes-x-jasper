# Discord voice autojoin and supervised restart

Use this when a Discord voice session should come back after a restart and the bot must re-enter the right channel reliably.

## Config to set

Set the autojoin triad through the Hermes CLI so the live config is the source of truth:

```bash
hermes config set discord.voice_fx.autojoin.guild_id <guild-id>
hermes config set discord.voice_fx.autojoin.channel_id <voice-channel-id>
hermes config set discord.voice_fx.autojoin.text_channel_id <text-channel-id>
```

## Supervised restart pattern

If the gateway is launchd-supervised, restart it from a shell outside the running gateway environment so you do not inherit stale gateway/session vars:

```bash
env -u HERMES_GATEWAY_SESSION -u _HERMES_GATEWAY -u HERMES_SESSION_SOURCE -u HERMES_SESSION_KEY -u HERMES_HOME \
  ~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile general gateway restart
```

If launchctl bootstrap is needed, bootstrap from a fresh shell and verify the label is running afterward.

## What to verify in logs

After restart, confirm all three signals before declaring success:

- `Discord voice autojoin active: guild=... voice_channel=... text_channel=...`
- `VoiceReceiver started`
- recurring `Voice UDP packet seen` lines while the channel is active

## Why this matters

A config write alone does not prove the live voice session recovered. The runtime must actually rejoin, start the receiver, and observe packets again.
