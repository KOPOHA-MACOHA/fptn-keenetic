#!/bin/sh
# First manual Entware service: IPv4 destination routing for br0, no boot hooks.
set -eu
export LC_ALL=C LANG=C
umask 077
ulimit -c 0
SELF=$(readlink -f "$0")
ROOT=${FPTN_PREFIX:-/opt}
CONF=$ROOT/etc/fptn
LIB=$ROOT/libexec/fptn
RUN=$ROOT/var/run/fptn-keenetic
TABLE=30091
PREF=61
FILTER=FPTN_KN_F
NAT=FPTN_KN_N
UPSTREAM_LOG=/var/log/fptn/fptn-client-cli.log
[ "$ROOT" = /opt ] || UPSTREAM_LOG=$ROOT/var/log/fptn/fptn-client-cli.log
fail() { echo "FPTN: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" = 0 ] || fail 'Нужен root.'; }
# BEGIN CIDR VALIDATOR
normalize_cidrs() {
  awk '
    function ipnum(ip, a,n,i,v) {
      n=split(ip,a,"."); if(n!=4) return -1
      v=0; for(i=1;i<=4;i++) {if(a[i]!~/^[0-9]+$/ || a[i]>255 || length(a[i])>3) return -1; v=v*256+a[i]}
      return v
    }
    function overlaps(lo,hi,a,b) {return lo<=b && hi>=a}
    {sub(/#.*/, ""); gsub(/,/, " "); for(k=1;k<=NF;k++) {
      entry=$k; n=split(entry,p,"/"); if(n==1) p[2]=32
      if(n>2 || p[2]!~/^[0-9]+$/ || p[2]<1 || p[2]>32) {bad=1; continue}
      ip=ipnum(p[1]); if(ip<0) {bad=1; continue}
      size=2^(32-p[2]); lo=int(ip/size)*size; hi=lo+size-1
      # Initial release accepts public IPv4 destinations only. Protect LAN/WAN.
      if(overlaps(lo,hi,0,16777215) || overlaps(lo,hi,167772160,184549375) ||
         overlaps(lo,hi,1681915904,1686110207) || overlaps(lo,hi,2130706432,2147483647) ||
         overlaps(lo,hi,2851995648,2852061183) || overlaps(lo,hi,2886729728,2887778303) ||
         overlaps(lo,hi,3232235520,3232301055) || overlaps(lo,hi,3758096384,4294967295)) {bad=1; continue}
      if(!seen[lo "/" p[2]]++) {
        count++; if(count>128) {bad=1; continue}
        a=int(lo/16777216); b=int(lo/65536)%256; c=int(lo/256)%256; d=lo%256
        printf "%d.%d.%d.%d/%d\n",a,b,c,d,p[2]
      }
    }}
    END {if(bad) exit 1}'
}
# END CIDR VALIDATOR
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
check_tools() {
  for cmd in ip iptables jq base64 stty timeout nice nohup awk; do
    command -v "$cmd" >/dev/null 2>&1 || fail "Нет команды $cmd"
  done
}
check_config() {
  [ -s "$CONF/token" ] && [ -s "$CONF/server" ] && [ -f "$CONF/cidrs" ] || fail 'Сначала выполните fptnctl configure.'
  normalize_cidrs < "$CONF/cidrs" > "$RUN/cidrs" || fail 'Некорректные CIDR: разрешены публичные IPv4, максимум 128 записей.'
  (cd "$LIB"; sha256sum -c SHA256SUMS) || fail 'Нарушены контрольные суммы установленных файлов.'
}
preflight() {
  [ "$(cat /proc/sys/net/ipv4/ip_forward)" = 1 ] || fail 'IPv4 forwarding выключен; автоматически не включаю.'
  ip link show dev br0 >/dev/null 2>&1 || fail 'Не найден LAN-интерфейс br0.'
  if ip link show dev fptn0 >/dev/null 2>&1; then fail 'fptn0 уже занят.'; fi
  if ip -4 rule show | awk -v p="$PREF:" '$1==p {yes=1} END {exit !yes}'; then fail "Приоритет $PREF занят."; fi
  [ -z "$(ip -4 route show table "$TABLE" 2>/dev/null || true)" ] || fail "Таблица $TABLE занята."
  if iptables -t filter -S "$FILTER" >/dev/null 2>&1; then fail "Цепочка $FILTER уже существует."; fi
  if iptables -t nat -S "$NAT" >/dev/null 2>&1; then fail "Цепочка $NAT уже существует."; fi
}
cleanup_network() {
  # Journal contains only successful additions. Never flush a shared table/chain.
  OK=1
  if [ -f "$RUN/rules" ]; then
    while IFS= read -r net; do
      ip -4 rule del priority "$PREF" iif br0 to "$net" lookup "$TABLE" 2>/dev/null || true
    done < "$RUN/rules"
  fi
  if [ -f "$RUN/filter" ]; then
    if iptables -t filter -C FORWARD -j "$FILTER" 2>/dev/null; then
      iptables -t filter -D FORWARD -j "$FILTER" || OK=0
    fi
    if iptables -t filter -S "$FILTER" >/dev/null 2>&1; then
      iptables -t filter -F "$FILTER" || OK=0
      iptables -t filter -X "$FILTER" || OK=0
    fi
  fi
  if [ -f "$RUN/nat" ]; then
    if iptables -t nat -C POSTROUTING -j "$NAT" 2>/dev/null; then
      iptables -t nat -D POSTROUTING -j "$NAT" || OK=0
    fi
    if iptables -t nat -S "$NAT" >/dev/null 2>&1; then
      iptables -t nat -F "$NAT" || OK=0
      iptables -t nat -X "$NAT" || OK=0
    fi
  fi
  if [ -f "$RUN/routes" ]; then
    while IFS= read -r net; do
      ip -4 route del table "$TABLE" "$net" dev fptn0 proto 186 2>/dev/null || true
    done < "$RUN/routes"
  fi
  if [ -f "$RUN/default" ]; then
    ip -4 route del unreachable default table "$TABLE" metric 32760 proto 186 2>/dev/null || true
  fi
  if [ -f "$RUN/rules" ] && ip -4 rule show | awk -v p="$PREF:" '$1==p {yes=1} END {exit !yes}'; then OK=0; fi
  if [ -f "$RUN/default" ] && [ -n "$(ip -4 route show table "$TABLE" 2>/dev/null || true)" ]; then OK=0; fi
  [ "$OK" = 1 ]
}
apply_network() {
  [ -s "$RUN/cidrs" ] || return 0
  ip -4 route add unreachable default table "$TABLE" metric 32760 proto 186
  : > "$RUN/default"
  iptables -t filter -N "$FILTER"
  : > "$RUN/filter"
  iptables -t nat -N "$NAT"
  : > "$RUN/nat"
  while IFS= read -r net; do
    ip -4 route add table "$TABLE" "$net" dev fptn0 proto 186
    printf '%s\n' "$net" >> "$RUN/routes"
    iptables -t filter -A "$FILTER" -i br0 -o fptn0 -d "$net" -j ACCEPT
    iptables -t filter -A "$FILTER" -i fptn0 -o br0 -s "$net" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    iptables -t nat -A "$NAT" -o fptn0 -d "$net" -j MASQUERADE
  done < "$RUN/cidrs"
  iptables -t filter -I FORWARD 1 -j "$FILTER"
  iptables -t nat -I POSTROUTING 1 -j "$NAT"
  while IFS= read -r net; do
    ip -4 rule add priority "$PREF" iif br0 to "$net" lookup "$TABLE"
    printf '%s\n' "$net" >> "$RUN/rules"
  done < "$RUN/cidrs"
}
trim_logs() {
  # Existing ELF also writes its own rotating log; cap that active file too.
  for log in "$RUN/client.log" "$UPSTREAM_LOG"; do
    if [ -f "$log" ] && [ ! -L "$log" ] && [ "$(wc -c < "$log")" -gt 131072 ]; then
      : > "$log"
    fi
  done
}
run_service() {
  record_pid "$$" "$RUN/supervisor.pid"
  finish() {
    trap '' INT TERM HUP
    CLEAN=1
    cleanup_network || CLEAN=0
    if client_alive; then
      kill -TERM "$PID" 2>/dev/null || true
      N=0
      while client_alive && [ "$N" -lt 10 ]; do sleep 1; N=$((N+1)); done
      if client_alive; then kill -KILL "$PID" 2>/dev/null || true; fi
    fi
    if [ -n "${CHILD:-}" ]; then wait "$CHILD" 2>/dev/null || true; fi
    rm -f "$RUN/ready"
    if [ "$CLEAN" = 1 ]; then
      mkdir -p "$CONF"
      if [ -f "$RUN/supervisor.log" ]; then tail -c 16384 "$RUN/supervisor.log" > "$CONF/service-last.log"; fi
      if [ -f "$RUN/client.log" ]; then tail -c 65536 "$RUN/client.log" > "$CONF/last.log"; fi
      rm -rf "$RUN"
    else
      rm -f "$RUN/supervisor.pid"
      echo 'FPTN: очистка неполная. Состояние сохранено; используйте fptnctl recover.' >&2
    fi
  }
  trap finish EXIT
  trap 'exit 0' TERM INT HUP
  TOKEN=$(cat "$CONF/token")
  SERVER=$(cat "$CONF/server")
  GW=$(ip -4 route show table main default | awk '$1=="default" && $2=="via" {print $3; exit}')
  IFACE=$(ip -4 route show table main default | awk '$1=="default" {for(i=1;i<NF;i++) if($i=="dev") {print $(i+1); exit}}')
  [ -n "$GW" ] && [ -n "$IFACE" ] || fail 'Не найден обычный шлюз.'
  nice -n 10 "$LIB/fptn-client-cli" --keenetic-connect --access-token "$TOKEN" \
    --preferred-server "$SERVER" --out-network-interface "$IFACE" --gateway-ip "$GW" \
    --tun-interface-name fptn0 --sni google.com --bypass-method obfuscation \
    --enable-split-tunnel false --enable-ad-block false --blacklist-domains '' \
    --connection-strategy dual-rolling-tunnel >> "$RUN/client.log" 2>&1 &
  CHILD=$!
  record_pid "$CHILD" "$RUN/client.pid"
  unset TOKEN
  N=0
  until grep -q 'Connection #1 READY' "$RUN/client.log"; do
    kill -0 "$CHILD" 2>/dev/null || fail 'Клиент завершился до READY.'
    [ "$N" -lt 60 ] || fail 'Клиент не готов за 60 секунд.'
    trim_logs; sleep 1; N=$((N+1))
  done
  ip link show dev fptn0 >/dev/null 2>&1 || fail 'Нет fptn0 после READY.'
  apply_network
  : > "$RUN/ready"
  while kill -0 "$CHILD" 2>/dev/null; do trim_logs; sleep 2; done
  wait "$CHILD"
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
  DEFAULT=''
  if command -v curl >/dev/null 2>&1; then
    IP=$(curl -4 --noproxy '*' -fsS --connect-timeout 5 --max-time 10 -o /dev/null -w '%{remote_ip}' https://api.ipify.org 2>/dev/null || true)
    if printf '%s\n' "$IP" | normalize_cidrs > "$TMP/default" && [ -s "$TMP/default" ]; then DEFAULT=$(cat "$TMP/default"); fi
  fi
  if [ -f "$CONF/cidrs" ]; then DEFAULT=$(tr '\n' ' ' < "$CONF/cidrs"); fi
  echo 'Маршрутизация по IPv4-назначению для всех устройств LAN (br0).'
  echo 'CIDR через пробел; Enter — предложенный список; знак - — без маршрутизации.'
  while :; do
    printf 'CIDR [%s]: ' "$DEFAULT"
    IFS= read -r CIDRS
    CIDRS=${CIDRS:-$DEFAULT}
    [ "$CIDRS" != - ] || CIDRS=''
    if printf '%s\n' "$CIDRS" | normalize_cidrs > "$TMP/cidrs"; then break; fi
    echo 'Некорректные CIDR. Нужны публичные IPv4, например 8.8.8.8/32.' >&2
  done
  printf '%s\n' "$TOKEN" > "$TMP/token"
  printf '%s\n' "$FPTN_SERVER" > "$TMP/server"
  for file in token server cidrs; do mv "$TMP/$file" "$CONF/$file"; done
  unset TOKEN FPTN_ACCESS_TOKEN
  echo 'Настройки сохранены (root, 0600). Автозапуск выключен.'
  echo 'Направляемые через VPN IPv4-сети:'
  cat "$CONF/cidrs"
}
install_service() {
  SOURCE=$(readlink -f "${1:-/opt/tmp/fptn-connect-test}")
  [ ! -d "$RUN" ] || fail 'Перед установкой остановите службу.'
  [ -f "$SOURCE/SHA256SUMS" ] || fail 'Не найден пакет fptn-connect-test.'
  (cd "$SOURCE"; sha256sum -c SHA256SUMS) || fail 'Контрольные суммы исходного пакета не совпали.'
  [ -f "$SOURCE/fptn-client-cli" ] && [ -f "$SOURCE/brotli" ] || fail 'Не найдены клиент и Brotli.'
  mkdir -p "$CONF" "$LIB" "$ROOT/bin" "$ROOT/var/run"
  chmod 700 "$CONF" "$LIB"
  cp "$SOURCE/fptn-client-cli" "$LIB/fptn-client-cli.new"
  cp "$SOURCE/brotli" "$LIB/brotli.new"
  chmod 700 "$LIB/fptn-client-cli.new" "$LIB/brotli.new"
  mv "$LIB/fptn-client-cli.new" "$LIB/fptn-client-cli"
  mv "$LIB/brotli.new" "$LIB/brotli"
  for file in LICENSE-FPTN LICENSE-BROTLI BROTLI-SOURCE.txt sources.lock.json; do
    [ ! -f "$SOURCE/$file" ] || cp "$SOURCE/$file" "$LIB/$file"
  done
  (cd "$LIB"; sha256sum fptn-client-cli brotli > SHA256SUMS)
  if [ "$SELF" != "$ROOT/bin/fptnctl" ]; then
    cp "$SELF" "$ROOT/bin/fptnctl.new"
    chmod 700 "$ROOT/bin/fptnctl.new"
    mv "$ROOT/bin/fptnctl.new" "$ROOT/bin/fptnctl"
  fi
  echo 'Установлено. Команды: fptnctl configure / start / stop / status / logs.'
  echo 'Автозапуск не установлен; сеть ещё не изменялась.'
}
start_service() {
  check_tools
  mkdir -p "$ROOT/var/run"
  mkdir "$RUN" 2>/dev/null || fail 'Уже запущено или осталось состояние: status / recover.'
  trap 'rm -rf "$RUN"' EXIT
  check_config
  preflight
  : > "$RUN/client.log"
  nohup sh "$SELF" _run > "$RUN/supervisor.log" 2>&1 < /dev/null &
  trap - EXIT
  N=0
  while [ "$N" -lt 70 ]; do
    if [ -f "$RUN/ready" ]; then status_service; return 0; fi
    [ -d "$RUN" ] || { [ ! -f "$CONF/service-last.log" ] || tail -n 12 "$CONF/service-last.log"; [ ! -f "$CONF/last.log" ] || tail -n 12 "$CONF/last.log"; fail 'Запуск не состоялся.'; }
    sleep 1; N=$((N+1))
  done
  fail 'Запуск не завершился: fptnctl logs; затем stop.'
}
stop_service() {
  if [ ! -d "$RUN" ]; then echo 'FPTN остановлен.'; return 0; fi
  alive "$RUN/supervisor.pid" || fail 'Нет живого supervisor; используйте recover.'
  kill -TERM "$PID"
  N=0
  while alive "$RUN/supervisor.pid" && [ "$N" -lt 15 ]; do sleep 1; N=$((N+1)); done
  [ ! -d "$RUN" ] || fail 'Осталось состояние. Проверьте logs; затем recover.'
  echo 'FPTN остановлен; собственные правила, маршруты и цепочки удалены.'
}
status_service() {
  if alive "$RUN/supervisor.pid"; then
    if [ -f "$RUN/ready" ] && client_alive; then
      echo 'FPTN: служба запущена, READY получен при старте (obfuscation, dual-rolling-tunnel).'
      printf 'Сервер: '; cat "$CONF/server"
      echo 'IPv4-назначения для LAN br0:'; cat "$RUN/cidrs"
      echo 'IPv6 и DNS не изменялись. Автозапуск выключен.'
    else echo 'FPTN: запуск или остановка.'; fi
  elif [ -d "$RUN" ]; then echo 'FPTN: остаточное состояние, требуется recover.'; return 1
  else echo 'FPTN: остановлен; автозапуск выключен.'; fi
}
recover_service() {
  [ -d "$RUN" ] || { echo 'Остаточного состояния нет.'; return 0; }
  if alive "$RUN/supervisor.pid"; then fail 'Служба жива: используйте stop.'; fi
  cleanup_network || fail 'Очистка неполная; состояние сохранено для проверки.'
  if client_alive; then
    kill -TERM "$PID"
    N=0
    while client_alive && [ "$N" -lt 10 ]; do sleep 1; N=$((N+1)); done
    if client_alive; then fail 'Клиент ещё работает; состояние сохранено.'; fi
  fi
  if [ -f "$RUN/client.log" ]; then tail -c 65536 "$RUN/client.log" > "$CONF/last.log"; fi
  rm -rf "$RUN"
  echo 'Собственное остаточное состояние удалено.'
}
need_root
case "${1:-}" in
  install) install_service "${2:-/opt/tmp/fptn-connect-test}" ;;
  configure) check_tools; mkdir -p "$CONF"; chmod 700 "$CONF"; configure ;;
  start) start_service ;;
  stop) stop_service ;;
  status) status_service ;;
  logs)
    if [ -d "$RUN" ]; then
      [ ! -f "$RUN/supervisor.log" ] || tail -n 20 "$RUN/supervisor.log"
      [ ! -f "$RUN/client.log" ] || tail -n 60 "$RUN/client.log"
    else
      [ ! -f "$CONF/service-last.log" ] || tail -n 20 "$CONF/service-last.log"
      [ ! -f "$CONF/last.log" ] || tail -n 60 "$CONF/last.log"
    fi ;;
  recover) recover_service ;;
  _run) [ -d "$RUN" ] || fail 'Нет каталога запуска.'; run_service ;;
  *) echo 'Использование: fptnctl install|configure|start|stop|status|logs|recover'; exit 1 ;;
esac
