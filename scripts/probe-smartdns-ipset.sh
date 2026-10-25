#!/bin/sh
# Isolated one-query capability test; never edits/restarts the production resolver.
set -eu
export LC_ALL=C LANG=C
umask 077
SET_NAME='_NDM_OGDN_4_@domain-list9'
for cmd in smartdns ipset ndmc timeout nslookup netstat; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Нет команды $cmd" >&2; exit 1; }
done
[ "$(id -u)" = 0 ] || { echo 'Нужен root.' >&2; exit 1; }
ipset list "$SET_NAME" >/dev/null
if netstat -lnptu 2>/dev/null | awk '$4 ~ /:15353$/ {found=1} END {exit !found}'; then
  echo 'Порт 15353 занят; тест не запускался.' >&2; exit 1
fi
WORK=$(mktemp -d "${TMPDIR:-/opt/tmp}/smartdns-ipset-probe.XXXXXX")
PROBE_PID=''
RESTORE=0
cleanup() {
  trap '' INT TERM HUP
  if [ -n "$PROBE_PID" ]; then
    kill -TERM "$PROBE_PID" 2>/dev/null || true
    wait "$PROBE_PID" 2>/dev/null || true
  fi
  if [ "$RESTORE" = 1 ]; then
    echo '[ПРОВЕРКА] Восстанавливаю два временных IP в штатном списке.'
    ndmc -c 'object-group fqdn domain-list9 include 8.6.112.0/32' || true
    ndmc -c 'object-group fqdn domain-list9 include 8.47.69.0/32' || true
  fi
  for log in "$WORK/console.log" "$WORK/probe.log"; do
    [ ! -f "$log" ] || { echo '[ПРОВЕРКА] Журнал временного процесса:'; tail -n 12 "$log"; }
  done
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
cat > "$WORK/probe.conf" <<EOF
server-name fptn-ipset-probe
bind 127.0.0.1:15353
server 127.0.0.1:53
cache-size 0
cache-persist no
cache-file $WORK/probe.cache
speed-check-mode none
dualstack-ip-selection no
log-level warn
log-file $WORK/probe.log
log-size 64k
log-num 1
ipset-timeout no
ipset /api.ipify.org/#4:$SET_NAME
EOF
echo '[ПРОВЕРКА] Временный SmartDNS на 127.0.0.1:15353, не более 15 секунд.'
# BusyBox timeout execs the command; GNU timeout forwards TERM to its child.
timeout -s TERM 15 smartdns -f -c "$WORK/probe.conf" -p "$WORK/probe.pid" > "$WORK/console.log" 2>&1 &
PROBE_PID=$!
sleep 1
kill -0 "$PROBE_PID" 2>/dev/null || { echo 'Временный SmartDNS не запустился.' >&2; exit 1; }
RESTORE=1
ndmc -c 'object-group fqdn domain-list9 no include 8.6.112.0/32'
ndmc -c 'object-group fqdn domain-list9 no include 8.47.69.0/32'
echo '[ПРОВЕРКА] Набор до DNS-запроса:'
ipset list "$SET_NAME"
echo '[ПРОВЕРКА] Один запрос через временный процесс, upstream — действующий SmartDNS на :53.'
timeout -s TERM 8 nslookup api.ipify.org 127.0.0.1:15353
echo '[ПРОВЕРКА] Набор после DNS-запроса:'
ipset list "$SET_NAME"
echo '[ПРОВЕРКА] Если в Members появились адреса, SmartDNS умеет наполнять этот набор.'
echo '[ПРОВЕРКА] Основной SmartDNS и его конфигурация не менялись.'
