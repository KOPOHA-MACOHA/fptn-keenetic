#!/bin/sh
# Manual idle connection test for Keenetic. No service or routing installation.
set -eu
umask 077
ulimit -c 0
cd "$(dirname "$0")"
for tool in ip timeout nice stty; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Missing command: $tool" >&2; exit 1; }
done
if ip link show dev fptn0 >/dev/null 2>&1; then
  echo 'fptn0 already exists; refusing to touch an existing interface.' >&2
  exit 1
fi
# Read the existing main default. Never replace or delete it.
FPTN_GATEWAY=$(ip -4 route show table main default | awk '$1=="default" && $2=="via" {print $3; exit}')
FPTN_INTERFACE=$(ip -4 route show table main default | awk '$1=="default" {for(i=1;i<NF;i++) if($i=="dev") {print $(i+1); exit}}')
[ -n "$FPTN_GATEWAY" ] && [ -n "$FPTN_INTERFACE" ] || { echo 'Cannot detect main gateway/interface.' >&2; exit 1; }
printf 'Preferred server name (one server, as shown in your Android app): '
IFS= read -r FPTN_SERVER
[ -n "$FPTN_SERVER" ] || { echo 'Server name must not be empty.' >&2; exit 1; }
FPTN_TTY_STATE=$(stty -g)
trap 'stty "$FPTN_TTY_STATE" 2>/dev/null || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'FPTN access token (hidden input): '
stty -echo
IFS= read -r FPTN_ACCESS_TOKEN
stty "$FPTN_TTY_STATE"
printf '\n'
[ -n "$FPTN_ACCESS_TOKEN" ] || { echo 'Token must not be empty.' >&2; exit 1; }
echo 'Starting one idle tunnel for at most 90 seconds. No routes/DNS/firewall are configured.'
echo 'Ctrl+C stops the test; timeout exit 124 at the end is expected.'
nice -n 10 timeout -s TERM 90 ./fptn-client-cli \
  --keenetic-connect \
  --access-token "$FPTN_ACCESS_TOKEN" \
  --preferred-server "$FPTN_SERVER" \
  --out-network-interface "$FPTN_INTERFACE" \
  --gateway-ip "$FPTN_GATEWAY" \
  --tun-interface-name fptn0 \
  --enable-split-tunnel false \
  --enable-ad-block false \
  --blacklist-domains '' \
  --connection-strategy rolling-tunnel
