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
- **Use your real browser session.** With `browser.cdp_url` set to a local DevTools endpoint, Hermes attaches to your actual Chrome. Every site you are logged into, it is logged into — mail, bank, GitHub.
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

## Before you publish a fork

```bash
make audit          # or: python3 scripts/audit_public.py .
```

`scripts/audit_public.py` scans for private keys, API tokens, bot tokens, `Authorization:` headers, absolute home paths, real email addresses and platform IDs, and exits non-zero on a hit. Add your own strings:

```bash
PUBLIC_AUDIT_DENYLIST="my-real-name,my-server.example" make audit
```

It runs in CI on every push. The only escape hatch is an `audit:allow` marker on one line. Whole-file `audit:allow-file` bypasses are deliberately unsupported. Do not use a marker to silence a genuine finding.

## Reporting a vulnerability

**In this starter** (setup script, patch, helper scripts): open a GitHub issue. This is a small community repo maintained on a best-effort basis — please do not include a working exploit against a third party, and never paste a real credential into an issue.

**In Hermes Agent itself**: report it upstream to [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/security), not here. This repo does not maintain the agent.

## Supply chain

`setup.sh` pins upstream to a single reviewed commit (`b56aafc2ef6befd96ecf00bf4788031cf4be169b`) rather than tracking a branch, so what you install is what was tested. It runs upstream's own installer, which pulls dependencies from PyPI. Read the patch before you apply it — `git apply --check` tells you it *fits*, not that it is *safe*. That part is on you, as it should be with any code you find on the internet.
