#!/bin/bash
# Keep completed Conan packages across bounded retries of transient HTTP failures.
set -euo pipefail
log=$(mktemp)
trap 'rm -f "$log"' EXIT
for attempt in 1 2 3; do
    set +e
    conan install . --output-folder=/build --build=missing \
        --lockfile=/work/build-locks/conan.lock \
        --profile:build=default --profile:host=/work/profile -s:b compiler.cppstd=20 \
        -o with_gui_client=False -o openwrt=True 2>&1 | tee "$log"
    status=${PIPESTATUS[0]}
    set -e
    if [ "$status" -eq 0 ]; then exit 0; fi
    if [ "$attempt" -eq 3 ] || ! grep -Eq '(ConanException|ERROR:).*Error downloading file.*(too many 50[234] error responses|Read timed out|Connection reset)' "$log"; then
        exit "$status"
    fi
    echo "Transient source download failure; retry $attempt/2 with completed packages retained."
    sleep 10
done
