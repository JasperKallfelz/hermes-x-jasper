# Active terminal/session discovery for Telegram chat routing

Use this when the user asks which Hermes sessions are currently "open" or wants the active terminals reflected in Telegram.

## Observation

Telegram's chat list/topic list often shows only **recently resumed** sessions. A live Hermes terminal can exist without an immediately visible Telegram thread entry yet.

## Discovery steps

1. Enumerate running Hermes TUI workers:
   ```bash
   ps -axo pid=,ppid=,tty=,command= | grep 'tui_gateway.slash_worker --session-key' | grep -v grep
   ```

2. Match session keys against the profile session registry:
   ```bash
   python3 - <<'PY'
   import json,os
   p=os.path.expanduser('~/.hermes/profiles/general/sessions/sessions.json')
   d=json.load(open(p))
   for k,v in sorted(d.items()):
       print(k, v['session_id'])
   PY
   ```

3. If needed, inspect the gateway process cwd to confirm the profile backing the terminal:
   ```bash
   ps -ww -p <pid> -o pid=,ppid=,tty=,etime=,command=
   lsof -a -p <pid> -d cwd -Fn 2>/dev/null
   ```

## Pitfall

- A session being absent from the Telegram list does **not** mean it is inactive.
- A visible Telegram entry may be only the latest resumed session, not the full set of running terminals.
- For user-facing answers, distinguish between:
  - **live worker processes**
  - **registered/resumable sessions**
  - **threads currently visible in Telegram**
