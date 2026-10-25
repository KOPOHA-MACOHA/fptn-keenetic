#!/bin/sh
# Uses the already built 0.1.0 client. Token decoding stays on the router.
set -eu
# Static libstdc++ supports the C locale; Entware may export an unavailable locale.
export LC_ALL=C LANG=C
umask 077
ulimit -c 0
cd "$(dirname "$0")"
sha256sum -c SHA256SUMS
chmod 700 fptn-client-cli
./fptn-client-cli --version
for tool in ip timeout nice stty jq base64; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Нет команды: $tool" >&2; exit 1; }
done
FPTN_DATA_DIR=''
FPTN_DATA_WORKER=''
FPTN_TTY_STATE=''
fptn_cleanup() {
  if [ -n "$FPTN_DATA_WORKER" ]; then
    kill -TERM "$FPTN_DATA_WORKER" 2>/dev/null || true
    wait "$FPTN_DATA_WORKER" 2>/dev/null || true
  fi
  [ -z "$FPTN_TTY_STATE" ] || stty "$FPTN_TTY_STATE" 2>/dev/null || true
  [ -z "$FPTN_DATA_DIR" ] || rm -rf "$FPTN_DATA_DIR"
}
trap fptn_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
if ip link show dev fptn0 >/dev/null 2>&1; then
  echo 'Интерфейс fptn0 уже существует. Тест остановлен.' >&2
  exit 1
fi
FPTN_GATEWAY=$(ip -4 route show table main default | awk '$1=="default" && $2=="via" {print $3; exit}')
FPTN_INTERFACE=$(ip -4 route show table main default | awk '$1=="default" {for(i=1;i<NF;i++) if($i=="dev") {print $(i+1); exit}}')
[ -n "$FPTN_GATEWAY" ] && [ -n "$FPTN_INTERFACE" ] || { echo 'Не найден обычный шлюз.' >&2; exit 1; }
if [ "${FPTN_DATA_TEST:-0}" = 1 ]; then
  command -v curl >/dev/null 2>&1 || { echo 'Для проверки данных нужен curl.' >&2; exit 1; }
  FPTN_DATA_DIR=$(mktemp -d "$PWD/fptn-data.XXXXXX")
  echo 'Проверяю внешний IPv4 обычного подключения...'
  if ! curl -4 --noproxy '*' -fsS --connect-timeout 10 --max-time 15 \
    -o "$FPTN_DATA_DIR/direct.txt" -w '%{remote_ip}' https://api.ipify.org > "$FPTN_DATA_DIR/target.txt"; then
    echo 'Сервис проверки IP недоступен по обычному подключению. VPN ещё не запускался.' >&2
    exit 1
  fi
fi
FPTN_TTY_STATE=$(stty -g)
printf 'Вставьте токен FPTN (ввод скрыт): '
stty -echo
IFS= read -r FPTN_ACCESS_TOKEN
stty "$FPTN_TTY_STATE"
printf '\n'
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
fptn_run_client() {
  nice -n 10 timeout -s TERM 90 ./fptn-client-cli \
  --keenetic-connect \
  --access-token "$FPTN_ACCESS_TOKEN" \
  --preferred-server "$FPTN_SERVER" \
  --out-network-interface "$FPTN_INTERFACE" \
  --gateway-ip "$FPTN_GATEWAY" \
  --tun-interface-name fptn0 \
  --sni "${FPTN_SNI:-google.com}" \
  --bypass-method "${FPTN_BYPASS_METHOD:-sni-spoofing-yandex-26-4}" \
  --enable-split-tunnel false \
  --enable-ad-block false \
  --blacklist-domains '' \
  --connection-strategy "${FPTN_CONNECTION_STRATEGY:-rolling-tunnel}"
}

fptn_valid_ipv4() {
  case "$1" in ''|*[!0-9.]*) return 1 ;; esac
  printf '%s\n' "$1" | awk -F. 'NF!=4 {exit 1} {for(i=1;i<=4;i++) if($i=="" || $i>255) exit 1}'
}

fptn_check_data() (
  FPTN_ROUTE_ADDED=0
  FPTN_RULE_ADDED=0
  FPTN_TARGET=$(cat "$FPTN_DATA_DIR/target.txt")
  FPTN_DIRECT=$(cat "$FPTN_DATA_DIR/direct.txt")
  fptn_remove_test_rule() {
    if [ "$FPTN_RULE_ADDED" = 1 ]; then
      ip -4 rule del priority 50 iif lo to "$FPTN_TARGET/32" lookup 30090 2>/dev/null || true
    fi
    if [ "$FPTN_ROUTE_ADDED" = 1 ]; then
      ip -4 route del table 30090 "$FPTN_TARGET/32" dev fptn0 proto 186 2>/dev/null || true
    fi
    FPTN_CLEAN_OK=1
    if [ "$FPTN_RULE_ADDED" = 1 ]; then
      FPTN_RULES_NOW=$(ip -4 rule show) || return 1
      if printf '%s\n' "$FPTN_RULES_NOW" | awk '$1=="50:" {found=1} END {exit !found}'; then
        echo '[ПРОВЕРКА] Не удалось подтвердить удаление правила 50. Нужна проверка ip rule show.' >&2
        FPTN_CLEAN_OK=0
      else
        FPTN_RULE_ADDED=0
      fi
    fi
    if [ "$FPTN_ROUTE_ADDED" = 1 ]; then
      FPTN_ROUTES_NOW=$(ip -4 route show table 30090 2>/dev/null || true)
      if [ -n "$FPTN_ROUTES_NOW" ]; then
        echo '[ПРОВЕРКА] Таблица 30090 не пуста после очистки; чужие записи не удаляю.' >&2
        FPTN_CLEAN_OK=0
      else
        FPTN_ROUTE_ADDED=0
      fi
    fi
    [ "$FPTN_CLEAN_OK" = 1 ]
  }
  trap fptn_remove_test_rule EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP
  if ! fptn_valid_ipv4 "$FPTN_TARGET" || ! fptn_valid_ipv4 "$FPTN_DIRECT"; then
    echo '[ПРОВЕРКА] Сервис вернул некорректный IPv4.' >&2; exit 1
  fi
  FPTN_READY=0
  FPTN_WAIT=0
  while [ "$FPTN_WAIT" -lt 45 ]; do
    if [ -f "$FPTN_DATA_DIR/client.log" ]; then
      if grep -q 'Login failed\|Config error:' "$FPTN_DATA_DIR/client.log"; then
        echo '[ПРОВЕРКА] Подключение не состоялось; тестовые маршруты не добавлялись.' >&2; exit 1
      fi
      if grep -q 'Connection #1 READY' "$FPTN_DATA_DIR/client.log" && ip link show dev fptn0 >/dev/null 2>&1; then
        FPTN_READY=1; break
      fi
    fi
    sleep 1
    FPTN_WAIT=$((FPTN_WAIT+1))
  done
  [ "$FPTN_READY" = 1 ] || { echo '[ПРОВЕРКА] Туннель не стал готов за 45 секунд.' >&2; exit 1; }
  FPTN_ENDPOINT=$(awk '/VPN SERVER IP:/ {print $NF; exit}' "$FPTN_DATA_DIR/client.log")
  [ -n "$FPTN_ENDPOINT" ] && [ "$FPTN_ENDPOINT" != "$FPTN_TARGET" ] || {
    echo '[ПРОВЕРКА] Нельзя безопасно отделить проверочный адрес от VPN-сервера.' >&2; exit 1;
  }
  if ip -4 rule show | awk '$1=="50:" {found=1} END {exit !found}'; then
    echo '[ПРОВЕРКА] Приоритет 50 занят; существующие правила не трогаю.' >&2; exit 1
  fi
  FPTN_EXISTING=$(ip -4 route show table 30090 2>/dev/null || true)
  [ -z "$FPTN_EXISTING" ] || { echo '[ПРОВЕРКА] Таблица 30090 занята; тест остановлен.' >&2; exit 1; }
  ip -4 route add table 30090 "$FPTN_TARGET/32" dev fptn0 proto 186
  FPTN_ROUTE_ADDED=1
  # iif lo matches locally originated packets only. LAN devices keep their routes.
  ip -4 rule add priority 50 iif lo to "$FPTN_TARGET/32" lookup 30090
  FPTN_RULE_ADDED=1
  echo '[ПРОВЕРКА] Временный маршрут /32 установлен только для запроса с роутера.'
  if ! curl -4 --noproxy '*' --interface fptn0 --resolve "api.ipify.org:443:$FPTN_TARGET" \
    -fsS --connect-timeout 10 --max-time 20 https://api.ipify.org -o "$FPTN_DATA_DIR/vpn.txt"; then
    echo '[ПРОВЕРКА] Запрос через fptn0 не прошёл.' >&2; exit 1
  fi
  FPTN_VPN_IP=$(cat "$FPTN_DATA_DIR/vpn.txt")
  fptn_valid_ipv4 "$FPTN_VPN_IP" || { echo '[ПРОВЕРКА] Ответ через VPN не содержит IPv4.' >&2; exit 1; }
  printf '[ПРОВЕРКА] Обычный выход: %s\n[ПРОВЕРКА] Выход через VPN: %s\n' "$FPTN_DIRECT" "$FPTN_VPN_IP"
  if [ "$FPTN_DIRECT" = "$FPTN_VPN_IP" ]; then
    echo '[ПРОВЕРКА] Адреса совпали — отдельный выход VPN пока не подтверждён.' >&2
    exit 1
  fi
  echo '[ПРОВЕРКА] УСПЕХ: HTTPS-запрос прошёл через fptn0, внешний IPv4 изменился.'
  fptn_remove_test_rule
  echo '[ПРОВЕРКА] Тестовое правило и маршрут удалены.'
)

if [ "${FPTN_DATA_TEST:-0}" = 1 ]; then
  echo 'Тест на 90 секунд: один запрос проверки IP через временный /32. DNS и существующие маршруты не меняются.'
  fptn_check_data &
  FPTN_DATA_WORKER=$!
  fptn_run_client 2>&1 | tee "$FPTN_DATA_DIR/client.log"
  if wait "$FPTN_DATA_WORKER"; then
    FPTN_DATA_WORKER=''
  else
    FPTN_DATA_WORKER=''
    exit 1
  fi
else
  echo 'Тест подключения — 90 секунд. Ctrl+C остановит его. Маршруты и DNS не меняются.'
  fptn_run_client
fi
