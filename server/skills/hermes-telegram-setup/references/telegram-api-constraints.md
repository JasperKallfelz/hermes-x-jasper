# Telegram Bot API Constraints for Multi-Session Agents

Quick reference for what a Telegram bot CAN and CANNOT do. This is the source of truth when reasoning about "spawn a new chat" UX.

## What bots CAN do

| Action | Bot API method | Notes |
|---|---|---|
| Send/receive messages in existing chats | `sendMessage`, polling/`getUpdates`, webhook | Always available |
| Create forum topics in supergroups | `createForumTopic` | Requires supergroup with Topics enabled |
| Create DM topic lanes (Bot API 9.4+) | `createForumTopic` in private chat | Late 2024 feature. Bot must have `has_topics_enabled` capability |
| Generate invite links for existing chats | `createChatInviteLink` | For users/groups that already exist |
| Manage members (kick, promote) | `kickChatMember`, `promoteChatMember` | With appropriate admin perms |
| Pin/unpin messages, edit messages | `pinChatMessage`, `editMessageText` | Standard |
| Set bot command menu | `setMyCommands` | Up to 100 commands, but ~4KB payload cap → practical limit ~30 visible + hidden |
| Read all group messages | requires `can_read_all_group_messages: true` | NOT granted by default. Bot must be admin with appropriate perms |

## What bots CANNOT do

| Action | Workaround |
|---|---|
| **Create new groups** (no `createGroup` method exists) | Use a user-client (telethon/pyrogram) — Pattern C in the umbrella skill |
| **Create new channels** (no `createChannel` method exists) | Same — user-client required |
| **Join groups** without an invite | Bot must be added by a user, or use an invite link |
| **See all messages in a group by default** | Bot must be admin with `post_messages` (this also enables reading all) |
| **Send messages older than 24h in DMs without user interaction** | Telegram's anti-spam; not an issue for normal flow |
| **Read messages from other bots in groups** | Out of scope — bots don't see other bots' messages either |

## Critical version requirements

- **Bot API 9.4+** (released late 2024) — required for DM topic lanes. Check a bot's capabilities with `getMyCommands` doesn't show this; instead, look at the `getChat` response for the user's DM and check `is_forum` or the DM-topics capability. Older bots/bot frameworks may need updates.
- **Telegram Forum Topics** in supergroups — available since 2022. Any modern Telegram app supports them.

## Bot identity from token

The token format is `<bot_id>:<secret>`. The numeric prefix (`bot_id`) is **the bot's own Telegram user ID**, not yours. Confusing this with your user ID is a common setup bug:

- Setting `TELEGRAM_HOME_CHANNEL` to the bot's ID → cron deliveries fail with `Forbidden: the bot can't send messages to the bot`
- Setting `TELEGRAM_ALLOWED_USERS` to the bot's ID → silently drops your messages

Use `@userinfobot` (or any `user_id` lookup) to get your real numeric user ID.

## getMe useful fields

```json
{
  "id": 8941021119,
  "is_bot": true,
  "username": "YourBot_bot",
  "can_join_groups": true,
  "can_read_all_group_messages": false,
  "supports_inline_queries": false,
  "has_topics_enabled": true,            // Bot API 9.4+ — can do DM topics
  "allows_users_to_create_topics": true  // In supergroups with Topics enabled
}
```

`can_read_all_group_messages: false` (the default for bots created via BotFather) means the bot is privacy-mode enabled. Disable privacy mode via @Botfather → `/setprivacy` → `Disable` if you need the bot to see all messages without admin promotion. Note: this still requires the bot to be added as admin in some cases (admin override is the most reliable).

## Sources

- Telegram Bot API: https://core.telegram.org/bots/api
- Bot API 9.4 changelog: https://core.telegram.org/bots/api#april-1-2025
- Forum topics: https://telegram.org/blog/topics-in-groups-2
- @BotFather commands: https://core.telegram.org/bots#6-botfather
