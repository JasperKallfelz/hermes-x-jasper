# Telegram Environment Variables for Hermes

From ~/.hermes/.env (as of June 2026):

| Variable | Required | Description |
|---|---|---|
| TELEGRAM_BOT_TOKEN | yes | Token from @BotFather |
| TELEGRAM_ALLOWED_USERS | strongly recommended | Comma-separated Telegram user IDs allowed to interact |
| TELEGRAM_HOME_CHANNEL | no | Default chat ID for cron job delivery |
| TELEGRAM_HOME_CHANNEL_NAME | no | Display name for the home channel |
| TELEGRAM_CRON_THREAD_ID | no | Forum topic ID for cron deliveries (overrides TELEGRAM_HOME_CHANNEL_THREAD_ID) |
| TELEGRAM_WEBHOOK_URL | no | Switches from long-polling to webhook mode (e.g. https://my-app.fly.dev/telegram) |
| TELEGRAM_WEBHOOK_PORT | no | Port for webhook (default 8443) |
| TELEGRAM_WEBHOOK_SECRET | no | Recommended for production webhook setups |

## Config options in config.yaml

Under `telegram:`:
```yaml
telegram:
  reactions: false           # Send reaction emoji on message receipt
  channel_prompts: {}        # Per-channel system prompt overrides
  allowed_chats: ''          # Restrict to specific chat IDs (alternative to TELEGRAM_ALLOWED_USERS)
```
