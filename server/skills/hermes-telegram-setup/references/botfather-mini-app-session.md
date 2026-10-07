# BotFather Mini App session handling

Use this when Telegram BotFather settings are only available in the Mini App and the page shows `Session expired` or loads empty.

## Reliable access pattern

1. Request a fresh Main WebView URL from Telegram via a user-client session.
2. Open the returned `webappinternal.telegram.org/...` URL immediately in a browser.
3. If the page is empty or says the session expired, do **not** assume the feature is unavailable — fetch a new URL and retry.

## Practical note

The mini app URL carries `tgWebAppData` in the fragment; it is session-bound and can expire quickly. Treat any saved URL as disposable.

## Observation from this session

BotFather's Mini App was only reachable after generating a fresh webview URL from Telegram; reusing an older URL landed on an empty page / expired session state.