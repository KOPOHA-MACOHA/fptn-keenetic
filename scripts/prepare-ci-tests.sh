#!/bin/sh
# Test-runner packages only; never used inside the locked cross-build SDK.
set -eu
if [ "$#" -eq 0 ]; then
    echo 'Usage: prepare-ci-tests.sh PACKAGE...' >&2
    exit 1
fi
# sudo normally drops the proxy environment. Preserve the runner's configured
# route instead of requiring direct DNS/Internet access from the package manager.
export http_proxy="${http_proxy:-${HTTP_PROXY:-${HTTPS_PROXY:-}}}"
export https_proxy="${https_proxy:-${HTTPS_PROXY:-${http_proxy:-}}}"
export no_proxy="${no_proxy:-${NO_PROXY:-}}"
if [ -n "$http_proxy$https_proxy" ]; then
    echo '[CI] APT will use the configured proxy (credentials are not logged).'
else
    echo '[CI] No proxy configured; APT uses the runner network directly.'
fi
# GitHub's mirror+file source can point exclusively at Azure's mirror. Its
# intermittent DNS failures otherwise stall every test job for many minutes.
for mirror in /etc/apt/apt-mirrors.txt /etc/apt/apt-mirrors-*.txt; do
    if [ -f "$mirror" ]; then
        sudo sed -i 's/azure\.archive\.ubuntu\.com/archive.ubuntu.com/g' "$mirror"
    fi
done
sudo --preserve-env=http_proxy,https_proxy,no_proxy apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 \
    -o Acquire::https::Timeout=30 -o APT::Update::Error-Mode=any update -qq
sudo --preserve-env=http_proxy,https_proxy,no_proxy env DEBIAN_FRONTEND=noninteractive apt-get \
    -o Acquire::Retries=2 -o Acquire::http::Timeout=30 \
    -o Acquire::https::Timeout=30 install -y -qq "$@"
