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
proc_state() {
  [ -r "/proc/$1/stat" ] || return 1
  awk '{sub(/^.*\) /, ""); print $1}' "/proc/$1/stat"
}

record_pid() { printf '%s %s %s\n' "$1" "$(proc_start "$1")" "$(cat /proc/sys/kernel/random/boot_id)" > "$2"; }

alive() {
  [ -f "$1" ] || return 1
  read -r PID STAMP BOOT < "$1" || return 1
  [ -n "$BOOT" ] && [ "$BOOT" = "$(cat /proc/sys/kernel/random/boot_id)" ] || return 1
  case "$PID:$STAMP" in *[!0-9:]*|:|*:|:*) return 1 ;; esac
  [ "$PID" -gt 1 ] || return 1
  case "$(proc_state "$PID" 2>/dev/null || true)" in ''|Z|X) return 1 ;; esac
  [ "$(proc_start "$PID" 2>/dev/null || true)" = "$STAMP" ] && kill -0 "$PID" 2>/dev/null
}

client_alive() {
  alive "$RUN/client.pid" || return 1
  CLIENT_EXE=$(readlink "/proc/$PID/exe" 2>/dev/null || true)
  EXPECTED_EXE=$(readlink -f "$LIB/fptn-client-cli")
  case "$CLIENT_EXE" in "$EXPECTED_EXE"|"$EXPECTED_EXE (deleted)") return 0 ;; *) return 1 ;; esac
}
supervisor_owned() {
  alive "$RUN/supervisor.pid" || return 1
  SHELL_EXE=$(readlink "/proc/$PID/exe" 2>/dev/null || true)
  case "$SHELL_EXE" in */sh|*/ash|*/dash|*/bash|*/busybox) ;; *) return 1 ;; esac
  INSTALLED_SCRIPT=$(readlink -f "$ROOT/bin/fptnctl" 2>/dev/null || true)
  tr '\000' '\n' < "/proc/$PID/cmdline" | awk -v installed="$ROOT/bin/fptnctl" -v canonical="$INSTALLED_SCRIPT" -v self="$SELF" '
    $0==installed || (canonical!="" && $0==canonical) || $0==self {script=1}
    $0=="_run" {run=1}
    END {exit !(script && run)}'
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
validate_server_policy() {
  POLICY=$(head -c 65537)
  [ "${#POLICY}" -le 65536 ] || fail 'Слишком большой список серверов.'
  printf '%s' "$POLICY" | jq -ce 'select(type=="object" and
    (.mode=="auto" or .mode=="manual") and
    (.favorites|type=="array" and length<=512 and all(.[]; type=="string" and length>0 and length<=128 and all(explode[]; .>=32 and .!=127))) and
    (.excluded|type=="array" and length<=512 and all(.[]; type=="string" and length>0 and length<=128 and all(explode[]; .>=32 and .!=127)))) |
    {mode:.mode,favorites:(.favorites|unique),excluded:(.excluded|unique)}' || fail 'Некорректная политика серверов.'
}
server_policy_json() {
  if [ -f "$CONF/server-policy.json" ]; then validate_server_policy < "$CONF/server-policy.json"
  else printf '%s\n' '{"mode":"manual","favorites":[],"excluded":[]}'
  fi
}
capture_selected() {
  SELECTED=$(awk '/KEENETIC_SELECTED: / {sub(/^.*KEENETIC_SELECTED: /, ""); name=$0} END {print name}' "$RUN/client.log")
  if [ -n "$SELECTED" ]; then ATTEMPT_SELECTED=$SELECTED; printf '%s\n' "$SELECTED" > "$RUN/active-server"; fi
  return 0
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
    if client_alive; then
      kill -KILL "$PID" 2>/dev/null || true
      STOP_KILL_N=0
      while client_alive && [ "$STOP_KILL_N" -lt 3 ]; do sleep 1; STOP_KILL_N=$((STOP_KILL_N+1)); done
    fi
  fi
  if [ -n "${CHILD:-}" ]; then
    # Never wait indefinitely for a still-live process whose identity is unknown.
    case "$(proc_state "$CHILD" 2>/dev/null || true)" in
      ''|Z|X) wait "$CHILD" 2>/dev/null || true ;;
      *) echo 'FPTN: дочерний процесс ещё жив; сохраняю PID для безопасного восстановления.' >&2; return 1 ;;
    esac
  fi
  CHILD=
  rm -f "$RUN/client.pid" "$RUN/ready"
}
run_service() {
  record_pid "$$" "$RUN/supervisor.pid"
  finish() {
    trap '' INT TERM HUP
    stop_child || return 1
    [ ! -f "$RUN/client.log" ] || tail -c 65536 "$RUN/client.log" > "$CONF/last.log" || true
    [ ! -f "$RUN/supervisor.log" ] || tail -c 16384 "$RUN/supervisor.log" > "$CONF/service-last.log" || true
    rm -rf "$RUN"
  }
  trap finish EXIT
  trap 'exit 0' TERM INT HUP
  EVER_READY=no
  REPORT_CAPABLE=no
  "$LIB/fptn-client-cli" --help | grep -q -- '--keenetic-probe-only' && REPORT_CAPABLE=yes
  BACKOFF=5
  FORCE_AUTO=no
  printf '[]\n' > "$RUN/failed-servers.json"
  while :; do
    rm -f "$RUN/ready" "$RUN/active-server" "$RUN/link.json"
    printf 'starting\n' > "$RUN/phase"
    echo 'FPTN: подключение.' > "$RUN/state"
    # Each attempt needs a fresh READY, not a line left by the previous client.
    if [ -s "$RUN/client.log" ]; then
      tail -c 65536 "$RUN/client.log" > "$CONF/last.log"
    fi
    : > "$RUN/client.log"
    attempt_service
    stop_child
    if [ -f "$CONF/server-policy.json" ] && [ -n "$ATTEMPT_SELECTED" ]; then
      jq --arg name "$ATTEMPT_SELECTED" '. + [$name] | unique' "$RUN/failed-servers.json" > "$RUN/failed-servers.new"
      mv "$RUN/failed-servers.new" "$RUN/failed-servers.json"
      FORCE_AUTO=yes
      echo 'FPTN: повторный автовыбор, неработающий сервер временно пропускается.'
      # Try other candidates even when the first process failed before READY.
      sleep 1
      continue
    fi
    if [ "$EVER_READY" != yes ] && [ "${FPTN_BOOT_RETRY:-}" != boot ]; then
      fail 'Первое подключение не удалось: fptnctl logs.'
    fi
    # A whole selection pass failed. Start a fresh pass after bounded backoff.
    printf '[]\n' > "$RUN/failed-servers.json"
    FORCE_AUTO=yes
    echo "FPTN: клиент завершился; повтор через $BACKOFF секунд." > "$RUN/state"
    printf 'retrying\n' > "$RUN/phase"
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
  ATTEMPT_SELECTED=
  load_connection
  # Re-read the uplink on every attempt: DHCP/failover can change it.
  GW=$(ip -4 route show table main default | awk '$1=="default" && $2=="via" {print $3; exit}')
  IFACE=$(ip -4 route show table main default | awk '$1=="default" {for(i=1;i<NF;i++) if($i=="dev") {print $(i+1); exit}}')
  if [ -z "$GW" ] || [ -z "$IFACE" ]; then
    echo 'FPTN: не найден обычный шлюз.'
    return 0
  fi
  TOKEN=$(cat "$CONF/token"); SERVER=$(cat "$CONF/server")
  set -- --keenetic-connect --keenetic-native \
    --access-token "$TOKEN" --preferred-server "$SERVER" \
    --out-network-interface "$IFACE" --gateway-ip "$GW" \
    --tun-interface-name opkgtun0 --tun-interface-ip 10.0.0.1 --mtu-size 1420 \
    --sni "$SNI" --bypass-method "$BYPASS" --connection-strategy "$STRATEGY" \
    --enable-split-tunnel false --enable-ad-block false --blacklist-domains ''
  READY_TIMEOUT=60
  if [ "$REPORT_CAPABLE" = yes ]; then
    set -- "$@" --keenetic-status-file "$RUN/link.json" --keenetic-probe-report "$RUN/server-checks.json"
  fi
  if [ -f "$CONF/server-policy.json" ]; then
    READY_TIMEOUT=540
    set -- "$@" --keenetic-server-policy "$CONF/server-policy.json" --keenetic-skip-file "$RUN/failed-servers.json"
    [ "$FORCE_AUTO" = no ] || set -- "$@" --keenetic-auto-select
    echo 'FPTN: выбор сервера — избранные, затем остальные разрешённые.' > "$RUN/state"
    printf 'selecting\n' > "$RUN/phase"
  fi
  nice -n 10 "$LIB/fptn-client-cli" "$@" >> "$RUN/client.log" 2>&1 &
  CHILD=$!; record_pid "$CHILD" "$RUN/client.pid"; unset TOKEN
  N=0
  until grep -q 'Connection #1 READY' "$RUN/client.log"; do
    client_alive || { capture_selected; echo 'FPTN: клиент завершился до READY.'; return 0; }
    [ "$N" -lt "$READY_TIMEOUT" ] || { capture_selected; echo 'FPTN: превышено время выбора сервера и запуска.'; return 0; }
    trim_logs; sleep 1; N=$((N+1))
  done
  : > "$RUN/ready"
  printf 'running\n' > "$RUN/phase"
  capture_selected
  EVER_READY=yes
  echo 'FPTN: READY получен для текущего процесса.'
  STABLE_N=0
  while client_alive; do
    trim_logs; sleep 2
    STABLE_N=$((STABLE_N+1))
    # Reset only after a stable minute, so rapid crash loops keep backing off.
    if [ "$STABLE_N" -eq 30 ]; then BACKOFF=5; printf '[]\n' > "$RUN/failed-servers.json"; fi
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
  START_TIMEOUT=70
  if [ -f "$CONF/server-policy.json" ]; then
    server_policy_json >/dev/null
    "$LIB/fptn-client-cli" --help | grep -q -- '--keenetic-server-policy' || fail 'Для автовыбора нужен клиент 0.3.0 с --keenetic-server-policy.'
    START_TIMEOUT=600
  fi
  [ -f "$CONF/native-setup-complete" ] || fail 'Сначала fptnctl setup-interface.'
  if [ "${1:-}" != boot ]; then ip link show dev opkgtun0 >/dev/null 2>&1 || fail 'Keenetic не создал opkgtun0.'; fi
  mkdir -p "$ROOT/var/run"
  if [ "${1:-}" = boot ] && alive "$RUN/supervisor.pid"; then return 0; fi
  mkdir "$RUN" 2>/dev/null || fail 'Уже запущено или осталось состояние: status / recover.'
  trap 'rm -rf "$RUN"' EXIT
  (cd "$LIB"; sha256sum -c SHA256SUMS) || fail 'Повреждены установленные файлы.'
  : > "$RUN/client.log"
  FPTN_BOOT_RETRY=${1:-manual} nohup sh "$SELF" _run > "$RUN/supervisor.log" 2>&1 < /dev/null &
  trap - EXIT
  [ "${1:-}" != boot ] || return 0
  N=0
  while [ "$N" -lt "$START_TIMEOUT" ]; do
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
      printf 'Сервер: '
      if [ -s "$RUN/active-server" ]; then cat "$RUN/active-server"; else cat "$CONF/server"; fi
      echo 'Интерфейс: OpkgTun0 / opkgtun0. CIDR, домены, маршруты и firewall — в Keenetic.'
    elif [ -f "$RUN/state" ]; then cat "$RUN/state"
    else echo 'FPTN: запуск или остановка.'; fi
  elif [ -d "$RUN" ]; then echo 'FPTN: остаточное состояние, требуется recover.'; return 1
  else echo 'FPTN: остановлен.'; fi
  if [ -f "$CONF/autostart" ]; then echo 'Автозапуск включён.'; else echo 'Автозапуск выключен.'; fi
}
status_json() {
  STATE=stopped; SUPERVISOR=false; CLIENT=false; CONNECTED=false; ACTIVE=
  if alive "$RUN/supervisor.pid"; then
    SUPERVISOR=true; STATE=starting
    if [ -r "$RUN/phase" ]; then
      PHASE=$(cat "$RUN/phase")
      case "$PHASE" in starting|selecting|retrying) STATE=$PHASE ;; esac
    fi
    if client_alive; then
      CLIENT=true
      if [ -f "$RUN/ready" ]; then
        STATE=running
        LINK=$(jq -r '.state // "unknown"' "$RUN/link.json" 2>/dev/null || true)
        if [ "$LINK" = reconnecting ]; then STATE=reconnecting
        elif [ "$LINK" = connected ]; then CONNECTED=true; fi
        if [ -s "$RUN/active-server" ]; then ACTIVE=$(cat "$RUN/active-server")
        elif [ ! -f "$CONF/server-policy.json" ]; then ACTIVE=$(cat "$CONF/server"); fi
      fi
    fi
  elif [ -d "$RUN" ]; then STATE=stale; fi
  case "$STATE" in
    stopped) MESSAGE='Служба остановлена.' ;;
    running) if [ "$CONNECTED" = true ]; then MESSAGE='Туннель FPTN подключён.'; else MESSAGE='Клиент запущен; текущее состояние туннеля не подтверждено.'; fi ;;
    reconnecting) MESSAGE='Соединение потеряно. FPTN восстанавливает туннель.' ;;
    selecting) MESSAGE='Проверяем серверы и выбираем подключение.' ;;
    retrying) MESSAGE='Ожидаем следующую попытку подключения.' ;;
    stale) MESSAGE='Осталось состояние завершённой службы. Требуется fptnctl recover.' ;;
    *) MESSAGE='Служба запускается.' ;;
  esac
  CAN_START=false; [ "$STATE" != stopped ] || CAN_START=true
  jq -n --arg state "$STATE" --arg message "$MESSAGE" --arg server "$ACTIVE" \
    --argjson supervisor "$SUPERVISOR" --argjson client "$CLIENT" --argjson connected "$CONNECTED" --argjson canStart "$CAN_START" \
    '{state:$state,message:$message,selectedServer:$server,supervisorAlive:$supervisor,clientAlive:$client,connected:$connected,canStart:$canStart,canStop:$supervisor,canRestart:$supervisor}'
}
check_servers() (
  "$LIB/fptn-client-cli" --help | grep -q -- '--keenetic-probe-only' || fail 'Для проверки нужен клиент 0.4.0.'
  load_connection
  CHECK_RUN=$ROOT/var/run/fptn-web
  mkdir -p "$CHECK_RUN"
  CHECK_TMP=$(mktemp -d "$CHECK_RUN/check.XXXXXX")
  trap 'rm -rf "$CHECK_TMP"' EXIT
  trap 'exit 1' INT TERM HUP
  server_policy_json > "$CHECK_TMP/policy.json"
  TOKEN=$(cat "$CONF/token")
  GW=$(ip -4 route show table main default | awk '$1=="default" && $2=="via" {print $3; exit}')
  IFACE=$(ip -4 route show table main default | awk '$1=="default" {for(i=1;i<NF;i++) if($i=="dev") {print $(i+1); exit}}')
  [ -n "$GW" ] && [ -n "$IFACE" ] || fail 'Не найден обычный шлюз.'
  nice -n 10 timeout -s TERM 600 "$LIB/fptn-client-cli" --keenetic-connect --keenetic-native \
    --keenetic-probe-only --keenetic-probe-report "$CHECK_RUN/server-checks.json" \
    --keenetic-server-policy "$CHECK_TMP/policy.json" --access-token "$TOKEN" \
    --out-network-interface "$IFACE" --gateway-ip "$GW" --tun-interface-name opkgtun0 \
    --bypass-method "$BYPASS" --connection-strategy "$STRATEGY" --sni "$SNI" \
    --enable-split-tunnel false --enable-ad-block false --blacklist-domains ''
)
stop_service() {
  [ -d "$RUN" ] || { echo 'FPTN остановлен.'; return 0; }
  for RECORD in "$RUN/supervisor.pid" "$RUN/client.pid"; do
    [ -f "$RECORD" ] || continue
    RECORD_PID=; RECORD_STAMP=; RECORD_BOOT=
    read -r RECORD_PID RECORD_STAMP RECORD_BOOT < "$RECORD" || true
    case "$RECORD_PID:$RECORD_STAMP" in *[!0-9:]*|:|*:|:*) fail 'Повреждена PID-запись. Состояние сохранено для диагностики.' ;; esac
    [ -n "$RECORD_BOOT" ] || fail 'В PID-записи нет boot-id. Состояние сохранено для диагностики.'
  done
  if alive "$RUN/supervisor.pid"; then
    supervisor_owned || fail 'PID supervisor не принадлежит этой службе; остановка отменена.'
    kill -TERM "$PID" 2>/dev/null || true
  fi
  # Also handle an orphaned client or an old supervisor stuck in its EXIT trap.
  if alive "$RUN/client.pid"; then
    client_alive || fail 'PID клиента не соответствует FPTN; остановка отменена.'
    kill -TERM "$PID" 2>/dev/null || true
  fi
  N=0
  while [ "$N" -lt 10 ]; do
    if ! alive "$RUN/supervisor.pid" && ! alive "$RUN/client.pid"; then break; fi
    sleep 1; N=$((N+1))
  done
  if alive "$RUN/client.pid"; then
    client_alive || fail 'Изменилась идентичность клиента; PID не трогаю.'
    kill -KILL "$PID" 2>/dev/null || true
  fi
  N=0
  while alive "$RUN/client.pid" && [ "$N" -lt 3 ]; do sleep 1; N=$((N+1)); done
  if alive "$RUN/client.pid"; then fail 'Клиент не завершился после SIGKILL. Файлы и PID сохранены; обновление запрещено.'; fi
  if alive "$RUN/supervisor.pid"; then
    supervisor_owned || fail 'Изменилась идентичность supervisor; PID не трогаю.'
    # The client is gone: killing only this verified shell cannot orphan it.
    kill -KILL "$PID" 2>/dev/null || true
  fi
  N=0
  while alive "$RUN/supervisor.pid" && [ "$N" -lt 3 ]; do sleep 1; N=$((N+1)); done
  alive "$RUN/supervisor.pid" && fail 'Supervisor не завершился; обновление запрещено.'
  [ ! -f "$RUN/client.log" ] || tail -c 65536 "$RUN/client.log" > "$CONF/last.log" || true
  [ ! -f "$RUN/supervisor.log" ] || tail -c 16384 "$RUN/supervisor.log" > "$CONF/service-last.log" || true
  rm -rf "$RUN"
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
set_autostart() {
  INIT=$ROOT/etc/init.d/S90fptn
  if [ -e "$INIT" ]; then grep -Fq '# Managed by fptn-native' "$INIT" || fail 'S90fptn занят чужим скриптом.'; fi
  mkdir -p "$ROOT/etc/init.d" "$CONF"
  if [ "$1" = enable ]; then
    [ -f "$CONF/native-setup-complete" ] || fail 'Сначала настройте и сохраните интерфейс Keenetic.'
    cat > "$INIT.new" <<'INITEOF'
#!/bin/sh
# Managed by fptn-native
PATH=/opt/sbin:/opt/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
case "${1:-}" in
 start) [ ! -f /opt/etc/fptn/autostart ] || /opt/bin/fptnctl _boot ;;
 stop|kill) /opt/bin/fptnctl stop ;;
 restart) /opt/bin/fptnctl stop && { [ ! -f /opt/etc/fptn/autostart ] || /opt/bin/fptnctl _boot; } ;;
 *) exit 1 ;;
esac
INITEOF
    chmod 700 "$INIT.new"; mv "$INIT.new" "$INIT"
    : > "$CONF/autostart"
    echo 'Автозапуск включён. Текущее подключение не менялось.'
  else
    rm -f "$CONF/autostart"
    echo 'Автозапуск выключен. Текущее подключение не менялось.'
  fi
}
need_root
case "${1:-}" in
  enable|disable) set_autostart "$1" ;;
  _boot) [ ! -f "$CONF/autostart" ] || start_service boot ;;
  web-password) sh "$LIB/web-auth.sh" passwd ;;
  get-settings) connection_json ;;
  validate-settings) validate_connection ;;
  get-server-policy) server_policy_json ;;
  validate-server-policy) validate_server_policy ;;
  status-json) status_json ;;
  check-servers) check_servers ;;
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
