# Desktop Session Hygiene — Session Notes

These notes were captured while reviewing the Hermes desktop app's session cleanup flow.

## Observed rules

- Route cleanup and analysis requests to the backend/store that owns the selected profile.
- `profile: null` means the current session store / active profile context.
- `profile: "all"` means analyze or clean across profiles.
- Do not hard-delete sessions as the default cleanup action; archive or soft-delete instead.
- Re-check protection rules immediately before applying the mutation.
- Protect active, current, pinned, or otherwise in-use sessions.

## Partial-failure behavior

- If some profiles fail and others succeed, keep the successes.
- Report the failed profile names or session IDs explicitly.
- Avoid collapsing the result into a generic "some items failed" message.

## Verification pattern used in the review

- Confirm the packaged desktop artifact contains the new session hygiene UI and route.
- Confirm the live backend answers the hygiene endpoint and enforces auth on protected calls.
- Confirm the launch/startup path after deployment still points at the updated bundle.

## Why this matters

Session hygiene flows are easy to get wrong because the UI often looks correct while the real bug is a scope mismatch or stale backend guard. Treat every cleanup action as a boundary-sensitive operation.
