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
if ip link show dev fptn0 >/dev/null 2>&1; then
  echo 'Интерфейс fptn0 уже существует. Тест остановлен.' >&2
  exit 1
fi
FPTN_GATEWAY=$(ip -4 route show table main default | awk '$1=="default" && $2=="via" {print $3; exit}')
FPTN_INTERFACE=$(ip -4 route show table main default | awk '$1=="default" {for(i=1;i<NF;i++) if($i=="dev") {print $(i+1); exit}}')
[ -n "$FPTN_GATEWAY" ] && [ -n "$FPTN_INTERFACE" ] || { echo 'Не найден обычный шлюз.' >&2; exit 1; }
FPTN_TTY_STATE=$(stty -g)
trap 'stty "$FPTN_TTY_STATE" 2>/dev/null || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
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
echo 'Тест подключения — 90 секунд. Ctrl+C остановит его. Маршруты и DNS не меняются.'
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
  --connection-strategy rolling-tunnel
