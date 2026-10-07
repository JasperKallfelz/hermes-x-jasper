# Read-only verifier sandboxes

Session note for isolated worktree verification under a read-only Docker container.

## Observed failure mode

In a Pi plugin verification run, the exact requested commands were still the right contract:

- `pytest -q`
- `ruff check .`
- `python3 -m compileall -q .`

But the verifier container itself was read-only, so the run exposed two classes of issues that need sandbox-aware handling:

- `pytest` collection could not import the project module from the workspace root.
- `pytest`, `ruff`, and `compileall` all tried to write cache/bytecode artifacts under the workspace and hit EROFS.

## Guardrails

- Keep `/workspace` read-only.
- Route temporary state to existing tmpfs mounts such as `/tmp` or `/home/user`.
- Do not mount auth/config/session/bootstrap into verification.
- Keep trusted executables fixed; avoid letting workspace files shadow the verifier tool before the tool has loaded.
- Preserve the original argv/result contract; fix the sandbox behavior, not the reported command.

## Use this reference for

- read-only verification containers,
- cache and pycache redirection,
- safe import-path handling for trusted test/lint entrypoints.
