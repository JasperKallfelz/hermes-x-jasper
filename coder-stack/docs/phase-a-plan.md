# Reliability hardening lineage

This document replaces the historical Phase A cross-provider proposal. The shipped automated stack is now Codex-only; current contracts live in [Reliability and quality gates](reliability-and-gates.md) and [Hermes Coder Flow](hermes-coder-flow.md).

## Retained Phase A outcomes

The implementation still carries the reliability boundaries introduced across the earlier phases:

- concurrent draining of model stdout/stderr with bounded diagnostic tails;
- stable failure classes and exit codes;
- finite lane escalation, attempt/wall timeouts, and quality-failure limits;
- an owner-only, privacy-safe JSONL journal;
- a persistent quota/auth circuit that degrades safely on malformed state;
- immutable argv-based quality gates and model-free `--gates-only`;
- recursion guards and sanitized Git/private child environments;
- stable Linux/macOS process identities and exact process-group cleanup;
- a persistent POSIX supervisor with bounded readiness/acknowledgement and fail-closed descendant cleanup;
- bounded Git-launch handshakes for flow preflight and worktree operations;
- source/common-Git fingerprints, frozen gate policy, and paired stage telemetry.

The original proposal suggested prompt hashes, matched output fragments, and path-rich records. Those were deliberately not shipped: durable journals retain controlled operational metadata only.

## Current routing decision

The automated runner has exactly one provider:

```text
fast      gpt-5.6-luna / low
normal    gpt-5.6-terra / medium
complex   gpt-5.6-sol / high
frontier  gpt-5.6-sol / max
security  gpt-5.6-sol / max
```

`--primary auto` and `--primary codex` both choose Codex. A capability failure may advance one lane; quota/auth failure opens the Codex circuit and stops provider retries. There is no opposite-provider route.

Flow independence is process-based rather than provider-based: implementation, review, optional repair, and re-review each run through a fresh serial `hermes-coder` and fresh `codex exec` process. Reviews remain read-only, isolated, bounded, and fail closed.

## Separate optional tool

Claude Deep Chat is maintained under [tools/deep-chat](../tools/deep-chat/README.md). It is an explicit persistent-session/worktree command and is not referenced by coder/flow routing or preflight.

Both stacks rely on authenticated local subscription/OAuth CLIs. Neither stack performs direct model API calls or direct API-billing authentication.
