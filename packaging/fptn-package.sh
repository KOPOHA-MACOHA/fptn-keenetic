#!/bin/sh
# opkg owns the immutable payload; the established installer owns deployment/rollback.
set -eu
umask 077
ROOT=${FPTN_PREFIX:-/opt}
PATH=$ROOT/sbin:$ROOT/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
PAYLOAD=$ROOT/share/fptn-package
CONF=$ROOT/etc/fptn
fail() { echo "FPTN package: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'Требуется root Entware.'
case "${1:-}" in
 deploy)
  [ -f "$PAYLOAD/.owned" ] || fail 'Не найден установленный пакет.'
  PLATFORM=$(sh "$PAYLOAD/platform.sh")
  jq -e --arg p "$PLATFORM" '.schema==1 and .platform==$p and (.revision|test("^[a-f0-9]{40}$")) and (.archiveSha256|test("^[a-f0-9]{64}$"))' "$PAYLOAD/package.json" >/dev/null || fail 'Неверная платформа/метаданные пакета.'
  SUM=$(jq -r .archiveSha256 "$PAYLOAD/package.json")
  printf '%s  %s\n' "$SUM" "$PAYLOAD/runtime.tar.gz" | sha256sum -c - || fail 'Повреждён payload.'
  mkdir -p "$ROOT/tmp"
  STAGE=$(mktemp -d "$ROOT/tmp/fptn-opkg.XXXXXX")
  trap 'rm -rf "$STAGE"' EXIT
  trap 'exit 1' INT TERM HUP
  tar -xzf "$PAYLOAD/runtime.tar.gz" -C "$STAGE"
  BUNDLE=$STAGE/fptn-keenetic
  (cd "$BUNDLE"; sha256sum -c SHA256SUMS) || fail 'Повреждён runtime.'
  REV=$(jq -r .revision "$PAYLOAD/package.json")
  [ "$(cat "$BUNDLE/release-revision")" = "$REV" ] || fail 'Версии payload не совпали.'
  LAN=${FPTN_PACKAGE_LAN:-}
  PORT=${FPTN_INSTALL_PORT:-}
  if [ -s "$CONF/web-origin" ]; then
    ORIGIN=$(cat "$CONF/web-origin"); ENDPOINT=${ORIGIN#http://}
    LAN=${LAN:-${ENDPOINT%:*}}; PORT=${PORT:-${ENDPOINT##*:}}
  fi
  LAN=${LAN:-192.168.1.1}
  # No terminal: retain the existing endpoint or explicitly try the default port.
  if [ -z "$PORT" ] && ! (: < /dev/tty) 2>/dev/null; then PORT=1111; fi
  BOOTSTRAP_HTTP=no
  if [ ! -e "$ROOT/bin/fptnctl" ] && ! pidof lighttpd >/dev/null 2>&1; then
    MAIN=$ROOT/etc/lighttpd/lighttpd.conf
    VENDOR_SUM=$(opkg status lighttpd | awk -v p="$MAIN" '$1==p && $2~/^[a-f0-9]+$/ {print $2}')
    if [ "${#VENDOR_SUM}" = 32 ] && [ -f "$MAIN" ] && [ "$(md5sum "$MAIN" | awk '{print $1}')" = "$VENDOR_SUM" ]; then
      EFFECTIVE=$(lighttpd -p -f "$MAIN")
      if printf '%s\n' "$EFFECTIVE" | grep -Eq 'server.port[[:space:]]*=[[:space:]]*80([[:space:]]|$)'; then BOOTSTRAP_HTTP=yes; fi
    fi
  fi
  FPTN_PACKAGE_DEPLOY=1 FPTN_BOOTSTRAP_HTTP=$BOOTSTRAP_HTTP sh "$BUNDLE/install.sh" install "$LAN" "$PORT"
  mkdir -p "$CONF"
  cp "$PAYLOAD/package.json" "$CONF/opkg-managed.new"
  chmod 600 "$CONF/opkg-managed.new"
  mv "$CONF/opkg-managed.new" "$CONF/opkg-managed.json"
  ;;
 remove)
  [ -f "$CONF/opkg-managed.json" ] || exit 0
  # Refuse silently removing a newer direct/manual deployment.
  if [ -f "$CONF/opkg-managed.json" ]; then
    jq -e --slurpfile p "$CONF/opkg-managed.json" '.revision==$p[0].revision' "$CONF/web-meta.json" >/dev/null || fail 'Установленный runtime не совпадает с управляемым opkg. Удаление остановлено.'
  fi
  if [ -x "$ROOT/bin/fptnctl" ]; then FPTN_PACKAGE_REMOVE=1 "$ROOT/bin/fptnctl" uninstall; fi
  rm -f "$CONF/opkg-managed.json"
  ;;
 configure)
  export FPTN_PACKAGE_LAN=${2:-} FPTN_INSTALL_PORT=${3:-}
  exec opkg configure fptn-keenetic
  ;;
 upgrade)
  [ -f "$CONF/opkg-managed.json" ] || fail 'Установка не управляется opkg.'
  opkg update
  opkg upgrade fptn-keenetic
  ;;
 *) fail 'Команды: configure [LAN_IP] [PORT], upgrade. Удаление: opkg remove fptn-keenetic.' ;;
esac
