#!/bin/sh
# FPTN transport only. KeeneticOS owns OpkgTun0, routes, DNS and firewall.
set -eu
export LC_ALL=C LANG=C
umask 077
ulimit -c 0
SELF=$(readlink -f "$0")
ROOT=${FPTN_PREFIX:-/opt}
CONF=$ROOT/etc/fptn
LIB=$ROOT/libexec/fptn
RUN=$ROOT/var/run/fptn-keenetic
UPSTREAM_LOG=/var/log/fptn/fptn-client-cli.log
[ "$ROOT" = /opt ] || UPSTREAM_LOG=$ROOT/var/log/fptn/fptn-client-cli.log
fail() { echo "FPTN: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" = 0 ] || fail 'Нужен root.'; }
proc_start() {
  [ -r "/proc/$1/stat" ] || return 1
  awk '{sub(/^.*\) /, ""); print $20}' "/proc/$1/stat"
}

record_pid() { printf '%s %s %s\n' "$1" "$(proc_start "$1")" "$(cat /proc/sys/kernel/random/boot_id)" > "$2"; }

alive() {
  [ -f "$1" ] || return 1
  read -r PID STAMP BOOT < "$1" || return 1
  [ -n "$BOOT" ] && [ "$BOOT" = "$(cat /proc/sys/kernel/random/boot_id)" ] || return 1
  case "$PID:$STAMP" in *[!0-9:]*|:|*:|:*) return 1 ;; esac
  [ "$PID" -gt 1 ] || return 1
  [ "$(proc_start "$PID" 2>/dev/null || true)" = "$STAMP" ] && kill -0 "$PID" 2>/dev/null
}

client_alive() {
  alive "$RUN/client.pid" || return 1
  [ "$(readlink "/proc/$PID/exe" 2>/dev/null || true)" = "$(readlink -f "$LIB/fptn-client-cli")" ]
}

trim_logs() {
  # Existing ELF also writes its own rotating log; cap that active file too.
  for log in "$RUN/client.log" "$RUN/supervisor.log" "$UPSTREAM_LOG"; do
    if [ -f "$log" ] && [ ! -L "$log" ] && [ "$(wc -c < "$log")" -gt 131072 ]; then
      : > "$log"
    fi
  done
}

# JSON is data, never sourced as shell code. Older jq builds need no regex support.
validate_connection() {
  SETTINGS=$(head -c 16385)
  [ "${#SETTINGS}" -le 16384 ] || fail 'Слишком большие настройки.'
  printf '%s' "$SETTINGS" | jq -e 'type=="object" and
    (.bypass|type=="string") and (.strategy|type=="string") and
    (.sni|type=="string")' >/dev/null 2>&1 || fail 'Некорректный JSON настроек.'
  BYPASS=$(printf '%s' "$SETTINGS" | jq -r '.bypass')
  STRATEGY=$(printf '%s' "$SETTINGS" | jq -r '.strategy')
  SNI=$(printf '%s' "$SETTINGS" | jq -r '.sni')
  case "$BYPASS" in
    obfuscation|sni-spoofing-chrome-149|sni-spoofing-chrome-148|sni-spoofing-chrome-147|sni-spoofing-chrome-146|sni-spoofing-chrome-145|sni-spoofing-firefox-151|sni-spoofing-firefox-150|sni-spoofing-firefox-149|sni-spoofing-yandex-26-4|sni-spoofing-yandex-26-3|sni-spoofing-yandex-25|sni-spoofing-yandex-24|sni-spoofing-safari-26-5|sni-spoofing-safari-26-4) ;;
    *) fail 'Неизвестный метод обхода.' ;;
  esac
  case "$STRATEGY" in rolling-tunnel|dual-rolling-tunnel|triple-rolling-tunnel) ;; *) fail 'Неизвестная стратегия.' ;; esac
  printf '%s\n' "$SNI" | awk '
    NR!=1 {exit 1}
    length($0)<1 || length($0)>253 || $0 ~ /[^a-zA-Z0-9.-]/ {exit 1}
    {n=split($0,a,"."); if(n<2) exit 1
     for(i=1;i<=n;i++) if(length(a[i])<1 || length(a[i])>63 || a[i] ~ /^-|-$/) exit 1}
  ' || fail 'Укажите корректный домен SNI латиницей.'
  jq -n --arg bypass "$BYPASS" --arg strategy "$STRATEGY" --arg sni "$SNI" \
    '{bypass:$bypass,strategy:$strategy,sni:$sni}'
}
connection_json() {
  if [ -f "$CONF/connection.json" ]; then validate_connection < "$CONF/connection.json"
  else printf '%s\n' '{"bypass":"obfuscation","strategy":"dual-rolling-tunnel","sni":"google.com"}'
  fi
}
load_connection() {
  SETTINGS=$(connection_json)
  BYPASS=$(printf '%s' "$SETTINGS" | jq -r '.bypass')
  STRATEGY=$(printf '%s' "$SETTINGS" | jq -r '.strategy')
  SNI=$(printf '%s' "$SETTINGS" | jq -r '.sni')
}

configure() {
  [ ! -d "$RUN" ] || fail 'Сначала stop; при остаточном состоянии — recover.'
  TMP=$(mktemp -d "$CONF/setup.XXXXXX")
  TTY=''
  tidy_setup() { [ -z "$TTY" ] || stty "$TTY" 2>/dev/null || true; rm -rf "$TMP"; }
  trap tidy_setup EXIT
  trap 'exit 130' INT TERM HUP
  TTY=$(stty -g)
  printf 'Вставьте токен FPTN (ввод скрыт): '
  stty -echo; IFS= read -r TOKEN; stty "$TTY"; TTY=''; printf '\n'
  cd "$LIB"
  FPTN_ACCESS_TOKEN=$TOKEN
[ -n "$FPTN_ACCESS_TOKEN" ] && [ "${#FPTN_ACCESS_TOKEN}" -le 262144 ] || { echo 'Пустой или слишком большой токен.' >&2; exit 1; }
FPTN_CLEAN=$(printf '%s' "$FPTN_ACCESS_TOKEN" | tr -d '[:space:]')
FPTN_COMPRESSED=0
case "$FPTN_CLEAN" in
  fptnb:*) FPTN_PAYLOAD=${FPTN_CLEAN#fptnb:}; FPTN_COMPRESSED=1 ;;
  fptnb//*) FPTN_PAYLOAD=${FPTN_CLEAN#fptnb//}; FPTN_COMPRESSED=1 ;;
  fptn://*) FPTN_PAYLOAD=${FPTN_CLEAN#fptn://} ;;
  fptn:*) FPTN_PAYLOAD=${FPTN_CLEAN#fptn:} ;;
  *) FPTN_PAYLOAD=$FPTN_CLEAN ;;
esac
FPTN_PAYLOAD=$(printf '%s' "$FPTN_PAYLOAD" | tr -d '=')
case $((${#FPTN_PAYLOAD} % 4)) in
  0) ;;
  2) FPTN_PAYLOAD="${FPTN_PAYLOAD}==" ;;
  3) FPTN_PAYLOAD="${FPTN_PAYLOAD}=" ;;
  *) echo 'Некорректная длина токена.' >&2; exit 1 ;;
esac
if [ "$FPTN_COMPRESSED" -eq 1 ]; then
  [ -f ./brotli ] || { echo 'В папке отсутствует декодер brotli.' >&2; exit 1; }
  chmod 700 brotli
  if ! FPTN_CONFIG=$(printf '%s' "$FPTN_PAYLOAD" | base64 -d | nice -n 10 timeout -s TERM 10 ./brotli -d -c 2>/dev/null | head -c 1048577); then
    echo 'Не удалось распаковать токен.' >&2; exit 1
  fi
else
  if ! FPTN_CONFIG=$(printf '%s' "$FPTN_PAYLOAD" | base64 -d 2>/dev/null); then
    echo 'Не удалось прочитать токен.' >&2; exit 1
  fi
fi
if [ "${#FPTN_CONFIG}" -gt 1048576 ] || ! printf '%s' "$FPTN_CONFIG" | jq -e '
  type == "object" and (.servers | type == "array") and (.servers | length > 0)
  and all(.servers[]; .name | type == "string" and length > 0
    and all(explode[]; . >= 32 and . != 127))' >/dev/null 2>&1; then
  echo 'В токене не найден корректный список серверов.' >&2; exit 1
fi
FPTN_COUNT=$(printf '%s' "$FPTN_CONFIG" | jq -r '.servers | length')
echo 'Серверы из вашего токена:'
printf '%s' "$FPTN_CONFIG" | jq -r '.servers | to_entries[] | "  \(.key+1)) \(.value.name)"'
while :; do
  if [ "$FPTN_COUNT" -eq 1 ]; then
    FPTN_CHOICE=1
  else
    printf 'Номер сервера [Enter — 1, q — выход]: '
    IFS= read -r FPTN_CHOICE
    FPTN_CHOICE=${FPTN_CHOICE:-1}
  fi
  case "$FPTN_CHOICE" in
    q|Q) exit 0 ;;
    *[!0-9]*) echo 'Введите номер из списка.'; continue ;;
  esac
  if [ "${#FPTN_CHOICE}" -gt 6 ] || [ "$FPTN_CHOICE" -lt 1 ] || [ "$FPTN_CHOICE" -gt "$FPTN_COUNT" ]; then
    echo 'Такого номера нет в списке.'; continue
  fi
  FPTN_SERVER=$(printf '%s' "$FPTN_CONFIG" | jq -r --arg n "$FPTN_CHOICE" '.servers[($n | tonumber)-1].name')
  case "$FPTN_SERVER" in
    *,*) echo 'Этот сервер содержит запятую в имени: старый клиент не поддерживает такой выбор.' >&2; exit 1 ;;
  esac
  break
done
unset FPTN_CONFIG FPTN_CLEAN FPTN_PAYLOAD
printf 'Выбран сервер: %s\n' "$FPTN_SERVER"
  printf '%s\n' "$TOKEN" > "$TMP/token"
  printf '%s\n' "$FPTN_SERVER" > "$TMP/server"
  mv "$TMP/token" "$CONF/token"
  mv "$TMP/server" "$CONF/server"
  unset TOKEN FPTN_ACCESS_TOKEN
  echo 'Токен и сервер сохранены. CIDR и домены задаются в интерфейсе Keenetic.'
}
setup_interface() {
  [ ! -d "$RUN" ] || fail 'Сначала остановите FPTN.'
  command -v ndmc >/dev/null 2>&1 || fail 'Нет ndmc.'
  mkdir -p "$CONF"
  if ndmc -c 'show interface OpkgTun0' >/dev/null 2>&1; then
    [ -f "$CONF/native-interface" ] || fail 'OpkgTun0 уже существует и не принадлежит FPTN.'
    if [ -f "$CONF/native-setup-complete" ]; then
      echo 'OpkgTun0 уже настроен; настройки Keenetic сохранены без изменений.'
      return 0
    fi
  else
    ndmc -c 'interface OpkgTun0'
    printf 'OpkgTun0\n' > "$CONF/native-interface"
  fi
  ndmc -c 'interface OpkgTun0 description FPTN'
  ndmc -c 'interface OpkgTun0 security-level public'
  ndmc -c 'interface OpkgTun0 ip address 10.0.0.1 255.255.255.255'
  ndmc -c 'interface OpkgTun0 ip mtu 1420'
  ndmc -c 'interface OpkgTun0 ip tcp adjust-mss pmtu'
  ndmc -c 'interface OpkgTun0 no ip global'
  ndmc -c 'interface OpkgTun0 up'
  : > "$CONF/native-setup-complete"
  echo 'OpkgTun0 создан. Общий выход в интернет не переключался.'
  echo 'Первый тест: выберите FPTN/OpkgTun0 на странице маршрутизации Keenetic.'
  echo 'Конфигурация пока не записана в startup-config. После проверки: ndmc -c "system configuration save".'
}
install_native() {
  [ ! -d "$RUN" ] || fail 'Сначала остановите старую службу командой fptnctl stop.'
  SOURCE=$(readlink -f "${1:-/opt/tmp/fptn-native-client}")
  (cd "$SOURCE"; sha256sum -c SHA256SUMS) || fail 'Не совпали контрольные суммы архива.'
  chmod 700 "$SOURCE/fptn-client-cli"
  "$SOURCE/fptn-client-cli" --help | grep -q -- '--keenetic-native' || fail 'Нужен клиент с native-поддержкой.'
  mkdir -p "$LIB" "$CONF" "$ROOT/bin" "$ROOT/var/run"
  chmod 700 "$LIB" "$CONF"
  if [ ! -x "$LIB/brotli" ]; then
    [ -f /opt/tmp/fptn-connect-test/brotli ] || fail 'Нужен Brotli из ранее установленного пакета fptn-connect-test.'
    cp /opt/tmp/fptn-connect-test/brotli "$LIB/brotli"; chmod 700 "$LIB/brotli"
  fi
  [ ! -f "$LIB/fptn-client-cli" ] || cp -p "$LIB/fptn-client-cli" "$LIB/fptn-client-cli.previous"
  [ ! -f "$ROOT/bin/fptnctl" ] || cp -p "$ROOT/bin/fptnctl" "$ROOT/bin/fptnctl.previous"
  cp "$SOURCE/fptn-client-cli" "$LIB/fptn-client-cli.new"
  chmod 700 "$LIB/fptn-client-cli.new"
  mv "$LIB/fptn-client-cli.new" "$LIB/fptn-client-cli"
  cp "$SELF" "$ROOT/bin/fptnctl.new"; chmod 700 "$ROOT/bin/fptnctl.new"
  mv "$ROOT/bin/fptnctl.new" "$ROOT/bin/fptnctl"
  for file in sources.lock.json LICENSE-FPTN; do cp "$SOURCE/$file" "$LIB/$file"; done
  (cd "$LIB"; sha256sum fptn-client-cli brotli > SHA256SUMS)
  echo 'Установлена native-служба. Токен и сервер сохранены; старый CIDR-файл больше не используется.'
  echo 'Далее: fptnctl setup-interface; fptnctl start. Автозапуск выключен.'
}
stop_child() {
  if client_alive; then
    kill -TERM "$PID" 2>/dev/null || true
    STOP_N=0
    while client_alive && [ "$STOP_N" -lt 10 ]; do sleep 1; STOP_N=$((STOP_N+1)); done
    if client_alive; then kill -KILL "$PID" 2>/dev/null || true; fi
  fi
  if [ -n "${CHILD:-}" ]; then wait "$CHILD" 2>/dev/null || true; fi
  CHILD=
  rm -f "$RUN/client.pid" "$RUN/ready"
}
run_service() {
  record_pid "$$" "$RUN/supervisor.pid"
  finish() {
    trap '' INT TERM HUP
    stop_child
    [ ! -f "$RUN/client.log" ] || tail -c 65536 "$RUN/client.log" > "$CONF/last.log"
    [ ! -f "$RUN/supervisor.log" ] || tail -c 16384 "$RUN/supervisor.log" > "$CONF/service-last.log"
    rm -rf "$RUN"
  }
  trap finish EXIT
  trap 'exit 0' TERM INT HUP
  EVER_READY=no
  BACKOFF=5
  while :; do
    rm -f "$RUN/ready"
    echo 'FPTN: подключение.' > "$RUN/state"
    # Each attempt needs a fresh READY, not a line left by the previous client.
    if [ -s "$RUN/client.log" ]; then
      tail -c 65536 "$RUN/client.log" > "$CONF/last.log"
    fi
    : > "$RUN/client.log"
    attempt_service
    stop_child
    [ "$EVER_READY" = yes ] || fail 'Первое подключение не удалось: fptnctl logs.'
    echo "FPTN: клиент завершился; повтор через $BACKOFF секунд." > "$RUN/state"
    cat "$RUN/state"
    trim_logs
    # Short whole-second waits allow stop during backoff on BusyBox ash.
    WAIT_N=0
    while [ "$WAIT_N" -lt "$BACKOFF" ]; do sleep 1; WAIT_N=$((WAIT_N+1)); done
    BACKOFF=$((BACKOFF*2))
    [ "$BACKOFF" -le 60 ] || BACKOFF=60
  done
}
attempt_service() {
  load_connection
  # Re-read the uplink on every attempt: DHCP/failover can change it.
  GW=$(ip -4 route show table main default | awk '$1=="default" && $2=="via" {print $3; exit}')
  IFACE=$(ip -4 route show table main default | awk '$1=="default" {for(i=1;i<NF;i++) if($i=="dev") {print $(i+1); exit}}')
  if [ -z "$GW" ] || [ -z "$IFACE" ]; then
    echo 'FPTN: не найден обычный шлюз.'
    return 0
  fi
  TOKEN=$(cat "$CONF/token"); SERVER=$(cat "$CONF/server")
  nice -n 10 "$LIB/fptn-client-cli" --keenetic-connect --keenetic-native \
    --access-token "$TOKEN" --preferred-server "$SERVER" \
    --out-network-interface "$IFACE" --gateway-ip "$GW" \
    --tun-interface-name opkgtun0 --tun-interface-ip 10.0.0.1 --mtu-size 1420 \
    --sni "$SNI" --bypass-method "$BYPASS" --connection-strategy "$STRATEGY" \
    --enable-split-tunnel false --enable-ad-block false --blacklist-domains '' >> "$RUN/client.log" 2>&1 &
  CHILD=$!; record_pid "$CHILD" "$RUN/client.pid"; unset TOKEN
  N=0
  until grep -q 'Connection #1 READY' "$RUN/client.log"; do
    client_alive || { echo 'FPTN: клиент завершился до READY.'; return 0; }
    [ "$N" -lt 60 ] || { echo 'FPTN: нет READY за 60 секунд.'; return 0; }
    trim_logs; sleep 1; N=$((N+1))
  done
  : > "$RUN/ready"
  EVER_READY=yes
  echo 'FPTN: READY получен для текущего процесса.'
  STABLE_N=0
  while client_alive; do
    trim_logs; sleep 2
    STABLE_N=$((STABLE_N+1))
    # Reset only after a stable minute, so rapid crash loops keep backing off.
    [ "$STABLE_N" -lt 30 ] || BACKOFF=5
  done
  CLIENT_RC=0
  wait "$CHILD" || CLIENT_RC=$?
  echo "FPTN: процесс завершён, код $CLIENT_RC."
  return 0
}
start_service() {
  for cmd in ip timeout nice nohup jq base64 stty; do command -v "$cmd" >/dev/null 2>&1 || fail "Нет команды $cmd"; done
  [ -s "$CONF/token" ] && [ -s "$CONF/server" ] || fail 'Сначала fptnctl configure.'
  load_connection
  [ -f "$CONF/native-setup-complete" ] || fail 'Сначала fptnctl setup-interface.'
  ip link show dev opkgtun0 >/dev/null 2>&1 || fail 'Keenetic не создал opkgtun0.'
  mkdir -p "$ROOT/var/run"
  mkdir "$RUN" 2>/dev/null || fail 'Уже запущено или осталось состояние: status / recover.'
  trap 'rm -rf "$RUN"' EXIT
  (cd "$LIB"; sha256sum -c SHA256SUMS) || fail 'Повреждены установленные файлы.'
  : > "$RUN/client.log"
  nohup sh "$SELF" _run > "$RUN/supervisor.log" 2>&1 < /dev/null &
  trap - EXIT
  N=0
  while [ "$N" -lt 70 ]; do
    if [ -f "$RUN/ready" ]; then status_service; return 0; fi
    [ -d "$RUN" ] || { show_logs; fail 'Клиент не запустился.'; }
    sleep 1; N=$((N+1))
  done
  fail 'Нет подтверждения запуска: fptnctl logs; затем stop.'
}
status_service() {
  if alive "$RUN/supervisor.pid"; then
    if [ -f "$RUN/ready" ] && client_alive; then
      echo 'FPTN native: клиент запущен, READY получен при старте.'
      printf 'Сервер: '; cat "$CONF/server"
      echo 'Интерфейс: OpkgTun0 / opkgtun0. CIDR, домены, маршруты и firewall — в Keenetic.'
    elif [ -f "$RUN/state" ]; then cat "$RUN/state"
    else echo 'FPTN: запуск или остановка.'; fi
  elif [ -d "$RUN" ]; then echo 'FPTN: остаточное состояние, требуется recover.'; return 1
  else echo 'FPTN: остановлен. Автозапуск выключен.'; fi
}
stop_service() {
  [ -d "$RUN" ] || { echo 'FPTN остановлен.'; return 0; }
  alive "$RUN/supervisor.pid" || fail 'Нет живого supervisor: recover.'
  kill -TERM "$PID"
  N=0
  while alive "$RUN/supervisor.pid" && [ "$N" -lt 15 ]; do sleep 1; N=$((N+1)); done
  [ ! -d "$RUN" ] || fail 'Остановка не завершилась: logs.'
  echo 'Клиент остановлен. Штатный интерфейс и правила Keenetic сохранены.'
}
recover_service() {
  [ -d "$RUN" ] || { echo 'Остаточного состояния нет.'; return 0; }
  if alive "$RUN/supervisor.pid"; then fail 'Служба жива: stop.'; fi
  if client_alive; then fail 'Клиент ещё работает; не удаляю его состояние.'; fi
  rm -rf "$RUN"
  echo 'Состояние завершённого процесса удалено. Интерфейс Keenetic не изменялся.'
}
show_logs() {
  if [ -d "$RUN" ]; then
    [ ! -f "$RUN/supervisor.log" ] || tail -n 20 "$RUN/supervisor.log"
    [ ! -f "$RUN/client.log" ] || tail -n 60 "$RUN/client.log"
  else
    [ ! -f "$CONF/service-last.log" ] || tail -n 20 "$CONF/service-last.log"
    [ ! -f "$CONF/last.log" ] || tail -n 60 "$CONF/last.log"
  fi
}
need_root
case "${1:-}" in
  get-settings) connection_json ;;
  validate-settings) validate_connection ;;
  install) install_native "${2:-/opt/tmp/fptn-native-client}" ;;
  setup-interface) setup_interface ;;
  configure) mkdir -p "$CONF"; chmod 700 "$CONF"; configure ;;
  start) start_service ;;
  stop) stop_service ;;
  status) status_service ;;
  logs) show_logs ;;
  recover) recover_service ;;
  _run) [ -d "$RUN" ] || fail 'Нет каталога запуска.'; run_service ;;
  *) echo 'Использование: fptnctl install|setup-interface|configure|start|stop|status|logs|recover'; exit 1 ;;
esac
