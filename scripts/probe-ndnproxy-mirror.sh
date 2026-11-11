#!/bin/sh
# Capture in private IPC by default; --native-check opts into fresh live mirrors.
set -eu
export LC_ALL=C LANG=C
umask 077
MODE=${1:-capture}
case "$MODE" in capture|--native-check) ;; *) echo 'Режимы: capture или --native-check' >&2; exit 1;; esac
NATIVE_SOCKET=/var/run/fqdn-dns-mirror.sock
if [ "$MODE" = --native-check ]; then
  command -v ndmc >/dev/null 2>&1 || { echo 'ndmc не найден.' >&2; exit 1; }
  [ -S "$NATIVE_SOCKET" ] || { echo 'Штатный сокет DNS-маршрутизации не найден.' >&2; exit 1; }
fi
for cmd in socat timeout nslookup netstat od; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Нет команды $cmd" >&2; exit 1; }
done
[ "$(id -u)" = 0 ] || { echo 'Нужен root.' >&2; exit 1; }
NDNPROXY=$(command -v ndnproxy || true)
[ -n "$NDNPROXY" ] || NDNPROXY=/usr/sbin/ndnproxy
[ -x "$NDNPROXY" ] || { echo 'ndnproxy не найден.' >&2; exit 1; }
for port in 15453 15454 15455; do
  if netstat -lnptu 2>/dev/null | awk -v p="$port" '$4 ~ (":" p "$") {found=1} END {exit !found}'; then
    echo "Порт $port занят; тест не запускался." >&2; exit 1
  fi
done
mkdir -p /opt/tmp
# Keep the pathname below the Unix-domain socket limit.
WORK=$(mktemp -d /opt/tmp/fqdn-probe.XXXXXX)
MIRROR_PATH=$WORK/mirror.sock
[ "$MODE" != --native-check ] || MIRROR_PATH=$NATIVE_SOCKET
CAPTURE_PID=''
WATCH_PID=''
LAUNCH_PID=''
own_process() {
  case "$1" in ''|*[!0-9]*) return 1;; esac
  [ "$1" -gt 1 ] || return 1
  [ -r "/proc/$1/cmdline" ] || return 1
  tr '\000' '\n' < "/proc/$1/cmdline" 2>/dev/null |
    awk -v conf="$WORK/probe.conf" 'prev=="-c" && $0==conf {found=1} {prev=$0} END {exit !found}'
}
stop_probe() {
  PROBE_PIDS="$(cat "$WORK/probe.pid" 2>/dev/null || true) $LAUNCH_PID"
  # A forked child may not have written its PID file when startup fails.
  if [ -n "$LAUNCH_PID" ] && [ ! -s "$WORK/probe.pid" ]; then
    for cmdline in /proc/[0-9]*/cmdline; do
      candidate=${cmdline%/cmdline}
      candidate=${candidate##*/}
      if own_process "$candidate"; then PROBE_PIDS="$PROBE_PIDS $candidate"; fi
    done
  fi
  for pid in $PROBE_PIDS; do
    if own_process "$pid"; then
      kill -TERM "$pid" 2>/dev/null || true
      n=0
      while own_process "$pid" && [ "$n" -lt 2 ]; do sleep 1; n=$((n+1)); done
      if own_process "$pid"; then kill -KILL "$pid" 2>/dev/null || true; fi
    fi
  done
}
cleanup() {
  trap '' INT TERM HUP
  stop_probe
  for pid in "$WATCH_PID" "$CAPTURE_PID"; do
    [ -z "$pid" ] || { kill -TERM "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; }
  done
  [ -z "$LAUNCH_PID" ] || wait "$LAUNCH_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
cat > "$WORK/probe.conf" <<EOF
rpc_port = 15454
rpc_ttl = 1000
rpc_wait = 1000
timeout = 3000
proceed = 100
stat_file = $WORK/probe.stat
stat_time = 10000
rr_port = 15455
dns_server = 127.0.0.1:53 .
dns_tcp_port = 15453
dns_udp_port = 15453
rpc_only = off
fqdn_mirror_path = $MIRROR_PATH
EOF
: > "$WORK/hosts"
: > "$WORK/safe"
# RECVFROM without fork exits after the request notification. RECV must stay
# listening for the later response notifications, including A/AAAA and CNAMEs.
if [ "$MODE" = capture ]; then
  timeout -s TERM 15 socat -u -b 65535 "UNIX-RECV:$WORK/mirror.sock" STDOUT > "$WORK/message.bin" 2> "$WORK/socat.log" &
  CAPTURE_PID=$!
  n=0
  while [ ! -S "$WORK/mirror.sock" ] && [ "$n" -lt 3 ]; do sleep 1; n=$((n+1)); done
  [ -S "$WORK/mirror.sock" ] || { cat "$WORK/socat.log"; echo 'Временный сокет не создан.' >&2; exit 1; }
  echo '[ПРОВЕРКА] Зеркало — только в приватный временный сокет.'
else
  echo '[ПРОВЕРКА] До запроса: штатная группа domain-list9.'
  ndmc -c 'show object-group fqdn domain-list9'
  echo '[ПРОВЕРКА] Свежие сообщения штатного ndnproxy об api.ipify.org пойдут в рабочий обработчик DNS-маршрутизации.'
  echo '[ПРОВЕРКА] Настройки группы, ручные IP, маршруты и основной DNS не изменяются.'
fi
echo '[ПРОВЕРКА] Отдельный ndnproxy: DNS :15453, RPC :15454, rr_port :15455.'
echo '[ПРОВЕРКА] Upstream — действующий SmartDNS :53.'
"$NDNPROXY" -c "$WORK/probe.conf" -p "$WORK/probe.pid" -f "$WORK/hosts" -s "$WORK/safe" > "$WORK/ndnproxy.log" 2>&1 &
LAUNCH_PID=$!
# Independent deadline also handles ndnproxy daemonizing away from the launcher.
(
  sleep 15 &
  TIMER_PID=$!
  trap 'kill -TERM "$TIMER_PID" 2>/dev/null || true; wait "$TIMER_PID" 2>/dev/null || true; exit 0' INT TERM HUP
  wait "$TIMER_PID"
  stop_probe
) >/dev/null 2>&1 &
WATCH_PID=$!
n=0
READY=0
while [ "$n" -lt 3 ]; do
  if netstat -lnptu 2>/dev/null | awk '$1=="udp" && $4 ~ /:15453$/ {found=1} END {exit !found}'; then READY=1; break; fi
  sleep 1
  n=$((n+1))
done
if [ "$READY" != 1 ]; then
  echo '[ПРОВЕРКА] Временный DNS не начал слушать :15453.' >&2
  cat "$WORK/ndnproxy.log"
  exit 1
fi
timeout -s TERM 6 nslookup api.ipify.org 127.0.0.1:15453 > "$WORK/answer" 2>&1 || true
cat "$WORK/answer"
if [ "$MODE" = --native-check ]; then
  echo '[ПРОВЕРКА] Сразу после ответа: адреса внутри entry с fqdn api.ipify.org.'
  ndmc -c 'show object-group fqdn domain-list9'
  echo '[ПРОВЕРКА] Проверяется обучение FQDN, а не уже существующие ручные IP. Готовый мост SmartDNS пока не установлен.'
  exit 0
fi
# The first message can arrive before the DNS response. Do not stop at nonempty.
sleep 3
stop_probe
# Freeze the stream before measuring or dumping it. Serialized XML documents
# are concatenated; original datagram boundaries are not preserved in this file.
kill -TERM "$CAPTURE_PID" 2>/dev/null || true
wait "$CAPTURE_PID" 2>/dev/null || true
CAPTURE_PID=''
if [ ! -s "$WORK/message.bin" ]; then
  echo '[ПРОВЕРКА] Сообщение зеркалирования не получено. Это не подтверждает совместимость.' >&2
  cat "$WORK/ndnproxy.log" "$WORK/socat.log"
  exit 1
fi
echo "[ПРОВЕРКА] Захвачена последовательность сообщений: $(wc -c < "$WORK/message.bin") байт. Первые 4096 байт (восьмеричные значения):"
dd if="$WORK/message.bin" bs=4096 count=1 2>/dev/null | od -b -v
echo '[ПРОВЕРКА] Это образец протокола, а не проверка готовой доменной маршрутизации.'
echo '[ПРОВЕРКА] Временный ndnproxy остановлен; настройки и рабочие сокеты не изменялись.'
