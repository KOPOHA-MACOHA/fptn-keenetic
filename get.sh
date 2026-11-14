#!/bin/sh
# Buffer the complete bootstrap in a private directory before executing it.
(
  set -eu
  umask 077
  # These are needed before the complete bootstrap can install dependencies.
  for fptn_command in curl mktemp rm sh; do
    command -v "$fptn_command" >/dev/null 2>&1 || {
      echo "FPTN: Не найдена команда $fptn_command для загрузки установщика." >&2
      exit 1
    }
  done
  fptn_setup=$(mktemp -d "${TMPDIR:-/tmp}/fptn-setup.XXXXXX")
  trap 'rm -rf "$fptn_setup"' EXIT
  trap 'exit 1' HUP INT TERM
  curl --proto '=https' --proto-redir '=https' -fLsS --connect-timeout 15 --max-time 120 \
    https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/main/install.sh \
    -o "$fptn_setup/install.sh"
  sh "$fptn_setup/install.sh" install "$@"
)
