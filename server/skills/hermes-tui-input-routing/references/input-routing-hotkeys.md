# Input routing hotkeys: reasoning cycle notes

Captured from a Hermes TUI implementation review.

## Reasoning hotkey behavior
- `Ctrl+R` is a session-local reasoning-effort cycler for the active chat.
- Cycle order: `none → minimal → low → medium → high → xhigh → max → ultra → none`.
- The composer must pass `Ctrl+R` through so focused typing does not insert a literal `r`.
- Unknown or empty current values should normalize to `none` before cycling.
- The transcript notice should only say "applies next turn" when the RPC response explicitly marks the update as deferred.

## Routing rules
- Read current state with `config.get { key: 'reasoning', session_id }`.
- Write the new value with exactly one `config.set { key: 'reasoning', session_id, value }`.
- Never send `scope: global` for the hotkey path.
- Ignore the hotkey when a modal or overlay owns input.
- Preserve any local overlay-specific `Ctrl+R` behavior that already exists in a focused subview.

## Concurrency / race notes
- Rapid repeated keypresses can race async responses.
- Use a per-session in-flight guard rather than one global boolean.
- A second `Ctrl+R` for the same session should collapse while the request is pending.
- A different active session should still cycle independently.

## Missing-session behavior
- If there is no active session, stop locally with a concise system notice.
- Do not make a gateway request in that case.

## Test ideas
- wraparound from `ultra` to `none`
- unknown value defaults to `none`
- missing active session is handled locally
- payload uses session scope only
- composer pass-through prevents literal `r`
- same-session requests collapse; different sessions stay independent
