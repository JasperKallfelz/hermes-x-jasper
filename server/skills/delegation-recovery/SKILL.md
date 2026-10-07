---
name: delegation-recovery
description: Use when delegate_task batches fail; salvage transcripts.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [delegation, subagents, recovery, transcripts, orchestration]
---

# Delegation Recovery

Use this skill when a `delegate_task` fan-out, review batch, or other background subagent run completes with a transport failure, timeout, circuit-open message, or missing end summary.

## Core rule

A failed delivery is **not** the same as failed work. If the child agents produced a live transcript, inspect that transcript before you decide the run was useless.

## Workflow

1. **Check the result shape**
   - Look for `live_transcripts`, partial outputs, or explicit file paths from the delegation response.
   - If the final answer only reports a transport/backend failure, treat the run as **unverified**, not empty.
   - If the user explicitly wants to *see* subagents, dispatch a small visible batch first and point them at the live transcript paths or `/agents`; do not hide the run behind a silent wait loop.

2. **Salvage from transcripts**
   - Read the live transcript files first.
   - Extract concrete artifacts: file paths, commands, line numbers, intermediate findings, and any directly quoted evidence.
   - Prefer transcript-backed facts over the final summary when the two disagree.

3. **Decide whether to rerun**
   - Rerun only if the transcript is too thin to recover the needed evidence or the child clearly never reached the target files.
   - If the transcript already contains enough file/line evidence, continue with direct tool calls instead of re-dispatching blindly.
   - If repeated background runs fail at delivery, switch to a direct read/search/terminal path or a local CLI worker.

4. **Report honestly**
   - State that the child run completed but delivery failed.
   - Separate transcript-backed findings from any speculative summary.
   - Never present a missing final message as proof that nothing was found.

## Good fits

- Parallel audits where the subagents did real reading work but the completion message failed.
- Long-running analysis batches where the transcript still contains useful evidence.
- Recovering work after a background batch is incomplete but partially inspectable.

## Pitfalls

- Do not trust a child's self-report alone for external side effects or file writes.
- Do not keep fanning out the same failing batch without first checking the transcript.
- Do not record environment-specific backend outages as a permanent rule; the durable lesson is to inspect the transcript and verify the actual work.

## Linked reference

- `references/delegation-failure-recovery.md` — concise recovery notes and transcript-first triage pattern.
