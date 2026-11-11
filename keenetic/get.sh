#!/bin/sh
# Buffer the complete bootstrap in a private directory before executing it.
(
  set -eu
  umask 077
  fptn_setup=$(mktemp -d "${TMPDIR:-/tmp}/fptn-setup.XXXXXX")
  trap 'rm -rf "$fptn_setup"' EXIT
  trap 'exit 1' HUP INT TERM
  curl --proto '=https' --proto-redir '=https' -fLsS --connect-timeout 15 --max-time 120 \
    https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/main/install.sh \
    -o "$fptn_setup/install.sh"
  sh "$fptn_setup/install.sh" install "$@"
)
