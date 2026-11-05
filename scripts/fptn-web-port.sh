#!/bin/sh
# Change only the owned lighttpd vhost. The VPN and firmware are untouched.
set -eu
umask 077
ROOT=${FPTN_PREFIX:-/opt}
CONF=$ROOT/etc/fptn
STATE=$CONF/web-port-change
FRAGMENT=$ROOT/etc/lighttpd/conf.d/95-fptn.conf
MAIN=$ROOT/etc/lighttpd/lighttpd.conf
fail() { echo "Порт панели: $*" >&2; exit 1; }
valid_port() { case "$1" in ''|*[!0-9]*|0*) return 1 ;; esac; [ "${#1}" -le 5 ] && [ "$1" -le 65535 ]; }
current() {
  OLD=$(cat "$CONF/web-origin")
  ENDPOINT=${OLD#http://};LAN=${ENDPOINT%:*};OLD_PORT=${ENDPOINT##*:}
  valid_port "$OLD_PORT" && [ "$OLD" = "http://$LAN:$OLD_PORT" ] || fail 'Повреждён адрес панели.'
  printf '%s\n' "$LAN" | awk -F. 'NF!=4{exit 1}{for(i=1;i<=4;i++)if($i!~/^[0-9]+$/||$i>255||length($i)>3)exit 1}' || fail 'Некорректный LAN IPv4.'
}
available() {
  LISTENERS=$(netstat -lntp 2>/dev/null) || fail 'Не удалось прочитать TCP-порты.'
  ! printf '%s\n' "$LISTENERS" | awk -v port="$1" '$1~/^tcp/ && $4 ~ (":" port "$"){found=1} END{exit !found}'
}
init_script() {
  INIT=
  for script in "$ROOT"/etc/init.d/S*lighttpd; do
    [ -x "$script" ] || continue
    [ -z "$INIT" ] || fail 'Найдено несколько init-скриптов lighttpd.'
    INIT=$script
  done
  [ -n "$INIT" ] || fail 'Не найден init-скрипт lighttpd.'
}
restart() {
  lighttpd -tt -f "$MAIN" || return 1
  if ! "$INIT" restart; then echo 'Init сообщил failed; проверяю фактический ответ панели.' >&2; fi
}
ready() {
  curl --noproxy '*' -fsS --connect-timeout 1 --max-time 2 "$1/api.cgi?auth-info" |
    jq -e --arg version "$(jq -r .uiVersion "$CONF/web-meta.json")" '.uiVersion==$version and (.configured|type=="boolean")' >/dev/null 2>&1
}
wait_ready() {
  DEADLINE=$(( $(date +%s)+20 ))
  until ready "$1"; do [ "$(date +%s)" -lt "$DEADLINE" ] || return 1; sleep 1; done
}
rollback() {
  [ -f "$STATE/old-fragment" ] && [ -f "$STATE/old-origin" ] || return 0
  cp -p "$STATE/old-fragment" "$FRAGMENT.new";mv "$FRAGMENT.new" "$FRAGMENT"
  cp -p "$STATE/old-origin" "$CONF/web-origin.new";mv "$CONF/web-origin.new" "$CONF/web-origin"
  restart && wait_ready "$(cat "$STATE/old-origin")" || return 1
  rm -rf "$STATE"
}
case "${1:-}" in
  check)
    PORT=${2:-};valid_port "$PORT" || fail 'Введите TCP-порт 1–65535.'
    current
    [ "$PORT" = "$OLD_PORT" ] || available "$PORT" || fail 'Порт занят другим сервисом. Выберите другой.' ;;
  info)
    if [ -r "$STATE/info.json" ]; then cat "$STATE/info.json"; else echo null; fi ;;
  confirm)
    [ -f "$STATE/info.json" ] || fail 'Нет ожидающей смены порта.'
    jq -e --arg id "${2:-}" --arg origin "${3:-}" --argjson now "$(date +%s)" \
      '.id==$id and .newOrigin==$origin and .deadline>$now' "$STATE/info.json" >/dev/null || fail 'Подтвердите новый адрес до истечения ожидания.'
    : > "$STATE/confirmed" ;;
  rollback) init_script;rollback ;;
  apply)
    [ "$(id -u)" = 0 ] || fail 'Требуется root.'
    PORT=${2:-};valid_port "$PORT" || fail 'Введите TCP-порт 1–65535.'
    case "${FPTN_OPERATION_ID:-}" in ''|*[!a-f0-9]*) fail 'Нет операции панели.' ;; esac
    [ "${#FPTN_OPERATION_ID}" -eq 32 ] || fail 'Некорректная операция.'
    jq -e --arg id "$FPTN_OPERATION_ID" '.id==$id' "$ROOT/var/run/fptn-web/action/owner.json" >/dev/null || fail 'Изменилась операция.'
    [ "$(cat "$ROOT/var/run/fptn-web/action/action")" = change-port ] || fail 'Занята другая операция.'
    current;[ "$PORT" != "$OLD_PORT" ] || exit 0
    [ -f "$ROOT/share/fptn-web/.fptn-web-owned" ] || fail 'Нет маркера собственной панели.'
    grep -Fq '# Managed by fptn-web-ui' "$FRAGMENT" || fail 'Чужая конфигурация.'
    available "$PORT" || fail 'Порт занят другим сервисом.'
    init_script
    mkdir "$STATE" || fail 'Есть незавершённая смена порта. Восстановите управление.'
    cp -p "$FRAGMENT" "$STATE/old-fragment";cp -p "$CONF/web-origin" "$STATE/old-origin"
    DONE=no
    finish() {
      trap ':' INT TERM HUP
      if [ "$DONE" != yes ]; then
        rollback || echo 'Не удалось завершить откат порта. Используйте восстановление управления.' >&2
      fi
    }
    trap finish EXIT
    trap 'exit 1' INT TERM HUP
    NEW=http://$LAN:$PORT
    # Replace exactly the managed socket line, retaining all other directives.
    awk -v old="$LAN:$OLD_PORT" -v new="$LAN:$PORT" '
      $0=="$SERVER[\"socket\"] == \"" old "\" {" {print "$SERVER[\"socket\"] == \"" new "\" {"; n++;next}
      {print} END{if(n!=1)exit 1}' "$STATE/old-fragment" > "$STATE/new-fragment"
    cp "$STATE/new-fragment" "$STATE/both-fragments"
    awk '/^\$SERVER\["socket"\]/{body=1} body{print}' "$STATE/old-fragment" >> "$STATE/both-fragments"
    jq -n --arg id "$FPTN_OPERATION_ID" --arg oldOrigin "$OLD" --arg newOrigin "$NEW" --argjson deadline "$(( $(date +%s)+120 ))" \
      '{id:$id,oldOrigin:$oldOrigin,newOrigin:$newOrigin,deadline:$deadline}' > "$STATE/info.json.new"
    mv "$STATE/info.json.new" "$STATE/info.json"
    available "$PORT" || fail 'Порт успели занять. Изменений нет.'
    cp "$STATE/both-fragments" "$FRAGMENT.new";mv "$FRAGMENT.new" "$FRAGMENT"
    restart && wait_ready "$NEW" || fail 'Новая панель не отвечает; возвращаю прежний порт.'
    UNTIL=$(jq -r .deadline "$STATE/info.json")
    while [ ! -f "$STATE/confirmed" ]; do
      [ "$(date +%s)" -lt "$UNTIL" ] || fail 'Новый адрес не подтверждён; возвращаю прежний порт.'
      sleep 1
    done
    cp "$STATE/new-fragment" "$FRAGMENT.new";mv "$FRAGMENT.new" "$FRAGMENT"
    printf '%s\n' "$NEW" > "$CONF/web-origin.new";mv "$CONF/web-origin.new" "$CONF/web-origin"
    restart && wait_ready "$NEW" || fail 'Не прошла завершающая проверка; возвращаю прежний порт.'
    # A failed init can leave the old process serving both sockets. Do not report
    # completion merely because the new port answers in that stale process.
    LISTENERS=$(netstat -lntp 2>/dev/null) || fail 'Не удалось проверить завершение смены порта.'
    if printf '%s\n' "$LISTENERS" | awk -v old="$LAN:$OLD_PORT" '$1~/^tcp/ && $4==old {found=1} END{exit !found}'; then
      fail 'Прежний порт остался открыт после перезапуска; возвращаю прежние настройки.'
    fi
    DONE=yes;rm -rf "$STATE"
    echo "Порт изменён. Панель: $NEW" ;;
  *) fail 'Неизвестное действие.' ;;
esac
