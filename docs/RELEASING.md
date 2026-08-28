# Releasing v0.3.0

`make release-audit` is the local artifact audit. Run it from a clean,
full-history checkout after `./scripts/install_gitleaks.sh`. It checks the actual
base range, exact tracked inventory, custom tree/history/metadata policy, pinned
Gitleaks (including the Pi patch), both module and stable patch inputs, and the
unpacked/hash-bound artifact produced only by `git archive` of the candidate
commit. The required hosted Pi workflow supplies architecture-specific release
evidence that a local artifact audit cannot honestly substitute.

Configure a public commit identity before committing:

```bash
git config user.email 72349064+JasperKallfelz@users.noreply.github.com
```

Repository administrators must create a GitHub ruleset for `refs/tags/v*` with tag creation/deletion restricted to release maintainers. The v0.3.0 contract is:

1. The candidate is on protected `main`, whose required check is `CI / check`.
   That aggregate requires `release-gates` and, whenever the fail-closed path
   classifier says Pi is affected, a successful `pi-runtime` reusable workflow;
   required skips fail the aggregate.
2. Only the exact reviewed candidate may receive `v0.3.0`; moving or deleting the tag is prohibited.
3. Both `Release v0.3.0 / pi-runtime` and the dependent `Release v0.3.0 / gates`
   job are required for the tag SHA. The Pi call runs the 1,419-case suite,
   desktop gates, exact Windows test, two no-cache `linux/arm64` builds, and 5/5
   Docker containment/egress E2E against image
   `sha256:e89f45110e9277902bafbf49009e842bc9e38180e668fea8a6ff3dcdb2dd2cdf`.
4. Only the dependent `publish` job has `contents: write`; it repeats the audit, rebuilds from the tag Git object, rescans the exact unpacked bytes, verifies the SHA-256 run manifest, then creates the GitHub release as its last command.

The experimental Pi module remains separate from the stable v0.20.6 baseline.
Its base is `306db2776c6b6f1acc85c31c4dabba3263f0e9fd`, feature is
`c1093d23837bab98013bc9929d0d2679416601e5`, and Pi is 0.84.3. Release notes
must say that no credentials/live config are included, the prior OAuth expired,
authenticated provider/model E2E is not current, and rollback removes use of
the separate installation. Never turn that gap into a pass or an inferred claim.

Do not create a release manually to bypass the tag workflow. A `release.created` event runs read-only gates for diagnostics but cannot publish assets.
