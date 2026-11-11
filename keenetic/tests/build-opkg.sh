#!/bin/sh
# Development-only native opkg for offline database/package format tests.
set -eu
: "${RUNNER_TEMP:?}"
mkdir -p "$RUNNER_TEMP/opkg-source" "$RUNNER_TEMP/opkg-bin"
curl --proto '=https' --proto-redir '=https' -fsSL --retry 3 --max-time 60 \
  https://downloads.yoctoproject.org/releases/opkg/opkg-0.7.0.tar.gz -o "$RUNNER_TEMP/opkg-source.tar.gz"
printf '%s  %s\n' d973fd0f1568f58f87d6aecd9aa95e3e1f60214a45cee26704bf8fe757c54567 "$RUNNER_TEMP/opkg-source.tar.gz" | sha256sum -c -
tar -xzf "$RUNNER_TEMP/opkg-source.tar.gz" --strip-components=1 -C "$RUNNER_TEMP/opkg-source"
cd "$RUNNER_TEMP/opkg-source"
./configure --prefix="$RUNNER_TEMP/opkg-bin" --disable-gpg --disable-ssl-curl --with-opkglibdir=/var/lib/opkg > configure.log
make -j2 > build.log
make install > install.log
printf 'FPTN_TEST_OPKG=%s\n' "$RUNNER_TEMP/opkg-bin/bin/opkg" >> "$GITHUB_ENV"
