#!/bin/sh
# Keep the entry independent of PATH inherited from lighttpd (no cat/dirname).
case "$0" in
  */*) ENTRY=$0 ;;
  *) ENTRY=${SCRIPT_FILENAME:-$0} ;;
esac
case "$ENTRY" in */*) PAGE=${ENTRY%/*}/index.html ;; *) PAGE=./index.html ;; esac
if [ ! -r "$PAGE" ]; then
  printf 'Status: 500 Internal Server Error\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\n\r\nFPTN: index.html is not readable.\n'
  exit 1
fi
printf '%s\r\n' 'Content-Type: text/html; charset=utf-8' \
  'Cache-Control: no-store, max-age=0' 'Pragma: no-cache' \
  'X-Frame-Options: DENY' "Content-Security-Policy: frame-ancestors 'none'" ''
while IFS= read -r LINE || [ -n "$LINE" ]; do printf '%s\n' "$LINE"; done < "$PAGE"
