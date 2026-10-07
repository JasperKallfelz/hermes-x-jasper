---
name: session-hygiene-management
description: Use when building or reviewing session cleanup, sorting, pinning, archive, or hygiene flows for agent/chat desktop apps. Route actions to the correct store or profile, protect active work, prefer archive over delete, and verify partial-failure reporting end to end.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [sessions, desktop, cleanup, archive, routing, safety, verification]
    related_skills: [software-craft, review-harness, hermes-agent-skill-authoring]
---

# Session Hygiene Management

## Overview

Use this skill for features that help users keep session lists manageable: cleanup buttons, archive or hide actions, sorting, pinning, pruning, and safety rails around long-lived agent sessions.

The main failure mode in this class is acting on the wrong scope. A UI button may look correct while the backend targets the wrong profile, store, or conversation set. Treat boundary mapping and server-side protection as part of the feature, not as afterthoughts.

For Hermes-specific macOS runtime hygiene, treat desktop autostart and per-profile gateway services as separate targets. Audit each profile explicitly, remove the launchd plist, and re-check after a short drain delay before calling it done.

Many live sessions are cheap; many separate TUI/gateway stacks are what usually hurt. Do not treat "more open sessions" as the same problem as "more active process stacks". Preserve long turns when they are useful; the fix is usually a narrower launcher or fewer duplicate dispatchers, not a turn cap.

For stale-session hygiene, prefer **archive/soft-hide** over prune/delete. A 3-day idle threshold is a practical default in this workspace when the goal is to get unused sessions out of the active list while keeping rows recoverable; age by last activity, not creation time.

Long-lived resumed sessions can emit a misleading `waiting on model` / `no response yet` notice after ~30s even when the request later succeeds. If only one session shows the issue, compress the session and restart the Hermes TUI before escalating to a provider incident.

See `references/desktop-session-hygiene.md` for the session-specific notes captured from the Hermes desktop hygiene feature review.

See `references/hermes-autostart-audit.md` for a Hermes-specific macOS autostart/service audit checklist (multi-profile gateway + desktop launchd verification).

See `references/resumed-session-watchdog.md` for the reproduced Hermes/TUI case and recovery sequence.

See `references/many-open-sessions-vs-process-stacks.md` for the performance-maintenance note on keeping many sessions open without paying for many separate stacks.

See `references/session-auto-archive-3-days.md` for the verified 3-day idle archive sweep and DB-check pattern.

See `references/visible-worker-session-routing.md` for the sidebar-clutter lesson: prefer hidden delegation for background subwork, and archive accidental visible helper sessions.

## When to Use

- Building a session manager, session list, or cleanup UI
- Adding archive, prune, sort, pin, or hide actions for sessions
- Reviewing backend routes that operate on sessions, threads, or profiles
- Debugging a cleanup flow that works in one profile but not another
- Verifying that a desktop bundle or app shell really contains the new session action

## Core Workflow

1. **Map the boundaries first.** Identify whether the action targets a local cache, a per-profile store, a remote backend, or an entire workspace. Write this down before editing code so the UI and backend stay aligned.

2. **Protect live work.** Exclude active, current, pinned, or otherwise in-use sessions from destructive operations unless the product explicitly says otherwise. Enforce this on the server as well as in the UI.

3. **Prefer archival over deletion.** For cleanup UX, default to archive, hide, or soft-delete semantics. Keep hard delete as an explicit, separately reviewed path.

4. **Return granular failures.** If a batch action touches multiple profiles or sessions, report failures by the exact profile/session identifier, not as a vague aggregate. Preserve successful items when some entries fail.

5. **Revalidate at apply time.** Do a fresh server-side check immediately before the action mutates data. UI state can go stale; the backend must be the source of truth.

6. **Verify the shipped artifact.** Confirm the built bundle contains the new UI affordance and route, then test the live app path. A green unit test is not enough if the packaged app never picked up the change.

## Common Pitfalls

1. **Wrong scope, right button.** The cleanup button appears in the UI, but the request is routed to the wrong profile/store. Fix by tracing the request path from click handler to backend handler.

2. **Over-broad cleanup.** A batch prune removes active or working sessions. Fix by hard-coding exclusion rules and rechecking them server-side.

3. **Silent partial failure.** The action reports success even though one profile failed. Fix by surfacing the failing profile names or session IDs.

4. **Visible background work clutters the sidebar.** A helper task was launched as a full Hermes session instead of a hidden subagent/delegation. Fix by routing background work to hidden delegation/Codex when the user wants one workspace, not one extra chat per subtask.

5. **Deletion where archive was intended.** Users lose recoverability. Fix by making archive the default mutation and requiring an explicit hard-delete path.

5. **Mistaking many sessions for prompt growth.** The session store can stay large and still be fine. The real cost is often duplicate live stacks and tool-surface bloat. Fix by measuring prompt size and process count separately.

6. **Confusing archive with prune.** A stale-session sweep should soft-hide first; delete is a separate, explicit path. If the user wants unused sessions out of the active list after a few idle days, archive them and leave the rows recoverable.

7. **UI-only verification.** The component looks correct in source but the packaged app still shows stale behavior. Fix by checking the built artifact and the live app.

## Verification Checklist

- [ ] Boundaries identified: local vs remote, per-profile vs global, cache vs source of truth
- [ ] Active/current/pinned/in-use sessions protected from cleanup
- [ ] Cleanup semantics are archive/soft-delete unless hard delete is explicitly required
- [ ] Batch operations report granular failures by profile/session identifier
- [ ] Backend revalidates before mutating data
- [ ] Stale-session archive defaults to last-activity age, not creation age
- [ ] Archive and prune are intentionally separate code paths
- [ ] Built artifact contains the new session action or route
- [ ] Live app or API path verified after build/deploy
- [ ] Background subwork is routed so it does not create unwanted visible helper sessions
- [ ] Any session-specific reproduction details moved into a reference file, not the main skill
