# Quality-first worktree foundations

Session-proven recipe for large repo work where a clean demo artifact matters.

## What worked

- Created a dedicated worktree before broad edits.
- Saved a checkpoint first so the user could restore the previous state.
- Wrote a self-contained prompt file inside the worktree.
- Ran the coding runner from that isolated path with explicit `--task`, `--lane`, `--workdir`, and `--prompt-file` arguments.
- Kept acceptance criteria and artifact paths in the prompt itself.
- Verified with real repo tests and by checking actual artifact existence/decodability.

## Example pattern

```bash
worktree=/path/to/repo-worktree
./scripts/checkpoint save "before-major-change"
hermes-coder --task implement --lane complex --workdir "$worktree" --prompt-file "$worktree/TASK.md"
pytest ...
ffprobe ...
```

## Verification notes

- Do not trust the runner summary alone.
- Check the actual output paths after the job finishes.
- Use this pattern for multi-file implementation, video/report generation, or other tasks where the deliverable must be externally verifiable.
