#!/bin/sh
# Reuse PHP already provided by the router web UI; no openssl executable required.
set -eu
ROOT=${FPTN_PREFIX:-/opt}
ACTION=${1:-info}
case "$ACTION" in reset)
  [ "$(id -u)" = 0 ] || { echo 'Требуется root.' >&2; exit 1; } ;;
 info|setup|login|verify|logout) ;;
 *) echo 'Использование: web-auth.sh info|setup|login|verify|logout|reset' >&2; exit 1 ;;
esac
PHP=${FPTN_PHP:-}
if [ -z "$PHP" ]; then
  for candidate in "$ROOT/bin/php-cgi" /opt/bin/php-cgi php-cgi php; do
    if command -v "$candidate" >/dev/null 2>&1; then PHP=$candidate; break; fi
  done
fi
[ -n "$PHP" ] || { echo 'Не найден PHP, используемый существующей панелью nfqws.' >&2; exit 1; }
DIR=$(dirname "$(readlink -f "$0")")
FILE=$DIR/web-auth.php
[ -f "$DIR/fptn-web-auth.php" ] && FILE=$DIR/fptn-web-auth.php
# -n isolates this helper from unrelated PHP extensions/auto-prepend settings.
# The helper is outside the document root and accepts data only on stdin.
FPTN_PREFIX=$ROOT FPTN_AUTH_ACTION=$ACTION REQUEST_METHOD= REDIRECT_STATUS=200 \
  SCRIPT_FILENAME=$FILE SCRIPT_NAME= QUERY_STRING= CONTENT_LENGTH= CONTENT_TYPE= \
  PATH_TRANSLATED= GATEWAY_INTERFACE= HTTP_AUTHORIZATION= \
  "$PHP" -n -q -d cgi.force_redirect=0 -d display_errors=stderr -d log_errors=0 -f "$FILE"
