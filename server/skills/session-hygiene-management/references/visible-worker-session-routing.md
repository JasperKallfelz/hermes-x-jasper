# Visible worker sessions vs hidden delegation

Session-specific note from a Hermes sidebar cleanup review.

## What happened
- Several helper runs showed up as separate visible sessions in the left sidebar.
- The user wanted the active workspace to stay focused and not be flooded by those helper sessions.
- The safe cleanup path was to archive the accidental helper sessions, not delete them.

## Takeaway
- If the goal is to keep the sidebar clean, use hidden delegation / Codex-style subwork when available.
- Do **not** create a full visible Hermes session for background subwork unless the user explicitly wants a separate chat.
- If visible helper sessions were created accidentally, archive them after verifying the active sessions you want to keep remain visible.

## Verification pattern used
- Archive matched sessions by a narrow filter, then read back the session store to confirm `archived=1`.
- Confirm the sidebar no longer shows the archived helper entries.