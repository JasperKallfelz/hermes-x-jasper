---
name: release-readiness-inventory
description: Use when inventorying unfinished work before release.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [readiness, inventory, release, handoff, verification, worktree, session-search]
    related_skills: [review-harness, session-hygiene-management, handoff]
---

# Release Readiness Inventory

Use this skill when the task is not "review this diff" but "what is still open, what is canonical, and what is actually ready to ship?" The goal is to produce a verified inventory of unfinished work across sessions, worktrees, and background jobs without conflating summaries, intent, and live state.

## When to Use

- The user asks what work is still unfinished.
- A feature exists in one worktree/session but not yet in the canonical path.
- You need to separate verified completion from partial progress or a stale task list.
- Several background jobs, worktrees, or branches are in flight and their current state matters.
- You are checking release readiness, handoff readiness, or whether a preview/runtime is truly the canonical source.

Do not use this skill for a normal code review, unless the review question has become a readiness or inventory question.

## Core Principle

A thing is only "done" if the live state proves it.

- A task note is not proof.
- A prior assistant summary is not proof.
- A branch existing somewhere is not proof.
- A background process still running is not proof of completion.
- A feature in a detached worktree or reference repo is not proof that the canonical path has it.

## Workflow

1. **Find the current authority.** Identify the canonical repo/worktree/branch first. If multiple sources exist, state which one is authoritative and which ones are only references.
2. **Recover prior context.** Use session history to find the latest relevant session and the anchor message that introduced the work.
3. **Inspect live state.** Verify the actual checkout, branch, untracked files, and running jobs. Do not trust a prior status summary if a process or checkout may have changed since then.
4. **Partition the work.** Separate:
   - verified and merged
   - verified but unmerged
   - in progress
   - blocked
   - intentionally out of scope
   - only present in a reference or detached worktree
5. **Report the gap.** For each unfinished item, say what is missing and what would count as completion.
6. **Update task trackers only after verification.** Mark an item complete when the underlying live state proves it, not when you expect it to be true.

## Evidence Rules

Prefer direct evidence from tools over narrative memory.

- Git status/diff for repository state.
- Process listings or process handles for background work.
- Session history for what was previously claimed or verified.
- Explicit file paths and branch names for canonical vs reference sources.

If a readiness claim depends on another session or worktree, name that source explicitly.

## Common Pitfalls

- Treating a reference worktree as if it were the canonical release path.
- Calling something complete because a reviewer said so, before the merge or deployment exists.
- Forgetting to mention untracked release files that are part of the real state.
- Collapsing "exists somewhere" and "exists in the shipped path" into the same bucket.
- Updating a todo list on intent instead of on verification.

## Output Shape

Prefer a concise inventory with one row per item:

- item / area
- current live state
- what is still missing
- what would prove completion
- any canonical-vs-reference caveat

When the user asks "what's left?", lead with the answer, then give the supporting evidence.

## Related Support Files

- `references/readiness-inventory-and-live-verification.md` — compact notes on session-history recovery, live process checks, and canonical-vs-reference separation.
