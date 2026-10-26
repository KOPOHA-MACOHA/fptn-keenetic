#!/bin/sh
# Small, standalone UI update; existing C++ binary and network settings stay intact.
set -eu
export LC_ALL=C LANG=C
PATH=/opt/sbin:/opt/bin:/opt/usr/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
fail() { echo "ОШИБКА: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'Запустите от root.'
[ "${1:-}" = install ] || fail 'Использование: sh install-web-ui.sh install UI_COMMIT [LAN_IP] [CLIENT_COMMIT]'
REV=${2:-}
case "$REV" in *[!a-f0-9]*|'') fail 'Нужен полный SHA коммита.' ;; esac
[ "${#REV}" -eq 40 ] || fail 'Нужен полный SHA коммита.'
CLIENT_REV=${4:-c0386cbb73bf9ddf35e551c08d094ac0debf2466}
case "$CLIENT_REV" in *[!a-f0-9]*|'') fail 'Некорректный SHA бинарника.' ;; esac
[ "${#CLIENT_REV}" -eq 40 ] || fail 'Нужен полный SHA бинарника.'
LAN=${3:-192.168.1.1}
printf '%s\n' "$LAN" | awk -F. 'NF!=4 {exit 1} {for(i=1;i<=4;i++) if($i!~/^[0-9]+$/ || $i>255 || length($i)>3) exit 1}' || fail 'Некорректный IPv4 LAN.'
PORT=1111
ORIGIN=http://$LAN:$PORT
CONF=/opt/etc/fptn
WEB=/opt/share/fptn-web
FRAGMENT=/opt/etc/lighttpd/conf.d/95-fptn.conf
MAIN=/opt/etc/lighttpd/lighttpd.conf
for cmd in curl jq sha256sum lighttpd netstat ip mktemp timeout; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Не найдена команда $cmd."
done
[ -x /opt/bin/fptnctl ] && [ -s "$CONF/token" ] && [ -s "$CONF/server" ] || fail 'Сначала установите и настройте native-клиент FPTN.'
[ ! -d /opt/var/run/fptn-web/action ] || fail 'Панель выполняет операцию. Дождитесь её завершения.'
ip -4 addr show | grep -Fq "inet $LAN/" || fail 'Этот IPv4 не назначен роутеру.'
[ -f "$MAIN" ] || fail 'Не найдена конфигурация lighttpd.'
lighttpd -tt -f "$MAIN" || fail 'Текущая конфигурация lighttpd уже содержит ошибку.'
lighttpd -p -f "$MAIN" | grep -q '"mod_cgi"' || fail 'В существующем lighttpd не включён mod_cgi.'
SETENV_MODULE=
if ! lighttpd -p -f "$MAIN" | grep -Fq '"mod_setenv"'; then
  SETENV_MODULE='server.modules += ( "mod_setenv" )'
fi
if [ -e "$FRAGMENT" ]; then
  grep -Fq '# Managed by fptn-web-ui' "$FRAGMENT" || fail '95-fptn.conf занят чужой конфигурацией.'
fi
[ ! -d "$WEB" ] || [ -f "$WEB/.fptn-web-owned" ] || fail 'Каталог панели уже занят чужими файлами.'
if netstat -lnt 2>/dev/null | awk -v port="$PORT" '$4 ~ (":" port "$") {found=1} END {exit !found}'; then
  [ -f "$FRAGMENT" ] && [ -f "$WEB/.fptn-web-owned" ] || fail 'Порт 1111 занят. Изменений нет.'
fi
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
ORIGIN_EXISTED=no
META_EXISTED=no
CLIENT_UPDATE=no
WAS_RUNNING=no
SERVICE_STOPPED=no
CLIENT_SWAPPED=no
RESTART_ATTEMPTED=no
INSTALL_LOCK=no
finish() {
  trap ':' INT TERM HUP
  RESTORE_SAFE=yes
  if [ "$CHANGED" = yes ] && [ "$DONE" != yes ]; then
    echo 'Установка не завершилась. Восстанавливаю файлы и lighttpd.' >&2
    if [ "$SERVICE_STOPPED" = yes ] && { [ "$RESTART_ATTEMPTED" = yes ] || [ -d /opt/var/run/fptn-keenetic ]; }; then
      if ! sh "$STAGE/scripts/fptn-native-service.sh" stop; then
        echo 'Новый клиент не остановлен. Оставляю его файлы на месте; резервная копия сохранена.' >&2
        RESTORE_SAFE=no
      fi
    fi
    if [ "$RESTORE_SAFE" = yes ]; then
    if [ "$CLIENT_SWAPPED" = yes ]; then
      cp -p "$BACKUP/fptn-client-cli" /opt/libexec/fptn/fptn-client-cli.restore
      mv /opt/libexec/fptn/fptn-client-cli.restore /opt/libexec/fptn/fptn-client-cli
      cp -p "$BACKUP/SHA256SUMS" /opt/libexec/fptn/SHA256SUMS
    fi
    cp -p "$BACKUP/fptnctl" /opt/bin/fptnctl.restore
    mv /opt/bin/fptnctl.restore /opt/bin/fptnctl
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
echo 'Скачиваю панель и управляющий скрипт. Сборка зависимостей не требуется.'
mkdir -p "$STAGE/web" "$STAGE/scripts"
curl -fL --connect-timeout 15 --max-time 60 "$BASE/web/SHA256SUMS" -o "$STAGE/SHA256SUMS"
for path in web/index.html web/index.cgi web/style.css web/app.js web/api.cgi scripts/fptn-native-service.sh scripts/fptn-web-auth.sh scripts/fptn-web-auth.php scripts/fptn-web-operations.php; do
  curl -fL --connect-timeout 15 --max-time 60 "$BASE/$path" -o "$STAGE/$path"
done
(cd "$STAGE"; sha256sum -c SHA256SUMS) || fail 'Контрольные суммы не совпали.'
sh -n "$STAGE/scripts/fptn-web-auth.sh"
# Reuse the PHP runtime already used by nfqws. Credentials are created in the browser.
sh "$STAGE/scripts/fptn-web-auth.sh" info >/dev/null || fail 'Не удалось запустить PHP существующей панели nfqws. Файлы не менялись.'
sh -n "$STAGE/web/api.cgi"
sh -n "$STAGE/web/index.cgi"
# Exercise the real entry file before any installed file or process is changed.
sh "$STAGE/web/index.cgi" > "$STAGE/entry-check" || fail 'Стартовый CGI не смог прочитать страницу. Установленные файлы не менялись.'
grep -Fq 'data-ui-version="0.7.0"' "$STAGE/entry-check" || fail 'Стартовый CGI не выдал HTML панели. Установленные файлы не менялись.' 
sh -n "$STAGE/scripts/fptn-native-service.sh"
CLIENT_INTEGRITY=no
if (cd /opt/libexec/fptn; awk '$2=="fptn-client-cli"' SHA256SUMS | sha256sum -c - >/dev/null 2>&1); then CLIENT_INTEGRITY=yes; fi
if [ "$CLIENT_INTEGRITY" != yes ] || ! /opt/libexec/fptn/fptn-client-cli --help | grep -q -- '--keenetic-probe-only'; then
  CLIENT_UPDATE=yes
  mkdir -p "$STAGE/client"
  CLIENT_BASE=https://github.com/KOPOHA-MACOHA/fptn-keenetic/releases/download/native-$CLIENT_REV
  echo 'Для проверки серверов и состояния нужен клиент 0.4.0. Скачиваю готовый бинарник для Hopper.'
  curl -fL --connect-timeout 15 --max-time 180 "$CLIENT_BASE/fptn-client-cli" -o "$STAGE/client/fptn-client-cli"
  curl -fL --connect-timeout 15 --max-time 60 "$CLIENT_BASE/SHA256SUMS" -o "$STAGE/client/SHA256SUMS.all"
  awk '$2=="fptn-client-cli"' "$STAGE/client/SHA256SUMS.all" > "$STAGE/client/SHA256SUMS"
  [ -s "$STAGE/client/SHA256SUMS" ] || fail 'В выпуске нет контрольной суммы клиента.'
  (cd "$STAGE/client"; sha256sum -c SHA256SUMS) || fail 'Бинарник повреждён.'
  chmod 700 "$STAGE/client/fptn-client-cli"
  "$STAGE/client/fptn-client-cli" --help | grep -q -- '--keenetic-probe-only' || fail 'Бинарник не поддерживает диагностику или не подходит платформе.'
fi
# Existing settings must be accepted by the updated controller before installation.
sh "$STAGE/scripts/fptn-native-service.sh" get-settings >/dev/null
mkdir -p "$CONF" /opt/share /opt/var/run/fptn-web
chmod 700 "$CONF" /opt/var/run/fptn-web
BACKUP=$(mktemp -d "$CONF/web-install-backup.XXXXXX")
cp -p /opt/bin/fptnctl "$BACKUP/fptnctl"
if [ -f /opt/libexec/fptn/web-auth.sh ]; then AUTH_HELPER_EXISTED=yes; cp -p /opt/libexec/fptn/web-auth.sh "$BACKUP/web-auth.sh"; fi
if [ -f /opt/libexec/fptn/web-auth.php ]; then AUTH_PHP_EXISTED=yes; cp -p /opt/libexec/fptn/web-auth.php "$BACKUP/web-auth.php"; fi
if [ -f /opt/libexec/fptn/web-operations.php ]; then OPS_EXISTED=yes; cp -p /opt/libexec/fptn/web-operations.php "$BACKUP/web-operations.php"; fi
if [ "$CLIENT_UPDATE" = yes ]; then
  cp -p /opt/libexec/fptn/fptn-client-cli "$BACKUP/fptn-client-cli"
  cp -p /opt/libexec/fptn/SHA256SUMS "$BACKUP/SHA256SUMS"
fi
[ ! -e "$FRAGMENT" ] || cp -p "$FRAGMENT" "$BACKUP/95-fptn.conf"
[ ! -d "$WEB" ] || cp -pR "$WEB" "$BACKUP/web"
if [ -f "$CONF/web-origin" ]; then ORIGIN_EXISTED=yes; cp -p "$CONF/web-origin" "$BACKUP/web-origin"; fi
if [ -f "$CONF/web-meta.json" ]; then META_EXISTED=yes; cp -p "$CONF/web-meta.json" "$BACKUP/web-meta.json"; fi
echo "Резервная копия: $BACKUP"
RESERVED=$(printf '%s' '{"action":"update"}' | FPTN_OWNER_PID=$$ sh "$STAGE/scripts/fptn-web-auth.sh" op-reserve) || fail 'Параллельно началась операция панели. Изменений нет.'
INSTALL_LOCK=yes
FPTN_OPERATION_ID=$(printf '%s' "$RESERVED" | jq -er .id)
export FPTN_OPERATION_ID
printf '%s\n' "$BACKUP" > /opt/var/run/fptn-web/action/install-backup
CHANGED=yes
mkdir -p "$WEB"
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
printf '%s\n' "$ORIGIN" > "$CONF/web-origin"
CLIENT_VERSION=$(/opt/libexec/fptn/fptn-client-cli --version)
jq -n --arg version "$CLIENT_VERSION" --arg revision "$REV" '{uiVersion:"0.7.0",clientVersion:$version,revision:$revision}' > "$CONF/web-meta.json.new"
mv "$CONF/web-meta.json.new" "$CONF/web-meta.json"
cat > "$FRAGMENT" <<EOF
# Managed by fptn-web-ui. Credentials live outside the document root.
$SETENV_MODULE
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
  grep -Fq 'data-ui-version="0.7.0"' "$STAGE/page" || return 1
  PANEL_CACHE_WARNING=no
  grep -iq '^Cache-Control:.*no-store' "$STAGE/headers" || PANEL_CACHE_WARNING=yes
  PANEL_ERROR='Не прошла проверка ограничения доступа к API.'
  CODE=$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --connect-timeout 1 --max-time 2 "$ORIGIN/api.cgi?status" 2>/dev/null) || return 1
  [ "$CODE" = 401 ] || return 1
  PANEL_ERROR='API настройки входа не готово или отвечает другой версией.'
  curl --noproxy '*' -fsS --connect-timeout 1 --max-time 2 "$ORIGIN/api.cgi?auth-info" -o "$STAGE/auth-info.json" 2>/dev/null || return 1
  jq -e '.uiVersion=="0.7.0" and (.configured|type=="boolean") and (.rootAvailable|type=="boolean")' "$STAGE/auth-info.json" >/dev/null 2>&1 || return 1
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
echo 'lighttpd отвечает: страница и API панели 0.7.0 проверены.'
if [ "$PANEL_CACHE_WARNING" = yes ]; then
  echo "Откройте свежую страницу по ссылке $ORIGIN/?v=$REV (заголовок кэша применится после перезагрузки lighttpd)."
fi
if [ "$CLIENT_UPDATE" = yes ]; then
  [ ! -d /opt/var/run/fptn-keenetic ] || WAS_RUNNING=yes
  # Use the verified new stop implementation, not the installed faulty one.
  if ! sh "$STAGE/scripts/fptn-native-service.sh" stop; then
    fail 'Не удалось остановить старую службу. Бинарник не заменялся; возвращаю прежнюю панель.'
  fi
  SERVICE_STOPPED=yes
  CHANGED=yes
  cp "$STAGE/client/fptn-client-cli" /opt/libexec/fptn/fptn-client-cli.new
  chmod 700 /opt/libexec/fptn/fptn-client-cli.new
  # Arm restoration before rename so an interrupt after rename is also covered.
  CLIENT_SWAPPED=yes
  mv /opt/libexec/fptn/fptn-client-cli.new /opt/libexec/fptn/fptn-client-cli
  (cd /opt/libexec/fptn; sha256sum fptn-client-cli brotli > SHA256SUMS.new; mv SHA256SUMS.new SHA256SUMS)
fi
# Record the final ELF version after replacing it; the web preflight used the old client.
CLIENT_VERSION=$(/opt/libexec/fptn/fptn-client-cli --version)
jq -n --arg version "$CLIENT_VERSION" --arg revision "$REV" '{uiVersion:"0.7.0",clientVersion:$version,revision:$revision}' > "$CONF/web-meta.json.new"
mv "$CONF/web-meta.json.new" "$CONF/web-meta.json"

if [ "$CLIENT_UPDATE" = yes ] && [ "$WAS_RUNNING" = yes ]; then
  RESTART_ATTEMPTED=yes
  /opt/bin/fptnctl start || fail 'Обновлённый клиент не запустился.'
fi
DONE=yes
echo
echo "Панель 0.7.0: $ORIGIN/?v=$REV"
rm -f "$CONF/web-key"
echo 'Откройте панель: создайте логин и пароль или войдите через root Entware.'
echo 'Пароль в терминале не нужен. Существующие настройки входа сохраняются.'
echo 'Токен, выбранный сервер, маршруты и DNS сохранены. Автозапуск FPTN не включался.'
if [ "$CLIENT_UPDATE" = yes ]; then echo 'Бинарник обновлён; прежнее состояние службы восстановлено.'
else echo 'Для применения параметров используйте панель; supervisor обновится при следующем запуске.'; fi
