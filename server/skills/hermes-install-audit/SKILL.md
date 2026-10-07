---
name: hermes-install-audit
description: "Use when checking Hermes install freshness."
version: 1.0.1
author: Hermes
license: MIT
metadata:
  hermes:
    tags: [hermes, install, audit, freshness, git, github, release, wrapper, versioning]
    related_skills: [macos-terminal-workflow, github-repo-management]
---

# Hermes installation freshness audit

## When to Use

Use this skill when the user asks whether a Hermes setup is "up to date", whether their live install matches GitHub, or whether wrapper binaries/config are synced across installs and repos.

## Goal

Give a grounded answer in three buckets:

1. **Live installed Hermes** — what is actually running now.
2. **Source repos / GitHub mirrors** — what the tracked repo and upstream release say.
3. **Wrapper stack / local tooling** — whether installed helper scripts are newer, older, or different from their checked-in copies.

Do not collapse these into one claim. It is common for a live install, a public starter repo, and a private helper stack to intentionally diverge.

## Workflow

1. **Clarify what "up to date" means** if needed:
   - current release tag
   - up-to-date with upstream `main`
   - synced with a public starter repo
   - synced with a private wrapper stack
   - all of the above

2. **Inspect the live install first**:
   - `hermes --version`
   - `command -v hermes`
   - `git rev-parse HEAD` in the install repo, if Hermes is installed from git
   - `git rev-list --left-right --count HEAD...origin/main`
   - `git log -1 --format=...` for the local head

3. **Check the upstream release shape**:
   - fetch tags before comparing
   - compare the tag commit with `tag^{} `, not the tag object itself
   - use `gh release list` / `gh release view` / `gh api repos/.../commits/main` when the GitHub view matters

4. **Compare helper binaries against their sources**:
   - `cmp -s` or checksum both sides
   - compare installed wrappers in `~/.local/bin` against their repo copies
   - if there is a public mirror and a private stack, compare both separately

5. **Report the result in buckets**:
   - Live install status
   - Upstream release lag / lead
   - Public repo lag / lead
   - Local wrapper divergence
   - Any config-overlay mismatch only if it is relevant to reproducibility

## Important pitfalls

- A release tag can point to a tag object; compare the peeled commit (`tag^{}`) when measuring ancestry.
- A public starter repo may intentionally lag the live setup. Do not call that a bug unless the user asked for a mirror.
- A matching `hermes --version` does not prove wrapper parity.
- A matching wrapper binary does not prove config parity.
- Do not count secrets or personal runtime state as part of the audit unless the user explicitly asked for a reproducibility check.
- If the install lives in a git checkout, check both the checkout head and the installed binary.

## Recommended output shape

- One-sentence verdict.
- Short bullets for the three buckets.
- A final recommendation: what to sync, what to leave alone, and whether any repo should be updated or released.

## Reference

See `references/freshness-audit-recipes.md` for a concise command recipe and an example of the exact comparison pattern that surfaced in a real Hermes audit.
