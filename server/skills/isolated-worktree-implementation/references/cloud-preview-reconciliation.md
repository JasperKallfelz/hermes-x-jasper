# Cloud Preview Supabase reconciliation

Use this when a cloud preview branch has drifted from the repo's historical migration ledger.

## Pattern

- Probe the live preview database read-only first.
- Compare the live schema to the repository's migration history; treat those as separate facts.
- Create one new forward-only reconciliation migration after the last known version.
- Make the migration idempotent, fail-closed, and safe on both partially-materialized and freshly migrated targets.
- Keep a separate read-only verification SQL file for the cloud target.
- After applying and re-verifying, repair the remote migration ledger only for historical versions that are already materialized.
- Never replay old migrations just to satisfy the ledger.
- Never mutate auth users or seed data during reconciliation.

## Session example

In this session, the live preview branch was missing `public.session_videos`, `public.coach_conversations`, and `public.coach_messages`, plus the private buckets `videos`, `snapshots`, and `labeling-videos`.
