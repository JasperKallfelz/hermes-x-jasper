# Many open sessions vs. process-stack cost

Source: Hermes performance-maintenance review (2026-09-02).

Key lesson:
- The number of saved or resumable sessions is not the main latency problem.
- The expensive part is usually how many separate TUI/gateway stacks are alive at once.
- Long turns can be valuable; do not 'optimize' by capping turns just to keep more sessions open.
- Prefer reducing duplicate dispatcher stacks, not deleting or pruning sessions.

Observed numbers from that review:
- Tool schemas in the full CLI prompt were ~72.7 KB across 41 tools.
- The fixed input budget was ~154.7 KB total.
- A focused launcher simulation reduced tool-schema bytes by ~32.9% while keeping the full mode intact.
- Eight separate TUI stacks were observed; the main cost was process/Gateway overhead, not the count of stored sessions.

Practical guidance:
- Keep a full-capability launcher for broad work.
- Use a focused launcher or workspace for the common case.
- Keep sessions open in the app/session store, but avoid spawning many duplicate stacks unless the user explicitly wants them.
- Verify with real prompt-size and process measurements after any tool-surface change.
