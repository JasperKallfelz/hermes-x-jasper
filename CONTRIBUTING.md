# Contributing

Thanks for helping out. This is a small community starter, so the bar is simple: **it must install cleanly on a fresh machine, and it must never leak anyone's data.**

## Where does your change belong?

This repo is *not* Hermes Agent. Before you open a PR here, check:

- **A bug in the agent, the gateway, a tool, a platform adapter?** → report it upstream at [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). Not here.
- **A bug in the installer, the patch, the example config, or the helper scripts?** → right place, carry on.
- **A feature the patch adds** (Discord voice, auto-CDP browser, voice jobs)? → here for now. If it is genuinely useful to everyone, the better home is an upstream PR — and we would rather delete a patch hunk than carry it forever.

## Before you open a PR

```bash
make verify
```

That runs everything CI runs: `bash -n` + shellcheck, `compileall`, the test suite, the leak audit, and `git apply --check` of the patch against a *fresh* clone of the pinned upstream commit (needs network). If `make verify` is green, CI will be too.

## The rules that actually matter

**1. No personal data. Ever.**

No names, emails, absolute home paths (`/Users/...`, `/home/...`), bot tokens, API keys, Discord/Telegram IDs, chat logs. Not in code, not in the patch, not in a comment, not in a commit message.

`make audit` enforces this and runs in CI. Placeholders are what you want instead:

- `user@example.com`, `<YOUR_TOKEN>`, `~/hermes-agent`, empty `KEY=` values
- Real names of technologies and vendors (Discord, Edge TTS, Parakeet) are fine — those are not personal data.

**2. Secrets never move through this repo.**

`setup.sh` must not accept, prompt for, print, or persist a credential. Config examples ship with empty values.

**3. Never clobber a user's config.**

Anything that touches `~/.hermes/config.yaml` or `.env` must: check whether the file exists, back it up before writing, and default to a dry run. `scripts/merge_config.py` is the only sanctioned way to modify a live config.

**4. `setup.sh` stays idempotent.**

Every step checks its own end state first, and every mutation goes through `run()` so `--dry-run` stays honest. Re-running the installer must be a no-op, not a second install.

## Changing the patch

`patches/voice-and-desktop-features.patch` is a plain `git diff` against the pinned commit. To regenerate it:

```bash
cd ~/hermes-agent                       # your patched checkout
git diff > ~/hermes-x-jasper/patches/voice-and-desktop-features.patch
cd ~/hermes-x-jasper && make verify
```

Then read your own diff before you commit it. A patch generated from a working tree picks up whatever else is in that tree — that is exactly how a home path or a bot token gets published.

If you bump the pinned commit, update it in **all** of: `setup.sh`, `verify.sh`, `.github/workflows/ci.yml`, and `README.md`. `make verify` will catch you if you miss one.

## Style

- Shell: `bash`, `set -euo pipefail`, shellcheck-clean at `-S warning`.
- Python: 3.11+, standard library where possible, type hints on new functions.
- Tests: `unittest` (pytest runs them fine). Every new branch in `merge_config.py` or `audit_public.py` needs a test — and for the audit, a test that it does **not** fire on the placeholder form.
- Comments explain *why*, not *what*.

## Licensing

Contributions are MIT, same as the repo and same as upstream. By opening a PR you confirm you wrote the code, or that it is compatibly licensed and attributed.
