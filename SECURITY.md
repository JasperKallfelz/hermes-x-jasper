# Security

This starter installs an AI agent that **runs code, uses a terminal, and drives a logged-in browser on your machine**. That is the point of it, and it is also the whole threat model. Please read this before you connect it to anything.

## The one thing you must not skip

**Set your allowlists.**

```dotenv
TELEGRAM_ALLOWED_USERS=
DISCORD_ALLOWED_USERS=
```

A Telegram or Discord bot is reachable by anyone who finds it. With an empty allowlist, "anyone who finds it" gets an agent that can execute code as you. Put your own numeric user id in there before the bot ever goes online.

## What the agent can do

Assume it can do anything you can do at your own keyboard:

- **Execute code and shell commands.** `code_execution` and the terminal toolset run as your user, with your permissions.
- **Use a logged-in browser profile.** With `browser.cdp_url` set to a local DevTools endpoint, Hermes attaches to the dedicated persistent Chrome debugging profile. Every site you log into in that profile is available to the agent.
- **Read and write files** anywhere your user can.
- **Spend money** on whatever API keys you give it.

Reduce blast radius if that makes you uncomfortable: keep `browser.allow_private_urls: false` (the default here), leave `delegation.subagent_auto_approve: false`, consider `memory.write_approval: true`, and run it in a VM or a dedicated user account if you want a real boundary.

## Prompt injection is a live risk

The agent reads web pages, emails and messages, and those are attacker-controlled text. A page can contain instructions aimed at your agent. Combine that with a logged-in browser and a shell and the consequences are real.

- Do not point the agent at untrusted content while it holds credentials you care about.
- Be sceptical of any tool call you did not ask for.
- Approval gates exist for a reason; leaving them off is a choice.

## Secrets

- Secrets go in `~/.hermes/.env` — **never** in `config.yaml`, never in this repo.
- `.env` is git-ignored. `.env.example` ships with every value empty, on purpose.
- `setup.sh` never asks for, prints, or stores a credential.
- Rotate anything you have ever pasted into a chat, a log, or an issue.

## Experimental Pi module boundary

`modules/pi-runtime/` is an explicit opt-in module and never changes the stable
v0.20.6 installation. Hermes remains the control plane; Pi 0.84.3 is a
contained coding runtime in a separate checkout. Setup requires an explicit
path and performs no Docker build/start, config edit, global install, gateway
start, credential copy, or authentication.

The reviewed source tuple is base
`306db2776c6b6f1acc85c31c4dabba3263f0e9fd` plus feature
`c1093d23837bab98013bc9929d0d2679416601e5`. Only the immutable `linux/arm64`
image ID
`sha256:e89f45110e9277902bafbf49009e842bc9e38180e668fea8a6ff3dcdb2dd2cdf`
is accepted by the public verifier; tags fail before Docker is invoked.
Reviewed evidence is 1,419 offline tests with zero failures/skips/retries, two
matching no-cache builds, containment/egress E2E 5/5, and independent READY
review with no P0–P2 findings.

No credentials, auth state, or live setup ship. Pi OAuth is a trusted-local
manual host flow outside contained execution. The prior OAuth expired, so
authenticated provider/model E2E is explicitly a current gap—not a pass or a
claim. Rollback is to stop using and archive/remove the separate installation;
never mutate the stable checkout to remove a module that was not installed in it.

## Before you publish a fork

```bash
make release-audit
```

`scripts/audit_public.py` scans for private keys, API tokens, bot tokens, `Authorization:` headers, absolute home paths, real email addresses and platform IDs, and exits non-zero on a hit. Add your own strings:

```bash
PUBLIC_AUDIT_DENYLIST="my-real-name,my-server.example" make audit
```

The authoritative target always runs the custom tree/full-history/metadata audit, checksum-pinned Gitleaks tree and history scans, real diff hygiene, exact-pin patch proof, release-input inventory checks, and a traversal-safe unpacked scan plus SHA-256 binding of the final `git archive` artifact. Gitleaks inline allows and `.gitleaksignore` are ignored/rejected.

Custom-audit exceptions live in `security/audit-exceptions.json`. Current exceptions require an exact trailing marker plus path/rule/line fingerprint. The candidate tip may use that current entry (avoiding an impossible self-referential commit hash); as soon as it is no longer `HEAD`, it must be migrated to a commit-keyed historical entry. The metadata section records only hashes: four already-public commits disclosed a personal author email before this gate existed. That pre-existing disclosure is reviewed explicitly here; public history is not rewritten. Future commits must use the documented GitHub noreply identity in [docs/RELEASING.md](docs/RELEASING.md).

## Reporting a vulnerability

**In this starter** (setup script, patch, helper scripts): open a GitHub issue. This is a small community repo maintained on a best-effort basis — please do not include a working exploit against a third party, and never paste a real credential into an issue.

**In Hermes Agent itself**: report it upstream to [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/security), not here. This repo does not maintain the agent.

## Supply chain

Root `setup.sh` pins upstream to a single reviewed commit (`5fc308a70719a83cccdbba4c0e39c23f5a8239d5`, Hermes Agent v0.20.6, tag `v2026.8.27`) rather than tracking a branch, so what you install is what was tested. Existing checkouts must be pristine at that pin or match the exact patched tree; setup rejects staged/unrelated changes, unexpected origins, and symlinked targets. It runs upstream's own installer, which pulls dependencies from PyPI. The separate Pi installer additionally binds its checked-in patch SHA-256 to both exact Git trees and uses plain apply only. Read each patch before you apply it—an integrity proof is not a safety proof.
