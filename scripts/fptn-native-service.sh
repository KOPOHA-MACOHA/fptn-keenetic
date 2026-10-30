#!/bin/sh
# FPTN transport only. KeeneticOS owns the selected OpkgTun, routes, DNS and firewall.
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
native_name_valid() {
  case "$1" in OpkgTun*) suffix=${1#OpkgTun} ;; *) return 1 ;; esac
  case "$suffix" in ''|*[!0-9]*|0?*) return 1 ;; esac
  [ "${#suffix}" -le 8 ]
}
NATIVE=$(cat "$CONF/native-interface" 2>/dev/null || true)
if [ -n "$NATIVE" ]; then native_name_valid "$NATIVE" || fail 'Повреждено имя собственного интерфейса FPTN.'; fi
TUN=$(printf '%s' "$NATIVE" | tr 'A-Z' 'a-z')
require_native() { [ -n "$NATIVE" ] && [ -f "$CONF/native-interface" ] || fail 'Сначала создайте собственный интерфейс FPTN.'; }

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

capabilities_json() {
  CAP_HELPER=$(dirname "$SELF")/fptn-client-capabilities.sh
  [ -f "$CAP_HELPER" ] || CAP_HELPER=$LIB/client-capabilities.sh
  sh "$CAP_HELPER" "${FPTN_CAP_CLIENT:-$LIB/fptn-client-cli}"
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
  MTU=$(printf '%s' "$SETTINGS" | jq -r '.mtu // 0')
  OUT_IFACE=$(printf '%s' "$SETTINGS" | jq -r '.outInterface // ""')
  IPV6=$(printf '%s' "$SETTINGS" | jq -r '.ipv6 // false')
  printf '%s' "$SETTINGS" | jq -e '(if has("mtu") then .mtu else 0 end) as $mtu | (if has("outInterface") then .outInterface else "" end) as $iface |
    ($mtu|type=="number" and floor==. and (.==0 or (.>=576 and .<=65535))) and
    ((if has("ipv6") then .ipv6 else false end)|type=="boolean") and
    ($iface|type=="string" and length<=15 and all(explode[]; (.>=48 and .<=57) or (.>=65 and .<=90) or (.>=97 and .<=122) or .==45 or .==46 or .==95))' >/dev/null || fail 'Проверьте MTU и имя исходящего интерфейса.'
  case "$OUT_IFACE" in -*) fail 'Неверное имя интерфейса.' ;; opkgtun*) fail 'Сам туннель FPTN не может быть исходящим интерфейсом.' ;; esac
  if [ "$IPV6" = true ]; then
    [ "$MTU" -eq 0 ] || [ "$MTU" -ge 1280 ] || fail 'Для IPv6 MTU должен быть не меньше 1280.'
  fi
  CAPABILITIES=$(capabilities_json) || fail 'Не удалось прочитать возможности клиента.'
  printf '%s' "$CAPABILITIES" | jq -e --arg method "$BYPASS" --arg strategy "$STRATEGY"     '(.methods|index($method)!=null) and (.strategies|index($strategy)!=null)' >/dev/null || fail 'Метод или стратегия не поддерживаются установленным клиентом.'
  printf '%s\n' "$SNI" | awk '
    NR!=1 {exit 1}
    length($0)<1 || length($0)>253 || $0 ~ /[^a-zA-Z0-9.-]/ {exit 1}
    {n=split($0,a,"."); if(n<2) exit 1
     for(i=1;i<=n;i++) if(length(a[i])<1 || length(a[i])>63 || a[i] ~ /^-|-$/) exit 1}
  ' || fail 'Укажите корректный домен SNI латиницей.'
  jq -n --arg bypass "$BYPASS" --arg strategy "$STRATEGY" --arg sni "$SNI" --argjson mtu "$MTU" --arg outInterface "$OUT_IFACE" --argjson ipv6 "$IPV6" \
    '{bypass:$bypass,strategy:$strategy,sni:$sni,mtu:$mtu,outInterface:$outInterface,ipv6:$ipv6}'
}
connection_json() {
  if [ -f "$CONF/connection.json" ]; then validate_connection < "$CONF/connection.json"
  else
    CAPABILITIES=$(capabilities_json) || return 1
    printf '%s' "$CAPABILITIES" | jq '{bypass:"obfuscation",strategy:(if (.strategies|index("dual-rolling-tunnel"))!=null then "dual-rolling-tunnel" else .strategies[0] end),sni:"google.com",mtu:0,outInterface:"",ipv6:false}'
  fi
}
load_connection() {
  SETTINGS=$(connection_json)
  BYPASS=$(printf '%s' "$SETTINGS" | jq -r '.bypass')
  STRATEGY=$(printf '%s' "$SETTINGS" | jq -r '.strategy')
  SNI=$(printf '%s' "$SETTINGS" | jq -r '.sni')
  MTU=$(printf '%s' "$SETTINGS" | jq -r '.mtu // 0')
  OUT_IFACE=$(printf '%s' "$SETTINGS" | jq -r '.outInterface // ""')
  IPV6=$(printf '%s' "$SETTINGS" | jq -r '.ipv6 // false')
}
interface_mtu() {
  require_native
  VALUE=$(ip -o link show dev "$TUN" | awk '{for(i=1;i<NF;i++) if($i=="mtu") {print $(i+1); exit}}')
  case "$VALUE" in ''|*[!0-9]*) echo "Не удалось прочитать MTU $NATIVE." >&2; return 1 ;; esac
  [ "$VALUE" -ge 576 ] && [ "$VALUE" -le 65535 ] || return 1
  printf '%s\n' "$VALUE"
}
set_native_mtu() {
  case "${1:-}" in ''|*[!0-9]*) return 1 ;; esac
  [ "${#1}" -le 5 ] && [ "$1" -ge 576 ] && [ "$1" -le 65535 ] || return 1
  require_native
  CURRENT_MTU=$(interface_mtu) || return 1
  [ "$CURRENT_MTU" != "$1" ] || return 0
  # Change only this owned interface's MTU through the firmware API.
  ndmc -c "interface $NATIVE ip mtu $1" || return 1
  [ "$(interface_mtu)" = "$1" ] || { echo 'Keenetic не применил MTU.' >&2; return 1; }
}
ipv6_helper() {
  HELPER=$(dirname "$SELF")/fptn-ipv6.sh
  [ -f "$HELPER" ] || HELPER=$LIB/ipv6.sh
  # Old installations without this optional feature have nothing to clean up.
  if [ "$1" = remove ] && [ ! -f "$HELPER" ]; then return 0; fi
  sh "$HELPER" "$1"
}
ipv6_address_present() {
  ip -6 -o addr show dev "$TUN" | awk '$3=="inet6" && $4=="fd42:6670:746e::1/128" {found=1} END{exit !found}'
}
prepare_ipv6() {
  if [ "$IPV6" != true ]; then
    ipv6_helper remove || return 1
    if [ -f "$CONF/ipv6-address-owned" ]; then
      if ipv6_address_present; then ndmc -c "interface $NATIVE no ipv6 address fd42:6670:746e::1/128" || return 1; fi
      rm -f "$CONF/ipv6-address-owned"
    fi
    return 0
  fi
  require_native
  [ "$MTU" -ge 1280 ] || { echo 'IPv6 требует MTU от 1280.' >&2; return 1; }
  ipv6_helper check || return 1
  if ! ipv6_address_present; then
    if ip -6 -o addr show | awk -v own="$TUN" '$3=="inet6" {split($4,a,"/");if(a[1]=="fd42:6670:746e::1" && $2!=own)found=1} END{exit !found}'; then
      echo 'IPv6-адрес FPTN уже используется другим интерфейсом.' >&2; return 1
    fi
    : > "$CONF/ipv6-address-owned"
    ndmc -c "interface $NATIVE ipv6 address fd42:6670:746e::1/128" || return 1
    ipv6_address_present || { echo 'Keenetic не применил IPv6-адрес FPTN.' >&2; return 1; }
  fi
  ipv6_helper apply
}
sync_ipv6() {
  # The firmware hook only notifies the owner. It never competes with stop or
  # writes rules while NDM may still hold the netfilter lock.
  if supervisor_owned && [ -f "$CONF/connection.json" ] && jq -e '.ipv6==true' "$CONF/connection.json" >/dev/null; then
    : > "$RUN/ipv6-resync"
  fi
  return 0
}
restore_ipv6() {
  [ "$IPV6" = true ] && [ -f "$RUN/ipv6-resync" ] || return 0
  rm -f "$RUN/ipv6-resync"
  ipv6_helper apply
}
uplink_json() {
  HELPER=$(dirname "$SELF")/fptn-uplink.sh
  [ -f "$HELPER" ] || HELPER=$LIB/uplink.sh
  sh "$HELPER" "$OUT_IFACE"
}
resolve_uplink() {
  UPLINK=$(uplink_json) || return 1
  IFACE=$(printf '%s' "$UPLINK" | jq -r .interface)
  GW=$(printf '%s' "$UPLINK" | jq -r .gateway)
  UPLINK_SIGNATURE=$(printf '%s' "$UPLINK" | jq -r .signature)
}
check_uplink_change() {
  CURRENT_UPLINK=$(uplink_json 2>/dev/null) || CURRENT_UPLINK='{}'
  CURRENT_SIGNATURE=$(printf '%s' "$CURRENT_UPLINK" | jq -r '.signature // "offline"')
  if [ "$CURRENT_SIGNATURE" = "$UPLINK_SIGNATURE" ]; then UPLINK_CANDIDATE=; UPLINK_OBSERVATIONS=0; return 0; fi
  if [ "$CURRENT_SIGNATURE" = "$UPLINK_CANDIDATE" ]; then UPLINK_OBSERVATIONS=$((UPLINK_OBSERVATIONS+1))
  else UPLINK_CANDIDATE=$CURRENT_SIGNATURE; UPLINK_OBSERVATIONS=1; fi
  [ "$UPLINK_OBSERVATIONS" -lt 2 ] && return 0
  UPLINK_CHANGED=yes
  printf 'reconnecting\n' > "$RUN/phase"
  echo 'Изменилось исходящее подключение. Переподключаем FPTN.' > "$RUN/state"
  return 1
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
fallback_json() {
  if [ ! -f "$CONF/fallback.json" ] && [ ! -f "$LIB/transport-plan.php" ]; then
    printf '%s\n' '{"enabled":false,"profiles":[],"domains":[],"budget":60}'
  else sh "$LIB/web-auth.sh" transport-settings; fi
}
make_transport_plan() {
  USE_PLAN=no; PLAN_INDEX=0; FAVORITES_DEADLINE=0
  [ -f "$CONF/fallback.json" ] || return 0
  [ "$REPORT_CAPABLE" = yes ] || return 0
  if ! FPTN_FORCE_AUTO=$FORCE_AUTO sh "$LIB/web-auth.sh" transport-plan > "$RUN/transport-plan.new"; then
    echo 'FPTN: резервный подбор недоступен; используем сохранённые параметры.'
    rm -f "$RUN/transport-plan.new"; return 0
  fi
  if jq -e '.enabled==true and (.plans|length>0)' "$RUN/transport-plan.new" >/dev/null; then
    mv "$RUN/transport-plan.new" "$RUN/transport-plan.json"
    PLAN_COUNT=$(jq '.plans|length' "$RUN/transport-plan.json")
    FAVORITES_BUDGET=$(jq .budget "$RUN/transport-plan.json")
    printf '[]\n' > "$RUN/auth-denied.json"
    USE_PLAN=yes
  else rm -f "$RUN/transport-plan.new"; fi
}
next_transport_attempt() {
  while [ "$PLAN_INDEX" -lt "$PLAN_COUNT" ]; do
    jq --argjson index "$PLAN_INDEX" '.plans[$index]' "$RUN/transport-plan.json" > "$RUN/attempt.json"
    PLAN_INDEX=$((PLAN_INDEX+1))
    BUDGETED=$(jq -r .budgeted "$RUN/attempt.json")
    PLAN_TIMEOUT=$(jq .timeout "$RUN/attempt.json")
    if [ "$BUDGETED" = true ]; then
      NOW=$(date +%s)
      [ "$FAVORITES_DEADLINE" -gt 0 ] || FAVORITES_DEADLINE=$((NOW+FAVORITES_BUDGET))
      REMAINING=$((FAVORITES_DEADLINE-NOW))
      if [ "$REMAINING" -le 0 ]; then continue; fi
      [ "$PLAN_TIMEOUT" -le "$REMAINING" ] || PLAN_TIMEOUT=$REMAINING
    fi
    if ! jq -e --slurpfile denied "$RUN/auth-denied.json" '[.eligible[] | . as $name | select(($denied[0]|index($name))==null)]|length>0' "$RUN/attempt.json" >/dev/null; then continue; fi
    jq --slurpfile denied "$RUN/auth-denied.json" '.policy | .excluded=(.excluded+$denied[0]|unique)' "$RUN/attempt.json" > "$RUN/attempt-policy.json"
    return 0
  done
  return 1
}
record_auth_denials() {
  if [ -f "$RUN/server-checks.json" ]; then
    jq --slurpfile report "$RUN/server-checks.json" '. + [$report[0].results[]? | select(.code==401 or .code==403) | .name] | unique' "$RUN/auth-denied.json" > "$RUN/auth-denied.new" 2>/dev/null || return 0
    mv "$RUN/auth-denied.new" "$RUN/auth-denied.json"
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
# Inventory must be readable; a failed query is not evidence of a free name.
select_native() {
  if [ -n "$NATIVE" ]; then printf '%s\n' "$NATIVE"; return; fi
  INVENTORY=$(ndmc -c 'show interface') || fail 'Не удалось прочитать интерфейсы Keenetic.'
  printf '%s\n' "$INVENTORY" | grep -Eq '^[[:space:]]*id:' || fail 'Неизвестный формат списка интерфейсов Keenetic.'
  INDEX=0
  while [ "$INDEX" -le 65535 ]; do
    CANDIDATE=OpkgTun$INDEX
    if ! printf '%s\n' "$INVENTORY" | awk -v id="$CANDIDATE" '$1=="id:" && $2==id {found=1} END {exit !found}' &&
       ! ip link show dev "opkgtun$INDEX" >/dev/null 2>&1; then
      printf '%s\n' "$CANDIDATE"; return
    fi
    INDEX=$((INDEX+1))
  done
  fail 'Нет свободного номера OpkgTun.'
}
setup_interface() (
  [ ! -d "$RUN" ] || fail 'Сначала остановите FPTN.'
  command -v ndmc >/dev/null 2>&1 || fail 'Нет ndmc.'
  mkdir -p "$CONF"
  # Also serializes command-line setup, independently of the web operation lock.
  mkdir "$CONF/native-setup-lock" 2>/dev/null || fail 'Настройка интерфейса уже выполняется.'
  CREATED=no; COMPLETE=no
  cleanup_native() {
    if [ "$CREATED" = yes ] && [ "$COMPLETE" = no ]; then
      if ndmc -c "no interface $NATIVE"; then
        rm -f "$CONF/native-interface" "$CONF/native-setup-complete" "$CONF/native-setup-unsaved" "$CONF/native-creation-owner"
      else echo "Не удалось удалить незавершённый $NATIVE; маркер владения сохранён." >&2; fi
    fi
    rmdir "$CONF/native-setup-lock"
  }
  trap cleanup_native EXIT
  trap 'exit 1' INT TERM HUP
  if [ -n "$NATIVE" ] && [ -f "$CONF/native-setup-complete" ]; then
    ndmc -c "show interface $NATIVE" >/dev/null || fail "Сохранённый $NATIVE отсутствует; новый номер автоматически не назначается."
    echo "$NATIVE уже настроен; настройки Keenetic сохранены без изменений."
    COMPLETE=yes; return 0
  fi
  NATIVE=$(select_native)
  TUN=$(printf '%s' "$NATIVE" | tr 'A-Z' 'a-z')
  if [ ! -s "$CONF/native-interface" ]; then
    # Recheck both namespaces immediately before the creation command.
    if ndmc -c "show interface $NATIVE" >/dev/null 2>&1 || ip link show dev "$TUN" >/dev/null 2>&1; then
      fail "$NATIVE успел занять другой сервис. Повторите установку."
    fi
    if [ "$NATIVE" != OpkgTun0 ]; then
      CAPABILITIES=$(capabilities_json) || fail 'Не удалось проверить поддержку интерфейсов клиентом.'
      printf '%s' "$CAPABILITIES" | jq -e '.nativeInterfaceSelection==true' >/dev/null || fail 'Для свободного OpkgTun нужен новый клиент с поддержкой OpkgTunN.'
    fi
    # Do not claim/delete a firmware object when creation itself fails.
    ndmc -c "interface $NATIVE" || fail "Прошивка не создала $NATIVE. Чужие интерфейсы не изменялись."
    CREATED=yes
    if [ -n "${FPTN_INTERFACE_TRANSACTION:-}" ]; then
      printf '%s:%s\n' "$NATIVE" "$FPTN_INTERFACE_TRANSACTION" > "$CONF/native-creation-owner"
    fi
    printf '%s\n' "$NATIVE" > "$CONF/native-interface.new"
    mv "$CONF/native-interface.new" "$CONF/native-interface"
  fi
  ndmc -c "interface $NATIVE description FPTN"
  ndmc -c "interface $NATIVE security-level public"
  ndmc -c "interface $NATIVE ip address 10.0.0.1 255.255.255.255"
  ndmc -c "interface $NATIVE ip mtu 1420"
  ndmc -c "interface $NATIVE ip tcp adjust-mss pmtu"
  ndmc -c "interface $NATIVE no ip global"
  ndmc -c "interface $NATIVE up"
  : > "$CONF/native-setup-complete"
  : > "$CONF/native-setup-unsaved"
  COMPLETE=yes
  echo "$NATIVE создан. Общий выход в интернет не переключался."
  echo "Выберите FPTN/$NATIVE на странице маршрутизации Keenetic."
  echo 'Конфигурация пока не записана в startup-config. После проверки: ndmc -c "system configuration save".'
)
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
    ipv6_helper remove || echo 'Не удалось убрать IPv6 NAT; неизвестные правила не трогаю.' >&2
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
  make_transport_plan
  while :; do
    load_connection
    if ! resolve_uplink; then
      printf 'waiting-network\n' > "$RUN/phase"
      echo 'Ожидаем исходящее подключение к интернету.' > "$RUN/state"
      sleep 5
      make_transport_plan
      continue
    fi
    UPLINK_CHANGED=no; NETWORK_MISSING=no
    rm -f "$RUN/ready" "$RUN/active-server" "$RUN/link.json"
    printf 'starting\n' > "$RUN/phase"
    echo 'FPTN: подключение.' > "$RUN/state"
    # Each attempt needs a fresh READY, not a line left by the previous client.
    if [ -s "$RUN/client.log" ]; then
      tail -c 65536 "$RUN/client.log" > "$CONF/last.log"
    fi
    : > "$RUN/client.log"
    if [ "$USE_PLAN" = yes ] && ! next_transport_attempt; then
      ATTEMPT_SELECTED=; ATTEMPT_READY=no; ATTEMPT_STABLE=no
    else
      attempt_service
      stop_child
      if [ "$UPLINK_CHANGED" = yes ]; then
        printf '[]\n' > "$RUN/failed-servers.json"
        BACKOFF=5
        make_transport_plan
        continue
      fi
      if [ "$NETWORK_MISSING" = yes ]; then
        printf 'waiting-network\n' > "$RUN/phase"
        echo 'Ожидаем исходящее подключение к интернету.' > "$RUN/state"
        sleep 5
        continue
      fi
      if [ "$USE_PLAN" = yes ]; then
        record_auth_denials
        if [ "$ATTEMPT_STABLE" = yes ]; then FORCE_AUTO=yes; make_transport_plan; continue; fi
        [ "$PLAN_INDEX" -ge "$PLAN_COUNT" ] || { sleep 1; continue; }
      fi
    fi
    if [ "$USE_PLAN" != yes ] && [ -f "$CONF/server-policy.json" ] && [ -n "$ATTEMPT_SELECTED" ]; then
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
    make_transport_plan
  done
}
attempt_service() {
  ATTEMPT_SELECTED=; ATTEMPT_READY=no; ATTEMPT_STABLE=no
  load_connection
  # Re-read the uplink on every attempt: DHCP/failover can change it.
  if ! resolve_uplink; then NETWORK_MISSING=yes; return 0; fi
  if [ -z "$GW" ] && ! "$LIB/fptn-client-cli" --help | grep -q -- '--keenetic-native-transport'; then
    echo 'Для PPPoE без шлюза нужен клиент 0.5.0.' >&2; return 0
  fi
  UPLINK_CANDIDATE=; UPLINK_OBSERVATIONS=0; UPLINK_TICKS=0
  if [ "$MTU" -eq 0 ]; then MTU=$(interface_mtu) || return 0
  else set_native_mtu "$MTU" || return 0; fi
  prepare_ipv6 || return 0
  if [ "$USE_PLAN" = yes ]; then
    BYPASS=$(jq -r .connection.bypass "$RUN/attempt.json")
    SNI=$(jq -r .connection.sni "$RUN/attempt.json")
  fi
  jq -n --arg bypass "$BYPASS" --arg sni "$SNI" --arg strategy "$STRATEGY" --argjson mtu "$MTU" --arg outInterface "$IFACE" --argjson automatic "$([ "$USE_PLAN" = yes ] && echo true || echo false)" '{bypass:$bypass,sni:$sni,strategy:$strategy,mtu:$mtu,outInterface:$outInterface,automatic:$automatic}' > "$RUN/effective-connection.new"
  mv "$RUN/effective-connection.new" "$RUN/effective-connection.json"
  rm -f "$RUN/server-checks.json"
  TOKEN=$(cat "$CONF/token"); SERVER=$(cat "$CONF/server")
  set -- --keenetic-connect --keenetic-native \
    --access-token "$TOKEN" --preferred-server "$SERVER" \
    --out-network-interface "$IFACE" --gateway-ip "$GW" \
    --tun-interface-name "$TUN" --tun-interface-ip 10.0.0.1 --tun-interface-ipv6 fd42:6670:746e::1 --mtu-size "$MTU" \
    --sni "$SNI" --bypass-method "$BYPASS" --connection-strategy "$STRATEGY" \
    --enable-split-tunnel false --enable-ad-block false --blacklist-domains ''
  READY_TIMEOUT=60
  if [ "$REPORT_CAPABLE" = yes ]; then
    set -- "$@" --keenetic-status-file "$RUN/link.json" --keenetic-probe-report "$RUN/server-checks.json"
  fi
  if [ "$USE_PLAN" = yes ]; then
    READY_TIMEOUT=$PLAN_TIMEOUT
    set -- "$@" --keenetic-server-policy "$RUN/attempt-policy.json" --keenetic-auto-select
    STAGE=$(jq -r .stage "$RUN/attempt.json")
    case "$STAGE" in cached) STAGE_LABEL='последний рабочий вариант' ;; manual) STAGE_LABEL='выбранный сервер' ;; favorites) STAGE_LABEL='избранные' ;; *) STAGE_LABEL='остальные серверы' ;; esac
    METHOD_LABEL='обфускация'
    [ "$BYPASS" = obfuscation ] || METHOD_LABEL="${BYPASS#sni-spoofing-}, SNI $SNI"
    echo "Пробуем: $STAGE_LABEL · $METHOD_LABEL ($PLAN_INDEX/$PLAN_COUNT)." > "$RUN/state"
    printf 'selecting\n' > "$RUN/phase"
  elif [ -f "$CONF/server-policy.json" ]; then
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
    UPLINK_TICKS=$((UPLINK_TICKS+1))
    if [ "$UPLINK_TICKS" -ge 6 ]; then UPLINK_TICKS=0; check_uplink_change || return 0; fi
  done
  # Address assignment can itself trigger a firmware firewall rebuild before
  # client.pid exists. Restore once at READY as well as on queued notifications.
  if [ "$IPV6" = true ]; then
    : > "$RUN/ipv6-resync"
    restore_ipv6 || fail 'Не удалось восстановить IPv6 NAT FPTN.'
  fi
  : > "$RUN/ready"
  printf 'running\n' > "$RUN/phase"
  capture_selected
  EVER_READY=yes; ATTEMPT_READY=yes
  echo 'FPTN: READY получен для текущего процесса.'
  STABLE_N=0
  while client_alive; do
    restore_ipv6 || fail 'Не удалось восстановить IPv6 NAT FPTN.'
    trim_logs; sleep 2
    UPLINK_TICKS=$((UPLINK_TICKS+2))
    if [ "$UPLINK_TICKS" -ge 6 ]; then UPLINK_TICKS=0; check_uplink_change || return 0; fi
    if [ "$USE_PLAN" = yes ] && ! jq -e '.state=="connected"' "$RUN/link.json" >/dev/null 2>&1; then STABLE_N=0
    else STABLE_N=$((STABLE_N+1)); fi
    # Reset only after a stable minute, so rapid crash loops keep backing off.
    if [ "$STABLE_N" -eq 30 ]; then
      BACKOFF=5; ATTEMPT_STABLE=yes; printf '[]\n' > "$RUN/failed-servers.json"
      if [ "$USE_PLAN" = yes ]; then sh "$LIB/web-auth.sh" transport-cache >/dev/null || true; fi
    fi
  done
  CLIENT_RC=0
  wait "$CHILD" || CLIENT_RC=$?
  echo "FPTN: процесс завершён, код $CLIENT_RC."
  return 0
}
start_service() {
  for cmd in ip timeout nice nohup jq base64 stty; do command -v "$cmd" >/dev/null 2>&1 || fail "Нет команды $cmd"; done
  [ -s "$CONF/token" ] && [ -s "$CONF/server" ] || fail 'Вставьте токен и сохраните сервер в веб-панели.'
  load_connection
  if [ "$IPV6" = true ]; then ipv6_helper check || fail 'IPv6 NAT недоступен. Выключите IPv6 в панели или установите поддержку ядра.'; fi
  START_TIMEOUT=70
  if [ -f "$CONF/server-policy.json" ]; then
    server_policy_json >/dev/null
    "$LIB/fptn-client-cli" --help | grep -q -- '--keenetic-server-policy' || fail 'Для автовыбора нужен клиент 0.3.0 с --keenetic-server-policy.'
    START_TIMEOUT=600
  fi
  if [ -f "$CONF/fallback.json" ] && jq -e '.enabled==true' "$CONF/fallback.json" >/dev/null; then
    "$LIB/fptn-client-cli" --help | grep -q -- '--keenetic-probe-only' || fail 'Для резервного подбора нужен клиент 0.4.0.'
    START_TIMEOUT=$((START_TIMEOUT+240))
  fi
  require_native
  [ -f "$CONF/native-setup-complete" ] || fail 'Сначала fptnctl setup-interface.'
  if [ "${1:-}" != boot ]; then ip link show dev "$TUN" >/dev/null 2>&1 || fail "Keenetic не создал $TUN."; fi
  mkdir -p "$ROOT/var/run"
  if [ "${1:-}" = boot ] && alive "$RUN/supervisor.pid"; then return 0; fi
  mkdir "$RUN" 2>/dev/null || fail 'Уже запущено или осталось состояние: status / recover.'
  trap 'rm -rf "$RUN"' EXIT
  (cd "$LIB"; sha256sum -c SHA256SUMS) || fail 'Повреждены установленные файлы.'
  : > "$RUN/client.log"
  FPTN_OPERATION_ID= FPTN_BOOT_RETRY=${1:-manual} nohup sh "$SELF" _run > "$RUN/supervisor.log" 2>&1 < /dev/null &
  trap - EXIT
  [ "${1:-}" != boot ] || return 0
  N=0
  while [ "$N" -lt "$START_TIMEOUT" ]; do
    if [ -f "$RUN/ready" ]; then status_service; return 0; fi
    if [ "$(cat "$RUN/phase" 2>/dev/null)" = waiting-network ] && supervisor_owned; then status_service; return 0; fi
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
      echo "Интерфейс: $NATIVE / $TUN. CIDR, домены, маршруты и firewall — в Keenetic."
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
      case "$PHASE" in starting|selecting|retrying|waiting-network|reconnecting) STATE=$PHASE ;; esac
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
    waiting-network) MESSAGE='Ожидаем исходящее подключение к интернету.' ;;
    reconnecting) MESSAGE='Соединение потеряно. FPTN восстанавливает туннель.' ;;
    selecting) MESSAGE='Проверяем серверы и выбираем подключение.'; [ ! -s "$RUN/state" ] || MESSAGE=$(cat "$RUN/state") ;;
    retrying) MESSAGE='Ожидаем следующую попытку подключения.' ;;
    stale) MESSAGE='Осталось состояние завершённой службы. Требуется fptnctl recover.' ;;
    *) MESSAGE='Служба запускается.' ;;
  esac
  EFFECTIVE=null
  if [ "$CLIENT" = true ] && [ -f "$RUN/effective-connection.json" ]; then EFFECTIVE=$(cat "$RUN/effective-connection.json"); fi
  CAN_START=false
  if [ "$STATE" = stopped ] && [ -s "$CONF/token" ] && [ -s "$CONF/server" ] && [ -f "$CONF/native-setup-complete" ]; then CAN_START=true; fi
  jq -n --arg nativeInterface "$NATIVE" --arg state "$STATE" --arg message "$MESSAGE" --arg server "$ACTIVE" \
    --argjson effective "$EFFECTIVE" --argjson supervisor "$SUPERVISOR" --argjson client "$CLIENT" --argjson connected "$CONNECTED" --argjson canStart "$CAN_START" \
    '{nativeInterface:$nativeInterface,effectiveConnection:$effective,state:$state,message:$message,selectedServer:$server,supervisorAlive:$supervisor,clientAlive:$client,connected:$connected,canStart:$canStart,canStop:$supervisor,canRestart:$supervisor}'
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
  resolve_uplink || fail 'Не удалось выбрать исходящий интерфейс.'
  nice -n 10 timeout -s TERM 600 "$LIB/fptn-client-cli" --keenetic-connect --keenetic-native \
    --keenetic-probe-only --keenetic-probe-report "$CHECK_RUN/server-checks.json" \
    --keenetic-server-policy "$CHECK_TMP/policy.json" --access-token "$TOKEN" \
    --out-network-interface "$IFACE" --gateway-ip "$GW" --tun-interface-name "${TUN:-opkgtun0}" \
    --bypass-method "$BYPASS" --connection-strategy "$STRATEGY" --sni "$SNI" \
    --enable-split-tunnel false --enable-ad-block false --blacklist-domains ''
)
stop_service() {
  [ -d "$RUN" ] || { ipv6_helper remove || echo 'Не удалось убрать IPv6 NAT.' >&2; echo 'FPTN остановлен.'; return 0; }
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
  if client_alive; then
    kill -TERM "$PID" 2>/dev/null || true
  elif alive "$RUN/client.pid"; then
    fail 'PID клиента не соответствует FPTN; остановка отменена.'
  fi
  N=0
  while [ "$N" -lt 10 ]; do
    if ! alive "$RUN/supervisor.pid" && ! alive "$RUN/client.pid"; then break; fi
    sleep 1; N=$((N+1))
  done
  if client_alive; then
    kill -KILL "$PID" 2>/dev/null || true
  elif alive "$RUN/client.pid"; then
    fail 'Изменилась идентичность клиента; PID не трогаю.'
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
  ipv6_helper remove || echo 'Не удалось убрать IPv6 NAT; неизвестные правила сохранены.' >&2
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
uninstall_runtime() (
  # Keep credentials and all firmware configuration, including routes using the owned OpkgTun.
  [ -f "$LIB/.runtime-managed" ] || fail 'Удаление поддерживается после обновления до 0.13.0.'
  WEB=$ROOT/share/fptn-web
  FRAGMENT=$ROOT/etc/lighttpd/conf.d/95-fptn.conf
  [ -f "$WEB/.fptn-web-owned" ] || fail 'Не подтверждено владение каталогом панели.'
  [ ! -e "$FRAGMENT" ] || grep -Fq '# Managed by fptn-web-ui' "$FRAGMENT" || fail '95-fptn.conf изменён другим приложением.'
  INIT=$ROOT/etc/init.d/S90fptn
  [ ! -e "$INIT" ] || grep -Fq '# Managed by fptn-native' "$INIT" || fail 'S90fptn принадлежит другому приложению.'
  HTTP_INIT=
  for candidate in "$ROOT"/etc/init.d/S*lighttpd; do
    [ -x "$candidate" ] || continue
    [ -z "$HTTP_INIT" ] || fail 'Найдено несколько init-скриптов lighttpd.'
    HTTP_INIT=$candidate
  done
  [ -n "$HTTP_INIT" ] || fail 'Не найден init-скрипт lighttpd.'
  # Reserve through the same mutex as the browser/installer; never erase a live operation.
  RESERVED=$(printf '%s' '{"action":"stop"}' | FPTN_OWNER_PID=$$ sh "$LIB/web-auth.sh" op-reserve) || fail 'Сначала завершите операцию панели.'
  LOCK=$ROOT/var/run/fptn-web/action
  FPTN_OPERATION_ID=$(printf '%s' "$RESERVED" | jq -er .id); export FPTN_OPERATION_ID
  trap 'rm -rf "$LOCK"' EXIT
  trap 'exit 1' INT TERM HUP
  stop_service
  ipv6_helper remove || fail 'Удаление прервано: проверьте IPv6 NAT. Файлы программы сохранены.'
  HOOK=$ROOT/etc/ndm/netfilter.d/190-fptn-ipv6.sh
  if [ -e "$HOOK" ]; then
    grep -Fq '# Managed by fptn IPv6' "$HOOK" || fail 'IPv6 hook изменён другим приложением.'
    rm -f "$HOOK"
  fi
  BACKUP=$(mktemp -d "$CONF/uninstall-backup.XXXXXX")
  if [ -f "$FRAGMENT" ]; then
    cp -p "$FRAGMENT" "$BACKUP/95-fptn.conf"
    rm "$FRAGMENT"
    if ! lighttpd -tt -f "$ROOT/etc/lighttpd/lighttpd.conf"; then
      cp -p "$BACKUP/95-fptn.conf" "$FRAGMENT"
      fail 'Общая конфигурация lighttpd не прошла проверку; файлы службы сохранены.'
    fi
    # Init may report failed during a successful delayed restart, as on Hopper.
    FPTN_OPERATION_ID= "$HTTP_INIT" restart || true
    READY=no
    for attempt in 1 2 3 4 5; do
      if pidof lighttpd >/dev/null 2>&1; then READY=yes; break; fi
      sleep 1
    done
    if [ "$READY" != yes ]; then
      cp -p "$BACKUP/95-fptn.conf" "$FRAGMENT"
      FPTN_OPERATION_ID= "$HTTP_INIT" restart || true
      fail 'lighttpd не подтвердил запуск. Фрагмент панели восстановлен; файлы службы сохранены.'
    fi
  fi
  rm -f "$CONF/autostart" "$INIT"
  for file in index.html index.cgi app.js style.css api.cgi .fptn-web-owned; do rm -f "$WEB/$file"; done
  rmdir "$WEB" 2>/dev/null || true
  for file in fptn-client-cli brotli web-auth.sh web-auth.php web-operations.php transport-plan.php web-update.sh client-capabilities.sh client-identity.sh platform.sh uplink.sh ipv6.sh sources.lock.json SHA256SUMS LICENSE-FPTN LICENSE-BROTLI BROTLI-SOURCE.txt .runtime-managed; do rm -f "$LIB/$file"; done
  rmdir "$LIB" 2>/dev/null || true
  rm -f "$ROOT/bin/fptnctl"
  echo 'Программа и панель FPTN удалены. Пакеты Entware и другие веб-панели сохранены.'
  echo 'Токен, вход, настройки и резервные копии оставлены в /opt/etc/fptn. Повторная установка использует их.'
  echo "Интерфейс $NATIVE и маршруты Keenetic оставлены: удалить или переназначить их можно в Keenetic."
)

set_autostart() {
  INIT=$ROOT/etc/init.d/S90fptn
  if [ -e "$INIT" ]; then grep -Fq '# Managed by fptn-native' "$INIT" || fail 'S90fptn занят чужим скриптом.'; fi
  mkdir -p "$ROOT/etc/init.d" "$CONF"
  if [ "$1" = enable ]; then
    [ -f "$CONF/native-setup-complete" ] && [ ! -f "$CONF/native-setup-unsaved" ] || fail 'Сначала сохраните конфигурацию интерфейса Keenetic в панели.'
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
  web-reset-login) sh "$LIB/web-auth.sh" reset ;;
  web-password) echo 'Логин и пароль задаются в браузере. Для сброса: fptnctl web-reset-login.' ;;
  get-fallback) fallback_json ;;
  validate-fallback) sh "$LIB/web-auth.sh" transport-validate ;;
  get-capabilities) capabilities_json ;;
  get-uplink) load_connection; uplink_json ;;
  sync-ipv6) sync_ipv6 ;;
  check-ipv6) ipv6_helper check ;;
  get-settings) connection_json ;;
  validate-settings) validate_connection ;;
  get-interface-mtu) interface_mtu ;;
  set-interface-mtu) [ ! -d "$RUN" ] || fail 'Сначала остановите FPTN.'; set_native_mtu "${2:-}" ;;
  get-server-policy) server_policy_json ;;
  validate-server-policy) validate_server_policy ;;
  status-json) status_json ;;
  check-servers) check_servers ;;
  install) install_native "${2:-/opt/tmp/fptn-native-client}" ;;
  setup-interface) setup_interface ;;
  setup-interface-save)
    if [ ! -f "$CONF/native-setup-complete" ]; then setup_interface; fi
    NATIVE=$(cat "$CONF/native-interface" 2>/dev/null || true)
    native_name_valid "$NATIVE" || fail 'Не определён собственный интерфейс FPTN.'
    ndmc -c "show interface $NATIVE" >/dev/null || fail 'Интерфейс FPTN не найден.'
    ndmc -c 'system configuration save'
    rm -f "$CONF/native-setup-unsaved" ;;
  uninstall) uninstall_runtime ;;
  configure) mkdir -p "$CONF"; chmod 700 "$CONF"; configure ;;
  start) start_service ;;
  stop) stop_service ;;
  status) status_service ;;
  logs) show_logs ;;
  recover) recover_service ;;
  _run) [ -d "$RUN" ] || fail 'Нет каталога запуска.'; run_service ;;
  *) echo 'Использование: fptnctl install|setup-interface|configure|start|stop|status|logs|recover'; exit 1 ;;
esac
