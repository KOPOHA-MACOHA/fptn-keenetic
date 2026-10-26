#!/bin/sh
# Read-only CLI discovery. No token, connection, network scan or Python dependency.
set -eu
umask 077
export LC_ALL=C LANG=C
ROOT=${FPTN_PREFIX:-/opt}
CLIENT=${1:-$ROOT/libexec/fptn/fptn-client-cli}
CACHE=$ROOT/var/run/fptn-capabilities
fail() { echo "FPTN capabilities: $*" >&2; exit 1; }
fingerprint() {
  if command -v stat >/dev/null 2>&1; then stat -Lc '%d:%i:%s:%Y:%Z' "$CLIENT"
  else busybox stat -Lc '%d:%i:%s:%Y:%Z' "$CLIENT"; fi
}
[ -x "$CLIENT" ] || fail 'Клиент не установлен.'
KEY=$(fingerprint) || fail 'Не удалось определить файл клиента.'
mkdir -p "$CACHE"; chmod 700 "$CACHE"
if [ -r "$CACHE/current.json" ] && [ "$(wc -c < "$CACHE/current.json")" -le 65536 ] &&
  jq -e --arg key "$KEY" '.schema==1 and .fingerprint==$key and (.methods|type=="array" and length>0) and (.strategies|type=="array" and length>0)' "$CACHE/current.json" >/dev/null 2>&1; then
  jq 'del(.fingerprint)' "$CACHE/current.json"; exit 0
fi
TMP=$(mktemp -d "$CACHE/read.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
trap 'exit 1' INT TERM HUP
# Bound both runtime and help output if a future CLI changes its behavior.
(ulimit -f 256; nice -n 10 timeout -s KILL 5 "$CLIENT" --help > "$TMP/help") || fail '--help не завершился успешно.'
[ "$(wc -c < "$TMP/help")" -le 131072 ] || fail 'Слишком большой вывод --help.'
awk '{gsub(/[^a-z0-9-]/," "); for(i=1;i<=NF;i++) {
  if($i ~ /^sni-spoofing-/ && $i !~ /^sni-spoofing-[a-z][a-z0-9]*-[0-9]+(-[0-9]+)*$/) exit 1
  if($i=="obfuscation" || $i ~ /^sni-spoofing-[a-z][a-z0-9]*-[0-9]+(-[0-9]+)*$/) {
    if(length($i)>128) exit 1
    if(!seen[$i]++) print $i
  }
}}' "$TMP/help" > "$TMP/methods" || fail 'Изменился формат методов в --help.'
awk '{gsub(/[^a-z0-9-]/," "); for(i=1;i<=NF;i++) {
  if($i ~ /rolling-tunnel$/ && $i!="rolling-tunnel" && $i !~ /^[a-z][a-z0-9]*-rolling-tunnel$/) exit 1
  if($i=="rolling-tunnel" || $i ~ /^[a-z][a-z0-9]*-rolling-tunnel$/) {
    if(length($i)>128) exit 1
    if(!seen[$i]++) print $i
  }
}}' "$TMP/help" > "$TMP/strategies" || fail 'Изменился формат стратегий в --help.'
METHODS=$(jq -Rs 'split("\n")|map(select(length>0))' "$TMP/methods")
STRATEGIES=$(jq -Rs 'split("\n")|map(select(length>0))' "$TMP/strategies")
printf '%s' "$METHODS" | jq -e 'length>0 and length<=256 and index("obfuscation")!=null' >/dev/null || fail 'В --help нет ожидаемого списка методов.'
printf '%s' "$STRATEGIES" | jq -e 'length>0 and length<=32' >/dev/null || fail 'В --help нет ожидаемого списка стратегий.'
(ulimit -f 256; nice -n 10 timeout -s KILL 5 "$CLIENT" --version > "$TMP/version") || fail '--version не завершился успешно.'
VERSION=$(head -c 129 "$TMP/version")
[ "${#VERSION}" -le 128 ] || fail 'Слишком длинная версия клиента.'
printf '%s\n' "$VERSION" | awk 'NR!=1 || $0 !~ /^[0-9]+\.[0-9]+\.[0-9]+([a-zA-Z0-9.+-]*)$/ {exit 1}' || fail 'Неизвестный формат версии клиента.'
SUM=$(nice -n 10 sha256sum "$CLIENT" | awk '{print $1}')
[ "$(fingerprint)" = "$KEY" ] || fail 'Клиент заменился во время проверки; повторите запрос.'
jq -n --arg key "$KEY" --arg version "$VERSION" --arg sum "$SUM" --argjson methods "$METHODS" --argjson strategies "$STRATEGIES" \
  '{schema:1,fingerprint:$key,clientVersion:$version,sha256:$sum,methods:$methods,strategies:$strategies}' > "$TMP/capabilities.json"
mv "$TMP/capabilities.json" "$CACHE/current.json"
jq 'del(.fingerprint)' "$CACHE/current.json"
