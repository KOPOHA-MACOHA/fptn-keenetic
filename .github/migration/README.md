# Fork migration checkpoint

The prepared `main` branch contains the upstream source and the complete Keenetic adaptation history. All three clients compiled and passed offline ELF/QEMU checks using existing dependency snapshots. The integrated runtime suite passed 358 tests. The final streaming upload helper additionally passed its four local unit tests.

Next operator step: select `main` as the default branch of `KOPOHA-MACOHA/fptn-keenetic-next` in GitHub settings. Keep both repository names unchanged.

After selecting the default branch, start the guarded release-transfer workflow by adding `.github/migration/start-release-copy` on `migration/releases`. Its repository identities and source-main SHA must match this checkpoint. The workflow preserves original tag objects, verifies every asset against the frozen inventory, and refuses different existing files. Files are streamed one at a time; no dependency builds or router access occur.

Do not rename the repositories before all 64 releases and 369 assets are verified. Historical Actions runs remain attached to the old repository; the publication checker permits read-only fallback to that repository after it is renamed to `fptn-keenetic-test`, checking its numeric identity and the historical runtime ancestry.
