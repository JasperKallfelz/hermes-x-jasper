# Messaging bridges (WhatsApp + Signal)

Opt-in, macOS-first setup that wires **personal WhatsApp and Signal** into
Hermes as loopback-only bridges. Nothing here runs until you invoke it, and no
upstream bridge code is vendored into this repo — the WhatsApp bridge lives in
the pinned upstream Hermes Agent checkout (`scripts/whatsapp-bridge`), and Signal
is driven by the community `signal-cli` daemon.

This module is a sanitized, public-safe mirror of the maintainer's private live
setup. Real numbers, session data, and machine paths are intentionally absent —
you supply them when you run the installers.

```bash
cd messaging
./setup_whatsapp.sh     # loopback Baileys bridge on :3000, self-chat trigger
./setup_signal.sh       # signal-cli JSON-RPC daemon on 127.0.0.1:8080
```

Both installers are idempotent: re-run them any time. They print clear
next-step guidance instead of failing silently.

---

## What it sets up

| Piece | Where | How it runs |
| --- | --- | --- |
| WhatsApp bridge | upstream `scripts/whatsapp-bridge` (Baileys) | LaunchAgent `com.example.hermes-messaging.whatsapp`, `node … --mode self-chat --port 3000` |
| Signal daemon | `signal-cli` (Homebrew) | LaunchAgent `com.example.hermes-messaging.signal`, `signal-cli --account <E164> daemon --http 127.0.0.1:8080` |

Both LaunchAgents use `RunAtLoad` + `KeepAlive`, a private `Umask` (63 → `077`),
and write logs under `$HOME/.hermes/second-brain/logs/`. Templates live in
[`launchd/`](launchd/) and are rendered by the installers (`__HOME__`,
`__NODE__`, `__SIGNAL_CLI__`, `__SIGNAL_E164_ACCOUNT__`, `__BRIDGE_DIR__`
placeholders are filled in at install time).

---

## WhatsApp (`setup_whatsapp.sh`)

Checks for `node`/`npm`, points a LaunchAgent at the upstream Baileys bridge,
installs the bridge's dependencies if needed, loads the agent, and verifies
`curl http://127.0.0.1:3000/health` (expects `"status":"connected"`, or prints
pairing guidance). The session directory
`$HOME/.hermes/platforms/whatsapp/session` is created `0700` — it holds
linked-device credentials.

**Trigger mode:** `--mode self-chat`. Message *yourself* on WhatsApp and the
agent responds in that same chat.

### HTTP API (loopback, port 3000)

| Endpoint | Purpose |
| --- | --- |
| `POST /send` | Send a text message |
| `POST /send-media` | Send an image / video / document / audio |
| `POST /reply` | Reply, quoting a message |
| `POST /reaction` | Add or remove an emoji reaction |
| `POST /edit` | Edit a previously sent message |
| `POST /delete` | Delete (revoke) a message |
| `POST /read` | Mark messages as read |
| `POST /typing` | Send a typing / presence indicator |
| `GET /chat/:id` | Fetch chat metadata |
| `GET /messages` | Fetch recent messages |
| `GET /health` | Connection status (`"status":"connected"` when paired) |

Voice notes are auto-converted to **ogg/opus** push-to-talk (PTT) so they arrive
as native WhatsApp voice messages (requires `ffmpeg` on `PATH`).

### Pairing

On first run the bridge is up but unpaired. Watch its log for a QR code, then
link it from your phone:

```bash
tail -f "$HOME/.hermes/second-brain/logs/hermes-messaging.whatsapp.err.log"
# phone: WhatsApp -> Settings -> Linked Devices -> Link a Device -> scan
curl -sS http://127.0.0.1:3000/health      # expect "status":"connected"
```

---

## Signal (`setup_signal.sh`)

Checks for `signal-cli` (Homebrew) and a Java 17+ runtime, links this machine as
a Signal **linked device**, then runs the `signal-cli` JSON-RPC daemon.

The linking flow is headless:

1. `signal-cli link -n "Hermes"` emits an `sgnl://` linking URI, captured to a
   `chmod 600` temp file.
2. A QR PNG is rendered with `qrencode` and opened for you to scan.
3. `signal-cli listAccounts` is polled (bounded retries) until the device links.
4. The QR/URI temp files are removed on exit.

Then it renders + loads the daemon LaunchAgent, verifies
`curl http://127.0.0.1:8080/api/v1/check`, and prints the env lines to add to
`~/.hermes/.env`:

```dotenv
SIGNAL_ACCOUNT=+<your-e164-number>
SIGNAL_HTTP_URL=http://127.0.0.1:8080
```

### Capabilities

Signal is driven over **JSON-RPC / SSE** (server-sent events for incoming
messages). Supported: native text formatting, reply quotes, reactions, and
attachments. **Note-to-Self** is the trigger — message yourself on Signal to
reach the agent.

---

## Limitations (honest)

- **No real voice or video calls.** Neither the Baileys WhatsApp bridge nor
  `signal-cli` places or answers actual calls. Calls are only detectable /
  initiated through the official desktop apps; this module does not add calling.
- **Linked-device model.** Both bridges run as a *secondary* device paired to
  your phone. Your phone remains the primary; unlink from the phone to revoke.
- **macOS-first.** The installers assume `launchd`. On Linux you would adapt the
  plist templates to `systemd --user` units yourself.
- **Upstream, not vendored.** The WhatsApp bridge is expected in the pinned
  upstream checkout. If you moved it, point the installer at it with
  `HERMES_WA_BRIDGE_DIR=…`.

---

## Security notes

- **Loopback only.** Both bridges bind `127.0.0.1` (ports 3000 and 8080). Do not
  expose these ports; anything that can reach them can send as you.
- **Session dirs are credentials.** `$HOME/.hermes/platforms/whatsapp/session`
  and the `signal-cli` data store are linked-device secrets. They are created
  private (`0700`), stay on your machine, and must never be committed.
- **Allowlist / fail closed.** Keep the bridges on self-chat / Note-to-Self
  triggers and gate who the agent will act for. An agent reachable by strangers
  is a shell reachable by strangers.
- **No secrets in git.** The account number, tokens, and session data belong in
  `~/.hermes/.env` and your local session dirs — never in this repo. Run
  `python3 scripts/audit_public.py .` before publishing a fork.

---

## Uninstall

```bash
launchctl bootout gui/$(id -u)/com.example.hermes-messaging.whatsapp
launchctl bootout gui/$(id -u)/com.example.hermes-messaging.signal
rm -f "$HOME/Library/LaunchAgents/com.example.hermes-messaging.whatsapp.plist"
rm -f "$HOME/Library/LaunchAgents/com.example.hermes-messaging.signal.plist"
# WhatsApp session credentials (unpair from your phone first):
rm -rf "$HOME/.hermes/platforms/whatsapp/session"
```
