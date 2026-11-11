# Completed GitHub fork migration

`KOPOHA-MACOHA/fptn-keenetic` is now the real fork of `fptn-project/fptn` (repository ID 1453702975, default branch `main`). The former independent repository is `KOPOHA-MACOHA/fptn-keenetic-test` (ID 1432556444), retained for historical Actions evidence. Its README points to the current project and its obsolete scheduled upstream job is skipped.

Both Git histories and all 64 original tag objects were preserved. Release-transfer run 42997776470 verified all 64 historical releases and 369 assets (10,100,002,162 bytes). Run 43001124635 verified public metadata downloads and removed only the 37 unchanged temporary history refs; original commits remain reachable through the preserved tags and histories.

The standard workflows compiled MIPS, ARM64 and ARMv7 clients from commit `463469320ae4bfef7f96018de66be88be3ae9acb` (runs 43005482182 and 43005482184). Runtime checks passed 365 tests (43005482094). Publication 43006941055 validated and published runtime `48a4ceb512ca91f6cb9fc051f9469e45ba732b32`; user release 43008057868 published `v0.18.4-655`. Panel and client versions remain 0.18.4 and 0.8.0.

Platform probes and ARM token decoders now reuse the verified dependency toolchains instead of rebuilding the Ubuntu toolchain. The dependency-stage Dockerfile prefix, locked versions and all three snapshot keys were preserved. Fresh-runner dependency reuse passed in 43006940838. Deployment sources participate in the compiled-input fingerprint; Markdown follows the existing documentation-only CI skip policy.

Canonical verification 43008563685 checked the newly published runtime evidence, historical assets, ten public metadata downloads and the pinned installer checksum, plus three build-input and 27 publication tests. Earlier verification 43002941266 exercised the historical-runtime proof through the renamed legacy repository. There are no remaining repository rename steps. Keep the historical repository while its Actions history is needed; Actions run IDs cannot be moved between repositories.
