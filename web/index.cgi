#!/bin/sh
# Serve the entry page without a browser cache, retaining the plain :1111/ URL.
printf 'Content-Type: text/html; charset=utf-8\r\nCache-Control: no-store, max-age=0\r\nPragma: no-cache\r\nX-Frame-Options: DENY\r\nContent-Security-Policy: frame-ancestors '\''none'\''\r\n\r\n'
cat "$(dirname "$0")/index.html"
