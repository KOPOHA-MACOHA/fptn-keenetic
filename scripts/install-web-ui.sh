#!/bin/sh
# Install/update the native service and panel from one verified runtime payload.
set -eu
export LC_ALL=C LANG=C
PATH=/opt/sbin:/opt/bin:/opt/usr/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
fail() { echo "ОШИБКА: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'Запустите от root.'
[ "${1:-}" = install ] || fail 'Использование: sh install-web-ui.sh install UI_COMMIT [LAN_IP] [CLIENT_COMMIT] [PORT]'
REV=${2:-}
case "$REV" in *[!a-f0-9]*|'') fail 'Нужен полный SHA коммита.' ;; esac
[ "${#REV}" -eq 40 ] || fail 'Нужен полный SHA коммита.'
CLIENT_REV=${4:-}
if [ -n "$CLIENT_REV" ]; then
case "$CLIENT_REV" in *[!a-f0-9]*|'') fail 'Некорректный SHA бинарника.' ;; esac
[ "${#CLIENT_REV}" -eq 40 ] || fail 'Нужен полный SHA бинарника.'
fi
LAN=${3:-192.168.1.1}
printf '%s\n' "$LAN" | awk -F. 'NF!=4 {exit 1} {for(i=1;i<=4;i++) if($i!~/^[0-9]+$/ || $i>255 || length($i)>3) exit 1}' || fail 'Некорректный IPv4 LAN.'
PORT=${5:-${FPTN_INSTALL_PORT:-}}
ORIGIN=
CONF=/opt/etc/fptn
WEB=/opt/share/fptn-web
FRAGMENT=/opt/etc/lighttpd/conf.d/95-fptn.conf
MAIN=/opt/etc/lighttpd/lighttpd.conf
HOOK=/opt/etc/ndm/netfilter.d/190-fptn-ipv6.sh
for cmd in curl jq sha256sum lighttpd netstat ip mktemp timeout; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Не найдена команда $cmd."
done
FRESH=no
if [ ! -e /opt/bin/fptnctl ]; then
  [ ! -e /opt/libexec/fptn ] || fail 'Каталог /opt/libexec/fptn уже существует без fptnctl. Сохраните его перед новой установкой.'
  [ ! -e "$WEB" ] && [ ! -e "$FRAGMENT" ] || fail 'Найдены остатки панели без службы. Сначала восстановите или удалите прежнюю установку.'
  [ ! -d /opt/var/run/fptn-keenetic ] || fail 'Найдено состояние прежнего клиента. Установка остановлена.'
  FRESH=yes
else
  [ -x /opt/bin/fptnctl ] || fail 'fptnctl существует, но не исполняется.'
fi
[ -n "${FPTN_OPERATION_ID:-}" ] || [ ! -d /opt/var/run/fptn-web/action ] || fail 'Панель выполняет операцию. Дождитесь её завершения.'
ip -4 addr show | grep -Fq "inet $LAN/" || fail 'Этот IPv4 не назначен роутеру.'
[ -f "$MAIN" ] || fail 'Не найдена конфигурация lighttpd.'
lighttpd -tt -f "$MAIN" || fail 'Текущая конфигурация lighttpd уже содержит ошибку.'
CGI_MODULE=
if ! lighttpd -p -f "$MAIN" | grep -Fq '"mod_cgi"'; then
  CGI_MODULE='server.modules += ( "mod_cgi" )'
fi
# Credentials and controls run as root, just like the existing nfqws panel.
if lighttpd -p -f "$MAIN" | grep -E 'server.username[[:space:]]*=' | grep -Ev '=[[:space:]]*"(root)?"[[:space:]]*$' >/dev/null; then
  fail 'lighttpd работает не от root. Его общие настройки не изменялись.'
fi
SETENV_MODULE=
if ! lighttpd -p -f "$MAIN" | grep -Fq '"mod_setenv"'; then
  SETENV_MODULE='server.modules += ( "mod_setenv" )'
fi
if [ -e "$FRAGMENT" ]; then
  grep -Fq '# Managed by fptn-web-ui' "$FRAGMENT" || fail '95-fptn.conf занят чужой конфигурацией.'
fi
if [ -e "$HOOK" ]; then grep -Fq '# Managed by fptn IPv6' "$HOOK" || fail 'IPv6 hook занят другим приложением.'; fi
[ ! -d "$WEB" ] || [ -f "$WEB/.fptn-web-owned" ] || fail 'Каталог панели уже занят чужими файлами.'
valid_port() {
  case "$1" in ''|*[!0-9]*|0*) return 1 ;; esac
  [ "${#1}" -le 5 ] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}
SAVED_PORT=
if [ -f "$CONF/web-origin" ]; then
  SAVED_ORIGIN=$(cat "$CONF/web-origin")
  SAVED_ENDPOINT=${SAVED_ORIGIN#http://}
  SAVED_PORT=${SAVED_ENDPOINT##*:}
  SAVED_LAN=${SAVED_ENDPOINT%:*}
  valid_port "$SAVED_PORT" && [ "$SAVED_ORIGIN" = "http://$SAVED_LAN:$SAVED_PORT" ] || fail 'Повреждён сохранённый адрес панели.'
fi
# A listener is ours only if its PID belongs to lighttpd and both the managed
# socket fragment and the saved origin identify precisely this endpoint.
port_available() {
  LISTENERS=$(netstat -lntp 2>/dev/null) || fail 'Не удалось проверить TCP-порты.'
  MATCHES=$(printf '%s\n' "$LISTENERS" | awk -v port="$1" '$1~/^tcp/ && $4 ~ (":" port "$") {print $4 " " $NF}')
  [ -n "$MATCHES" ] || return 0
  [ "$1" = "$SAVED_PORT" ] && [ -f "$WEB/.fptn-web-owned" ] && [ -f "$FRAGMENT" ] || return 1
  grep -Fq '$SERVER["socket"] == "'"$SAVED_LAN:$SAVED_PORT"'" {' "$FRAGMENT" || return 1
  printf '%s\n' "$MATCHES" | while read -r endpoint process; do
    [ "$endpoint" = "$SAVED_LAN:$SAVED_PORT" ] || exit 1
    case "$process" in */lighttpd) listener_pid=${process%/*} ;; *) exit 1 ;; esac
    case "$listener_pid" in ''|*[!0-9]*) exit 1 ;; esac
    [ "$(readlink "/proc/$listener_pid/exe" 2>/dev/null)" = "$(readlink -f "$(command -v lighttpd)")" ] || exit 1
  done
}
select_port() {
  # Web operations never read a terminal; regular updates preserve their port.
  if [ -n "$PORT" ]; then
    valid_port "$PORT" || fail 'TCP-порт должен быть целым числом от 1 до 65535.'
    port_available "$PORT" || fail "TCP-порт $PORT занят другим сервисом. Изменений нет."
    return
  fi
  if [ -n "${FPTN_OPERATION_ID:-}" ]; then
    [ -n "$SAVED_PORT" ] || fail 'Не найден сохранённый порт панели.'
    PORT=$SAVED_PORT
    port_available "$PORT" || fail "Сохранённый TCP-порт $PORT занят другим сервисом."
    return
  fi
  if ! ( : < /dev/tty ) 2>/dev/null; then
    [ -n "$SAVED_PORT" ] || fail 'Нет терминала. Передайте порт явно: sh install.sh install LAN_IP PORT.'
    PORT=$SAVED_PORT
    port_available "$PORT" || fail "TCP-порт $PORT занят. Передайте другой порт явно."
    return
  fi
  exec 3<> /dev/tty
  PROPOSED=${SAVED_PORT:-1111}
  while :; do
    printf 'TCP-порт панели [Enter — %s]: ' "$PROPOSED" >&3
    IFS= read -r ANSWER <&3 || fail 'Ввод порта прерван.'
    PORT=${ANSWER:-$PROPOSED}
    if ! valid_port "$PORT"; then echo 'Введите целый порт от 1 до 65535.' >&3; continue; fi
    if port_available "$PORT"; then break; fi
    echo "TCP-порт $PORT занят. Сервис на нём не изменялся." >&3
    if [ "$PORT" -lt 65535 ]; then PROPOSED=$((PORT+1))
    else PROPOSED=1111; echo 'Достигнут конец диапазона. Выберите другой порт.' >&3; fi
  done
  exec 3>&-
}
INIT=
for candidate in /opt/etc/init.d/S*lighttpd; do
  [ -x "$candidate" ] || continue
  [ -z "$INIT" ] || fail 'Найдено несколько init-скриптов lighttpd.'
  INIT=$candidate
done
[ -n "$INIT" ] || fail 'Не найден init-скрипт lighttpd.'
mkdir -p /opt/tmp
STAGE=$(mktemp -d /opt/tmp/fptn-web.XXXXXX)
BACKUP=
CHANGED=no
DONE=no
AUTH_HELPER_EXISTED=no
AUTH_PHP_EXISTED=no
OPS_EXISTED=no
TRANSPORT_EXISTED=no
UPDATE_EXISTED=no
CAPS_EXISTED=no
IDENTITY_HELPER_EXISTED=no
UPLINK_EXISTED=no
PLATFORM_HELPER_EXISTED=no
IPV6_HELPER_EXISTED=no
HOOK_EXISTED=no
IDENTITY_EXISTED=no
SOURCE_LOCK_EXISTED=no
SUMS_EXISTED=no
ORIGIN_EXISTED=no
META_EXISTED=no
CLIENT_UPDATE=no
SERVICE_UPDATE=no
WAS_RUNNING=no
SERVICE_STOPPED=no
CLIENT_SWAPPED=no
RESTART_ATTEMPTED=no
INSTALL_LOCK=no
DECODER_NEW=no
LICENSES_NEW=no
INTERFACE_NEW=no
finish() {
  trap ':' INT TERM HUP
  RESTORE_SAFE=yes
  if [ "$CHANGED" = yes ] && [ "$DONE" != yes ]; then
    echo 'Установка не завершилась. Восстанавливаю файлы и lighttpd.' >&2
    if [ "$SERVICE_STOPPED" = yes ] && { [ "$RESTART_ATTEMPTED" = yes ] || [ -d /opt/var/run/fptn-keenetic ]; }; then
      if [ "$FRESH" = no ] && ! sh "$STAGE/scripts/fptn-native-service.sh" stop; then
        echo 'Новый клиент не остановлен. Оставляю его файлы на месте; резервная копия сохранена.' >&2
        RESTORE_SAFE=no
      fi
    fi
    if [ "$RESTORE_SAFE" = yes ]; then
    if [ "$INTERFACE_NEW" = yes ] && [ -s "$CONF/native-interface" ]; then
      NEW_NATIVE=$(cat "$CONF/native-interface")
      # The service validated and created this name during this transaction only.
      if [ "$(cat "$CONF/native-creation-owner" 2>/dev/null)" = "$NEW_NATIVE:$FPTN_OPERATION_ID" ] && ndmc -c "no interface $NEW_NATIVE"; then rm -f "$CONF/native-interface" "$CONF/native-setup-complete" "$CONF/native-setup-unsaved" "$CONF/native-creation-owner"
      else echo "Не удалось удалить созданный $NEW_NATIVE; маркер владения сохранён." >&2; fi
    fi
    if [ "$CLIENT_SWAPPED" = yes ] && [ "$FRESH" = no ]; then
      cp -p "$BACKUP/fptn-client-cli" /opt/libexec/fptn/fptn-client-cli.restore
      mv /opt/libexec/fptn/fptn-client-cli.restore /opt/libexec/fptn/fptn-client-cli
    fi
    if [ "$SUMS_EXISTED" = yes ]; then cp -p "$BACKUP/SHA256SUMS" /opt/libexec/fptn/SHA256SUMS
    else rm -f /opt/libexec/fptn/SHA256SUMS; fi
    if [ "$SOURCE_LOCK_EXISTED" = yes ]; then cp -p "$BACKUP/sources.lock.json" /opt/libexec/fptn/sources.lock.json
    else rm -f /opt/libexec/fptn/sources.lock.json; fi
    if [ "$IDENTITY_EXISTED" = yes ]; then cp -p "$BACKUP/client-release.json" "$CONF/client-release.json"
    else rm -f "$CONF/client-release.json"; fi
    if [ "$IDENTITY_HELPER_EXISTED" = yes ]; then cp -p "$BACKUP/client-identity.sh" /opt/libexec/fptn/client-identity.sh
    else rm -f /opt/libexec/fptn/client-identity.sh; fi
    if [ "$FRESH" = yes ]; then
      rm -f /opt/bin/fptnctl
    else
      cp -p "$BACKUP/fptnctl" /opt/bin/fptnctl.restore
      mv /opt/bin/fptnctl.restore /opt/bin/fptnctl
    fi
    rm -rf "$WEB"
    [ ! -d "$BACKUP/web" ] || cp -pR "$BACKUP/web" "$WEB"
    if [ -f "$BACKUP/95-fptn.conf" ]; then cp -p "$BACKUP/95-fptn.conf" "$FRAGMENT"
    else rm -f "$FRAGMENT"; fi
    if [ "$ORIGIN_EXISTED" = yes ]; then cp -p "$BACKUP/web-origin" "$CONF/web-origin"
    else rm -f "$CONF/web-origin"; fi
    if [ "$META_EXISTED" = yes ]; then cp -p "$BACKUP/web-meta.json" "$CONF/web-meta.json"
    else rm -f "$CONF/web-meta.json"; fi
    if [ "$AUTH_HELPER_EXISTED" = yes ]; then cp -p "$BACKUP/web-auth.sh" /opt/libexec/fptn/web-auth.sh
    else rm -f /opt/libexec/fptn/web-auth.sh; fi
    if [ "$AUTH_PHP_EXISTED" = yes ]; then cp -p "$BACKUP/web-auth.php" /opt/libexec/fptn/web-auth.php
    else rm -f /opt/libexec/fptn/web-auth.php; fi
    if [ "$OPS_EXISTED" = yes ]; then cp -p "$BACKUP/web-operations.php" /opt/libexec/fptn/web-operations.php
    else rm -f /opt/libexec/fptn/web-operations.php; fi
    if [ "$TRANSPORT_EXISTED" = yes ]; then cp -p "$BACKUP/transport-plan.php" /opt/libexec/fptn/transport-plan.php
    else rm -f /opt/libexec/fptn/transport-plan.php; fi
    if [ "$UPDATE_EXISTED" = yes ]; then cp -p "$BACKUP/web-update.sh" /opt/libexec/fptn/web-update.sh
    else rm -f /opt/libexec/fptn/web-update.sh; fi
    if [ "$CAPS_EXISTED" = yes ]; then cp -p "$BACKUP/client-capabilities.sh" /opt/libexec/fptn/client-capabilities.sh
    else rm -f /opt/libexec/fptn/client-capabilities.sh; fi
    if [ "$HOOK_EXISTED" = yes ]; then cp -p "$BACKUP/190-fptn-ipv6.sh" "$HOOK"
    else rm -f "$HOOK"; fi
    if [ "$IPV6_HELPER_EXISTED" = yes ]; then cp -p "$BACKUP/ipv6.sh" /opt/libexec/fptn/ipv6.sh
    else rm -f /opt/libexec/fptn/ipv6.sh; fi
    if [ "$PLATFORM_HELPER_EXISTED" = yes ]; then cp -p "$BACKUP/platform.sh" /opt/libexec/fptn/platform.sh
    else rm -f /opt/libexec/fptn/platform.sh; fi
    if [ "$UPLINK_EXISTED" = yes ]; then cp -p "$BACKUP/uplink.sh" /opt/libexec/fptn/uplink.sh
    else rm -f /opt/libexec/fptn/uplink.sh; fi
    if [ "$FRESH" = yes ]; then rm -rf /opt/libexec/fptn
    elif [ "$DECODER_NEW" = yes ]; then rm -f /opt/libexec/fptn/brotli; fi
    for file in LICENSE-FPTN LICENSE-BROTLI BROTLI-SOURCE.txt; do
      if [ -f "$BACKUP/$file" ]; then cp -p "$BACKUP/$file" /opt/libexec/fptn/"$file"
      elif [ "$LICENSES_NEW" = yes ]; then rm -f /opt/libexec/fptn/"$file"; fi
    done
    if [ -f "$BACKUP/.runtime-managed" ]; then cp -p "$BACKUP/.runtime-managed" /opt/libexec/fptn/.runtime-managed
    else rm -f /opt/libexec/fptn/.runtime-managed; fi
    FPTN_OPERATION_ID= "$INIT" restart || echo 'Не удалось перезапустить lighttpd при откате; резервная копия сохранена.' >&2
    if [ "$SERVICE_STOPPED" = yes ] && [ "$WAS_RUNNING" = yes ]; then /opt/bin/fptnctl start || true; fi
    fi
  fi
  [ "$INSTALL_LOCK" = no ] || rm -rf /opt/var/run/fptn-web/action
  rm -rf "$STAGE"
}
trap finish EXIT
trap 'exit 1' INT TERM HUP
BASE=https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/$REV
fetch() {
  if [ -n "${FPTN_PAYLOAD_DIR:-}" ]; then
    case "$1" in
      "$BASE/"*) relative=${1#"$BASE/"} ;;
      https://github.com/KOPOHA-MACOHA/fptn-keenetic/releases/download/*) relative=client/${1##*/} ;;
      *) fail 'Неизвестный файл пакета.' ;;
    esac
    cp "$FPTN_PAYLOAD_DIR/$relative" "$2"
  else
    curl --proto '=https' --proto-redir '=https' -fL --connect-timeout 15 --max-time 180 "$1" -o "$2"
  fi
}
echo 'Скачиваю панель и управляющий скрипт. Сборка зависимостей не требуется.'
mkdir -p "$STAGE/web" "$STAGE/scripts" "$STAGE/client"
fetch "$BASE/web/SHA256SUMS" "$STAGE/SHA256SUMS"
for path in web/index.html web/index.cgi web/style.css web/app.js web/api.cgi scripts/fptn-native-service.sh scripts/fptn-web-auth.sh scripts/fptn-web-auth.php scripts/fptn-web-operations.php scripts/fptn-transport-plan.php scripts/fptn-web-update.sh scripts/fptn-client-capabilities.sh scripts/fptn-client-identity.sh scripts/fptn-platform.sh scripts/fptn-uplink.sh scripts/fptn-ipv6.sh runtime-assets.json client-release.json platform-releases.json; do
  fetch "$BASE/$path" "$STAGE/$path"
done
(cd "$STAGE"; sha256sum -c SHA256SUMS) || fail 'Контрольные суммы не совпали.'
PLATFORM=$(sh "$STAGE/scripts/fptn-platform.sh") || fail 'Установка на этой платформе не поддерживается.'
if [ "$PLATFORM" != mipsel-mips32r2-softfloat ]; then
  jq -e --arg p "$PLATFORM" '.schema==1 and (.platforms[$p]|type=="object")' "$STAGE/platform-releases.json" >/dev/null || fail 'Для этой платформы проверенный выпуск пока не опубликован.'
  jq --arg p "$PLATFORM" '.platforms[$p].client' "$STAGE/platform-releases.json" > "$STAGE/client-release.json"
  jq --arg p "$PLATFORM" '.platforms[$p].assets' "$STAGE/platform-releases.json" > "$STAGE/runtime-assets.json"
fi
[ "$(jq -r .platform "$STAGE/client-release.json")" = "$PLATFORM" ] || fail 'Бинарник предназначен для другой платформы.'
command -v ndmc >/dev/null 2>&1 || fail 'Не найден ndmc: нужна KeeneticOS.'
[ -c /dev/net/tun ] || fail 'Нет устройства /dev/net/tun: включите компонент TUN Keenetic.'
sh -n "$STAGE/scripts/fptn-web-auth.sh"
# Reuse the PHP runtime already used by nfqws. Credentials are created in the browser.
sh "$STAGE/scripts/fptn-web-auth.sh" info >/dev/null || fail 'Не удалось запустить PHP панели. Файлы не менялись.'
sh -n "$STAGE/web/api.cgi"
sh -n "$STAGE/web/index.cgi"
# Exercise the real entry file before any installed file or process is changed.
sh "$STAGE/web/index.cgi" > "$STAGE/entry-check" || fail 'Стартовый CGI не смог прочитать страницу. Установленные файлы не менялись.'
grep -Fq 'data-ui-version="0.16.0"' "$STAGE/entry-check" || fail 'Стартовый CGI не выдал HTML панели. Установленные файлы не менялись.' 
sh -n "$STAGE/scripts/fptn-native-service.sh"
TARGET=$(sh "$STAGE/scripts/fptn-client-identity.sh" validate "$STAGE/client-release.json") || fail 'Некорректное описание сборки клиента.'
TARGET_REV=$(printf '%s' "$TARGET" | jq -r .buildRevision)
TARGET_SUM=$(printf '%s' "$TARGET" | jq -r .sha256)
TARGET_VERSION=$(printf '%s' "$TARGET" | jq -r .clientVersion)
SOURCE_SUM=$(printf '%s' "$TARGET" | jq -r .sourceLockSha256)
[ -z "$CLIENT_REV" ] || [ "$CLIENT_REV" = "$TARGET_REV" ] || fail 'Каталог и описание сборки указывают разные коммиты клиента.'
CLIENT_REV=$TARGET_REV
[ -z "${FPTN_TARGET_CLIENT_VERSION:-}" ] || [ "$FPTN_TARGET_CLIENT_VERSION" = "$TARGET_VERSION" ] || fail 'В каталоге другая версия клиента.'
[ -z "${FPTN_TARGET_CLIENT_SHA256:-}" ] || [ "$FPTN_TARGET_CLIENT_SHA256" = "$TARGET_SUM" ] || fail 'В каталоге другая контрольная сумма клиента.'
[ -z "${FPTN_TARGET_UPSTREAM_VERSION:-}" ] || [ "$FPTN_TARGET_UPSTREAM_VERSION" = "$(printf '%s' "$TARGET" | jq -r .upstreamVersion)" ] || fail 'В каталоге другая версия исходного FPTN.'
[ -z "${FPTN_TARGET_UPSTREAM_REVISION:-}" ] || [ "$FPTN_TARGET_UPSTREAM_REVISION" = "$(printf '%s' "$TARGET" | jq -r .upstreamRevision)" ] || fail 'В каталоге другой коммит исходного FPTN.'
CURRENT_SUM=
if [ -f /opt/libexec/fptn/fptn-client-cli ]; then CURRENT_SUM=$(nice -n 10 sha256sum /opt/libexec/fptn/fptn-client-cli | awk '{print $1}'); fi
CLIENT_TAG=native-$CLIENT_REV
if [ "$PLATFORM" != mipsel-mips32r2-softfloat ]; then
  CLIENT_TAG=port-$PLATFORM-$CLIENT_REV
  [ "$(jq -r .clientRelease "$STAGE/runtime-assets.json")" = "$CLIENT_TAG" ] || fail 'Некорректный тег выпуска платформы.'
fi
CLIENT_BASE=https://github.com/KOPOHA-MACOHA/fptn-keenetic/releases/download/$CLIENT_TAG
if [ "$CURRENT_SUM" != "$TARGET_SUM" ]; then
  CLIENT_UPDATE=yes
  mkdir -p "$STAGE/client"
  echo "Скачиваю клиент $TARGET_VERSION, сборка $CLIENT_REV."
  fetch "$CLIENT_BASE/fptn-client-cli" "$STAGE/client/fptn-client-cli"
  printf '%s  fptn-client-cli\n' "$TARGET_SUM" > "$STAGE/client/SHA256SUMS"
  (cd "$STAGE/client"; sha256sum -c SHA256SUMS) || fail 'Бинарник не соответствует закреплённой сборке.'
  chmod 700 "$STAGE/client/fptn-client-cli"
else echo 'Бинарник клиента уже соответствует выпуску; замена не требуется.'; fi
if [ -f /opt/libexec/fptn/sources.lock.json ] && [ "$(sha256sum /opt/libexec/fptn/sources.lock.json | awk '{print $1}')" = "$SOURCE_SUM" ]; then
  cp /opt/libexec/fptn/sources.lock.json "$STAGE/sources.lock.json"
else fetch "$CLIENT_BASE/sources.lock.json" "$STAGE/sources.lock.json"; fi
printf '%s  %s\n' "$SOURCE_SUM" "$STAGE/sources.lock.json" | sha256sum -c - || fail 'Не совпал список исходников сборки.'
printf '%s' "$TARGET" | jq -r .upstreamRevision > "$STAGE/upstream-revision"
jq -e --arg rev "$(cat "$STAGE/upstream-revision")" '.fptn==$rev' "$STAGE/sources.lock.json" >/dev/null || fail 'Список исходников указывает другой FPTN.'
# Bootstrap the previously tested static token decoder independently of temporary folders.
ASSETS=$STAGE/runtime-assets.json
jq -e --arg platform "$PLATFORM" 'def hex($n): type=="string" and length==$n and all(explode[]; (.>=48 and .<=57) or (.>=97 and .<=102));
 .schema==1 and .platform==$platform and (.decoderRelease|type=="string" and
   (if $platform=="mipsel-mips32r2-softfloat" then startswith("decoder-") and (.[8:]|hex(40))
    else startswith("port-"+$platform+"-") and (.[(6+($platform|length)):]|hex(40)) end)) and
 (.decoderSha256|hex(64)) and (.decoderLicenseSha256|hex(64)) and (.decoderSourceSha256|hex(64)) and (.clientLicenseSha256|hex(64))' "$ASSETS" >/dev/null || fail 'Некорректное описание вспомогательных файлов.'
DECODER_BASE=https://github.com/KOPOHA-MACOHA/fptn-keenetic/releases/download/$(jq -r .decoderRelease "$ASSETS")
if [ ! -x /opt/libexec/fptn/brotli ]; then
  [ ! -e /opt/libexec/fptn/brotli ] || fail 'Существующий Brotli не исполняется. Проверьте установку.'
  fetch "$DECODER_BASE/brotli" "$STAGE/client/brotli"
  printf '%s  %s\n' "$(jq -r .decoderSha256 "$ASSETS")" "$STAGE/client/brotli" | sha256sum -c - || fail 'Не совпала контрольная сумма Brotli.'
  chmod 700 "$STAGE/client/brotli"
  DECODER_NEW=yes
fi
# Include licenses in clean installations; legacy updates retain existing copies.
if [ "$FRESH" = yes ]; then
  fetch "$CLIENT_BASE/LICENSE-FPTN" "$STAGE/client/LICENSE-FPTN"
  fetch "$DECODER_BASE/LICENSE-BROTLI" "$STAGE/client/LICENSE-BROTLI"
  fetch "$DECODER_BASE/BROTLI-SOURCE.txt" "$STAGE/client/BROTLI-SOURCE.txt"
  for pair in LICENSE-FPTN:clientLicenseSha256 LICENSE-BROTLI:decoderLicenseSha256 BROTLI-SOURCE.txt:decoderSourceSha256; do
    printf '%s  %s\n' "$(jq -r ".${pair#*:}" "$ASSETS")" "$STAGE/client/${pair%:*}" | sha256sum -c - || fail 'Не совпала лицензия или источник.'
  done
fi
# Validate current settings against the exact client that will remain installed.
CAP_CLIENT=/opt/libexec/fptn/fptn-client-cli
[ "$CLIENT_UPDATE" = no ] || CAP_CLIENT=$STAGE/client/fptn-client-cli
sh "$STAGE/scripts/fptn-client-capabilities.sh" "$CAP_CLIENT" > "$STAGE/capabilities.json" || fail 'Не удалось определить возможности клиента. Установленные файлы не менялись.'
jq -e --arg sum "$TARGET_SUM" --arg version "$TARGET_VERSION" '.sha256==$sum and .clientVersion==$version' "$STAGE/capabilities.json" >/dev/null || fail 'Возможности клиента не совпали с описанием сборки.'
if [ -s "$CONF/native-interface" ] && [ "$(cat "$CONF/native-interface")" != OpkgTun0 ]; then
  jq -e '.nativeInterfaceSelection==true' "$STAGE/capabilities.json" >/dev/null || fail 'Клиент выпуска не поддерживает сохранённый OpkgTun. Откат на несовместимый клиент запрещён.'
fi
select_port
ORIGIN=http://$LAN:$PORT
FPTN_CAP_CLIENT=$CAP_CLIENT sh "$STAGE/scripts/fptn-native-service.sh" get-settings >/dev/null
# A changed supervisor must run now, even when the ELF is byte-identical.
# UI-only/provenance-only updates keep an unchanged running service in place.
if [ "$FRESH" = no ] && [ "$(sha256sum /opt/bin/fptnctl | awk '{print $1}')" != "$(sha256sum "$STAGE/scripts/fptn-native-service.sh" | awk '{print $1}')" ]; then SERVICE_UPDATE=yes; fi
mkdir -p "$CONF" /opt/share /opt/var/run/fptn-web
chmod 700 "$CONF" /opt/var/run/fptn-web
BACKUP=$(mktemp -d "$CONF/web-install-backup.XXXXXX")
[ "$FRESH" = yes ] || cp -p /opt/bin/fptnctl "$BACKUP/fptnctl"
if [ -f /opt/libexec/fptn/web-auth.sh ]; then AUTH_HELPER_EXISTED=yes; cp -p /opt/libexec/fptn/web-auth.sh "$BACKUP/web-auth.sh"; fi
if [ -f /opt/libexec/fptn/web-auth.php ]; then AUTH_PHP_EXISTED=yes; cp -p /opt/libexec/fptn/web-auth.php "$BACKUP/web-auth.php"; fi
if [ -f /opt/libexec/fptn/web-operations.php ]; then OPS_EXISTED=yes; cp -p /opt/libexec/fptn/web-operations.php "$BACKUP/web-operations.php"; fi
if [ -f /opt/libexec/fptn/transport-plan.php ]; then TRANSPORT_EXISTED=yes; cp -p /opt/libexec/fptn/transport-plan.php "$BACKUP/transport-plan.php"; fi
if [ "$CLIENT_UPDATE" = yes ] && [ "$FRESH" = no ]; then
  cp -p /opt/libexec/fptn/fptn-client-cli "$BACKUP/fptn-client-cli"
fi
[ ! -e "$FRAGMENT" ] || cp -p "$FRAGMENT" "$BACKUP/95-fptn.conf"
[ ! -d "$WEB" ] || cp -pR "$WEB" "$BACKUP/web"
if [ -f "$CONF/web-origin" ]; then ORIGIN_EXISTED=yes; cp -p "$CONF/web-origin" "$BACKUP/web-origin"; fi
if [ -f "$CONF/web-meta.json" ]; then META_EXISTED=yes; cp -p "$CONF/web-meta.json" "$BACKUP/web-meta.json"; fi
if [ -f /opt/libexec/fptn/web-update.sh ]; then UPDATE_EXISTED=yes; cp -p /opt/libexec/fptn/web-update.sh "$BACKUP/web-update.sh"; fi
if [ -f /opt/libexec/fptn/client-capabilities.sh ]; then CAPS_EXISTED=yes; cp -p /opt/libexec/fptn/client-capabilities.sh "$BACKUP/client-capabilities.sh"; fi
if [ -f /opt/libexec/fptn/SHA256SUMS ]; then SUMS_EXISTED=yes; cp -p /opt/libexec/fptn/SHA256SUMS "$BACKUP/SHA256SUMS"; fi
if [ -f /opt/libexec/fptn/sources.lock.json ]; then SOURCE_LOCK_EXISTED=yes; cp -p /opt/libexec/fptn/sources.lock.json "$BACKUP/sources.lock.json"; fi
if [ -f "$CONF/client-release.json" ]; then IDENTITY_EXISTED=yes; cp -p "$CONF/client-release.json" "$BACKUP/client-release.json"; fi
if [ -f /opt/libexec/fptn/client-identity.sh ]; then IDENTITY_HELPER_EXISTED=yes; cp -p /opt/libexec/fptn/client-identity.sh "$BACKUP/client-identity.sh"; fi
for file in LICENSE-FPTN LICENSE-BROTLI BROTLI-SOURCE.txt .runtime-managed; do
  [ ! -f /opt/libexec/fptn/"$file" ] || cp -p /opt/libexec/fptn/"$file" "$BACKUP/$file"
done
if [ -f /opt/libexec/fptn/platform.sh ]; then PLATFORM_HELPER_EXISTED=yes; cp -p /opt/libexec/fptn/platform.sh "$BACKUP/platform.sh"; fi
if [ -f /opt/libexec/fptn/uplink.sh ]; then UPLINK_EXISTED=yes; cp -p /opt/libexec/fptn/uplink.sh "$BACKUP/uplink.sh"; fi
if [ -f /opt/libexec/fptn/ipv6.sh ]; then IPV6_HELPER_EXISTED=yes; cp -p /opt/libexec/fptn/ipv6.sh "$BACKUP/ipv6.sh"; fi
if [ -f "$HOOK" ]; then HOOK_EXISTED=yes; cp -p "$HOOK" "$BACKUP/190-fptn-ipv6.sh"; fi
echo "Резервная копия: $BACKUP"
if [ -n "${FPTN_OPERATION_ID:-}" ]; then
  CURRENT=$(sh "$STAGE/scripts/fptn-web-auth.sh" op-state)
  printf '%s' "$CURRENT" | jq -e --arg id "$FPTN_OPERATION_ID" '.busy==true' >/dev/null || fail 'Потеряно владение операцией обновления.'
  jq -e --arg id "$FPTN_OPERATION_ID" '.id==$id' /opt/var/run/fptn-web/action/owner.json >/dev/null || fail 'Изменилась операция обновления.'
  [ "$(cat /opt/var/run/fptn-web/action/action)" = update ] || fail 'Занята другая операция.'
else
  RESERVED=$(printf '%s' '{"action":"update"}' | FPTN_OWNER_PID=$$ sh "$STAGE/scripts/fptn-web-auth.sh" op-reserve) || fail 'Параллельно началась операция панели. Изменений нет.'
  INSTALL_LOCK=yes
  FPTN_OPERATION_ID=$(printf '%s' "$RESERVED" | jq -er .id)
  export FPTN_OPERATION_ID
fi
printf '%s\n' "$BACKUP" > /opt/var/run/fptn-web/action/install-backup
# A different process may have taken the chosen port while files downloaded.
port_available "$PORT" || fail "TCP-порт $PORT успел занять другой сервис. Установленные файлы не менялись."
CHANGED=yes
mkdir -p "$WEB" /opt/libexec/fptn /opt/bin
chmod 700 /opt/libexec/fptn
chmod 755 "$WEB"
cp "$STAGE/web/"* "$WEB/"
chmod 644 "$WEB/index.html" "$WEB/style.css" "$WEB/app.js"
chmod 700 "$WEB/api.cgi" "$WEB/index.cgi"
: > "$WEB/.fptn-web-owned"
cp "$STAGE/scripts/fptn-native-service.sh" /opt/bin/fptnctl.new
chmod 700 /opt/bin/fptnctl.new
mv /opt/bin/fptnctl.new /opt/bin/fptnctl
cp "$STAGE/scripts/fptn-web-auth.sh" /opt/libexec/fptn/web-auth.sh.new
chmod 700 /opt/libexec/fptn/web-auth.sh.new
mv /opt/libexec/fptn/web-auth.sh.new /opt/libexec/fptn/web-auth.sh
cp "$STAGE/scripts/fptn-web-auth.php" /opt/libexec/fptn/web-auth.php.new
chmod 600 /opt/libexec/fptn/web-auth.php.new
mv /opt/libexec/fptn/web-auth.php.new /opt/libexec/fptn/web-auth.php
cp "$STAGE/scripts/fptn-web-operations.php" /opt/libexec/fptn/web-operations.php.new
chmod 600 /opt/libexec/fptn/web-operations.php.new
mv /opt/libexec/fptn/web-operations.php.new /opt/libexec/fptn/web-operations.php
cp "$STAGE/scripts/fptn-transport-plan.php" /opt/libexec/fptn/transport-plan.php.new
chmod 600 /opt/libexec/fptn/transport-plan.php.new
mv /opt/libexec/fptn/transport-plan.php.new /opt/libexec/fptn/transport-plan.php
cp "$STAGE/scripts/fptn-web-update.sh" /opt/libexec/fptn/web-update.sh.new
chmod 700 /opt/libexec/fptn/web-update.sh.new
mv /opt/libexec/fptn/web-update.sh.new /opt/libexec/fptn/web-update.sh
cp "$STAGE/scripts/fptn-client-capabilities.sh" /opt/libexec/fptn/client-capabilities.sh.new
chmod 700 /opt/libexec/fptn/client-capabilities.sh.new
mv /opt/libexec/fptn/client-capabilities.sh.new /opt/libexec/fptn/client-capabilities.sh
cp "$STAGE/scripts/fptn-client-identity.sh" /opt/libexec/fptn/client-identity.sh.new
chmod 700 /opt/libexec/fptn/client-identity.sh.new
mv /opt/libexec/fptn/client-identity.sh.new /opt/libexec/fptn/client-identity.sh
cp "$STAGE/scripts/fptn-ipv6.sh" /opt/libexec/fptn/ipv6.sh.new
chmod 700 /opt/libexec/fptn/ipv6.sh.new
mv /opt/libexec/fptn/ipv6.sh.new /opt/libexec/fptn/ipv6.sh
mkdir -p /opt/etc/ndm/netfilter.d
cat > "$HOOK" <<'HOOKEOF'
#!/bin/sh
# Managed by fptn IPv6. Restore only the optional interface-scoped NAT66 rule.
PATH=/opt/sbin:/opt/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
if [ -x /opt/bin/fptnctl ] && ! /opt/bin/fptnctl sync-ipv6 >/dev/null 2>&1; then
  command -v logger >/dev/null 2>&1 && logger -t fptn 'Не удалось восстановить IPv6 NAT FPTN' || true
fi
HOOKEOF
chmod 700 "$HOOK"
cp "$STAGE/scripts/fptn-platform.sh" /opt/libexec/fptn/platform.sh.new
chmod 700 /opt/libexec/fptn/platform.sh.new
mv /opt/libexec/fptn/platform.sh.new /opt/libexec/fptn/platform.sh
cp "$STAGE/scripts/fptn-uplink.sh" /opt/libexec/fptn/uplink.sh.new
chmod 700 /opt/libexec/fptn/uplink.sh.new
mv /opt/libexec/fptn/uplink.sh.new /opt/libexec/fptn/uplink.sh
if [ "$DECODER_NEW" = yes ]; then cp "$STAGE/client/brotli" /opt/libexec/fptn/brotli; chmod 700 /opt/libexec/fptn/brotli; fi
if [ "$FRESH" = yes ]; then
  LICENSES_NEW=yes
  for file in LICENSE-FPTN LICENSE-BROTLI BROTLI-SOURCE.txt; do cp "$STAGE/client/$file" /opt/libexec/fptn/"$file"; done
fi
printf '%s\n' "$ORIGIN" > "$CONF/web-origin"
CLIENT_VERSION=$(jq -r '.clientVersion // "неизвестна"' "$CONF/web-meta.json" 2>/dev/null || echo неизвестна)
jq -n --arg version "$CLIENT_VERSION" --arg revision "$REV" '{uiVersion:"0.16.0",clientVersion:$version,revision:$revision}' > "$CONF/web-meta.json.new"
mv "$CONF/web-meta.json.new" "$CONF/web-meta.json"
cat > "$FRAGMENT" <<EOF
# Managed by fptn-web-ui. Credentials live outside the document root.
$SETENV_MODULE
$CGI_MODULE
\$SERVER["socket"] == "$LAN:$PORT" {
    server.document-root = "$WEB"
    index-file.names = ( "index.html" )
    setenv.add-response-header = (
        "Cache-Control" => "no-store, max-age=0",
        "Pragma" => "no-cache",
        "X-Frame-Options" => "DENY"
    )
    cgi.assign = ( ".cgi" => "/bin/sh" )
    server.max-request-size = 300
}
EOF
chmod 600 "$FRAGMENT"
lighttpd -tt -f "$MAIN" || fail 'lighttpd отверг настройки панели.'
if ! FPTN_OPERATION_ID= "$INIT" restart; then
  echo 'Init-скрипт сообщил failed. Проверяю, поднялись ли lighttpd и панель.' >&2
fi
check_panel() {
  PANEL_ERROR='Страница панели не отвечает.'
  curl --noproxy '*' -fsS -D "$STAGE/headers" --connect-timeout 1 --max-time 2 "$ORIGIN/" -o "$STAGE/page" 2>/dev/null || return 1
  PANEL_ERROR='На порту открылась другая версия панели.'
  grep -Fq 'data-ui-version="0.16.0"' "$STAGE/page" || return 1
  PANEL_CACHE_WARNING=no
  grep -iq '^Cache-Control:.*no-store' "$STAGE/headers" || PANEL_CACHE_WARNING=yes
  PANEL_ERROR='Не прошла проверка ограничения доступа к API.'
  CODE=$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --connect-timeout 1 --max-time 2 "$ORIGIN/api.cgi?status" 2>/dev/null) || return 1
  [ "$CODE" = 401 ] || return 1
  PANEL_ERROR='API настройки входа не готово или отвечает другой версией.'
  curl --noproxy '*' -fsS --connect-timeout 1 --max-time 2 "$ORIGIN/api.cgi?auth-info" -o "$STAGE/auth-info.json" 2>/dev/null || return 1
  jq -e '.uiVersion=="0.16.0" and (.configured|type=="boolean") and (.rootAvailable|type=="boolean")' "$STAGE/auth-info.json" >/dev/null 2>&1 || return 1
  return 0
}
# rc.func may report failed before the daemon becomes visible. Decide from the
# actual listener's responses, also when restart itself returned success.
PANEL_DEADLINE=$(( $(date +%s) + 30 ))
until check_panel; do
  if [ "$(date +%s)" -ge "$PANEL_DEADLINE" ]; then
    mkdir -p "$BACKUP/http-diagnostic"
    for item in headers page auth-info.json; do
      [ ! -f "$STAGE/$item" ] || cp "$STAGE/$item" "$BACKUP/http-diagnostic/$item"
    done
    echo "Ответ сервера сохранён: $BACKUP/http-diagnostic" >&2
    if [ -f "$STAGE/page" ]; then
      echo "Размер ответа страницы: $(wc -c < "$STAGE/page") байт." >&2
    fi
    fail "Панель не готова после ожидания: $PANEL_ERROR"
  fi
  sleep 1
done
echo 'lighttpd отвечает: страница и API панели 0.16.0 проверены.'
if [ "$PANEL_CACHE_WARNING" = yes ]; then
  echo "Откройте свежую страницу по ссылке $ORIGIN/?v=$REV (заголовок кэша применится после перезагрузки lighttpd)."
fi
if [ "$CLIENT_UPDATE" = yes ] || [ "$SERVICE_UPDATE" = yes ]; then
  [ ! -d /opt/var/run/fptn-keenetic ] || WAS_RUNNING=yes
  # Use the verified new stop implementation, not the installed faulty one.
  if [ "$FRESH" = no ] && ! sh "$STAGE/scripts/fptn-native-service.sh" stop; then
    fail 'Не удалось остановить старую службу. Бинарник не заменялся; возвращаю прежнюю панель.'
  fi
  SERVICE_STOPPED=yes
  CHANGED=yes
fi
if [ "$CLIENT_UPDATE" = yes ]; then
  cp "$STAGE/client/fptn-client-cli" /opt/libexec/fptn/fptn-client-cli.new
  chmod 700 /opt/libexec/fptn/fptn-client-cli.new
  # Arm restoration before rename so an interrupt after rename is also covered.
  CLIENT_SWAPPED=yes
  mv /opt/libexec/fptn/fptn-client-cli.new /opt/libexec/fptn/fptn-client-cli
fi
# Recheck settings against the final installed ELF, including enabled fallbacks.
/opt/bin/fptnctl get-settings >/dev/null || fail 'Установленный клиент не поддерживает сохранённые параметры.'
/opt/bin/fptnctl get-fallback >/dev/null || fail 'Установленный клиент не поддерживает резервные профили.'
# Bind source metadata and observed version to the final verified binary.
FINAL_CAPS=$(sh /opt/libexec/fptn/client-capabilities.sh)
printf '%s' "$FINAL_CAPS" | jq -e --arg sum "$TARGET_SUM" --arg version "$TARGET_VERSION" '.sha256==$sum and .clientVersion==$version' >/dev/null || fail 'Установлен другой бинарник клиента.'
cp "$STAGE/sources.lock.json" /opt/libexec/fptn/sources.lock.json.new
chmod 600 /opt/libexec/fptn/sources.lock.json.new
mv /opt/libexec/fptn/sources.lock.json.new /opt/libexec/fptn/sources.lock.json
(cd /opt/libexec/fptn; sha256sum fptn-client-cli brotli > SHA256SUMS.new; mv SHA256SUMS.new SHA256SUMS)
if [ ! -s "$CONF/native-interface" ]; then
  # Mark before setup: an interruption after creation must still be rolled back.
  INTERFACE_NEW=yes
  FPTN_INTERFACE_TRANSACTION=$FPTN_OPERATION_ID /opt/bin/fptnctl setup-interface || fail 'Не удалось создать собственный интерфейс FPTN.'
fi
if [ "$SERVICE_STOPPED" = yes ] && [ "$WAS_RUNNING" = yes ]; then
  RESTART_ATTEMPTED=yes
  /opt/bin/fptnctl start || fail 'Обновлённый клиент не запустился.'
fi
printf '%s\n' "$TARGET" > "$CONF/client-release.json.new"
chmod 600 "$CONF/client-release.json.new"
mv "$CONF/client-release.json.new" "$CONF/client-release.json"
printf '%s' "$TARGET" | jq --arg revision "$REV" '. + {uiVersion:"0.16.0",revision:$revision,clientRevision:.buildRevision,clientSha256:.sha256}' > "$CONF/web-meta.json.new"
mv "$CONF/web-meta.json.new" "$CONF/web-meta.json"
: > /opt/libexec/fptn/.runtime-managed
DONE=yes
if [ "$INTERFACE_NEW" = yes ] && [ "$(cat "$CONF/native-creation-owner" 2>/dev/null)" = "$(cat "$CONF/native-interface" 2>/dev/null):$FPTN_OPERATION_ID" ]; then
  rm -f "$CONF/native-creation-owner"
fi
echo
echo "Панель 0.16.0: $ORIGIN/?v=$REV"
rm -f "$CONF/web-key"
echo 'Откройте панель: создайте логин и пароль или войдите через root Entware.'
echo 'Пароль в терминале не нужен. Существующие настройки входа сохраняются.'
if [ "$FRESH" = yes ]; then
  echo 'В браузере вставьте токен, выберите сервер и сохраните настройки. Интерфейс уже создан; запустите FPTN после сохранения настроек.'
else echo 'Токен, выбранный сервер, маршруты и DNS сохранены.'; fi
echo 'Автозапуск FPTN не включался.' 
if [ "$CLIENT_UPDATE" = yes ]; then echo 'Бинарник обновлён; прежнее состояние службы восстановлено.'
elif [ "$SERVICE_UPDATE" = yes ]; then echo 'Supervisor обновлён; прежнее состояние службы восстановлено.'
else echo 'Служба и бинарник не изменились; повторный запуск не требовался.'; fi
