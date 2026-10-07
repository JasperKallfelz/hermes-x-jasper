# Resumed session watchdog false-positive

## Observation
In a single long-lived Hermes TUI session, a resumed deep-chat/Claude workflow showed a wait notice around 30s (`waiting on model` / `no response yet`) even though the request eventually completed successfully. Other sessions were normal.

## Likely cause
This pattern points to **session-local state** rather than a global provider outage:
- very large accumulated context
- high-reasoning / long-thinking request
- stale in-memory session/UI state after resume

The wait notice can be misleading when the backend is still working but has not emitted a stream event yet.

## Recovery sequence
1. Check whether the issue is isolated to one session.
2. If yes, compress the session before resuming.
3. Restart the Hermes TUI / host shell to clear stale in-memory state.
4. Re-run the request and verify the session returns to ready.

## Verification
Treat it as a provider incident only if the problem reproduces across fresh sessions or multiple models. If the resumed session succeeds after compression/restart, the root cause was session-local state plus the watchdog threshold, not a broken provider.
