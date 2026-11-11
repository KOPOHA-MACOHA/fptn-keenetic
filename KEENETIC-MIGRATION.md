# Keenetic fork migration — preparation branch

This branch starts at upstream FPTN revision
`a389bee62da930807c3bc85284ee4976e9c7e08f`, the exact revision used by the
currently released Keenetic client. It does not upgrade the protocol or dependencies.

The migration imports the complete adaptation history from
`KOPOHA-MACOHA/fptn-keenetic` at `efbf0d550f9a03c437ff03714cbecc9d6f5b6f22`.
Its working tree is preserved byte for byte under `keenetic/`; upstream source
paths remain at the repository root. Both histories are parents of the import.
The upstream workflow is archived under `.github/upstream-workflows/` because
it requires the upstream project's self-hosted runners and publication secrets.
The imported adaptation workflows remain inactive inside `keenetic/.github/`.

**This branch is not an installation or update channel. Do not rename either
repository or install from this branch yet.** Existing releases, feeds, caches,
update URLs, and the working repository are unchanged.

Remaining migration gates:

1. Connect the Keenetic build to the checked-out upstream sources, retaining
   the exact dependency image keys and restoring the existing verified caches.
2. Adapt active workflow paths and publication checks to the combined repository.
3. Preserve historical raw URLs and tags, transfer release assets and feeds,
   and account explicitly for Actions evidence that belongs to the old repository.
4. Test all three architectures and legacy-to-fork installation/update paths.
5. Switch repository names and the default branch only after those checks pass.

The preparation workflow validates the imported runtime suite. It does not
compile a new client, publish releases, alter routers, or write the old repository.
