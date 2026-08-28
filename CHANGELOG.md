# Changelog

## v0.3.0 — 2026-08-28

This release ports the public starter baseline from Hermes Agent v0.19.0 to
v0.20.6, pinned to tag `v2026.8.27` at peeled commit
`5fc308a70719a83cccdbba4c0e39c23f5a8239d5`.

### Changed

- Rebased the reversible feature patch onto the exact v0.20.6 tree.
- Kept patch-owned automatic launch and loopback-only binding for a configured
  dedicated Chrome CDP profile.
- Kept internal per-call TTS voice/model persona overrides while treating
  v0.20.6's model-facing provider and speed arguments as upstream behavior.
- Kept Telegram reply-keyboard cleanup after location and venue shares.
- Reconciled the example overlay with the v0.20.6 schema and its 300-second,
  50-tool-call `execute_code` defaults; the deprecated
  `delegation.max_async_children` key and non-upstream `second_brain:` section
  remain absent.
- Made setup accept only a pristine pin or the exact patched tree and reject
  staged/unrelated changes, unexpected origins, unsafe target symlinks, and
  non-plain patch application.
- Updated CI, verification, tests, security guidance, troubleshooting, and
  feature provenance for the new release tuple.
- Added the experimental, explicit opt-in `modules/pi-runtime/` distribution.
  It installs only into a separate checkout; Hermes remains the control plane
  and Pi 0.84.3 is a contained coding runtime. The stable v0.20.6 starter pin
  and root patch contract are unchanged.
- Locked the Pi base
  `306db2776c6b6f1acc85c31c4dabba3263f0e9fd`, feature
  `c1093d23837bab98013bc9929d0d2679416601e5`, 775,861-byte patch, and immutable
  `linux/arm64` image
  `sha256:e89f45110e9277902bafbf49009e842bc9e38180e668fea8a6ff3dcdb2dd2cdf`.
  Evidence is 1,419 offline tests with zero failures/skips/retries, two matching
  clean no-cache builds, real containment/egress E2E 5/5, and independent READY
  re-review with no P0–P2 findings.
- Added fail-closed module setup/verification plus required Linux, Windows, and
  ARM64 Docker jobs. The aggregate CI `check` rejects unexpectedly skipped Pi
  gates whenever module scripts, manifest, patch, tests, or workflows change.

### Extension boundaries

- Pi is experimental and does not run unless its separate module is explicitly
  installed and activated. No credentials, auth state, live config, or image
  bytes ship. The prior OAuth expired, so authenticated provider/model E2E is
  explicitly not current. Trusted-local manual OAuth is outside contained
  execution and outside setup.
- The independently evolving `coder-stack/` snapshot is unchanged.
- Second Brain and messaging remain opt-in modules and do nothing until
  explicitly installed or invoked.
- No private/live configuration, credentials, identifiers, databases, local
  paths, sessions, or authentication state are included.
- Pi rollback is stopping use of and archiving/removing the separate
  installation, not mutating the stable starter checkout.

This is an unofficial community starter and is not affiliated with, endorsed
by, or maintained by Nous Research.
