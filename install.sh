#!/bin/sh
# Public bootstrap; install only missing Entware packages, then verify the pinned updater.
set -eu
umask 077
export LC_ALL=C LANG=C
PATH=/opt/sbin:/opt/bin:/opt/usr/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
fail() { echo "FPTN: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'Запустите от root Entware.'
[ "${1:-install}" = install ] || fail 'Использование: sh install.sh install [LAN_IP]'
LAN=${2:-192.168.1.1}
printf '%s\n' "$LAN" | awk -F. 'NF!=4 {exit 1} {for(i=1;i<=4;i++) if($i!~/^[0-9]+$/ || $i>255 || length($i)>3) exit 1}' || fail 'Некорректный IPv4 LAN.'
[ -r /opt/etc/entware_release ] || fail 'Требуется установленный Entware.'
value() { awk -F= -v key="$1" '$1==key {gsub(/["\047\r]/,"",$2); print $2}' /opt/etc/entware_release; }
[ "$(value arch):$(value cpu_type):$(value float)" = mipsel:mips32r2:soft ] || fail 'Эта готовая сборка только для Entware mipsel / mips32r2 / soft-float.'
command -v ndmc >/dev/null 2>&1 || fail 'Требуется KeeneticOS.'
[ -c /dev/net/tun ] || fail 'Включите компонент TUN Keenetic.'
command -v opkg >/dev/null 2>&1 || fail 'Не найден opkg.'
NEW_HTTP=no
if [ ! -f /opt/etc/lighttpd/lighttpd.conf ] && ! command -v lighttpd >/dev/null 2>&1; then
  pidof lighttpd >/dev/null 2>&1 && fail 'Уже работает lighttpd с другой конфигурацией.'
  NEW_HTTP=yes
fi
PACKAGES=
for pair in curl:curl jq:jq lighttpd:lighttpd; do
  command -v "${pair%:*}" >/dev/null 2>&1 || PACKAGES="$PACKAGES ${pair#*:}"
done
[ -x /opt/sbin/ip ] || [ -x /opt/libexec/ip-full ] || PACKAGES="$PACKAGES ip-full"
if ! command -v php-cgi >/dev/null 2>&1 && ! command -v php >/dev/null 2>&1; then PACKAGES="$PACKAGES php8-cgi"; fi
for module in cgi setenv; do
  [ -f "/opt/lib/lighttpd/mod_$module.so" ] || PACKAGES="$PACKAGES lighttpd-mod-$module"
done
if [ -n "$PACKAGES" ]; then
  echo "Устанавливаю недостающие пакеты Entware:$PACKAGES"
  opkg update
  # Deliberate word splitting of a fixed list; no user-controlled package names.
  opkg install $PACKAGES
fi
if [ "$NEW_HTTP" = yes ]; then
  # A newly installed Entware lighttpd otherwise defaults to :80, occupied by Keenetic.
  # Keep its unused primary listener private; FPTN gets its own LAN :1111 socket.
  BASE_FRAGMENT=/opt/etc/lighttpd/conf.d/05-fptn-bootstrap.conf
  [ ! -e "$BASE_FRAGMENT" ] || fail 'Фрагмент начальной настройки lighttpd уже существует.'
  if netstat -lnt 2>/dev/null | awk '$4 ~ /:1110$/ {found=1} END {exit !found}'; then fail 'Порт 1110 занят: lighttpd не перенастраивался.'; fi
  [ -f /opt/etc/lighttpd/lighttpd.conf ] || fail 'Пакет lighttpd не создал основной конфиг.'
  mkdir -p /opt/etc/lighttpd/conf.d
  printf '%s\n' '# Initial Entware lighttpd listener, created by FPTN bootstrap.' 'server.bind := "127.0.0.1"' 'server.port := 1110' > "$BASE_FRAGMENT"
  if ! lighttpd -tt -f /opt/etc/lighttpd/lighttpd.conf; then
    rm -f "$BASE_FRAGMENT"
    fail 'Не удалось подготовить новый lighttpd. Его основной конфиг не менялся.'
  fi
fi
for cmd in curl jq sha256sum mktemp; do command -v "$cmd" >/dev/null 2>&1 || fail "Не найдена команда $cmd."; done
BUNDLE=$(dirname "$(readlink -f "$0")")
if [ -f "$BUNDLE/release-revision" ] && [ -f "$BUNDLE/scripts/install-web-ui.sh" ]; then
  REV=$(cat "$BUNDLE/release-revision")
  case "$REV" in *[!a-f0-9]*|'') fail 'Некорректная версия пакета.' ;; esac
  [ "${#REV}" -eq 40 ] || fail 'Нужен полный SHA пакета.'
  (cd "$BUNDLE"; sha256sum -c SHA256SUMS) || fail 'Пакет повреждён.'
  FPTN_PAYLOAD_DIR=$BUNDLE sh "$BUNDLE/scripts/install-web-ui.sh" install "$REV" "$LAN"
  exit 0
fi
mkdir -p /opt/tmp
STAGE=$(mktemp -d /opt/tmp/fptn-bootstrap.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT
trap 'exit 1' INT TERM HUP
curl --proto '=https' --proto-redir '=https' -fLsS --connect-timeout 15 --max-time 60 \
  https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/main/web/update.json -o "$STAGE/channel.json"
jq -e 'def hex($n): type=="string" and length==$n and all(explode[]; (.>=48 and .<=57) or (.>=97 and .<=102));
  .schema==1 and (.revision|hex(40)) and (.installerSha256|hex(64))' "$STAGE/channel.json" >/dev/null || fail 'Некорректный каталог выпуска.'
REV=$(jq -r .revision "$STAGE/channel.json")
SUM=$(jq -r .installerSha256 "$STAGE/channel.json")
curl --proto '=https' --proto-redir '=https' -fLsS --connect-timeout 15 --max-time 60 \
  "https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/$REV/scripts/install-web-ui.sh" -o "$STAGE/install-web-ui.sh"
printf '%s  %s\n' "$SUM" "$STAGE/install-web-ui.sh" | sha256sum -c - || fail 'Контрольная сумма установщика не совпала.'
sh "$STAGE/install-web-ui.sh" install "$REV" "$LAN"
