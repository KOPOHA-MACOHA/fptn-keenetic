# Keenetic fork migration — staging branch

This is a real GitHub fork of `fptn-project/fptn`. The integration starts at
`a389bee62da930807c3bc85284ee4976e9c7e08f`, the exact upstream revision used by
the released Keenetic client. Upstream sources keep their original paths.
Keenetic scripts, panel, profiles, and tests are added at their existing paths,
so historical installer and update URLs do not need a directory migration.
Both the upstream history and the full adaptation history are preserved.

The dependency-stage Dockerfile prefix, source locks, and all three dependency
image keys are unchanged. `export-fork-source.py` exports committed local fork
sources for the client stage. Changes to recipes and dependency CMake files are
rejected until the upstream baseline and reviewed locks are updated together.
The release fingerprint includes the original CMake and dependency inputs too.

The upstream CI workflow is archived under `.github/upstream-workflows/` because
it requires upstream-owned self-hosted runners and publication secrets.
The migration validation workflow runs the full Keenetic runtime suite and
compiles the client for MIPS, ARM64 and ARMv7 using existing verified snapshots.
It fails when a snapshot is unavailable; it never rebuilds dependencies.
It does not publish releases, change the old repository or contact routers/VPNs.

**Not an installation or update channel yet. Do not rename either repository.**

Remaining cutover gates:

1. Confirm runtime checks and all three offline compilation/emulation jobs.
2. Preserve old tags and release assets, indexes and update-channel references.
3. Verify installation/update paths against the fork and account explicitly for
   Actions run evidence that remains attached to the old repository.
4. Select the prepared default branch, then switch names only after these gates.

Stable upstream updates remain reviewed draft PRs. A source-lock update alone
cannot replace checked-out fork sources: the exporter rejects a new baseline
that has not actually been merged into the fork. A dependency lock refresh is
still required before compiling an updated baseline; nothing is auto-merged.
