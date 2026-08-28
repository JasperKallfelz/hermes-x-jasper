# Contributing

Thanks for helping out. This is a small community starter, so the bar is simple: **it must install cleanly on a fresh machine, and it must never leak anyone's data.**

## Where does your change belong?

This repo is *not* Hermes Agent. Before you open a PR here, check:

- **A bug in the agent, the gateway, a tool, a platform adapter?** → report it upstream at [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). Not here.
- **A bug in the installer, the patch, the example config, or the helper scripts?** → right place, carry on.
- **A feature the patch adds** (auto-CDP launch, internal TTS persona overrides, Telegram location-keyboard cleanup)? → here for now. If it is genuinely useful to everyone, the better home is an upstream PR — and we would rather delete a patch hunk than carry it forever.
- **The experimental Pi RPC runtime?** → changes belong in
  `modules/pi-runtime/` and must preserve its separate-installation boundary.
  Do not fold it into the stable v0.20.6 patch or touch `coder-stack/` as part
  of a Pi-only change.

## Before you open a PR

```bash
make verify
make release-audit
```

That runs the local model-free gates: `bash -n` + required shellcheck,
`compileall`, pytest + PyYAML, the leak audit, pinned Gitleaks 8.30.1
current-tree/full-history scans, and plain-apply proof of the stable patch.
Module-focused tests also bind the Pi manifest, scripts, docs, path classifier,
and CI aggregation contract. GitHub's required Pi workflow adds the 1,419-case
release suite, desktop tests/typecheck/build, exact Windows drive-letter test,
and native ARM64 two-build plus 5/5 Docker E2E gates. An unavailable or skipped
required Pi job is not green.

## The rules that actually matter

**1. No personal data. Ever.**

No names, emails, absolute home paths (`/Users/...`, `/home/...`), bot tokens, provider API credentials, numeric Discord or Telegram IDs, or chat logs. Not in code, not in the patch, not in a comment, not in a commit message.

`make release-audit` is the authoritative fail-closed gate; its custom scanner
and pinned Gitleaks tree/history passes both run in CI. The scanner's own test vectors are
assembled at runtime so no static secret ever lands in the tree; the only
historical exceptions are narrow, rule-bound, commit+path-scoped gitleaks
allowlists for a fixed set of old commits — never a blanket test-directory or
global allowlist (see `.gitleaks.toml` and `security/audit-exceptions.json`). Placeholders are what you want instead:

- `user@example.com`, `<YOUR_TOKEN>`, `~/hermes-agent`, empty `KEY=` values
- Real names of technologies and vendors (Discord, Edge TTS, Parakeet) are fine — those are not personal data.

**2. Secrets never move through this repo.**

`setup.sh` must not accept, prompt for, print, or persist a credential. Config examples ship with empty values.

**3. Never clobber a user's config.**

Anything that touches `~/.hermes/config.yaml` or `.env` must: check whether the file exists, back it up before writing, and default to a dry run. `scripts/merge_config.py` is the only sanctioned way to modify a live config.

**4. `setup.sh` stays idempotent.**

Every step checks its own end state first, and every mutation goes through `run()` so `--dry-run` stays honest. Re-running the installer must be a no-op, not a second install.

**5. Pi stays isolated and unauthenticated by automation.**

The module manifest must continue to lock base
`306db2776c6b6f1acc85c31c4dabba3263f0e9fd`, feature
`c1093d23837bab98013bc9929d0d2679416601e5`, Pi 0.84.3, and image
`sha256:e89f45110e9277902bafbf49009e842bc9e38180e668fea8a6ff3dcdb2dd2cdf`
unless a new independently reviewed evidence set replaces the whole tuple.
Setup may use only exact objects and plain apply. It must never edit live
config, install globally, run Docker, start a process, or authenticate. Pi OAuth
is a trusted-local manual flow; the expired OAuth means authenticated E2E is a
documented gap. No credentials or auth state belong in tests or fixtures.

Any Pi module script, manifest, patch, test, workflow, evidence, or classifier
change must classify as Pi-sensitive. The aggregate `check` must fail if the
required Pi workflow fails, is cancelled, or is unexpectedly skipped.

## Changing the patch

`patches/voice-and-desktop-features.patch` is a plain, full-index `git diff`
against the pinned commit. Generate it only from a dedicated detached checkout:

```bash
scripts/regenerate_patch.sh /absolute/path/to/dedicated-patched-checkout
cd ~/hermes-x-jasper && make verify
```

The regeneration helper stages only `patches/voice-and-desktop-features.paths`, fails unless the cached set equals that allowlist exactly, and always unstages it. It never uses `git add -A`. Read the resulting diff before committing it.

Before creating a commit, configure this repository to use the public GitHub noreply identity:

```bash
git config user.email 72349064+JasperKallfelz@users.noreply.github.com
```

The release audit scans author/committer names, emails, and messages, so a personal address fails before a tag can be published.

If you bump the pinned commit, update the release tuple in every source listed
by `tests/test_release_baseline.py` (installer, verifier, CI, AGENTS, README,
security/troubleshooting docs, config comments, and tests). `make verify` will
catch a stale current-baseline reference.

## Style

- Shell: `bash`, `set -euo pipefail`, shellcheck-clean at `-S warning`.
- Python: 3.11+, standard library where possible, type hints on new functions.
- Tests: `unittest` (pytest runs them fine). Every new branch in `merge_config.py` or `audit_public.py` needs a test — and for the audit, a test that it does **not** fire on the placeholder form.
- Comments explain *why*, not *what*.

## Licensing

Contributions are MIT, same as the repo and same as upstream. By opening a PR you confirm you wrote the code, or that it is compatibly licensed and attributed.
