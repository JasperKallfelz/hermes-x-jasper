---
name: hermes-tui-input-routing
description: "Use when changing Hermes TUI hotkeys. Keep input scoped."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [hermes, tui, hotkeys, slash-commands, overlays, config-routing, ui]
    related_skills: [hermes-model-selection, session-hygiene-management, software-craft]
---

# Hermes TUI input routing

## When to use
Use this skill when changing or reviewing Hermes terminal UI behavior around:
- global keyboard shortcuts
- slash commands and command aliases
- modal or overlay-owned input handling
- session-scoped config changes from the TUI
- live updates that should take effect immediately without a restart

This class of work often looks small but has hidden state boundaries: the same key may mean different things in the composer, an overlay, a session switcher, or while a turn is in flight. Treat those boundaries as part of the feature.

## Core principles
1. **Respect input ownership first.**
   A hotkey is only global if no overlay, modal, or focused subview owns the event.

2. **Prefer session-scoped config for live behavior.**
   If a UI action changes user-facing behavior for the active chat, use the active session id and keep the change local unless the feature explicitly says otherwise.

3. **Keep async handlers single-flight.**
   If a keypress triggers `config.get` followed by `config.set`, guard against overlapping invocations so stale responses do not clobber newer intent.

4. **Announce visible state changes.**
   If the user can see the effect in the transcript or status UI, emit the corresponding system notice instead of leaving the change silent.

5. **Validate the actual UI path.**
   The fix is not complete until the binding, command routing, and user-visible result all line up in the running TUI.

## Recommended workflow
1. Identify the owner of the key event: composer, overlay, session switcher, or global handler.
2. Check whether the behavior should be session-scoped or global.
3. Patch the smallest routing layer that can own the behavior cleanly.
4. Add tests for:
   - the key path
   - the config payload
   - missing-session behavior
   - any wraparound or cycling logic
   - overlay precedence
5. Re-run the focused tests and the TUI build/typecheck path that proves the binding compiles.

## Pitfalls
- **Do not let a new global hotkey break an overlay shortcut.**
  If a modal already owns the chord, keep its local behavior intact or make the global handler explicitly conditional.

- **Do not infer global intent from a session feature.**
  A control that changes active-session behavior should not silently write global config.

- **Do not trust the first async reply that arrives.**
  Input handlers that call the gateway can race; guard by flight id, session id, or another monotonic token. For session-scoped toggles, a per-session in-flight set is safer than one global boolean.

- **Do not forget the user-visible confirmation path.**
  If the TUI has a transcript line for the change, test that it appears.

- **Do not encode one-off key bindings as hard-coded special cases if they belong in a shared cycling or registry helper.**
  Prefer a pure helper plus a thin key-binding layer.

- **Do not let focused input leak characters when a global hotkey should own the chord.**
  If the composer still receives the key, add it to the pass-through list and test that no literal character is inserted.

- **Do not promise next-turn timing unless the RPC actually defers.**
  Session config updates may apply immediately; only print deferred wording when the response explicitly carries it.

## Verification checklist
- [ ] Hotkey ownership is correct in all relevant UI states
- [ ] Session vs global scope is explicit in the RPC payload
- [ ] Overlapping keypresses cannot produce duplicate or stale updates
- [ ] Missing active session is handled locally and cleanly
- [ ] User-facing notice or transcript line matches the new state
- [ ] Focused tests cover the route and the payload
- [ ] Build/typecheck passes for the TUI entrypoints

## Support files
- `references/input-routing-hotkeys.md` — session-specific notes for reasoning hotkeys, session-scoped config payloads, and single-flight routing.