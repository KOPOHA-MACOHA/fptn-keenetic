#!/bin/sh
# Test-runner packages only; never used inside the locked cross-build SDK.
set -eu
if [ "$#" -eq 0 ]; then
    echo 'Usage: prepare-ci-tests.sh PACKAGE...' >&2
    exit 1
fi
# GitHub's mirror+file source can point exclusively at Azure's mirror. Its
# intermittent DNS failures otherwise stall every test job for many minutes.
for mirror in /etc/apt/apt-mirrors.txt /etc/apt/apt-mirrors-*.txt; do
    if [ -f "$mirror" ]; then
        sudo sed -i 's/azure\.archive\.ubuntu\.com/archive.ubuntu.com/g' "$mirror"
    fi
done
sudo apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 \
    -o Acquire::https::Timeout=30 -o APT::Update::Error-Mode=any update -qq
sudo env DEBIAN_FRONTEND=noninteractive apt-get \
    -o Acquire::Retries=2 -o Acquire::http::Timeout=30 \
    -o Acquire::https::Timeout=30 install -y -qq "$@"
