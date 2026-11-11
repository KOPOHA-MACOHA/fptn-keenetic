#!/bin/sh
# Isolated one-query capability test; never edits/restarts the production resolver.
set -eu
export LC_ALL=C LANG=C
umask 077
SET_NAME="fptn_probe_4_@$$"
for cmd in smartdns ipset timeout nslookup netstat; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Нет команды $cmd" >&2; exit 1; }
done
[ "$(id -u)" = 0 ] || { echo 'Нужен root.' >&2; exit 1; }
if netstat -lnptu 2>/dev/null | awk '$4 ~ /:15353$/ {found=1} END {exit !found}'; then
  echo 'Порт 15353 занят; тест не запускался.' >&2; exit 1
fi
WORK=$(mktemp -d "${TMPDIR:-/opt/tmp}/smartdns-ipset-probe.XXXXXX")
PROBE_PID=''
SET_CREATED=0
cleanup() {
  trap '' INT TERM HUP
  if [ -n "$PROBE_PID" ]; then
    kill -TERM "$PROBE_PID" 2>/dev/null || true
    wait "$PROBE_PID" 2>/dev/null || true
  fi
  CLEAN_FAILED=0
  if [ "$SET_CREATED" = 1 ]; then
    if ipset destroy "$SET_NAME"; then
      echo "[ПРОВЕРКА] Временный набор $SET_NAME удалён."
    else
      echo "[ПРОВЕРКА] Не удалось удалить временный набор $SET_NAME." >&2
      CLEAN_FAILED=1
    fi
  fi
  for log in "$WORK/console.log" "$WORK/probe.log"; do
    [ ! -f "$log" ] || { echo '[ПРОВЕРКА] Журнал временного процесса:'; tail -n 12 "$log"; }
  done
  rm -rf "$WORK"
  [ "$CLEAN_FAILED" = 0 ] || exit 1
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
if ipset list "$SET_NAME" >/dev/null 2>&1; then
  echo "Проверочный набор $SET_NAME уже существует; не изменяю его." >&2; exit 1
fi
ipset create "$SET_NAME" hash:net family inet hashsize 64 maxelem 64
SET_CREATED=1
ipset save "$SET_NAME" > "$WORK/before.set"
if awk '$1=="add" {found=1} END {exit !found}' "$WORK/before.set"; then
  echo 'Проверочный набор не пуст до запуска — результат был бы неоднозначным.' >&2; exit 1
fi
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
echo '[ПРОВЕРКА] Набор до DNS-запроса:'
ipset list "$SET_NAME"
echo '[ПРОВЕРКА] Один запрос через временный процесс, upstream — действующий SmartDNS на :53.'
timeout -s TERM 8 nslookup api.ipify.org 127.0.0.1:15353 > "$WORK/answer"
cat "$WORK/answer"
echo '[ПРОВЕРКА] Набор после DNS-запроса:'
ipset list "$SET_NAME"
ipset save "$SET_NAME" > "$WORK/after.set"
awk '/^Name:/ {answer=1} answer && /^Address/ {for(i=2;i<=NF;i++) if($i~/^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) print $i}' "$WORK/answer" > "$WORK/answer.ips"
if awk 'FILENAME==ARGV[1] {answers[$1]=1; next} $1=="add" {split($3,a,"/"); if(a[1] in answers) found=1} END {exit !found}' "$WORK/answer.ips" "$WORK/after.set"; then
  echo '[ПРОВЕРКА] PASS: IP из DNS-ответа появился в изначально пустом проверочном наборе.'
else
  echo '[ПРОВЕРКА] Запись IP из DNS-ответа в проверочный набор не подтверждена.' >&2
  exit 1
fi
echo '[ПРОВЕРКА] Рабочий набор Keenetic, его списки и основной SmartDNS не изменялись.'
