# Delegation failure recovery

Use this when a background delegation returns a transport-layer failure, timeout, or missing summary but the child agents may still have produced useful transcript output.

## Triage pattern

- Read the `live_transcripts` paths from the delegation response.
- Salvage concrete evidence from the transcript: file paths, commands run, line numbers, and intermediate findings.
- Treat the final summary as unverified until you have transcript evidence or a successful rerun.
- If the transcript already contains enough detail, continue with direct tool calls instead of re-fanning out blindly.
- If repeated background runs fail at delivery, switch to a direct read/search/terminal path or a local CLI worker.

## Why it matters

A missing or broken completion message can hide real work that already happened. Transcript-first recovery preserves that work and avoids repeating the same batch unnecessarily.
