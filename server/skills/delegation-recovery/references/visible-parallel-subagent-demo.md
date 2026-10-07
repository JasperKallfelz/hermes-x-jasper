# Visible Parallel Subagent Demo

Use this when the user explicitly wants to *see* background subagents being spawned or asks for a visible parallel test.

## Pattern
- Dispatch a small batch of 2-4 independent leaf subagents.
- Keep each task self-contained and analysis-only unless the user explicitly wants writes.
- Include the absolute checkout path and a narrow goal for each task.
- Share the delegation id and the live transcript file paths so the user can watch progress.
- Do not block on polling; let the consolidated result return when all children finish.
- If you want the TUI to show an active multi-session indicator for a while, make at least one child task deliberately longer-running but still read-only.

## Handoff rule
- Use the visible batch only for the demo or the audit.
- If a repair is needed afterward, switch to one serial writer against the same checkout.

## Verification cues
- The parent response includes `delegation_id` and `live_transcripts`.
- The operator can inspect the live logs or the `/agents` view while work is running.
- After completion, summarize the consolidated findings briefly and move on to the next serial step.
