# Telegram group troubleshooting for Hermes

Session-derived notes:

- A bare text message like `hallo` in a Telegram group may not reach the bot at all unless the bot is allowed to read group messages.
- In groups, the reliable test is a slash command targeted at the bot, e.g. `/whoami@Hermesjdrbrjfifb_bot`, or an explicit `@botusername` mention.
- If the bot does not respond in a group, verify:
  1. The correct Hermes profile/gateway is running.
  2. The bot is actually a member of the group.
  3. The bot is promoted to admin if the group should forward all messages, not just mentions/replies/commands.
  4. You are using the correct bot username for that profile.
- For the `general` profile, `hermes --profile general gateway status` should show `ai.hermes.gateway-general` as loaded/running.
- Gateway logs for the `general` profile live under `~/.hermes/profiles/general/logs/gateway.log`.
