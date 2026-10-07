# Artifact cleanup in a worktree

Use this when builds or verification steps generate tracked or untracked artifacts that should not remain in the final diff.

## Pattern

1. **Restore tracked generated files** if the build rewrote them but they are not intended source changes.
   - Examples: `dist/index.html`, hashed bundle outputs, `*.tsbuildinfo`.
2. **Move untracked build outputs aside** instead of leaving them in the worktree.
   - Examples: generated `dist/assets/*`, temporary profiles, `node_modules` symlinks.
   - Prefer a timestamped folder in `~/.Trash/` or another clearly separated quarantine location.
3. **Re-check the status with exclusions** for known binary/model artifacts.
   - Verify the final source diff only contains intended code changes.
4. **Run a whitespace/diff sanity check** after cleanup.
   - `git diff --check` should be clean.

## Why

This keeps verification honest: you can still prove the build worked, but you do not accidentally leave behind noisy generated files that hide the real source delta.