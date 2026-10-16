# Upstream FPTN compatibility and release sync

Upstream repository: https://github.com/fptn-project/fptn

## Policy

The Keenetic fork should keep its router-specific integration separate from the upstream FPTN client code:

- Upstream client/protocol/library changes should be brought in from official release tags.
- Keenetic installer, Lighttpd/PHP web panel, Entware integration, init scripts, monitoring, and router-specific tests remain maintained in this fork.
- Upstream release assets for OpenWrt are **not** installed directly on Keenetic. OpenWrt packages target a different OS/package/service environment and do not replace the Entware/MIPS client binary. Rebuild the CLI from the merged source using this repository's `build-keenetic.yml` workflow.
- The Keenetic package version in `deploy/keenetic/version.txt` is a fork/installer release identifier. Do not blindly replace it with the upstream semantic version: the installer currently uses it for legacy fallback URLs. Upstream source version and Keenetic installer version need to remain distinct until release metadata is redesigned.

## Automation

`.github/workflows/upstream-release-sync.yml` checks the official repository's latest non-draft release weekly and on manual dispatch. When it finds a release tag that is not already included in `master`, it attempts to merge that tag into a separate branch and opens a pull request.

The workflow does **not** push the merge directly to `master`, publish a Keenetic binary, or update a router. If the merge has conflicts, it opens an issue instead of guessing how to reconcile Keenetic-specific changes.

For GitHub to open pull requests using `GITHUB_TOKEN`, repository Actions settings may need **Allow GitHub Actions to create and approve pull requests** enabled. If this setting is disabled, the sync job will fail at PR creation; it still must not be treated as a successful sync.

## Required checks before merging a sync PR

1. Review upstream changes affecting CLI flags, config format, networking, routing restoration, DNS, TUN handling, and reconnect behavior.
2. Run Keenetic shell/PHP validation and the cross-architecture build workflow.
3. Verify that the MIPS binary is compatible with the router's `mipsel-3.4_kn` Entware userspace. A successful compile or `--help` smoke test alone is not sufficient.
4. Verify that `--show-servers` and every CLI option used by the web panel/service still exist and behave as expected.
5. Do not install automatically. Router runtime testing remains a separate, explicit step with a rollback plan.

## Current upstream baseline

At the time this policy was added, the official latest release was [0.4.6](https://github.com/fptn-project/fptn/releases/tag/0.4.6), published on 2026-10-03. This is an observation, not a claim that the Keenetic fork has already been updated to 0.4.6.
