# Freshness audit recipe

This reference captures the comparison pattern that surfaced in a real Hermes audit.

## Minimal command set

```bash
hermes --version
command -v hermes

git fetch --tags --prune origin

git rev-parse HEAD

git rev-parse origin/main

git rev-list --left-right --count HEAD...origin/main

git log -1 --format='%cI %h %s' HEAD

git log -1 --format='%cI %h %s' origin/main

# When comparing a release tag, peel the tag first

git rev-parse v2026.8.27^{}
git rev-list --left-right --count HEAD...v2026.8.27^{}

# For GitHub release/state checks

gh release list --repo NousResearch/hermes-agent --limit 5
gh release view v2026.8.27 --repo NousResearch/hermes-agent

gh api repos/NousResearch/hermes-agent/commits/main
```

## Wrapper parity checks

Compare installed helper scripts against checked-in copies with `cmp -s` or checksums.

```bash
cmp -s ~/.local/bin/hermes-coder ~/.hermes/coder-stack/bin/hermes-coder
cmp -s ~/.local/bin/hermes-coder-flow ~/.hermes/coder-stack/bin/hermes-coder-flow
```

If both the private stack and the public starter exist, compare them separately so you can say which layer diverged.

## Real audit findings to remember

- The installed Hermes core can be current even when the public starter repo is intentionally older.
- The public starter repo may be clean and CI-green while still lagging the live setup by many releases.
- Installed wrappers can differ from both the public starter and the private checked-in stack; do not assume parity from one comparison alone.
- For GitHub release ancestry, compare the peeled commit (`tag^{}`), not the tag object.

## Reporting template

- Live install: current / behind / ahead
- Upstream release: current / behind / ahead
- Public repo: current / intentionally lagging / out of sync
- Wrapper stack: identical / diverged
- Recommendation: sync release, leave starter alone, or publish a new mirror
