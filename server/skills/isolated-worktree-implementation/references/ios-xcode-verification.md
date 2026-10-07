# iOS / Xcode verification pattern

Use this when the isolated worktree task targets a Swift/Xcode repo and needs real proof, not a self-report.

## Recommended sequence

1. Freeze the task in a prompt file inside the worktree.
   - Include acceptance criteria.
   - Include the exact files in scope.
   - Include the exact verification commands.
   - Keep the prompt file under `.hermes/` so the contract is easy to inspect.

2. Run strict TDD.
   - Add the failing XCTest first.
   - Run the smallest relevant `xcodebuild ... test` command and confirm the failure is the missing behavior, not a typo or scheme issue.
   - Implement the smallest production change.
   - Re-run the same test command until it is green.

3. Verify with the real Xcode commands for the target.
   - Prefer the project’s explicit simulator destination when one is supplied in the task.
   - Follow with a Release simulator build when the task changes core logic and you want a compile-only sanity pass.

4. Finish with local hygiene checks.
   - `git status --short`
   - `git diff --check`
   - `git diff -- <touched files>`

## Notes

- Keep the worktree isolated; do not write into the main checkout.
- Do not trust a runner/subagent summary alone.
- Report the exact command lines and the observed result codes.
