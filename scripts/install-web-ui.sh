#!/bin/sh
# Small, standalone UI update; existing C++ binary and network settings stay intact.
set -eu
export LC_ALL=C LANG=C
PATH=/opt/sbin:/opt/bin:/opt/usr/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
fail() { echo "ОШИБКА: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'Запустите от root.'
[ "${1:-}" = install ] || fail 'Использование: sh install-web-ui.sh install COMMIT [LAN_IP]'
REV=${2:-}
case "$REV" in *[!a-f0-9]*|'') fail 'Нужен полный SHA коммита.' ;; esac
[ "${#REV}" -eq 40 ] || fail 'Нужен полный SHA коммита.'
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
KEY_CREATED=no
ORIGIN_EXISTED=no
finish() {
  trap '' INT TERM HUP
  if [ "$CHANGED" = yes ] && [ "$DONE" != yes ]; then
    echo 'Установка не завершилась. Восстанавливаю файлы и lighttpd.' >&2
    cp -p "$BACKUP/fptnctl" /opt/bin/fptnctl.restore
    mv /opt/bin/fptnctl.restore /opt/bin/fptnctl
    rm -rf "$WEB"
    [ ! -d "$BACKUP/web" ] || cp -pR "$BACKUP/web" "$WEB"
    if [ -f "$BACKUP/95-fptn.conf" ]; then cp -p "$BACKUP/95-fptn.conf" "$FRAGMENT"
    else rm -f "$FRAGMENT"; fi
    if [ "$ORIGIN_EXISTED" = yes ]; then cp -p "$BACKUP/web-origin" "$CONF/web-origin"
    else rm -f "$CONF/web-origin"; fi
    [ "$KEY_CREATED" = no ] || rm -f "$CONF/web-key"
    "$INIT" restart || echo 'Не удалось перезапустить lighttpd при откате; резервная копия сохранена.' >&2
  fi
  rm -rf "$STAGE"
}
trap finish EXIT
trap 'exit 1' INT TERM HUP
BASE=https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/$REV
echo 'Скачиваю панель и управляющий скрипт. Сборка зависимостей не требуется.'
mkdir -p "$STAGE/web" "$STAGE/scripts"
curl -fL --connect-timeout 15 --max-time 60 "$BASE/web/SHA256SUMS" -o "$STAGE/SHA256SUMS"
for path in web/index.html web/style.css web/app.js web/api.cgi scripts/fptn-native-service.sh; do
  curl -fL --connect-timeout 15 --max-time 60 "$BASE/$path" -o "$STAGE/$path"
done
(cd "$STAGE"; sha256sum -c SHA256SUMS) || fail 'Контрольные суммы не совпали.'
sh -n "$STAGE/web/api.cgi"
sh -n "$STAGE/scripts/fptn-native-service.sh"
# Existing settings must be accepted by the updated controller before installation.
sh "$STAGE/scripts/fptn-native-service.sh" get-settings >/dev/null
mkdir -p "$CONF" /opt/share /opt/var/run/fptn-web
chmod 700 "$CONF" /opt/var/run/fptn-web
BACKUP=$(mktemp -d "$CONF/web-install-backup.XXXXXX")
cp -p /opt/bin/fptnctl "$BACKUP/fptnctl"
[ ! -e "$FRAGMENT" ] || cp -p "$FRAGMENT" "$BACKUP/95-fptn.conf"
[ ! -d "$WEB" ] || cp -pR "$WEB" "$BACKUP/web"
if [ -f "$CONF/web-origin" ]; then ORIGIN_EXISTED=yes; cp -p "$CONF/web-origin" "$BACKUP/web-origin"; fi
echo "Резервная копия: $BACKUP"
CHANGED=yes
mkdir -p "$WEB"
chmod 755 "$WEB"
cp "$STAGE/web/"* "$WEB/"
chmod 644 "$WEB/index.html" "$WEB/style.css" "$WEB/app.js"
chmod 700 "$WEB/api.cgi"
: > "$WEB/.fptn-web-owned"
cp "$STAGE/scripts/fptn-native-service.sh" /opt/bin/fptnctl.new
chmod 700 /opt/bin/fptnctl.new
mv /opt/bin/fptnctl.new /opt/bin/fptnctl
if [ ! -s "$CONF/web-key" ]; then
  KEY_CREATED=yes
  # UUID from the kernel CSPRNG; no dependency on openssl or Python.
  tr -d '\n-' < /proc/sys/kernel/random/uuid > "$CONF/web-key"
fi
chmod 600 "$CONF/web-key"
printf '%s\n' "$ORIGIN" > "$CONF/web-origin"
cat > "$FRAGMENT" <<EOF
# Managed by fptn-web-ui. Credentials live outside the document root.
\$SERVER["socket"] == "$LAN:$PORT" {
    server.document-root = "$WEB"
    index-file.names = ( "index.html" )
    cgi.assign = ( ".cgi" => "/bin/sh" )
    server.max-request-size = 300
}
EOF
chmod 600 "$FRAGMENT"
lighttpd -tt -f "$MAIN" || fail 'lighttpd отверг настройки панели.'
"$INIT" restart || fail 'Не удалось перезапустить lighttpd.'
sleep 1
curl --noproxy '*' -fsS --connect-timeout 5 --max-time 10 "$ORIGIN/" >/dev/null || fail 'Страница панели не отвечает.'
CODE=$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 10 "$ORIGIN/api.cgi?status")
[ "$CODE" = 401 ] || fail 'Проверка ограничения доступа к API не прошла.'
KEY=$(cat "$CONF/web-key")
# Keep the panel key out of curl argv/process listings.
printf 'header = "Authorization: Bearer %s"\n' "$KEY" |
  curl --noproxy '*' -K - -fsS --max-time 15 "$ORIGIN/api.cgi?status" |
  jq -e '(.status|type=="string") and (.busy|type=="boolean")' >/dev/null || fail 'Авторизованный запрос к API не прошёл.'
DONE=yes
echo
echo "Панель: $ORIGIN/"
echo "Код доступа: $KEY"
echo 'Это код панели, не токен VPN. Сохраните его.'
echo 'FPTN, его токен, сервер, маршруты и DNS не переключались. Автозапуск FPTN не включался.'
echo 'Для применения параметров используйте панель. Старый supervisor обновится при следующем запуске клиента.'
