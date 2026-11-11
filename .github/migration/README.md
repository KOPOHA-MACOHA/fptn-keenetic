# Fork migration checkpoint

The fork preserves the upstream history and the complete Keenetic adaptation history. The client compiled for MIPS, ARM64 and ARMv7 using the existing dependency snapshots; ELF/QEMU checks passed (run 42992353429). The integrated runtime suite passed 358 tests (run 42993467275).

Release transfer completed in run 42997776470: all 64 releases, 369 assets and 10,100,002,162 bytes match the frozen inventory by name, size and SHA-256. All 64 original tag objects match exactly. The normal Latest release remains `v0.18.4-128`.

Run 43001124635 checked ten public installation metadata downloads without authentication, verified historical Actions access, and removed only the 37 unchanged temporary history refs after checking the original tags. Original commits remain reachable through those tags and the imported histories.

Next operator step, in order: rename the old repository to `fptn-keenetic-test`, then rename this fork from `fptn-keenetic-next` to `fptn-keenetic`. Keep `main` as the default branch. Do not delete the old repository: historical Actions IDs remain attached to its numeric identity, and the publication checker has a constrained read-only fallback for those old proofs.

After both renames, verify the numeric repository identities, public installation/update URLs, Latest release and archived Actions evidence again. The rename itself has not been performed by the migration scripts. Router settings, installed service and update-channel contents were not changed during preparation.
