#!/bin/sh
# Fixed repository channel; never execute a browser-supplied URL or command.
set -eu
umask 077
ROOT=${FPTN_PREFIX:-/opt}
RUN=$ROOT/var/run/fptn-web
CONF=$ROOT/etc/fptn
LIB=$ROOT/libexec/fptn
fail() { echo "$*" >&2; exit 1; }
validate() {
  jq -ce 'select(.schema==1 and (.version|type=="string") and
    (.version|split(".")|length==3 and all(.[]; length>0 and length<=4 and all(explode[]; .>=48 and .<=57))) and
    (.revision|type=="string" and length==40 and all(explode[]; (.>=48 and .<=57) or (.>=97 and .<=102))) and
    (.installerSha256|type=="string" and length==64 and all(explode[]; (.>=48 and .<=57) or (.>=97 and .<=102))) and
    (.clientRevision|type=="string" and length==40 and all(explode[]; (.>=48 and .<=57) or (.>=97 and .<=102))) and
    (.clientVersion|type=="string" and length>0 and length<=32 and all(explode[]; (.>=48 and .<=57) or .==46))) |
    {schema,version,revision,installerSha256,clientRevision,clientVersion}' "$1"
}
mkdir -p "$RUN"
case "${1:-}" in
 check)
  TEMP=$(mktemp -d "$RUN/update-check.XXXXXX")
  trap 'rm -rf "$TEMP"' EXIT
  curl --proto '=https' --proto-redir '=https' -fsSL --connect-timeout 10 --max-time 30 --max-filesize 65536 \
    https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/main/web/update.json -o "$TEMP/channel.json"
  [ "$(wc -c < "$TEMP/channel.json")" -le 65536 ] || fail 'Слишком большой каталог обновлений.'
  validate "$TEMP/channel.json" > "$TEMP/validated.json" || fail 'Некорректный каталог обновлений.'
  jq --argjson checkedAt "$(date +%s)" '. + {checkedAt:$checkedAt}' "$TEMP/validated.json" > "$RUN/update-available.new"
  mv "$RUN/update-available.new" "$RUN/update-available.json"
  ;;
 status)
  if [ ! -f "$RUN/update-available.json" ]; then echo '{"checked":false,"available":false}'; exit 0; fi
  CATALOG=$(validate "$RUN/update-available.json") || fail 'Повторите проверку обновлений.'
  META='{}'; [ ! -f "$CONF/web-meta.json" ] || META=$(cat "$CONF/web-meta.json")
  printf '%s' "$CATALOG" | jq --argjson meta "$META" '. + {checked:true,available:(.revision!=($meta.revision//"") and ((.version|split(".")|map(tonumber)) >= (($meta.uiVersion//"0.0.0")|split(".")|map(tonumber))))}'
  ;;
 apply)
  # Caller must hold the journalled update operation. The installer verifies it too.
  [ -n "${FPTN_OPERATION_ID:-}" ] || fail 'Обновление не зарезервировано.'
  CATALOG=$(validate "$RUN/update-available.json") || fail 'Сначала проверьте обновления.'
  CHECKED=$(jq -r '.checkedAt//0' "$RUN/update-available.json")
  NOW=$(date +%s)
  case "$CHECKED" in ''|*[!0-9]*) fail 'Повторите проверку обновлений.' ;; esac
  [ "$CHECKED" -le "$NOW" ] && [ "$((NOW-CHECKED))" -le 86400 ] || fail 'Каталог устарел. Повторите проверку обновлений.'
  sh "$0" status | jq -e '.available==true' >/dev/null || fail 'Обновление уже установлено или версия устарела.'
  TEMP=$(mktemp -d "$RUN/update-install.XXXXXX")
  trap 'rm -rf "$TEMP"' EXIT
  REV=$(printf '%s' "$CATALOG" | jq -r .revision)
  SUM=$(printf '%s' "$CATALOG" | jq -r .installerSha256)
  CLIENT_REV=$(printf '%s' "$CATALOG" | jq -r .clientRevision)
  FPTN_TARGET_CLIENT_VERSION=$(printf '%s' "$CATALOG" | jq -r .clientVersion)
  export FPTN_TARGET_CLIENT_VERSION
  curl --proto '=https' --proto-redir '=https' -fsSL --connect-timeout 15 --max-time 60 --max-filesize 262144 \
    "https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/$REV/scripts/install-web-ui.sh" -o "$TEMP/install.sh"
  printf '%s  %s\n' "$SUM" "$TEMP/install.sh" | sha256sum -c - || fail 'Не совпала контрольная сумма установщика.'
  sh -n "$TEMP/install.sh"
  ORIGIN=$(cat "$CONF/web-origin")
  LAN=${ORIGIN#http://}; LAN=${LAN%:1111}
  sh "$TEMP/install.sh" install "$REV" "$LAN" "$CLIENT_REV"
  ;;
 *) fail 'Неизвестная команда обновления.' ;;
esac
