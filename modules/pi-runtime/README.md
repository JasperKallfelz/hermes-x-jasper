# Experimental Pi RPC runtime module

This public, opt-in module applies the reviewed Pi RPC feature to a **separate
Hermes checkout**. It does not change this starter's stable Hermes v0.20.6
baseline (`5fc308a70719a83cccdbba4c0e39c23f5a8239d5`, tag
`v2026.8.27`). Hermes remains the control plane; Pi 0.84.3 is a contained coding
runtime selected by Hermes.

Nothing here contains credentials, authentication state, a live config, or a
prebuilt image. Setup does not install dependencies or global packages, invoke
an AI coding CLI, run Docker, build an image, start Hermes/a gateway, or perform
Pi authentication.

## Immutable contract

[`manifest.json`](manifest.json) locks the public upstream and feature object
IDs, both Git tree IDs, the exact patch size and SHA-256, Pi 0.84.3, the
`linux/arm64` target, the immutable image ID, tool versions, and all reviewed
test/build counts. Setup fetches commit IDs, never a moving branch. The public
fork branch `JasperKallfelz/hermes-agent:feature/pi-rpc-20260828` is a
convenient review surface, not an identity trusted by the scripts.

Reviewed evidence for this exact tuple:

- base `306db2776c6b6f1acc85c31c4dabba3263f0e9fd`;
- feature `c1093d23837bab98013bc9929d0d2679416601e5`;
- patch SHA-256
  `d1c3f99a7ad5f0028ebd813cd0553a524c0c3db1bc4d39a7fe4aff13f03a3e75`
  (775,861 bytes);
- 1,419 offline release tests passed with zero failures, skips, or retries;
- two clean no-cache `linux/arm64` builds produced the same image ID,
  `sha256:e89f45110e9277902bafbf49009e842bc9e38180e668fea8a6ff3dcdb2dd2cdf`;
- real Docker containment/egress E2E passed 5/5; and
- independent re-review was READY with no P0, P1, or P2 findings.

There is **no current authenticated provider/model E2E**. The previously
available Pi OAuth had expired, so that lane was not claimed and no auth
material is distributed.

The shell entrypoints choose a working `python3.11` before any older unversioned
`python3`. Verification also requires the exact uv 0.9.28 from the manifest.
Keep that tool isolated from the uv version used by other components and pass its
absolute executable path explicitly:

```bash
PI_UV=/absolute/path/to/isolated/uv-0.9.28
"$PI_UV" --version   # must print exactly: uv 0.9.28
```

## Safe setup

Choose a new, explicit directory outside this starter and outside any live
Hermes checkout. The parent must already exist. Dry-run makes no filesystem or
network changes:

```bash
modules/pi-runtime/setup.sh --dry-run /absolute/separate/hermes-pi-runtime
modules/pi-runtime/setup.sh /absolute/separate/hermes-pi-runtime
```

To avoid network access, an existing Git object store may be supplied. It is
read only and must have exactly one `origin` identifying
`NousResearch/hermes-agent` and contain both locked commits:

```bash
modules/pi-runtime/setup.sh \
  --object-store /absolute/path/to/reviewed-hermes-object-store \
  /absolute/separate/hermes-pi-runtime
```

The target must be absent or an exact prior module checkout. Wrong origins,
base commits/branches/tags, symlinked or overlapping paths, staged changes, and
any worktree other than the exact base or exact feature tree fail closed.
Rerunning setup on the exact feature tree is idempotent. The patch is checked
against the manifest, reconstructed independently from the two Git commits,
checked with `git apply --check --whitespace=error-all`, and applied with plain
`git apply --whitespace=error-all`; no three-way fallback exists.

## Verification lanes

Verification always creates an isolated temporary checkout, proves the object
and patch relationships, plain-applies the patch, and compares the resulting
tree to the feature commit. Select either a read-only local object store or an
explicit bounded public fetch.

The default lane resolves only locked dependencies, then executes the
model-free release suite with networking disabled during the test command:

```bash
modules/pi-runtime/verify.sh --uv "$PI_UV" \
  --object-store /absolute/path/to/object-store
# or, explicitly permit bounded public exact-object fetches:
modules/pi-runtime/verify.sh --uv "$PI_UV" --fetch
```

For a completely offline run, the exact uv/Python artifacts and dependency
cache must already exist. An incomplete cache is reported as a gap and exits
nonzero; it is never converted into a pass:

```bash
modules/pi-runtime/verify.sh --uv "$PI_UV" --offline \
  --object-store /absolute/path/to/object-store
```

Docker and reproducibility are explicit, non-default lanes. They require a
native `linux/arm64` Docker host and the exact locally loaded manifest image;
mutable tags and other image IDs are rejected before Docker is invoked:

```bash
IMAGE_ID=sha256:e89f45110e9277902bafbf49009e842bc9e38180e668fea8a6ff3dcdb2dd2cdf
modules/pi-runtime/verify.sh --uv "$PI_UV" \
  --object-store /absolute/path/to/object-store \
  --reproducibility --docker "$IMAGE_ID"
```

Exit code 3 means a requested lane was an honest environment gap (for example,
Docker or an offline cache was unavailable). Every run reports unrequested
Docker/reproducibility lanes and the unavailable authenticated lane as `GAP`,
not `PASS`.

## Manual build and activation (after setup only)

Setup never runs these commands. On a reviewed native ARM64/Linux host, an
operator may explicitly build and inspect the image from the separate checkout:

```bash
cd /absolute/separate/hermes-pi-runtime
agent/transports/pi_assets/build-image.sh docker hermes-pi:0.84.3
docker image inspect --format '{{.Id}}' \
  sha256:e89f45110e9277902bafbf49009e842bc9e38180e668fea8a6ff3dcdb2dd2cdf
```

Only after the ID matches should an operator manually enable `agent.pi_rpc`,
set `docker_image` to that immutable ID, and configure exact provider egress in
the **separate installation's** private config. Do not use a mutable tag.

Pi 0.84.3 interactive OAuth cannot run inside the normal contained RPC profile.
If authentication is deliberately needed, `hermes pi-runtime auth
--acknowledge-trusted-local` is a separate trusted-local host operation. Review
the patched checkout's `references/pi-rpc-runtime.md` first. It is outside
contained execution, is never run by this module, and must never be treated as
verification evidence for the currently missing authenticated E2E lane.

## Rollback

Switch the separate installation back to the native Hermes runtime and stop
using it. Then archive or remove that separate module installation through your
normal recoverable filesystem workflow. Rollback does not reverse or mutate
the stable starter checkout because this module never installs into it.
